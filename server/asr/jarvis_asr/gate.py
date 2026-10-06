"""Speech gate: skip the decoder for silence or noise (Whisper invents text on them).

xiaozhi already gates speech with Silero in auto mode, but in PTT/manual mode it
sends whatever was recorded, so an empty press must not reach Whisper. The gate
never modifies the audio (decoding still uses vad_filter=False).
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Optional

import numpy as np

from .audio import TARGET_SR, peak_frame_dbfs

log = logging.getLogger("jarvis_asr.gate")

MIN_SAMPLES = TARGET_SR // 10  # < 100 ms cannot contain a command


@dataclass
class GateResult:
    passed: bool
    reason: Optional[str] = None  # too_short | silence | no_speech
    peak_dbfs: Optional[float] = None
    speech_ms: Optional[float] = None
    elapsed_ms: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d["peak_dbfs"] is not None and not np.isfinite(d["peak_dbfs"]):
            d["peak_dbfs"] = None  # JSON has no -inf
        return d


def _silero_speech_ms(threshold: float) -> Callable[[np.ndarray], float]:
    """Total speech duration (ms) found by faster-whisper's bundled Silero VAD v6."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    opts = VadOptions(
        threshold=threshold,
        min_speech_duration_ms=100,
        min_silence_duration_ms=300,
        speech_pad_ms=0,
    )

    def speech_ms(samples: np.ndarray) -> float:
        chunks = get_speech_timestamps(samples, opts, sampling_rate=TARGET_SR)
        return sum(c["end"] - c["start"] for c in chunks) * 1000.0 / TARGET_SR

    return speech_ms


class SpeechGate:
    def __init__(
        self,
        min_rms_dbfs: Optional[float] = -60.0,
        vad_enabled: bool = True,
        vad_threshold: float = 0.5,
        min_speech_ms: int = 250,
        vad_fn: Optional[Callable[[np.ndarray], float]] = None,
    ) -> None:
        self.min_rms_dbfs = min_rms_dbfs
        self.vad_enabled = vad_enabled
        self.vad_threshold = vad_threshold
        self.min_speech_ms = min_speech_ms
        self._vad_fn = vad_fn

    def load(self) -> None:
        """Import and warm the VAD; if it is unavailable the gate falls back to RMS only."""
        if not self.vad_enabled:
            return
        if self._vad_fn is None:
            try:
                self._vad_fn = _silero_speech_ms(self.vad_threshold)
            except Exception as exc:  # noqa: BLE001 - optional dependency
                log.warning("Silero VAD unavailable (%s): speech gate uses RMS only", exc)
                self.vad_enabled = False
                return
        self._vad_fn(np.zeros(TARGET_SR, dtype=np.float32))

    def check(self, samples: np.ndarray) -> GateResult:
        started = time.perf_counter()
        peak = peak_frame_dbfs(samples)

        def done(passed: bool, reason: Optional[str], speech: Optional[float] = None) -> GateResult:
            return GateResult(passed, reason, peak, speech, (time.perf_counter() - started) * 1000.0)

        if samples.size < MIN_SAMPLES:
            return done(False, "too_short")
        if self.min_rms_dbfs is not None and peak < self.min_rms_dbfs:
            return done(False, "silence")
        if self.vad_enabled:
            if self._vad_fn is None:
                self.load()
            if self.vad_enabled and self._vad_fn is not None:
                speech = float(self._vad_fn(samples))
                if speech < self.min_speech_ms:
                    return done(False, "no_speech", speech)
                return done(True, None, speech)
        return done(True, None)
