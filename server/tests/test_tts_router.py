"""tts-router end to end against fake VieNeu / Kokoro backends (real HTTP, uvicorn threads).

Covers: streaming PCM at the requested rate, first chunk forwarded before the backend finishes,
normalization, resampling, WAV output, English routing to Kokoro and fallbacks, error mapping,
unknown-voice fallback, voices listing, health, and closing the backend when the client leaves.
"""
import asyncio
import io
import socket
import struct
import sys
import threading
import time
import urllib.parse
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import httpx
import numpy as np
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tts" / "router"))

from tts_router.config import Settings  # noqa: E402
from tts_router.main import create_app  # noqa: E402


# --- tiny servers ---------------------------------------------------------------------------

class Served:
    def __init__(self, app):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [sock]}, daemon=True)
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.01)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)


def tone(rate: int, seconds: float) -> bytes:
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * 220 * t) * 8000).astype("<i2").tobytes()


@dataclass
class FakeState:
    voices: tuple = ("Hải Đăng", "Thiện Minh", "Mai Anh")
    down: bool = False
    fail_status: int = 0
    rate_override: int = 0
    seconds: float = 0.5
    first_chunk: int = 960
    chunk: int = 4096
    delay: float = 0.0
    requests: List[dict] = field(default_factory=list)
    cancelled: bool = False
    finished: bool = False
    # Failures inside synthesis after a 200: "break" (connection drops before any audio),
    # "empty" (no audio at all), "break_after_first" (drops after the first chunk).
    mid_stream: str = ""


def fake_backend(state: FakeState, kind: str) -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health():
        if state.down:
            return JSONResponse({"status": "error"}, status_code=503)
        return {"status": "ok" if kind == "vieneu" else "healthy"}

    @app.get("/v1/voices")
    async def voices():
        return {"object": "list", "data": [
            {"id": v, "name": v, "gender": "female" if v == "Mai Anh" else "male",
             "description": "test", "featured": None, "aliases": []} for v in state.voices]}

    @app.get("/v1/audio/voices")
    async def kokoro_voices():
        return {"voices": ["bm_george", "bm_lewis", "af_heart"]}

    @app.post("/v1/audio/speech")
    async def speech(request: Request):
        body = await request.json()
        state.requests.append(body)
        if state.down or state.fail_status:
            return JSONResponse({"error": {"message": "boom"}}, status_code=state.fail_status or 500)
        voice = body.get("voice")
        if kind == "vieneu" and voice and voice not in state.voices:
            return JSONResponse({"error": {"message": f"unknown voice '{voice}'; see GET /v1/voices"}},
                                status_code=400)
        rate = state.rate_override or (body.get("sample_rate", 48000) if kind == "vieneu" else 24000)
        pcm = tone(rate, state.seconds)

        async def gen():
            try:
                if state.mid_stream == "break":
                    raise RuntimeError("synthesis crashed")
                if state.mid_stream == "empty":
                    return
                yield pcm[: state.first_chunk]
                if state.mid_stream == "break_after_first":
                    await asyncio.sleep(0.05)
                    raise RuntimeError("synthesis crashed")
                if state.delay:
                    await asyncio.sleep(state.delay)
                rest = pcm[state.first_chunk:]
                for i in range(0, len(rest), state.chunk):
                    yield rest[i: i + state.chunk]
                    await asyncio.sleep(0.001)
                state.finished = True
            except (asyncio.CancelledError, GeneratorExit):
                state.cancelled = True
                raise

        headers = {"X-Sample-Rate": str(rate)} if kind == "vieneu" else {}
        return StreamingResponse(gen(), media_type="audio/pcm", headers=headers)

    return app


@pytest.fixture
def vieneu():
    state = FakeState()
    srv = Served(fake_backend(state, "vieneu"))
    state.url = srv.url
    yield state
    srv.stop()


@pytest.fixture
def kokoro():
    state = FakeState()
    srv = Served(fake_backend(state, "kokoro"))
    state.url = srv.url
    yield state
    srv.stop()


@pytest.fixture
def make_router():
    servers = []

    def _make(vieneu_url: str, kokoro_url: str = "", **kw) -> str:
        settings = Settings(vieneu_url=vieneu_url, kokoro_url=kokoro_url, voice="Hải Đăng",
                            health_interval=3600, sentence_gap_ms=0, **kw)
        app = create_app(settings)
        srv = Served(app)
        servers.append(srv)
        _make.routers[srv.url] = app.state.router
        return srv.url

    _make.routers = {}
    yield _make
    for srv in servers:
        srv.stop()


