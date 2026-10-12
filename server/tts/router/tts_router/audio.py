"""PCM helpers: s16le alignment across HTTP chunks, streaming resampling (soxr), streaming
time-stretching (WSOLA), WAV headers."""
from __future__ import annotations

import struct
from typing import Optional

import numpy as np

SAMPLE_WIDTH = 2  # s16le mono


def wav_header(sample_rate: int, data_bytes: Optional[int] = None) -> bytes:
    """44-byte PCM WAV header. data_bytes=None -> 0xFFFFFFFF sizes ("unknown length", streamable)."""
    if data_bytes is None:
        riff_size = data_size = 0xFFFFFFFF
    else:
        data_size = data_bytes
        riff_size = 36 + data_bytes
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", riff_size, b"WAVE", b"fmt ", 16, 1, 1,
        sample_rate, sample_rate * SAMPLE_WIDTH, SAMPLE_WIDTH, 16,
        b"data", data_size,
    )


def silence(sample_rate: int, ms: int) -> bytes:
    return bytes(int(sample_rate * ms / 1000) * SAMPLE_WIDTH)


class PcmAligner:
    """HTTP chunk boundaries may split a 16-bit sample; carry the odd byte to the next chunk."""

    def __init__(self) -> None:
        self._carry = b""

    def push(self, data: bytes) -> bytes:
        if self._carry:
            data = self._carry + data
            self._carry = b""
        if len(data) % SAMPLE_WIDTH:
            self._carry = data[-1:]
            data = data[:-1]
        return data


class StreamResampler:
    """Chunk-by-chunk s16le resampling with carried filter state (soxr HQ, negligible latency)."""

    def __init__(self, src_rate: int, dst_rate: int):
        import soxr  # imported lazily: only needed when a backend rate differs from the request

        self.src_rate = src_rate
        self.dst_rate = dst_rate
        self._rs = soxr.ResampleStream(src_rate, dst_rate, 1, dtype="int16", quality="HQ")

    def process(self, pcm: bytes) -> bytes:
        if not pcm:
            return b""
        out = self._rs.resample_chunk(np.frombuffer(pcm, dtype="<i2"))
        return np.asarray(out, dtype="<i2").tobytes()

    def flush(self) -> bytes:
        out = self._rs.resample_chunk(np.zeros(0, dtype="<i2"), last=True)
        return np.asarray(out, dtype="<i2").tobytes()


def _to_s16(samples: np.ndarray) -> bytes:
    return np.clip(np.rint(samples), -32768, 32767).astype("<i2").tobytes()


class TimeStretcher:
    """Chunk-by-chunk WSOLA time-scale modification of s16le mono PCM: the speech plays `speed`
    times faster (0.5-2.0 sounds natural) at the same pitch. VieNeu has no speed control.

    30 ms frames, 50 % overlap (a periodic Hann window sums to exactly 1, so the level is kept).
    The input position of each frame advances `speed` times faster than the output and may move
    by up to +-8 ms to where it best continues the previous frame (cross-correlation), which
    keeps voiced speech free of phasing and doubled pitch periods. Output length is
    round(input / speed); added latency is about one frame plus the search window.
    """

    FRAME_S = 0.030
    TOLERANCE_S = 0.008

    def __init__(self, sample_rate: int, speed: float):
        if speed <= 0:
            raise ValueError("speed must be positive")
        self.speed = float(speed)
        self.n = max(32, int(round(sample_rate * self.FRAME_S)) // 2 * 2)
        self.hs = self.n // 2                   # synthesis hop (output)
        self.ha = self.hs * self.speed          # analysis hop (input), fractional
        self.tol = max(1, int(round(sample_rate * self.TOLERANCE_S)))
        self.win = np.hanning(self.n + 1)[:-1].astype(np.float32)
        self._in = np.zeros(0, dtype=np.float32)
        self._in_start = 0      # absolute input index of self._in[0]
        self._in_total = 0      # input samples received
        self._acc = np.zeros(2 * self.n, dtype=np.float32)  # overlap-add accumulator
        self._acc_start = 0     # absolute output index of self._acc[0]
        self._k = 0             # next frame
        self._prev = -1         # input position of the previous frame (-1: none yet)

    def _get(self, start: int, length: int) -> np.ndarray:
        i = start - self._in_start
        return self._in[i:i + length]

    def _ready(self) -> bool:
        end = self._in_start + len(self._in)
        if self._prev < 0:
            return self.n <= end
        p = int(round(self._k * self.ha))
        return max(p + self.tol, self._prev + self.hs) + self.n <= end

    def _step(self) -> None:
        p = int(round(self._k * self.ha))
        if self._prev < 0:
            pos = p
            win = self.win.copy()
            win[:self.hs] = 1.0  # no fade-in on the very first frame
        else:
            # The frame that best continues the previous one (its natural next samples).
            template = self._get(self._prev + self.hs, self.n)
            lo = max(p - self.tol, self._in_start)
            region = self._get(lo, p + self.tol + self.n - lo)
            pos = lo + int(np.argmax(np.correlate(region, template, mode="valid")))
            win = self.win
        o = self._k * self.hs - self._acc_start
        if o + self.n > len(self._acc):
            self._acc = np.concatenate([self._acc, np.zeros(o + self.n - len(self._acc) + self.n,
                                                            dtype=np.float32)])
        self._acc[o:o + self.n] += self._get(pos, self.n) * win
        self._prev = pos
        self._k += 1
        # Drop input no later frame can reach.
        keep = min(pos + self.hs, int(round(self._k * self.ha)) - self.tol)
        if keep > self._in_start:
            self._in = self._in[keep - self._in_start:]
            self._in_start = keep

    def _drain(self, until: int) -> bytes:
        """Return output samples [acc_start, until): no later frame adds to them."""
        count = until - self._acc_start
        if count <= 0:
            return b""
        out = self._acc[:count]
        if len(out) < count:  # flush of a stream shorter than the frames already placed
            out = np.concatenate([out, np.zeros(count - len(out), dtype=np.float32)])
        self._acc = self._acc[count:]
        self._acc_start = until
        return _to_s16(out)

    def process(self, pcm: bytes) -> bytes:
        if pcm:
            x = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
            self._in = np.concatenate([self._in, x])
            self._in_total += len(x)
        while self._ready():
            self._step()
        return self._drain(self._k * self.hs)

    def flush(self) -> bytes:
        if self._in_total == 0:
            return b""
        target = int(round(self._in_total / self.speed))
        pad = self.n + 2 * self.tol + int(self.ha) + 1
        self._in = np.concatenate([self._in, np.zeros(pad, dtype=np.float32)])
        while int(round(self._k * self.ha)) < self._in_total and self._ready():
            self._step()
        return self._drain(max(target, self._acc_start))
