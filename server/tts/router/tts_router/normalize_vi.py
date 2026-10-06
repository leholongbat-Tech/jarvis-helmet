"""Text normalization for TTS (Vietnamese first, light English) and English-sentence detection.

LLM replies contain digits, units, times, abbreviations, emoji and markdown that VieNeu-TTS reads
badly or not at all. `Normalizer.vi()` rewrites a Vietnamese (or mixed VI/EN) sentence into
speakable Vietnamese words; `Normalizer.en()` does the same for a pure-English sentence;
`Normalizer.is_english()` decides which of the two applies (and whether Kokoro may read it).

Pure Python, no dependencies. Readings follow the usual Northern conventions:
  21 "hai mươi mốt", 24 "hai mươi tư", 25 "hai mươi lăm", 14 "mười bốn", 15 "mười lăm",
  105 "một trăm linh năm" (zero_tens="lẻ" -> "lẻ"), 1005 "một nghìn không trăm linh năm",
  3,5 / 3.5 "ba phẩy năm", 5,02 "năm phẩy không hai".
Separator rule: a single "." or "," followed by exactly three digits is a thousands separator
("1.250 mA", "1,000"); otherwise it is a decimal mark ("5.02 V", "3,5%").
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

__all__ = [
    "Normalizer",
    "NormalizerConfig",
    "normalize_vi",
    "normalize_en",
    "is_english_sentence",
    "strip_markup",
    "vi_int",
    "en_int",
    "has_vietnamese_marks",
    "has_speakable",
]

# ---------------------------------------------------------------------------------------------
# Markup, emoji, control characters
# ---------------------------------------------------------------------------------------------

_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # pictographs, emoticons, transport, flags, supplemental symbols
    "\U0001FB00-\U0001FBFF"
    "←-⇿"  # arrows
    "⌀-⏿"  # misc technical (watch, hourglass ...)
    "①-⓿"  # enclosed alphanumerics
    "■-◿"  # geometric shapes
    "☀-➿"  # misc symbols, dingbats
    "⤀-⥿"
    "⬀-⯿"  # arrows, stars
    "〰〽㊗㊙"
    "︀-️"  # variation selectors
    "​-‏⁠﻿"  # zero-width characters
    "⃣"  # keycap
    "\U000E0020-\U000E007F"  # tags
    "©®™"  # (c) (r) tm
    "]+"
)
_CTRL_RE = re.compile(r"[\x00-\x08\x0B-\x1F\x7F]")
_CODE_FENCE_RE = re.compile(r"```.*?(?:```|$)", re.S)
_INLINE_CODE_RE = re.compile(r"`([^`\n]*)`")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.M)
_QUOTE_RE = re.compile(r"^[ \t]{0,3}>+[ \t]?", re.M)
_BULLET_RE = re.compile(r"^[ \t]*[-*+•·][ \t]+", re.M)
_ORDERED_RE = re.compile(r"^[ \t]*\d{1,2}[.)][ \t]+", re.M)
_HR_RE = re.compile(r"^[ \t]*(?:[-*_][ \t]*){3,}$", re.M)
_TABLE_SEP_RE = re.compile(r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(?:\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$", re.M)
_BOLD_RE = re.compile(r"(\*\*|__|~~)(?=\S)(.+?)(?<=\S)\1", re.S)
_ITALIC_STAR_RE = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
_ITALIC_UNDER_RE = re.compile(r"(?<![\w])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w])")


def strip_markup(text: str) -> str:
    """Remove markdown, URLs, emoji and control characters; keep the readable words.

    Newlines are kept (they separate sentences); table pipes become commas.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    text = _CODE_FENCE_RE.sub(" ", text)
    text = _IMAGE_RE.sub(" ", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _URL_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _TABLE_SEP_RE.sub("", text)
    text = _HR_RE.sub("", text)
    text = _HEADING_RE.sub("", text)
    text = _QUOTE_RE.sub("", text)
    text = _BULLET_RE.sub("", text)
    text = _ORDERED_RE.sub("", text)
    text = _BOLD_RE.sub(r"\2", text)
    text = _ITALIC_STAR_RE.sub(r"\1", text)
    text = _ITALIC_UNDER_RE.sub(r"\1", text)
    text = text.replace("|", ", ")
    text = _EMOJI_RE.sub(" ", text)
    text = _CTRL_RE.sub(" ", text)
    text = text.replace("…", "...")
    text = re.sub(r"[  ]{2,}", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def has_speakable(text: str) -> bool:
    return any(ch.isalnum() for ch in text)


# ---------------------------------------------------------------------------------------------
# Vietnamese numbers
# ---------------------------------------------------------------------------------------------

_VI_DIGITS = ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")


def _vi_triple(n: int, full: bool, zero_tens: str) -> list:
    """Read 0 < n < 1000. `full`: a higher group precedes, so 5 -> "không trăm linh năm"."""
    hundreds, rest = divmod(n, 100)
    tens, units = divmod(rest, 10)
    words = []
    if hundreds or full:
        words += [_VI_DIGITS[hundreds], "trăm"]
    if tens == 0:
        if units:
            if hundreds or full:
                words.append(zero_tens)
            words.append(_VI_DIGITS[units])
        return words
    if tens == 1:
        words.append("mười")
    else:
        words += [_VI_DIGITS[tens], "mươi"]
    if units == 0:
        return words
    if units == 1:
        words.append("mốt" if tens >= 2 else "một")
    elif units == 4:
        words.append("tư" if tens >= 2 else "bốn")
    elif units == 5:
        words.append("lăm")
    else:
        words.append(_VI_DIGITS[units])
    return words


def _vi_read(n: int, full: bool, zero_tens: str, thousand: str) -> list:
    if n >= 1_000_000_000:
        high, low = divmod(n, 1_000_000_000)
        words = _vi_read(high, full, zero_tens, thousand) + ["tỷ"]
        if low:
            words += _vi_read(low, True, zero_tens, thousand)
        return words
    names = ("", thousand, "triệu")
    groups = (n % 1000, (n // 1000) % 1000, n // 1_000_000)
    top = max(k for k in range(3) if groups[k])
    words = []
    for k in range(top, -1, -1):
        if groups[k] == 0:
            continue
        words += _vi_triple(groups[k], full or k != top, zero_tens)
        if names[k]:
            words.append(names[k])
    return words


def vi_int(n: int, zero_tens: str = "linh", thousand: str = "nghìn") -> str:
    """Vietnamese reading of an integer (any size; groups above 10^9 repeat "tỷ")."""
    if n < 0:
        return "âm " + vi_int(-n, zero_tens, thousand)
    if n == 0:
        return "không"
    return " ".join(_vi_read(n, False, zero_tens, thousand))


def vi_digits(digits: str) -> str:
    return " ".join(_VI_DIGITS[int(c)] for c in digits if c.isdigit())


# ---------------------------------------------------------------------------------------------
# English numbers
# ---------------------------------------------------------------------------------------------

_EN_ONES = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
            "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
            "eighteen", "nineteen")
_EN_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
_EN_SCALES = ((10**12, "trillion"), (10**9, "billion"), (10**6, "million"), (1000, "thousand"))
_EN_ORDINAL_IRREGULAR = {"one": "first", "two": "second", "three": "third", "five": "fifth",
                         "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}
_EN_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
              "September", "October", "November", "December")


def _en_below_1000(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(f"{_EN_ONES[hundreds]} hundred")
    if rest:
        if rest < 20:
            words = _EN_ONES[rest]
        else:
            tens, units = divmod(rest, 10)
            words = _EN_TENS[tens] + (f"-{_EN_ONES[units]}" if units else "")
        parts.append(("and " if hundreds else "") + words)
    return " ".join(parts)


def en_int(n: int) -> str:
    """British English reading ("one hundred and five")."""
    if n < 0:
        return "minus " + en_int(-n)
    if n < 1000:
        return _en_below_1000(n) if n else "zero"
    parts = []
    rest = n
    for value, name in _EN_SCALES:
        if rest >= value:
            high, rest = divmod(rest, value)
            parts.append(f"{en_int(high)} {name}")
    if rest:
        parts.append(("and " if rest < 100 else "") + _en_below_1000(rest))
    return " ".join(parts)


def en_digits(digits: str) -> str:
    return " ".join(_EN_ONES[int(c)] for c in digits if c.isdigit())


def en_ordinal(n: int) -> str:
    words = en_int(n)
    head, sep, last = words.rpartition(" ")
    if "-" in last:
        h2, _, l2 = last.rpartition("-")
        last = h2 + "-" + _en_ordinal_word(l2)
    else:
        last = _en_ordinal_word(last)
    return f"{head}{sep}{last}"


def _en_ordinal_word(w: str) -> str:
    if w in _EN_ORDINAL_IRREGULAR:
        return _EN_ORDINAL_IRREGULAR[w]
    if w.endswith("y"):
        return w[:-1] + "ieth"
    return w + "th"


def en_year(y: int) -> str:
    if 2000 <= y <= 2009 or y % 1000 == 0 or y < 1000 or y > 9999:
        return en_int(y)
    high, low = divmod(y, 100)
    if low == 0:
        return f"{en_int(high)} hundred"
    if low < 10:
        return f"{en_int(high)} oh {en_int(low)}"
    return f"{en_int(high)} {en_int(low)}"


# ---------------------------------------------------------------------------------------------
# Number tokens
# ---------------------------------------------------------------------------------------------

def _thousand_groups(groups: list) -> bool:
    return 1 <= len(groups[0]) <= 3 and all(len(g) == 3 for g in groups[1:])


def parse_number(token: str, convention: str = "vi") -> Optional[Tuple[str, str]]:
    """Split a digit token with separators into (integer digits, fraction digits).

    Returns None when the token is not a plain number (versions, IP addresses).
    """
    dots, commas = token.count("."), token.count(",")
    if not dots and not commas:
        return token, ""
    if dots and commas:
        dec = "," if token.rfind(",") > token.rfind(".") else "."
        thou = "." if dec == "," else ","
        if token.count(dec) != 1:
            return None
        int_part, frac = token.split(dec)
        groups = int_part.split(thou)
        return ("".join(groups), frac) if _thousand_groups(groups) else None
    sep = "." if dots else ","
    parts = token.split(sep)
    if len(parts) > 2:
        return ("".join(parts), "") if _thousand_groups(parts) else None
    int_part, frac = parts
    thousands = len(frac) == 3 and 1 <= len(int_part) <= 3 and int_part != "0"
    if convention == "en" and sep == ".":
        thousands = False
    if thousands:
        return int_part + frac, ""
    return int_part, frac


# ---------------------------------------------------------------------------------------------
# Tables (overridable through NormalizerConfig / TTS_ABBREV_FILE)
# ---------------------------------------------------------------------------------------------

VI_ABBREVIATIONS: Dict[str, str] = {
    "J.A.R.V.I.S": "Giắc vít",
    "JARVIS": "Giắc vít",
    "Jarvis": "Giắc vít",
    "jarvis": "giắc vít",
    "AI": "ây ai",
    "WiFi": "wai phai",
    "Wifi": "wai phai",
    "wifi": "wai phai",
    "WIFI": "wai phai",
    "Wi-Fi": "wai phai",
    "USB": "u ét bê",
    "OK": "ô kê",
    "Ok": "ô kê",
    "ok": "ô kê",
    "Okay": "ô kê",
    "okay": "ô kê",
    "LED": "lét",
    "HUD": "hắt",
    "GPS": "gi pi ét",
    "SOS": "ét ô ét",
    "TV": "ti vi",
    "GPU": "gi pi u",
    "CPU": "xi pi u",
    "PTT": "pi ti ti",
    "Bluetooth": "blu tút",
    "bluetooth": "blu tút",
    "v.v.": "vân vân",
    "v.v": "vân vân",
    "TP.": "thành phố",
    "Tp.": "thành phố",
}

EN_ABBREVIATIONS: Dict[str, str] = {
    "J.A.R.V.I.S": "Jarvis",
    "JARVIS": "Jarvis",
    "Wi-Fi": "Wi-Fi",
    "WiFi": "Wi-Fi",
    "Wifi": "Wi-Fi",
    "e.g.": "for example",
    "i.e.": "that is",
    "etc.": "et cetera",
}

# unit -> Vietnamese reading. Matched right after a number, longest key first.
VI_UNITS: Dict[str, str] = {
    "km/h": "ki lô mét trên giờ",
    "m/s": "mét trên giây",
    "mAh": "mi li ampe giờ",
    "kWh": "ki lô oát giờ",
    "Wh": "oát giờ",
    "Ah": "ampe giờ",
    "mV": "mi li vôn",
    "kV": "ki lô vôn",
    "V": "vôn",
    "mA": "mi li ampe",
    "A": "ampe",
    "mW": "mi li oát",
    "kW": "ki lô oát",
    "W": "oát",
    "°C": "độ xê",
    "℃": "độ xê",
    "°F": "độ ép",
    "°": "độ",
    "%": "phần trăm",
    "km²": "ki lô mét vuông",
    "m²": "mét vuông",
    "m³": "mét khối",
    "km": "ki lô mét",
    "cm": "xen ti mét",
    "mm": "mi li mét",
    "m": "mét",
    "kg": "ki lô gam",
    "mg": "mi li gam",
    "g": "gam",
    "TB": "tê ra bai",
    "GB": "gi ga bai",
    "MB": "mê ga bai",
    "KB": "ki lô bai",
    "kB": "ki lô bai",
    "Mbps": "mê ga bít trên giây",
    "kbps": "ki lô bít trên giây",
    "GHz": "gi ga héc",
    "MHz": "mê ga héc",
    "kHz": "ki lô héc",
    "Hz": "héc",
    "ms": "mi li giây",
    "s": "giây",
    "h": "giờ",
    "dB": "đề xi ben",
    "VNĐ": "đồng",
    "VND": "đồng",
    "đ": "đồng",
    "₫": "đồng",
    "USD": "đô la Mỹ",
}

# unit -> (singular, plural) English reading.
EN_UNITS: Dict[str, Tuple[str, str]] = {
    "km/h": ("kilometre per hour", "kilometres per hour"),
    "m/s": ("metre per second", "metres per second"),
    "mAh": ("milliamp hour", "milliamp hours"),
    "kWh": ("kilowatt hour", "kilowatt hours"),
    "Wh": ("watt hour", "watt hours"),
    "mV": ("millivolt", "millivolts"),
    "kV": ("kilovolt", "kilovolts"),
    "V": ("volt", "volts"),
    "mA": ("milliamp", "milliamps"),
    "A": ("amp", "amps"),
    "kW": ("kilowatt", "kilowatts"),
    "W": ("watt", "watts"),
    "°C": ("degree Celsius", "degrees Celsius"),
    "℃": ("degree Celsius", "degrees Celsius"),
    "°F": ("degree Fahrenheit", "degrees Fahrenheit"),
    "°": ("degree", "degrees"),
    "%": ("percent", "percent"),
    "km": ("kilometre", "kilometres"),
    "cm": ("centimetre", "centimetres"),
    "mm": ("millimetre", "millimetres"),
    "m": ("metre", "metres"),
    "kg": ("kilogram", "kilograms"),
    "g": ("gram", "grams"),
    "GB": ("gigabyte", "gigabytes"),
    "MB": ("megabyte", "megabytes"),
    "KB": ("kilobyte", "kilobytes"),
    "kB": ("kilobyte", "kilobytes"),
    "GHz": ("gigahertz", "gigahertz"),
    "MHz": ("megahertz", "megahertz"),
    "kHz": ("kilohertz", "kilohertz"),
    "Hz": ("hertz", "hertz"),
    "ms": ("millisecond", "milliseconds"),
    "dB": ("decibel", "decibels"),
    "USD": ("dollar", "dollars"),
}

_COMMON_FRACTIONS = {(1, 2), (1, 3), (2, 3), (1, 4), (3, 4)}
# "x/y" next to these words is a score ("điểm 9/10", "4/5 sao"), after these a date ("từ 1/4").
_SCORE_NEXT = frozenset({"điểm", "sao"})
_SCORE_PREV = frozenset({"điểm", "chấm", "được", "đạt"})
_DATE_CUES = frozenset({"từ", "đến", "tới", "hạn", "trước", "sau", "vào", "hôm"})
# NUM (op NUM)+ = NUM: a dash inside such a chain is a minus sign, not a range.
_ARITH_CHAIN_RE = re.compile(
    r"\d+(?:[.,]\d+)*(?:[ \t]*[-−–+×x*/÷][ \t]*\d+(?:[.,]\d+)*)+[ \t]*=[ \t]*[-−–]?\d+(?:[.,]\d+)*")
_ARITH_MINUS_RE = re.compile(r"(?<=\d)[ \t]*[-−–][ \t]*(?=\d)")
_WORD_BEFORE_RE = re.compile(r"([^\W\d_]+)[^\w]*$")
_WORD_AFTER_RE = re.compile(r"^[^\w]*([^\W\d_]+)")
# "phòng 12A", "lớp 10A", "quốc lộ 1A", "mạng 4g": after these words a single-letter "unit" is a
# label, not amperes/volts/grams.
_VI_DESIGNATORS = frozenset({
    "phòng", "tầng", "lớp", "căn", "nhà", "số", "khu", "dãy", "lô", "block", "lộ", "mạng", "tòa",
    "toà", "cổng", "bàn", "ghế", "hẻm", "ngõ", "ngách", "kiệt", "sảnh", "tuyến", "mã", "cửa", "xe",
})
_EN_DESIGNATORS = frozenset({
    "room", "floor", "class", "block", "gate", "flat", "apartment", "unit", "suite", "seat",
    "platform", "route", "highway", "level", "bay", "building", "number", "no",
})
# Phone numbers written in groups: 0912 345 678, 0912.345.678, 028 3822 1234, 1900 6060.
_PHONE_RE = re.compile(r"(?<![\d.,])(?:0\d{2,3}(?:[ .-]\d{2,4}){1,3}|1[89]00(?:[ .-]\d{3,4}){1,2})(?![\d])")
# Space as thousands separator: 1 000 000, 2 500.
_SPACE_THOUSANDS_RE = re.compile(r"(?<![\d.,])\d{1,3}(?: \d{3})+(?![\d]|[.,]\d)")

# ---------------------------------------------------------------------------------------------
# English-sentence detection
# ---------------------------------------------------------------------------------------------

_VI_MARKS = frozenset(
    "ăâđêôơư"
    "àáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ"
)

_EN_COMMON = frozenset("""
a an the and or but if then than so because of to in on at by for with from as into over under
about after before again also only just very too not no yes is are was were be been being am do
does did done have has had having i you he she it we they me him her us them my your his its our
their mine yours this that these those what which who whom whose where when why how all any some
each every both few more most other such own same can cannot could will would shall should may
might must let lets get got go going gone come back up down out off on now here there today
tonight tomorrow sir madam ma'am please thank thanks hello hi hey good morning afternoon evening
night welcome ready online offline system systems mode mask eyes eye open opened opening close
closed closing power battery level full low engaged activated deactivated status everything
nothing something right left well sure course afraid i'm i'll i've i'd you're you've you'll it's
that's there's here's what's let's can't won't don't doesn't isn't aren't wasn't weren't okay ok
one two three four five six seven eight nine ten hundred thousand million percent volts degrees
minutes seconds hours combat stealth salute target weapons armor armour shield suit protocol all
""".split())

_NEUTRAL_NAMES = frozenset({"jarvis", "j.a.r.v.i.s"})

_VI_ONSETS = ("ngh", "ng", "nh", "ch", "gh", "gi", "kh", "ph", "qu", "th", "tr",
              "b", "c", "d", "g", "h", "k", "l", "m", "n", "p", "r", "s", "t", "v", "x")
_VI_CODAS = frozenset({"", "c", "ch", "m", "n", "ng", "nh", "p", "t"})
_VOWELS_RE = re.compile(r"[aeiouy]+")


def has_vietnamese_marks(text: str) -> bool:
    return any(ch.lower() in _VI_MARKS for ch in text)


def looks_vietnamese_syllable(word: str) -> bool:
    """True if an ASCII word could be one Vietnamese syllable written without diacritics."""
    w = word.lower()
    if not w.isalpha() or not w.isascii() or any(c in "fjwz" for c in w):
        return False
    rest = w
    for onset in _VI_ONSETS:
        if w.startswith(onset) and len(w) > len(onset):
            rest = w[len(onset):]
            break
    m = _VOWELS_RE.match(rest)
    if not m or len(m.group(0)) > 3:
        return False
    return rest[m.end():] in _VI_CODAS


def english_word_counts(text: str) -> Tuple[int, int]:
    """(English-looking words, Vietnamese-looking words); acronyms, names, single letters are neutral."""
    en = vi = 0
    for tok in re.findall(r"[A-Za-z]+(?:\.[A-Za-z])*(?:'[A-Za-z]+)?", text):
        low = tok.lower()
        if low in _NEUTRAL_NAMES or "." in tok:
            continue
        if len(tok) == 1 and low not in ("i", "a"):
            continue
        if tok.isupper() and 2 <= len(tok) <= 5 and low not in _EN_COMMON:
            continue
        if low in _EN_COMMON or not looks_vietnamese_syllable(low.replace("'", "")):
            en += 1
        else:
            vi += 1
    return en, vi


# ---------------------------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------------------------

@dataclass
class NormalizerConfig:
    zero_tens: str = "linh"  # 105 -> "một trăm linh năm" ("lẻ" in the South)
    thousand: str = "nghìn"  # or "ngàn"
    vi_abbreviations: Dict[str, str] = field(default_factory=lambda: dict(VI_ABBREVIATIONS))
    en_abbreviations: Dict[str, str] = field(default_factory=lambda: dict(EN_ABBREVIATIONS))
    english_min_words: int = 2
    english_min_ratio: float = 0.6

    def load_overrides(self, path: str) -> None:
        """Merge a JSON file {"vi": {...}, "en": {...}} (or a flat {...} for "vi")."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("abbreviation file must contain a JSON object")
        if "vi" in data or "en" in data:
            self.vi_abbreviations.update({str(k): str(v) for k, v in (data.get("vi") or {}).items()})
            self.en_abbreviations.update({str(k): str(v) for k, v in (data.get("en") or {}).items()})
        else:
            self.vi_abbreviations.update({str(k): str(v) for k, v in data.items()})


_LETTER = r"A-Za-zÀ-ỹĐđ"
_NUM = r"\d+(?:[.,]\d+)*"


def _abbreviation_regex(table: Dict[str, str]) -> Optional["re.Pattern[str]"]:
    if not table:
        return None
    alts = []
    for key in sorted(table, key=len, reverse=True):
        alt = re.escape(key)
        if "." in key and not key.endswith("."):
            alt += r"\.?"  # "J.A.R.V.I.S." - the final dot belongs to the initialism
        alts.append(alt)
    return re.compile(r"(?<![\w.])(?:" + "|".join(alts) + r")(?!\w)")


def _unit_alternation(units) -> str:
    return "|".join(re.escape(u) for u in sorted(units, key=len, reverse=True))


class Normalizer:
    def __init__(self, config: Optional[NormalizerConfig] = None):
        self.cfg = config or NormalizerConfig()
        self._vi_abbr_re = _abbreviation_regex(self.cfg.vi_abbreviations)
        self._en_abbr_re = _abbreviation_regex(self.cfg.en_abbreviations)
        self._vi_unit_re = re.compile(
            r"(?<![\w.,])(" + _NUM + r")\s?(" + _unit_alternation(VI_UNITS) + r")(?![" + _LETTER + r"\d])")
        self._en_unit_re = re.compile(
            r"(?<![\w.,])(" + _NUM + r")\s?(" + _unit_alternation(EN_UNITS) + r")(?![" + _LETTER + r"\d])")

    # ----- public API -----------------------------------------------------------------------

    def is_english(self, text: str) -> bool:
        """Pure-English sentence: no Vietnamese letters and mostly English words."""
        if has_vietnamese_marks(text):
            return False
        en, vi = english_word_counts(text)
        return en >= self.cfg.english_min_words and en >= self.cfg.english_min_ratio * (en + vi)

    def vi(self, text: str) -> str:
        """Normalize a Vietnamese or mixed VI/EN sentence into speakable Vietnamese."""
        text = strip_markup(text).replace("\n", ". ")
        if not text:
            return ""
        text = self._abbreviations(text, self._vi_abbr_re, self.cfg.vi_abbreviations)
        text = self._phones(text, vi_digits)
        text = _SPACE_THOUSANDS_RE.sub(lambda m: m.group(0).replace(" ", ""), text)
        text = re.sub(r"(?:\b(ngày|Ngày)\s+)?(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)", self._vi_iso_date, text)
        text = re.sub(r"(?:\b(ngày|Ngày)\s+)?(?<![\d/.])(\d{1,2})([/.-])(\d{1,2})\3(\d{4})(?![\d/])",
                      self._vi_dmy_date, text)
        text = self._arithmetic(text, " trừ ")
        text = self._ranges(text, " đến ")
        text = re.sub(r"(?<![\d:])([01]?\d|2[0-4]):([0-5]\d)(?::([0-5]\d))?(?![\d:])", self._vi_time, text)
        text = re.sub(r"(?<![\w.,])([01]?\d|2[0-4])h([0-5]\d)?(?![\w])", self._vi_time_h, text)
        text = re.sub(r"(?:\b(ngày|Ngày)\s+)?(?<![\d/.,])(\d{1,2})/(\d{1,2})(?![\d/]|[.,]\d)",
                      self._vi_dm_date, text)
        text = re.sub(r"\b[Qq]([1-4])\s?/\s?(\d{4})(?![\d/])",
                      lambda m: f"quý {self._int(int(m.group(1)))} năm {self._int(int(m.group(2)))}", text)
        text = re.sub(r"(?:\b([Tt]háng)\s+)?(?<![\w/.,])(\d{1,2})/(\d{4})(?![\d/])", self._vi_month_year, text)
        text = re.sub(r"\$\s?(" + _NUM + r")", lambda m: f"{self._vi_number(m.group(1))} đô la", text)
        text = re.sub(r"(?<![\w.,)])[-−–](?=\d)", "âm ", text)
        text = re.sub(r"\b(thứ|Thứ|hạng|Hạng)\s+(\d{1,2})(?!\d|[.,]\d)", self._vi_ordinal, text)
        text = re.sub(r"\b(tháng|Tháng)\s+(\d{1,2})(?!\d|[.,]\d)",
                      lambda m: f"{m.group(1)} {self._vi_month(int(m.group(2)))}", text)
        text = self._vi_unit_re.sub(self._vi_unit, text)
        text = re.sub(r"(?<![\w.,])(\d{1,3})k(?![" + _LETTER + r"\d])",
                      lambda m: f"{self._int(int(m.group(1)))} {self.cfg.thousand}", text)
        text = self._operators(text, plus=" cộng ", times=" nhân ", equals=" bằng ", over=" trên ",
                               negative="âm")
        text = re.sub(r"\d+(?:[.,]\d+)*", self._vi_number_match, text)
        return self._cleanup(text, amp=" và ", at=" a còng ", percent=" phần trăm")

    def en(self, text: str) -> str:
        """Normalize a pure-English sentence (numbers, units, times, dates in English words)."""
        text = strip_markup(text).replace("\n", ". ")
        if not text:
            return ""
        text = self._abbreviations(text, self._en_abbr_re, self.cfg.en_abbreviations)
        text = self._phones(text, en_digits)
        text = re.sub(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)",
                      lambda m: self._en_date(int(m.group(3)), int(m.group(2)), int(m.group(1)), m.group(0)), text)
        text = re.sub(r"(?<![\d/.])(\d{1,2})([/.-])(\d{1,2})\2(\d{4})(?![\d/])",
                      lambda m: self._en_date(int(m.group(1)), int(m.group(3)), int(m.group(4)), m.group(0)), text)
        text = self._arithmetic(text, " minus ")
        text = self._ranges(text, " to ")
        text = re.sub(r"(?<![\d:])([01]?\d|2[0-4]):([0-5]\d)(?::([0-5]\d))?(?![\d:])", self._en_time, text)
        text = re.sub(r"\$\s?(" + _NUM + r")", self._en_dollars, text)
        text = re.sub(r"(?<![\w.,)])[-−–](?=\d)", "minus ", text)
        text = self._en_unit_re.sub(self._en_unit, text)
        text = self._operators(text, plus=" plus ", times=" times ", equals=" equals ", over=" over ",
                               negative="minus")
        text = re.sub(r"\d+(?:[.,]\d+)*", self._en_number_match, text)
        return self._cleanup(text, amp=" and ", at=" at ", percent=" percent")

    # ----- shared helpers -------------------------------------------------------------------

    @staticmethod
    def _abbreviations(text: str, regex, table: Dict[str, str]) -> str:
        if regex is None:
            return text

        def repl(m: "re.Match[str]") -> str:
            token = m.group(0)
            key = token if token in table else token[:-1]
            out = table.get(key, token)
            if len(token) > len(key):  # consumed the dot after an initialism
                rest = m.string[m.end():]
                nxt = rest.lstrip()[:1]
                if not nxt or (rest[:1].isspace() and nxt.isupper()):
                    out += "."  # it also ended the sentence
            return out

        return regex.sub(repl, text)

    @staticmethod
    def _phones(text: str, digits) -> str:
        """Read grouped phone numbers digit by digit, a short pause between the groups."""
        def repl(m: "re.Match[str]") -> str:
            groups = re.split(r"[ .-]", m.group(0))
            if not 8 <= sum(len(g) for g in groups) <= 11:
                return m.group(0)
            return ", ".join(digits(g) for g in groups)

        return _PHONE_RE.sub(repl, text)

    @staticmethod
    def _arithmetic(text: str, minus: str) -> str:
        # "10 - 3 = 7", "10-3=7": inside a chain that ends in "= NUM" the dash means minus (the
        # sign of a negative result, "= -2", stays and is read by the negative-number rule).
        return _ARITH_CHAIN_RE.sub(lambda m: _ARITH_MINUS_RE.sub(minus, m.group(0)), text)

    @staticmethod
    def _neighbour_words(m: "re.Match[str]") -> Tuple[str, str]:
        """Lower-cased word just before and just after a match ("" at the edges)."""
        before = _WORD_BEFORE_RE.search(m.string[:m.start()])
        after = _WORD_AFTER_RE.search(m.string[m.end():])
        return (before.group(1).lower() if before else "", after.group(1).lower() if after else "")

    @staticmethod
    def _ranges(text: str, word: str) -> str:
        # "20-30%", "3V-5V", "14:00-15:00": a dash between two numbers reads as "to".
        return re.sub(r"(\d(?:%|°C|°|[A-Za-z]{1,4})?)[ \t]*[-–—][ \t]*(?=\d)", r"\1" + word, text)

    @staticmethod
    def _operators(text: str, plus: str, times: str, equals: str, over: str, negative: str = "") -> str:
        text = re.sub(r"(?<=\d)[ \t]*\+[ \t]*(?=\d)", plus, text)
        text = re.sub(r"(?<=\d)[ \t]*[×x*][ \t]*(?=\d)", times, text)
        # "= -2" was already rewritten to "= âm 2" / "= minus 2" by the negative-number rule.
        neg = r"|" + re.escape(negative.strip()) + r"\b" if negative.strip() else ""
        text = re.sub(r"(?<=\d)[ \t]*=[ \t]*(?=\d" + neg + r")", equals, text)
        text = re.sub(r"(?<=\d)[ \t]*[/÷][ \t]*(?=\d)", over, text)
        return text

    @staticmethod
    def _cleanup(text: str, amp: str, at: str, percent: str) -> str:
        text = text.replace("&", amp).replace("@", at).replace("%", percent)
        text = re.sub(r"[\"“”«»„`´^~|<>{}\[\]\\*_#=+]", " ", text)
        text = re.sub(r"(?<![A-Za-zÀ-ỹĐđ])['‘’]|['‘’](?![A-Za-zÀ-ỹĐđ])", " ", text)
        text = re.sub(r"[()]", ", ", text)
        text = re.sub(r"\s*/\s*", " ", text)
        text = re.sub(r"(?<=[A-Za-zÀ-ỹĐđ0-9])-(?=[A-Za-zÀ-ỹĐđ])", " ", text)
        text = re.sub(r"\s[-–—]+(?=\s)", ",", text)
        text = re.sub(r"[-–—]{2,}", ", ", text)
        text = re.sub(r"([!?;:,])(?:\s*\1)+", r"\1", text)
        text = re.sub(r"\.{4,}", "...", text)
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)
        text = re.sub(r",(?:\s*,)+", ",", text)
        text = re.sub(r"([,;:])(?=[A-Za-zÀ-ỹĐđ])", r"\1 ", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
        text = re.sub(r"^[\s,.;:!?\-]+", "", text)
        # keep a final , ; : (prosody hint: the reply continues in the next segment)
        text = re.sub(r"[\s\-]+$", "", text)
        return text

    def _int(self, n: int) -> str:
        return vi_int(n, self.cfg.zero_tens, self.cfg.thousand)

    # ----- Vietnamese pieces ----------------------------------------------------------------

    def _vi_number(self, token: str) -> str:
        """Read a digit token such as 12, 1.234.567, 3,5, 5.02 (no surrounding context)."""
        if len(token) > 1 and token[0] == "0" and token.isdigit():
            return vi_digits(token)  # phone numbers, codes
        parsed = parse_number(token, "vi")
        if parsed is None:
            return " chấm ".join(self._vi_number(p) for p in re.split(r"[.,]", token) if p)
        int_part, frac = parsed
        if len(int_part) > 15:
            return vi_digits(int_part)
        words = self._int(int(int_part))
        if frac:
            if len(frac) <= 2 and not frac.startswith("0"):
                words += " phẩy " + self._int(int(frac))
            else:
                words += " phẩy " + vi_digits(frac)
        return words

    def _vi_number_match(self, m: "re.Match[str]") -> str:
        token = m.group(0)
        before = m.string[m.start() - 1] if m.start() else ""
        after = m.string[m.end()] if m.end() < len(m.string) else ""
        if before.isalpha():
            # model numbers / codes glued to letters: ESP32 -> "ESP ba hai", v2.5 -> "v hai chấm năm"
            if any(c in token for c in ".,"):
                words = " chấm ".join(vi_digits(p) if len(p) > 1 else self._int(int(p))
                                      for p in re.split(r"[.,]", token) if p)
            else:
                words = vi_digits(token)
            return " " + words + (" " if after.isalpha() else "")
        return self._vi_number(token) + (" " if after.isalpha() else "")

    def _vi_unit(self, m: "re.Match[str]") -> str:
        number, unit = m.group(1), m.group(2)
        if len(unit) == 1 and unit.isalpha() and self._neighbour_words(m)[0] in _VI_DESIGNATORS:
            return f"{self._vi_number(number)} {unit.upper()}"  # "phòng 12A" -> "phòng mười hai A"
        return f"{self._vi_number(number)} {VI_UNITS[unit]}"

    def _vi_month(self, month: int) -> str:
        return "tư" if month == 4 else self._int(month)

    def _vi_ordinal(self, m: "re.Match[str]") -> str:
        n = int(m.group(2))
        word = {1: "nhất", 4: "tư"}.get(n, self._int(n))
        return f"{m.group(1)} {word}"

    def _vi_date_words(self, day: int, month: int, year: Optional[int], cue: Optional[str]) -> str:
        out = f"{cue or 'ngày'} {self._int(day)} tháng {self._vi_month(month)}"
        if year is not None:
            out += f" năm {self._int(year)}"
        return out

    def _vi_iso_date(self, m: "re.Match[str]") -> str:
        year, month, day = int(m.group(2)), int(m.group(3)), int(m.group(4))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return self._vi_date_words(day, month, year, m.group(1))
        return m.group(0)

    def _vi_dmy_date(self, m: "re.Match[str]") -> str:
        day, month, year = int(m.group(2)), int(m.group(4)), int(m.group(5))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return self._vi_date_words(day, month, year, m.group(1))
        return m.group(0)

    def _vi_dm_date(self, m: "re.Match[str]") -> str:
        cue, day, month = m.group(1), int(m.group(2)), int(m.group(3))
        prev, nxt = self._neighbour_words(m)
        if not cue and (nxt in _SCORE_NEXT or prev in _SCORE_PREV):
            return f"{self._int(day)} trên {self._int(month)}"  # "Điểm 9/10", "4/5 sao"
        if not cue and (day, month) in _COMMON_FRACTIONS and prev not in _DATE_CUES:
            return f"{self._int(day)} phần {self._int(month)}"
        if 1 <= month <= 12 and 1 <= day <= 31:
            return self._vi_date_words(day, month, None, cue)
        return m.group(0)

    def _vi_month_year(self, m: "re.Match[str]") -> str:
        cue, month, year = m.group(1), int(m.group(2)), int(m.group(3))
        if 1 <= month <= 12:
            return f"{cue or 'tháng'} {self._vi_month(month)} năm {self._int(year)}"
        return m.group(0)

    def _vi_time(self, m: "re.Match[str]") -> str:
        hour, minute, second = int(m.group(1)), int(m.group(2)), m.group(3)
        out = f"{self._int(hour)} giờ"
        if minute:
            out += f" {self._int(minute)} phút"
        if second and int(second):
            out += f" {self._int(int(second))} giây"
        return out

    def _vi_time_h(self, m: "re.Match[str]") -> str:
        out = f"{self._int(int(m.group(1)))} giờ"
        if m.group(2) and int(m.group(2)):
            out += f" {self._int(int(m.group(2)))} phút"
        return out

    # ----- English pieces -------------------------------------------------------------------

    def _en_number(self, token: str) -> str:
        if len(token) > 1 and token[0] == "0" and token.isdigit():
            return en_digits(token)
        parsed = parse_number(token, "en")
        if parsed is None:
            return " point ".join(self._en_number(p) for p in re.split(r"[.,]", token) if p)
        int_part, frac = parsed
        if len(int_part) > 15:
            return en_digits(int_part)
        words = en_int(int(int_part))
        if frac:
            words += " point " + en_digits(frac)
        return words

    def _en_number_match(self, m: "re.Match[str]") -> str:
        token = m.group(0)
        before = m.string[m.start() - 1] if m.start() else ""
        after = m.string[m.end()] if m.end() < len(m.string) else ""
        if before.isalpha():
            return " " + en_digits(token) + (" " if after.isalpha() else "")
        if token.isdigit() and len(token) == 4 and 1900 <= int(token) <= 2099:
            return en_year(int(token)) + (" " if after.isalpha() else "")  # most likely a year
        return self._en_number(token) + (" " if after.isalpha() else "")

    def _en_unit(self, m: "re.Match[str]") -> str:
        number = m.group(1)
        if len(m.group(2)) == 1 and m.group(2).isalpha() and self._neighbour_words(m)[0] in _EN_DESIGNATORS:
            return f"{self._en_number(number)} {m.group(2).upper()}"  # "Room 12A" -> "Room twelve A"
        singular, plural = EN_UNITS[m.group(2)]
        return f"{self._en_number(number)} {singular if number == '1' else plural}"

    def _en_dollars(self, m: "re.Match[str]") -> str:
        number = m.group(1)
        return f"{self._en_number(number)} {'dollar' if number == '1' else 'dollars'}"

    @staticmethod
    def _en_time(m: "re.Match[str]") -> str:
        hour, minute = int(m.group(1)), int(m.group(2))
        if minute == 0:
            return f"{en_int(hour)} hundred" if hour >= 13 else f"{en_int(hour)} o'clock"
        if minute < 10:
            return f"{en_int(hour)} oh {en_int(minute)}"
        return f"{en_int(hour)} {en_int(minute)}"

    @staticmethod
    def _en_date(day: int, month: int, year: int, original: str) -> str:
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return original
        return f"the {en_ordinal(day)} of {_EN_MONTHS[month - 1]}, {en_year(year)}"


_DEFAULT = Normalizer()


def normalize_vi(text: str) -> str:
    return _DEFAULT.vi(text)


def normalize_en(text: str) -> str:
    return _DEFAULT.en(text)


def is_english_sentence(text: str) -> bool:
    return _DEFAULT.is_english(text)
