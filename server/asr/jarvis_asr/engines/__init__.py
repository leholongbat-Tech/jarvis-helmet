"""Engine factory (ASR_ENGINE)."""

from __future__ import annotations

from typing import Any

from .base import Engine, EngineError, EngineUnavailable, Segment, TranscribeOptions, Transcription
from .fake import FakeEngine

__all__ = [
    "Engine",
    "EngineError",
    "EngineUnavailable",
    "FakeEngine",
    "Segment",
    "TranscribeOptions",
    "Transcription",
    "create_engine",
]


def create_engine(settings: Any) -> Engine:
    """Build the engine selected by ``settings.engine`` (heavy imports stay lazy)."""
    if settings.engine == "faster_whisper":
        from .faster_whisper_engine import FasterWhisperEngine

        return FasterWhisperEngine(settings)
    if settings.engine == "qwen3_vllm":
        from .qwen3_vllm import Qwen3VllmEngine

        return Qwen3VllmEngine(settings)
    if settings.engine == "fake":
        return FakeEngine(settings)
    raise ValueError(f"unknown ASR_ENGINE {settings.engine!r}")