def speak(url: str, **body) -> httpx.Response:
    body.setdefault("response_format", "pcm")
    return httpx.post(url + "/v1/audio/speech", json=body, timeout=20)


# --- basic streaming ------------------------------------------------------------------------

def test_streams_pcm_at_requested_rate(vieneu, make_router):
    url = make_router(vieneu.url)
    r = speak(url, input="Xin chào thưa ngài.", sample_rate=24000)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/pcm")
    assert r.headers["x-sample-rate"] == "24000"
    assert r.headers["x-tts-route"] == "vieneu"
    assert len(r.content) == len(tone(24000, vieneu.seconds))
    req = vieneu.requests[-1]
    assert req["sample_rate"] == 24000 and req["response_format"] == "pcm"
    assert req["voice"] == "Hải Đăng"
    assert req["input"] == "Xin chào thưa ngài."


def test_default_rate_is_24000(vieneu, make_router):
    url = make_router(vieneu.url)
    r = speak(url, input="Vâng.")
    assert r.status_code == 200 and r.headers["x-sample-rate"] == "24000"
    assert vieneu.requests[-1]["sample_rate"] == 24000


def test_first_chunk_is_forwarded_before_backend_finishes(vieneu, make_router):
    vieneu.delay = 0.8
    url = make_router(vieneu.url)
    t0 = time.perf_counter()
    first = None
    total = 0
    with httpx.stream("POST", url + "/v1/audio/speech", timeout=20,
                      json={"input": "Đã kích hoạt combat mode, thưa ngài.", "response_format": "pcm"}) as r:
        assert r.status_code == 200
        for chunk in r.iter_raw():
            if first is None and chunk:
                first = time.perf_counter() - t0
            total += len(chunk)
    elapsed = time.perf_counter() - t0
    assert first is not None and first < 0.5, first
    assert elapsed >= 0.8
    assert total == len(tone(24000, vieneu.seconds))


def test_text_is_normalized(vieneu, make_router):
    url = make_router(vieneu.url)
    r = speak(url, input="**Pin** còn 85% và 5,02 V lúc 14:32 😀")
    assert r.status_code == 200
    expected = "Pin còn tám mươi lăm phần trăm và năm phẩy không hai vôn lúc mười bốn giờ ba mươi hai phút"
    assert vieneu.requests[-1]["input"] == expected
    assert urllib.parse.unquote(r.headers["x-tts-normalized"]) == expected


def test_odd_sized_backend_chunks_stay_sample_aligned(vieneu, make_router):
    vieneu.first_chunk, vieneu.chunk = 1001, 777
    url = make_router(vieneu.url)
    r = speak(url, input="Một hai ba.")
    assert len(r.content) == len(tone(24000, vieneu.seconds))
    assert len(r.content) % 2 == 0


# --- resampling, formats ------------------------------------------------------------------

def test_resamples_when_backend_rate_differs(vieneu, make_router):
    vieneu.rate_override = 48000  # backend ignores the requested rate
    url = make_router(vieneu.url)
    r = speak(url, input="Xin chào.", sample_rate=24000)
    assert r.status_code == 200
    expected = len(tone(24000, vieneu.seconds))
    assert abs(len(r.content) - expected) <= 200


def test_rate_not_supported_by_vieneu_is_resampled(vieneu, make_router):
    url = make_router(vieneu.url)
    r = speak(url, input="Xin chào.", sample_rate=12000)
    assert vieneu.requests[-1]["sample_rate"] == 48000
    assert r.headers["x-sample-rate"] == "12000"
    assert abs(len(r.content) - len(tone(12000, vieneu.seconds))) <= 200


def test_wav_streaming_header(vieneu, make_router):
    url = make_router(vieneu.url)
    r = speak(url, input="Xin chào.", response_format="wav")
    assert r.headers["content-type"].startswith("audio/wav")
    head = r.content[:44]
    assert head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    assert struct.unpack("<I", head[40:44])[0] == 0xFFFFFFFF
    assert len(r.content) - 44 == len(tone(24000, vieneu.seconds))


def test_wav_non_streaming_has_exact_sizes(vieneu, make_router):
    url = make_router(vieneu.url)
    r = speak(url, input="Xin chào.", response_format="wav", stream=False, sample_rate=16000)
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
        assert w.getnframes() == len(tone(16000, vieneu.seconds)) // 2


# --- language routing -----------------------------------------------------------------------

def test_english_stays_on_vieneu_by_default(vieneu, kokoro, make_router):
    url = make_router(vieneu.url, kokoro.url)  # TTS_ENGLISH_ENGINE defaults to vieneu
    r = speak(url, input="Xin chào. Good evening, sir. All systems are online.")
    assert r.status_code == 200 and r.headers["x-tts-route"] == "vieneu"
    assert len(vieneu.requests) == 1 and not kokoro.requests
    assert vieneu.requests[0]["input"] == "Xin chào. Good evening, sir. All systems are online."


