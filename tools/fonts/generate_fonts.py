#!/usr/bin/env python3
"""Generate the LVGL fonts of the Jarvis Helmet HUD (FW6, contract D10).

Downloads pinned OFL fonts (Noto Sans, Rajdhani), checks their SHA-256, converts them with
`npx -y lv_font_conv@1.5.3` into LVGL C fonts and writes them into the board directory:

  font_jarvis_vi_16_4.c   Noto Sans Medium 16 px, 4 bpp - body text (assistant reply, card title)
  font_jarvis_vi_14_4.c   Noto Sans Medium 14 px, 4 bpp - small text (user line, captions, card text)
  font_jarvis_hud_16_4.c  Rajdhani SemiBold 16 px, 4 bpp - digits / ASCII HUD labels (clock, 5.02V)

Finally runs check_glyphs.py on the result. Python >= 3.10, standard library only; needs Node.js
(npx) on PATH and network access on the first run (fonts and lv_font_conv are cached).

Usage:
  python tools/fonts/generate_fonts.py                    # download (if needed), convert, check
  python tools/fonts/generate_fonts.py --cache-dir /tmp/f # custom cache directory
  python tools/fonts/generate_fonts.py --check-only       # only run the glyph coverage check
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
BOARD_DIR = REPO / "firmware" / "main" / "boards" / "jarvis-helmet"
DEFAULT_CACHE = Path(os.environ.get("JARVIS_FONT_CACHE", HERE / "cache"))

LV_FONT_CONV = "lv_font_conv@1.5.3"

# Official sources pinned to a commit so the generated files are reproducible.
_NOTO = ("https://raw.githubusercontent.com/notofonts/notofonts.github.io/"
         "28b15b4b43b7bed62b5cf6e6b0b5ff5846270535/fonts/NotoSans/unhinted/ttf/")
_RAJDHANI = ("https://raw.githubusercontent.com/google/fonts/"
             "9d1ce2fc3c335cca32b6db00c19f55d57b0a68fe/ofl/rajdhani/")
SOURCES: dict[str, tuple[str, str]] = {
    # file name in the cache: (url, sha256)
    "NotoSans-Medium.ttf": (
        _NOTO + "NotoSans-Medium.ttf",
        "220e74546aee1b9a17cd50025dd917a064d0224e653f562287446c2328e4160b"),
    "NotoSans-OFL.txt": (
        "https://raw.githubusercontent.com/notofonts/latin-greek-cyrillic/NotoSans-v2.015/OFL.txt",
        "cee9892f9f0cc8fe882c9e9537ee6a89621d86ee7ceaf70b02e2b2b1c25c061a"),
    "Rajdhani-SemiBold.ttf": (
        _RAJDHANI + "Rajdhani-SemiBold.ttf",
        "94bbd25a18ca665999feb05a537de9fd2b860dcfb78bbe9ca00270825bf235da"),
    "Rajdhani-OFL.txt": (
        _RAJDHANI + "OFL.txt",
        "f62ef357d3a1c3d27edd35a6e1ba350e8a8d13499797964eeadefbf0b3b15d1f"),
}

# Contract D10: ASCII, Latin-1, Vietnamese letters (Ă Đ Ĩ Ũ Ơ Ư), combining tone marks as a
# fallback for non-NFC text, all precomposed Vietnamese letters, general punctuation
# (– — ‘ ’ “ ” • …) and the dong sign ₫. The HUD draws its symbols with LVGL shapes and the
# Material Symbols icon font, so no extra symbol ranges are needed here.
VI_RANGES = ("0x20-0x7E,0xA0-0xFF,0x102-0x103,0x110-0x111,0x128-0x129,0x168-0x169,"
             "0x1A0-0x1A1,0x1AF-0x1B0,0x300-0x303,0x309,0x323,0x1EA0-0x1EF9,"
             "0x2010-0x2027,0x20AB")
# Technical font: printable ASCII and the degree sign; anything else falls back to the
# Vietnamese font at runtime (the HUD chains the fonts).
HUD_RANGES = "0x20-0x7E,0xB0"


@dataclass(frozen=True)
class FontSpec:
    name: str        # C symbol and file stem
    source: str      # key of SOURCES
    size: int
    ranges: str
    license_note: str
    bpp: int = 4


FONTS = [
    FontSpec("font_jarvis_vi_16_4", "NotoSans-Medium.ttf", 16, VI_RANGES,
             "Noto Sans Medium 2.015, Copyright 2022 The Noto Project Authors"),
    FontSpec("font_jarvis_vi_14_4", "NotoSans-Medium.ttf", 14, VI_RANGES,
             "Noto Sans Medium 2.015, Copyright 2022 The Noto Project Authors"),
    FontSpec("font_jarvis_hud_16_4", "Rajdhani-SemiBold.ttf", 16, HUD_RANGES,
             "Rajdhani SemiBold 1.201, Copyright (c) 2014, Indian Type Foundry"),
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_sources(cache: Path, offline: bool) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    for name, (url, digest) in SOURCES.items():
        dest = cache / name
        if dest.exists() and sha256(dest) == digest:
            continue
        if offline:
            sys.exit(f"error: {dest} missing or modified and --offline was given")
        print(f"downloading {url}", flush=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(url, timeout=60) as resp, tmp.open("wb") as out:
            shutil.copyfileobj(resp, out)
        got = sha256(tmp)
        if got != digest:
            tmp.unlink(missing_ok=True)
            sys.exit(f"error: sha256 mismatch for {name}: got {got}, expected {digest}")
        tmp.replace(dest)


def find_npx() -> str:
    npx = shutil.which("npx")
    if npx is None:
        sys.exit("error: npx not found - install Node.js (https://nodejs.org) first")
    return npx


HEADER = """/* Generated by tools/fonts/generate_fonts.py - do not edit by hand.
 * Source: {note}.
 * SIL Open Font License 1.1, see FONTS-LICENSE.md in this directory. */
