"""Shared helpers for the "Quản gia" wake word tools (Python 3.12, numpy only).

Audio is handled as float32 mono in [-1, 1]; files are written as 16 kHz mono PCM16 WAV,
the format the microWakeWord trainer expects.
"""
from __future__ import annotations

import io
import json
import math
import random
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

import numpy as np

TARGET_RATE = 16000
WAKE_WORD = "quản gia"


def utf8_stdio() -> None:
    """Print Vietnamese safely: on Windows a stdout that is not a real console (Git Bash's
    mintty, a pipe, `| tee`) uses the ANSI code page (cp1252) and the first 'ầ' raises
    UnicodeEncodeError. Call first thing in every entry point."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


# --------------------------------------------------------------------------- text / names

def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def slugify(text: str, max_len: int = 40) -> str:
    """ASCII file-name slug ("Quản gia" -> "quan_gia", "Đức Trí" -> "duc_tri")."""
    text = nfc(text).replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    out = []
    for ch in text.lower():
        out.append(ch if ch.isascii() and ch.isalnum() else "_")
    slug = "_".join(part for part in "".join(out).split("_") if part)
    return (slug or "x")[:max_len]


def load_phrases(path: Path) -> list[str]:
    """One phrase per line; '#' starts a comment; blank lines ignored."""
    phrases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            phrases.append(nfc(line))
    return phrases


# --------------------------------------------------------------------------- audio I/O

def to_float(samples: np.ndarray) -> np.ndarray:
    if samples.dtype == np.int16:
        return samples.astype(np.float32) / 32768.0
    if samples.dtype == np.int32:
        return samples.astype(np.float32) / 2147483648.0
    if samples.dtype == np.uint8:
        return (samples.astype(np.float32) - 128.0) / 128.0
    return samples.astype(np.float32)


def to_int16(audio: np.ndarray) -> np.ndarray:
    return np.clip(np.round(audio * 32767.0), -32768, 32767).astype(np.int16)


def parse_wav_bytes(data: bytes) -> tuple[np.ndarray, int]:
    """Parse a PCM WAV, tolerating the 0xFFFFFFFF sizes of streamed WAV responses."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    pos, rate, channels, bits, fmt = 12, None, 1, 16, 1
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        body = pos + 8
        if chunk_id == b"fmt ":
            fmt = int.from_bytes(data[body:body + 2], "little")
            channels = int.from_bytes(data[body + 2:body + 4], "little")
            rate = int.from_bytes(data[body + 4:body + 8], "little")
            bits = int.from_bytes(data[body + 14:body + 16], "little")
        elif chunk_id == b"data":
            end = len(data) if size in (0, 0xFFFFFFFF) or body + size > len(data) else body + size
            payload = data[body:end]
            if rate is None:
                raise ValueError("WAV data chunk before fmt chunk")
            if fmt == 3 and bits == 32:
                samples = np.frombuffer(payload[:len(payload) // 4 * 4], dtype="<f4")
            elif bits == 16:
                samples = to_float(np.frombuffer(payload[:len(payload) // 2 * 2], dtype="<i2"))
            elif bits == 32:
                samples = to_float(np.frombuffer(payload[:len(payload) // 4 * 4], dtype="<i4"))
            elif bits == 8:
                samples = to_float(np.frombuffer(payload, dtype=np.uint8))
            else:
                raise ValueError(f"unsupported WAV sample width {bits}")
            if channels > 1:
                samples = samples[: len(samples) // channels * channels].reshape(-1, channels).mean(axis=1)
            return samples.astype(np.float32), rate
        pos = body + size + (size & 1)
    raise ValueError("WAV without data chunk")


def read_audio(path: Path, rate: int = TARGET_RATE) -> np.ndarray:
    """Read any audio file as float32 mono at `rate` (WAV natively, else soundfile/ffmpeg)."""
    path = Path(path)
    audio = None
    source_rate = None
    if path.suffix.lower() == ".wav":
        try:
            audio, source_rate = parse_wav_bytes(path.read_bytes())
        except ValueError:
            audio = None
    if audio is None:
        try:
            import soundfile  # optional

            data, source_rate = soundfile.read(str(path), dtype="float32", always_2d=True)
            audio = data.mean(axis=1)
        except Exception:  # noqa: BLE001 - fall back to ffmpeg
            audio = None
    if audio is None:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError(f"cannot decode {path}: install soundfile or ffmpeg")
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(rate), "-"],
            check=True, capture_output=True).stdout
        return to_float(np.frombuffer(raw, dtype="<i2"))
    return resample(audio, source_rate, rate)


def write_wav(path: Path, audio: np.ndarray, rate: int = TARGET_RATE) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = audio if audio.dtype == np.int16 else to_int16(audio)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.astype("<i2").tobytes())


# --------------------------------------------------------------------------- DSP

def resample(audio: np.ndarray, rate_in: float, rate_out: float) -> np.ndarray:
    """Band-limited resampling (scipy polyphase if installed, else FFT)."""
    if len(audio) == 0 or abs(rate_in - rate_out) < 1e-6:
        return audio.astype(np.float32)
    try:
        from scipy.signal import resample_poly  # optional

        ratio = Fraction(rate_out / rate_in).limit_denominator(1000)
        return resample_poly(audio, ratio.numerator, ratio.denominator).astype(np.float32)
    except ImportError:
        pass
    n_out = max(1, int(round(len(audio) * rate_out / rate_in)))
    pad = min(len(audio), 1024)
    padded = np.concatenate([np.zeros(pad, np.float32), audio.astype(np.float32), np.zeros(pad, np.float32)])
    n_pad_out = int(round(len(padded) * rate_out / rate_in))
    spectrum = np.fft.rfft(padded)
    out_bins = n_pad_out // 2 + 1
    if out_bins <= len(spectrum):
        spectrum = spectrum[:out_bins]
    else:
        spectrum = np.concatenate([spectrum, np.zeros(out_bins - len(spectrum), complex)])
    out = np.fft.irfft(spectrum, n_pad_out) * (n_pad_out / len(padded))
    start = int(round(pad * rate_out / rate_in))
    return out[start:start + n_out].astype(np.float32)


def change_speed(audio: np.ndarray, factor: float, rate: int = TARGET_RATE) -> np.ndarray:
    """Tape-style speed change: factor > 1 is faster and higher (VieNeu ignores `speed`)."""
    return resample(audio, rate * factor, rate)


def time_stretch(audio: np.ndarray, factor: float, rate: int = TARGET_RATE) -> np.ndarray:
    """WSOLA tempo change without pitch change; factor > 1 is faster (shorter)."""
    if abs(factor - 1.0) < 1e-3 or len(audio) < rate // 10:
        return audio.astype(np.float32)
    frame = int(0.030 * rate)
    hop_out = frame // 2
    hop_in = hop_out * factor
    tolerance = int(0.010 * rate)
    window = np.hanning(frame).astype(np.float32)
    n_out = int(len(audio) / factor) + frame
    out = np.zeros(n_out + frame, np.float32)
    norm = np.zeros(n_out + frame, np.float32)
    src = np.concatenate([audio.astype(np.float32), np.zeros(frame + tolerance + 1, np.float32)])
    prev_tail = None
    out_pos, in_pos = 0, 0.0
    while int(in_pos) + frame + tolerance < len(src) and out_pos + frame < len(out):
        center = int(in_pos)
        best = center
        if prev_tail is not None:
            lo, hi = max(0, center - tolerance), center + tolerance
            seg = src[lo:hi + hop_out]
            corr = np.correlate(seg, prev_tail, mode="valid")
            if len(corr):
                best = lo + int(np.argmax(corr))
        piece = src[best:best + frame]
        if len(piece) < frame:
            break
        out[out_pos:out_pos + frame] += piece * window
        norm[out_pos:out_pos + frame] += window
        prev_tail = src[best + hop_out:best + hop_out + hop_out]
        if len(prev_tail) < hop_out:
            break
        out_pos += hop_out
        in_pos += hop_in
    norm[norm < 1e-6] = 1.0
    result = (out / norm)[: int(len(audio) / factor)]
    return result.astype(np.float32)


def pitch_shift(audio: np.ndarray, semitones: float, rate: int = TARGET_RATE) -> np.ndarray:
    """Pitch shift keeping the duration (time stretch, then tape-speed resample)."""
    if abs(semitones) < 1e-3:
        return audio.astype(np.float32)
    factor = 2.0 ** (semitones / 12.0)
    return change_speed(time_stretch(audio, 1.0 / factor, rate), factor, rate)


def rms_dbfs(audio: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64)))) if len(audio) else 0.0
    return 20.0 * math.log10(max(rms, 1e-9))


def peak_dbfs(audio: np.ndarray) -> float:
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    return 20.0 * math.log10(max(peak, 1e-9))


def apply_gain_db(audio: np.ndarray, gain_db: float) -> np.ndarray:
    return (audio * (10.0 ** (gain_db / 20.0))).astype(np.float32)


def normalize_peak(audio: np.ndarray, target_dbfs: float = -3.0) -> np.ndarray:
    return apply_gain_db(audio, target_dbfs - peak_dbfs(audio))


def trim_silence(audio: np.ndarray, rate: int = TARGET_RATE, threshold_db: float = -40.0,
                 pad_ms: int = 150) -> np.ndarray:
    """Cut leading/trailing parts quieter than threshold_db below the loudest 10 ms frame."""
    frame = max(1, rate // 100)
    n = len(audio) // frame
    if n == 0:
        return audio
    frames = audio[: n * frame].reshape(n, frame)
    levels = 10.0 * np.log10(np.mean(frames.astype(np.float64) ** 2, axis=1) + 1e-12)
    active = np.where(levels > levels.max() + threshold_db)[0]
    if len(active) == 0:
        return audio
    pad = int(pad_ms * rate / 1000)
    start = max(0, active[0] * frame - pad)
    end = min(len(audio), (active[-1] + 1) * frame + pad)
    return audio[start:end]


# --------------------------------------------------------------------------- manifests

def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --------------------------------------------------------------------------- TTS client

class TtsError(RuntimeError):
    pass


@dataclass
class TtsClient:
    """OpenAI-style speech API: our tts-router or a VieNeu `apps.openai_speech` server.

    Requests raw PCM (`response_format: pcm`) at `rate` and accepts WAV as well.
    VieNeu ignores `speed`; speed/pitch variation is done locally.
    """

    url: str
    api_key: str | None = None
    model: str = "vieneu-v3-turbo"
    rate: int = 24000
    timeout: float = 120.0
    retries: int = 4
    extra: dict = field(default_factory=dict)

    def _request(self, url: str, payload: dict | None) -> tuple[bytes, dict]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="GET" if data is None else "POST")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.api_key:
            request.add_header("Authorization", f"Bearer {self.api_key}")
        delay = 1.0
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read(), dict(response.headers.items())
            except urllib.error.HTTPError as error:
                body = error.read()[:300].decode("utf-8", "replace")
                if error.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    retry_after = error.headers.get("Retry-After")
                    time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else delay)
                    delay *= 2
                    continue
                raise TtsError(f"HTTP {error.code} from {url}: {body}") from error
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                if attempt < self.retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise TtsError(f"cannot reach {url}: {error}") from error
        raise TtsError("unreachable")

    def synthesize(self, text: str, voice: str | None, extra: dict | None = None) -> np.ndarray:
        """Return float32 mono audio at TARGET_RATE (`extra` adds request fields)."""
        payload = {"model": self.model, "input": nfc(text), "response_format": "pcm",
                   "sample_rate": self.rate, **self.extra, **(extra or {})}
        if voice:
            payload["voice"] = nfc(voice)
        body, headers = self._request(self.url, payload)
        lowered = {key.lower(): value for key, value in headers.items()}
        if body[:4] == b"RIFF":
            audio, rate = parse_wav_bytes(body)
        else:
            rate = int(lowered.get("x-sample-rate", self.rate))
            audio = to_float(np.frombuffer(body[: len(body) // 2 * 2], dtype="<i2"))
        if len(audio) == 0:
            raise TtsError(f"empty audio for {text!r} / {voice!r}")
        return resample(audio, rate, TARGET_RATE)

    def list_voices(self) -> list[str]:
        """GET <base>/v1/voices; accepts {"data": [...]}, {"voices": [...]} or a list."""
        base = self.url.split("/v1/", 1)[0]
        body, _ = self._request(base + "/v1/voices", None)
        data = json.loads(body)
        if isinstance(data, dict):
            data = data.get("data") or data.get("voices") or []
        names = []
        for item in data:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                # tts-router also lists its Kokoro (English) voices; for Vietnamese text it would
                # silently swap such a name for its default VieNeu voice.
                engine = item.get("engine")
                if engine is not None and str(engine).lower() != "vieneu":
                    continue
                name = item.get("name") or item.get("id") or item.get("voice_id")
                if name:
                    names.append(str(name))
        return [nfc(name) for name in names]


# --------------------------------------------------------------------------- Piper (optional)

class PiperVoice:
    """Optional Piper voice (pip install piper-tts) with per-call speaker/length/noise."""

    def __init__(self, model_path: Path):
        try:
            from piper import PiperVoice as _Voice  # type: ignore
        except ImportError as error:
            raise RuntimeError("piper-tts is not installed (pip install piper-tts)") from error
        self.path = Path(model_path)
        self.voice = _Voice.load(str(self.path))
        config = getattr(self.voice, "config", None)
        self.num_speakers = int(getattr(config, "num_speakers", 1) or 1)
        self.rate = int(getattr(config, "sample_rate", 22050) or 22050)
        self.name = self.path.stem

    def synthesize(self, text: str, speaker: int, length_scale: float, noise_scale: float,
                   noise_w: float) -> np.ndarray:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            try:  # piper-tts >= 1.3
                from piper import SynthesisConfig  # type: ignore

                config = SynthesisConfig(speaker_id=speaker if self.num_speakers > 1 else None,
                                         length_scale=length_scale, noise_scale=noise_scale,
                                         noise_w_scale=noise_w)
                self.voice.synthesize_wav(nfc(text), handle, syn_config=config)
            except ImportError:  # piper-tts 1.2
                self.voice.synthesize(nfc(text), handle,
                                      speaker_id=speaker if self.num_speakers > 1 else None,
                                      length_scale=length_scale, noise_scale=noise_scale,
                                      noise_w=noise_w)
        audio, rate = parse_wav_bytes(buffer.getvalue())
        return resample(audio, rate, TARGET_RATE)


# --------------------------------------------------------------------------- variation

@dataclass
class Variation:
    speed: float = 1.0       # tape speed (tempo and pitch together)
    tempo: float = 1.0       # WSOLA tempo only
    semitones: float = 0.0   # pitch only
    gain_db: float = -3.0    # final peak level (dBFS)

    def apply(self, audio: np.ndarray) -> np.ndarray:
        out = audio
        if abs(self.speed - 1.0) > 1e-3:
            out = change_speed(out, self.speed)
        if abs(self.tempo - 1.0) > 1e-3:
            out = time_stretch(out, self.tempo)
        if abs(self.semitones) > 1e-3:
            out = pitch_shift(out, self.semitones)
        out = trim_silence(out)
        return normalize_peak(out, self.gain_db)

    def as_dict(self) -> dict:
        return {"speed": round(self.speed, 3), "tempo": round(self.tempo, 3),
                "semitones": round(self.semitones, 2), "peak_dbfs": round(self.gain_db, 1)}


def random_variation(rng: random.Random, speed: tuple[float, float], tempo: tuple[float, float],
                     semitones: float, gain: tuple[float, float]) -> Variation:
    variation = Variation(speed=rng.uniform(*speed), gain_db=rng.uniform(*gain))
    if rng.random() < 0.5:
        variation.tempo = rng.uniform(*tempo)
    if semitones > 0 and rng.random() < 0.5:
        variation.semitones = rng.uniform(-semitones, semitones)
    return variation


def synthetic_speech(rng: random.Random, seconds: float = 0.7) -> np.ndarray:
    """Speech-like test signal (vowel formants + syllable envelope) for --dry-run."""
    n = int(seconds * TARGET_RATE)
    t = np.arange(n) / TARGET_RATE
    f0 = rng.uniform(95, 220)
    voice = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, 12))
    envelope = np.clip(np.sin(np.pi * t / seconds * 2) ** 2, 0, 1)
    lead = np.zeros(int(0.2 * TARGET_RATE), np.float32)
    signal = np.concatenate([lead, (voice * envelope).astype(np.float32), lead])
    return normalize_peak(signal + 0.002 * np.random.default_rng(rng.randint(0, 2**31)).standard_normal(len(signal)), -6)
