"""EasyCon uploads and OBS optical downloads for the Seedsow FTP bridge."""
from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import threading
import time
import zlib

import serial
import websockets
from PIL import Image
from optical import HEADER, decode_frame

BUTTONS = {'Y': 1, 'B': 2, 'A': 4, 'X': 8, 'L': 16, 'R': 32}
MAX_SIZE = 65536


def encode(buttons=0):
    value = int.from_bytes(struct.pack('>HB4B', buttons, 8, 128, 128, 128, 128), 'big')
    result = bytearray((value >> shift) & 127 for shift in range(49, -1, -7))
    result[-1] |= 128
    return bytes(result)


class Controller:
    def __init__(self, config):
        self.config = config

    def __enter__(self):
        self.s = serial.Serial(port=None, baudrate=115200, timeout=.7, write_timeout=1)
        self.s.dtr = False
        self.s.rts = False
        self.s.port = self.config.get('serial_port', 'COM6')
        self.s.open()
        try:
            self.s.reset_input_buffer()
            self.s.write(b'\xa5\xa5')
            self.s.flush()
            time.sleep(.03)
            self.s.write(b'\x81')
            self.s.flush()
            reply = self.s.read(1)
            if reply != b'\x80':
                raise RuntimeError('EasyCon handshake failed: ' + reply.hex())
        except BaseException:
            self.s.close()
            raise
        return self

    def report(self, buttons=0):
        self.s.write(encode(buttons))
        self.s.flush()
        reply = self.s.read(1)
        # The S3 firmware stores the new report even when its USB endpoint is
        # temporarily busy (00); screen checks establish console delivery.
        if reply not in (b'\x00', b'\xff'):
            raise RuntimeError('EasyCon report failed: ' + reply.hex())

    def tap(self, name, down=.16, up=.24):
        self.report(BUTTONS[name])
        time.sleep(down)
        self.report()
        time.sleep(up)

    def send(self, source: bytes, name: str):
        encoded_name = name.encode('ascii')
        packet = b'SSE2\x01' + struct.pack('<II', len(source), zlib.adler32(source))
        packet += bytes([len(encoded_name)]) + encoded_name + source
        print(f'Controller upload: {len(source)} source bytes, CRC {zlib.adler32(source):08X}', flush=True)
        self.report()
        time.sleep(1)
        clock = 0
        prepare = self.config.get('prepare_seconds', .03)
        hold = self.config.get('hold_seconds', .06)
        for index, value in enumerate(packet):
            for nibble in (value >> 4, value & 15):
                mask = sum(BUTTONS[key] for bit, key in enumerate(('A', 'B', 'X', 'Y')) if nibble & (1 << bit))
                self.report(mask | clock)
                time.sleep(prepare)
                clock ^= BUTTONS['R']
                self.report(mask | clock)
                time.sleep(hold)
            if (index + 1) % 100 == 0:
                print(f'  {index + 1}/{len(packet)} bytes sent', flush=True)
        self.report()
        time.sleep(.5)

    def __exit__(self, *exc):
        try:
            self.report()
        except Exception:
            if exc[0] is None:
                raise
        finally:
            self.s.close()


