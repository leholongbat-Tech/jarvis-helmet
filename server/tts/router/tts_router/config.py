"""Router settings from environment variables (see server/tts/README.md)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional


def _get(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(_get(env, name, str(default)))
    except ValueError:
        return default


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(_get(env, name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    vieneu_url: str = "http://vieneu:8000"
    vieneu_api_key: str = ""
    vieneu_model: str = "vieneu-v3-turbo"
    kokoro_url: str = ""  # empty = Kokoro disabled
    voice: str = "Hải Đăng"  # VieNeu preset (exact NFC name); pick a male voice with tools/tests/tts/bench.py
    english_voice: str = "bm_george"
    english_engine: str = "vieneu"  # "vieneu" | "kokoro"
    speed: float = 1.0  # Kokoro natively; VieNeu audio is time-stretched by the router (WSOLA)
    default_sample_rate: int = 24000
    connect_timeout: float = 3.0
    read_timeout: float = 20.0  # first audio byte and gaps between chunks
    health_timeout: float = 2.0
    health_interval: float = 10.0
    sentence_gap_ms: int = 120  # silence between sentences synthesized by different requests
    english_min_words: int = 2
    english_min_ratio: float = 0.6
    zero_tens: str = "linh"
    thousand: str = "nghìn"
    abbreviations_file: str = ""
    max_input_chars: int = 5000
    log_level: str = "info"
    host: str = "0.0.0.0"
    port: int = 8880

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if env is None else env
        engine = _get(env, "TTS_ENGLISH_ENGINE", "vieneu").lower()
        return cls(
            vieneu_url=_get(env, "VIENEU_URL", "http://vieneu:8000").rstrip("/"),
            vieneu_api_key=_get(env, "VIENEU_API_KEY", ""),
            vieneu_model=_get(env, "VIENEU_MODEL", "vieneu-v3-turbo"),
            kokoro_url=_get(env, "KOKORO_URL", "").rstrip("/"),
            voice=_get(env, "TTS_VOICE", "Hải Đăng"),
            english_voice=_get(env, "TTS_ENGLISH_VOICE", "bm_george"),
            english_engine=engine if engine in ("vieneu", "kokoro") else "vieneu",
            speed=_float(env, "TTS_SPEED", 1.0),
            default_sample_rate=_int(env, "TTS_SAMPLE_RATE", 24000),
            connect_timeout=_float(env, "TTS_CONNECT_TIMEOUT", 3.0),
            read_timeout=_float(env, "TTS_READ_TIMEOUT", 20.0),
            health_timeout=_float(env, "TTS_HEALTH_TIMEOUT", 2.0),
            health_interval=_float(env, "TTS_HEALTH_INTERVAL", 10.0),
            sentence_gap_ms=_int(env, "TTS_SENTENCE_GAP_MS", 120),
            english_min_words=_int(env, "TTS_ENGLISH_MIN_WORDS", 2),
            english_min_ratio=_float(env, "TTS_ENGLISH_MIN_RATIO", 0.6),
            zero_tens=_get(env, "TTS_NUM_ZERO_TENS", "linh"),
            thousand=_get(env, "TTS_NUM_THOUSAND", "nghìn"),
            abbreviations_file=_get(env, "TTS_ABBREV_FILE", ""),
            max_input_chars=_int(env, "TTS_MAX_INPUT_CHARS", 5000),
            log_level=_get(env, "LOG_LEVEL", "info").lower(),
            host=_get(env, "HOST", "0.0.0.0"),
            port=_int(env, "PORT", 8880),
        )
