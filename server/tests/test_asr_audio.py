"""WAV decoding / resampling tests (server/asr/jarvis_asr/audio.py, soundfile only, no PyAV)."""

from __future__ import annotations

import io
import sys
import wave
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

ASR_DIR = Path(__file__).resolve().parents[1] / "asr"
if str(ASR_DIR) not in sys.path:
    sys.path.insert(0, str(ASR_DIR))

from jarvis_asr.audio import (  # noqa: E402
    TARGET_SR,
    AudioDecodeError,
    decode_audio_bytes,
    make_warmup_sample,
    peak_frame_dbfs,
    resample,
    to_wav_bytes,
)


def sine(freq: float, sr: int, seconds: float = 1.0, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(sr * seconds)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def encode(samples: np.ndarray, sr: int, fmt: str = "WAV", subtype: str = "PCM_16") -> bytes:
    buf = io.BytesIO()
    sf.write(buf, samples, sr, format=fmt, subtype=subtype)
    return buf.getvalue()


def dominant_freq(x: np.ndarray, sr: int) -> float:
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.argmax(spec) * sr / len(x))


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))


def test_xiaozhi_wav_is_decoded_exactly():
    """16 kHz mono PCM16 (what xiaozhi writes with the wave module) -> exact k/32768 samples."""
    pcm = np.array([0, 1, -1, 16384, -16384, 32767, -32768], dtype="<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm.tobytes())
    out = decode_audio_bytes(buf.getvalue())
    assert out.sample_rate_in == 16000 and out.channels_in == 1
    assert out.format == "WAV" and out.subtype == "PCM_16" and out.extension == "wav"
    assert out.samples.dtype == np.float32
    np.testing.assert_array_equal(out.samples, pcm.astype(np.float32) / 32768.0)


def test_wav_encode_decode_roundtrip_is_lossless():
    pcm = np.random.default_rng(1).integers(-32768, 32767, 4000).astype("<i2")
    samples = pcm.astype(np.float32) / 32768.0
    back = decode_audio_bytes(to_wav_bytes(samples)).samples
    np.testing.assert_array_equal(back, samples)


def test_stereo_48k_float_is_downmixed_and_resampled():
    left, right = sine(1000, 48000, amp=0.4), sine(1000, 48000, amp=0.2)
    out = decode_audio_bytes(encode(np.stack([left, right], axis=1), 48000, subtype="FLOAT"))
    assert out.sample_rate_in == 48000 and out.channels_in == 2
    assert len(out.samples) == TARGET_SR
    assert dominant_freq(out.samples, TARGET_SR) == pytest.approx(1000, abs=2)
    assert rms(out.samples[1000:-1000]) == pytest.approx(0.3 / np.sqrt(2), rel=0.02)


@pytest.mark.parametrize("sr_in", [8000, 11025, 22050, 24000, 32000, 44100, 48000])
def test_resample_length_and_pitch(sr_in):
    x = sine(440, sr_in, seconds=1.25)
    y = resample(x, sr_in, TARGET_SR)
    assert len(y) == int(np.ceil(len(x) * TARGET_SR / sr_in))
    assert dominant_freq(y, TARGET_SR) == pytest.approx(440, abs=2)
    assert rms(y[2000:-2000]) == pytest.approx(rms(x), rel=0.02)


def test_resample_removes_content_above_new_nyquist():
    x = sine(12000, 48000)  # above 8 kHz: must not alias into the 16 kHz signal
    y = resample(x, 48000, TARGET_SR)
    # Steady state only: the hard onset/offset of the test tone rings at the edges (Gibbs).
    assert rms(y[800:-800]) < 1e-6 * rms(x)
    assert rms(y) < 5e-3 * rms(x)


def test_resample_identity_and_empty():
    x = sine(300, TARGET_SR)
    assert resample(x, TARGET_SR, TARGET_SR) is not None
    np.testing.assert_array_equal(resample(x, TARGET_SR, TARGET_SR), x)
    assert resample(np.zeros(0, dtype=np.float32), 44100, TARGET_SR).size == 0


@pytest.mark.parametrize(
    "fmt, subtype",
    [("WAV", "PCM_24"), ("WAV", "PCM_32"), ("WAV", "PCM_U8"), ("WAV", "FLOAT"), ("FLAC", "PCM_16")],
)
def test_other_formats_decode(fmt, subtype):
    x = sine(500, TARGET_SR, amp=0.3)
    out = decode_audio_bytes(encode(x, TARGET_SR, fmt, subtype))
    assert len(out.samples) == len(x)
    assert np.max(np.abs(out.samples - x)) < 0.02  # u8 is coarse
    assert out.extension == ("flac" if fmt == "FLAC" else "wav")


def test_non_finite_samples_are_sanitized():
    x = sine(300, TARGET_SR, seconds=0.1)
    x[10], x[20], x[30] = np.nan, np.inf, -np.inf
    out = decode_audio_bytes(encode(x, TARGET_SR, subtype="FLOAT")).samples
    assert np.all(np.isfinite(out))
    assert out[10] == 0.0 and out[20] == 1.0 and out[30] == -1.0


@pytest.mark.parametrize("data", [b"", b"RIFF\x00\x00\x00\x00WAVE", b"not audio" * 100])
def test_garbage_raises_decode_error(data):
    with pytest.raises(AudioDecodeError):
        decode_audio_bytes(data)


def test_max_seconds_truncates_and_flags():
    out = decode_audio_bytes(to_wav_bytes(sine(200, TARGET_SR, seconds=5.0)), max_seconds=2.0)
    assert len(out.samples) == 2 * TARGET_SR and out.truncated
    short = decode_audio_bytes(to_wav_bytes(sine(200, TARGET_SR, seconds=1.0)), max_seconds=2.0)
    assert not short.truncated


def test_peak_frame_level():
    assert peak_frame_dbfs(np.zeros(TARGET_SR, dtype=np.float32)) == float("-inf")
    assert peak_frame_dbfs(sine(1000, TARGET_SR, amp=1.0)) == pytest.approx(-3.01, abs=0.1)
    quiet = np.concatenate([np.zeros(TARGET_SR), sine(1000, TARGET_SR, 0.1, amp=0.01)]).astype(np.float32)
    assert peak_frame_dbfs(quiet) == pytest.approx(-43.0, abs=0.5)  # loudest frame, not the average


def test_warmup_sample_is_valid_audio():
    x = make_warmup_sample()
    assert x.dtype == np.float32 and len(x) == 2 * TARGET_SR
    assert np.all(np.isfinite(x)) and 0.05 < np.max(np.abs(x)) < 1.0
