#!/usr/bin/env python3
"""Synthesize positive wake word clips ("quản gia" by default) for microWakeWord training.

Uses the VieNeu-TTS OpenAI-style API (our tts-router, or a VieNeu server directly) with
every available voice, varies speed by resampling (VieNeu ignores `speed`) plus WSOLA tempo
and pitch shifts, and optionally adds Piper vi_VN voices. Output: 16 kHz mono PCM16 WAV +
manifest.jsonl, ready for <trainer data>/personal_samples (see train.sh).

Examples:
    python gen_positives.py --api-url http://localhost:8880/v1/audio/speech --count 3000
    python gen_positives.py --api-url http://vieneu:8000/v1/audio/speech --sampling-params \
        --piper-voice voices/vi_VN-vivos-x_low.onnx --count 5000
    python gen_positives.py --wake-word "hàu ơi" --api-url http://vieneu:8000/v1/audio/speech \
        --sampling-params --count 3000
    python gen_positives.py --dry-run --count 30        # offline pipeline test
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import wwsynth
import wwcommon as ww


def default_texts(wake_word: str) -> list[str]:
    """Punctuation changes VieNeu's prosody; a few carrier words in front give natural onsets.
    Nothing after the wake word: training keeps the end of each clip."""
    w = ww.nfc(wake_word).strip().lower()
    cap = w[:1].upper() + w[1:]
    return [w, f"{cap}.", f"{cap}!", f"{cap}?", f"{w},", f"{cap}...",
            f"{cap}.", f"{cap}!", f"này, {w}", f"Này {w}!", f"ê {w}", f"Ơ, {w}?"]


DEFAULT_TEXTS = default_texts(ww.WAKE_WORD)


def main(argv: list[str] | None = None) -> int:
    ww.utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    wwsynth.add_common_arguments(parser, default_out="out/positives", default_count=3000)
    parser.add_argument("--wake-word", default=ww.WAKE_WORD,
                        help=f"phrase to synthesize (default: {ww.WAKE_WORD!r})")
    parser.add_argument("--texts-file", type=Path,
                        help="one text per line instead of the built-in variants of --wake-word")
    args = parser.parse_args(argv)

    wake_word = ww.nfc(args.wake_word).strip().lower()
    texts = ww.load_phrases(args.texts_file) if args.texts_file else default_texts(wake_word)
    for text in texts:
        if wake_word not in ww.nfc(text).lower():
            print(f"warning: positive text without '{wake_word}': {text!r}", file=sys.stderr)
    written, failures = wwsynth.run(args, texts, prefix="pos", kind="positive")
    return wwsynth.exit_code(written, failures)


if __name__ == "__main__":
    sys.exit(main())
