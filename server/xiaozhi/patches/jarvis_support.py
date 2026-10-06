"""Jarvis helpers used by the patched xiaozhi-server code (installed as core/utils/jarvis_support.py).

- `[LAT]` latency milestone lines (contract 4.10):
      [LAT] sid=<session_id> stage=<stage> t_ms=<epoch ms> [key=value ...]
  Values never contain whitespace (replaced by "_").
- Header redaction for log lines (Authorization, Cookie, CF-Access-*).

Every public function swallows its own errors: a helper failure must never break the
voice pipeline. Python 3.10 compatible, no third-party imports at module load.
"""
import re
import time

TAG = "jarvis.lat"

_WS_RE = re.compile(r"\s+")
_SENSITIVE_HEADERS = {"authorization", "proxy-authorization", "cookie", "x-api-key"}
_SENSITIVE_PREFIXES = ("cf-access-",)
# TTS providers that emit their own `tts_first_audio` line (at the first PCM chunk)
_OWN_TTS_LAT_MODULES = ("core.providers.tts.jarvis_stream",)
_fallback_logger = None


def now_ms():
    """Epoch time in integer milliseconds."""
    return int(time.time() * 1000)


def as_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def safe_len(value):
    try:
        return len(value or "")
    except Exception:
        return 0


def _clean(value):
    text = _WS_RE.sub("_", str(value).strip())
    return text if text else "-"


def format_line(sid, stage, t_ms=None, **fields):
    """Build one `[LAT]` line (pure; used by tests and log parsers)."""
    if t_ms is None:
        t_ms = now_ms()
    parts = ["[LAT]", "sid=" + _clean(sid), "stage=" + _clean(stage), "t_ms=%d" % int(t_ms)]
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = int(value)
        parts.append("%s=%s" % (_clean(key), _clean(value)))
    return " ".join(parts)


def _logger_for(conn):
    log = getattr(conn, "logger", None)
    if log is not None:
        return log
    global _fallback_logger
    if _fallback_logger is None:
        from loguru import logger as _loguru_logger  # configured by config.logger.setup_logging

        _fallback_logger = _loguru_logger
    return _fallback_logger


def lat(conn_or_sid, stage, t_ms=None, **fields):
    """Emit one `[LAT]` line at INFO via the xiaozhi (loguru) logger."""
    try:
        if isinstance(conn_or_sid, str) or conn_or_sid is None:
            conn, sid = None, conn_or_sid or "-"
        else:
            conn, sid = conn_or_sid, getattr(conn_or_sid, "session_id", None) or "-"
        _logger_for(conn).bind(tag=TAG).info(format_line(sid, stage, t_ms, **fields))
    except Exception:
        pass


def llm_first_token(conn, response, depth=0, turn=None):
    """Log `llm_first_token` on the first chunk carrying content or a tool call.

    Returns True once logged (or on an unexpected chunk shape, to stop retrying).
    """
    try:
        content, tool_calls = None, None
        if isinstance(response, tuple) and len(response) == 2:
            content, tool_calls = response
        elif isinstance(response, dict):
            content = response.get("content")
        else:
            content = response
        has_content = isinstance(content, str) and len(content) > 0
        if not has_content and not tool_calls:
            return False
        lat(conn, "llm_first_token", depth=depth, turn=turn, kind="tool" if tool_calls else "content")
        return True
    except Exception:
        return True


def tts_first_audio(tts):
    """Log `tts_first_audio` once per turn (first Opus frame encoded from TTS PCM).

    Fallback for upstream providers: providers that log their own line at the first PCM chunk
    (jarvis_stream, or any class with JARVIS_LAT_TTS_FIRST_AUDIO = True) are skipped.
    """
    try:
        if getattr(tts, "JARVIS_LAT_TTS_FIRST_AUDIO", False) or type(tts).__module__ in _OWN_TTS_LAT_MODULES:
            return
        conn = getattr(tts, "conn", None)
        turn = getattr(tts, "current_sentence_id", None) or getattr(conn, "sentence_id", None)
        if turn is None or getattr(tts, "_jarvis_lat_turn", None) == turn:
            return
        tts._jarvis_lat_turn = turn
        lat(conn, "tts_first_audio", turn=turn)
    except Exception:
        pass


def first_packet_sent(conn, flow_control=None):
    """Log `first_packet_sent` once per turn (first Opus packet written to the device)."""
    try:
        turn = None
        if isinstance(flow_control, dict):
            turn = flow_control.get("sentence_id")
        if turn is None:
            turn = getattr(conn, "sentence_id", None)
        if getattr(conn, "_jarvis_lat_fp_turn", None) == turn:
            return
        conn._jarvis_lat_fp_turn = turn
        lat(conn, "first_packet_sent", turn=turn)
    except Exception:
        pass


def mcp_call_done(conn, tool, t0_ms, ok, **fields):
    """Log `mcp_call` for a device tool call: t_ms = request sent, ms = round trip."""
    try:
        t0 = as_int(t0_ms)
        lat(conn, "mcp_call", t_ms=t0, tool=tool, ms=now_ms() - t0, ok=bool(ok), **fields)
    except Exception:
        pass


def redact_headers(headers):
    """Copy of `headers` with credential values replaced by "***" (for log lines)."""
    try:
        out = {}
        for key, value in headers.items():
            low = str(key).lower()
            if low in _SENSITIVE_HEADERS or low.startswith(_SENSITIVE_PREFIXES):
                out[key] = "***"
            else:
                out[key] = value
        return out
    except Exception:
        return "<redacted>"
