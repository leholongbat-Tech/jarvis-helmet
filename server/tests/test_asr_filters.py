"""Hallucination filter tests (server/asr/jarvis_asr/filters.py + hallucinations.txt)."""

from __future__ import annotations

import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import pytest

ASR_DIR = Path(__file__).resolve().parents[1] / "asr"
if str(ASR_DIR) not in sys.path:
    sys.path.insert(0, str(ASR_DIR))

from jarvis_asr.config import FilterSettings  # noqa: E402
from jarvis_asr.engines import Segment  # noqa: E402
from jarvis_asr.filters import (  # noqa: E402
    HallucinationFilter,
    PhraseList,
    find_loop,
    longest_common_run,
    normalize,
    words,
)

HALLUCINATIONS = ASR_DIR / "config" / "hallucinations.txt"
SENTENCES = Path(__file__).resolve().parents[2] / "tools" / "tests" / "asr" / "sentences.md"
PROMPT = "Jarvis, quản gia, open mask, close mask, mặt nạ, đèn mắt, combat mode, stealth mode, thưa ngài."


@pytest.fixture(scope="module")
def flt() -> HallucinationFilter:
    return HallucinationFilter(FilterSettings(), str(HALLUCINATIONS))


def seg(text, lp=-0.2, ns=0.02, cr=1.2):
    return Segment(text=text, avg_logprob=lp, no_speech_prob=ns, compression_ratio=cr)


def test_normalization_is_nfc_casefold_and_punctuation_free():
    nfd = unicodedata.normalize("NFD", "Mở MẶT NẠ!!")
    assert normalize(nfd) == "mở mặt nạ"
    assert words("What's up, Jarvis?") == ["whats", "up", "jarvis"]
    assert normalize("...  ♪ ") == ""


def test_phrase_list_parsing():
    pl = PhraseList(lines=["# comment", "", "Hãy subscribe cho kênh", "=Thank you.", "  =  You  "])
    assert pl.count == 3
    assert pl.find_phrase(words("ok hãy SUBSCRIBE cho kênh nhé")) == "Hãy subscribe cho kênh"
    assert pl.is_exact(words("thank you"))
    assert pl.is_exact(words("You!"))
    assert not pl.is_exact(words("thank you jarvis"))


def test_shipped_blocklist_has_the_classic_phrases():
    pl = PhraseList(str(HALLUCINATIONS))
    for phrase in ("Hãy subscribe cho kênh", "Cảm ơn các bạn đã xem", "Thanks for watching",
                   "Subtitles by the Amara.org community"):
        assert pl.find_phrase(words(phrase)) or pl.is_exact(words(phrase)), phrase
    assert pl.count >= 60


@pytest.mark.parametrize(
    "text",
    [
        "Hãy subscribe cho kênh Ghiền Mì Gõ Để không bỏ lỡ những video hấp dẫn",
        "Hãy subscribe cho kênh Ghiền Mì Gõ để không bỏ lỡ những video hấp dẫn.",
        "hãy SUBSCRIBE cho kênh!",
        "Cảm ơn các bạn đã xem video.",
        "Cảm ơn các bạn đã theo dõi và hẹn gặp lại.",
        "Hẹn gặp lại các bạn trong những video tiếp theo!",
        "Thanks for watching!",
        "Thank you for watching.",
        "Please subscribe to my channel.",
        "Subtitles by the Amara.org community",
        "Thank you.",
        "you",
        "字幕由Amara.org社区提供",
        "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
        "ご視聴ありがとうございました",
    ],
)
def test_classic_hallucinations_are_rejected(flt, text):
    result = flt.apply([seg(" " + text)])
    assert result.text == "", result
    assert result.dropped in ("blocklist", "cjk")


def test_hallucinated_sentence_is_removed_but_command_kept(flt):
    result = flt.apply([seg(" Mở mặt nạ. Cảm ơn các bạn đã xem.")])
    assert result.text == "Mở mặt nạ."
    assert result.dropped is None
    assert any(n.startswith("blocklist:") for n in result.notes)