"""


def convert(spec: FontSpec, cache: Path, out_dir: Path, npx: str) -> Path:
    out_name = f"{spec.name}.c"
    # Run inside the cache with relative paths: lv_font_conv records its command line in the
    # file header, which must not contain machine-specific paths.
    cmd = [npx, "-y", LV_FONT_CONV,
           "--font", spec.source, "--size", str(spec.size), "--bpp", str(spec.bpp),
           "--format", "lvgl", "-r", spec.ranges,
           "--no-compress", "--no-prefilter", "--force-fast-kern-format",
           "--lv-include", "lvgl.h", "--lv-font-name", spec.name, "-o", out_name]
    print(f"converting {spec.name} ({spec.source}, {spec.size} px)", flush=True)
    result = subprocess.run(cmd, cwd=cache, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        sys.exit(f"error: lv_font_conv failed for {spec.name}")
    generated = cache / out_name
    body = generated.read_bytes().replace(b"\r\n", b"\n")
    dest = out_dir / out_name
    dest.write_bytes(HEADER.format(note=spec.license_note).encode() + body)
    generated.unlink()
    return dest


def main() -> int:
    # Vietnamese output must not crash on legacy Windows consoles (cp1252).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE,
                    help="where fonts are downloaded (default: tools/fonts/cache or "
                         "$JARVIS_FONT_CACHE)")
    ap.add_argument("--out-dir", type=Path, default=BOARD_DIR,
                    help="output directory (default: the jarvis-helmet board directory)")
    ap.add_argument("--offline", action="store_true", help="never download, use the cache only")
    ap.add_argument("--check-only", action="store_true", help="only run the glyph check")
    args = ap.parse_args()

    out_dir: Path = args.out_dir.resolve()
    outputs = [out_dir / f"{spec.name}.c" for spec in FONTS]
    if not args.check_only:
        cache: Path = args.cache_dir.resolve()
        fetch_sources(cache, args.offline)
        npx = find_npx()
        out_dir.mkdir(parents=True, exist_ok=True)
        for spec in FONTS:
            convert(spec, cache, out_dir, npx)

    check = [sys.executable, str(HERE / "check_glyphs.py"), *map(str, outputs)]
    return subprocess.run(check).returncode


if __name__ == "__main__":
    sys.exit(main())
