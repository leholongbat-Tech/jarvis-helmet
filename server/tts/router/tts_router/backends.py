"""HTTP clients for the synthesis backends (VieNeu-TTS, optional Kokoro-FastAPI)."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger("tts-router")

VIENEU_RATES = (48000, 24000, 16000, 8000)
KOKORO_RATE = 24000


class BackendError(Exception):
    """A backend refused or failed a request before sending audio."""

    def __init__(self, backend: str, status: int, message: str):
        super().__init__(f"{backend}: {status} {message}")
        self.backend = backend
        self.status = status  # 0 = transport error / timeout
        self.message = message


@dataclass
class OpenStream:
    backend: str
    response: httpx.Response
    sample_rate: int
    voice: Optional[str]
    t_open: float = field(default_factory=time.perf_counter)

    async def aclose(self) -> None:
        await self.response.aclose()


class Backend:
    name = "backend"
    health_path = "/health"

    def __init__(self, base_url: str, api_key: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.healthy: Optional[bool] = None  # None = not checked yet
        self.detail: Any = None
        self.checked_at = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def check_health(self, client: httpx.AsyncClient, timeout: float) -> bool:
        if not self.enabled:
            self.healthy, self.detail = False, "disabled"
            return False
        try:
            resp = await client.get(self.base_url + self.health_path, timeout=timeout)
            ok = resp.status_code == 200
            try:
                self.detail = resp.json()
            except ValueError:
                self.detail = resp.text[:200]
        except httpx.HTTPError as exc:
            ok, self.detail = False, f"{type(exc).__name__}: {exc}"
        if ok != self.healthy:
            log.info("backend %s is %s", self.name, "healthy" if ok else f"DOWN ({self.detail})")
        self.healthy, self.checked_at = ok, time.time()
        return ok

    def mark_failed(self, reason: str) -> None:
        if self.healthy:
            log.warning("backend %s marked down: %s", self.name, reason)
        self.healthy, self.detail, self.checked_at = False, reason, time.time()

    async def _open(self, client: httpx.AsyncClient, payload: Dict[str, Any]) -> httpx.Response:
        request = client.build_request("POST", self.base_url + "/v1/audio/speech",
                                       json=payload, headers=self.headers())
        try:
            resp = await client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise BackendError(self.name, 0, f"timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise BackendError(self.name, 0, f"{type(exc).__name__}: {exc}") from exc
        if resp.status_code != 200:
            body = (await resp.aread())[:400].decode("utf-8", "replace")
            await resp.aclose()
            raise BackendError(self.name, resp.status_code, body)
        return resp


class VieNeuBackend(Backend):
    name = "vieneu"

    def __init__(self, base_url: str, api_key: str = "", model: str = "vieneu-v3-turbo"):
        super().__init__(base_url, api_key)
        self.model = model

    async def open(self, client: httpx.AsyncClient, text: str, voice: Optional[str],
                   sample_rate: int) -> OpenStream:
        # VieNeu resamples itself (soxr) to 48/24/16/8 kHz; other rates are resampled here.
        native = sample_rate if sample_rate in VIENEU_RATES else 48000
        payload: Dict[str, Any] = {"model": self.model, "input": text, "response_format": "pcm",
                                   "sample_rate": native}
        if voice:
            payload["voice"] = voice
        resp = await self._open(client, payload)
        try:
            rate = int(resp.headers.get("x-sample-rate", native))
        except ValueError:
            rate = native
        return OpenStream(self.name, resp, rate, voice)

    async def voices(self, client: httpx.AsyncClient, timeout: float) -> List[Dict[str, Any]]:
        resp = await client.get(self.base_url + "/v1/voices", headers=self.headers(), timeout=timeout)
        resp.raise_for_status()
        out = []
        for v in resp.json().get("data", []):
            out.append({"id": v.get("id"), "name": v.get("name", v.get("id")), "engine": self.name,
                        "gender": v.get("gender", ""), "description": v.get("description", ""),
                        "featured": v.get("featured"), "aliases": v.get("aliases") or []})
        return out


class KokoroBackend(Backend):
    name = "kokoro"

    async def open(self, client: httpx.AsyncClient, text: str, voice: Optional[str],
                   speed: float) -> OpenStream:
        payload = {"model": "kokoro", "input": text, "voice": voice or "bm_george",
                   "response_format": "pcm", "stream": True, "speed": speed}
        resp = await self._open(client, payload)
        return OpenStream(self.name, resp, KOKORO_RATE, voice)

    async def voices(self, client: httpx.AsyncClient, timeout: float) -> List[Dict[str, Any]]:
        resp = await client.get(self.base_url + "/v1/audio/voices", timeout=timeout)
        resp.raise_for_status()
        out = []
        for vid in resp.json().get("voices", []):
            if not isinstance(vid, str):
                continue
            gender = {"m": "male", "f": "female"}.get(vid[1:2], "") if "_" in vid else ""
            out.append({"id": vid, "name": vid, "engine": self.name, "gender": gender,
                        "description": "", "featured": None, "aliases": []})
        return out


async def check_all(client: httpx.AsyncClient, backends: List[Backend], timeout: float) -> None:
    await asyncio.gather(*(b.check_health(client, timeout) for b in backends if b.enabled))
