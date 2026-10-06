"""API tests for server/asr with a fake engine (no GPU, no model download)."""

from __future__ import annotations

import importlib.util
import io
import logging
import sys
import threading
import time
import uuid
import warnings
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

ASR_DIR = Path(__file__).resolve().parents[1] / "asr"
if str(ASR_DIR) not in sys.path:
    sys.path.insert(0, str(ASR_DIR))

with warnings.catch_warnings():
    warnings.simplefilter("ignore")  # starlette's "use httpx2" notice
    from fastapi.testclient import TestClient

from jarvis_asr.audio import to_wav_bytes  # noqa: E402
from jarvis_asr.config import Settings  # noqa: E402
from jarvis_asr.engines import (  # noqa: E402
    EngineError,
    EngineUnavailable,
    FakeEngine,
    Segment,
    Transcription,
)
from jarvis_asr.gate import SpeechGate  # noqa: E402
from jarvis_asr.service import AsrService, session_id_from_filename  # noqa: E402

SR = 16000
URL = "/v1/audio/transcriptions"


@pytest.fixture(scope="module")
def app_module():
    """Load server/asr/app.py under a unique module name (no clash with other services' app.py)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ASR_ENGINE", "fake")
        spec = importlib.util.spec_from_file_location("jarvis_asr_app", ASR_DIR / "app.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def tone(seconds: float = 2.0, freq: float = 220.0, amp: float = 0.3, sr: int = SR) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def wav(samples: np.ndarray, sr: int = SR) -> bytes:
    return to_wav_bytes(samples, sr)


def build(app_module, engine=None, gate=None, text="mở mặt nạ", **settings_kw):
    settings_kw.setdefault("warmup", 0)
    settings = Settings(engine="fake", **settings_kw)
    engine = engine or FakeEngine(settings, text=text)
    gate = gate or SpeechGate(min_rms_dbfs=-60.0, vad_enabled=False)
    svc = AsrService(settings, engine=engine, gate=gate)
    return app_module.create_app(service=svc), svc, engine


def post(client, data: bytes, filename: str = "audio.wav", **form):
    return client.post(URL, files={"file": (filename, data, "audio/wav")}, data=form)


def test_health_reports_engine_model_device(app_module):
    app, _, _ = build(app_module)
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["engine"] == "fake"
    assert body["model"] == "large-v3"
    assert body["device"] == "cpu"
    assert body["language"] == "vi"
    assert body["hallucination_entries"] > 20


def test_transcribe_returns_text_and_feeds_16k_mono_float32(app_module):
    app, _, engine = build(app_module)
    with TestClient(app) as client:
        resp = post(client, wav(tone(2.0)), model="whisper-large-v3")
    assert resp.status_code == 200
    assert resp.json() == {"text": "mở mặt nạ"}
    audio = engine.calls[-1]["audio"]
    assert audio.dtype == np.float32 and audio.ndim == 1 and len(audio) == 2 * SR
    opts = engine.calls[-1]["opts"]
    assert opts.language == "vi" and opts.initial_prompt is None and opts.beam_size == 5


def test_xiaozhi_openai_asr_request_is_accepted(app_module, caplog):
    """Replays exactly what xiaozhi's OpenaiASR sends: requests multipart, bare file object."""
    requests = pytest.importorskip("requests")
    sid = str(uuid.uuid4())
    name = f"asr_base_{sid}_{uuid.uuid4()}.wav"
    fileobj = io.BytesIO(wav(tone(1.5)))
    fileobj.name = name  # requests derives the part filename from .name, sends no part Content-Type
    prepared = requests.Request(
        "POST",
        "http://asr:9000" + URL,
        files={"file": fileobj},
        data={"model": "whisper-large-v3"},
        headers={"Authorization": "Bearer none"},
    ).prepare()
    assert b"Content-Type: audio" not in prepared.body
    app, _, _ = build(app_module)
    caplog.set_level(logging.INFO, logger="jarvis_asr")
    with TestClient(app) as client:
        resp = client.post(URL, content=prepared.body, headers=dict(prepared.headers))
    assert resp.status_code == 200
    assert resp.json()["text"] == "mở mặt nạ"
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("[ASR] rid=")]
    assert lines, "per-request timing line missing"
    line = lines[-1]
    assert f"sid={sid}" in line
    for key in ("audio_ms=1500", "decode_ms=", "gate_ms=", "infer_ms=", "total_ms=", "rtf=", "lang=vi"):
        assert key in line


