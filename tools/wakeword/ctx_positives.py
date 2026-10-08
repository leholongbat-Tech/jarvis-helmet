#!/usr/bin/env python3
"""Positive clips with speech right after the wake word ("Hàu ơi, mở mặt nạ" in one breath).

The trainer keeps the end of every positive clip and places it 0.2-0.3 s before the end of its
window, with only background noise after it, so a model learns to fire just after the wake word
when silence follows. Said in one breath, the next word has already started by then: the first
"hàu ơi" model woke on 98% of the clips with the wake word alone but on ~63% of the clips
followed by a command. Each clip made here is a wake word clip, a short pause and the start of
a command clip, cut shortly after the wake word, so the window ends inside the command onset.

    python ctx_positives.py --wake out/hau_oi/positives --next out/hau_oi/commands \\
        --out out/hau_oi/positives_ctx --count 3000

--wake: clips that end with the wake word (gen_positives.py output); --next: any speech
without the wake word (gen_negatives.py with a list of commands). 16 kHz mono WAV +
manifest.jsonl.
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import numpy as np

import wwcommon as ww


def quiet(rng: random.Random, seconds: float) -> np.ndarray:
    """-70 dBFS noise for the pause between the wake word and the command."""
    n = int(seconds * ww.TARGET_RATE)
    return (np.random.default_rng(rng.randrange(1 << 30)).standard_normal(n)
            * 10 ** (-70 / 20)).astype(np.float32)


def peak(audio: np.ndarray) -> float:
    return float(np.max(np.abs(audio))) if len(audio) else 0.0


def join(wake: np.ndarray, following: np.ndarray, gap_s: float, after_s: float,
         rng: random.Random) -> np.ndarray:
    """wake word + pause + start of the next utterance, cut `after_s` after the wake word."""
    wake = ww.trim_silence(wake, pad_ms=20)
    following = ww.trim_silence(following, pad_ms=20)
    # The command at the wake word's level (both are peak-normalized TTS clips).
    following = following * (peak(wake) / max(peak(following), 1e-9))
    joined = np.concatenate([wake, quiet(rng, gap_s), following.astype(np.float32)])
    return joined[: len(wake) + int(after_s * ww.TARGET_RATE)]


def main(argv: list[str] | None = None) -> int:
    ww.utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wake", type=Path, required=True, help="clips ending with the wake word")
    parser.add_argument("--next", dest="following", type=Path, required=True,
                        help="speech clips to append (commands, chatter)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--count", type=int, default=3000)
    parser.add_argument("--gap", type=float, nargs=2, default=(0.03, 0.20), metavar=("MIN", "MAX"),
                        help="pause after the wake word, seconds")
    parser.add_argument("--after", type=float, nargs=2, default=(0.10, 0.40), metavar=("MIN", "MAX"),
                        help="audio kept after the end of the wake word, seconds")
    parser.add_argument("--peak", type=float, nargs=2, default=(-12.0, -1.0), metavar=("MIN", "MAX"),
                        help="final peak level, dBFS")
    parser.add_argument("--seed", type=int, default=5)
    args = parser.parse_args(argv)

    wakes = sorted(args.wake.glob("*.wav"))
    followers = sorted(args.following.glob("*.wav"))
    if not wakes or not followers:
        print("error: --wake and --next need WAV files", file=sys.stderr)
        return 1
    if args.gap[0] > args.gap[1] or args.after[0] > args.after[1] or min(*args.gap, *args.after) < 0:
        print("error: --gap/--after need 0 <= MIN <= MAX", file=sys.stderr)
        return 1
    rng = random.Random(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = args.out / "manifest.jsonl"
    for i in range(args.count):
        wake_path = wakes[i % len(wakes)]
        next_path = rng.choice(followers)
        gap_s = rng.uniform(*args.gap)
        after_s = max(gap_s + 0.02, rng.uniform(*args.after))  # always some of the next word
        audio = join(ww.read_audio(wake_path), ww.read_audio(next_path), gap_s, after_s, rng)
        audio = ww.normalize_peak(audio, rng.uniform(*args.peak))
        name = f"ctx_{i:05d}.wav"
        ww.write_wav(args.out / name, audio)
        ww.append_jsonl(manifest, {"file": name, "kind": "positive", "wake": wake_path.name,
                                   "next": next_path.name, "gap_s": round(gap_s, 3),
                                   "after_s": round(after_s, 3)})
    print(f"wrote {args.count} clips to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