class _OBSCapture:
    """One connection, authentication and source lookup on one event loop."""
    def __init__(self, config, metrics=None):
        self.config = config
        self.metrics = metrics
        self.ws = None
        self.source = config.get('obs_source')
        self.request_id = 0

    async def connect(self):
        config_file = Path(os.environ['APPDATA']) / 'obs-studio/plugin_config/obs-websocket/config.json'
        obs = json.loads(config_file.read_text(encoding='utf-8-sig'))
        port = self.config.get('obs_port', obs.get('server_port', 4455))
        self.ws = await websockets.connect(f'ws://127.0.0.1:{port}', max_size=20 * 1024 * 1024, open_timeout=8)
        if self.metrics is not None:
            self.metrics['connections'] += 1
        hello = json.loads(await asyncio.wait_for(self.ws.recv(), 8))['d']
        identify = {'rpcVersion': 1}
        if 'authentication' in hello:
            auth = hello['authentication']
            secret = base64.b64encode(hashlib.sha256((obs['server_password'] + auth['salt']).encode()).digest()).decode()
            identify['authentication'] = base64.b64encode(hashlib.sha256((secret + auth['challenge']).encode()).digest()).decode()
        await self.ws.send(json.dumps({'op': 1, 'd': identify}))
        response = json.loads(await asyncio.wait_for(self.ws.recv(), 8))
        if response['op'] != 2:
            raise RuntimeError('OBS identification failed')
        if not self.source:
            scene = (await self.request('GetCurrentProgramScene'))['currentProgramSceneName']
            items = (await self.request('GetSceneItemList', {'sceneName': scene}))['sceneItems']
            self.source = next((item['sourceName'] for item in items if item.get('inputKind') == 'dshow_input' and item['sceneItemEnabled']), None)
            if not self.source:
                raise RuntimeError('No enabled capture-device source in the current OBS scene')

    async def request(self, kind, data=None):
        self.request_id += 1
        request_id = str(self.request_id)
        await self.ws.send(json.dumps({'op': 6, 'd': {'requestType': kind, 'requestId': request_id, 'requestData': data or {}}}))
        while True:
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), 8))
            if msg['op'] == 7 and msg['d']['requestId'] == request_id:
                if not msg['d']['requestStatus']['result']:
                    raise RuntimeError('OBS request failed: ' + str(msg['d']['requestStatus']))
                return msg['d'].get('responseData', {})

    async def capture(self):
        if self.ws is None:
            await self.connect()
        if self.metrics is not None:
            self.metrics['captures'] += 1
        response = await self.request('GetSourceScreenshot', {'sourceName': self.source, 'imageFormat': 'png', 'imageWidth': 1280, 'imageHeight': 720})
        return Image.open(io.BytesIO(base64.b64decode(response['imageData'].split(',', 1)[1]))).convert('RGB')

    async def close(self):
        if self.ws is not None:
            await self.ws.close()


