"""OBS session/request tests use fake sockets and controllers, never hardware."""

import asyncio
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import transport as t


class FakeWebSocket:
    def __init__(self, fail_capture=None):
        self.loop = asyncio.get_running_loop()
        self.received = [dict(op=0, d=dict(authentication=dict(salt='salt', challenge='challenge')))]
        self.sent = []
        self.closed = False
        self.captures = 0
        self.fail_capture = fail_capture
        png = io.BytesIO()
        Image.new('RGB', (1, 1), (20, 40, 60)).save(png, 'PNG')
        self.image_data = 'data:image/png;base64,' + base64.b64encode(png.getvalue()).decode()

    def assert_loop(self):
        if asyncio.get_running_loop() is not self.loop:
            raise AssertionError('OBS websocket used on a different event loop')

    async def send(self, data):
        self.assert_loop()
        message = json.loads(data)
        self.sent.append(message)
        if message['op'] == 1:
            self.received.append(dict(op=2, d={}))
            return
        request = message['d']
        kind = request['requestType']
        if kind == 'GetCurrentProgramScene':
            result = dict(currentProgramSceneName='Scene')
        elif kind == 'GetSceneItemList':
            result = dict(sceneItems=[dict(sourceName='Capture', inputKind='dshow_input', sceneItemEnabled=True)])
        elif kind == 'GetSourceScreenshot':
            self.captures += 1
            if self.captures == self.fail_capture:
                raise ConnectionError('simulated OBS disconnect')
            result = dict(imageData=self.image_data)
        else:
            raise AssertionError(kind)
        self.received.append(dict(op=7, d=dict(requestId=request['requestId'],
                                  requestStatus=dict(result=True), responseData=result)))

    async def recv(self):
        self.assert_loop()
        return json.dumps(self.received.pop(0))

    async def close(self):
        self.assert_loop()
        self.closed = True


class FakeController:
    instances = []

    def __init__(self, config):
        self.taps = []
        self.uploads = []
        self.closed = False
        self.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *error):
        self.closed = True

    def tap(self, name):
        self.taps.append(name)

    def send(self, source, name):
        self.uploads.append((source, name))


