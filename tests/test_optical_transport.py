import importlib.util
import pathlib
import sys
import unittest
import zlib
from PIL import Image, ImageDraw

path = pathlib.Path(__file__).resolve().parents[1] / 'transport.py'
sys.path.insert(0, str(path.parent))
spec = importlib.util.spec_from_file_location('transport', path)
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)


class OpticalTests(unittest.TestCase):
    def picture(self, payload, page, level):
        count = max(1, (len(payload) + 511) // 512)
        raw = t.HEADER.pack(b'SF01', 123, len(payload), page, count, zlib.adler32(payload), 0)
        raw += payload[page * 512:(page + 1) * 512]
        raw = raw.ljust(576, b'\0')
        image = Image.new('RGB', (1280, 720))
        draw = ImageDraw.Draw(image)
        for bit in range(4608):
            if raw[bit // 8] & (1 << (7 - bit % 8)):
                x, y = 160 + bit % 96 * 10, 120 + bit // 96 * 10
                draw.rectangle((x, y, x + 9, y + 9), fill=(level,) * 3)
        return image

    def test_binary_pages_and_idle_dimming(self):
        data = bytes(range(256)) * 3 + b'\0\xffEND'
        for level in (255, 40):
            pages = [t.decode_frame(self.picture(data, page, level)) for page in (0, 1)]
            self.assertEqual(b''.join(page['payload'] for page in pages), data)
            self.assertEqual(pages[0]['checksum'], zlib.adler32(data))

    def test_empty(self):
        self.assertEqual(t.decode_frame(self.picture(b'', 0, 255))['payload'], b'')

    def test_blank_rejected(self):
        with self.assertRaises(ValueError):
            t.decode_frame(Image.new('RGB', (1280, 720)))


if __name__ == '__main__':
    unittest.main()
