#!/usr/bin/env python3
"""Generate offtube's extension PNG icons. Stdlib only.

The mark matches web/favicon.svg: a black film-rebate square, a paper print
window, and a crimson grease-pencil tick running off the print. Drawn in a
32-unit grid and supersampled 4x4 per pixel for antialiasing.
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

REBATE = (0x0F, 0x0F, 0x0E)
PAPER = (0xFB, 0xFB, 0xF9)
PENCIL = (0xD0, 0x10, 0x3A)
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "extension" / "icons"
SS = 4  # samples per axis per pixel

WINDOW = (4.0, 7.0, 28.0, 25.0)  # x0, y0, x1, y1 in the 32 grid
# The tick from favicon.svg: two cubic Béziers in absolute coordinates.
TICK = [
    ((8.5, 16.5), (10.1, 17.7), (11.5, 19.3), (12.8, 21.1)),
    ((12.8, 21.1), (15.8, 16.5), (19.2, 12.5), (23.8, 8.9)),
]


def bezier_points(curve, steps: int = 24) -> list[tuple[float, float]]:
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = curve
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        pts.append((
            u ** 3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t ** 3 * x3,
            u ** 3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t ** 3 * y3,
        ))
    return pts


SEGMENTS = []
for _curve in TICK:
    _pts = bezier_points(_curve)
    SEGMENTS += list(zip(_pts, _pts[1:]))


def dist_seg(px: float, py: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    (x0, y0), (x1, y1) = a, b
    dx, dy = x1 - x0, y1 - y0
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / l2))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def sample(x: float, y: float, half_stroke: float) -> tuple[int, int, int]:
    if min(dist_seg(x, y, a, b) for a, b in SEGMENTS) <= half_stroke:
        return PENCIL
    x0, y0, x1, y1 = WINDOW
    return PAPER if x0 <= x <= x1 and y0 <= y <= y1 else REBATE


def render(size: int) -> list[tuple[int, int, int, int]]:
    scale = 32.0 / size
    # Thicker pencil at toolbar sizes so the tick survives 16px.
    half = (3.8 if size <= 16 else 3.2 if size <= 32 else 3.0) / 2
    pixels = []
    for py in range(size):
        for px in range(size):
            acc = [0, 0, 0]
            for sy in range(SS):
                for sx in range(SS):
                    c = sample((px + (sx + .5) / SS) * scale, (py + (sy + .5) / SS) * scale, half)
                    acc = [a + v for a, v in zip(acc, c)]
            n = SS * SS
            pixels.append((acc[0] // n, acc[1] // n, acc[2] // n, 255))
    return pixels


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


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 48, 128):
        dest = OUT / f"icon-{size}.png"
        write_png(dest, size, render(size))
        print(f"wrote {dest.relative_to(ROOT)} ({size}x{size})")


if __name__ == "__main__":
    main()
