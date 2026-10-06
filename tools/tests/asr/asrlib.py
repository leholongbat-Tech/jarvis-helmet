"""Shared helpers for the ASR evaluation tools: Vietnamese-aware normalization, WER,
command keyword accuracy, sentences.md / manifest I/O and percentiles.

Manifest format (JSON Lines, one utterance per line; paths relative to the manifest file):
    {"id": "M01", "audio": "audio/M01.wav", "reference": "Jarvis, open mask rồi bật đèn mắt lên.",
     "group": "mixed"}
Required keys: audio, reference, group (mixed | vi | en | noise). Optional: id, synthetic (bool),
source, note. Negative samples use group "noise" and reference "".
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
GROUP_BY_PREFIX = {"M": "mixed", "V": "vi", "E": "en"}
GROUP_ORDER = ("mixed", "vi", "en", "noise")
SPEECH_GROUPS = ("mixed", "vi", "en")

_APOSTROPHES = {"'", "’", "ʼ", "`"}
_TONES = "̣̀́̃̉"  # grave, acute, tilde, hook above, dot below
_OLD_TONE_RE = re.compile(r"o([" + _TONES + r"])([ae])$|u([" + _TONES + r"])(y)$")
_ROW_RE = re.compile(r"^\|\s*([MVE])(\d{2,3})\s*\|\s*(.+?)\s*\|\s*$")


# ----------------------------------------------------------------------------- normalization
def canonical_tone(word: str) -> str:
    """Unify the two Vietnamese tone-mark conventions in open oa/oe/uy syllables.

    Old style "hòa, khỏe, thủy" and new style "hoà, khoẻ, thuỷ" become the same string
    (new style). "mùa", "quý", "hoàng" are left untouched.
    """
    nfd = unicodedata.normalize("NFD", word)

    def move(m: "re.Match[str]") -> str:
        if m.group(1):
            return "o" + m.group(2) + m.group(1)
        return "u" + m.group(4) + m.group(3)

    return unicodedata.normalize("NFC", _OLD_TONE_RE.sub(move, nfd))


def normalize_text(text: str) -> str:
    """NFC, lowercase, punctuation/symbols removed, tone placement unified, single spaces."""
    text = unicodedata.normalize("NFC", text or "").casefold()
    out = []
    for ch in text:
        if ch in _APOSTROPHES:
            continue
        cat = unicodedata.category(ch)
        out.append(" " if cat[0] in ("P", "S", "Z", "C") else ch)
    return " ".join(canonical_tone(w) for w in "".join(out).split())


def tokenize(text: str) -> List[str]:
    return normalize_text(text).split()


# ----------------------------------------------------------------------------- WER
def edit_ops(ref: Sequence[str], hyp: Sequence[str]) -> Tuple[int, int, int]:
    """(substitutions, deletions, insertions) of a minimum-cost alignment."""
    n, m = len(ref), len(hyp)
    dist = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dist[i][0] = i
    for j in range(m + 1):
        dist[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dist[i][j] = min(dist[i - 1][j] + 1, dist[i][j - 1] + 1, dist[i - 1][j - 1] + cost)
    sub = dele = ins = 0
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dist[i][j] == dist[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):
            sub += ref[i - 1] != hyp[j - 1]
            i, j = i - 1, j - 1
        elif i > 0 and dist[i][j] == dist[i - 1][j] + 1:
            dele += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    return sub, dele, ins


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = tokenize(reference), tokenize(hypothesis)
    s, d, i = edit_ops(ref, hyp)
    if not ref:
        return 0.0 if not hyp else 1.0
    return (s + d + i) / len(ref)


# ----------------------------------------------------------------------------- keywords
@dataclass(frozen=True)
class Keyword:
    name: str
    forms: Tuple[Tuple[str, ...], ...]


def parse_keywords(lines: Iterable[str]) -> List[Keyword]:
    out = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        forms = tuple(tuple(tokenize(f)) for f in line.split("|") if tokenize(f))
        if forms:
            out.append(Keyword(" ".join(forms[0]), forms))
    return out


def load_keywords(path: Path) -> List[Keyword]:
    return parse_keywords(Path(path).read_text(encoding="utf-8").splitlines())


def count_form(tokens: Sequence[str], form: Sequence[str]) -> int:
    """Non-overlapping occurrences of the word sequence ``form`` in ``tokens``."""
    n, count, i = len(form), 0, 0
    while i + n <= len(tokens):
        if tuple(tokens[i : i + n]) == tuple(form):
            count += 1
            i += n
        else:
            i += 1
    return count


def keyword_stats(ref_tokens: Sequence[str], hyp_tokens: Sequence[str],
                  keywords: Sequence[Keyword]) -> Dict[str, Dict[str, int]]:
    """Per keyword present in either text: {"ref": n, "hit": n, "false_alarm": n}."""
    stats = {}
    for kw in keywords:
        r = sum(count_form(ref_tokens, f) for f in kw.forms)
        h = sum(count_form(hyp_tokens, f) for f in kw.forms)
        if r or h:
            stats[kw.name] = {"ref": r, "hit": min(r, h), "false_alarm": max(0, h - r)}
    return stats


# ----------------------------------------------------------------------------- datasets
def load_sentences(path: Path = HERE / "sentences.md") -> List[Dict[str, str]]:
    """Rows ``| M01 | text |`` of sentences.md -> [{"id", "group", "text"}]."""
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = _ROW_RE.match(line.strip())
        if m:
            rows.append({"id": m.group(1) + m.group(2), "group": GROUP_BY_PREFIX[m.group(1)],
                         "text": m.group(3)})
    return rows


def load_manifest(path: Path) -> List[Dict]:
    """Read a manifest; adds ``audio_path`` (absolute) and a default ``id``."""
    path = Path(path)
    items = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{n}: invalid JSON: {exc}") from exc
        missing = [k for k in ("audio", "reference", "group") if k not in item]
        if missing:
            raise ValueError(f"{path}:{n}: missing keys {missing}")
        if item["group"] not in GROUP_ORDER:
            raise ValueError(f"{path}:{n}: unknown group {item['group']!r} (use {GROUP_ORDER})")
        audio = Path(item["audio"])
        item["audio_path"] = str(audio if audio.is_absolute() else (path.parent / audio).resolve())
        item.setdefault("id", f"#{n}")
        items.append(item)
    return items


def write_manifest(path: Path, items: Iterable[Dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for item in items:
            clean = {k: v for k, v in item.items() if k != "audio_path"}
            fh.write(json.dumps(clean, ensure_ascii=False) + "\n")


# ----------------------------------------------------------------------------- audio
def resample(x, sr_in: int, sr_out: int):
    """Band-limited FFT resampling of a 1-D float array (same method as server/asr)."""
    import numpy as np

    if sr_in == sr_out or len(x) == 0:
        return np.asarray(x, dtype=np.float32)
    g = math.gcd(int(sr_in), int(sr_out))
    up, down = int(sr_out) // g, int(sr_in) // g
    n = len(x)
    pad = down * max(1, math.ceil(0.05 * sr_in / down))
    tail = (-(n + 2 * pad)) % down
    xp = np.concatenate([np.zeros(pad), np.asarray(x, dtype=np.float64), np.zeros(pad + tail)])
    n_in = xp.shape[0]
    n_out = n_in // down * up
    spec = np.fft.rfft(xp)
    out = np.zeros(n_out // 2 + 1, dtype=spec.dtype)
    keep = min(n_in, n_out)
    out[: keep // 2 + 1] = spec[: keep // 2 + 1]
    if keep % 2 == 0:
        out[keep // 2] *= 2.0 if n_out < n_in else 0.5
    y = np.fft.irfft(out, n_out) * (n_out / n_in)
    start = pad // down * up
    return y[start : start + min(math.ceil(n * up / down), n_out - start)].astype(np.float32)


# ----------------------------------------------------------------------------- stats
def percentile(values: Sequence[float], pct: float) -> Optional[float]:
    """Linear-interpolated percentile (numpy's default); None for no data."""
    data = sorted(v for v in values if v is not None and not math.isnan(v))
    if not data:
        return None
    if len(data) == 1:
        return float(data[0])
    rank = (len(data) - 1) * pct / 100.0
    lo, hi = math.floor(rank), math.ceil(rank)
    return float(data[lo] + (data[hi] - data[lo]) * (rank - lo))
