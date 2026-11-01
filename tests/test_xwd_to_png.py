"""The XWD to PNG converter must not exchange red and blue.

Every screenshot of this app is taken through this script, so a
channel swap here made the UI's own colours wrong in the images used
to review the changes: an amber warning (#996600) came out blue, and
a red error came out green. The screenshots looked like the app
chose those colours.

The script is a script rather than a module in the package, so it is
loaded by path.
"""
import importlib.util
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

_spec = importlib.util.spec_from_file_location(
    "xwd_to_png", ROOT / "scripts" / "xwd_to_png.py"
)
xwd_to_png = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(xwd_to_png)


def _xwd(width, height, pixels, byte_order=0, bpp=24):
    """Build an XWD blob from (r, g, b) pixels.

    byte_order 0 is LSBFirst, which stores pixels as B, G, R;
    byte_order 1 is MSBFirst, which stores them as R, G, B.

    xwd here produces 32-bit pixels, which is where the swap was: the
    24-bit path was always right, so a test built on 24-bit pixels
    passed against the broken code.
    """
    depth = bpp
    bytes_per_line = width * (bpp // 8)
    header = [
        100,        # header_size
        7,          # file_version
        2,          # pixmap_format
        depth,      # pixmap_depth
        width,
        height,
        0,          # xoffset
        byte_order,  # byte_order
        32,         # bitmap_unit
        0,          # bitmap_bit_order
        32,         # bitmap_pad
        depth,      # bits_per_pixel
        bytes_per_line,
        4,          # visual_class: TrueColor
        0xFF0000,   # red_mask
        0x00FF00,   # green_mask
        0x0000FF,   # blue_mask
        8,          # bits_per_rgb
        0,          # colormap_entries
        0,          # ncolors
        width,
        height,
        0, 0, 0,
    ]
    assert len(header) == 25
    blob = struct.pack(f">{len(header)}I", *header)
    data = bytearray()
    for r, g, b in pixels:
        if bpp == 32:
            data += bytes((0xFF, r, g, b) if byte_order == 1 else (b, g, r, 0xFF))
        else:
            data += bytes((r, g, b) if byte_order == 1 else (b, g, r))
    return blob + bytes(data)


class TestXwdToPng(unittest.TestCase):
    """A pixel comes out of the converter as the colour it went in."""

    def _pixel(self, pixel, byte_order=0, bpp=24):
        blob = _xwd(1, 1, [pixel], byte_order, bpp)
        width, height, rows = xwd_to_png.parse_xwd(blob)
        self.assertEqual((width, height), (1, 1))
        return tuple(rows[0][:3])

    def test_amber_survives(self):
        # The colour the Diagnostics tab uses for a warning.
        self.assertEqual(self._pixel((0x99, 0x66, 0x00)), (0x99, 0x66, 0x00))

    def test_red_survives(self):
        self.assertEqual(self._pixel((0xCC, 0x00, 0x00)), (0xCC, 0x00, 0x00))

    def test_blue_survives(self):
        self.assertEqual(self._pixel((0x00, 0x00, 0xCC)), (0x00, 0x00, 0xCC))

    def test_green_survives(self):
        self.assertEqual(self._pixel((0x00, 0x66, 0x00)), (0x00, 0x66, 0x00))

    def test_a_big_endian_dump_survives_too(self):
        self.assertEqual(
            self._pixel((0x99, 0x66, 0x00), byte_order=1), (0x99, 0x66, 0x00)
        )

    def test_red_and_blue_are_not_exchanged(self):
        # The regression this whole file is about.
        self.assertEqual(self._pixel((0x11, 0x22, 0x33))[0], 0x11)
        self.assertEqual(self._pixel((0x11, 0x22, 0x33))[2], 0x33)

    def test_a_32_bit_amber_survives(self):
        # 32-bit is what xwd produces here, and 32-bit is where the
        # swap was: the 24-bit path was always right, so the 24-bit
        # tests above passed against the broken code.
        self.assertEqual(
            self._pixel((0x99, 0x66, 0x00), bpp=32), (0x99, 0x66, 0x00)
        )

    def test_a_32_bit_red_does_not_come_out_blue(self):
        pixel = self._pixel((0xCC, 0x00, 0x00), bpp=32)
        self.assertEqual(pixel, (0xCC, 0x00, 0x00))

    def test_a_32_bit_blue_does_not_come_out_red(self):
        pixel = self._pixel((0x00, 0x00, 0xCC), bpp=32)
        self.assertEqual(pixel, (0x00, 0x00, 0xCC))

    def test_a_32_bit_big_endian_dump_survives(self):
        self.assertEqual(
            self._pixel((0x99, 0x66, 0x00), byte_order=1, bpp=32),
            (0x99, 0x66, 0x00),
        )

    def test_every_32_bit_pixel_of_a_row_keeps_its_place(self):
        pixels = [(0x10, 0x20, 0x30), (0x40, 0x50, 0x60), (0x70, 0x80, 0x90)]
        blob = _xwd(3, 1, pixels, bpp=32)
        _, _, rows = xwd_to_png.parse_xwd(blob)
        # The output is three bytes per pixel -- the alpha byte is
        # dropped, not kept.
        self.assertEqual(
            [tuple(rows[0][i:i + 3]) for i in (0, 3, 6)], pixels
        )

    def test_every_pixel_of_a_row_keeps_its_place(self):
        pixels = [(0x10, 0x20, 0x30), (0x40, 0x50, 0x60), (0x70, 0x80, 0x90)]
        blob = _xwd(3, 1, pixels)
        _, _, rows = xwd_to_png.parse_xwd(blob)
        self.assertEqual(
            [tuple(rows[0][i:i + 3]) for i in (0, 3, 6)], pixels
        )

    def test_the_png_is_written_with_the_same_colours(self):
        import zlib

        blob = _xwd(1, 1, [(0x99, 0x66, 0x00)])
        width, height, rows = xwd_to_png.parse_xwd(blob)
        png_path = Path(self._tmpdir) / "out.png"
        xwd_to_png.write_png(str(png_path), width, height, rows)
        data = png_path.read_bytes()
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        # The IDAT holds the raw scanlines; decompress and read the
        # three bytes of the single pixel back out.
        idat = b""
        pos = 8
        while pos < len(data):
            length = struct.unpack(">I", data[pos:pos + 4])[0]
            tag = data[pos + 4:pos + 8]
            body = data[pos + 8:pos + 8 + length]
            if tag == b"IDAT":
                idat += body
            pos += 12 + length
        raw = zlib.decompress(idat)
        self.assertEqual(raw[1:4], bytes((0x99, 0x66, 0x00)))

    def setUp(self):
        import tempfile

        self._tmpdir = tempfile.mkdtemp(prefix="pulsar_xwd_")


if __name__ == "__main__":
    unittest.main()