def frame(payload, *, page=0, seq=101, format='SF02'):
    chunk = 1664 if format == 'SF02' else 512
    return dict(format=format, chunk_size=chunk, seq=seq, total=len(payload),
                page=page, pages=max(1, (len(payload) + chunk - 1) // chunk),
                checksum=zlib.adler32(payload), status=0,
                payload=payload[page * chunk:(page + 1) * chunk])


class TransportSessionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.config = root / 'config.json'
        self.config.write_text('{}', encoding='utf-8')
        obs = root / 'obs-studio/plugin_config/obs-websocket/config.json'
        obs.parent.mkdir(parents=True)
        obs.write_text(json.dumps(dict(server_password='test-password')), encoding='utf-8')
        self.connections = []
        self.fail_capture = None

        async def connect(*args, **kwargs):
            socket = FakeWebSocket(self.fail_capture)
            self.connections.append(socket)
            return socket

        FakeController.instances = []
        for patcher in (patch.dict(t.os.environ, APPDATA=str(root)),
                        patch.object(t.websockets, 'connect', side_effect=connect),
                        patch.object(t, 'Controller', FakeController),
                        patch.object(t.time, 'sleep', return_value=None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def transport(self):
        transport = t.GameTransport(self.config)
        transport.seq = 100
        return transport

    def test_both_formats_share_one_session_and_report_separate_timings(self):
        payload = bytes(range(256)) * 8
        for format in ('SF01', 'SF02'):
            with self.subTest(format=format):
                transport = self.transport()
                first = frame(payload, format=format)
                ready = frame(b'READY', seq=0, format=format)
                decoded = [ready, ready] + [frame(payload, page=page, format=format) for page in range(first['pages'])]
                before = len(self.connections)
                with patch.object(t, 'decode_frame', side_effect=decoded):
                    self.assertEqual(transport.request('G', 'sample.bin'), payload)
                self.assertEqual(len(self.connections) - before, 1)
                socket = self.connections[-1]
                self.assertTrue(socket.closed)
                self.assertEqual(sum(message['op'] == 1 for message in socket.sent), 1)
                kinds = [message['d']['requestType'] for message in socket.sent if message['op'] == 6]
                self.assertEqual(kinds.count('GetCurrentProgramScene'), 1)
                self.assertEqual(kinds.count('GetSceneItemList'), 1)
                ids = [message['d']['requestId'] for message in socket.sent if message['op'] == 6]
                self.assertEqual(len(set(ids)), len(ids))
                controller = FakeController.instances[-1]
                self.assertEqual(controller.taps, ['L'] + ['R'] * (first['pages'] - 1))
                self.assertEqual(len(controller.uploads), 1)
                metrics = transport.last_metrics
                self.assertEqual((metrics['format'], metrics['bytes'], metrics['pages']),
                                 (format, len(payload), first['pages']))
                self.assertEqual((metrics['connections'], metrics['captures'], metrics['retries']),
                                 (1, first['pages'] + 2, 0))
                self.assertEqual(len(metrics['page_times']), first['pages'])
                self.assertTrue(all(page['captures'] == 1 for page in metrics['page_times']))
                self.assertTrue(metrics['success'])
                self.assertGreater(metrics['optical'], 0)
                self.assertAlmostEqual(metrics['total'], metrics['request_setup'] + metrics['optical'] + metrics['finalize'])

    def test_bad_page_and_stale_frame_only_recapture_without_retapping(self):
        transport = self.transport()
        payload = b'x' * 2000
        ready = frame(b'READY', seq=0)
        first, second = frame(payload), frame(payload, page=1)
        with patch.object(t, 'decode_frame', side_effect=[ready, ready, first, ValueError('page Adler mismatch'), first, second]):
            self.assertEqual(transport.request('G', 'sample.bin'), payload)
        self.assertEqual(FakeController.instances[-1].taps, ['L', 'R'])
        self.assertEqual(len(FakeController.instances[-1].uploads), 1)
        self.assertEqual(transport.last_metrics['connections'], 1)
        self.assertEqual(transport.last_metrics['retries'], 2)
        self.assertEqual(transport.last_metrics['page_times'][1]['captures'], 3)

    def test_response_format_change_is_rejected(self):
        transport = self.transport()
        payload = b'x' * 2000
        ready = frame(b'READY', seq=0)
        changed = frame(payload, page=1)
        changed['format'] = 'SF01'
        with patch.object(t, 'decode_frame', side_effect=[ready, ready, frame(payload), changed]):
            with self.assertRaisesRegex(RuntimeError, 'response changed'):
                transport.request('G', 'sample.bin')
        self.assertFalse(transport.last_metrics['success'])
        self.assertTrue(self.connections[-1].closed)
        self.assertEqual(FakeController.instances[-1].taps, ['L', 'R'])

    def test_obs_disconnect_does_not_reconnect_or_replay_write(self):
        transport = self.transport()
        self.fail_capture = 3
        ready = frame(b'READY', seq=0)
        with patch.object(t, 'decode_frame', side_effect=[ready, ready]):
            with self.assertRaisesRegex(ConnectionError, 'simulated OBS disconnect'):
                transport.request('P', 'sample.lua', b'print(1)')
        self.assertEqual(len(self.connections), 1)
        self.assertTrue(self.connections[0].closed)
        controller = FakeController.instances[-1]
        self.assertEqual(len(controller.uploads), 1)
        self.assertTrue(controller.uploads[0][0].startswith(b'P\n101\nsample.lua\n'))
        self.assertEqual(controller.taps, ['L'])
        self.assertTrue(controller.closed)
        self.assertFalse(transport.last_metrics['success'])
        self.assertIn('simulated OBS disconnect', transport.last_metrics['error'])

    def test_standalone_capture_closes_its_own_session(self):
        transport = self.transport()
        for _ in range(2):
            self.assertEqual(transport.capture().getpixel((0, 0)), (20, 40, 60))
        self.assertEqual(len(self.connections), 2)
        self.assertTrue(all(socket.closed for socket in self.connections))
        self.assertIsNone(transport.last_metrics)


if __name__ == '__main__':
    unittest.main()
