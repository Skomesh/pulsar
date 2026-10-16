"""Generate Pulsar's application icon.

The repo had none: pulsar.desktop pointed at the generic `audio-card` theme
icon, which every packaging format needs a real one for. SVG is authored here
as the source of truth and rasterised to the PNG sizes the desktop icon
theme spec wants (16/24/32/48/64/128/256/512).

Deliberately geometric and flat. It has to survive being shrunk to 16px in a
panel and being blown up to 512px on a store page, and it has to read as
"audio routing" rather than "audio playback" -- hence the signal path with a
branch splitting off to a second device, which is what the app is for.
"""

import struct
import subprocess
import sys
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "assets" / "icons"
SIZES = [16, 24, 32, 48, 64, 128, 256, 512]

# Rounded-square app tile with a signal splitting toward a second device.
SVG = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#3b82f6"/>
      <stop offset="1" stop-color="#1d4ed8"/>
    </linearGradient>
  </defs>

  <rect x="16" y="16" width="480" height="480" rx="104" fill="url(#bg)"/>

  <!-- source device: the app whose audio is being routed -->
  <rect x="104" y="150" width="76" height="212" rx="18" fill="#ffffff" opacity="0.95"/>

  <!-- signal leaving it -->
  <path d="M196 256 H286" stroke="#ffffff" stroke-width="30"
        stroke-linecap="round" opacity="0.95"/>

  <!-- the branch: this is the routing, not just playback -->
  <path d="M286 256 C 286 256, 300 150, 336 150" stroke="#ffffff"
        stroke-width="26" stroke-linecap="round" fill="none" opacity="0.75"/>
  <path d="M286 256 C 286 256, 300 362, 336 362" stroke="#ffffff"
        stroke-width="26" stroke-linecap="round" fill="none" opacity="0.75"/>

  <!-- destination devices -->
  <rect x="336" y="112" width="72" height="76" rx="16" fill="#ffffff" opacity="0.75"/>
  <rect x="336" y="324" width="72" height="76" rx="16" fill="#ffffff" opacity="0.75"/>
