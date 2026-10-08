"""Unit / dry-run tests for tools/wakeword (no GPU, no real TTS server needed)."""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

import augment
import ctx_positives
import eval_wakeword
import gen_negatives
import gen_positives
import record_personal
import tflite_to_c
import wwcommon as ww

REPO = Path(__file__).resolve().parents[3]
RATE = ww.TARGET_RATE


def sine(freq: float, seconds: float, rate: int = RATE) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def dominant_hz(audio: np.ndarray, rate: int = RATE) -> float:
    spectrum = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
    return float(np.fft.rfftfreq(len(audio), 1 / rate)[np.argmax(spectrum)])


# --------------------------------------------------------------------------- wwcommon

def test_slugify_folds_vietnamese():
    assert ww.slugify("Quản gia") == "quan_gia"
    assert ww.slugify("Đức Trí") == "duc_tri"
    assert ww.slugify("vi_VN-vivos-x_low#12") == "vi_vn_vivos_x_low_12"


def test_wav_roundtrip_and_streamed_header(tmp_path):
    audio = sine(440, 0.5)
    path = tmp_path / "a.wav"
    ww.write_wav(path, audio)
    back = ww.read_audio(path)
    assert len(back) == len(audio)
    assert np.max(np.abs(back - audio)) < 1e-3
    # Streamed WAV (VieNeu): RIFF/data sizes 0xFFFFFFFF, 24 kHz.
    pcm = ww.to_int16(sine(300, 0.2, 24000)).tobytes()
    header = (b"RIFF" + b"\xff\xff\xff\xff" + b"WAVE" + b"fmt " + (16).to_bytes(4, "little")
              + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + (24000).to_bytes(4, "little")
              + (48000).to_bytes(4, "little") + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
              + b"data" + b"\xff\xff\xff\xff")
    samples, rate = ww.parse_wav_bytes(header + pcm)
    assert rate == 24000 and len(samples) == len(pcm) // 2


def test_resample_keeps_frequency_and_length():
    out = ww.resample(sine(1000, 1.0, 24000), 24000, 16000)
    assert abs(len(out) - 16000) <= 1
    assert abs(dominant_hz(out) - 1000) < 5


def test_speed_tempo_pitch():
    tone = sine(400, 1.0)
    faster = ww.change_speed(tone, 1.25)
    assert abs(len(faster) - RATE / 1.25) < 5 and abs(dominant_hz(faster) - 500) < 8
    stretched = ww.time_stretch(tone, 1.25)
    assert abs(len(stretched) - RATE / 1.25) < 0.02 * RATE
    assert abs(dominant_hz(stretched) - 400) < 8
    shifted = ww.pitch_shift(tone, 12.0)
    assert abs(len(shifted) - RATE) < 0.03 * RATE
    assert abs(dominant_hz(shifted) - 800) < 15


def test_trim_silence():
    audio = np.concatenate([np.zeros(RATE), sine(300, 0.5), np.zeros(RATE)])
    trimmed = ww.trim_silence(audio, pad_ms=100)
    assert 0.5 * RATE <= len(trimmed) <= 0.75 * RATE


# --------------------------------------------------------------------------- fake TTS server

class FakeVieNeu(BaseHTTPRequestHandler):
    requests: list = []
    fail_first = 0

    def log_message(self, *args):  # silence
        pass

    def do_GET(self):
        if self.path == "/v1/voices":
            body = json.dumps({"object": "list", "data": [
                {"id": "Hải Đăng", "name": "Hải Đăng"}, {"id": "Thùy Dung", "name": "Thùy Dung"}]})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())
        else:
            self.send_error(404)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeVieNeu.requests.append(payload)
        if FakeVieNeu.fail_first > 0:
            FakeVieNeu.fail_first -= 1
            self.send_response(503)
            self.send_header("Retry-After", "0")
            self.end_headers()
            return
        rate = payload["sample_rate"]
        rng = random.Random(len(payload["input"]))
        audio = ww.synthetic_speech(rng)
        pcm = ww.to_int16(ww.resample(audio, RATE, rate)).tobytes()
        self.send_response(200)
        self.send_header("Content-Type", "audio/pcm")
        self.send_header("X-Sample-Rate", str(rate))
        self.end_headers()
        self.wfile.write(pcm)


