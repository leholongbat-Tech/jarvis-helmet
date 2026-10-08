#!/usr/bin/env python3
"""Synthesize hard-negative clips (confusable phrases) for a wake word model ("quản gia" by
default; negatives_hau_oi.txt with --wake-word "hàu ơi").

Reads negatives_vi.txt (quản lý, gia đình, quán gà, quản trị, hoàng gia, ...), speaks every
phrase with several voices and variations through the same VieNeu/Piper pipeline as
gen_positives.py. Output goes to <trainer data>/negative_samples (see train.sh), where the
trainer weights them as hard negatives.

Examples:
    python gen_negatives.py --api-url http://localhost:8880/v1/audio/speech --per-phrase 6
    python gen_negatives.py --dry-run --per-phrase 1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import wwsynth
import wwcommon as ww

DEFAULT_PHRASES = Path(__file__).resolve().with_name("negatives_vi.txt")


def main(argv: list[str] | None = None) -> int:
    ww.utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    wwsynth.add_common_arguments(parser, default_out="out/negatives", default_count=0)
    parser.add_argument("--phrases", type=Path, default=DEFAULT_PHRASES,
                        help="phrase list (default: negatives_vi.txt)")
    parser.add_argument("--wake-word", default=ww.WAKE_WORD,
                        help=f"phrase the negatives must not contain (default: {ww.WAKE_WORD!r})")
    parser.add_argument("--per-phrase", type=int, default=6,
                        help="clips per phrase when --count is not given")
    parser.set_defaults(variants=2)
    args = parser.parse_args(argv)

    phrases = ww.load_phrases(args.phrases)
    wake_word = ww.nfc(args.wake_word).strip().lower()
    bad = [p for p in phrases if wake_word in p.lower()]
    if bad:
        print(f"error: negatives must not contain '{wake_word}': {bad}", file=sys.stderr)
        return 1
    if args.count <= 0:
        args.count = len(phrases) * max(1, args.per_phrase)
    # Phrases in turn (each gets the same share), random voice per call.
    written, failures = wwsynth.run(args, phrases, prefix="neg", kind="negative", cycle_texts=True)
    return wwsynth.exit_code(written, failures)


if __name__ == "__main__":
    sys.exit(main())
