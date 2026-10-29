import struct
import unittest
import zlib

from PIL import Image, ImageDraw

from optical import COLOR_CHUNK, COLOR_PALETTE, HEADER, decode_frame


def picture(payload, page=0, transform=lambda color: color, corrupt=None):
    count = max(1, (len(payload) + COLOR_CHUNK - 1) // COLOR_CHUNK)
    head = HEADER.pack(b'SF02', 2468, len(payload), page, count, zlib.adler32(payload), 0)
    chunk = payload[page * COLOR_CHUNK:(page + 1) * COLOR_CHUNK]
    raw = bytearray(head + struct.pack('<I', zlib.adler32(head + chunk)) + chunk)
    raw = raw.ljust(1728, b'\0')
    if corrupt is not None:
        raw[corrupt] ^= 1
    colors = [transform(color) for color in COLOR_PALETTE]
    result = Image.new('RGB', (1280, 720), colors[0])
    draw = ImageDraw.Draw(result)
    for index, color in enumerate(colors):
        draw.rectangle((160 + index * 120, 70, 259 + index * 120, 99), fill=color)
    symbols = []
    for start in range(0, len(raw), 3):
        value = int.from_bytes(raw[start:start + 3], 'big')
        symbols.extend((value >> shift) & 7 for shift in range(21, -1, -3))
    for index, symbol in enumerate(symbols):
        x, y = 160 + index % 96 * 10, 120 + index // 96 * 10
        draw.rectangle((x, y, x + 9, y + 9), fill=colors[symbol])
    return result


class ColorOpticalTests(unittest.TestCase):
    def test_binary_multiple_pages(self):
        payload = bytes(range(256)) * 17 + b'\0\xffend'
        pages = [decode_frame(picture(payload, page)) for page in range(3)]
        self.assertEqual(b''.join(page['payload'] for page in pages), payload)
        self.assertEqual(pages[0]['format'], 'SF02')
        self.assertEqual(pages[0]['chunk_size'], 1664)

    def test_empty_boundary_and_maximum_last_page(self):
        for length in (0, 1, 1663, 1664, 1665, 65536):
            payload = (bytes(range(256)) * 256)[:length]
            page = max(0, (length - 1) // COLOR_CHUNK)
            frame = decode_frame(picture(payload, page))
            self.assertEqual(frame['payload'], payload[page * COLOR_CHUNK:])
            self.assertEqual(frame['total'], length)

    def test_calibration_tracks_dimming_and_color_cast(self):
        payload = bytes(range(256)) * 4
        for transform in (
            lambda c: tuple(value // 6 for value in c),
            lambda c: (int(c[0] * .8) + 8, int(c[1] * .9), int(c[2] * .7) + 12),
        ):
            self.assertEqual(decode_frame(picture(payload, transform=transform))['payload'], payload)

    def test_page_checksum_rejects_valid_but_wrong_color(self):
        with self.assertRaisesRegex(ValueError, 'checksum'):
            decode_frame(picture(bytes(range(256)), corrupt=HEADER.size + 4 + 32))

    def test_header_corruption_rejected(self):
        with self.assertRaises(ValueError):
            decode_frame(picture(b'hello', corrupt=4))

    def test_collapsed_calibration_rejected(self):
        with self.assertRaises(ValueError):
            decode_frame(picture(b'hello', transform=lambda c: (20, 20, 20)))


if __name__ == '__main__':
    unittest.main()
