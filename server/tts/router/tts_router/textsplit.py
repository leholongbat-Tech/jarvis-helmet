"""Sentence splitting for language routing (Vietnamese / English text, some CJK punctuation).

A boundary is a newline, a CJK terminal mark, or a run of . ! ? ; : (plus closing quotes)
followed by whitespace. Not boundaries: decimals and times (5.02, 3,5, 14:32 - no space after
the mark), initialisms followed by a lowercase word (J.A.R.V.I.S. đang ...), known abbreviations
(Mr., TP., v.v. ...), single capital initials and list markers ("1. ").
The same rules are used by the xiaozhi provider's streaming segmenter (jarvis_stream.py).
"""
from __future__ import annotations

import re
from typing import List

TERMINALS = ".!?;:…"
CJK_TERMINALS = "。！？；："  # 。！？；：
CLOSERS = "\"'”’»)]}"
OPENERS = "\"'“‘«([{"
ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "st", "jr", "sr", "prof", "vs", "etc", "no", "fig", "approx",
    "inc", "ltd", "co", "tp", "ths", "ts", "pgs", "gs", "bs", "ks", "nxb",
})
_INITIALISM_RE = re.compile(r"(?:[A-Za-z]\.)+[A-Za-z]")


def _word_before(text: str, i: int) -> str:
    j = i
    while j > 0 and not text[j - 1].isspace():
        j -= 1
    return text[j:i].lstrip(OPENERS)


def _protected_dot(text: str, i: int, run_end: int, seg_start: int) -> bool:
    """A single '.' at text[i] followed by whitespace that still does not end a sentence."""
    word = _word_before(text, i)
    if word.lower() in ABBREVIATIONS:
        return True
    nxt = text[run_end:].lstrip()[:1]
    if _INITIALISM_RE.fullmatch(word):  # J.A.R.V.I.S, v.v, e.g
        return not nxt.isupper()
    if len(word) == 1 and word.isalpha() and word.isupper():
        # initial ("A. Einstein") unless it is a unit after a number ("5,02 V. Hết.")
        prev = text[seg_start:i - 1].split()
        return not (prev and prev[-1][:1].isdigit())
    if word.isdigit() and text[seg_start:i].strip() == word:  # list marker "1. "
        return True
    return False


def find_boundary(text: str, start: int = 0, final: bool = False) -> int:
    """Index just past the first sentence boundary at or after `start`, or -1.

    A terminal run that reaches the end of `text` is undecidable while more text may follow
    ("5." could become "5.02"): it counts only when `final` is True.
    """
    n = len(text)
    i = start
    while i < n:
        c = text[i]
        if c == "\n" or c == "\r":
            return i + 1
        if c in CJK_TERMINALS:
            j = i + 1
            while j < n and (text[j] in CJK_TERMINALS or text[j] in TERMINALS or text[j] in CLOSERS):
                j += 1
            return j
        if c in TERMINALS:
            j = i + 1
            while j < n and (text[j] in TERMINALS or text[j] in CLOSERS):
                j += 1
            if j >= n:
                return n if final else -1
            if text[j].isspace():
                if c == "." and j == i + 1 and _protected_dot(text, i, j, start):
                    i = j
                    continue
                return j
            i = j
            continue
        i += 1
    return -1


def split_sentences(text: str) -> List[str]:
    """Split a complete text into stripped, non-empty sentences."""
    out: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        cut = find_boundary(text, start, final=True)
        if cut == -1:
            cut = n
        piece = text[start:cut].strip()
        if piece:
            out.append(piece)
        start = cut
    return out
