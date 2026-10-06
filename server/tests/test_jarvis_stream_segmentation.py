"""Sentence segmentation of the xiaozhi `jarvis_stream` TTS provider (pure helpers).

The provider module is imported from its source file; outside the xiaozhi image its runtime
imports fail on purpose and only the pure helpers are usable (TTSProvider refuses to start).
"""
import importlib.util
import io
import random
import sys
import wave
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "xiaozhi" / "providers" / "tts" / "jarvis_stream.py"


def _load():
    spec = importlib.util.spec_from_file_location("jarvis_stream_under_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


js = _load()


def stream(text, chunks=None, **kw):
    """Feed `text` in chunks (default: one character at a time) then flush."""
    seg = js.SentenceSegmenter(**kw)
    out = []
    if chunks is None:
        chunks = list(text)
    for c in chunks:
        out += seg.feed(c)
    return out + seg.flush()


# --- sentence boundaries -------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Vâng, thưa ngài. Mặt nạ đã mở.", ["Vâng, thưa ngài.", "Mặt nạ đã mở."]),
    ("Ngài cần gì không? Tôi sẵn sàng!", ["Ngài cần gì không?", "Tôi sẵn sàng!"]),
    ("Thật sao?! Tuyệt vời.", ["Thật sao?!", "Tuyệt vời."]),
    ("Lưu ý: pin yếu; hãy sạc ngay.", ["Lưu ý:", "pin yếu;", "hãy sạc ngay."]),
    # ASCII ':' / ';' split only Vietnamese text: an English sentence keeps one voice.
    ("Sir, the battery level is: 85%. Mặt nạ đã mở.", ["Sir, the battery level is: 85%.", "Mặt nạ đã mở."]),
    ("Status: all systems online; eyes on.", ["Status: all systems online; eyes on."]),
    ("Dòng một\nDòng hai", ["Dòng một", "Dòng hai"]),
    ("Để tôi xem... à, đây rồi.", ["Để tôi xem...", "à, đây rồi."]),
    ("Để tôi xem… à, đây rồi.", ["Để tôi xem…", "à, đây rồi."]),
    ("Ngài nói \"mở mặt nạ.\" Tôi làm ngay.", ["Ngài nói \"mở mặt nạ.\"", "Tôi làm ngay."]),
    ("你好。我是贾维斯", ["你好。", "我是贾维斯"]),
])
def test_sentence_boundaries(text, expected):
    assert stream(text) == expected


@pytest.mark.parametrize("text", [
    "Điện áp hiện tại là 5.02 V, rất ổn định.",
    "Điện áp hiện tại là 5,02 V và dòng là 1.250 mA.",
    "Bây giờ là 14:32 thưa ngài.",
    "Có 1,000 người dùng và 3,5% pin.",
    "Phiên bản v2.5.0 đã sẵn sàng.",
    "Truy cập example.com để xem.",
    "Gặp TS. Nguyễn ở TP. Hồ Chí Minh nhé.",
    "Ông A. Einstein từng nói vậy.",
    "J.A.R.V.I.S. đang trực tuyến.",
    "Pin, đèn, mặt nạ v.v. đều ổn.",
])
def test_no_split_inside_numbers_times_abbreviations(text):
    # one segment when the first comma rule does not apply (ASCII or short fragment)
    segs = stream(text, first_min_chars=500)
    assert segs == [text]


def test_initialism_followed_by_capital_ends_sentence():
    assert stream("Tôi là J.A.R.V.I.S. Rất vui được phục vụ ngài.") == [
        "Tôi là J.A.R.V.I.S.", "Rất vui được phục vụ ngài."]


def test_decimal_dot_at_chunk_end_waits_for_more_text():
    seg = js.SentenceSegmenter()
    assert seg.feed("Pin còn 5.") == []  # could become 5.02
    assert seg.feed("02 V.") == []  # sentence end still undecidable
    assert seg.feed(" Hết.") == ["Pin còn 5.02 V."]
    assert seg.flush() == ["Hết."]


def test_list_markers_are_not_sentences():
    assert stream("1. Mở mặt nạ\n2. Bật đèn mắt") == ["1. Mở mặt nạ", "2. Bật đèn mắt"]


# --- first segment: comma rule -------------------------------------------------------------

def test_first_comma_gives_fast_first_segment():
    text = "Đã kích hoạt combat mode, thưa ngài, hệ thống vũ khí đã sẵn sàng."
    assert stream(text) == ["Đã kích hoạt combat mode,", "thưa ngài, hệ thống vũ khí đã sẵn sàng."]


