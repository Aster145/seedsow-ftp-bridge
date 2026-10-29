"""Real local FTP sockets against a fake game transport; never opens hardware."""

import concurrent.futures
import ftplib
import importlib.util
import io
import socket
import threading
import time
import unittest
from pathlib import Path


SERVER_FILE = Path(__file__).resolve().parents[1] / "ftp_server.py"
spec = importlib.util.spec_from_file_location("seedsow_ftp_server", SERVER_FILE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FakeTransport:
    def __init__(self):
        self.files = {"seed.lua": b"print('seed')\n"}
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.state_lock = threading.Lock()
        self.fail_write = False
        self.write_reply = None
        self.delay = 0
        self.write_started = threading.Event()
        self.allow_write = threading.Event()
        self.allow_write.set()

    def request(self, op, name="", data=b""):
        with self.state_lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append((op, name, data))
        try:
            if self.delay:
                time.sleep(self.delay)
            if op == "L":
                return "\n".join(f"{key}\t{len(value)}" for key, value in self.files.items()).encode()
            if op == "G":
                return self.files[name]
            if op == "P":
                self.write_started.set()
                if not self.allow_write.wait(5):
                    raise TimeoutError("test gate not released")
                if self.fail_write:
                    raise RuntimeError("simulated game save failure")
                if self.write_reply is None:
                    self.files[name] = data
                    return f"OK\t{name}\t{len(data)}\n".encode("ascii")
                return self.write_reply
            raise AssertionError(op)
        finally:
            with self.state_lock:
                self.active -= 1


class EPSVFTP(ftplib.FTP):
    def makepasv(self):
        return ftplib.parse229(self.sendcmd("EPSV"), self.sock.getpeername())


class FTPIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self.server = module.GameFTPServer(("127.0.0.1", 0), self.transport, timeout=5)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.clients = []

    def tearDown(self):
        self.transport.allow_write.set()
        for client in self.clients:
            client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def client(self, cls=ftplib.FTP, login=True):
        client = cls(timeout=5)
        client.connect(*self.server.server_address)
        self.clients.append(client)
        if login:
            client.login("seedsow", "seedsow")
        return client

    def test_login_root_and_rejected_mutations(self):
        client = self.client(login=False)
        with self.assertRaisesRegex(ftplib.error_perm, "530"):
            client.pwd()
        with self.assertRaisesRegex(ftplib.error_perm, "530"):
            client.login("seedsow", "wrong")
        client.login("seedsow", "seedsow")
        self.assertEqual(client.pwd(), "/")
        client.cwd("/")
        with self.assertRaisesRegex(ftplib.error_perm, "550"):
            client.cwd("subdir")
        self.assertTrue(client.sendcmd("SYST").startswith("215"))
        self.assertIn("UTF8", client.sendcmd("FEAT"))
        self.assertTrue(client.sendcmd("OPTS UTF8 ON").startswith("200"))
        for command in ("DELE seed.lua", "RNFR seed.lua", "RNTO other.lua", "MKD dir"):
            with self.assertRaisesRegex(ftplib.error_perm, "550"):
                client.sendcmd(command)
        for name in ("../escape", "a/b", ".hidden", "中文.lua", "a" * 65):
            with self.assertRaisesRegex(ftplib.error_perm, "553"):
                client.sendcmd("STOR " + name)
        self.assertEqual(self.transport.calls, [])
        self.assertTrue(client.quit().startswith("221"))

    def test_live_list_size_mlsd_and_timestamp_unavailable(self):
        client = self.client()
        self.assertEqual(client.nlst(), ["seed.lua"])
        self.transport.files["external.lua"] = b"from game"
        self.assertEqual(client.nlst(), ["external.lua", "seed.lua"])
        lines = []
        client.retrlines("LIST -la", lines.append)
        self.assertEqual(len(lines), 2)
        self.assertTrue(any(line.endswith("external.lua") for line in lines))
        facts = dict(client.mlsd())
        self.assertEqual(facts["external.lua"], {"type": "file", "size": "9"})
        self.assertEqual(client.size("external.lua"), 9)
        self.assertIn("type=file;size=9; external.lua", client.sendcmd("MLST external.lua"))
        with self.assertRaisesRegex(ftplib.error_perm, "modification times"):
            client.sendcmd("MDTM external.lua")
        with self.assertRaisesRegex(ftplib.error_perm, "550"):
            client.size("absent.lua")
        self.assertTrue(all(call[0] == "L" for call in self.transport.calls))
        self.assertEqual(len(self.transport.calls), 8)

    def test_binary_roundtrip_and_empty_file(self):
        client = self.client()
        payload = bytes(range(256)) * 27 + b"\x00\r\n\xffEND"
        self.assertTrue(client.storbinary("STOR /roundtrip.bin", io.BytesIO(payload)).startswith("226"))
        received = bytearray()
        self.assertTrue(client.retrbinary("RETR /roundtrip.bin", received.extend).startswith("226"))
        self.assertEqual(bytes(received), payload)
        self.assertEqual(self.transport.files["roundtrip.bin"], payload)
        self.assertTrue(client.storbinary("STOR empty.lua", io.BytesIO(b"")).startswith("226"))
        self.assertEqual(self.transport.files["empty.lua"], b"")
        self.assertEqual([call[0] for call in self.transport.calls], ["P", "G", "P"])

    def test_epsv_transfer(self):
        client = self.client(EPSVFTP)
        self.assertEqual(client.nlst(), ["seed.lua"])
        received = bytearray()
        client.retrbinary("RETR seed.lua", received.extend)
        self.assertEqual(received, self.transport.files["seed.lua"])

    def test_upload_limit_before_game_write(self):
        client = self.client()
        exact = bytes(range(256)) * 256
        self.assertEqual(len(exact), 65536)
        client.storbinary("STOR exact.bin", io.BytesIO(exact))
        self.assertEqual(self.transport.files["exact.bin"], exact)
        with self.assertRaisesRegex(ftplib.error_perm, "552"):
            client.storbinary("STOR oversized.bin", io.BytesIO(exact + b"X"))
        self.assertNotIn("oversized.bin", self.transport.files)
        self.assertEqual([call[1] for call in self.transport.calls if call[0] == "P"], ["exact.bin"])
        self.assertTrue(client.sendcmd("NOOP").startswith("200"))

    def test_game_failure_or_missing_confirmation_never_succeeds(self):
        client = self.client()
        self.transport.fail_write = True
        with self.assertRaisesRegex(ftplib.error_temp, "451.*simulated game save failure"):
            client.storbinary("STOR failed.lua", io.BytesIO(b"new"))
        self.assertNotIn("failed.lua", self.transport.files)
        self.transport.fail_write = False
        self.transport.write_reply = b"ERR"
        for reply in (b"ERR", b"OK", b"OK\twrong.lua\t3\n", b"OK\tunconfirmed.lua\t4\n"):
            self.transport.write_reply = reply
            with self.assertRaisesRegex(ftplib.error_temp, "451.*did not confirm"):
                client.storbinary("STOR unconfirmed.lua", io.BytesIO(b"new"))
        self.assertNotIn("unconfirmed.lua", self.transport.files)

    def test_write_reply_waits_for_game_confirmation(self):
        client = self.client()
        self.transport.allow_write.clear()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            upload = executor.submit(client.storbinary, "STOR blocked.lua", io.BytesIO(b"pending"))
            self.assertTrue(self.transport.write_started.wait(2))
            self.assertFalse(upload.done(), "FTP must not report success while the game is still saving")
            self.assertNotIn("blocked.lua", self.transport.files)
            self.transport.allow_write.set()
            self.assertTrue(upload.result(2).startswith("226"))
        self.assertEqual(self.transport.files["blocked.lua"], b"pending")

    def test_two_clients_share_game_request_lock(self):
        first, second = self.client(), self.client()
        self.transport.delay = 0.08
        barrier = threading.Barrier(2)

        def list_files(client):
            barrier.wait()
            return client.nlst()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            a = executor.submit(list_files, first)
            b = executor.submit(list_files, second)
            self.assertEqual(a.result(3), ["seed.lua"])
            self.assertEqual(b.result(3), ["seed.lua"])
        self.assertEqual(self.transport.max_active, 1)
        self.assertEqual(len(self.transport.calls), 2)

    def test_server_cannot_bind_lan(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            module.GameFTPServer(("0.0.0.0", 0), self.transport)

    def test_site_stop_requires_login_and_stops_serving(self):
        client = self.client(login=False)
        with self.assertRaisesRegex(ftplib.error_perm, "530"):
            client.sendcmd("SITE STOP")
        self.assertFalse(self.server.stop_requested.is_set())
        client.login("seedsow", "seedsow")
        self.assertTrue(client.sendcmd("SITE STOP").startswith("200"))
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())
        self.assertTrue(self.server.stop_requested.is_set())
        with self.assertRaisesRegex(RuntimeError, "stopping"):
            self.server.game_request("L")
        self.assertEqual(self.transport.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
