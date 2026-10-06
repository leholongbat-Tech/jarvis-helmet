"""Environment-driven configuration (contract section 4.4).

xiaozhi's OpenaiASR provider only sends `file` + `model`, so every recognition
setting (language, prompt, beam size, filters) is decided here from env vars.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

APP_DIR = Path(__file__).resolve().parent.parent

ENGINES = ("faster_whisper", "qwen3_vllm", "fake")
DEVICES = ("cuda", "cpu", "auto")

# Language codes accepted by Whisper (faster_whisper/tokenizer.py, v1.2.1).
WHISPER_LANGUAGES = frozenset(
    "af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo fr gl gu ha "
    "haw he hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo lt lv mg mi mk ml mn mr "
    "ms mt my ne nl nn no oc pa pl ps pt ro ru sa sd si sk sl sn so sq sr su sv sw ta te tg th "
    "tk tl tr tt uk ur uz vi yi yo zh yue".split()
)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_DISABLED = {"off", "none", "disabled", "null"}
_AUTO_LANG = {"auto", "none", "detect", "null"}
_NO_PROMPT = {"none", "off", "-", "null"}
_LANG_RE = re.compile(r"^[a-z]{2,3}$")


class ConfigError(ValueError):
    """Invalid configuration value or request override."""


def parse_language(value: Optional[str]) -> Optional[str]:
    """Normalize a language spec.

    Returns None for auto-detection, ``"vi"`` for a fixed language, or
    ``"vi,en"`` for auto-detection restricted to the listed languages.
    """
    if value is None:
        return None
    spec = value.strip().lower().replace(" ", "")
    if spec in _AUTO_LANG or spec == "":
        return None
    codes = []
    for code in spec.split(","):
        if not code:
            continue
        if not _LANG_RE.match(code) or code not in WHISPER_LANGUAGES:
            raise ConfigError(f"invalid language code: {code!r}")
        if code not in codes:
            codes.append(code)
    return ",".join(codes) if codes else None


def parse_prompt(value: Optional[str]) -> Optional[str]:
    """Normalize a prompt: empty, "none", "off" or "-" disable it."""
    if value is None:
        return None
    text = value.strip()
    if text == "" or text.lower() in _NO_PROMPT:
        return None
    return text


def _get(env: Mapping[str, str], name: str) -> Optional[str]:
    raw = env.get(name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw if raw else None


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _get(env, name)
    if raw is None:
        return default
    low = raw.lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    raise ConfigError(f"{name}: expected a boolean, got {raw!r}")


def _number(env: Mapping[str, str], name: str, default, cast, lo=None, hi=None):
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        value = cast(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}: expected a number, got {raw!r}") from exc
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise ConfigError(f"{name}: {value} outside [{lo}, {hi}]")
    return value


def _opt_float(env: Mapping[str, str], name: str, default: Optional[float]) -> Optional[float]:
    raw = _get(env, name)
    if raw is None:
        return default
    if raw.lower() in _DISABLED:
        return None
    return _number(env, name, default, float)


@dataclass(frozen=True)
class FilterSettings:
    """Hallucination filter thresholds (see filters.py)."""

    enabled: bool = True
    logprob_min: Optional[float] = -1.0  # drop a segment below this avg logprob
    no_speech_max: Optional[float] = 0.6  # ... or above this no_speech_prob
    no_speech_logprob: float = -0.5  # ... when its avg logprob is also below this
    compression_max: Optional[float] = 2.4  # drop very repetitive segments
    repeat_min: int = 4  # n-gram repeated this many times in a row = loop
    drop_cjk: bool = True  # VI/EN helmet: CJK output is always a hallucination
    prompt_echo_words: int = 4  # min contiguous words copied from the prompt


@dataclass(frozen=True)
class Settings:
    engine: str = "faster_whisper"
    model: str = "large-v3"
    model_revision: Optional[str] = None
    device: str = "cuda"
    device_index: int = 0
    compute_type: str = "auto"
    cpu_threads: int = 0
    language: Optional[str] = "vi"
    initial_prompt: Optional[str] = None
    hotwords: Optional[str] = None
    beam_size: int = 5
    max_tokens_per_s: Optional[float] = 30.0  # decode cap vs. runaway loops (None = off)
    local_files_only: bool = False
    hallucinations_file: Optional[str] = str(APP_DIR / "config" / "hallucinations.txt")
    filter: FilterSettings = field(default_factory=FilterSettings)
    min_rms_dbfs: Optional[float] = -60.0
    vad_gate: bool = True
    vad_threshold: float = 0.5
    vad_min_speech_ms: int = 250
    max_audio_s: float = 120.0
    max_upload_mb: float = 50.0
    request_timeout_s: float = 60.0  # 0 = no limit; xiaozhi itself never times out
    save_dir: Optional[str] = None
    vllm_url: Optional[str] = None
    vllm_timeout_s: float = 30.0
    vllm_wait_s: float = 300.0
    warmup: int = 1
    log_text: bool = True
    fake_text: str = "xin chào"

    def __post_init__(self) -> None:
        if self.engine not in ENGINES:
            raise ConfigError(f"ASR_ENGINE must be one of {ENGINES}, got {self.engine!r}")
        if self.device not in DEVICES:
            raise ConfigError(f"ASR_DEVICE must be one of {DEVICES}, got {self.device!r}")
        if self.engine == "qwen3_vllm" and not self.vllm_url:
            raise ConfigError("ASR_ENGINE=qwen3_vllm requires ASR_VLLM_URL")

    def compute_type_for(self, device: str) -> str:
        """Resolve ``auto`` to float16 on CUDA and int8 on CPU."""
        ctype = (self.compute_type or "auto").lower()
        if ctype != "auto":
            return ctype
        return "float16" if device == "cuda" else "int8"

    def replace(self, **changes: Any) -> "Settings":
        return replace(self, **changes)

    def public_dict(self) -> Dict[str, Any]:
        """Settings for /health and capture records (nothing secret in here)."""
        return asdict(self)

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if env is None else env
        d = cls()
        f = FilterSettings()
        filt = FilterSettings(
            enabled=_bool(env, "ASR_FILTER", f.enabled),
            logprob_min=_opt_float(env, "ASR_FILTER_LOGPROB_MIN", f.logprob_min),
            no_speech_max=_opt_float(env, "ASR_FILTER_NO_SPEECH_MAX", f.no_speech_max),
            no_speech_logprob=_number(
                env, "ASR_FILTER_NO_SPEECH_LOGPROB", f.no_speech_logprob, float
            ),
            compression_max=_opt_float(env, "ASR_FILTER_COMPRESSION_MAX", f.compression_max),
            repeat_min=_number(env, "ASR_FILTER_REPEAT_MIN", f.repeat_min, int, 2, 100),
            drop_cjk=_bool(env, "ASR_FILTER_DROP_CJK", f.drop_cjk),
            prompt_echo_words=_number(
                env, "ASR_FILTER_PROMPT_ECHO_WORDS", f.prompt_echo_words, int, 0, 1000
            ),
        )
        warmup_raw = _get(env, "ASR_WARMUP")
        if warmup_raw is not None and warmup_raw.lower() in _TRUE | _FALSE:
            warmup = 1 if warmup_raw.lower() in _TRUE else 0
        else:
            warmup = _number(env, "ASR_WARMUP", d.warmup, int, 0, 10)
        return cls(
            engine=(_get(env, "ASR_ENGINE") or d.engine).lower(),
            model=_get(env, "ASR_MODEL") or d.model,
            model_revision=_get(env, "ASR_MODEL_REVISION"),
            device=(_get(env, "ASR_DEVICE") or d.device).lower(),
            device_index=_number(env, "ASR_DEVICE_INDEX", d.device_index, int, 0),
            compute_type=(_get(env, "ASR_COMPUTE_TYPE") or d.compute_type).lower(),
            cpu_threads=_number(env, "ASR_CPU_THREADS", d.cpu_threads, int, 0),
            language=parse_language(_get(env, "ASR_LANGUAGE") or d.language),
            initial_prompt=parse_prompt(_get(env, "ASR_INITIAL_PROMPT")),
            hotwords=parse_prompt(_get(env, "ASR_HOTWORDS")),
            beam_size=_number(env, "ASR_BEAM_SIZE", d.beam_size, int, 1, 16),
            max_tokens_per_s=_opt_float(env, "ASR_MAX_TOKENS_PER_S", d.max_tokens_per_s),
            local_files_only=_bool(env, "ASR_LOCAL_FILES_ONLY", d.local_files_only),
            hallucinations_file=_get(env, "ASR_HALLUCINATIONS_FILE") or d.hallucinations_file,
            filter=filt,
            min_rms_dbfs=_opt_float(env, "ASR_MIN_RMS_DBFS", d.min_rms_dbfs),
            vad_gate=_bool(env, "ASR_VAD_GATE", d.vad_gate),
            vad_threshold=_number(env, "ASR_VAD_THRESHOLD", d.vad_threshold, float, 0.0, 1.0),
            vad_min_speech_ms=_number(env, "ASR_VAD_MIN_SPEECH_MS", d.vad_min_speech_ms, int, 0),
            max_audio_s=_number(env, "ASR_MAX_AUDIO_S", d.max_audio_s, float, 1.0),
            max_upload_mb=_number(env, "ASR_MAX_UPLOAD_MB", d.max_upload_mb, float, 0.1),
            request_timeout_s=_number(env, "ASR_REQUEST_TIMEOUT_S", d.request_timeout_s, float, 0.0),
            save_dir=_get(env, "ASR_SAVE_DIR"),
            vllm_url=_get(env, "ASR_VLLM_URL"),
            vllm_timeout_s=_number(env, "ASR_VLLM_TIMEOUT_S", d.vllm_timeout_s, float, 0.1),
            vllm_wait_s=_number(env, "ASR_VLLM_WAIT_S", d.vllm_wait_s, float, 0.0),
            warmup=warmup,
            log_text=_bool(env, "ASR_LOG_TEXT", d.log_text),
            fake_text=env.get("ASR_FAKE_TEXT", d.fake_text),
        )