def test_session_id_parsing():
    sid = str(uuid.uuid4())
    assert session_id_from_filename(f"tmp/asr_base_{sid}_{uuid.uuid4()}.wav") == sid
    assert session_id_from_filename("audio.wav") is None
    assert session_id_from_filename(None) is None


@pytest.mark.parametrize(
    "form, expected_lang, expected_prompt",
    [
        ({}, "vi", "env prompt"),
        ({"language": "en"}, "en", "env prompt"),
        ({"language": "auto"}, None, "env prompt"),
        ({"language": "VI, EN"}, "vi,en", "env prompt"),
        ({"prompt": "none"}, "vi", None),
        ({"prompt": "Jarvis, mặt nạ"}, "vi", "Jarvis, mặt nạ"),
        ({"prompt": ""}, "vi", "env prompt"),  # empty form field = not provided
    ],
)
def test_language_and_prompt_overrides(app_module, form, expected_lang, expected_prompt):
    app, _, engine = build(app_module, initial_prompt="env prompt")
    with TestClient(app) as client:
        resp = post(client, wav(tone()), **form)
    assert resp.status_code == 200, resp.text
    opts = engine.calls[-1]["opts"]
    assert opts.language == expected_lang
    assert opts.initial_prompt == expected_prompt


def test_invalid_language_is_400(app_module):
    app, _, engine = build(app_module)
    with TestClient(app) as client:
        resp = post(client, wav(tone()), language="klingon")
    assert resp.status_code == 400
    assert "language" in resp.json()["error"]["message"]
    assert engine.calls == []


def test_response_formats(app_module):
    app, _, _ = build(app_module)
    with TestClient(app) as client:
        text = post(client, wav(tone()), response_format="text")
        verbose = post(client, wav(tone()), response_format="verbose_json")
        bad = post(client, wav(tone()), response_format="srt")
    assert text.status_code == 200 and text.text == "mở mặt nạ"
    body = verbose.json()
    assert body["text"] == "mở mặt nạ" and body["language"] == "vi"
    assert body["segments"][0]["avg_logprob"] == pytest.approx(-0.15)
    jarvis = body["jarvis"]
    assert jarvis["dropped"] is None and jarvis["gate"]["passed"] is True
    assert set(jarvis["timing_ms"]) == {"decode", "gate", "wait", "infer", "filter", "total"}
    assert jarvis["engine"]["engine"] == "fake"
    assert bad.status_code == 400


def test_undecodable_upload_is_400(app_module):
    app, _, engine = build(app_module)
    with TestClient(app) as client:
        garbage = post(client, b"this is not audio at all" * 10)
        empty = post(client, b"")
    assert garbage.status_code == 400
    assert empty.status_code == 400
    assert engine.calls == []


def test_zero_length_wav_returns_empty_text(app_module):
    app, _, engine = build(app_module)
    with TestClient(app) as client:
        resp = post(client, wav(np.zeros(0, dtype=np.float32)))
    assert resp.status_code == 200 and resp.json() == {"text": ""}
    assert engine.calls == []


def test_30s_silence_is_empty_and_never_decoded(app_module):
    app, svc, engine = build(app_module)
    with TestClient(app) as client:
        resp = post(client, wav(np.zeros(30 * SR, dtype=np.float32)), response_format="verbose_json")
    assert resp.status_code == 200
    assert resp.json()["text"] == ""
    assert resp.json()["jarvis"]["dropped"] == "gate:silence"
    assert engine.calls == []
    assert svc.stats["dropped"] == 1


def test_30s_noise_is_empty_when_vad_finds_no_speech(app_module):
    gate = SpeechGate(min_rms_dbfs=-60.0, vad_enabled=True, min_speech_ms=250, vad_fn=lambda x: 0.0)
    app, _, engine = build(app_module, gate=gate)
    noise = np.random.default_rng(0).normal(0, 0.1, 30 * SR).astype(np.float32)
    with TestClient(app) as client:
        resp = post(client, wav(noise), response_format="verbose_json")
    assert resp.json()["text"] == ""
    assert resp.json()["jarvis"]["dropped"] == "gate:no_speech"
    assert engine.calls == []