@pytest.fixture()
def tts_server():
    FakeVieNeu.requests = []
    FakeVieNeu.fail_first = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeVieNeu)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1/audio/speech"
    server.shutdown()


def test_tts_client_voices_and_retry(tts_server):
    client = ww.TtsClient(url=tts_server, retries=2)
    assert client.list_voices() == ["Hải Đăng", "Thùy Dung"]
    FakeVieNeu.fail_first = 1
    audio = client.synthesize("quản gia", "Hải Đăng")
    assert len(audio) > 0.3 * RATE
    assert FakeVieNeu.requests[-1]["response_format"] == "pcm"
    assert FakeVieNeu.requests[-1]["voice"] == "Hải Đăng"


def test_list_voices_skips_kokoro_voices_of_the_router(monkeypatch):
    # tts-router merges Kokoro (English) voices into /v1/voices; VieNeu would replace those
    # names with its default voice for Vietnamese text, so they must not be used.
    body = json.dumps({"data": [
        {"name": "Hải Đăng", "engine": "vieneu"},
        {"name": "af_heart", "engine": "kokoro"},
        {"name": "bm_george", "engine": "Kokoro"},
        {"name": "Thùy Dung"},  # no engine field (VieNeu directly)
        "Ngọc Huyền",
    ]}).encode("utf-8")
    client = ww.TtsClient(url="http://router:8880/v1/audio/speech", retries=0)
    monkeypatch.setattr(client, "_request", lambda url, payload: (body, {}))
    assert client.list_voices() == ["Hải Đăng", "Thùy Dung", "Ngọc Huyền"]


def test_gen_positives_end_to_end(tts_server, tmp_path):
    out = tmp_path / "pos"
    code = gen_positives.main(["--api-url", tts_server, "--count", "7", "--variants", "3",
                               "--out", str(out), "--workers", "2"])
    assert code == 0
    wavs = sorted(out.glob("*.wav"))
    assert len(wavs) == 7
    records = ww.read_jsonl(out / "manifest.jsonl")
    assert {r["voice"] for r in records} == {"Hải Đăng", "Thùy Dung"}
    assert all("quản gia" in r["text"].lower() for r in records)
    assert len(FakeVieNeu.requests) == 3  # ceil(7 / 3) calls
    # Resume: nothing new, no new requests.
    assert gen_positives.main(["--api-url", tts_server, "--count", "7", "--variants", "3",
                               "--out", str(out)]) == 0
    assert len(FakeVieNeu.requests) == 3


def test_gen_positives_for_another_wake_word(tts_server, tmp_path):
    assert gen_positives.DEFAULT_TEXTS == gen_positives.default_texts("quản gia")
    assert gen_positives.DEFAULT_TEXTS[:2] == ["quản gia", "Quản gia."]
    texts = gen_positives.default_texts("Hàu ơi")
    assert texts[:3] == ["hàu ơi", "Hàu ơi.", "Hàu ơi!"] and "ê hàu ơi" in texts
    out = tmp_path / "pos"
    assert gen_positives.main(["--wake-word", "Hàu ơi", "--api-url", tts_server, "--count", "4",
                               "--variants", "2", "--out", str(out), "--workers", "1"]) == 0
    records = ww.read_jsonl(out / "manifest.jsonl")
    assert len(records) == 4 and all("hàu ơi" in r["text"].lower() for r in records)


def test_hau_oi_negatives_never_contain_the_wake_word(tmp_path):
    phrases = ww.load_phrases(Path(gen_negatives.__file__).with_name("negatives_hau_oi.txt"))
    assert len(phrases) >= 60 and "hàu" in phrases and "tàu ơi" in phrases
    # Near-homophones would contradict the TTS positives (see the file header).
    assert not {"hầu ơi", "hậu ơi", "hào ơi"} & set(phrases)
    assert not [p for p in phrases if "hàu ơi" in p.lower()]
    bad = tmp_path / "bad.txt"
    bad.write_text("ê hàu ơi\n", encoding="utf-8")
    assert gen_negatives.main(["--dry-run", "--wake-word", "hàu ơi", "--phrases", str(bad),
                               "--out", str(tmp_path / "neg")]) == 1