def test_comma_rule_only_for_first_segment():
    text = "Xong. Đã kích hoạt combat mode, thưa ngài."
    assert stream(text) == ["Xong.", "Đã kích hoạt combat mode, thưa ngài."]


def test_short_comma_fragment_is_not_split():
    assert stream("Vâng, thưa ngài.") == ["Vâng, thưa ngài."]


def test_ascii_fragment_is_not_split_to_avoid_english_routing():
    assert stream("Good evening, sir. All systems are online.") == [
        "Good evening, sir.", "All systems are online."]
    assert stream("Stealth mode engaged, đèn mắt đã tắt, thưa ngài.") == [
        "Stealth mode engaged, đèn mắt đã tắt,", "thưa ngài."]


def test_full_sentence_preferred_when_already_available():
    # the whole first sentence is in the buffer: no reason to cut at its comma
    seg = js.SentenceSegmenter()
    assert seg.feed("Đã kích hoạt combat mode, thưa ngài. Hệ thống ") == [
        "Đã kích hoạt combat mode, thưa ngài."]


def test_decimal_comma_never_a_split_point():
    assert stream("Điện áp đo được 3,7 vôn thưa ngài.") == ["Điện áp đo được 3,7 vôn thưa ngài."]


def test_reset_starts_a_new_turn():
    seg = js.SentenceSegmenter()
    assert seg.feed("Đã kích hoạt combat mode, thưa ngài") == ["Đã kích hoạt combat mode,"]
    seg.reset()
    assert seg.pending == ""
    assert seg.feed("Đã đóng mặt nạ lại rồi, thưa ngài") == ["Đã đóng mặt nạ lại rồi,"]


def test_cjk_comma_first_segment():
    assert stream("我已经准备好了，请吩咐。") == ["我已经准备好了，", "请吩咐。"]


# --- cleaning, limits ----------------------------------------------------------------------

def test_punctuation_only_and_emoji_only_segments_are_dropped():
    assert stream("😀. Vâng.") == ["Vâng."]
    assert stream("... !!! ") == []


def test_leading_separators_are_stripped():
    assert js.clean_segment(" , - thưa ngài.") == "thưa ngài."
    assert js.clean_segment("  \n") == ""


def test_long_run_without_punctuation_is_cut():
    words = ("mặt nạ đang mở " * 40).strip()
    segs = stream(words, max_chars=120)
    assert len(segs) > 1
    assert all(len(s) <= 120 for s in segs)
    assert " ".join(segs) == words


def test_long_run_prefers_comma():
    text = "a" * 60 + ", " + "b " * 80
    cut = js.soft_cut(text, 120)
    assert text[:cut].endswith(",")


def test_streaming_matches_whole_text_without_commas():
    texts = [
        "Vâng thưa ngài. Mặt nạ đã mở. Pin còn 85 phần trăm!",
        "Điện áp là 5.02 V. Bây giờ là 14:32. Tôi là J.A.R.V.I.S. Rất vui.",
        "Good evening sir. All systems are online.\nReady when you are.",
    ]
    rnd = random.Random(1234)
    for text in texts:
        whole = js.split_segments(text)
        for _ in range(25):
            chunks, i = [], 0
            while i < len(text):
                n = rnd.randint(1, 7)
                chunks.append(text[i:i + n])
                i += n
            assert stream(text, chunks) == whole, chunks


def test_split_segments_whole_text():
    assert js.split_segments("Xin chào. Tôi là J.A.R.V.I.S, trợ lý của ngài.") == [
        "Xin chào.", "Tôi là J.A.R.V.I.S, trợ lý của ngài."]


# --- other helpers -------------------------------------------------------------------------

def test_pcm_to_wav_roundtrip():
    pcm = bytes(range(256)) * 10
    data = js.pcm_to_wav(pcm + b"\x01", 24000)  # odd trailing byte dropped
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)
        assert w.readframes(w.getnframes()) == pcm


@pytest.mark.parametrize("value,expected", [
    (24000, 24000), ("16000", 16000), (48000, 48000), (44100, 24000), (None, 24000), ("x", 24000)])
def test_opus_rate(value, expected):
    assert js.opus_rate(value, 24000) == expected


def test_provider_refuses_outside_xiaozhi_runtime():
    if js._RUNTIME_ERROR is None:
        pytest.skip("running inside the xiaozhi runtime")
    with pytest.raises(RuntimeError):
        js.TTSProvider({}, True)
