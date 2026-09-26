import struct
import unittest
import zlib

from canvas_graphics import blend, corner_png, encode_png, mass_png
from theme import format_clock, format_value


def read_png(data: bytes) -> tuple[int, int, bytes]:
    """Décode un PNG RGBA non entrelacé produit par ``encode_png``."""

    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", data[16:24])
    offset, compressed = 8, b""
    while offset < len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        if kind == b"IDAT":
            compressed += data[offset + 8 : offset + 8 + length]
        offset += 12 + length
    raw = zlib.decompress(compressed)
    stride = width * 4 + 1
    pixels = b"".join(raw[row * stride + 1 : (row + 1) * stride] for row in range(height))
    return width, height, pixels


class FormattingTests(unittest.TestCase):
    def test_values_use_french_typography(self):
        self.assertEqual(format_value(120.5, "°"), "120,5°")
        self.assertEqual(format_value(-10, "°"), "−10°")
        self.assertEqual(format_value(1, "m"), "1 m")

    def test_clock_has_two_decimals(self):
        self.assertEqual(format_clock(4.3), "t = 4,30 s")


class GraphicsTests(unittest.TestCase):
    def test_blend_interpolates_towards_foreground(self):
        self.assertEqual(blend("#ffffff", "#000000", 0), "#000000")
        self.assertEqual(blend("#ffffff", "#000000", 1), "#ffffff")
        self.assertEqual(blend("#ff0000", "#0000ff", 0.5), "#800080")

    def test_png_round_trip(self):
        pixels = bytes([255, 0, 0, 255, 0, 0, 255, 128])
        self.assertEqual(read_png(encode_png(2, 1, pixels)), (2, 1, pixels))

    def test_corner_is_opaque_inside_and_transparent_outside(self):
        width, height, pixels = read_png(corner_png(8, 1, "#101010", "#808080", "nw"))
        self.assertEqual((width, height), (8, 8))
        alpha = lambda x, y: pixels[(y * width + x) * 4 + 3]
        self.assertEqual(alpha(0, 0), 0)
        self.assertEqual(alpha(7, 7), 255)

    def test_mass_sprite_is_opaque_at_center(self):
        width, height, pixels = read_png(mass_png("#58c4dd", 6, 15))
        center = ((height // 2) * width + width // 2) * 4
        self.assertEqual(pixels[center + 3], 255)
        self.assertEqual(pixels[3], 0)


if __name__ == "__main__":
    unittest.main()
