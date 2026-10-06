#!/usr/bin/env python3
"""Offline augmentation for wake word clips: helmet-like reverb, background noise, gain.

The trainer adds its own room impulse responses and background sets; this script adds what
it does not know about: the inside of a helmet (very short, dense early reflections from a
shell ~20 cm across, a little low-pass) and our own noise recordings (fan, street, cafe,
music). Input/output: 16 kHz mono WAV (other formats are decoded with soundfile/ffmpeg).

Examples:
    python augment.py --in out/personal --out out/personal_aug --variants 3
    python augment.py --in out/positives --out out/positives_aug --noise-dir noise/ --rir-dir rirs/
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import numpy as np

import wwcommon as ww

AUDIO_SUFFIXES = {".wav", ".flac", ".mp3", ".ogg", ".m4a", ".opus"}


def fft_filter(audio: np.ndarray, highpass_hz: float | None = None, lowpass_hz: float | None = None,
               rate: int = ww.TARGET_RATE) -> np.ndarray:
    """Zero-phase filter with smooth (2nd-order-like) magnitude slopes."""
    n = len(audio)
    if n == 0:
        return audio
    size = 1 << (n - 1).bit_length()
    spectrum = np.fft.rfft(audio, size)
    freqs = np.fft.rfftfreq(size, 1.0 / rate)
    gain = np.ones_like(freqs)
    if highpass_hz:
        ratio = freqs / highpass_hz
        gain *= ratio ** 2 / np.sqrt(1.0 + ratio ** 4)
    if lowpass_hz:
        ratio = freqs / lowpass_hz
        gain *= 1.0 / np.sqrt(1.0 + ratio ** 4)
    return np.fft.irfft(spectrum * gain, size)[:n].astype(np.float32)


def convolve(audio: np.ndarray, impulse: np.ndarray) -> np.ndarray:
    size = 1 << (len(audio) + len(impulse) - 2).bit_length()
    out = np.fft.irfft(np.fft.rfft(audio, size) * np.fft.rfft(impulse, size), size)
    return out[: len(audio)].astype(np.float32)


def helmet_impulse(rng: random.Random, rate: int = ww.TARGET_RATE) -> np.ndarray:
    """Synthetic impulse response of a closed helmet around the mouth and microphone."""
    length = int(rng.uniform(0.06, 0.18) * rate)
    impulse = np.zeros(length, np.float32)
    impulse[0] = 1.0
    # Early reflections: path differences of 5-60 cm inside the shell (0.3-1.8 ms).
    for _ in range(rng.randint(4, 9)):
        delay = int(rng.uniform(0.0003, 0.0018) * rate)
        impulse[max(1, delay)] += rng.uniform(0.25, 0.75) * rng.choice((-1.0, 1.0))
    # Dense, fast decaying tail (RT60 40-150 ms) from padding and visor.
    rt60 = rng.uniform(0.04, 0.15)
    t = np.arange(length) / rate
    np_rng = np.random.default_rng(rng.randint(0, 2**31))
    tail = np_rng.standard_normal(length).astype(np.float32) * np.exp(-6.9 * t / rt60)
    impulse += rng.uniform(0.05, 0.25) * tail
    impulse = fft_filter(impulse, lowpass_hz=rng.uniform(4500, 7500), rate=rate)
    return impulse / (np.sqrt(np.sum(impulse ** 2)) + 1e-9)


def colored_noise(rng: random.Random, n: int) -> np.ndarray:
    """White, pink or brown noise, or a fan-like hum, normalized to RMS 1."""
    np_rng = np.random.default_rng(rng.randint(0, 2**31))
    kind = rng.choice(("white", "pink", "brown", "hum"))
    if kind == "hum":
        t = np.arange(n) / ww.TARGET_RATE
        base = rng.uniform(90, 260)
        noise = sum(rng.uniform(0.2, 1.0) / k * np.sin(2 * np.pi * base * k * t + rng.uniform(0, 6.3))
                    for k in range(1, 6)) + 0.3 * np_rng.standard_normal(n)
    else:
        white = np_rng.standard_normal(n)
        if kind == "white":
            noise = white
        else:
            size = 1 << (n - 1).bit_length()
            spectrum = np.fft.rfft(white, size)
            freqs = np.maximum(np.fft.rfftfreq(size, 1.0 / ww.TARGET_RATE), 20.0)
            spectrum /= np.sqrt(freqs) if kind == "pink" else freqs
            noise = np.fft.irfft(spectrum, size)[:n]
    noise = np.asarray(noise, np.float32)
    return noise / (np.sqrt(np.mean(noise ** 2)) + 1e-9)


class NoiseBank:
    def __init__(self, directory: Path | None, rng: random.Random, max_cached: int = 64):
        self.files = sorted(p for p in directory.rglob("*") if p.suffix.lower() in AUDIO_SUFFIXES) \
            if directory else []
        self.rng = rng
        self.cache: dict[Path, np.ndarray] = {}
        self.max_cached = max_cached

    def segment(self, n: int) -> tuple[np.ndarray, str]:
        if not self.files:
            return colored_noise(self.rng, n), "synthetic"
        path = self.rng.choice(self.files)
        audio = self.cache.get(path)
        if audio is None:
            audio = ww.read_audio(path)
            if len(self.cache) < self.max_cached:
                self.cache[path] = audio
        if len(audio) < n:
            audio = np.tile(audio, n // max(1, len(audio)) + 1)
        start = self.rng.randrange(0, len(audio) - n + 1)
        segment = audio[start:start + n]
        rms = np.sqrt(np.mean(segment ** 2))
        return (segment / (rms + 1e-9)).astype(np.float32), path.name


def mix_at_snr(clean: np.ndarray, noise_unit: np.ndarray, snr_db: float) -> np.ndarray:
    speech_rms = np.sqrt(np.mean(clean ** 2)) + 1e-9
    return (clean + noise_unit * speech_rms * 10 ** (-snr_db / 20.0)).astype(np.float32)


def augment_clip(audio: np.ndarray, rng: random.Random, noises: NoiseBank, rirs: list[Path],
                 args) -> tuple[np.ndarray, dict]:
    info: dict = {}
    out = audio.astype(np.float32)
    # A little silence around the word, like a real capture.
    lead = np.zeros(int(rng.uniform(0.05, 0.4) * ww.TARGET_RATE), np.float32)
    tail = np.zeros(int(rng.uniform(0.05, 0.3) * ww.TARGET_RATE), np.float32)
    out = np.concatenate([lead, out, tail])
    if rirs and rng.random() < args.rir_prob:
        rir_path = rng.choice(rirs)
        rir = ww.read_audio(rir_path)[: ww.TARGET_RATE]
        out = convolve(out, rir / (np.sqrt(np.sum(rir ** 2)) + 1e-9))
        info["rir"] = rir_path.name
    if rng.random() < args.helmet_prob:
        out = convolve(out, helmet_impulse(rng))
        info["helmet_reverb"] = True
    out = fft_filter(out, highpass_hz=rng.uniform(70, 150))  # small MEMS mic roll-off
    if rng.random() < args.noise_prob:
        noise, source = noises.segment(len(out))
        snr = rng.uniform(args.snr_min, args.snr_max)
        out = mix_at_snr(out, noise, snr)
        info.update(noise=source, snr_db=round(snr, 1))
    gain = rng.uniform(args.gain_min, args.gain_max)
    out = ww.normalize_peak(out, -1.0)
    out = ww.apply_gain_db(out, gain)
    info["gain_db"] = round(gain, 1)
    return np.clip(out, -1.0, 1.0), info


def main(argv: list[str] | None = None) -> int:
    ww.utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--in", dest="input", type=Path, required=True, help="input clip folder")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--variants", type=int, default=2, help="augmented copies per clip")
    parser.add_argument("--keep-original", action="store_true", help="also copy the clean clip")
    parser.add_argument("--noise-dir", type=Path, help="background recordings (any length)")
    parser.add_argument("--rir-dir", type=Path, help="room impulse responses (wav)")
    parser.add_argument("--noise-prob", type=float, default=0.8)
    parser.add_argument("--helmet-prob", type=float, default=0.6)
    parser.add_argument("--rir-prob", type=float, default=0.3)
    parser.add_argument("--snr-min", type=float, default=3.0)
    parser.add_argument("--snr-max", type=float, default=20.0)
    parser.add_argument("--gain-min", type=float, default=-15.0, help="dB below -1 dBFS peak")
    parser.add_argument("--gain-max", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=3)
    args = parser.parse_args(argv)

    rng = random.Random(args.seed)
    files = sorted(p for p in args.input.rglob("*") if p.suffix.lower() in AUDIO_SUFFIXES)
    if not files:
        print(f"error: no audio files in {args.input}", file=sys.stderr)
        return 1
    rirs = sorted(p for p in args.rir_dir.rglob("*.wav")) if args.rir_dir else []
    noises = NoiseBank(args.noise_dir, rng)
    manifest = args.out / "manifest.jsonl"
    written = 0
    for path in files:
        audio = ww.read_audio(path)
        stem = path.stem
        if args.keep_original:
            ww.write_wav(args.out / f"{stem}.wav", audio)
            written += 1
        for index in range(args.variants):
            name = f"{stem}_aug{index}.wav"
            clip, info = augment_clip(audio, rng, noises, rirs, args)
            ww.write_wav(args.out / name, clip)
            ww.append_jsonl(manifest, {"file": name, "source": path.name, **info})
            written += 1
    print(f"wrote {written} clips to {args.out} ({len(files)} inputs, noise: "
          f"{'synthetic' if not noises.files else f'{len(noises.files)} files'}, RIRs: {len(rirs)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