class GameTransport:
    def __init__(self, config_path: Path):
        self.config_path = Path(config_path)
        self.config = json.loads(self.config_path.read_text(encoding='utf-8-sig'))
        self.lock = threading.Lock()
        self.seq = int(time.time()) & 0xffffffff
        self.last_metrics = None
        self._local = threading.local()

    @contextmanager
    def _obs_session(self, metrics=None):
        # A standalone capture gets its own session. A request's captures share
        # its thread-local session, avoiding websocket reuse across event loops.
        current = getattr(self._local, 'obs_session', None)
        if current is not None:
            yield current
            return
        loop = asyncio.new_event_loop()
        client = _OBSCapture(self.config, metrics)
        self._local.obs_session = (loop, client)
        try:
            yield loop, client
        finally:
            try:
                loop.run_until_complete(client.close())
            finally:
                loop.close()
                del self._local.obs_session

    def capture(self):
        with self._obs_session() as (loop, client):
            return loop.run_until_complete(client.capture())

    def check_ready(self):
        frame = decode_frame(self.capture())
        return {key: value for key, value in frame.items() if key != 'payload'}

    def wait_frame(self, seq, page=0, timeout=20):
        deadline = time.monotonic() + timeout
        last_error = 'No matching response'
        while time.monotonic() < deadline:
            try:
                frame = decode_frame(self.capture())
                if frame['seq'] == 0 and frame['status'] == 1:
                    raise RuntimeError('Game receiver failed: ' + frame['payload'].decode('utf-8', errors='replace'))
                if frame['seq'] == seq and frame['page'] == page:
                    return frame
                last_error = f"Waiting for sequence {seq}, page {page}; saw {frame['seq']}/{frame['page']}"
            except ValueError as error:
                last_error = str(error)
            current = getattr(self._local, 'obs_session', None)
            if current is not None and current[1].metrics is not None:
                current[1].metrics['retries'] += 1
            time.sleep(.25)
        raise TimeoutError(last_error + '; no request was automatically replayed')

    def request(self, op: str, name: str = '', data: bytes = b'') -> bytes:
        if op not in ('L', 'G', 'P') or '\n' in name or '\r' in name:
            raise ValueError('Invalid game request')
        if len(data) > MAX_SIZE:
            raise ValueError('File exceeds 64 KiB')
        with self.lock:
            started = time.monotonic()
            setup_done = optical_done = None
            metrics = dict(request_setup=0.0, optical=0.0, finalize=0.0, total=0.0,
                           format=None, bytes=0, pages=0, captures=0, retries=0,
                           connections=0, page_times=[], success=False)
            self.last_metrics = metrics
            try:
                with self._obs_session(metrics):
                    self.check_ready()
                    self.seq = (self.seq + 1) & 0xffffffff or 1
                    source = f'{op}\n{self.seq}\n{name}\n'.encode('ascii') + data
                    with Controller(self.config) as controller:
                        controller.tap('L')
                        time.sleep(.45)
                        ready = self.wait_frame(0)
                        if ready['status'] or ready['payload'] != b'READY' or ready['checksum'] != zlib.adler32(b'READY'):
                            raise RuntimeError('The game has not confirmed READY for a new request')
                        controller.send(source, 'ftp-request')
                        setup_done = time.monotonic()
                        metrics['request_setup'] = setup_done - started

                        def receive_page(page):
                            page_start = time.monotonic()
                            captures, retries = metrics['captures'], metrics['retries']
                            if page:
                                controller.tap('R')
                            frame = self.wait_frame(self.seq, page)
                            metrics['page_times'].append(dict(
                                page=page, seconds=time.monotonic() - page_start,
                                captures=metrics['captures'] - captures,
                                retries=metrics['retries'] - retries))
                            return frame

                        first = receive_page(0)
                        metrics.update(format=first['format'], bytes=first['total'],
                                       pages=first['pages'], chunk_size=first['chunk_size'])
                        payload = first['payload']
                        for page in range(1, first['pages']):
                            frame = receive_page(page)
                            if any(frame[key] != first[key] for key in ('format', 'chunk_size', 'total', 'pages', 'checksum', 'status')):
                                raise RuntimeError('Bridge response changed while reading pages')
                            payload += frame['payload']
                        optical_done = time.monotonic()
                        metrics['optical'] = optical_done - setup_done
                    if len(payload) != first['total'] or zlib.adler32(payload) != first['checksum']:
                        raise RuntimeError('Optical download checksum mismatch')
                    if first['status']:
                        raise RuntimeError(payload.decode('utf-8', errors='replace'))
                metrics['success'] = True
                print(f'Game {op} {name}: {len(payload)} response bytes verified', flush=True)
                return payload
            except BaseException as error:
                metrics['error'] = str(error)
                raise
            finally:
                finished = time.monotonic()
                metrics['total'] = finished - started
                if setup_done is None:
                    metrics['request_setup'] = metrics['total']
                elif optical_done is None:
                    metrics['optical'] = finished - setup_done
                else:
                    metrics['finalize'] = finished - optical_done


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'install', 'leave'))
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('config.json'))
    args = parser.parse_args()
    transport = GameTransport(args.config)
    if args.action == 'check':
        print(transport.check_ready())
    elif args.action == 'leave':
        transport.check_ready()
        with Controller(transport.config) as controller:
            controller.tap('L', down=1.7)
        print('Returned to the Lua editor.')
    else:
        # User first chooses Receive PC in the editor, as documented.
        saved_name = 'ftp_console_color.lua'
        source = Path(__file__).with_name('ftp_bootstrap.lua').read_bytes()
        with Controller(transport.config) as controller:
            controller.send(source, 'F000_' + saved_name)
            time.sleep(1)
            transport.capture().save(Path(__file__).with_name('install-receipt.png'))
        print('Input stream finished. Check the GAME for PC FILE SAVED: ' + saved_name + '.')
        print('Only after that confirmation: press L, release, then Y to start the bridge.')
        print('If the game reports a transfer error, do not run the buffer; retry Receive PC.')
        print('START.cmd will verify the optical bridge before opening the FTP port.')


if __name__ == '__main__':
    main()