def test_english_sentences_go_to_kokoro_when_enabled(vieneu, kokoro, make_router):
    url = make_router(vieneu.url, kokoro.url, english_engine="kokoro", speed=1.05)
    r = speak(url, input="Xin chào. Good evening, sir. All systems are online. Pin ổn định.")
    assert r.status_code == 200
    assert r.headers["x-tts-route"] == "vieneu+kokoro+vieneu"
    assert [q["input"] for q in vieneu.requests] == ["Xin chào.", "Pin ổn định."]
    assert len(kokoro.requests) == 1
    kq = kokoro.requests[0]
    assert kq["input"] == "Good evening, sir. All systems are online."
    assert kq["voice"] == "bm_george" and kq["response_format"] == "pcm" and kq["speed"] == 1.05
    expected = 2 * len(tone(24000, vieneu.seconds)) + len(tone(24000, kokoro.seconds))
    assert len(r.content) == expected


def test_mixed_sentence_stays_on_vieneu(vieneu, kokoro, make_router):
    url = make_router(vieneu.url, kokoro.url, english_engine="kokoro")
    r = speak(url, input="Đã kích hoạt combat mode, thưa ngài.")
    assert r.headers["x-tts-route"] == "vieneu" and not kokoro.requests


def test_kokoro_down_at_planning_uses_vieneu(vieneu, kokoro, make_router):
    kokoro.down = True
    url = make_router(vieneu.url, kokoro.url, english_engine="kokoro")
    r = speak(url, input="Good evening, sir.")
    assert r.status_code == 200 and r.headers["x-tts-route"] == "vieneu"
    assert not kokoro.requests
    health = httpx.get(url + "/health").json()
    assert health["status"] == "degraded" and health["backends"]["kokoro"]["healthy"] is False


def test_kokoro_request_failure_falls_back_to_vieneu(vieneu, kokoro, make_router):
    url = make_router(vieneu.url, kokoro.url, english_engine="kokoro")
    kokoro.fail_status = 500  # healthy at start-up, fails on synthesis
    r = speak(url, input="Good evening, sir.")
    assert r.status_code == 200 and r.headers["x-tts-route"] == "vieneu"
    assert len(kokoro.requests) == 1 and len(vieneu.requests) == 1
    assert vieneu.requests[0]["voice"] == "Hải Đăng"


@pytest.mark.parametrize("mode", ["break", "empty"])
def test_kokoro_failing_inside_synthesis_falls_back_to_vieneu(vieneu, kokoro, make_router, mode):
    """audio-services-5: 200 + no audio (OOM on the shared GPU) while /health stays 200."""
    url = make_router(vieneu.url, kokoro.url, english_engine="kokoro")
    kokoro.mid_stream = mode
    r = speak(url, input="Good evening, sir. All systems are online.", sample_rate=24000)
    assert r.status_code == 200
    assert len(kokoro.requests) == 1 and len(vieneu.requests) == 1
    assert len(r.content) == len(tone(24000, vieneu.seconds))  # the sentence was still spoken
    assert make_router.routers[url].kokoro.healthy is False


def test_kokoro_breaking_after_audio_is_not_repeated(vieneu, kokoro, make_router):
    url = make_router(vieneu.url, kokoro.url, english_engine="kokoro")
    kokoro.mid_stream = "break_after_first"
    r = speak(url, input="Good evening, sir. All systems are online.", sample_rate=24000)
    assert r.status_code == 200
    assert len(r.content) == kokoro.first_chunk  # heard part of it: no second voice on top
    assert len(kokoro.requests) == 1 and not vieneu.requests
    assert make_router.routers[url].kokoro.healthy is False


def test_english_sentence_with_colon_stays_english(vieneu, kokoro, make_router):
    """audio-services-4: no voice switch and no Vietnamese number inside an English reply."""
    url = make_router(vieneu.url, kokoro.url, english_engine="kokoro")
    r = speak(url, input="Sir, the battery level is: 85%.")
    assert r.headers["x-tts-route"] == "kokoro" and not vieneu.requests
    assert kokoro.requests[0]["input"] == "Sir, the battery level is: eighty five percent."
    r = speak(url, input="All systems are online, sir. 42.")
    assert r.headers["x-tts-route"] == "kokoro"  # a fragment without letters keeps the language
    assert kokoro.requests[1]["input"].endswith("forty two.")


