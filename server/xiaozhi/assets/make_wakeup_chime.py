#!/usr/bin/env python3
"""Write a short two-tone chime (16 kHz mono PCM16 WAV).

Replaces upstream config/assets/wakeup_words_short.wav (a Chinese voice clip) as the
fallback wake reply, played only until the Vietnamese TTS reply cache exists.
Deterministic, stdlib only: python make_wakeup_chime.py <output.wav>
"""
import math
import struct
import sys
import wave

RATE = 16000
PEAK = 0.25  # about -12 dBFS
NOTES = ((880.0, 0.16), (1318.5, 0.26))  # A5 then E6, seconds
GAP_S = 0.03
FADE_S = 0.012


def tone(freq, seconds):
    n = int(RATE * seconds)
    fade = int(RATE * FADE_S)
    out = []
    for i in range(n):
        env = min(1.0, i / fade, (n - 1 - i) / fade) if fade else 1.0
        decay = math.exp(-3.0 * i / n)
        out.append(PEAK * env * decay * math.sin(2.0 * math.pi * freq * i / RATE))
    return out


def chime():
    samples = []
    for i, (freq, seconds) in enumerate(NOTES):
        if i:
            samples.extend([0.0] * int(RATE * GAP_S))
        samples.extend(tone(freq, seconds))
    samples.extend([0.0] * int(RATE * 0.05))
    return samples


def write(path):
    frames = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in chime())
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("usage: make_wakeup_chime.py <output.wav>\n")
        sys.exit(2)
    write(sys.argv[1])
