"""Tests for the ASR evaluation tools (asrlib, eval.py, capture_to_manifest.py, make_synthetic.py).

    .venv/Scripts/python -m pytest tools/tests/asr -q
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unicodedata
from pathlib import Path

import httpx
import numpy as np
import pytest
import soundfile as sf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import asrlib  # noqa: E402


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"asr_tool_{name}", HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


evaluate = load("eval")
capture_tool = load("capture_to_manifest")
synth = load("make_synthetic")


# ------------------------------------------------------------------ normalization / WER
def test_normalization_is_vietnamese_aware():
    assert asrlib.normalize_text("Jarvis, OPEN mask rồi bật ĐÈN MẮT lên!") == "jarvis open mask rồi bật đèn mắt lên"
    assert asrlib.normalize_text(unicodedata.normalize("NFD", "Mở mặt nạ.")) == "mở mặt nạ"
    assert asrlib.normalize_text("What's up?") == "whats up"
    assert asrlib.normalize_text("“Đèn-mắt” …") == "đèn mắt"


@pytest.mark.parametrize("old, new", [("hòa", "hoà"), ("khỏe", "khoẻ"), ("thủy", "thuỷ"), ("Tòa", "toà"),
                                      ("họa", "hoạ")])
def test_tone_mark_placement_is_unified(old, new):
    assert asrlib.normalize_text(old) == asrlib.normalize_text(new) == new


@pytest.mark.parametrize("word", ["mùa", "quý", "hoàng", "thuyền", "của"])
def test_tone_mark_canonicalization_leaves_other_words(word):
    assert asrlib.canonical_tone(word) == word


def test_wer_spells_numbers_and_matches_spacing():
    """tests-tools-7: a correct transcript with digits or "Good bye" is not an error."""
    assert asrlib.wer("Một năm có bao nhiêu ngày?", "1 năm có bao nhiêu ngày") == 0.0
    assert asrlib.wer("Goodbye.", "Good bye.", lang="en") == 0.0
    assert asrlib.wer("Good bye.", "Goodbye.", lang="en") == 0.0
    assert asrlib.wer("Set the volume to fifty.", "Set the volume to 50.", lang="en") == 0.0
    assert asrlib.wer("Gọi không chín một hai.", "Gọi 0912.") == 0.0
    assert asrlib.wer("Mở mặt nạ.", "mở mặt") == pytest.approx(1 / 3)
    ref, hyp = asrlib.scoring_tokens("mở mặt nạ", "mở mặtnạ")
    assert hyp == ["mở", "mặt", "nạ"]  # split only into adjacent reference words


def test_edit_ops_and_wer():
    assert asrlib.edit_ops("a b c d".split(), "a x c d e".split()) == (1, 0, 1)
    assert asrlib.edit_ops("a b c".split(), "a c".split()) == (0, 1, 0)
    assert asrlib.wer("Mở mặt nạ.", "mở mặt nạ") == 0.0
    assert asrlib.wer("mở mặt nạ đi", "mở mặt") == pytest.approx(0.5)
    assert asrlib.wer("", "") == 0.0 and asrlib.wer("", "xin chào") == 1.0


def test_keyword_stats_counts_hits_misses_and_false_alarms():
    kws = asrlib.parse_keywords(["mask", "mặt nạ", "jarvis | giắc vít", "# comment", "đèn mắt"])
    ref = asrlib.tokenize("Jarvis, open mask rồi bật đèn mắt lên")
    hyp = asrlib.tokenize("Giắc vít open mát rồi bật đèn mắt lên mặt nạ")
    stats = asrlib.keyword_stats(ref, hyp, kws)
    assert stats["jarvis"] == {"ref": 1, "hit": 1, "false_alarm": 0}
    assert stats["mask"] == {"ref": 1, "hit": 0, "false_alarm": 0}
    assert stats["đèn mắt"] == {"ref": 1, "hit": 1, "false_alarm": 0}
    assert stats["mặt nạ"] == {"ref": 0, "hit": 0, "false_alarm": 1}


def test_shipped_keywords_cover_the_plan_examples():
    names = {k.name for k in asrlib.load_keywords(HERE / "keywords.txt")}
    assert {"mask", "mặt nạ", "đèn mắt", "open", "close", "combat mode"} <= names


def test_percentile():
    assert asrlib.percentile([], 50) is None
    assert asrlib.percentile([5.0], 90) == 5.0
    assert asrlib.percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)
    assert asrlib.percentile(list(range(1, 11)), 90) == pytest.approx(9.1)


def test_resample_helper_matches_length_and_pitch():
    x = np.sin(2 * np.pi * 440 * np.arange(24000) / 24000).astype(np.float32)
    y = asrlib.resample(x, 24000, 16000)
    assert len(y) == 16000
    spec = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    assert np.argmax(spec) * 16000 / len(y) == pytest.approx(440, abs=2)


# ------------------------------------------------------------------ datasets
def test_recording_script_has_three_groups_and_50_plus_sentences():
    rows = asrlib.load_sentences(HERE / "sentences.md")
    assert len(rows) >= 50
    groups = {g: [r for r in rows if r["group"] == g] for g in asrlib.SPEECH_GROUPS}
    assert all(len(v) >= 10 for v in groups.values())
    assert len({r["id"] for r in rows}) == len(rows)
    assert all(not any(ch.isdigit() for ch in r["text"]) for r in rows)  # no digit/word ambiguity


def test_manifest_roundtrip_and_validation(tmp_path):
    items = [{"id": "M01", "audio": "audio/M01.wav", "reference": "mở mặt nạ", "group": "mixed"},
             {"id": "N01", "audio": "audio/N01.wav", "reference": "", "group": "noise"}]
    asrlib.write_manifest(tmp_path / "manifest.jsonl", items)
    loaded = asrlib.load_manifest(tmp_path / "manifest.jsonl")
    assert [i["id"] for i in loaded] == ["M01", "N01"]
    assert Path(loaded[0]["audio_path"]) == (tmp_path / "audio" / "M01.wav").resolve()
    (tmp_path / "bad.jsonl").write_text('{"audio": "a.wav", "reference": "x", "group": "klingon"}\n',
                                        encoding="utf-8")
    with pytest.raises(ValueError, match="unknown group"):
        asrlib.load_manifest(tmp_path / "bad.jsonl")
    (tmp_path / "bad2.jsonl").write_text('{"audio": "a.wav"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="missing keys"):
        asrlib.load_manifest(tmp_path / "bad2.jsonl")


# ------------------------------------------------------------------ eval.py end to end
def _write_wav(path: Path, seconds: float = 3.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.zeros(int(16000 * seconds), dtype=np.float32), 16000, subtype="PCM_16")


def test_eval_against_a_mock_service(tmp_path, monkeypatch):
    items = [
        {"id": "M01", "audio": "audio/M01.wav", "reference": "Jarvis, open mask rồi bật đèn mắt lên.", "group": "mixed"},
        {"id": "V04", "audio": "audio/V04.wav", "reference": "Mở mặt nạ.", "group": "vi"},
        {"id": "E03", "audio": "audio/E03.wav", "reference": "Open the mask.", "group": "en"},
        {"id": "N01", "audio": "audio/N01.wav", "reference": "", "group": "noise", "synthetic": True},
    ]
    for it in items:
        _write_wav(tmp_path / it["audio"])
    asrlib.write_manifest(tmp_path / "manifest.jsonl", items)
    answers = {"M01.wav": "Jarvis open mát rồi bật đèn mắt lên", "V04.wav": "mở mặt nạ",
               "E03.wav": "Open the mask.", "N01.wav": ""}
    seen_forms = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "engine": "faster_whisper", "model": "large-v3",
                                             "device": "cuda"})
        body = request.content.decode("utf-8", errors="ignore")
        name = next(n for n in answers if n in body)
        seen_forms.append(body)
        text = answers[name]
        return httpx.Response(200, json={"text": text, "duration": 3.0, "language": "vi",
                                         "jarvis": {"timing_ms": {"total": 210.0, "infer": 200.0},
                                                    "dropped": None if text else "gate:no_speech",
                                                    "raw_text": text}})

    real_client = httpx.Client
    monkeypatch.setattr(evaluate.httpx, "Client", lambda *a, **k: real_client(transport=httpx.MockTransport(handler)))
    out = tmp_path / "RESULTS.md"
    rc = evaluate.main(["--manifest", str(tmp_path / "manifest.jsonl"), "--url",
                        "http://asr.test/v1/audio/transcriptions", "--out", str(out), "--no-warmup"])
    assert rc == 0
    report = out.read_text(encoding="utf-8")
    assert "large-v3+vi" in report and "large-v3+auto" in report and "large-v3+vi+prompt" in report
    assert "DỮ LIỆU SMOKE" in report  # synthetic item -> banner
    details = (tmp_path / "eval_details" / "large-v3+vi.jsonl").read_text(encoding="utf-8").splitlines()
    rows = {json.loads(line)["id"]: json.loads(line) for line in details}
    assert rows["M01"]["sub"] == 1 and rows["V04"]["wer"] == 0.0
    assert rows["M01"]["keywords"]["mask"]["hit"] == 0
    assert any('name="language"\r\n\r\nauto' in f for f in seen_forms)
    assert any('name="prompt"\r\n\r\nnone' in f for f in seen_forms)
    summary = evaluate.summarize(list(rows.values()))
    assert summary["groups"]["vi"]["wer"] == 0.0
    assert summary["groups"]["mixed"]["wer"] == pytest.approx(1 / 8)
    assert summary["negatives"] == 1 and summary["negatives_nonempty"] == 0
    assert summary["mid_p50"] == pytest.approx(210.0)
    assert summary["kw_acc"] == pytest.approx(8 / 9)  # M01 misses "mask" (1 of 5)


def test_load_configs_yaml(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("configs:\n  - name: a\n    url: http://x/v1/audio/transcriptions\n    language: vi\n"
                   "    prompt: none\n  - name: b\n    url: http://y:9002/v1/audio/transcriptions\n",
                   encoding="utf-8")
    configs = evaluate.load_configs(str(cfg), "unused", "unused")
    assert [c.name for c in configs] == ["a", "b"]
    assert configs[0].health_url == "http://x/health" and configs[1].language is None
    example = evaluate.load_configs(str(HERE / "configs.example.yaml"), "", "")
    assert {"large-v3+vi", "large-v3+auto", "large-v3+vi+prompt", "qwen3-asr+vi", "phowhisper+vi"} <= {
        c.name for c in example}


# ------------------------------------------------------------------ capture_to_manifest.py
def test_capture_to_manifest_matches_script_sentences(tmp_path):
    cap = tmp_path / "capture"
    records = [
        ("Jarvis open mát rồi bật đèn mắt lên", None),  # M01 (misrecognized but close)
        ("", "gate:no_speech"),  # empty PTT press -> skipped
        ("Mở mặt nạ đi Jarvis", None),  # M02
        ("hoàn toàn không liên quan gì cả xyz", None),  # needs review
        ("Mở mặt nạ đi Jarvis.", None),  # second take of M02
    ]
    lines = []
    for n, (text, dropped) in enumerate(records):
        rel = f"audio/20261006/{n:03d}_r{n}.wav"
        _write_wav(cap / rel, 1.0)
        lines.append(json.dumps({"rid": f"r{n}", "audio": rel, "text": text, "raw_text": text,
                                 "dropped": dropped, "ts": "2026-10-06T10:00:00+07:00"}, ensure_ascii=False))
    (cap / "transcripts.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = tmp_path / "real"
    assert capture_tool.main(["--capture", str(cap), "--out", str(out)]) == 0
    items = asrlib.load_manifest(out / "manifest.jsonl")
    assert [i["id"] for i in items] == ["M01", "M02", "U003", "M02_2"]
    assert items[0]["reference"] == "Jarvis, open mask rồi bật đèn mắt lên." and not items[0]["needs_review"]
    assert items[2]["needs_review"] is True
    assert all(Path(i["audio_path"]).exists() for i in items)


# ------------------------------------------------------------------ make_synthetic.py helpers
def test_router_synthesis_handles_wav_and_pcm():
    tone24 = (0.2 * np.sin(2 * np.pi * 300 * np.arange(24000) / 24000)).astype(np.float32)

    def wav_handler(request):
        payload = json.loads(request.content)
        assert payload["input"] == "Mở mặt nạ." and payload["response_format"] == "wav"
        import io

        buf = io.BytesIO()
        sf.write(buf, tone24, 24000, format="WAV", subtype="PCM_16")
        return httpx.Response(200, content=buf.getvalue(), headers={"content-type": "audio/wav"})

    def pcm_handler(request):
        pcm = (tone24 * 32767).astype("<i2").tobytes()
        return httpx.Response(200, content=pcm, headers={"content-type": "audio/pcm", "x-sample-rate": "24000"})

    for handler in (wav_handler, pcm_handler):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            audio = synth.synthesize_router(client, "http://tts/v1/audio/speech", "Mở mặt nạ.")
        assert len(audio) == 16000 and np.max(np.abs(audio)) == pytest.approx(0.2, abs=0.01)


def test_negative_samples_and_padding():
    rng = np.random.default_rng(0)
    negs = synth.negatives(rng, seconds=1.0)
    assert [n["id"] for n in negs] == ["N01", "N02", "N03", "N04", "N05"]
    assert not np.any(negs[0]["audio"])
    level = 20 * np.log10(np.sqrt(np.mean(negs[2]["audio"].astype(np.float64) ** 2)))
    assert level == pytest.approx(-30, abs=0.5)
    padded = synth.finish(np.ones(100, dtype=np.float32) * 0.5, [600, 700], None, rng)
    assert len(padded) == 100 + int(16000 * 1.3)
