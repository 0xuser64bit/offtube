#!/usr/bin/env python3
"""Generate offtube PNG icons (rounded ink square + download arrow). Stdlib only."""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

INK = (0x1B, 0x1A, 0x17, 255)
PAPER = (0xFA, 0xF8, 0xF3, 255)
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "extension" / "icons"

# Favicon viewBox 32x32 strokes (cx, cy pairs), thickness 2.4.
STROKES = [
    ((16, 7), (16, 19.5)),
    ((11, 15.2), (16, 20.2)),
    ((16, 20.2), (21, 15.2)),
    ((9, 24), (23, 24)),
]
THICK = 2.4
RADIUS = 7.0  # rx in the 32x32 viewBox


def write_png(path: Path, size: int, pixels: list[tuple[int, int, int, int]]) -> None:
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            raw.extend(pixels[y * size + x])

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def dist_seg(px: float, py: float, x0: float, y0: float, x1: float, y1: float) -> float:
    dx, dy = x1 - x0, y1 - y0
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return math.hypot(px - x0, py - y0)
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / l2))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def rounded_rect_sdf(x: float, y: float, size: float, radius: float) -> float:
    # Distance to rounded-rect outline interior (negative inside).
    hw = size / 2 - radius
    qx, qy = abs(x - size / 2) - hw, abs(y - size / 2) - hw
    outside = math.hypot(max(qx, 0), max(qy, 0))
    inside = min(max(qx, qy), 0)
    return outside + inside - radius


def render(size: int) -> list[tuple[int, int, int, int]]:
    scale = size / 32.0
    radius = RADIUS * scale
    pixels: list[tuple[int, int, int, int]] = []
    for y in range(size):
        for x in range(size):
            # Sample at pixel center.
            px, py = x + 0.5, y + 0.5
            if rounded_rect_sdf(px, py, size, radius) > 0:
                pixels.append((0, 0, 0, 0))
                continue
            ink = True
            # Arrow in 32-space.
            ax, ay = px / scale, py / scale
            for (x0, y0), (x1, y1) in STROKES:
                if dist_seg(ax, ay, x0, y0, x1, y1) <= THICK / 2 + 0.15:
                    ink = False
                    break
            pixels.append(INK if ink else PAPER)
    return pixels


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 48, 128):
        dest = OUT / f"icon-{size}.png"
        write_png(dest, size, render(size))
        print(f"wrote {dest.relative_to(ROOT)} ({size}x{size})")


if __name__ == "__main__":
    main()
