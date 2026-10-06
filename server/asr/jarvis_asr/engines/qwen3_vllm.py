"""Engine B: proxy to a vLLM sidecar serving Qwen3-ASR (OpenAI transcription API).

vLLM's Qwen3-ASR model puts the request ``prompt`` into the system turn as
context (vocabulary biasing) and forces the language when ``language`` is set.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import httpx
import numpy as np

from ..audio import to_wav_bytes
from .base import Engine, EngineError, EngineUnavailable, Segment, TranscribeOptions, Transcription

log = logging.getLogger("jarvis_asr.engine")


def _endpoints(url: str) -> "tuple[str, str]":
    """Accept a base URL, a .../v1 URL or the full transcription URL."""
    url = url.rstrip("/")
    if url.endswith("/audio/transcriptions"):
        base = url[: -len("/audio/transcriptions")]
        return url, base + "/models"
    if url.endswith("/v1"):
        return url + "/audio/transcriptions", url + "/models"
    return url + "/v1/audio/transcriptions", url + "/v1/models"


class Qwen3VllmEngine(Engine):
    name = "qwen3_vllm"

    def __init__(self, settings: Any, transport: Optional[httpx.BaseTransport] = None) -> None:
        super().__init__(settings)
        self.url, self.models_url = _endpoints(settings.vllm_url)
        self._transport = transport  # tests inject httpx.MockTransport
        self._client: Optional[httpx.Client] = None
        self.served_model: Optional[str] = None
        self._warned_multi = False

    def load(self) -> None:
        self._client = httpx.Client(timeout=self.settings.vllm_timeout_s, transport=self._transport)
        deadline = time.monotonic() + self.settings.vllm_wait_s
        while True:
            try:
                resp = self._client.get(self.models_url, timeout=5.0)
                resp.raise_for_status()
                ids: List[str] = [m.get("id") for m in resp.json().get("data", []) if m.get("id")]
                break
            except (httpx.HTTPError, ValueError) as exc:
                if time.monotonic() >= deadline:
                    raise EngineUnavailable(f"vLLM not reachable at {self.models_url}: {exc}") from exc
                log.info("waiting for vLLM at %s (%s)", self.models_url, exc)
                time.sleep(5.0)
        if self.settings.model in ids:
            self.served_model = self.settings.model
        elif len(ids) == 1:
            self.served_model = ids[0]
            log.info("ASR_MODEL=%s not served; using the only vLLM model %s", self.settings.model, ids[0])
        else:
            raise EngineError(f"model {self.settings.model!r} not served by vLLM (available: {ids})")
        log.info("vLLM ready url=%s model=%s", self.url, self.served_model)

    def transcribe(self, audio: np.ndarray, opts: TranscribeOptions) -> Transcription:
        if self._client is None or self.served_model is None:
            raise EngineError("engine not loaded")
        data: Dict[str, str] = {
            "model": self.served_model,
            "temperature": "0",
            "response_format": "json",
        }
        if opts.language and "," not in opts.language:
            data["language"] = opts.language
        elif opts.language and not self._warned_multi:
            log.warning("vLLM cannot restrict detection to %s; using auto-detect", opts.language)
            self._warned_multi = True
        context = " ".join(p for p in (opts.hotwords, opts.initial_prompt) if p)
        if context:
            data["prompt"] = context
        files = {"file": ("audio.wav", to_wav_bytes(audio), "audio/wav")}
        try:
            resp = self._client.post(self.url, data=data, files=files)
        except httpx.TransportError as exc:
            raise EngineUnavailable(f"vLLM request failed: {exc}") from exc
        if resp.status_code != 200:
            raise EngineError(f"vLLM HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            text = str(resp.json().get("text", ""))
        except ValueError as exc:
            raise EngineError(f"vLLM returned non-JSON: {resp.text[:200]}") from exc
        segs = [Segment(text=text)] if text.strip() else []
        return Transcription(segs, language=data.get("language"))

    def info(self) -> Dict[str, Any]:
        return {
            "engine": self.name,
            "model": self.served_model or self.settings.model,
            "device": "vllm",
            "upstream": self.url,
        }

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