</svg>
"""


def write_png(path, width, height, rgba_rows):
    """Write a minimal 8-bit RGBA PNG. Avoids a Pillow dependency."""
    raw = b"".join(b"\x00" + row for row in rgba_rows)

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def rasterise_with_rsvg(svg_path):
    """Use librsvg via rsvg-convert if present — best quality."""
    if not subprocess.run(["which", "rsvg-convert"],
                          capture_output=True).returncode == 0:
        return False
    for size in SIZES:
        subprocess.run(
            ["rsvg-convert", "-w", str(size), "-h", str(size),
             "-o", str(OUT / f"pulsar-{size}.png"), str(svg_path)],
            check=True,
        )
    return True


def rasterise_with_python(svg_path, size):
    """Fallback: draw the icon directly at this size, no external tool.

    Draws the same composition as the SVG with plain geometry so the build
    has no hard dependency on librsvg.

    Stroke weights are optically corrected. The SVG's 30/512 signal stroke is
    0.9 device pixels at 16px, which antialiases into a pale squiggle — the
    silhouette survives but the icon stops reading. Clamping every stroke to
    a floor of ~2 device pixels below 32px keeps the mark legible in a panel
    while leaving the large sizes exactly as the SVG draws them.
    """

    s = size / 512.0
    px = [[(0, 0, 0, 0)] * size for _ in range(size)]

    def stroke(frac):
        """Return a stroke width in device pixels, with a legibility floor.

        ``frac`` is in the SVG's 512-unit coordinate space, so the natural
        width is frac * size / 512 -- not frac * size.
        """
        natural = frac * s
        floor = 2.1 if size <= 32 else 1.6
        return max(natural, floor)

    def rect(x, y, w, h, r, color, alpha=255):
        x0, y0 = int(round(x * s)), int(round(y * s))
        x1, y1 = int(round((x + w) * s)), int(round((y + h) * s))
        rr = max(0, int(round(r * s)))
        for py in range(max(0, y0), min(size, y1)):
            for pxx in range(max(0, x0), min(size, x1)):
                # rounded corners
                dx = 0
                dy = 0
                if pxx < x0 + rr and py < y0 + rr:
                    dx, dy = (x0 + rr) - pxx, (y0 + rr) - py
                elif pxx >= x1 - rr and py < y0 + rr:
                    dx, dy = pxx - (x1 - rr - 1), (y0 + rr) - py
                elif pxx < x0 + rr and py >= y1 - rr:
                    dx, dy = (x0 + rr) - pxx, py - (y1 - rr - 1)
                elif pxx >= x1 - rr and py >= y1 - rr:
                    dx, dy = pxx - (x1 - rr - 1), py - (y1 - rr - 1)
                if dx * dx + dy * dy > rr * rr:
                    continue
                old = px[py][pxx]
                a = alpha / 255.0
                px[py][pxx] = (
                    int(color[0] * a + old[0] * (1 - a)),
                    int(color[1] * a + old[1] * (1 - a)),
                    int(color[2] * a + old[2] * (1 - a)),
                    max(old[3], alpha),
                )

    def line(x1, y1, x2, y2, frac, color, alpha=255):
        """Thick line in device pixels, rasterised by sampling."""
        w = stroke(frac)
        steps = int(max(abs(x2 - x1), abs(y2 - y1)) * s) * 2 + 1
        half = w / 2.0
        for i in range(steps + 1):
            t = i / steps
            cx = (x1 + (x2 - x1) * t) * s
            cy = (y1 + (y2 - y1) * t) * s
            r = int(half) + 1
            for py in range(max(0, int(cy) - r), min(size, int(cy) + r + 1)):
                for pxx in range(max(0, int(cx) - r), min(size, int(cx) + r + 1)):
                    if (pxx - cx) ** 2 + (py - cy) ** 2 <= half * half:
                        old = px[py][pxx]
                        a = alpha / 255.0
                        px[py][pxx] = (
                            int(color[0] * a + old[0] * (1 - a)),
                            int(color[1] * a + old[1] * (1 - a)),
                            int(color[2] * a + old[2] * (1 - a)),
                            max(old[3], alpha),
                        )

    # Background tile
    rect(16, 16, 480, 480, 104, (0x2C, 0x6F, 0xE0))

    if size <= 24:
        # Optical simplification. The full composition has two branches
        # 106 units apart, which is 3.3 device pixels at 16px — they merge
        # into mush no matter how thick the strokes get, because the shape
        # itself is too fine. Below 32px the mark reduces to what still
        # reads at a glance: one device, a bold arrow, one destination.
        rect(120, 176, 88, 160, 20, (0xFF, 0xFF, 0xFF), 255)
        # shaft
        line(216, 256, 300, 256, 34, (0xFF, 0xFF, 0xFF), 255)
        # arrowhead
        for dy in (-46, 46):
            line(300, 256 + dy, 300, 256, 30, (0xFF, 0xFF, 0xFF), 255)
        rect(304, 176, 88, 160, 20, (0xFF, 0xFF, 0xFF), 255)
    else:
        # Source device
        rect(104, 150, 76, 212, 18, (0xFF, 0xFF, 0xFF), 245)
        # Main signal
        line(196, 256, 286, 256, 30, (0xFF, 0xFF, 0xFF), 245)
        # The branch — the routing, which is the point of the app
        for dy in (-106, 106):
            line(286, 256, 336, 256 + dy, 26, (0xFF, 0xFF, 0xFF), 205)
        # Destination devices
        rect(336, 112, 72, 76, 16, (0xFF, 0xFF, 0xFF), 205)
        rect(336, 324, 72, 76, 16, (0xFF, 0xFF, 0xFF), 205)

    rows = [b"".join(struct.pack("BBBB", *px[y][x]) for x in range(size))
            for y in range(size)]
    return rows


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    svg_path = OUT / "pulsar.svg"
    svg_path.write_text(SVG)
    print(f"wrote {svg_path}")

    if rasterise_with_rsvg(svg_path):
        print("rasterised with rsvg-convert")
    else:
        print("rsvg-convert not found; using the built-in rasteriser")
        for size in SIZES:
            rows = rasterise_with_python(svg_path, size)
            write_png(OUT / f"pulsar-{size}.png", size, size, rows)
            print(f"  wrote pulsar-{size}.png")

    made = sorted(p.name for p in OUT.iterdir())
    print(f"\n{len(made)} files in {OUT}:")
    for name in made:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
