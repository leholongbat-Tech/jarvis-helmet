"""Dataset capture tests (ASR_SAVE_DIR): audio bytes + one JSONL record per request."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
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
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from jarvis_asr.audio import to_wav_bytes  # noqa: E402
from jarvis_asr.capture import DatasetCapture  # noqa: E402
from jarvis_asr.config import Settings  # noqa: E402
from jarvis_asr.engines import FakeEngine  # noqa: E402
from jarvis_asr.gate import SpeechGate  # noqa: E402
from jarvis_asr.service import AsrService  # noqa: E402

SR = 16000
URL = "/v1/audio/transcriptions"


@pytest.fixture(scope="module")
def app_module():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ASR_ENGINE", "fake")
        spec = importlib.util.spec_from_file_location("jarvis_asr_app_capture", ASR_DIR / "app.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def tone(seconds=1.5, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    return (0.3 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)


def build(app_module, save_dir, text="mở mặt nạ", **kw):
    settings = Settings(engine="fake", warmup=0, save_dir=str(save_dir), initial_prompt="Jarvis, mặt nạ", **kw)
    svc = AsrService(settings, engine=FakeEngine(settings, text=text),
                     gate=SpeechGate(min_rms_dbfs=-60.0, vad_enabled=False))
    return app_module.create_app(service=svc), svc


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_capture_writes_exact_audio_and_full_record(app_module, tmp_path):
    app, svc = build(app_module, tmp_path / "ds")
    sid = str(uuid.uuid4())
    name = f"asr_base_{sid}_{uuid.uuid4()}.wav"
    upload = to_wav_bytes(tone())
    with TestClient(app) as client:
        assert client.get("/health").json()["capture"] is True
        resp = client.post(URL, files={"file": (name, upload, "audio/wav")}, data={"model": "whisper-large-v3"})
    assert resp.status_code == 200
    records = read_jsonl(tmp_path / "ds" / "transcripts.jsonl")
    assert len(records) == 1
    rec = records[0]
    assert (tmp_path / "ds" / rec["audio"]).read_bytes() == upload
    assert rec["audio"].startswith("audio/") and rec["audio"].endswith(".wav")
    assert rec["text"] == "mở mặt nạ" and rec["raw_text"] == "mở mặt nạ" and rec["dropped"] is None
    assert rec["sid"] == sid and rec["client_filename"] == name and rec["client_model"] == "whisper-large-v3"
    assert rec["duration_s"] == pytest.approx(1.5)
    assert rec["input"]["sample_rate"] == 16000 and rec["input"]["bytes"] == len(upload)
    assert {"decode", "gate", "infer", "total"} <= set(rec["timing_ms"])
    assert rec["engine"]["engine"] == "fake"
    # max_new_tokens None = engine default (length-scaled cap, ASR_MAX_TOKENS_PER_S)
    assert rec["options"] == {"language": "vi", "initial_prompt": "Jarvis, mặt nạ", "hotwords": None,
                              "beam_size": 5, "max_new_tokens": None}
    assert rec["segments"][0]["avg_logprob"] == pytest.approx(-0.15)
    assert rec["ts"] and rec["rid"]


def test_dropped_results_are_captured_with_reason(app_module, tmp_path):
    app, _ = build(app_module, tmp_path / "ds", text="Thanks for watching!")
    with TestClient(app) as client:
        assert client.post(URL, files={"file": ("a.wav", to_wav_bytes(tone()), "audio/wav")}).json() == {"text": ""}
        silence = to_wav_bytes(np.zeros(3 * SR, dtype=np.float32))
        assert client.post(URL, files={"file": ("b.wav", silence, "audio/wav")}).json() == {"text": ""}
    recs = read_jsonl(tmp_path / "ds" / "transcripts.jsonl")
    assert [r["dropped"] for r in recs] == ["blocklist", "gate:silence"]
    assert recs[0]["raw_text"] == "Thanks for watching!"
    assert all((tmp_path / "ds" / r["audio"]).exists() for r in recs)


def test_non_wav_upload_keeps_its_extension(app_module, tmp_path):
    app, _ = build(app_module, tmp_path / "ds")
    buf = io.BytesIO()
    sf.write(buf, tone(), SR, format="FLAC")
    with TestClient(app) as client:
        assert client.post(URL, files={"file": ("a.flac", buf.getvalue(), "audio/flac")}).status_code == 200
    rec = read_jsonl(tmp_path / "ds" / "transcripts.jsonl")[0]
    assert rec["audio"].endswith(".flac") and rec["input"]["format"] == "FLAC"


def test_unwritable_capture_dir_does_not_break_recognition(app_module, tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    app, svc = build(app_module, blocker / "ds")
    with TestClient(app) as client:
        assert client.get("/health").json()["capture"] is False
        resp = client.post(URL, files={"file": ("a.wav", to_wav_bytes(tone()), "audio/wav")})
    assert resp.status_code == 200 and resp.json() == {"text": "mở mặt nạ"}
    assert svc.capture is None


def test_capture_save_never_raises(tmp_path):
    cap = DatasetCapture(str(tmp_path / "ds"))
    rel = cap.save("abc123", b"RIFF....", "wav", {"text": "x", "weird": object()})
    assert rel is not None and (tmp_path / "ds" / rel).exists()
    (tmp_path / "ds" / "transcripts.jsonl").unlink()
    (tmp_path / "ds" / "transcripts.jsonl").mkdir()  # make the JSONL path unusable
    assert cap.save("def456", b"x", "wav", {"text": "y"}) is None
