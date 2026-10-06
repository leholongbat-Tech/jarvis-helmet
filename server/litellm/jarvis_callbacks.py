"""LiteLLM proxy callback for the Jarvis Helmet.

Writes one JSON line to stdout (-> docker json-file logs) for:
  - every successful cloud call   event=cloud_call    (model, tokens, cost, latency)
  - every failed model call       event=llm_error     (local or cloud)
  - every router fallback         event=fallback      (e.g. jarvis-main -> jarvis-cloud)
  - requests the proxy rejected before any model call (missing/wrong key ...)
                                  event=proxy_reject
  - calls refused because the monthly cloud budget is used up (HTTP 429)
                                  event=budget_exceeded
  - local calls, only if JARVIS_LOG_LOCAL_CALLS=1    event=local_call

Loaded from the config directory by `litellm_settings.callbacks:
jarvis_callbacks.proxy_handler_instance`. Read the lines with:
  docker compose logs litellm | grep '"tag": "jarvis-llm"'

It also raises max_tokens of cloud calls to a floor (async_pre_call_deployment_hook, runs per
deployment, so fallbacks are covered): Claude Opus/Sonnet 5.5 always think (adaptive) and the
thinking tokens count against max_tokens, so xiaozhi's 300 could leave an empty reply. The
spoken length stays bounded by the prompt; local (Ollama) calls keep the client value.

Environment:
  JARVIS_CLOUD_MODEL_GROUPS    comma-separated model groups counted as cloud (default jarvis-cloud);
                               any non-Ollama provider is cloud as well
  JARVIS_CLOUD_MIN_MAX_TOKENS  max_tokens floor for cloud calls (default 1024, 0 = off)
  JARVIS_LOG_LOCAL_CALLS       1 = also log successful local calls
  JARVIS_LLM_LOG_FILE          optional path; lines are appended there too

The hooks never raise: a logging problem must not break a request. The one deliberate failure is
at import (proxy start): CLOUD_MONTHLY_BUDGET_USD <= 0 or not a number refuses to start, because
LiteLLM treats max_budget 0 as "no budget" and the cloud would then be uncapped.
"""

import datetime as _dt
import json
import os
import sys

from litellm.integrations.custom_logger import CustomLogger

try:  # set by the proxy on auth/rate-limit errors of requests that never reached a model
    from litellm.constants import LITELLM_LOGGING_NO_UPSTREAM_LLM_CALL as _NO_UPSTREAM_KEY
except ImportError:  # pragma: no cover - older/newer LiteLLM without the constant
    _NO_UPSTREAM_KEY = "litellm_no_upstream_llm_call"

TAG = "jarvis-llm"
FAILURE_EVENTS = ("llm_error", "proxy_reject", "budget_exceeded")
LOCAL_PROVIDERS = frozenset({"ollama", "ollama_chat"})
DEFAULT_CLOUD_MIN_MAX_TOKENS = 1024
_TRUE = frozenset({"1", "true", "yes", "on"})
_MAX_ERROR_CHARS = 300


def _cloud_groups():
    raw = os.environ.get("JARVIS_CLOUD_MODEL_GROUPS", "jarvis-cloud")
    return frozenset(g.strip() for g in raw.split(",") if g.strip())


def cloud_min_max_tokens():
    raw = os.environ.get("JARVIS_CLOUD_MIN_MAX_TOKENS", "").strip()
    if not raw:
        return DEFAULT_CLOUD_MIN_MAX_TOKENS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_CLOUD_MIN_MAX_TOKENS


def is_cloud_request(kwargs, cloud_groups=None):
    """True for a deployment call that goes to a cloud model (hook kwargs, before the call)."""
    if not isinstance(kwargs, dict):
        return False
    groups = _cloud_groups() if cloud_groups is None else cloud_groups
    for key in ("metadata", "litellm_metadata"):
        md = kwargs.get(key)
        if isinstance(md, dict) and md.get("model_group") in groups:
            return True
    model = str(kwargs.get("model") or "")
    provider = str(kwargs.get("custom_llm_provider") or (model.split("/", 1)[0] if "/" in model else ""))
    return bool(provider) and provider not in LOCAL_PROVIDERS


