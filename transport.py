"""EasyCon uploads and OBS optical downloads for the Seedsow FTP bridge."""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import struct
import threading
import time
import zlib

import serial
import websockets
from PIL import Image

BUTTONS = {'Y': 1, 'B': 2, 'A': 4, 'X': 8, 'L': 16, 'R': 32}
HEADER = struct.Struct('<4sIIIIIB')
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


def decode_frame(picture: Image.Image):
    """Decode the fixed 96x48 grid; adapt to console idle dimming."""
    if picture.size != (1280, 720):
        raise ValueError('Expected a 1280x720 game source screenshot')
    grey = picture.convert('L')
    samples = [grey.getpixel((165 + x * 10, 125 + y * 10)) for y in range(48) for x in range(96)]
    low, high = min(samples), max(samples)
    if high - low < 12:
        raise ValueError('No optical bridge contrast')
    threshold = (low + high) / 2
    raw = bytearray()
    for start in range(0, len(samples), 8):
        value = 0
        for sample in samples[start:start + 8]:
            value = (value << 1) | (sample > threshold)
        raw.append(value)
    magic, seq, total, page, pages, checksum, status = HEADER.unpack_from(raw)
    if magic != b'SF01':
        raise ValueError('The game is not showing the FTP bridge grid')
    if total > MAX_SIZE or pages != max(1, math.ceil(total / 512)) or page >= pages or status not in (0, 1):
        raise ValueError('Invalid bridge page header')
    size = min(512, total - page * 512)
    return dict(seq=seq, total=total, page=page, pages=pages, checksum=checksum,
                status=status, payload=bytes(raw[HEADER.size:HEADER.size + size]))


class GameTransport:
    def __init__(self, config_path: Path):
        self.config_path = Path(config_path)
        self.config = json.loads(self.config_path.read_text(encoding='utf-8-sig'))
        self.lock = threading.Lock()
        self.seq = int(time.time()) & 0xffffffff

    async def _capture(self):
        config_file = Path(os.environ['APPDATA']) / 'obs-studio/plugin_config/obs-websocket/config.json'
        obs = json.loads(config_file.read_text(encoding='utf-8-sig'))
        port = self.config.get('obs_port', obs.get('server_port', 4455))
        async with websockets.connect(f'ws://127.0.0.1:{port}', max_size=20 * 1024 * 1024, open_timeout=8) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 8))['d']
            identify = {'rpcVersion': 1}
            if 'authentication' in hello:
                auth = hello['authentication']
                secret = base64.b64encode(hashlib.sha256((obs['server_password'] + auth['salt']).encode()).digest()).decode()
                identify['authentication'] = base64.b64encode(hashlib.sha256((secret + auth['challenge']).encode()).digest()).decode()
            await ws.send(json.dumps({'op': 1, 'd': identify}))
            response = json.loads(await asyncio.wait_for(ws.recv(), 8))
            if response['op'] != 2:
                raise RuntimeError('OBS identification failed')

            async def request(kind, data=None):
                await ws.send(json.dumps({'op': 6, 'd': {'requestType': kind, 'requestId': kind, 'requestData': data or {}}}))
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 8))
                    if msg['op'] == 7 and msg['d']['requestId'] == kind:
                        if not msg['d']['requestStatus']['result']:
                            raise RuntimeError('OBS request failed: ' + str(msg['d']['requestStatus']))
                        return msg['d'].get('responseData', {})

            source = self.config.get('obs_source')
            if not source:
                scene = (await request('GetCurrentProgramScene'))['currentProgramSceneName']
                items = (await request('GetSceneItemList', {'sceneName': scene}))['sceneItems']
                source = next((item['sourceName'] for item in items if item.get('inputKind') == 'dshow_input' and item['sceneItemEnabled']), None)
                if not source:
                    raise RuntimeError('No enabled capture-device source in the current OBS scene')
            response = await request('GetSourceScreenshot', {'sourceName': source, 'imageFormat': 'png', 'imageWidth': 1280, 'imageHeight': 720})
            return Image.open(io.BytesIO(base64.b64decode(response['imageData'].split(',', 1)[1]))).convert('RGB')

    def capture(self):
        return asyncio.run(self._capture())

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
            time.sleep(.25)
        raise TimeoutError(last_error + '; no request was automatically replayed')

    def request(self, op: str, name: str = '', data: bytes = b'') -> bytes:
        if op not in ('L', 'G', 'P') or '\n' in name or '\r' in name:
            raise ValueError('Invalid game request')
        if len(data) > MAX_SIZE:
            raise ValueError('File exceeds 64 KiB')
        with self.lock:
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
                first = self.wait_frame(self.seq)
                payload = first['payload']
                for page in range(1, first['pages']):
                    controller.tap('R')
                    frame = self.wait_frame(self.seq, page)
                    if any(frame[key] != first[key] for key in ('total', 'pages', 'checksum', 'status')):
                        raise RuntimeError('Bridge response changed while reading pages')
                    payload += frame['payload']
            if len(payload) != first['total'] or zlib.adler32(payload) != first['checksum']:
                raise RuntimeError('Optical download checksum mismatch')
            if first['status']:
                raise RuntimeError(payload.decode('utf-8', errors='replace'))
            print(f'Game {op} {name}: {len(payload)} response bytes verified', flush=True)
            return payload


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
        source = Path(__file__).with_name('ftp_bootstrap.lua').read_bytes()
        with Controller(transport.config) as controller:
            controller.send(source, 'F000_ftp_console.lua')
            time.sleep(1)
            transport.capture().save(Path(__file__).with_name('install-receipt.png'))
        print('Input stream finished. Check the GAME for PC FILE SAVED: ftp_console.lua.')
        print('Only after that confirmation: press L, release, then Y to start the bridge.')
        print('If the game reports a transfer error, do not run the buffer; retry Receive PC.')
        print('START.cmd will verify the optical bridge before opening the FTP port.')


if __name__ == '__main__':
    main()
