#!/usr/bin/env python3
"""Glyph coverage check for the generated LVGL fonts (FW6 acceptance #1).

Parses lv_font_conv `--format lvgl --no-compress` C files the same way LVGL resolves a code
point (cmap subtables -> glyph id -> glyph descriptor) and asserts that every required
character maps to a real glyph. This matters because the firmware builds with
CONFIG_LV_USE_FONT_PLACEHOLDER=n: a missing glyph is silently skipped, not drawn as a box.

Profiles (picked from the file name, override with --profile):
  vi   font_jarvis_vi_*  : the FW6 acceptance sentence, the full Vietnamese alphabet with every
                           tone in upper and lower case, digits and common punctuation
                           (- – — ‘ ’ “ ” … ₫ ...) plus the combining tone marks.
  hud  font_jarvis_hud_* : printable ASCII and the degree sign (clock, voltage, labels).

Usage: python tools/fonts/check_glyphs.py <font.c> [<font.c> ...]   (exit 1 on any miss)
Python >= 3.10, standard library only.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

ACCEPTANCE_SENTENCE = "Ừ, đã bật chế độ chiến đấu – tôi sẵn sàng, thưa ngài!"

_VOWELS = "aăâeêioôơuưy"
_TONES = ("", "̀", "́", "̉", "̃", "̣")  # ngang huyền sắc hỏi ngã nặng
_CONSONANTS = "bcdđghklmnpqrstvx" + "fjwz"


def vietnamese_alphabet() -> list[str]:
    """Every Vietnamese letter (all vowels x 6 tones, consonants), lower and upper case."""
    letters: list[str] = []
    for vowel in _VOWELS + _VOWELS.upper():
        for tone in _TONES:
            ch = unicodedata.normalize("NFC", vowel + tone)
            assert len(ch) == 1, f"{vowel}+{tone!r} has no precomposed form"
            letters.append(ch)
    letters += list(_CONSONANTS + _CONSONANTS.upper())
    return letters


DIGITS = "0123456789"
PUNCTUATION = ".,;:!?'\"()[]{}/\\@#&*+=<>%^_|~`$-" + "–—‘’“”…₫«»·•°"
COMBINING_MARKS = "̣̀́̂̃̉"

PROFILES: dict[str, str] = {
    "vi": "".join(dict.fromkeys(ACCEPTANCE_SENTENCE + "".join(vietnamese_alphabet()) + DIGITS
                                + PUNCTUATION + COMBINING_MARKS + " ")),
    "hud": "".join(chr(c) for c in range(0x20, 0x7F)) + "°",
}

# Characters that legitimately have an empty bitmap.
BLANK_OK = {" ", " "}


@dataclass
class Glyph:
    bitmap_index: int
    adv_w: int  # 1/16 px
    box_w: int
    box_h: int
    ofs_x: int
    ofs_y: int


class LvFont:
    """Minimal reader for lv_font_conv 1.5.x LVGL output."""

    _GLYPH = re.compile(r"\{\.bitmap_index = (\d+), \.adv_w = (\d+), \.box_w = (\d+), "
                        r"\.box_h = (\d+), \.ofs_x = (-?\d+), \.ofs_y = (-?\d+)\}")
    _CMAP = re.compile(r"\.range_start = (\d+), \.range_length = (\d+), \.glyph_id_start = (\d+),"
                       r"\s*\.unicode_list = (\w+), \.glyph_id_ofs_list = (\w+), "
                       r"\.list_length = (\d+), \.type = (\w+)")
    _ARRAY = re.compile(r"static const uint(?:8|16)_t (\w+)\[\] = \{(.*?)\};", re.S)
    _BITMAP = re.compile(r"glyph_bitmap\[\] = \{(.*?)\};", re.S)

    def __init__(self, path: Path):
        self.path = path
        text = path.read_text(encoding="utf-8")
        self.name = path.stem
        self.line_height = int(re.search(r"\.line_height = (\d+)", text).group(1))
        self.base_line = int(re.search(r"\.base_line = (\d+)", text).group(1))
        bpp = re.search(r"\.bpp = (\d+)", text)
        self.bpp = int(bpp.group(1)) if bpp else 4
        if re.search(r"\.bitmap_format = (\d+)", text).group(1) != "0":
            raise ValueError(f"{path}: compressed bitmaps (regenerate with --no-compress)")
        self.glyphs = [Glyph(*map(int, m.groups())) for m in self._GLYPH.finditer(text)]
        arrays = {m.group(1): [int(v, 0) for v in re.findall(r"0x[0-9a-fA-F]+|\d+",
                                                             _strip_comments(m.group(2)))]
                  for m in self._ARRAY.finditer(text)}
        self.bitmap = bytes(int(v, 16) for v in re.findall(
            r"0x[0-9a-fA-F]+", _strip_comments(self._BITMAP.search(text).group(1))))
        self.code_to_gid: dict[int, int] = {}
        for m in self._CMAP.finditer(text):
            start, length, gid_start = int(m.group(1)), int(m.group(2)), int(m.group(3))
            ulist, olist, kind = m.group(4), m.group(5), m.group(7)
            if kind == "LV_FONT_FMT_TXT_CMAP_FORMAT0_TINY":
                for i in range(length):
                    self.code_to_gid[start + i] = gid_start + i
            elif kind == "LV_FONT_FMT_TXT_CMAP_FORMAT0_FULL":
                for i, ofs in enumerate(arrays[olist]):
                    # Like LVGL: offset 0 anywhere but the first slot marks a missing glyph.
                    if ofs != 0 or i == 0:
                        self.code_to_gid[start + i] = gid_start + ofs
            elif kind == "LV_FONT_FMT_TXT_CMAP_SPARSE_TINY":
                for i, delta in enumerate(arrays[ulist]):
                    self.code_to_gid[start + delta] = gid_start + i
            elif kind == "LV_FONT_FMT_TXT_CMAP_SPARSE_FULL":
                for delta, ofs in zip(arrays[ulist], arrays[olist]):
                    self.code_to_gid[start + delta] = gid_start + ofs
            else:
                raise ValueError(f"{path}: unknown cmap type {kind}")
        if not self.code_to_gid:
            raise ValueError(f"{path}: no character map found")

    def glyph(self, ch: str) -> Glyph | None:
        gid = self.code_to_gid.get(ord(ch))
        if gid is None or gid >= len(self.glyphs):
            return None
        return self.glyphs[gid]

    def has(self, ch: str) -> bool:
        g = self.glyph(ch)
        if g is None:
            return False
        return ch in BLANK_OK or (g.box_w > 0 and g.box_h > 0)

    def ascent(self) -> int:
        return self.line_height - self.base_line

    def max_overshoot(self) -> tuple[int, int]:
        """Pixels drawn above the ascent / below the descent by the tallest glyphs."""
        top = max((g.ofs_y + g.box_h for g in self.glyphs[1:]), default=0)
        bottom = min((g.ofs_y for g in self.glyphs[1:]), default=0)
        return max(0, top - self.ascent()), max(0, -bottom - self.base_line)

    def flash_estimate(self) -> int:
        """Rough flash bytes (glyph_dsc is 16 B/glyph with LV_FONT_FMT_TXT_LARGE=y)."""
        return len(self.bitmap) + 16 * len(self.glyphs) + 2 * len(self.code_to_gid)


def _strip_comments(s: str) -> str:
    return re.sub(r"/\*.*?\*/", "", s, flags=re.S)


def profile_for(path: Path) -> str:
    return "hud" if "_hud_" in path.name else "vi"


def check(path: Path, profile: str) -> bool:
    font = LvFont(path)
    required = PROFILES[profile]
    missing = [ch for ch in required if not font.has(ch)]
    over_top, over_bottom = font.max_overshoot()
    print(f"{path.name}: profile={profile} glyphs={len(font.glyphs) - 1} "
          f"line_height={font.line_height} base_line={font.base_line} "
          f"overshoot(top/bottom)={over_top}/{over_bottom}px "
          f"bitmap={len(font.bitmap)}B est_flash~{font.flash_estimate() // 1024}KB")
    if missing:
        listing = " ".join(f"{ch!r}(U+{ord(ch):04X})" for ch in missing)
        print(f"  MISSING {len(missing)} of {len(required)}: {listing}")
        return False
    print(f"  OK: all {len(required)} required characters present")
    return True


def main() -> int:
    # Vietnamese output must not crash on legacy Windows consoles (cp1252).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Check glyph coverage of LVGL C fonts")
    ap.add_argument("fonts", nargs="+", type=Path)
    ap.add_argument("--profile", choices=sorted(PROFILES), help="default: from the file name")
    args = ap.parse_args()
    ok = True
    for path in args.fonts:
        if not path.exists():
            print(f"{path}: not found")
            ok = False
            continue
        ok &= check(path, args.profile or profile_for(path))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