def test_gen_negatives_covers_phrases_and_rejects_wake_word(tts_server, tmp_path):
    phrases = tmp_path / "neg.txt"
    phrases.write_text("quản lý\ngia đình\nquán gà\n", encoding="utf-8")
    out = tmp_path / "neg"
    assert gen_negatives.main(["--api-url", tts_server, "--phrases", str(phrases),
                               "--per-phrase", "2", "--variants", "1", "--out", str(out)]) == 0
    texts = [r["text"] for r in ww.read_jsonl(out / "manifest.jsonl")]
    assert sorted(texts) == sorted(["quản lý", "gia đình", "quán gà"] * 2)
    phrases.write_text("quản gia ơi\n", encoding="utf-8")
    assert gen_negatives.main(["--dry-run", "--phrases", str(phrases), "--out", str(out)]) == 1


def test_default_negatives_never_contain_the_wake_word():
    phrases = ww.load_phrases(gen_negatives.DEFAULT_PHRASES)
    assert len(phrases) >= 40
    assert not [p for p in phrases if "quản gia" in p.lower()]


# --------------------------------------------------------------------------- record / augment

def test_record_personal_dry_run(tmp_path):
    out = tmp_path / "personal"
    assert record_personal.main(["--dry-run", "--count", "4", "--out", str(out), "--speaker", "Long"]) == 0
    records = ww.read_jsonl(out / "manifest.jsonl")
    assert len(records) == 4
    assert {r["accent"] for r in records} == {"Bắc", "Nam"}
    assert all((out / r["file"]).exists() for r in records)


def test_record_personal_runs_with_a_cp1252_stdout(tmp_path):
    # Git Bash (mintty), a pipe or `| tee` on Windows: stdout is not a console and Python
    # would encode it as cp1252, which has no 'ầ'/'ả'.
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    result = subprocess.run(
        [sys.executable, str(Path(record_personal.__file__)), "--dry-run", "--count", "2",
         "--out", str(tmp_path / "personal")],
        capture_output=True, env=env, timeout=120)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert "quản gia" in result.stdout.decode("utf-8")
    assert len(ww.read_jsonl(tmp_path / "personal" / "manifest.jsonl")) == 2


def test_record_check_take():
    _, problem = record_personal.check_take(np.zeros(RATE, np.float32) + 1e-4)
    assert problem and "nhỏ" in problem
    _, problem = record_personal.check_take(np.ones(RATE, np.float32))
    assert problem and "clipping" in problem
    trimmed, problem = record_personal.check_take(np.concatenate([np.zeros(8000), sine(200, 0.5), np.zeros(8000)]))
    assert problem is None and len(trimmed) < 1.0 * RATE


def test_augment_outputs(tmp_path):
    src = tmp_path / "in"
    ww.write_wav(src / "a.wav", sine(300, 0.6))
    ww.write_wav(src / "b.wav", sine(500, 0.6))
    out = tmp_path / "out"
    assert augment.main(["--in", str(src), "--out", str(out), "--variants", "3", "--keep-original"]) == 0
    assert len(list(out.glob("*.wav"))) == 8
    for path in out.glob("*_aug*.wav"):
        audio = ww.read_audio(path)
        assert np.max(np.abs(audio)) <= 1.0 and len(audio) > 0.6 * RATE


def test_helmet_impulse_and_snr():
    rng = random.Random(1)
    impulse = augment.helmet_impulse(rng)
    assert abs(np.sum(impulse ** 2) - 1.0) < 1e-3 and len(impulse) < 0.2 * RATE
    clean = sine(300, 1.0)
    noise = augment.colored_noise(rng, len(clean))
    mixed = augment.mix_at_snr(clean, noise, 10.0)
    snr = 10 * np.log10(np.mean(clean ** 2) / np.mean((mixed - clean) ** 2))
    assert abs(snr - 10.0) < 0.1


# --------------------------------------------------------------------------- export

def fake_model(size: int = 64) -> bytes:
    return b"\x1c\x00\x00\x00TFL3" + bytes(range(size - 8))


def test_placeholder_matches_committed_file(tmp_path):
    out = tmp_path / "mww_model_data.c"
    assert tflite_to_c.main(["--placeholder", "--output", str(out)]) == 0
    committed = REPO / "firmware" / "main" / "boards" / "jarvis-helmet" / "mww_model_data.c"
    assert out.read_text(encoding="utf-8") == committed.read_text(encoding="utf-8")
    assert "g_jarvis_mww_model_size = 0;" in out.read_text(encoding="utf-8")