def test_sentence_only_entries_need_the_whole_sentence():
    pl = PhraseList(lines=["~Cảm ơn các bạn", "Hẹn gặp lại các bạn trong"])
    assert pl.find_phrase(words("Cảm ơn các bạn!")) == "Cảm ơn các bạn"
    assert pl.find_phrase(words("nói cảm ơn các bạn đã đến dự tiệc")) is None
    assert pl.find_phrase(words("Hẹn gặp lại các bạn trong video sau")) == "Hẹn gặp lại các bạn trong"


@pytest.mark.parametrize("command", [
    "Jarvis, nói cảm ơn các bạn đã đến dự tiệc.",
    "Jarvis, chào tạm biệt và hẹn gặp lại các bạn.",
    "Jarvis, bấm chuông thông báo khi pin yếu.",
])
def test_commands_containing_short_outro_words_survive(flt, command):
    result = flt.apply([seg(" " + command)])
    assert result.dropped is None and result.text == command, result


def test_trailing_short_outro_sentence_is_still_removed(flt):
    result = flt.apply([seg(" Mở mặt nạ. Cảm ơn các bạn.")])
    assert result.text == "Mở mặt nạ."
    result = flt.apply([seg(" Bật đèn mắt. Hẹn gặp lại các bạn trong những video tiếp theo!")])
    assert result.text == "Bật đèn mắt."


def test_exact_only_entries_need_the_whole_utterance(flt):
    assert flt.filter_text("Thank you very much!").dropped == "blocklist"
    kept = flt.filter_text("Thank you, Jarvis.")
    assert kept.text == "Thank you, Jarvis." and kept.dropped is None


@pytest.mark.parametrize("text", ["", "   ", "...", " . , ? ", "♪♪♪", "- -"])
def test_empty_or_punctuation_only_is_rejected(flt, text):
    result = flt.apply([seg(text)])
    assert result.text == "" and result.dropped == "empty"


def test_cjk_rule(flt):
    assert flt.filter_text("谢谢大家收看本期节目").dropped == "cjk"
    assert flt.filter_text("Mở mặt nạ. 好的。").text == "Mở mặt nạ."
    no_cjk_rule = HallucinationFilter(FilterSettings(drop_cjk=False), phrases=PhraseList(lines=[]))
    assert no_cjk_rule.filter_text("好的").text == "好的"


def test_dominant_repetition_loop_is_rejected(flt):
    assert flt.filter_text("the the the the the the the").dropped == "repetition"
    assert flt.filter_text("Mở mặt nạ mở mặt nạ mở mặt nạ mở mặt nạ mở mặt nạ").dropped == "repetition"


def test_minor_repetition_loop_is_collapsed(flt):
    text = "Jarvis ơi bật đèn mắt lên rồi mở mặt nạ ra giúp tôi với nhé, ok ok ok ok."
    result = flt.filter_text(text)
    assert result.dropped is None
    assert result.text == "Jarvis ơi bật đèn mắt lên rồi mở mặt nạ ra giúp tôi với nhé, ok."
    assert any(n.startswith("loop_collapsed:ok") for n in result.notes)


def test_short_repetitions_are_kept(flt):
    assert flt.filter_text("Không không không, đóng mặt nạ lại.").dropped is None
    assert flt.filter_text("Open the mask, open the mask.").dropped is None


def test_find_loop_and_common_run_helpers():
    assert find_loop(["a", "b", "a", "b", "a", "b", "a", "b"], 4) == (0, 2, 4)
    assert find_loop(["a", "b", "c"], 4) is None
    assert longest_common_run(["x", "open", "mask", "y"], ["open", "mask", "close"]) == 2


