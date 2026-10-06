"""Audio decoding with soundfile (no PyAV) and resampling to 16 kHz mono float32.

faster-whisper 1.2.1's own decode_audio() breaks with PyAV 19, so uploads are
decoded here and the engine always receives a NumPy array.
"""

from __future__ import annotations

import io
import math
import wave
from dataclasses import dataclass
from typing import Optional

import numpy as np
import soundfile as sf

TARGET_SR = 16000


class AudioDecodeError(ValueError):
    """The uploaded bytes are not a readable audio file."""


@dataclass
class DecodedAudio:
    samples: np.ndarray  # float32, mono, TARGET_SR
    sample_rate_in: int
    channels_in: int
    format: str  # libsndfile container, e.g. "WAV"
    subtype: str  # e.g. "PCM_16"
    truncated: bool = False

    @property
    def duration_s(self) -> float:
        return float(len(self.samples)) / TARGET_SR

    @property
    def extension(self) -> str:
        return {"WAV": "wav", "WAVEX": "wav", "FLAC": "flac", "OGG": "ogg", "MP3": "mp3"}.get(
            self.format.upper(), "bin"
        )


def decode_audio_bytes(
    data: bytes, target_sr: int = TARGET_SR, max_seconds: Optional[float] = None
) -> DecodedAudio:
    """Decode WAV/FLAC/OGG/MP3 bytes to float32 mono at ``target_sr``.

    Multi-channel audio is averaged, non-finite samples are zeroed and anything
    longer than ``max_seconds`` is truncated (flagged in the result).
    """
    if not data:
        raise AudioDecodeError("empty upload")
    try:
        with sf.SoundFile(io.BytesIO(data)) as snd:
            sr, channels = int(snd.samplerate), int(snd.channels)
            fmt, subtype = str(snd.format), str(snd.subtype)
            limit = int(max_seconds * sr) if max_seconds else -1
            audio = snd.read(frames=limit, dtype="float32", always_2d=True)
            truncated = limit > 0 and snd.frames > limit
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        raise AudioDecodeError(f"cannot decode audio: {exc}") from exc
    if sr <= 0:
        raise AudioDecodeError(f"invalid sample rate {sr}")
    mono = audio.mean(axis=1) if audio.shape[1] > 1 else audio[:, 0]
    mono = np.nan_to_num(mono, nan=0.0, posinf=1.0, neginf=-1.0)
    mono = np.clip(mono, -1.0, 1.0)
    if sr != target_sr:
        mono = resample(mono, sr, target_sr)
    return DecodedAudio(
        samples=np.ascontiguousarray(mono, dtype=np.float32),
        sample_rate_in=sr,
        channels_in=channels,
        format=fmt,
        subtype=subtype,
        truncated=bool(truncated),
    )


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """Band-limited FFT resampling (scipy.signal.resample semantics).

    The signal is zero-padded (>= 50 ms each side, to a multiple of the
    decimation factor) so the circular FFT does not wrap the end onto the start.
    """
    if sr_in == sr_out or x.size == 0:
        return np.asarray(x, dtype=np.float32)
    g = math.gcd(int(sr_in), int(sr_out))
    up, down = int(sr_out) // g, int(sr_in) // g
    n = int(x.shape[0])
    pad = down * max(1, math.ceil(0.05 * sr_in / down))
    tail = (-(n + 2 * pad)) % down
    xp = np.concatenate(
        [np.zeros(pad), np.asarray(x, dtype=np.float64), np.zeros(pad + tail)]
    )
    n_in = xp.shape[0]
    n_out = n_in // down * up
    spec = np.fft.rfft(xp)
    out = np.zeros(n_out // 2 + 1, dtype=spec.dtype)
    keep = min(n_in, n_out)
    nyq = keep // 2 + 1
    out[:nyq] = spec[:nyq]
    if keep % 2 == 0:
        # Split/join the Nyquist bin exactly like scipy.signal.resample.
        out[keep // 2] *= 2.0 if n_out < n_in else 0.5
    y = np.fft.irfft(out, n_out) * (float(n_out) / float(n_in))
    start = pad // down * up
    length = min(int(math.ceil(n * up / down)), n_out - start)
    return y[start : start + length].astype(np.float32)


def to_wav_bytes(samples: np.ndarray, sr: int = TARGET_SR) -> bytes:
    """Encode float32 [-1, 1] as 16-bit PCM mono WAV (inverse of soundfile's int16 read)."""
    pcm = np.clip(np.round(np.asarray(samples, dtype=np.float64) * 32768.0), -32768, 32767)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sr)
        wav.writeframes(pcm.astype("<i2").tobytes())
    return buf.getvalue()


def peak_frame_dbfs(x: np.ndarray, sr: int = TARGET_SR, frame_ms: int = 30) -> float:
    """Loudest short-term RMS level in dBFS (-inf for digital silence)."""
    if x.size == 0:
        return float("-inf")
    n = max(1, int(sr * frame_ms / 1000))
    full = (x.size // n) * n
    frames = x[:full].reshape(-1, n) if full else x.reshape(1, -1)
    rms = np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1))
    peak = float(rms.max())
    return 20.0 * math.log10(peak) if peak > 0 else float("-inf")


def make_warmup_sample(seconds: float = 2.0, sr: int = TARGET_SR, seed: int = 1234) -> np.ndarray:
    """Speech-like synthetic signal (harmonics, vibrato, syllable envelope) for warm-up."""
    t = np.arange(int(seconds * sr)) / sr
    f0 = 140.0 + 20.0 * np.sin(2 * np.pi * 3.0 * t)
    phase = 2 * np.pi * np.cumsum(f0) / sr
    voiced = sum(np.sin(k * phase) / k for k in range(1, 16))
    envelope = 0.5 * (1.0 - np.cos(2 * np.pi * ((t * 2.5) % 1.0)))
    noise = np.random.default_rng(seed).normal(0.0, 0.003, t.size)
    x = 0.2 * voiced / np.max(np.abs(voiced)) * envelope + noise
    return x.astype(np.float32)
