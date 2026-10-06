"""Model-free engine for tests and wiring checks (ASR_ENGINE=fake, ASR_FAKE_TEXT=...)."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import numpy as np

from .base import Engine, Segment, TranscribeOptions, Transcription


class FakeEngine(Engine):
    """Returns a canned text (or whatever ``responder`` builds) and records calls."""

    name = "fake"

    def __init__(
        self,
        settings: Any = None,
        text: Optional[str] = None,
        responder: Optional[Callable[[np.ndarray, TranscribeOptions], Transcription]] = None,
    ) -> None:
        super().__init__(settings)
        default = getattr(settings, "fake_text", "xin chào") if settings is not None else "xin chào"
        self.text = default if text is None else text
        self.responder = responder
        self.calls: List[Dict[str, Any]] = []
        self.loaded = False

    def load(self) -> None:
        self.loaded = True

    def transcribe(self, audio: np.ndarray, opts: TranscribeOptions) -> Transcription:
        self.calls.append({"audio": audio, "opts": opts})
        if self.responder is not None:
            return self.responder(audio, opts)
        lang = opts.language.split(",")[0] if opts.language else "vi"
        seg = Segment(
            text=" " + self.text if self.text else "",
            avg_logprob=-0.15,
            no_speech_prob=0.01,
            compression_ratio=1.1,
            start=0.0,
            end=round(len(audio) / 16000.0, 2),
        )
        return Transcription([seg] if self.text else [], language=lang, language_probability=1.0)

    def info(self) -> Dict[str, Any]:
        model = getattr(self.settings, "model", "fake") if self.settings is not None else "fake"
        return {"engine": self.name, "model": model, "device": "cpu"}
