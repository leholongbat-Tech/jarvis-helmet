#!/usr/bin/env python3
"""Render text with a generated LVGL C font into a PNG, without hardware.

Uses the same data LVGL uses (cmap -> glyph descriptor -> 4 bpp bitmap, advance in 1/16 px;
kerning is ignored). Characters missing from the font are drawn as red boxes here so they are
easy to spot (on the device they would silently disappear).

Usage:
  python tools/fonts/preview_font.py firmware/main/boards/jarvis-helmet/font_jarvis_vi_16_4.c \
      -o preview.png [--text "Xin chào"] [--width 240]
Without --text it renders the FW6 acceptance sentence and the Vietnamese alphabet.
Python >= 3.10, standard library only.
"""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_glyphs import ACCEPTANCE_SENTENCE, LvFont, vietnamese_alphabet  # noqa: E402

BG = (0x00, 0x07, 0x0D)   # HUD background
FG = (0x00, 0xE5, 0xFF)   # HUD cyan
MISSING = (0xFF, 0x3B, 0x30)


def wrap(font: LvFont, text: str, max_w: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        line, width = "", 0
        for word in para.split(" "):
            piece = (" " if line else "") + word
            w = sum(_advance(font, ch) for ch in piece)
            if line and width + w > max_w:
                lines.append(line)
                line, width = word, sum(_advance(font, ch) for ch in word)
            else:
                line, width = line + piece, width + w
        lines.append(line)
    return lines


def _advance(font: LvFont, ch: str) -> int:
    g = font.glyph(ch)
    return (g.adv_w + 8) // 16 if g else font.line_height // 2


def render(font: LvFont, text: str, width: int, margin: int = 4) -> tuple[int, int, bytearray]:
    lines = wrap(font, text, width - 2 * margin)
    height = 2 * margin + font.line_height * len(lines)
    px = bytearray(bytes(BG) * (width * height))
    bits = font.bpp
    vmax = (1 << bits) - 1
    for row, line in enumerate(lines):
        x = margin
        top = margin + row * font.line_height
        for ch in line:
            g = font.glyph(ch)
            if g is None:
                _box(px, width, height, x, top + 2, font.line_height // 2, font.line_height - 4)
                x += font.line_height // 2
                continue
            gx = x + g.ofs_x
            gy = top + font.ascent() - g.box_h - g.ofs_y
            base = g.bitmap_index * 8
            for i in range(g.box_w * g.box_h):
                bit = base + i * bits
                byte = font.bitmap[bit // 8]
                v = (byte >> (8 - bits - bit % 8)) & vmax
                if v == 0:
                    continue
                X, Y = gx + i % g.box_w, gy + i // g.box_w
                if 0 <= X < width and 0 <= Y < height:
                    a = v / vmax
                    o = 3 * (Y * width + X)
                    for c in range(3):
                        px[o + c] = round(px[o + c] * (1 - a) + FG[c] * a)
            x += (g.adv_w + 8) // 16
    return width, height, px


def _box(px: bytearray, width: int, height: int, x: int, y: int, w: int, h: int) -> None:
    for yy in range(y, min(y + h, height)):
        for xx in range(x, min(x + w, width)):
            if yy in (y, y + h - 1) or xx in (x, x + w - 1):
                o = 3 * (yy * width + xx)
                px[o:o + 3] = bytes(MISSING)


def upscale(width: int, height: int, px: bytearray, k: int) -> tuple[int, int, bytearray]:
    if k <= 1:
        return width, height, px
    out = bytearray()
    for y in range(height):
        row = b"".join(bytes(px[3 * (y * width + x):3 * (y * width + x) + 3]) * k
                       for x in range(width))
        out += row * k
    return width * k, height * k, out


def write_png(path: Path, width: int, height: int, px: bytearray) -> None:
    raw = b"".join(b"\x00" + bytes(px[3 * width * y:3 * width * (y + 1)]) for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(
            ">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main() -> int:
    # Vietnamese output must not crash on legacy Windows consoles (cp1252).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Render text with an LVGL C font into a PNG")
    ap.add_argument("font", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--text", help="text to render (default: acceptance sentence + alphabet)")
    ap.add_argument("--width", type=int, default=240)
    ap.add_argument("--scale", type=int, default=1, help="integer zoom of the PNG")
    args = ap.parse_args()
    font = LvFont(args.font)
    letters = vietnamese_alphabet()
    text = args.text if args.text is not None else "\n".join(
        [ACCEPTANCE_SENTENCE] + [" ".join(letters[i:i + 12]) for i in range(0, 144, 12)]
        + [" ".join(letters[144:]), "0123456789 – — ‘’ “” … ₫ « » • °"])
    w, h, px = upscale(*render(font, text, args.width), args.scale)
    write_png(args.output, w, h, px)
    print(f"wrote {args.output} ({w}x{h})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
