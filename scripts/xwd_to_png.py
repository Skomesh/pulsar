"""Convert an XWD dump to PNG.

Pillow 13 dropped its XwdImagePlugin and ImageMagick is not installed here,
so this parses the format directly. XWD is a 25-word big-endian header, an
optional window-name string, an optional colour map, then raw pixels in
32-bit or 16-bit chunks.

Written so the app can be screenshotted on a headless box, which is the
only way to actually see the UI changes in this project.
"""
import struct
import sys
import zlib

HEADER_FIELDS = 25


def parse_xwd(blob):
    """Return (width, height, rows) where rows is a list of RGB bytearrays."""
    if len(blob) < HEADER_FIELDS * 4:
        raise ValueError("file too short to be xwd")
    (
        header_size, file_version, pixmap_format, pixmap_depth,
        pixmap_width, pixmap_height, xoffset, byte_order, bitmap_unit,
        bitmap_bit_order, bitmap_pad, bits_per_pixel, bytes_per_line,
        visual_class, red_mask, green_mask, blue_mask, bits_per_rgb,
        colormap_entries, ncolors, window_width, window_height,
        window_x, window_y, window_bdrwidth,
    ) = struct.unpack(f">{HEADER_FIELDS}I", blob[:HEADER_FIELDS * 4])

    if file_version != 7:
        raise ValueError(f"unexpected xwd version {file_version}")
    if bits_per_pixel not in (16, 24, 32):
        raise ValueError(f"unsupported bits_per_pixel {bits_per_pixel}")

    offset = header_size
    # Skip the colour map if present (ncolors entries of 12 bytes each).
    if ncolors:
        offset += ncolors * 12

    pixels = blob[offset:]
    rows = []
    big_endian = byte_order == 1
    fmt = ">" if big_endian else "<"

    for y in range(pixmap_height):
        base = y * bytes_per_line
        row = bytearray()
        line = pixels[base:base + bytes_per_line]
        if bits_per_pixel == 32:
            for x in range(pixmap_width):
                off = x * 4
                if off + 4 > len(line):
                    break
                b, g, r, _a = line[off:off + 4]
                if not big_endian:
                    b, g, r = r, g, b
                row += bytes((r, g, b))
        elif bits_per_pixel == 24:
            for x in range(pixmap_width):
                off = x * 4
                if off + 3 > len(line):
                    break
                b, g, r = line[off:off + 3]
                row += bytes((r, g, b))
        else:  # 16-bit, 5-6-5
            for x in range(pixmap_width):
                off = x * 2
                if off + 2 > len(line):
                    break
                (v,) = struct.unpack(fmt + "H", line[off:off + 2])
                r = ((v >> 11) & 0x1F) << 3
                g = ((v >> 5) & 0x3F) << 2
                b = (v & 0x1F) << 3
                row += bytes((r, g, b))
        rows.append(bytes(row))
    return pixmap_width, pixmap_height, rows


def write_png(path, width, height, rows):
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(
            ">I", zlib.crc32(body)
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)


def main():
    if len(sys.argv) < 3:
        print("usage: xwd_to_png.py <in.xwd> <out.png>")
        return 2
    with open(sys.argv[1], "rb") as fh:
        blob = fh.read()
    w, h, rows = parse_xwd(blob)
    write_png(sys.argv[2], w, h, rows)
    print(f"{sys.argv[1]} -> {sys.argv[2]} ({w}x{h})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
