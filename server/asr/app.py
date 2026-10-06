"""Jarvis ASR service (contract section 4.4).

POST /v1/audio/transcriptions   multipart ``file`` (+ ``model``, ignored) -> {"text": "..."}
    optional form fields for direct testing / A/B runs:
      language        vi | en | auto | vi,en (auto restricted to the list)
      prompt          initial prompt override ("none" disables the env prompt)
      hotwords        faster-whisper hotwords override ("none" disables)
      response_format json (default) | text | verbose_json (segments, timing, filter info)
GET  /health                    {"status": "ok", "engine": ..., "model": ..., "device": ...}

xiaozhi's OpenaiASR provider sends only ``file`` + ``model`` with no timeout, so
everything else comes from ASR_* env vars (see jarvis_asr/config.py, README.md).

Run: uvicorn app:app --host 0.0.0.0 --port 9000
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Optional

import anyio
from fastapi import BackgroundTasks, FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jarvis_asr import __version__  # noqa: E402
from jarvis_asr.audio import AudioDecodeError  # noqa: E402
from jarvis_asr.config import ConfigError, Settings  # noqa: E402
from jarvis_asr.engines import Engine, EngineError, EngineUnavailable  # noqa: E402
from jarvis_asr.service import AsrService, Overrides  # noqa: E402

log = logging.getLogger("jarvis_asr")

RESPONSE_FORMATS = ("json", "text", "verbose_json")


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    for noisy in ("faster_whisper", "httpx", "httpcore", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _error(status: int, message: str, kind: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"message": message, "type": kind}})


def create_app(
    settings: Optional[Settings] = None,
    engine: Optional[Engine] = None,
    service: Optional[AsrService] = None,
) -> FastAPI:
    """Build the app; the engine is loaded (and warmed up) in the lifespan startup."""
    svc = service or AsrService(settings or Settings.from_env(), engine=engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await run_in_threadpool(svc.startup)  # failure -> uvicorn exits -> container restarts
        try:
            yield
        finally:
            svc.shutdown()

    app = FastAPI(title="Jarvis ASR", version=__version__, lifespan=lifespan)
    app.state.service = svc

    @app.get("/health")
    async def health() -> JSONResponse:
        body = svc.health()
        return JSONResponse(body, status_code=200 if body["status"] == "ok" else 503)

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(
        background: BackgroundTasks,
        file: UploadFile = File(...),
        model: Optional[str] = Form(None),
        language: Optional[str] = Form(None),
        prompt: Optional[str] = Form(None),
        hotwords: Optional[str] = Form(None),
        response_format: Optional[str] = Form(None),
        temperature: Optional[str] = Form(None),  # OpenAI compat; decoding always uses 0
    ):
        fmt = (response_format or "json").strip().lower()
        if fmt not in RESPONSE_FORMATS:
            return _error(400, f"unsupported response_format {fmt!r}; use one of {RESPONSE_FORMATS}")
        if not svc.ready:
            return _error(503, "ASR engine is not ready", "service_unavailable")
        data = await file.read()
        if len(data) > svc.settings.max_upload_mb * 1024 * 1024:
            return _error(413, f"upload larger than {svc.settings.max_upload_mb} MB")
        overrides = Overrides(language=language, prompt=prompt, hotwords=hotwords)
        timeout = svc.settings.request_timeout_s or None
        try:
            with anyio.fail_after(timeout):
                # abandon_on_cancel: a stuck decode must not block xiaozhi's (timeout-less) request
                result = await anyio.to_thread.run_sync(
                    svc.transcribe_bytes, data, file.filename, overrides, abandon_on_cancel=True
                )
        except TimeoutError:
            log.error("[ASR] request timed out after %.0f s (file=%s)", timeout, file.filename)
            return _error(504, f"recognition took longer than {timeout} s", "timeout")
        except (ConfigError, AudioDecodeError) as exc:
            return _error(400, str(exc))
        except EngineUnavailable as exc:
            return _error(503, str(exc), "service_unavailable")
        except EngineError as exc:
            return _error(500, str(exc), "server_error")
        except Exception as exc:  # noqa: BLE001
            log.exception("[ASR] unexpected error")
            return _error(500, f"internal error: {exc}", "server_error")
        if svc.capture is not None:
            background.add_task(svc.save_capture, result, data, file.filename, model, overrides)
        if fmt == "text":
            return PlainTextResponse(result.text)
        if fmt == "verbose_json":
            return svc.verbose_json(result)
        return {"text": result.text}

    return app


setup_logging(os.environ.get("ASR_LOG_LEVEL", "INFO"))
app = create_app()
