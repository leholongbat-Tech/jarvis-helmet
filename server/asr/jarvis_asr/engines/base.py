"""Engine interface shared by faster-whisper, the Qwen3-ASR proxy and the fake engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np


class EngineError(RuntimeError):
    """Recognition failed (model error, upstream unreachable...)."""


class EngineUnavailable(EngineError):
    """Upstream engine not reachable (maps to HTTP 503)."""


@dataclass
class Segment:
    text: str
    avg_logprob: Optional[float] = None
    no_speech_prob: Optional[float] = None
    compression_ratio: Optional[float] = None
    start: Optional[float] = None
    end: Optional[float] = None


@dataclass
class Transcription:
    segments: List[Segment] = field(default_factory=list)
    language: Optional[str] = None
    language_probability: Optional[float] = None

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.segments).strip()


@dataclass(frozen=True)
class TranscribeOptions:
    language: Optional[str]  # None = auto, "vi" = fixed, "vi,en" = restricted auto
    initial_prompt: Optional[str] = None
    hotwords: Optional[str] = None
    beam_size: int = 5
    max_new_tokens: Optional[int] = None  # None = engine default (scaled with audio length)


class Engine(ABC):
    """A loaded recognizer. Calls are serialized by the service (one GPU worker)."""

    name = "base"

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @abstractmethod
    def load(self) -> None:
        """Load/download the model or check the upstream (called once at startup)."""

    @abstractmethod
    def transcribe(self, audio: np.ndarray, opts: TranscribeOptions) -> Transcription:
        """Recognize float32 mono 16 kHz audio."""

    def info(self) -> Dict[str, Any]:
        """Identity for /health and capture records."""
        return {"engine": self.name, "model": self.settings.model, "device": self.settings.device}

    def close(self) -> None:
        """Release resources."""
