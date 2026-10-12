"""Jarvis TTS router: OpenAI-style /v1/audio/speech in front of VieNeu-TTS (and optional Kokoro).

Per request: strip markup -> split sentences -> detect pure-English sentences -> normalize each
sentence (Vietnamese or English readings) -> group consecutive sentences per engine -> stream
s16le mono PCM at the requested rate, forwarding every backend chunk as soon as it arrives.
`speed` goes to Kokoro as is; VieNeu has no speed control, so its audio is time-stretched here
(WSOLA, same pitch).
English sentences go to Kokoro only when TTS_ENGLISH_ENGINE=kokoro and Kokoro is healthy;
everything else (and any Kokoro failure) goes to VieNeu.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import unicodedata
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, List, Optional

import anyio
import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .audio import PcmAligner, StreamResampler, TimeStretcher, silence, wav_header
from .backends import BackendError, KokoroBackend, OpenStream, VieNeuBackend, check_all
from .config import Settings
from .normalize_vi import Normalizer, NormalizerConfig, has_speakable, strip_markup
from .textsplit import split_sentences

log = logging.getLogger("tts-router")

_KOKORO_VOICE_RE = re.compile(r"^[a-z]{2}_[a-z0-9_]+(?:\(\d+(?:\.\d+)?\))?(?:\+[a-z]{2}_[a-z0-9_]+(?:\(\d+(?:\.\d+)?\))?)*$")


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    input: str = Field(min_length=1, max_length=20_000)
    voice: Optional[str] = None
    model: Optional[str] = None
    response_format: str = "pcm"
    sample_rate: Optional[int] = None
    stream: bool = True
    speed: Optional[float] = Field(default=None, ge=0.25, le=4.0)
    language: str = "auto"  # extension: "auto" | "vi" | "en" (forces the sentence language)


@dataclass
class Group:
    engine: str
    texts: List[str] = field(default_factory=list)  # normalized sentences
    raw: List[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(self.texts)


def _error(status: int, message: str, code: Optional[str] = None, headers=None) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": "invalid_request_error" if status < 500
                                   else "server_error", "code": code or status}},
                        status_code=status, headers=headers)


async def _close_shielded(stream: Optional[OpenStream]) -> None:
    """Close a backend response even while the request is being cancelled (client went away),
    so the backend sees the disconnect and stops synthesizing."""
    if stream is None:
        return
    with anyio.CancelScope(shield=True):
        try:
            await stream.aclose()
        except Exception:  # closing a broken connection
            pass


def _setup_logging(level: str) -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    log.setLevel(level.upper() if level.upper() in logging._nameToLevel else "INFO")


class Router:
    """Routing, normalization and streaming state shared by the endpoints."""

    def __init__(self, settings: Settings):
        self.settings = settings
        cfg = NormalizerConfig(zero_tens=settings.zero_tens, thousand=settings.thousand,
                               english_min_words=settings.english_min_words,
                               english_min_ratio=settings.english_min_ratio)
        if settings.abbreviations_file:
            try:
                cfg.load_overrides(settings.abbreviations_file)
            except (OSError, ValueError) as exc:
                log.error("cannot load TTS_ABBREV_FILE %s: %s", settings.abbreviations_file, exc)
        self.normalizer = Normalizer(cfg)
        self.vieneu = VieNeuBackend(settings.vieneu_url, settings.vieneu_api_key, settings.vieneu_model)
        self.kokoro = KokoroBackend(settings.kokoro_url)
        self.client: Optional[httpx.AsyncClient] = None
        self._voices_cache: Optional[tuple] = None

    @property
    def backends(self):
        return [self.vieneu, self.kokoro]

    async def start(self) -> None:
        s = self.settings
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=s.connect_timeout, read=s.read_timeout, write=10.0, pool=5.0),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=8))
        await check_all(self.client, self.backends, s.health_timeout)

    async def stop(self) -> None:
        if self.client is not None:
            await self.client.aclose()

    async def health_loop(self) -> None:
        while True:
            await asyncio.sleep(max(1.0, self.settings.health_interval))
            try:
                await check_all(self.client, self.backends, self.settings.health_timeout)
            except Exception:  # never let the poller die
                log.exception("health poll failed")

    # ----- planning -------------------------------------------------------------------------

    def kokoro_usable(self) -> bool:
        return self.settings.english_engine == "kokoro" and self.kokoro.enabled and bool(self.kokoro.healthy)

    def plan(self, text: str, language: str = "auto") -> List[Group]:
        language = (language or "auto").lower()
        groups: List[Group] = []
        use_kokoro = self.kokoro_usable()
        prev_lang = ""
        for sentence in split_sentences(strip_markup(text)):
            if language in ("en", "vi"):
                lang = language
            elif prev_lang and not any(ch.isalpha() for ch in sentence):
                lang = prev_lang  # "42." after an English sentence is read in English, same voice
            else:
                lang = "en" if self.normalizer.is_english(sentence) else "vi"
            prev_lang = lang
            spoken = self.normalizer.en(sentence) if lang == "en" else self.normalizer.vi(sentence)
            if not has_speakable(spoken):
                continue
            engine = "kokoro" if (lang == "en" and use_kokoro) else "vieneu"
            if not groups or groups[-1].engine != engine:
                groups.append(Group(engine))
            groups[-1].texts.append(spoken)
            groups[-1].raw.append(sentence)
        return groups

    def vieneu_voice(self, requested: Optional[str]) -> Optional[str]:
        voice = (requested or "").strip()
        if not voice or voice.lower() in ("default", "auto", "none") or _KOKORO_VOICE_RE.match(voice):
            voice = self.settings.voice.strip()
        return unicodedata.normalize("NFC", voice) if voice else None

    def kokoro_voice(self, requested: Optional[str]) -> str:
        voice = (requested or "").strip()
        return voice if _KOKORO_VOICE_RE.match(voice) else self.settings.english_voice

    # ----- backends -------------------------------------------------------------------------

    async def open_vieneu(self, text: str, voice: Optional[str], rate: int) -> OpenStream:
        tried = []
        candidates = [voice]
        configured = unicodedata.normalize("NFC", self.settings.voice.strip()) or None
        if configured != voice:
            candidates.append(configured)
        if None not in candidates:
            candidates.append(None)
        last_exc: Optional[BackendError] = None
        for cand in candidates:
            try:
                stream = await self.vieneu.open(self.client, text, cand, rate)
                if tried:
                    log.warning("vieneu: voice %r rejected, spoke with %r instead", voice, cand)
                return stream
            except BackendError as exc:
                last_exc = exc
                tried.append(cand)
                if exc.status == 400 and "voice" in exc.message.lower():
                    continue  # unknown voice: keep the helmet talking with another voice
                if exc.status == 0 or exc.status >= 500:
                    self.vieneu.mark_failed(str(exc))
                raise
        assert last_exc is not None
        raise last_exc

    async def open_group(self, group: Group, rate: int, speed: float, voice: Optional[str]) -> OpenStream:
        if group.engine == "kokoro":
            try:
                return await self.kokoro.open(self.client, group.text, self.kokoro_voice(voice), speed)
            except BackendError as exc:
                self.kokoro.mark_failed(str(exc))
                log.warning("kokoro failed (%s); falling back to vieneu", exc)
                group.engine = "vieneu"
        return await self.open_vieneu(group.text, self.vieneu_voice(voice), rate)

    async def _pcm(self, stream: OpenStream, rate: int, speed: float = 1.0) -> AsyncIterator[bytes]:
        aligner = PcmAligner()
        # Kokoro applies `speed` itself; VieNeu ignores it, so its audio is time-stretched.
        stretcher = (TimeStretcher(stream.sample_rate, speed)
                     if stream.backend == "vieneu" and abs(speed - 1.0) > 0.005 else None)
        resampler = StreamResampler(stream.sample_rate, rate) if stream.sample_rate != rate else None
        async for chunk in stream.response.aiter_bytes():
            data = aligner.push(chunk)
            if stretcher is not None:
                data = stretcher.process(data)
            if resampler is not None:
                data = resampler.process(data)
            if data:
                yield data
        tail = stretcher.flush() if stretcher is not None else b""
        if resampler is not None:
            tail = resampler.process(tail) + resampler.flush()
        if tail:
            yield tail

    async def synthesize(self, groups: List[Group], first: OpenStream, rate: int, speed: float,
                         voice: Optional[str], rid: str, prefix: bytes = b"") -> AsyncIterator[bytes]:
        """Yield `prefix` (e.g. a WAV header) then PCM for every group, in order."""
        t0 = time.perf_counter()
        ttfb: Optional[float] = None
        nbytes = 0
        sent = 0  # bytes of the current group
        stream: Optional[OpenStream] = first
        errors = 0

        async def relay(s: OpenStream) -> AsyncIterator[bytes]:
            nonlocal ttfb, nbytes, sent
            async for pcm in self._pcm(s, rate, speed):
                if ttfb is None:
                    ttfb = time.perf_counter() - t0
                nbytes += len(pcm)
                sent += len(pcm)
                yield pcm

        try:
            if prefix:
                yield prefix
            for idx, group in enumerate(groups):
                if idx > 0:
                    try:
                        stream = await self.open_group(group, rate, speed, voice)
                    except BackendError as exc:
                        log.error("%s: group %d (%s) failed: %s", rid, idx, group.engine, exc)
                        errors += 1
                        stream = None
                        continue
                    if nbytes and self.settings.sentence_gap_ms > 0:
                        gap = silence(rate, self.settings.sentence_gap_ms)
                        nbytes += len(gap)
                        yield gap
                sent = 0
                failure = ""
                try:
                    async for pcm in relay(stream):
                        yield pcm
                except httpx.HTTPError as exc:
                    failure = f"stream broke: {exc}"
                finally:
                    await _close_shielded(stream)
                    stream = None
                if group.engine == "kokoro" and (failure or not sent):
                    # Kokoro can fail inside synthesis (OOM on the shared GPU) while /health stays
                    # 200: mark it down and, if nothing was heard yet, say the group with VieNeu.
                    self.kokoro.mark_failed(failure or "empty audio")
                    if not sent:
                        log.warning("%s: kokoro %s; group %d spoken by vieneu", rid, failure or "sent no audio",
                                    idx)
                        group.engine = "vieneu"
                        failure = ""
                        try:
                            stream = await self.open_vieneu(group.text, self.vieneu_voice(voice), rate)
                            async for pcm in relay(stream):
                                yield pcm
                        except BackendError as exc:
                            failure = str(exc)
                        except httpx.HTTPError as exc:
                            failure = f"stream broke: {exc}"
                        finally:
                            await _close_shielded(stream)
                            stream = None
                if failure:
                    log.error("%s: %s %s", rid, group.engine, failure)
                    errors += 1
        finally:
            await _close_shielded(stream)
            total = time.perf_counter() - t0
            log.info("%s done: route=%s ttfb=%s total=%.0fms audio=%.0fms rate=%d errors=%d", rid,
                     "+".join(g.engine for g in groups),
                     f"{ttfb * 1000:.0f}ms" if ttfb is not None else "-", total * 1000,
                     nbytes / 2 / rate * 1000, rate, errors)

    # ----- discovery ------------------------------------------------------------------------

    async def voices(self) -> dict:
        now = time.time()
        if self._voices_cache and now - self._voices_cache[0] < 60:
            return self._voices_cache[1]
        data, errors = [], {}
        for backend in self.backends:
            if not backend.enabled:
                continue
            try:
                data += await backend.voices(self.client, self.settings.health_timeout + 3)
            except (httpx.HTTPError, ValueError) as exc:
                errors[backend.name] = f"{type(exc).__name__}: {exc}"
        result = {"object": "list", "default_voice": self.settings.voice,
                  "english_voice": self.settings.english_voice,
                  "english_engine": self.settings.english_engine, "data": data}
        if errors:
            result["errors"] = errors
        if data and not errors:
            self._voices_cache = (now, result)
        return result


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    _setup_logging(settings.log_level)
    router = Router(settings)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        await router.start()
        log.info("tts-router %s: vieneu=%s kokoro=%s english_engine=%s voice=%r english_voice=%r",
                 __version__, settings.vieneu_url, settings.kokoro_url or "-", settings.english_engine,
                 settings.voice, settings.english_voice)
        poller = asyncio.create_task(router.health_loop())
        try:
            yield
        finally:
            poller.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poller
            await router.stop()

    app = FastAPI(title="Jarvis TTS router", version=__version__, lifespan=lifespan)
    app.state.router = router

    @app.exception_handler(RequestValidationError)
    async def _invalid(_req: Request, exc: RequestValidationError):
        errs = exc.errors()
        first = errs[0] if errs else {}
        loc = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        return _error(400, f"{loc}: {first.get('msg', 'invalid request')}" if loc else "invalid request")

    @app.post("/v1/audio/speech")
    async def speech(req: SpeechRequest):
        rid = f"tts-{uuid.uuid4().hex[:8]}"
        fmt = req.response_format.lower()
        if fmt not in ("pcm", "wav"):
            return _error(400, f"response_format '{fmt}' not supported; use 'pcm' or 'wav'")
        rate = req.sample_rate or settings.default_sample_rate
        if not 8000 <= rate <= 48000:
            return _error(400, "sample_rate must be between 8000 and 48000")
        if len(req.input) > settings.max_input_chars:
            return _error(400, f"input longer than {settings.max_input_chars} characters")
        speed = req.speed or settings.speed
        groups = router.plan(req.input, req.language)
        media = "audio/wav" if fmt == "wav" else "audio/pcm"
        normalized = " | ".join(g.text for g in groups)
        headers = {"X-Request-Id": rid, "X-Sample-Rate": str(rate), "Cache-Control": "no-store",
                   "X-TTS-Normalized": urllib.parse.quote(normalized[:1500])}
        if not groups:
            headers["X-TTS-Route"] = "none"
            return Response(wav_header(rate, 0) if fmt == "wav" else b"", media_type=media, headers=headers)
        try:
            first = await router.open_group(groups[0], rate, speed, req.voice)
        except BackendError as exc:
            log.error("%s: %s", rid, exc)
            if exc.status == 0:
                return _error(503, f"{exc.backend} unavailable: {exc.message}")
            if exc.status == 429:
                return _error(429, f"{exc.backend} busy", headers={"Retry-After": "1"})
            return _error(exc.status if 400 <= exc.status < 500 else 502, f"{exc.backend}: {exc.message}")
        headers["X-TTS-Route"] = "+".join(g.engine for g in groups)
        if abs(speed - 1.0) > 0.005:
            headers["X-TTS-Speed"] = f"{speed:g}"
        if not req.stream:
            pcm = b"".join([c async for c in router.synthesize(groups, first, rate, speed, req.voice, rid)])
            body = wav_header(rate, len(pcm)) + pcm if fmt == "wav" else pcm
            return Response(body, media_type=media, headers=headers)
        prefix = wav_header(rate) if fmt == "wav" else b""
        return StreamingResponse(router.synthesize(groups, first, rate, speed, req.voice, rid, prefix),
                                 media_type=media, headers=headers)

    @app.get("/health")
    async def health():
        await check_all(router.client, router.backends, settings.health_timeout)
        vieneu_ok = bool(router.vieneu.healthy)
        kokoro_needed = settings.english_engine == "kokoro"
        if not vieneu_ok:
            status = "error"
        elif kokoro_needed and not router.kokoro.healthy:
            status = "degraded"  # English falls back to VieNeu
        else:
            status = "ok"
        body = {
            "status": status,
            "version": __version__,
            "english_engine": settings.english_engine,
            "voice": settings.voice,
            "english_voice": settings.english_voice,
            "backends": {
                "vieneu": {"url": settings.vieneu_url, "healthy": vieneu_ok, "detail": router.vieneu.detail},
                "kokoro": {"url": settings.kokoro_url or None, "enabled": router.kokoro.enabled,
                           "healthy": router.kokoro.healthy if router.kokoro.enabled else None,
                           "detail": router.kokoro.detail if router.kokoro.enabled else None},
            },
        }
        return JSONResponse(body, status_code=200 if vieneu_ok else 503)

    @app.get("/livez")
    async def livez():
        return {"status": "alive"}

    @app.get("/v1/voices")
    async def voices():
        result = await router.voices()
        return JSONResponse(result, status_code=200 if result["data"] else 503)

    return app


def app_factory() -> FastAPI:  # for `uvicorn --factory tts_router.main:app_factory`
    return create_app()