def test_export_model_with_manifest(tmp_path):
    model = tmp_path / "quan_gia.tflite"
    model.write_bytes(fake_model())
    (tmp_path / "quan_gia.json").write_text(json.dumps({
        "type": "micro", "wake_word": "quản gia", "version": 2,
        "micro": {"probability_cutoff": 0.96, "sliding_window_size": 6,
                  "feature_step_size": 10, "tensor_arena_size": 30000}}), encoding="utf-8")
    out = tmp_path / "out.c"
    assert tflite_to_c.main(["--model", str(model), "--output", str(out), "--no-inspect"]) == 0
    text = out.read_text(encoding="utf-8")
    assert "const unsigned int g_jarvis_mww_model_size = 64;" in text
    assert "const unsigned int g_jarvis_mww_tensor_arena_size = 30000;" in text
    assert "CONFIG_JARVIS_MWW_CUTOFF_PCT=96" in text and "CONFIG_JARVIS_MWW_SLIDING_WINDOW=6" in text
    assert "0x1c, 0x00, 0x00, 0x00, 0x54, 0x46, 0x4c, 0x33" in text
    assert text.count("#ifdef CONFIG_JARVIS_MICRO_WAKE_WORD") == 1
    # The manifest's word, capitalized, is what the firmware reports to the server.
    assert "// Wake word:  Quản gia" in text
    assert ("const char g_jarvis_mww_wake_word[] = "
            "{0x51, 0x75, 0xe1, 0xba, 0xa3, 0x6e, 0x20, 0x67, 0x69, 0x61, 0x00};") in text


def test_export_reports_the_chosen_wake_word(tmp_path):
    model = tmp_path / "hau_oi.tflite"
    model.write_bytes(fake_model())
    (tmp_path / "hau_oi.json").write_text(json.dumps({"wake_word": "hàu ơi", "micro": {}}),
                                          encoding="utf-8")
    out = tmp_path / "out.c"
    assert tflite_to_c.main(["--model", str(model), "--output", str(out), "--no-inspect"]) == 0
    hau_oi = "{0x48, 0xc3, 0xa0, 0x75, 0x20, 0xc6, 0xa1, 0x69, 0x00}"  # "Hàu ơi"
    assert f"const char g_jarvis_mww_wake_word[] = {hau_oi};" in out.read_text(encoding="utf-8")
    assert tflite_to_c.main(["--model", str(model), "--output", str(out), "--no-inspect",
                             "--wake-word", "Hey Hàu"]) == 0
    assert '// "Hey Hàu"' in out.read_text(encoding="utf-8")


def test_export_rejects_bad_inputs(tmp_path):
    bad = tmp_path / "bad.tflite"
    bad.write_bytes(b"not a model at all, definitely")
    assert tflite_to_c.main(["--model", str(bad), "--output", str(tmp_path / "x.c"), "--no-inspect"]) == 1
    model = tmp_path / "m.tflite"
    model.write_bytes(fake_model())
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"micro": {"feature_step_size": 20}}), encoding="utf-8")
    args = ["--model", str(model), "--manifest", str(manifest), "--output", str(tmp_path / "y.c"), "--no-inspect"]
    assert tflite_to_c.main(args) == 1
    assert tflite_to_c.main(args + ["--force"]) == 0


# --------------------------------------------------------------------------- decision logic

def stream(values_at: dict[int, int], length: int) -> np.ndarray:
    probs = np.full(length, -1, np.int16)
    for index, value in values_at.items():
        probs[index] = value
    return probs


def test_detect_mean_over_window_and_refractory():
    # stride 2: an inference on every odd slice; strong word from slice 201 to 221.
    values = {i: (250 if 201 <= i <= 221 else 10) for i in range(1, 600, 2)}
    probs = stream(values, 600)
    found = eval_wakeword.detect(probs, 97, 5)
    assert len(found) == 1 and 201 <= found[0] <= 221
    # cutoff 98% (249/255): 250 is still above.
    assert len(eval_wakeword.detect(probs, 98, 5)) == 1
    # 99% (252/255): never.
    assert eval_wakeword.detect(probs, 99, 5) == []


