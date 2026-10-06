"""PCM helpers: s16le alignment across HTTP chunks, streaming resampling (soxr), WAV headers."""
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
