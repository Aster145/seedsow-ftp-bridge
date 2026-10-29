"""Backward-compatible SF01 monochrome and SF02 calibrated 8-color frames."""
import math
import struct
import zlib

from PIL import Image

HEADER = struct.Struct('<4sIIIIIB')
MAX_SIZE = 65536
COLOR_CHUNK = 1664
COLOR_PALETTE = tuple(((index >> 2 & 1) * 255, (index >> 1 & 1) * 255, (index & 1) * 255) for index in range(8))


def _sample(pixels, x, y):
    # Cell centers are well away from chroma-subsampling and scaling boundaries.
    points = [pixels[x, y], pixels[x - 1, y], pixels[x + 1, y], pixels[x, y - 1], pixels[x, y + 1]]
    return tuple(sorted(point[channel] for point in points)[2] for channel in range(3))


def _distance(a, b):
    return sum((left - right) ** 2 for left, right in zip(a, b))


def color_symbols(picture):
    """Read calibrated symbol values, also usable by the hardware color probe."""
    pixels = picture.convert('RGB').load()
    palette = [_sample(pixels, 210 + index * 120, 85) for index in range(8)]
    separation = min(_distance(palette[a], palette[b]) for a in range(8) for b in range(a))
    if separation < 144:
        raise ValueError('Color calibration swatches are not distinguishable')
    symbols = []
    for y in range(48):
        for x in range(96):
            point = _sample(pixels, 165 + x * 10, 125 + y * 10)
            distances = [_distance(point, color) for color in palette]
            index = min(range(8), key=distances.__getitem__)
            ordered = sorted(distances)
            if ordered[0] > separation * .16 or ordered[1] - ordered[0] < separation * .1:
                raise ValueError(f'Ambiguous color at cell {x},{y}')
            symbols.append(index)
    return symbols, palette


def _color_bytes(picture):
    symbols, _ = color_symbols(picture)
    result = bytearray()
    for start in range(0, len(symbols), 8):
        value = 0
        for symbol in symbols[start:start + 8]:
            value = (value << 3) | symbol
        result.extend(value.to_bytes(3, 'big'))
    return result


def _mono_bytes(picture):
    pixels = picture.convert('L').load()
    samples = [pixels[165 + x * 10, 125 + y * 10] for y in range(48) for x in range(96)]
    low, high = min(samples), max(samples)
    if high - low < 12:
        raise ValueError('No optical bridge contrast')
    threshold = (low + high) / 2
    result = bytearray()
    for start in range(0, len(samples), 8):
        value = 0
        for sample in samples[start:start + 8]:
            value = (value << 1) | (sample > threshold)
        result.append(value)
    return result


def _frame(raw, magic, chunk_size):
    actual, seq, total, page, pages, checksum, status = HEADER.unpack_from(raw)
    if actual != magic:
        raise ValueError('The game is not showing a supported FTP bridge grid')
    if total > MAX_SIZE or pages != max(1, math.ceil(total / chunk_size)) or page >= pages or status not in (0, 1):
        raise ValueError('Invalid bridge page header')
    size = min(chunk_size, total - page * chunk_size)
    offset = HEADER.size
    if magic == b'SF02':
        page_checksum, = struct.unpack_from('<I', raw, offset)
        offset += 4
        actual_checksum = zlib.adler32(bytes(raw[:HEADER.size]) + bytes(raw[offset:offset + size]))
        if page_checksum != actual_checksum:
            raise ValueError('Color page checksum mismatch; capture the same page again')
    return dict(seq=seq, total=total, page=page, pages=pages, checksum=checksum, status=status,
                payload=bytes(raw[offset:offset + size]), format=magic.decode('ascii'), chunk_size=chunk_size)


def decode_frame(picture: Image.Image):
    if picture.size != (1280, 720):
        raise ValueError('Expected a 1280x720 game source screenshot')
    # SF01 remains the inexpensive path for existing installations. SF02's
    # header differs after luminance thresholding and requires its color row.
    try:
        mono = _mono_bytes(picture)
        if mono[:4] == b'SF01':
            return _frame(mono, b'SF01', 512)
    except ValueError:
        pass
    return _frame(_color_bytes(picture), b'SF02', COLOR_CHUNK)