def test_detect_warmup_and_strict_mean():
    early = stream({i: 255 for i in range(1, 60, 2)}, 400)
    assert eval_wakeword.detect(early, 97, 3) == []          # inside the 1 s warm-up
    assert eval_wakeword.detect(early, 97, 3, warmup=False)  # without it: detected
    # mean exactly at the cutoff (247) is not a detection, one more is.
    at_cutoff = stream({i: 247 for i in range(101, 400, 2)}, 400)
    assert eval_wakeword.detect(at_cutoff, 97, 4) == []
    above = stream({i: 248 for i in range(101, 400, 2)}, 400)
    assert len(eval_wakeword.detect(above, 97, 4)) == 1      # long word: refractory holds


def test_detect_retriggers_after_quiet_second():
    values = {}
    for start in (150, 450):
        for i in range(start, start + 20):
            values[i] = 255
    probs = stream({i: values.get(i, 0) for i in range(600)}, 600)
    assert len(eval_wakeword.detect(probs, 97, 3)) == 2


def test_parse_range():
    assert eval_wakeword.parse_range("0.95,0.97", True) == [95, 97]
    assert eval_wakeword.parse_range("90:93:1", True) == [90, 91, 92, 93]
    assert eval_wakeword.parse_range("3:7:2", False) == [3, 5, 7]


def test_quantization_matches_firmware_formula():
    class Dummy(eval_wakeword.StreamingModel):
        def __init__(self):
            self.multiplier = int(round(eval_wakeword.FEATURE_SCALE / (26 / 256) * 65536))
            self.zero_point = -128

    features = np.array([[0, 333, 666, 1000, 65535] + [0] * 35], np.uint16)
    q = Dummy().quantize(features)[0][:5].tolist()
    # Reference integer formula from the TFLM micro_speech example.
    reference = [max(-128, min(127, (v * 256 + 333) // 666 - 128)) for v in (0, 333, 666, 1000, 65535)]
    assert q == reference


def test_positive_clips_get_quiet_audio_after_them(tmp_path, monkeypatch):
    # The trainer places wake words 0.2-0.3 s before the end of its window, so models fire just
    # after the last syllable: a clip cut right there would never be detected.
    clip = tmp_path / "a.wav"
    ww.write_wav(clip, sine(440, 0.5))
    lengths = []

    def features(audio):
        lengths.append(len(audio))
        return np.zeros((0, 40), np.uint16)

    class Dummy:
        def probabilities(self, feats):
            return np.zeros(0, np.int16)

    monkeypatch.setattr(eval_wakeword, "compute_features", features)
    streams = eval_wakeword.load_streams(Dummy(), [clip], lead_ms=1500, trail_ms=1000)
    assert lengths == [int(0.5 * RATE) + int(1.5 * RATE) + RATE]
    assert streams[0].seconds == pytest.approx(0.5)  # only the clip itself counts


def test_ctx_positive_ends_inside_the_next_word():
    silence = np.zeros(int(0.3 * RATE), np.float32)
    wake = np.concatenate([silence, sine(300, 0.5), silence])
    following = np.concatenate([silence, sine(2000, 0.6), silence])
    out = ctx_positives.join(wake, following, gap_s=0.05, after_s=0.30, rng=random.Random(1))
    trimmed = ww.trim_silence(wake, pad_ms=20)
    assert len(out) == len(trimmed) + int(0.30 * RATE)
    assert abs(dominant_hz(out[: len(trimmed)]) - 300) < 50
    assert abs(dominant_hz(out[-int(0.15 * RATE):]) - 2000) < 100  # the command has started


def test_ctx_positives_cli(tmp_path):
    for sub, freq in (("w", 300), ("n", 2000)):
        (tmp_path / sub).mkdir()
        for i in range(2):
            ww.write_wav(tmp_path / sub / f"{sub}{i}.wav", sine(freq, 0.5))
    out = tmp_path / "o"
    assert ctx_positives.main(["--wake", str(tmp_path / "w"), "--next", str(tmp_path / "n"),
                               "--out", str(out), "--count", "5"]) == 0
    rows = [json.loads(line) for line in (out / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 5 and len(list(out.glob("*.wav"))) == 5
    assert all(row["after_s"] >= row["gap_s"] + 0.02 for row in rows)
    assert ctx_positives.main(["--wake", str(tmp_path / "w"), "--next", str(tmp_path / "missing"),
                               "--out", str(out)]) == 1

