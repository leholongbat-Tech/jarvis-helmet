"""faster-whisper / CTranslate2 engine (default). PhoWhisper = another CT2 repo in ASR_MODEL."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from .base import Engine, EngineError, Segment, TranscribeOptions, Transcription

log = logging.getLogger("jarvis_asr.engine")

_LARGE_V3 = ("Systran/faster-whisper-large-v3", "edaa852ec7e145841d8ffdb056a99866b5f0a478")
_TURBO = ("dropbox-dash/faster-whisper-large-v3-turbo", "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf")
_PHOWHISPER = ("qbsmlabs/PhoWhisper-large", "3893cf5c791a168d708e730a2e2b81afc42c1e5b")

# Aliases/repos with a pinned Hugging Face revision (ext-ai-stack.md section 1).
KNOWN_MODELS: Dict[str, Tuple[str, str]] = {
    "large-v3": _LARGE_V3,
    "large": _LARGE_V3,
    "systran/faster-whisper-large-v3": _LARGE_V3,
    "large-v3-turbo": _TURBO,
    "turbo": _TURBO,
    "mobiuslabsgmbh/faster-whisper-large-v3-turbo": _TURBO,
    "dropbox-dash/faster-whisper-large-v3-turbo": _TURBO,
    "phowhisper": _PHOWHISPER,
    "phowhisper-large": _PHOWHISPER,
    "qbsmlabs/phowhisper-large": _PHOWHISPER,
}


_MAX_LENGTH = 448  # Whisper decoder context (WhisperModel.max_length)
_PROMPT_CAP = 223  # faster-whisper keeps at most max_length // 2 - 1 prompt tokens
_MIN_TOKENS = 64  # floor for the length-scaled decode cap


def resolve_model(model: str, revision: Optional[str]) -> Tuple[str, Optional[str]]:
    """Map an alias or repo id to (model id or local path, revision to pin)."""
    if os.path.isdir(model):
        return model, None
    known = KNOWN_MODELS.get(model.lower())
    if known:
        return known[0], revision or known[1]
    return model, revision


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001 - any failure means "no usable GPU"
        return False


class FasterWhisperEngine(Engine):
    name = "faster_whisper"

    def __init__(self, settings: Any, model_factory: Optional[Callable[..., Any]] = None) -> None:
        super().__init__(settings)
        self._factory = model_factory  # tests inject a fake WhisperModel
        self._model: Any = None
        self._prompt_cache: Dict[str, int] = {}
        self.model_id, self.revision = resolve_model(settings.model, settings.model_revision)
        self.device = settings.device
        self.compute_type = settings.compute_type

    def load(self) -> None:
        factory = self._factory
        if factory is None:
            from faster_whisper import WhisperModel

            factory = WhisperModel
        device = self.settings.device
        if device == "auto":
            device = "cuda" if _cuda_available() else "cpu"
        compute_type = self.settings.compute_type_for(device)
        if self.revision is None and not os.path.isdir(self.model_id):
            log.warning("model %s has no pinned revision (set ASR_MODEL_REVISION)", self.model_id)
        started = time.perf_counter()
        try:
            self._model = factory(
                self.model_id,
                device=device,
                device_index=self.settings.device_index,
                compute_type=compute_type,
                cpu_threads=self.settings.cpu_threads,
                revision=self.revision,
                local_files_only=self.settings.local_files_only,
            )
        except Exception as exc:
            raise EngineError(
                f"cannot load {self.model_id}@{self.revision or 'latest'} on {device}/{compute_type}: {exc}"
            ) from exc
        self.device, self.compute_type = device, compute_type
        log.info(
            "loaded model=%s revision=%s device=%s compute_type=%s in %.1f s",
            self.model_id,
            self.revision or "-",
            device,
            compute_type,
            time.perf_counter() - started,
        )

    def transcribe(self, audio: np.ndarray, opts: TranscribeOptions) -> Transcription:
        if self._model is None:
            raise EngineError("model not loaded")
        language = opts.language
        picked_prob: Optional[float] = None
        if language and "," in language:
            language, picked_prob = self._pick_language(audio, language.split(","))
        extra: Dict[str, Any] = {}
        limit = self._token_limit(audio, opts)
        if limit is not None:
            extra["max_new_tokens"] = limit
        segments, info = self._model.transcribe(
            audio,
            language=language,
            task="transcribe",
            beam_size=opts.beam_size,
            temperature=0.0,
            vad_filter=False,  # xiaozhi already segmented the utterance with Silero
            condition_on_previous_text=False,
            initial_prompt=opts.initial_prompt,
            hotwords=opts.hotwords,
            without_timestamps=True,
            **extra,
        )
        # The generator runs the decoder lazily: materialize it here.
        segs: List[Segment] = [
            Segment(
                text=s.text,
                avg_logprob=float(s.avg_logprob),
                no_speech_prob=float(s.no_speech_prob),
                compression_ratio=float(s.compression_ratio),
                start=float(s.start),
                end=float(s.end),
            )
            for s in segments
        ]
        prob = picked_prob if picked_prob is not None else float(info.language_probability)
        return Transcription(segs, language=info.language, language_probability=prob)

    def _prompt_tokens(self, text: Optional[str]) -> int:
        """Token count faster-whisper puts in front of the decoder for a prompt (capped at 223)."""
        if not text:
            return 0
        if text not in self._prompt_cache:
            try:
                ids = self._model.hf_tokenizer.encode(" " + text.strip(), add_special_tokens=False).ids
                self._prompt_cache[text] = min(_PROMPT_CAP, len(ids))
            except Exception:  # noqa: BLE001 - unknown tokenizer: assume the worst case
                self._prompt_cache[text] = _PROMPT_CAP
        return self._prompt_cache[text]

    def _token_limit(self, audio: np.ndarray, opts: TranscribeOptions) -> Optional[int]:
        """Decode cap that scales with audio length, so a looping decode cannot run to 448
        tokens (it would block xiaozhi's synchronous ASR call), but never cuts real speech."""
        limit = opts.max_new_tokens
        rate = self.settings.max_tokens_per_s
        if limit is None and rate:
            limit = max(_MIN_TOKENS, int(rate * len(audio) / 16000.0))
        if limit is None:
            return None
        # faster-whisper rejects len(prompt) + max_new_tokens > 448.
        prompt_len = 1 + self._prompt_tokens(opts.hotwords) + self._prompt_tokens(opts.initial_prompt) + 4
        allowed = _MAX_LENGTH - prompt_len - 1
        return min(limit, allowed) if allowed >= 16 else None

    def _pick_language(self, audio: np.ndarray, candidates: List[str]) -> Tuple[str, float]:
        """Auto-detect restricted to ``candidates`` (e.g. vi,en): best-scoring allowed language."""
        _, _, all_probs = self._model.detect_language(audio=audio)
        probs = {lang: float(p) for lang, p in all_probs}
        best = max(candidates, key=lambda code: probs.get(code, 0.0))
        return best, probs.get(best, 0.0)

    def info(self) -> Dict[str, Any]:
        return {
            "engine": self.name,
            "model": self.settings.model,
            "model_id": self.model_id,
            "revision": self.revision,
            "device": self.device,
            "compute_type": self.compute_type,
        }

    def close(self) -> None:
        self._model = None