def test_hallucinated_text_from_engine_is_dropped(app_module):
    app, _, engine = build(
        app_module, text="Hãy subscribe cho kênh Ghiền Mì Gõ Để không bỏ lỡ những video hấp dẫn"
    )
    with TestClient(app) as client:
        resp = post(client, wav(tone()), response_format="verbose_json")
    body = resp.json()
    assert body["text"] == ""
    assert body["jarvis"]["dropped"] == "blocklist"
    assert body["jarvis"]["raw_text"].startswith("Hãy subscribe")
    assert len(engine.calls) == 1


def test_low_confidence_segment_is_dropped(app_module):
    def responder(audio, opts):
        return Transcription([Segment(" Cảm ơn.", avg_logprob=-1.6, no_speech_prob=0.3)], "vi", 1.0)

    app, _, _ = build(app_module, engine=FakeEngine(responder=responder))
    with TestClient(app) as client:
        resp = post(client, wav(tone()), response_format="verbose_json")
    assert resp.json()["text"] == ""
    assert resp.json()["jarvis"]["dropped"] == "low_logprob"


def test_engine_errors_map_to_http_codes(app_module):
    def broken(audio, opts):
        raise EngineError("CUDA out of memory")

    def unreachable(audio, opts):
        raise EngineUnavailable("vLLM down")

    app, svc, _ = build(app_module, engine=FakeEngine(responder=broken))
    with TestClient(app) as client:
        resp = post(client, wav(tone()))
    assert resp.status_code == 500 and "CUDA" in resp.json()["error"]["message"]
    assert svc.stats["errors"] == 1
    app, _, _ = build(app_module, engine=FakeEngine(responder=unreachable))
    with TestClient(app) as client:
        assert post(client, wav(tone())).status_code == 503


def test_not_ready_returns_503(app_module):
    app, _, _ = build(app_module)
    client = TestClient(app)  # no context manager -> lifespan (model load) never ran
    assert post(client, wav(tone())).status_code == 503
    assert client.get("/health").status_code == 503


def test_requests_are_serialized_on_the_engine(app_module):
    active, peak, lock = [0], [0], threading.Lock()

    def slow(audio, opts):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.15)
        with lock:
            active[0] -= 1
        return Transcription([Segment(" xin chào", -0.1, 0.01, 1.0)], "vi", 1.0)

    app, _, _ = build(app_module, engine=FakeEngine(responder=slow))
    with TestClient(app) as client:
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(post(client, wav(tone())).status_code))
            for _ in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert results == [200, 200, 200]
    assert peak[0] == 1


def test_stuck_engine_times_out_with_504(app_module):
    def stuck(audio, opts):
        time.sleep(1.0)
        return Transcription([Segment(" late")], "vi", 1.0)

    app, _, _ = build(app_module, engine=FakeEngine(responder=stuck), request_timeout_s=0.2)
    with TestClient(app) as client:
        started = time.perf_counter()
        resp = post(client, wav(tone()))
        elapsed = time.perf_counter() - started
    assert resp.status_code == 504
    assert elapsed < 0.9


def test_resamples_48k_stereo_upload(app_module):
    app, _, engine = build(app_module)
    stereo = np.stack([tone(1.0, sr=48000), tone(1.0, sr=48000)], axis=1)
    buf = io.BytesIO()
    sf.write(buf, stereo, 48000, format="WAV", subtype="FLOAT")
    with TestClient(app) as client:
        assert post(client, buf.getvalue()).status_code == 200
    audio = engine.calls[-1]["audio"]
    assert audio.ndim == 1 and len(audio) == SR


def test_startup_loads_and_warms_up_engine(app_module):
    app, svc, engine = build(app_module, warmup=2)
    with TestClient(app) as client:
        assert client.get("/health").json()["warmup_ms"] is not None
    assert engine.loaded is True
    assert len(engine.calls) == 2  # warm-up runs on the generated sample
    assert len(engine.calls[0]["audio"]) == 2 * SR


def test_upload_size_limit(app_module):
    app, _, engine = build(app_module, max_upload_mb=0.1)
    with TestClient(app) as client:
        resp = post(client, wav(tone(5.0)))
    assert resp.status_code == 413
    assert engine.calls == []


def test_long_audio_is_truncated(app_module, caplog):
    app, _, engine = build(app_module, max_audio_s=2.0)
    caplog.set_level(logging.INFO, logger="jarvis_asr")
    with TestClient(app) as client:
        assert post(client, wav(tone(5.0))).status_code == 200
    assert len(engine.calls[-1]["audio"]) == 2 * SR
    assert any("truncated=1" in r.getMessage() for r in caplog.records)
