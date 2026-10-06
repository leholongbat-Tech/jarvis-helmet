"""Request pipeline: decode -> speech gate -> engine (one at a time) -> filter -> log/capture."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, List, Optional

from . import __version__
from .audio import DecodedAudio, decode_audio_bytes, make_warmup_sample
from .capture import DatasetCapture
from .config import Settings, parse_language, parse_prompt
from .engines import Engine, Segment, TranscribeOptions, create_engine
from .filters import FilterResult, HallucinationFilter
from .gate import GateResult, SpeechGate

log = logging.getLogger("jarvis_asr")

# xiaozhi uploads asr_<module>_<session uuid>_<uuid4>.wav (core/providers/asr/base.py).
_XIAOZHI_FILE_RE = re.compile(r"^asr_[A-Za-z0-9]+_(?P<sid>[0-9a-fA-F-]{36})_[0-9a-fA-F-]{36}\.wav$")


def session_id_from_filename(filename: Optional[str]) -> Optional[str]:
    """xiaozhi session id embedded in the upload name (correlates with its [LAT] lines)."""
    if not filename:
        return None
    match = _XIAOZHI_FILE_RE.match(os.path.basename(filename))
    return match.group("sid") if match else None


@dataclass
class Overrides:
    """Raw per-request form fields; None = use the service default (env)."""

    language: Optional[str] = None
    prompt: Optional[str] = None
    hotwords: Optional[str] = None


@dataclass
class Result:
    rid: str
    sid: Optional[str]
    text: str
    raw_text: str
    dropped: Optional[str]
    notes: List[str]
    language: Optional[str]
    language_probability: Optional[float]
    segments: List[Segment]
    gate: GateResult
    timing_ms: Dict[str, float]
    options: TranscribeOptions
    audio: DecodedAudio
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return self.audio.duration_s


class AsrService:
    def __init__(
        self,
        settings: Settings,
        engine: Optional[Engine] = None,
        gate: Optional[SpeechGate] = None,
        hallucination_filter: Optional[HallucinationFilter] = None,
    ) -> None:
        self.settings = settings
        self.engine = engine if engine is not None else create_engine(settings)
        self.filter = hallucination_filter or HallucinationFilter(
            settings.filter, settings.hallucinations_file
        )
        self.gate = gate or SpeechGate(
            min_rms_dbfs=settings.min_rms_dbfs,
            vad_enabled=settings.vad_gate,
            vad_threshold=settings.vad_threshold,
            min_speech_ms=settings.vad_min_speech_ms,
        )
        self.capture: Optional[DatasetCapture] = None
        self.ready = False
        self.load_ms: Optional[float] = None
        self.warmup_ms: Optional[float] = None
        self._engine_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self.stats = {"requests": 0, "dropped": 0, "errors": 0}

    # ---------------------------------------------------------------- lifecycle
    def startup(self) -> None:
        started = time.perf_counter()
        self.engine.load()
        self.load_ms = (time.perf_counter() - started) * 1000.0
        self.gate.load()
        if self.settings.save_dir:
            try:
                self.capture = DatasetCapture(self.settings.save_dir)
                log.info("dataset capture enabled: %s", self.settings.save_dir)
            except OSError as exc:
                log.error("dataset capture disabled (%s): %s", self.settings.save_dir, exc)
        if self.settings.warmup > 0:
            self._warmup()
        self.ready = True
        info = self.engine.info()
        log.info(
            "[ASR] ready engine=%s model=%s revision=%s device=%s compute_type=%s language=%s "
            "prompt=%s vad_gate=%s filter=%s capture=%s load_ms=%.0f warmup_ms=%s",
            info.get("engine"),
            info.get("model_id") or info.get("model"),
            info.get("revision") or "-",
            info.get("device"),
            info.get("compute_type") or "-",
            self.settings.language or "auto",
            "yes" if self.settings.initial_prompt else "no",
            self.gate.vad_enabled,
            self.settings.filter.enabled,
            self.capture is not None,
            self.load_ms,
            "-" if self.warmup_ms is None else f"{self.warmup_ms:.0f}",
        )

    def _warmup(self) -> None:
        """Run the full decoder once (CUDA init, cuBLAS handles, PTX JIT on new GPUs).

        The synthetic sample is not speech: with a prompt the decoder may loop, so the
        decode is capped at a few tokens.
        """
        sample = make_warmup_sample()
        opts = replace(self.options_for(Overrides()), max_new_tokens=16)
        for i in range(self.settings.warmup):
            started = time.perf_counter()
            with self._engine_lock:
                out = self.engine.transcribe(sample, opts)
            self.warmup_ms = (time.perf_counter() - started) * 1000.0
            log.info("warm-up %d/%d done in %.0f ms (%d chars)", i + 1, self.settings.warmup,
                     self.warmup_ms, len(out.text))

    def shutdown(self) -> None:
        self.ready = False
        self.engine.close()

    # ---------------------------------------------------------------- requests
    def options_for(self, overrides: Overrides) -> TranscribeOptions:
        s = self.settings
        return TranscribeOptions(
            language=s.language if overrides.language is None else parse_language(overrides.language),
            initial_prompt=s.initial_prompt if overrides.prompt is None else parse_prompt(overrides.prompt),
            hotwords=s.hotwords if overrides.hotwords is None else parse_prompt(overrides.hotwords),
            beam_size=s.beam_size,
        )

    def transcribe_bytes(
        self, data: bytes, filename: Optional[str] = None, overrides: Optional[Overrides] = None
    ) -> Result:
        """Full pipeline for one upload. Raises ConfigError/AudioDecodeError/EngineError."""
        overrides = overrides or Overrides()
        rid = uuid.uuid4().hex[:12]
        sid = session_id_from_filename(filename)
        t0 = time.perf_counter()
        opts = self.options_for(overrides)
        audio = decode_audio_bytes(data, max_seconds=self.settings.max_audio_s)
        t1 = time.perf_counter()
        gate = self.gate.check(audio.samples)
        t2 = time.perf_counter()
        segments: List[Segment] = []
        language: Optional[str] = None
        lang_prob: Optional[float] = None
        wait_ms = infer_ms = filter_ms = 0.0
        if gate.passed:
            try:
                with self._engine_lock:
                    t3 = time.perf_counter()
                    transcription = self.engine.transcribe(audio.samples, opts)
                    t4 = time.perf_counter()
            except Exception as exc:
                with self._stats_lock:
                    self.stats["errors"] += 1
                log.error("[ASR] rid=%s sid=%s error=%s", rid, sid or "-", exc)
                raise
            wait_ms, infer_ms = (t3 - t2) * 1000.0, (t4 - t3) * 1000.0
            segments = transcription.segments
            language, lang_prob = transcription.language, transcription.language_probability
            prompts = [p for p in (opts.initial_prompt, opts.hotwords) if p]
            filtered = self.filter.apply(segments, prompts)
            filter_ms = (time.perf_counter() - t4) * 1000.0
        else:
            filtered = FilterResult(text="", raw_text="", dropped=f"gate:{gate.reason}")
        total_ms = (time.perf_counter() - t0) * 1000.0
        result = Result(
            rid=rid,
            sid=sid,
            text=filtered.text,
            raw_text=filtered.raw_text,
            dropped=filtered.dropped,
            notes=filtered.notes,
            language=language,
            language_probability=lang_prob,
            segments=segments,
            gate=gate,
            timing_ms={
                "decode": (t1 - t0) * 1000.0,
                "gate": (t2 - t1) * 1000.0,
                "wait": wait_ms,
                "infer": infer_ms,
                "filter": filter_ms,
                "total": total_ms,
            },
            options=opts,
            audio=audio,
        )
        with self._stats_lock:
            self.stats["requests"] += 1
            if result.dropped:
                self.stats["dropped"] += 1
        self._log(result)
        return result

    def _log(self, r: Result) -> None:
        t = r.timing_ms
        audio_ms = r.duration_s * 1000.0
        parts = [
            f"rid={r.rid}",
            f"sid={r.sid or '-'}",
            f"audio_ms={audio_ms:.0f}",
            f"sr_in={r.audio.sample_rate_in}",
            f"decode_ms={t['decode']:.1f}",
            f"gate_ms={t['gate']:.1f}",
            f"wait_ms={t['wait']:.1f}",
            f"infer_ms={t['infer']:.1f}",
            f"filter_ms={t['filter']:.1f}",
            f"total_ms={t['total']:.1f}",
            f"rtf={(t['total'] / audio_ms) if audio_ms else 0.0:.3f}",
            f"lang={r.language or '-'}",
            f"dropped={r.dropped or '-'}",
            f"chars={len(r.text)}",
        ]
        if r.audio.truncated:
            parts.append("truncated=1")
        if self.settings.log_text:
            parts.append("text=" + json.dumps(r.text, ensure_ascii=False))
            if r.dropped and r.raw_text:
                parts.append("raw=" + json.dumps(r.raw_text, ensure_ascii=False))
        log.info("[ASR] %s", " ".join(parts))

    # ---------------------------------------------------------------- outputs
    def verbose_json(self, r: Result) -> Dict[str, Any]:
        return {
            "text": r.text,
            "language": r.language,
            "duration": round(r.duration_s, 3),
            "segments": [
                {
                    "id": i,
                    "start": s.start,
                    "end": s.end,
                    "text": s.text,
                    "avg_logprob": s.avg_logprob,
                    "no_speech_prob": s.no_speech_prob,
                    "compression_ratio": s.compression_ratio,
                }
                for i, s in enumerate(r.segments)
            ],
            "jarvis": {
                "rid": r.rid,
                "sid": r.sid,
                "raw_text": r.raw_text,
                "dropped": r.dropped,
                "notes": r.notes,
                "language_probability": r.language_probability,
                "gate": r.gate.as_dict(),
                "timing_ms": {k: round(v, 2) for k, v in r.timing_ms.items()},
                "options": asdict(r.options),
                "engine": self.engine.info(),
            },
        }

    def save_capture(
        self,
        r: Result,
        data: bytes,
        filename: Optional[str],
        client_model: Optional[str],
        overrides: Optional[Overrides],
    ) -> Optional[str]:
        if self.capture is None:
            return None
        record = {
            "sid": r.sid,
            "client_filename": filename,
            "client_model": client_model,
            "text": r.text,
            "raw_text": r.raw_text,
            "dropped": r.dropped,
            "notes": r.notes,
            "language": r.language,
            "language_probability": r.language_probability,
            "duration_s": round(r.duration_s, 3),
            "input": {
                "format": r.audio.format,
                "subtype": r.audio.subtype,
                "sample_rate": r.audio.sample_rate_in,
                "channels": r.audio.channels_in,
                "bytes": len(data),
                "truncated": r.audio.truncated,
            },
            "timing_ms": {k: round(v, 2) for k, v in r.timing_ms.items()},
            "gate": r.gate.as_dict(),
            "options": asdict(r.options),
            "overrides": asdict(overrides) if overrides else None,
            "engine": self.engine.info(),
            "segments": [asdict(s) for s in r.segments],
        }
        return self.capture.save(r.rid, data, r.audio.extension, record)

    def health(self) -> Dict[str, Any]:
        info = self.engine.info()
        body: Dict[str, Any] = {
            "status": "ok" if self.ready else "loading",
            "engine": info.get("engine"),
            "model": info.get("model"),
            "device": info.get("device"),
        }
        body.update({k: v for k, v in info.items() if k not in body})
        with self._stats_lock:
            stats = dict(self.stats)
        body.update(
            {
                "language": self.settings.language or "auto",
                "initial_prompt": bool(self.settings.initial_prompt),
                "hotwords": bool(self.settings.hotwords),
                "beam_size": self.settings.beam_size,
                "vad_gate": self.gate.vad_enabled,
                "min_rms_dbfs": self.settings.min_rms_dbfs,
                "filter": self.settings.filter.enabled,
                "hallucination_entries": self.filter.phrases.count,
                "capture": self.capture is not None,
                "load_ms": None if self.load_ms is None else round(self.load_ms),
                "warmup_ms": None if self.warmup_ms is None else round(self.warmup_ms),
                "version": __version__,
                **stats,
            }
        )
        return body
