"""Local passive FTP front end for files held by the running Seedsow game.

The transport owns the hardware and the game's file operations. This server
never substitutes local files for successful game reads or writes.
"""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import re
import socket
import socketserver
import threading
from pathlib import Path


TIMEOUT = 600
MAX_UPLOAD = 64 * 1024
FILE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\Z")


class FTPError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def file_name(argument: str) -> str:
    """Accept a single file at the FTP root, never a host filesystem path."""
    name = argument[1:] if argument.startswith("/") else argument
    if not FILE_NAME.fullmatch(name):
        raise FTPError(553, "Use a plain ASCII filename of 1 to 64 characters.")
    return name


class GameFTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, game_transport, *, timeout: float = TIMEOUT):
        host, port = address
        if host == "localhost":
            host = "127.0.0.1"
        try:
            local = ipaddress.IPv4Address(host).is_loopback
        except ipaddress.AddressValueError:
            local = False
        if not local:
            raise ValueError("This FTP server only binds to an IPv4 loopback address.")
        self.game_transport = game_transport
        self.game_lock = threading.Lock()
        self.stop_requested = threading.Event()
        self.operation_timeout = timeout
        super().__init__((host, port), GameFTPHandler)

    def game_request(self, op: str, name: str = "", data: bytes = b"") -> bytes:
        # All control connections share one console and one transport session.
        with self.game_lock:
            if self.stop_requested.is_set():
                raise RuntimeError("FTP server is stopping.")
            result = self.game_transport.request(op, name, data)
        if not isinstance(result, bytes):
            raise RuntimeError("Game transport returned an invalid response.")
        return result

    def listing(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for line in self.game_request("L").decode("utf-8").splitlines():
            if not line:
                continue
            name, size_text = line.split("\t")
            if not FILE_NAME.fullmatch(name) or name in result:
                raise RuntimeError("Game returned an invalid or duplicate filename.")
            if not size_text.isascii() or not size_text.isdecimal():
                raise RuntimeError("Game returned an invalid file size.")
            result[name] = int(size_text)
        return result


class GameFTPHandler(socketserver.StreamRequestHandler):
    server: GameFTPServer

    def setup(self):
        self.request.settimeout(self.server.operation_timeout)
        super().setup()
        self.authenticated = False
        self.username = ""
        self.passive = None

    def reply(self, code: int, message: str):
        message = message.replace("\r", " ").replace("\n", " ")[:400]
        self.wfile.write(f"{code} {message}\r\n".encode("utf-8"))
        self.wfile.flush()

    def close_passive(self):
        if self.passive is not None:
            self.passive.close()
            self.passive = None

    def handle(self):
        self.reply(220, "Seedsow game files. Login seedsow / seedsow.")
        try:
            while True:
                line = self.rfile.readline(4097)
                if not line:
                    break
                if len(line) > 4096:
                    self.reply(500, "Command too long.")
                    break
                try:
                    command_line = line.rstrip(b"\r\n").decode("utf-8")
                    if "\x00" in command_line:
                        raise FTPError(501, "Invalid command.")
                    command, _, argument = command_line.partition(" ")
                    command = command.upper()
                    if command == "QUIT":
                        self.reply(221, "Goodbye.")
                        break
                    if self.command(command, argument) is False:
                        break
                except FTPError as error:
                    self.reply(error.code, str(error))
                except UnicodeError:
                    self.reply(501, "Command must be UTF-8.")
                except Exception as error:
                    self.reply(451, f"Game operation failed: {error}")
        except (TimeoutError, OSError):
            # The peer may already have gone away, so do not retry writes here.
            pass
        finally:
            self.close_passive()

    def command(self, command: str, argument: str):
        if command == "USER":
            self.authenticated = False
            self.username = argument
            self.reply(331, "Password required.")
        elif command == "PASS":
            valid = hmac.compare_digest(self.username.encode(), b"seedsow")
            valid &= hmac.compare_digest(argument.encode(), b"seedsow")
            self.authenticated = bool(valid)
            self.reply(230 if valid else 530, "Logged in." if valid else "Login incorrect.")
        elif command == "SYST":
            self.reply(215, "UNIX Type: L8")
        elif command == "FEAT":
            self.wfile.write(
                b"211-Features\r\n UTF8\r\n EPSV\r\n SIZE\r\n"
                b" MLST type*;size*;\r\n211 End\r\n"
            )
            self.wfile.flush()
        elif command == "OPTS":
            if argument.upper() not in ("UTF8", "UTF8 ON"):
                raise FTPError(501, "Only OPTS UTF8 ON is supported.")
            self.reply(200, "UTF8 enabled; game filenames use ASCII.")
        elif not self.authenticated:
            raise FTPError(530, "Please login first.")
        elif command == "SITE" and argument.upper() == "STOP":
            self.server.stop_requested.set()
            try:
                self.reply(200, "Stopping FTP after the current game operation finishes.")
            finally:
                # shutdown() must run outside the serve_forever thread.
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            return False
        elif self.server.stop_requested.is_set():
            raise FTPError(421, "FTP server is stopping.")
        elif command == "NOOP":
            self.reply(200, "OK")
        elif command in ("PWD", "XPWD"):
            self.reply(257, '"/" is the game save directory.')
        elif command in ("CWD", "XCWD", "CDUP"):
            if command != "CDUP" and argument not in ("", "/", ".", "..", "/.", "/.."):
                raise FTPError(550, "Only the game save root is available.")
            self.reply(250, "Directory is /.")
        elif command == "TYPE":
            if argument.upper() not in ("A", "I", "L 8"):
                raise FTPError(504, "Use TYPE A or TYPE I.")
            self.reply(200, "Transfer type accepted.")
        elif command in ("PASV", "EPSV"):
            self.enter_passive(command, argument)
        elif command in ("LIST", "NLST", "MLSD"):
            self.send_listing(command, argument)
        elif command == "MLST":
            name = file_name(argument)
            items = self.server.listing()
            if name not in items:
                raise FTPError(550, "File not found.")
            self.wfile.write(
                f"250-File information\r\n type=file;size={items[name]}; {name}\r\n250 End\r\n".encode("ascii")
            )
            self.wfile.flush()
        elif command == "SIZE":
            name = file_name(argument)
            items = self.server.listing()
            if name not in items:
                raise FTPError(550, "File not found.")
            self.reply(213, str(items[name]))
        elif command == "MDTM":
            name = file_name(argument)
            if name not in self.server.listing():
                raise FTPError(550, "File not found.")
            raise FTPError(550, "The game does not expose file modification times.")
        elif command == "RETR":
            name = file_name(argument)
            self.transfer(lambda connection: connection.sendall(self.server.game_request("G", name)))
        elif command == "STOR":
            name = file_name(argument)
            self.transfer(lambda connection: self.receive_file(connection, name))
        elif command in ("DELE", "RNFR", "RNTO", "MKD", "RMD", "APPE", "STOU", "SITE"):
            raise FTPError(550, "This operation is disabled; use RETR or STOR.")
        else:
            raise FTPError(502, "Command not implemented. Use passive mode.")

    def enter_passive(self, command: str, argument: str):
        if command == "EPSV" and argument not in ("", "1"):
            raise FTPError(522, "Only IPv4 EPSV is supported.")
        self.close_passive()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind((self.server.server_address[0], 0))
            listener.listen(1)
            listener.settimeout(self.server.operation_timeout)
        except Exception:
            listener.close()
            raise
        self.passive = listener
        host, port = listener.getsockname()
        if command == "EPSV":
            self.reply(229, f"Entering Extended Passive Mode (|||{port}|).")
        else:
            parts = host.split(".") + [str(port // 256), str(port % 256)]
            self.reply(227, "Entering Passive Mode (" + ",".join(parts) + ").")

    def transfer(self, action):
        listener, self.passive = self.passive, None
        if listener is None:
            raise FTPError(425, "Use PASV or EPSV first.")
        connection = None
        try:
            self.reply(150, "Opening data connection; waiting for the game may take several minutes.")
            connection, address = listener.accept()
            if address[0] != self.client_address[0]:
                raise FTPError(425, "Data peer does not match control peer.")
            connection.settimeout(self.server.operation_timeout)
            action(connection)
        except FTPError:
            raise
        except (TimeoutError, ConnectionError) as error:
            raise FTPError(426, f"Data transfer interrupted: {error}") from error
        finally:
            if connection is not None:
                connection.close()
            listener.close()
        # A game request (and game-side save, for STOR) must succeed first.
        self.reply(226, "Transfer complete; game operation succeeded.")

    def send_listing(self, command: str, argument: str):
        # Common GUI clients send LIST -a or -la for this flat directory.
        if argument in ("", "/", ".", "-a", "-l", "-la", "-al"):
            target = None
        else:
            target = file_name(argument)

        def send(connection):
            items = self.server.listing()
            if target is not None:
                if target not in items:
                    raise FTPError(550, "File not found.")
                items = {target: items[target]}
            lines = []
            for name, size in sorted(items.items()):
                if command == "NLST":
                    lines.append(name)
                elif command == "MLSD":
                    lines.append(f"type=file;size={size}; {name}")
                else:
                    # LIST needs a Unix-style date column, but no timestamp is
                    # supplied by the game. The epoch is only a display placeholder.
                    lines.append(f"-rw-r--r-- 1 seedsow seedsow {size:>12} Jan 01  1970 {name}")
            connection.sendall(("\r\n".join(lines) + ("\r\n" if lines else "")).encode("utf-8"))

        self.transfer(send)

    def receive_file(self, connection, name: str):
        chunks = []
        total = 0
        while True:
            chunk = connection.recv(min(16384, MAX_UPLOAD + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD:
                raise FTPError(552, "Upload exceeds the 64 KiB limit; nothing was written.")
            chunks.append(chunk)
        result = self.server.game_request("P", name, b"".join(chunks))
        expected = f"OK\t{name}\t{total}\n".encode("ascii")
        if result != expected:
            raise RuntimeError("Game did not confirm the expected filename and byte count.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="IPv4 loopback address only")
    parser.add_argument("--port", type=int, default=2121)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    args = parser.parse_args()
    from transport import GameTransport

    transport = GameTransport(config_path=args.config)
    if transport.check_ready() is False:
        raise RuntimeError("The game is not displaying the installed SF01 receiver.")
    with GameFTPServer((args.host, args.port), transport) as server:
        host, port = server.server_address
        print(f"Seedsow FTP: ftp://{host}:{port}  user=seedsow password=seedsow", flush=True)
        print("Game access is serialized. Set the FTP client timeout to at least 600 seconds.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.stop_requested.set()
            # Finish an in-flight hardware operation; queued requests fail above.
            with server.game_lock:
                pass
    print("FTP server stopped.", flush=True)


if __name__ == "__main__":
    main()