def apply_cloud_token_floor(kwargs, floor=None, cloud_groups=None):
    """Raise max_tokens / max_completion_tokens of a cloud call to the floor.

    Returns the (mutated) kwargs when something changed, else None (= leave the request as is).
    """
    floor = cloud_min_max_tokens() if floor is None else floor
    if floor <= 0 or not is_cloud_request(kwargs, cloud_groups):
        return None
    changed = False
    for key in ("max_tokens", "max_completion_tokens"):
        value = kwargs.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and 0 < value < floor:
            kwargs[key] = floor
            changed = True
    return kwargs if changed else None


def _now_iso():
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _to_epoch(value):
    """LiteLLM passes datetimes to hooks and floats in the logging payload."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, _dt.datetime):
        return value.timestamp()
    return None


def _ms_between(start, end):
    a, b = _to_epoch(start), _to_epoch(end)
    if a is None or b is None or b < a:
        return None
    return int(round((b - a) * 1000))


def _payload(kwargs):
    if not isinstance(kwargs, dict):
        return {}
    p = kwargs.get("standard_logging_object")
    return p if isinstance(p, dict) else {}


def _metadata(kwargs, payload):
    """Request metadata; the router's fallback keys (original_model_group,
    attempted_fallbacks) only survive in litellm_params.metadata."""
    merged = {}
    md = payload.get("metadata")
    if isinstance(md, dict):
        merged.update(md)
    lp = kwargs.get("litellm_params") if isinstance(kwargs, dict) else None
    if isinstance(lp, dict):
        for key in ("metadata", "litellm_metadata"):
            raw = lp.get(key)
            if isinstance(raw, dict):
                for k, v in raw.items():
                    merged.setdefault(k, v)
    return merged


def _short(text, limit=_MAX_ERROR_CHARS):
    if text is None:
        return None
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def is_proxy_reject(kwargs):
    """True when the proxy failed the request before calling any model (auth, rate limit)."""
    return isinstance(kwargs, dict) and kwargs.get(_NO_UPSTREAM_KEY) is True


def _error_text(payload):
    info = payload.get("error_information")
    info = info if isinstance(info, dict) else {}
    return str(payload.get("error_str") or info.get("error_message") or "")


def is_budget_exceeded(payload):
    """Router budget limiter: 'No deployments available - crossed budget ...' (no fallback)."""
    return "crossed budget" in _error_text(payload)


def failure_event(kwargs):
    if is_proxy_reject(kwargs):
        return "proxy_reject"
    if is_budget_exceeded(_payload(kwargs)):
        return "budget_exceeded"
    return "llm_error"


def is_cloud_call(payload, cloud_groups=None):
    """True when the call went to a cloud deployment (by model group or provider)."""
    groups = _cloud_groups() if cloud_groups is None else cloud_groups
    if payload.get("model_group") in groups:
        return True
    provider = payload.get("custom_llm_provider")
    return bool(provider) and provider not in LOCAL_PROVIDERS


def build_call_record(event, kwargs, start_time=None, end_time=None):
    """Flatten the StandardLoggingPayload into a small, stable JSON record."""
    p = _payload(kwargs)
    md = _metadata(kwargs, p)
    # Total duration from the payload timestamps (for streams `response_time` is only the
    # time to first token); the hook's own start/end times when the payload has none.
    start = p.get("startTime")
    end = p.get("endTime")
    latency_ms = _ms_between(start if start is not None else start_time, end if end is not None else end_time)
    if latency_ms is None and isinstance(p.get("response_time"), (int, float)) and not p.get("stream"):
        latency_ms = int(round(p["response_time"] * 1000))
    ttft_ms = None
    if p.get("stream"):
        ttft_ms = _ms_between(p.get("startTime"), p.get("completionStartTime"))
    error_info = p.get("error_information") if isinstance(p.get("error_information"), dict) else {}
    cost = p.get("response_cost")
    attempted = md.get("attempted_fallbacks")
    fallback_from = md.get("original_model_group") if isinstance(attempted, int) and attempted > 0 else None
    record = {
        "tag": TAG,
        "ts": _now_iso(),
        "event": event,
        "model_group": p.get("model_group") or md.get("model_group"),
        "model": p.get("model"),
        "provider": p.get("custom_llm_provider"),
        "deployment": p.get("model_id"),
        "stream": bool(p.get("stream")),
        "prompt_tokens": p.get("prompt_tokens"),
        "completion_tokens": p.get("completion_tokens"),
        "total_tokens": p.get("total_tokens"),
        "cost_usd": round(float(cost), 6) if isinstance(cost, (int, float)) else None,
        "latency_ms": latency_ms,
        "ttft_ms": ttft_ms,
        "fallback_from": fallback_from,
        "attempted_fallbacks": attempted,
        "trace_id": p.get("trace_id"),
    }
    if event == "proxy_reject":
        # No model was called: drop the deployment the proxy only attributed to the request.
        for key in ("model", "provider", "deployment"):
            record[key] = None
    if event in FAILURE_EVENTS:
        record["error_class"] = error_info.get("error_class")
        record["error_code"] = error_info.get("error_code")
        record["error"] = _short(_error_text(p) or None)
    return record


def build_fallback_record(original_model_group, kwargs, original_exception, ok):
    kw = kwargs if isinstance(kwargs, dict) else {}
    return {
        "tag": TAG,
        "ts": _now_iso(),
        "event": "fallback",
        "from": original_model_group,
        "to": kw.get("model"),
        "ok": bool(ok),
        "attempt": kw.get("fallback_depth"),
        "reason_class": type(original_exception).__name__ if original_exception is not None else None,
        "reason": _short(original_exception),
    }


class JarvisCallLogger(CustomLogger):
    """Logs cloud calls, failures and fallbacks as JSON lines; max_tokens floor for cloud calls."""

    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        try:
            return apply_cloud_token_floor(kwargs)
        except Exception as exc:  # never block a request
            self._emit({"tag": TAG, "ts": _now_iso(), "event": "logger_error", "error": _short(repr(exc))})
            return None

    def _emit(self, record):
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
            print(line, file=sys.stdout, flush=True)
            path = os.environ.get("JARVIS_LLM_LOG_FILE", "").strip()
            if path:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except Exception as exc:  # never break the request path
            try:
                print(f"[{TAG}] logging error: {exc!r}", file=sys.stderr, flush=True)
            except Exception:
                pass

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        try:
            payload = _payload(kwargs)
            if is_cloud_call(payload):
                self._emit(build_call_record("cloud_call", kwargs, start_time, end_time))
            elif os.environ.get("JARVIS_LOG_LOCAL_CALLS", "0").strip().lower() in _TRUE:
                self._emit(build_call_record("local_call", kwargs, start_time, end_time))
        except Exception as exc:
            self._emit({"tag": TAG, "ts": _now_iso(), "event": "logger_error", "error": _short(repr(exc))})

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        try:
            self._emit(build_call_record(failure_event(kwargs), kwargs, start_time, end_time))
        except Exception as exc:
            self._emit({"tag": TAG, "ts": _now_iso(), "event": "logger_error", "error": _short(repr(exc))})

    async def log_success_fallback_event(self, original_model_group, kwargs, original_exception):
        try:
            self._emit(build_fallback_record(original_model_group, kwargs, original_exception, ok=True))
        except Exception as exc:
            self._emit({"tag": TAG, "ts": _now_iso(), "event": "logger_error", "error": _short(repr(exc))})

    async def log_failure_fallback_event(self, original_model_group, kwargs, original_exception):
        try:
            self._emit(build_fallback_record(original_model_group, kwargs, original_exception, ok=False))
        except Exception as exc:
            self._emit({"tag": TAG, "ts": _now_iso(), "event": "logger_error", "error": _short(repr(exc))})


def check_cloud_budget(environ=None):
    """Refuse a budget that LiteLLM would read as "unlimited" (0, negative, NaN) or cannot parse.

    Returns the budget in USD, or None when the variable is not set (compose always sets it).
    """
    environ = os.environ if environ is None else environ
    raw = environ.get("CLOUD_MONTHLY_BUDGET_USD")
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw.strip())
    except ValueError:
        value = float("nan")
    if not value > 0 or value == float("inf"):
        message = ("CLOUD_MONTHLY_BUDGET_USD=%r is not a positive number. LiteLLM treats 0 as no budget, "
                   "so the cloud would be uncapped. Set a positive amount; to block the cloud entirely, "
                   "leave ANTHROPIC_API_KEY empty or remove the jarvis-cloud fallback." % raw)
        sys.stdout.write(json.dumps({"tag": TAG, "ts": _now_iso(), "event": "config_error",
                                     "error": message}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        raise ValueError(message)
    return value


check_cloud_budget()

proxy_handler_instance = JarvisCallLogger()