@pytest.mark.parametrize(
    "segment, reason",
    [
        (seg(" Cảm ơn.", lp=-1.4), "low_logprob"),
        (seg(" Ừ.", lp=-0.7, ns=0.85), "no_speech_prob"),
        (seg(" la la la la la la la", cr=3.1), "compression"),
    ],
)
def test_low_confidence_segments_are_rejected(flt, segment, reason):
    result = flt.apply([segment])
    assert result.text == "" and result.dropped == reason


def test_confident_segment_with_high_no_speech_prob_is_kept(flt):
    result = flt.apply([seg(" Mở mặt nạ.", lp=-0.2, ns=0.8)])
    assert result.text == "Mở mặt nạ."


def test_only_bad_segment_is_removed(flt):
    result = flt.apply([seg(" Mở mặt nạ."), seg(" Hãy subscribe cho kênh.", lp=-1.5)])
    assert result.text == "Mở mặt nạ."
    assert result.notes == ["segment:low_logprob"]


def test_prompt_echo_is_rejected_but_prompt_words_are_allowed(flt):
    assert flt.filter_text(PROMPT, prompts=[PROMPT]).dropped == "prompt_echo"
    echo = "quản gia open mask close mask mặt nạ đèn mắt combat mode"
    assert flt.filter_text(echo, prompts=[PROMPT]).dropped == "prompt_echo"
    assert flt.filter_text("Jarvis, open mask.", prompts=[PROMPT]).text == "Jarvis, open mask."
    assert flt.filter_text("Quản gia, open mask nhanh lên.", prompts=[PROMPT]).dropped is None


def test_disabled_filter_passes_everything_through():
    off = HallucinationFilter(FilterSettings(enabled=False), str(HALLUCINATIONS))
    result = off.apply([seg(" Thanks for watching!", lp=-2.0)])
    assert result.text == "Thanks for watching!" and result.dropped is None


def test_blocklist_file_is_reloaded_when_it_changes(tmp_path):
    path = tmp_path / "h.txt"
    path.write_text("phrase alpha\n", encoding="utf-8")
    pl = PhraseList(str(path))
    flt = HallucinationFilter(FilterSettings(), phrases=pl)
    assert flt.filter_text("phrase beta").dropped is None
    path.write_text("phrase alpha\nphrase beta\n", encoding="utf-8")
    os.utime(path, (time.time() + 10, time.time() + 10))
    pl._checked = 0.0  # skip the 2 s throttle
    assert flt.filter_text("phrase beta").dropped == "blocklist"


def test_missing_blocklist_falls_back_to_builtin(tmp_path):
    flt = HallucinationFilter(FilterSettings(), str(tmp_path / "missing.txt"))
    assert flt.phrases.count > 0
    assert flt.filter_text("Thanks for watching!").dropped == "blocklist"


# --- regressions: real commands must never be filtered -------------------------------------
def _script_sentences():
    if not SENTENCES.exists():
        return []
    rows = re.findall(r"^\|\s*[MVE]\d{2,3}\s*\|\s*(.+?)\s*\|\s*$", SENTENCES.read_text(encoding="utf-8"), re.M)
    return rows


def test_recording_script_is_present():
    assert len(_script_sentences()) >= 50


@pytest.mark.parametrize("sentence", _script_sentences())
def test_scripted_sentences_survive_the_filter(flt, sentence):
    for prompts in ((), (PROMPT,)):
        result = flt.apply([seg(" " + sentence)], prompts)
        assert result.dropped is None and result.text == sentence, (sentence, result)


@pytest.mark.parametrize(
    "utterance",
    ["Jarvis", "Jarvis.", "Giắc vít", "Quản gia", "Quản gia.", "Tạm biệt", "Kết thúc.", "Goodbye.",
     "That's all.", "Cảm ơn Jarvis.", "Cảm ơn nhé.", "Ok.", "Không.", "Dừng lại."],
)
def test_wake_and_exit_words_survive(flt, utterance):
    assert flt.apply([seg(" " + utterance)]).text == utterance