def test_language_hint_forces_vietnamese(vieneu, kokoro, make_router):
    url = make_router(vieneu.url, kokoro.url, english_engine="kokoro")
    speak(url, input="Good evening, sir.", language="vi")
    assert not kokoro.requests and len(vieneu.requests) == 1


def test_speed_is_reported_as_ignored_for_vieneu(vieneu, make_router):
    url = make_router(vieneu.url)
    r = speak(url, input="Xin chào.", speed=1.2)
    assert r.headers.get("x-tts-ignored") == "speed"
    assert "speed" not in vieneu.requests[-1]


# --- errors -----------------------------------------------------------------------------------

def test_backend_error_maps_to_502(vieneu, make_router):
    vieneu.fail_status = 500
    url = make_router(vieneu.url)
    r = speak(url, input="Xin chào.")
    assert r.status_code == 502
    assert "error" in r.json()


def test_backend_busy_maps_to_429(vieneu, make_router):
    vieneu.fail_status = 429
    url = make_router(vieneu.url)
    r = speak(url, input="Xin chào.")
    assert r.status_code == 429


def test_backend_unreachable_maps_to_503(make_router):
    url = make_router("http://127.0.0.1:9")  # nothing listens there
    r = speak(url, input="Xin chào.")
    assert r.status_code == 503
    assert httpx.get(url + "/health").status_code == 503


def test_unknown_voice_falls_back_to_configured_voice(vieneu, make_router):
    url = make_router(vieneu.url)
    r = speak(url, input="Xin chào.", voice="Không Tồn Tại")
    assert r.status_code == 200
    assert [q.get("voice") for q in vieneu.requests] == ["Không Tồn Tại", "Hải Đăng"]


def test_voice_name_is_nfc_normalized(vieneu, make_router):
    import unicodedata
    url = make_router(vieneu.url)
    r = speak(url, input="Xin chào.", voice=unicodedata.normalize("NFD", "Thiện Minh"))
    assert r.status_code == 200 and vieneu.requests[-1]["voice"] == "Thiện Minh"


@pytest.mark.parametrize("body,status", [
    ({"input": "Xin chào.", "response_format": "mp3"}, 400),
    ({"input": "Xin chào.", "sample_rate": 100}, 400),
    ({"input": ""}, 400),
    ({"voice": "x"}, 400),
])
def test_validation_errors(vieneu, make_router, body, status):
    url = make_router(vieneu.url)
    r = httpx.post(url + "/v1/audio/speech", json=body, timeout=10)
    assert r.status_code == status
    assert "error" in r.json()
    assert not vieneu.requests


def test_nothing_speakable_returns_empty_audio(vieneu, make_router):
    url = make_router(vieneu.url)
    r = speak(url, input="😀🎉")
    assert r.status_code == 200 and r.content == b"" and r.headers["x-tts-route"] == "none"
    assert not vieneu.requests


# --- health, voices, abort --------------------------------------------------------------------

def test_health_reports_backends(vieneu, make_router):
    url = make_router(vieneu.url)
    r = httpx.get(url + "/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["backends"]["vieneu"]["healthy"] is True
    assert body["backends"]["kokoro"]["enabled"] is False
    vieneu.down = True
    r = httpx.get(url + "/health")
    assert r.status_code == 503 and r.json()["status"] == "error"
    assert httpx.get(url + "/livez").status_code == 200


def test_voices_lists_both_engines(vieneu, kokoro, make_router):
    url = make_router(vieneu.url, kokoro.url, english_engine="kokoro")
    body = httpx.get(url + "/v1/voices").json()
    ids = {(v["engine"], v["id"]) for v in body["data"]}
    assert ("vieneu", "Hải Đăng") in ids and ("kokoro", "bm_george") in ids
    male_vieneu = [v["id"] for v in body["data"] if v["engine"] == "vieneu" and v["gender"] == "male"]
    assert male_vieneu == ["Hải Đăng", "Thiện Minh"]
    assert body["default_voice"] == "Hải Đăng" and body["english_voice"] == "bm_george"


def test_client_disconnect_stops_backend_stream(vieneu, make_router):
    vieneu.delay, vieneu.seconds = 3.0, 1.0
    url = make_router(vieneu.url)
    with httpx.stream("POST", url + "/v1/audio/speech", timeout=20,
                      json={"input": "Một câu rất dài.", "response_format": "pcm"}) as r:
        for chunk in r.iter_raw():
            if chunk:
                break  # got the first audio, now hang up like an aborted turn
    deadline = time.time() + 2.5
    while not vieneu.cancelled and time.time() < deadline:
        time.sleep(0.05)
    assert vieneu.cancelled and not vieneu.finished
