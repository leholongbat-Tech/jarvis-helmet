"""Engine, speech gate and configuration tests (no GPU, no model download, no network)."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
import soundfile as sf

ASR_DIR = Path(__file__).resolve().parents[1] / "asr"
if str(ASR_DIR) not in sys.path:
    sys.path.insert(0, str(ASR_DIR))

from jarvis_asr.config import ConfigError, Settings, parse_language, parse_prompt  # noqa: E402
from jarvis_asr.engines import (  # noqa: E402
    EngineError,
    EngineUnavailable,
    FakeEngine,
    TranscribeOptions,
    create_engine,
)
from jarvis_asr.engines.faster_whisper_engine import FasterWhisperEngine, resolve_model  # noqa: E402
from jarvis_asr.engines.qwen3_vllm import Qwen3VllmEngine, _endpoints  # noqa: E402
from jarvis_asr.gate import SpeechGate  # noqa: E402

SR = 16000
LARGE_V3_REV = "edaa852ec7e145841d8ffdb056a99866b5f0a478"


def tone(seconds=1.0, amp=0.3):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * 300 * t)).astype(np.float32)


# ------------------------------------------------------------------ config
def test_env_defaults_match_contract():
    s = Settings.from_env({})
    assert (s.engine, s.model, s.device, s.language, s.beam_size, s.warmup) == (
        "faster_whisper", "large-v3", "cuda", "vi", 5, 1)
    assert s.save_dir is None and s.initial_prompt is None and s.vllm_url is None
    assert s.compute_type_for("cuda") == "float16" and s.compute_type_for("cpu") == "int8"


def test_env_parsing():
    s = Settings.from_env({
        "ASR_ENGINE": "QWEN3_VLLM", "ASR_VLLM_URL": "http://asr-qwen:8000", "ASR_MODEL": "Qwen/Qwen3-ASR-1.7B",
        "ASR_LANGUAGE": "VI, EN", "ASR_INITIAL_PROMPT": "  Jarvis, mặt nạ  ", "ASR_BEAM_SIZE": "3",
        "ASR_COMPUTE_TYPE": "int8_float16", "ASR_SAVE_DIR": "", "ASR_WARMUP": "false",
        "ASR_FILTER_LOGPROB_MIN": "off", "ASR_MIN_RMS_DBFS": "-55", "ASR_VAD_GATE": "no",
        "ASR_FILTER_DROP_CJK": "0",
    })
    assert s.engine == "qwen3_vllm" and s.language == "vi,en" and s.initial_prompt == "Jarvis, mặt nạ"
    assert s.beam_size == 3 and s.compute_type_for("cuda") == "int8_float16"
    assert s.save_dir is None and s.warmup == 0 and s.min_rms_dbfs == -55.0 and s.vad_gate is False
    assert s.filter.logprob_min is None and s.filter.drop_cjk is False


@pytest.mark.parametrize(
    "env",
    [{"ASR_ENGINE": "kaldi"}, {"ASR_DEVICE": "tpu"}, {"ASR_LANGUAGE": "xx"}, {"ASR_BEAM_SIZE": "0"},
     {"ASR_VAD_GATE": "maybe"}, {"ASR_ENGINE": "qwen3_vllm"}, {"ASR_VAD_THRESHOLD": "2"}],
)
def test_invalid_env_is_rejected(env):
    with pytest.raises(ConfigError):
        Settings.from_env(env)


def test_language_and_prompt_helpers():
    assert parse_language("auto") is None and parse_language("") is None and parse_language(None) is None
    assert parse_language(" EN ") == "en" and parse_language("vi,en,vi") == "vi,en"
    with pytest.raises(ConfigError):
        parse_language("english")
    assert parse_prompt("none") is None and parse_prompt(" - ") is None and parse_prompt("") is None
    assert parse_prompt(" x ") == "x"


# ------------------------------------------------------------------ model resolution
@pytest.mark.parametrize(
    "model, revision, expected",
    [
        ("large-v3", None, ("Systran/faster-whisper-large-v3", LARGE_V3_REV)),
        ("Systran/faster-whisper-large-v3", None, ("Systran/faster-whisper-large-v3", LARGE_V3_REV)),
        ("large-v3", "main", ("Systran/faster-whisper-large-v3", "main")),
        ("large-v3-turbo", None, ("dropbox-dash/faster-whisper-large-v3-turbo",
                                  "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf")),
        ("phowhisper-large", None, ("qbsmlabs/PhoWhisper-large", "3893cf5c791a168d708e730a2e2b81afc42c1e5b")),
        ("qbsmlabs/PhoWhisper-large", None, ("qbsmlabs/PhoWhisper-large",
                                             "3893cf5c791a168d708e730a2e2b81afc42c1e5b")),
        ("small", None, ("small", None)),
        ("org/custom-ct2", "abc", ("org/custom-ct2", "abc")),
    ],
)
def test_resolve_model(model, revision, expected):
    assert resolve_model(model, revision) == expected


def test_local_model_dir_has_no_revision(tmp_path):
    assert resolve_model(str(tmp_path), "ignored") == (str(tmp_path), None)


# ------------------------------------------------------------------ faster-whisper engine
class FakeWhisperModel:
    """Stands in for faster_whisper.WhisperModel and records how it is used."""

    instances = []

    def __init__(self, model, **kwargs):
        self.model, self.kwargs = model, kwargs
        self.transcribe_kwargs = None
        self.detect_calls = 0
        FakeWhisperModel.instances.append(self)

    def detect_language(self, audio=None, **kwargs):
        self.detect_calls += 1
        return "zh", 0.5, [("zh", 0.5), ("en", 0.3), ("vi", 0.2)]

    def transcribe(self, audio, **kwargs):
        self.transcribe_kwargs = kwargs

        def gen():
            yield SimpleNamespace(text=" Mở mặt nạ.", avg_logprob=-0.21, no_speech_prob=0.03,
                                  compression_ratio=1.1, start=0.0, end=1.0)

        lang = kwargs.get("language") or "vi"
        return gen(), SimpleNamespace(language=lang, language_probability=0.97)


def make_fw(**kw):
    settings = Settings(engine="faster_whisper", **kw)
    return FasterWhisperEngine(settings, model_factory=FakeWhisperModel)


def test_faster_whisper_load_arguments():
    eng = make_fw(model="large-v3", device="cuda")
    eng.load()
    model = FakeWhisperModel.instances[-1]
    assert model.model == "Systran/faster-whisper-large-v3"
    assert model.kwargs["revision"] == LARGE_V3_REV
    assert model.kwargs["device"] == "cuda" and model.kwargs["compute_type"] == "float16"
    info = eng.info()
    assert info["engine"] == "faster_whisper" and info["revision"] == LARGE_V3_REV
    assert info["device"] == "cuda" and info["compute_type"] == "float16"


def test_faster_whisper_cpu_defaults_to_int8():
    eng = make_fw(model="small", device="cpu")
    eng.load()
    assert FakeWhisperModel.instances[-1].kwargs["compute_type"] == "int8"


def test_faster_whisper_transcribe_uses_contract_decoding_options():
    eng = make_fw(model="large-v3")
    eng.load()
    opts = TranscribeOptions(language="vi", initial_prompt="Jarvis, mặt nạ", hotwords=None, beam_size=5)
    result = eng.transcribe(tone(), opts)
    kw = FakeWhisperModel.instances[-1].transcribe_kwargs
    assert kw["language"] == "vi" and kw["beam_size"] == 5 and kw["temperature"] == 0.0
    assert kw["vad_filter"] is False and kw["condition_on_previous_text"] is False
    assert kw["initial_prompt"] == "Jarvis, mặt nạ" and kw["without_timestamps"] is True
    assert kw["task"] == "transcribe"
    assert result.text == "Mở mặt nạ." and result.segments[0].avg_logprob == pytest.approx(-0.21)
    assert result.language == "vi"


def test_faster_whisper_auto_language_passes_none():
    eng = make_fw()
    eng.load()
    eng.transcribe(tone(), TranscribeOptions(language=None))
    model = FakeWhisperModel.instances[-1]
    assert model.transcribe_kwargs["language"] is None and model.detect_calls == 0


def test_faster_whisper_restricted_detection_picks_best_allowed_language():
    eng = make_fw()
    eng.load()
    result = eng.transcribe(tone(), TranscribeOptions(language="vi,en"))
    model = FakeWhisperModel.instances[-1]
    assert model.detect_calls == 1
    assert model.transcribe_kwargs["language"] == "en"  # zh scored higher but is not allowed
    assert result.language_probability == pytest.approx(0.3)


def test_faster_whisper_load_failure_is_engine_error():
    def boom(*a, **k):
        raise RuntimeError("CUDA driver version is insufficient")

    eng = FasterWhisperEngine(Settings(), model_factory=boom)
    with pytest.raises(EngineError, match="CUDA driver"):
        eng.load()
    with pytest.raises(EngineError):
        eng.transcribe(tone(), TranscribeOptions(language="vi"))


# ------------------------------------------------------------------ Qwen3-ASR via vLLM
def test_vllm_endpoint_forms():
    assert _endpoints("http://asr-qwen:8000") == ("http://asr-qwen:8000/v1/audio/transcriptions",
                                                  "http://asr-qwen:8000/v1/models")
    assert _endpoints("http://h:8000/v1/") == ("http://h:8000/v1/audio/transcriptions", "http://h:8000/v1/models")
    assert _endpoints("http://h:8000/v1/audio/transcriptions")[1] == "http://h:8000/v1/models"


def make_vllm_engine(handler, model="Qwen/Qwen3-ASR-1.7B", wait=0.0):
    settings = Settings(engine="qwen3_vllm", vllm_url="http://asr-qwen:8000", model=model, vllm_wait_s=wait)
    return Qwen3VllmEngine(settings, transport=httpx.MockTransport(handler))


def models_response(*ids):
    return httpx.Response(200, json={"object": "list", "data": [{"id": i, "object": "model"} for i in ids]})


def test_vllm_request_format():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return models_response("Qwen/Qwen3-ASR-1.7B")
        seen["path"] = request.url.path
        seen["ctype"] = request.headers["content-type"]
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "Mở mặt nạ đi Jarvis."})

    eng = make_vllm_engine(handler)
    eng.load()
    result = eng.transcribe(tone(1.0), TranscribeOptions(language="vi", initial_prompt="Jarvis, mặt nạ",
                                                         hotwords="open mask"))
    assert result.text == "Mở mặt nạ đi Jarvis." and result.language == "vi"
    assert seen["path"] == "/v1/audio/transcriptions"
    body, ctype = seen["body"], seen["ctype"]
    assert ctype.startswith("multipart/form-data")
    for field, value in (("model", "Qwen/Qwen3-ASR-1.7B"), ("language", "vi"), ("temperature", "0"),
                         ("prompt", "open mask Jarvis, mặt nạ")):
        assert f'name="{field}"\r\n\r\n{value}\r\n'.encode() in body
    wav = body[body.index(b"RIFF"):]
    audio, sr = sf.read(io.BytesIO(wav[: wav.rindex(b"\r\n--")]))
    assert sr == 16000 and audio.ndim == 1 and len(audio) == SR
    assert eng.info() == {"engine": "qwen3_vllm", "model": "Qwen/Qwen3-ASR-1.7B", "device": "vllm",
                          "upstream": "http://asr-qwen:8000/v1/audio/transcriptions"}


def test_vllm_restricted_language_is_not_sent():
    bodies = []

    def handler(request):
        if request.url.path == "/v1/models":
            return models_response("Qwen/Qwen3-ASR-1.7B")
        bodies.append(request.content)
        return httpx.Response(200, json={"text": "ok"})

    eng = make_vllm_engine(handler)
    eng.load()
    eng.transcribe(tone(), TranscribeOptions(language="vi,en"))
    eng.transcribe(tone(), TranscribeOptions(language=None))
    assert all(b'name="language"' not in b for b in bodies)


def test_vllm_uses_the_only_served_model_when_name_differs():
    eng = make_vllm_engine(lambda r: models_response("qwen3-asr"), model="large-v3")
    eng.load()
    assert eng.served_model == "qwen3-asr"


def test_vllm_errors():
    eng = make_vllm_engine(lambda r: models_response("a", "b"), model="c")
    with pytest.raises(EngineError, match="not served"):
        eng.load()

    def down(request):
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(EngineUnavailable):
        make_vllm_engine(down).load()

    def failing(request):
        if request.url.path == "/v1/models":
            return models_response("Qwen/Qwen3-ASR-1.7B")
        return httpx.Response(500, text="CUDA error")

    eng = make_vllm_engine(failing)
    eng.load()
    with pytest.raises(EngineError, match="500"):
        eng.transcribe(tone(), TranscribeOptions(language="vi"))

    calls = {"n": 0}

    def flaky(request):
        if request.url.path == "/v1/models":
            return models_response("Qwen/Qwen3-ASR-1.7B")
        raise httpx.ReadTimeout("timed out", request=request)

    eng = make_vllm_engine(flaky)
    eng.load()
    with pytest.raises(EngineUnavailable):
        eng.transcribe(tone(), TranscribeOptions(language="vi"))


def test_vllm_empty_text_gives_no_segments():
    def handler(request):
        if request.url.path == "/v1/models":
            return models_response("Qwen/Qwen3-ASR-1.7B")
        return httpx.Response(200, json={"text": "  "})

    eng = make_vllm_engine(handler)
    eng.load()
    assert eng.transcribe(tone(), TranscribeOptions(language="vi")).segments == []


def test_create_engine_dispatch():
    assert isinstance(create_engine(Settings(engine="fake")), FakeEngine)
    assert isinstance(create_engine(Settings(engine="faster_whisper")), FasterWhisperEngine)
    assert isinstance(create_engine(Settings(engine="qwen3_vllm", vllm_url="http://x:8000")), Qwen3VllmEngine)


# ------------------------------------------------------------------ speech gate
def test_gate_rms_and_length_rules():
    gate = SpeechGate(min_rms_dbfs=-60.0, vad_enabled=False)
    assert gate.check(np.zeros(SR, dtype=np.float32)).reason == "silence"
    assert gate.check(tone(0.05)).reason == "too_short"
    assert gate.check(np.full(SR, 10 ** (-70 / 20), dtype=np.float32)).reason == "silence"
    assert gate.check(tone(1.0)).passed


def test_gate_vad_rule_with_injected_detector():
    gate = SpeechGate(min_rms_dbfs=-60.0, vad_enabled=True, min_speech_ms=250, vad_fn=lambda x: 120.0)
    result = gate.check(tone(1.0))
    assert not result.passed and result.reason == "no_speech" and result.speech_ms == 120.0
    gate = SpeechGate(min_rms_dbfs=None, vad_enabled=True, min_speech_ms=250, vad_fn=lambda x: 900.0)
    assert gate.check(tone(1.0)).passed
    assert gate.check(np.zeros(SR, dtype=np.float32)).passed  # RMS rule disabled, detector says speech


def test_gate_result_is_json_safe():
    d = SpeechGate(vad_enabled=False).check(np.zeros(SR, dtype=np.float32)).as_dict()
    assert d["peak_dbfs"] is None and d["reason"] == "silence"


def test_real_silero_gate_rejects_noise_and_silence():
    pytest.importorskip("faster_whisper")
    gate = SpeechGate(min_rms_dbfs=None, vad_enabled=True, min_speech_ms=250)
    gate.load()
    assert gate.vad_enabled, "Silero VAD failed to load"
    rng = np.random.default_rng(0)
    white = rng.normal(0, 0.03, 30 * SR).astype(np.float32)
    hum = (0.05 * np.sin(2 * np.pi * 50 * np.arange(30 * SR) / SR)).astype(np.float32)
    for name, audio in (("silence", np.zeros(30 * SR, dtype=np.float32)), ("white", white), ("hum", hum)):
        result = gate.check(audio)
        assert not result.passed, (name, result)
        assert result.reason == "no_speech"
