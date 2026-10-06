#!/usr/bin/env python3
"""Guided recording of personal "quản gia" samples from a laptop microphone.

Records 50-100 takes at 16 kHz mono, alternating Northern ("kwản za") and Southern
("wảng da / quảng ya") pronunciation and varying distance, loudness and speed. Each take
is trimmed, level-checked and saved as PCM16 WAV + manifest.jsonl for
<trainer data>/personal_samples (see train.sh).

Needs `pip install sounddevice` (PortAudio). Keys after each take:
    Enter = keep, r = record again, p = play back, s = skip, q = quit

Examples:
    python record_personal.py --list-devices
    python record_personal.py --speaker long --count 80
    python record_personal.py --dry-run --count 4      # no microphone: synthetic takes
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import numpy as np

import wwcommon as ww

ACCENTS = {
    "bac": ("Bắc", "giọng Bắc, đọc gần như \"kwản za\""),
    "nam": ("Nam", "giọng Nam, đọc gần như \"wảng da\" / \"quảng ya\""),
}
STYLES = [
    "bình thường, cách mic khoảng 30 cm",
    "nói nhanh, gọn",
    "nói chậm, rõ từng chữ",
    "nhỏ giọng (như nói trong phòng có người ngủ)",
    "to hơn bình thường",
    "đứng xa mic khoảng 1 m",
    "quay mặt lệch khỏi mic",
    "như đang gọi người ở phòng bên",
    "đội mũ / che miệng bằng tay như đội mũ giáp",
    "kéo dài chữ \"gia\"",
]
MIN_PEAK_DBFS = -35.0
CLIP_PEAK_DBFS = -0.3
MIN_SPEECH_S = 0.25


def make_schedule(count: int, accents: list[str], seed: int) -> list[tuple[str, str]]:
    """Alternate accents, cycle styles in a shuffled order."""
    rng = random.Random(seed)
    styles = STYLES[:]
    rng.shuffle(styles)
    return [(accents[i % len(accents)], styles[i % len(styles)]) for i in range(count)]


def check_take(audio: np.ndarray) -> tuple[np.ndarray, str | None]:
    """Return (trimmed audio, problem or None)."""
    peak = ww.peak_dbfs(audio)
    if peak >= CLIP_PEAK_DBFS:
        return audio, "bị rè (clipping) - lùi xa mic hoặc giảm gain"
    if peak < MIN_PEAK_DBFS:
        return audio, "quá nhỏ - nói to hơn hoặc lại gần mic"
    trimmed = ww.trim_silence(audio, threshold_db=-35.0, pad_ms=200)
    if len(trimmed) / ww.TARGET_RATE < MIN_SPEECH_S:
        return trimmed, "không nghe thấy giọng nói"
    return trimmed, None


class Recorder:
    def __init__(self, device, seconds: float, dry_run: bool, seed: int):
        self.seconds = seconds
        self.dry_run = dry_run
        self.device = device
        self.rng = random.Random(seed)
        self.sd = None
        if not dry_run:
            try:
                import sounddevice  # type: ignore
            except ImportError:
                raise SystemExit("error: pip install sounddevice (or use --dry-run)")
            self.sd = sounddevice

    def record(self) -> np.ndarray:
        if self.dry_run:
            speech = ww.synthetic_speech(self.rng, seconds=self.rng.uniform(0.5, 0.9))
            pad = np.zeros(int(0.4 * ww.TARGET_RATE), np.float32)
            return np.concatenate([pad, speech, pad])
        frames = int(self.seconds * ww.TARGET_RATE)
        data = self.sd.rec(frames, samplerate=ww.TARGET_RATE, channels=1, dtype="float32",
                           device=self.device)
        self.sd.wait()
        return data[:, 0].copy()

    def play(self, audio: np.ndarray) -> None:
        if self.sd is not None:
            self.sd.play(audio, ww.TARGET_RATE, device=self.device)
            self.sd.wait()


def ask(prompt: str, dry_run: bool) -> str:
    if dry_run:
        print(prompt + "[dry-run: Enter]")
        return ""
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return "q"


def main(argv: list[str] | None = None) -> int:
    ww.utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("out/personal"))
    parser.add_argument("--speaker", default="user", help="name tag in file names")
    parser.add_argument("--count", type=int, default=60, help="takes to record (50-100)")
    parser.add_argument("--accents", default="bac,nam", help="comma list of: bac, nam")
    parser.add_argument("--seconds", type=float, default=2.5, help="recording window per take")
    parser.add_argument("--device", default=None, help="input device index or name")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true", help="synthetic takes, no microphone")
    args = parser.parse_args(argv)

    if args.list_devices:
        import sounddevice  # type: ignore

        print(sounddevice.query_devices())
        return 0
    accents = [a.strip() for a in args.accents.split(",") if a.strip()]
    unknown = [a for a in accents if a not in ACCENTS]
    if unknown or not accents:
        print(f"error: unknown accent(s) {unknown}; use: {', '.join(ACCENTS)}", file=sys.stderr)
        return 1
    device = int(args.device) if args.device is not None and str(args.device).isdigit() else args.device
    recorder = Recorder(device, args.seconds, args.dry_run, args.seed)
    manifest = args.out / "manifest.jsonl"
    speaker = ww.slugify(args.speaker, 20)
    schedule = make_schedule(args.count, accents, args.seed)
    existing = {record.get("file") for record in ww.read_jsonl(manifest)}

    print(f"Ghi {args.count} lần \"quản gia\" vào {args.out}. Mỗi lần có {args.seconds:.1f} s.")
    print("Nói một lần \"quản gia\" sau khi thấy dòng 'NÓI!'. Không nói thêm chữ nào khác.")
    saved = 0
    take = 0
    while take < len(schedule):
        accent, style = schedule[take]
        name = f"personal_{speaker}_{accent}_{take:03d}.wav"
        path = args.out / name
        if name in existing and path.exists():
            take += 1
            continue
        label, hint = ACCENTS[accent]
        print(f"\n[{take + 1}/{len(schedule)}] {hint}; {style}")
        answer = ask("Enter để bắt đầu (s = bỏ qua, q = thoát): ", args.dry_run)
        if answer == "q":
            break
        if answer == "s":
            take += 1
            continue
        if not args.dry_run:
            for n in (3, 2, 1):
                print(f"  {n}...", flush=True)
                time.sleep(0.4)
        print("  NÓI!", flush=True)
        audio = recorder.record()
        trimmed, problem = check_take(audio)
        if problem:
            print(f"  -> {problem}. Ghi lại.")
            if args.dry_run:
                take += 1
            continue
        answer = ask(f"  {len(trimmed) / ww.TARGET_RATE:.2f} s, đỉnh {ww.peak_dbfs(trimmed):.1f} dBFS. "
                     "Enter = giữ, r = ghi lại, p = nghe lại: ", args.dry_run)
        while answer == "p":
            recorder.play(trimmed)
            answer = ask("  Enter = giữ, r = ghi lại: ", args.dry_run)
        if answer == "q":
            break
        if answer == "r":
            continue
        ww.write_wav(path, trimmed)
        ww.append_jsonl(manifest, {"file": name, "kind": "personal", "speaker": speaker,
                                   "accent": label, "style": style,
                                   "duration_s": round(len(trimmed) / ww.TARGET_RATE, 3),
                                   "peak_dbfs": round(ww.peak_dbfs(trimmed), 1)})
        saved += 1
        take += 1
    print(f"\nĐã lưu {saved} file mới trong {args.out}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
