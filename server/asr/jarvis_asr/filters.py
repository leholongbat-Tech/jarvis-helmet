"""Whisper hallucination filter.

Rules, in order (each rejection is reported with a reason):
1. segment confidence: very low avg logprob, high no_speech_prob with a mediocre
   logprob, or a too-repetitive segment (gzip compression ratio);
2. empty / punctuation-only text;
3. sentences containing CJK (the helmet only speaks VI/EN) or a blocklisted
   phrase from hallucinations.txt are removed; exact-only entries ("=...")
   reject the utterance only when it is exactly that phrase;
4. the decoder echoed the initial prompt (long run of prompt words);
5. one n-gram repeated many times in a row: dominant -> reject, else collapse.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .config import FilterSettings
from .engines.base import Segment

log = logging.getLogger("jarvis_asr.filter")

# A word: letters/digits (NFC Vietnamese included), combining marks, inner apostrophes.
_WORD_RE = re.compile(r"[^\W_](?:[^\W_]|[̀-ͯ]|['’](?=[^\W_]))*")
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+|(?<=[。！？])|\n+")
_LOOP_DOMINANCE = 0.5  # a loop covering >= half of the words rejects the utterance
_LOOP_MAX_NGRAM = 6

# Used only if the hallucinations file cannot be read.
_FALLBACK_PHRASES = (
    "hãy subscribe cho kênh",
    "đăng ký kênh",
    "cảm ơn các bạn đã xem",
    "cảm ơn các bạn đã theo dõi",
    "thanks for watching",
    "thank you for watching",
    "subtitles by the amara.org community",
    "字幕由amara.org社区提供",
)


def tokens(text: str) -> List[Tuple[str, int, int]]:
    """(normalized word, start, end) over the NFC text; words are casefolded, apostrophes dropped."""
    text = unicodedata.normalize("NFC", text)
    return [
        (m.group(0).casefold().replace("'", "").replace("’", ""), m.start(), m.end())
        for m in _WORD_RE.finditer(text)
    ]


def words(text: str) -> List[str]:
    return [t[0] for t in tokens(text)]


def normalize(text: str) -> str:
    """Matching key: NFC, casefold, punctuation removed, single spaces."""
    return " ".join(words(text))


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:!?…])", r"\1", text)
    text = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", text)
    text = re.sub(r"^[\s,;:.!?…-]+", "", text)
    return text.strip()


@dataclass(frozen=True)
class PhraseEntry:
    words: Tuple[str, ...]
    exact: bool
    source: str


class PhraseList:
    """hallucinations.txt: one phrase per line, '#' comments, '=' prefix = exact-only entry.

    The file is re-read when its mtime changes (checked at most every 2 s).
    """

    def __init__(self, path: Optional[str] = None, lines: Optional[Iterable[str]] = None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._mtime: Optional[float] = None
        self._checked = 0.0
        self.phrases: Dict[str, List[PhraseEntry]] = {}
        self.exact: set = set()
        self.count = 0
        if lines is not None:
            self._install(self.parse(lines))
        elif path:
            self._load(initial=True)

    @staticmethod
    def parse(lines: Iterable[str]) -> List[PhraseEntry]:
        entries = []
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            exact = line.startswith("=")
            body = line[1:].strip() if exact else line
            ws = tuple(words(body))
            if ws:
                entries.append(PhraseEntry(ws, exact, body))
        return entries

    def _install(self, entries: List[PhraseEntry]) -> None:
        phrases: Dict[str, List[PhraseEntry]] = {}
        exact = set()
        for e in entries:
            if e.exact:
                exact.add(e.words)
            else:
                phrases.setdefault(e.words[0], []).append(e)
        for bucket in phrases.values():
            bucket.sort(key=lambda e: len(e.words), reverse=True)
        self.phrases, self.exact, self.count = phrases, exact, len(entries)

    def _load(self, initial: bool = False) -> None:
        try:
            mtime = os.stat(self.path).st_mtime
            with open(self.path, encoding="utf-8") as fh:
                entries = self.parse(fh)
        except OSError as exc:
            if initial:
                log.warning("hallucinations file %s unreadable (%s); using built-in list", self.path, exc)
                self._install(self.parse(_FALLBACK_PHRASES))
            return
        self._install(entries)
        self._mtime = mtime
        log.info("loaded %d hallucination entries from %s", self.count, self.path)

    def maybe_reload(self) -> None:
        if not self.path:
            return
        now = time.monotonic()
        if now - self._checked < 2.0:
            return
        with self._lock:
            self._checked = now
            try:
                mtime = os.stat(self.path).st_mtime
            except OSError:
                return
            if mtime != self._mtime:
                self._load()

    def find_phrase(self, ws: Sequence[str]) -> Optional[str]:
        """First blocklisted phrase occurring as a contiguous word run in ``ws``."""
        for i, w in enumerate(ws):
            for entry in self.phrases.get(w, ()):
                n = len(entry.words)
                if tuple(ws[i : i + n]) == entry.words:
                    return entry.source
        return None

    def is_exact(self, ws: Sequence[str]) -> bool:
        return tuple(ws) in self.exact


@dataclass
class FilterResult:
    text: str
    raw_text: str = ""
    dropped: Optional[str] = None  # reason when the whole utterance was rejected
    notes: List[str] = field(default_factory=list)  # partial removals


def _segment_reason(seg: Segment, cfg: FilterSettings) -> Optional[str]:
    lp, ns, cr = seg.avg_logprob, seg.no_speech_prob, seg.compression_ratio
    if lp is not None and cfg.logprob_min is not None and lp < cfg.logprob_min:
        return "low_logprob"
    if (
        ns is not None
        and cfg.no_speech_max is not None
        and ns > cfg.no_speech_max
        and (lp is None or lp < cfg.no_speech_logprob)
    ):
        return "no_speech_prob"
    if cr is not None and cfg.compression_max is not None and cr > cfg.compression_max:
        return "compression"
    return None


def longest_common_run(a: Sequence[str], b: Sequence[str]) -> int:
    """Length of the longest contiguous word run shared by ``a`` and ``b``."""
    best = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def find_loop(ws: Sequence[str], min_repeats: int) -> Optional[Tuple[int, int, int]]:
    """Longest (start, n, k): an n-gram (n <= 6) repeated k >= min_repeats times in a row."""
    best: Optional[Tuple[int, int, int]] = None
    best_len = 0
    total = len(ws)
    for n in range(1, _LOOP_MAX_NGRAM + 1):
        i = 0
        while i + n * min_repeats <= total:
            gram = ws[i : i + n]
            k = 1
            while i + (k + 1) * n <= total and ws[i + k * n : i + (k + 1) * n] == gram:
                k += 1
            if k >= min_repeats and k * n > best_len:
                best, best_len = (i, n, k), k * n
            i += 1
    return best


class HallucinationFilter:
    def __init__(self, cfg: FilterSettings, phrases_path: Optional[str] = None,
                 phrases: Optional[PhraseList] = None) -> None:
        self.cfg = cfg
        self.phrases = phrases if phrases is not None else PhraseList(phrases_path)

    def apply(self, segments: Sequence[Segment], prompts: Sequence[str] = ()) -> FilterResult:
        raw = "".join(s.text for s in segments).strip()
        if not self.cfg.enabled:
            return FilterResult(text=raw, raw_text=raw)
        kept: List[Segment] = []
        notes: List[str] = []
        first_reason: Optional[str] = None
        for seg in segments:
            reason = _segment_reason(seg, self.cfg)
            if reason:
                notes.append(f"segment:{reason}")
                first_reason = first_reason or reason
            else:
                kept.append(seg)
        if segments and not kept:
            return FilterResult(text="", raw_text=raw, dropped=first_reason, notes=notes)
        result = self.filter_text("".join(s.text for s in kept), prompts)
        result.raw_text = raw
        result.notes = notes + result.notes
        return result

    def filter_text(self, text: str, prompts: Sequence[str] = ()) -> FilterResult:
        """Text-only rules (also used for engines without segment scores)."""
        text = unicodedata.normalize("NFC", text).strip()
        raw = text
        if not self.cfg.enabled:
            return FilterResult(text=text, raw_text=raw)
        notes: List[str] = []
        if not words(text):
            return FilterResult("", raw, "empty", notes)

        # 3. CJK / blocklisted sentences.
        self.phrases.maybe_reload()
        kept: List[str] = []
        reason: Optional[str] = None
        for sentence in _SENTENCE_SPLIT_RE.split(text):
            if not sentence or not sentence.strip():
                continue
            ws = words(sentence)
            if self.cfg.drop_cjk and _CJK_RE.search(sentence):
                notes.append("cjk")
                reason = reason or "cjk"
                continue
            hit = self.phrases.find_phrase(ws) if ws else None
            if hit:
                notes.append(f"blocklist:{hit}")
                reason = reason or "blocklist"
                continue
            kept.append(sentence)
        if reason:
            text = _tidy(" ".join(kept))
        ws = words(text)
        if not ws:
            return FilterResult("", raw, reason or "empty", notes)
        if self.phrases.is_exact(ws):
            return FilterResult("", raw, "blocklist", notes + ["blocklist:exact"])

        # 4. Prompt echo.
        if self.cfg.prompt_echo_words > 0:
            for prompt in prompts:
                pw = words(prompt or "")
                need = max(self.cfg.prompt_echo_words, int(math.ceil(0.5 * len(pw))))
                if len(pw) >= need and longest_common_run(ws, pw) >= need:
                    return FilterResult("", raw, "prompt_echo", notes)

        # 5. Repetition loops.
        for _ in range(5):
            toks = tokens(text)
            ws = [t[0] for t in toks]
            loop = find_loop(ws, self.cfg.repeat_min)
            if loop is None:
                break
            start, n, k = loop
            if k * n >= _LOOP_DOMINANCE * len(ws):
                return FilterResult("", raw, "repetition", notes)
            cut_from, cut_to = toks[start + n][1], toks[start + k * n - 1][2]
            text = _tidy(text[:cut_from] + text[cut_to:])
            notes.append(f"loop_collapsed:{' '.join(ws[start:start + n])}x{k}")
        return FilterResult(text, raw, None, notes)
