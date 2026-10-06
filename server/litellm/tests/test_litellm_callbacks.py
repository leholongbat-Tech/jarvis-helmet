"""Unit tests for server/litellm/jarvis_callbacks.py (runs without LiteLLM installed: the two imports
are replaced by minimal stand-ins; inside the image the real classes are used).

    .venv/Scripts/python -m pytest server/litellm/tests -q
"""
from __future__ import annotations

import asyncio
import datetime as dt
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

CALLBACKS = Path(__file__).resolve().parents[1] / "jarvis_callbacks.py"
NO_UPSTREAM = "litellm_no_upstream_llm_call"


@pytest.fixture(scope="module")
def cb():
    added = []
    try:
        import litellm.integrations.custom_logger  # noqa: F401  (real LiteLLM available)
    except ImportError:
        fake = {
            "litellm": types.ModuleType("litellm"),
            "litellm.integrations": types.ModuleType("litellm.integrations"),
            "litellm.integrations.custom_logger": types.ModuleType("litellm.integrations.custom_logger"),
            "litellm.constants": types.ModuleType("litellm.constants"),
        }

        class CustomLogger:  # minimal stand-in
            pass

        fake["litellm.integrations.custom_logger"].CustomLogger = CustomLogger
        fake["litellm.constants"].LITELLM_LOGGING_NO_UPSTREAM_LLM_CALL = NO_UPSTREAM
        for name, module in fake.items():
            if name not in sys.modules:
                sys.modules[name] = module
                added.append(name)
    spec = importlib.util.spec_from_file_location("jarvis_callbacks_under_test", CALLBACKS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    for name in added:
        sys.modules.pop(name, None)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("JARVIS_CLOUD_MODEL_GROUPS", "JARVIS_LOG_LOCAL_CALLS", "JARVIS_LLM_LOG_FILE",
                "JARVIS_CLOUD_MIN_MAX_TOKENS"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("raw", ["0", "0.0", "-1", "nan", "inf", "abc", " 0 "])
def test_cloud_budget_guard_refuses_unlimited_values(cb, raw, capsys):
    with pytest.raises(ValueError, match="CLOUD_MONTHLY_BUDGET_USD"):
        cb.check_cloud_budget({"CLOUD_MONTHLY_BUDGET_USD": raw})
    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert record["event"] == "config_error"


@pytest.mark.parametrize("raw, expected", [("10", 10.0), ("0.5", 0.5), (" 25 ", 25.0)])
def test_cloud_budget_guard_accepts_positive_values(cb, raw, expected):
    assert cb.check_cloud_budget({"CLOUD_MONTHLY_BUDGET_USD": raw}) == expected


def test_cloud_budget_guard_ignores_unset(cb):
    assert cb.check_cloud_budget({}) is None
    assert cb.check_cloud_budget({"CLOUD_MONTHLY_BUDGET_USD": ""}) is None


def payload(**overrides):
    base = {
        "model_group": "jarvis-cloud", "model": "claude-opus-5-5", "custom_llm_provider": "anthropic",
        "model_id": "jarvis-cloud-anthropic", "stream": True, "prompt_tokens": 1000, "completion_tokens": 20,
        "total_tokens": 1020, "response_cost": 0.0044000001, "startTime": 1000.0, "endTime": 1002.5,
        "completionStartTime": 1000.4, "trace_id": "t-1", "metadata": {},
    }
    base.update(overrides)
    return base


def kwargs_for(p, **extra):
    kw = {"standard_logging_object": p}
    kw.update(extra)
    return kw


# ---- classification -----------------------------------------------------------------------------
def test_is_cloud_call(cb, monkeypatch):
    assert cb.is_cloud_call(payload())
    assert cb.is_cloud_call(payload(model_group="x", custom_llm_provider="openai"))
    assert not cb.is_cloud_call(payload(model_group="jarvis-main", custom_llm_provider="ollama_chat"))
    # the local stand-in of config.local.yaml counts as cloud through its model group
    assert cb.is_cloud_call(payload(model_group="jarvis-cloud", custom_llm_provider="ollama_chat"))
    monkeypatch.setenv("JARVIS_CLOUD_MODEL_GROUPS", "expert, other")
    assert cb.is_cloud_call(payload(model_group="expert", custom_llm_provider="ollama_chat"))


def test_failure_event(cb):
    assert cb.failure_event({NO_UPSTREAM: True, "standard_logging_object": payload()}) == "proxy_reject"
    budget = payload(error_str="No deployments available - crossed budget: Exceeded budget for deployment")
    assert cb.failure_event(kwargs_for(budget)) == "budget_exceeded"
    info = payload(error_information={"error_message": "boom", "error_class": "APIConnectionError"})
    assert cb.failure_event(kwargs_for(info)) == "llm_error"
    assert cb.failure_event(None) == "llm_error"


# ---- records ------------------------------------------------------------------------------------
def test_cloud_call_record(cb):
    rec = cb.build_call_record("cloud_call", kwargs_for(payload()))
    assert rec["tag"] == "jarvis-llm" and rec["event"] == "cloud_call"
    assert rec["latency_ms"] == 2500 and rec["ttft_ms"] == 400
    assert rec["cost_usd"] == 0.0044
    assert rec["prompt_tokens"] == 1000 and rec["completion_tokens"] == 20
    assert rec["deployment"] == "jarvis-cloud-anthropic" and rec["provider"] == "anthropic"
    assert rec["fallback_from"] is None
    assert "error" not in rec
    dt.datetime.fromisoformat(rec["ts"])  # ISO timestamp with offset


def test_non_stream_latency_and_datetimes(cb):
    p = payload(stream=False, startTime=None, endTime=None, response_time=1.234)
    rec = cb.build_call_record("local_call", kwargs_for(p))
    assert rec["latency_ms"] == 1234 and rec["ttft_ms"] is None
    start = dt.datetime(2026, 10, 6, 10, 0, 0)
    rec = cb.build_call_record("local_call", kwargs_for(payload(startTime=None, endTime=None)),
                               start_time=start, end_time=start + dt.timedelta(milliseconds=750))
    assert rec["latency_ms"] == 750


def test_fallback_from_comes_from_litellm_params_metadata(cb):
    kw = kwargs_for(payload(), litellm_params={"metadata": {"original_model_group": "jarvis-main",
                                                            "attempted_fallbacks": 1}})
    rec = cb.build_call_record("cloud_call", kw)
    assert rec["fallback_from"] == "jarvis-main" and rec["attempted_fallbacks"] == 1


def test_error_records(cb):
    long_error = "x" * 1000
    p = payload(error_str=long_error, error_information={"error_class": "Timeout", "error_code": "408"})
    rec = cb.build_call_record("llm_error", kwargs_for(p))
    assert rec["error_class"] == "Timeout" and rec["error_code"] == "408"
    assert len(rec["error"]) == 300 and rec["error"].endswith("...")
    rec = cb.build_call_record("proxy_reject", {NO_UPSTREAM: True, "standard_logging_object": payload(
        error_str="No api key passed in.")})
    assert rec["model"] is None and rec["provider"] is None and rec["deployment"] is None
    assert rec["model_group"] == "jarvis-cloud" and rec["error"] == "No api key passed in."


def test_fallback_record(cb):
    rec = cb.build_fallback_record("jarvis-main", {"model": "jarvis-cloud", "fallback_depth": 1},
                                   TimeoutError("slow\n  local"), ok=True)
    assert rec == {**rec, "event": "fallback", "from": "jarvis-main", "to": "jarvis-cloud", "ok": True,
                   "attempt": 1, "reason_class": "TimeoutError", "reason": "slow local"}


# ---- max_tokens floor for cloud calls -----------------------------------------------------------
def test_cloud_token_floor(cb, monkeypatch):
    cloud = {"model": "anthropic/claude-opus-5-5", "max_tokens": 300, "metadata": {"model_group": "jarvis-cloud"}}
    assert cb.apply_cloud_token_floor(dict(cloud))["max_tokens"] == 1024
    assert cb.apply_cloud_token_floor({"model": "anthropic/claude-opus-5-5", "max_completion_tokens": 100}) \
        ["max_completion_tokens"] == 1024
    local = {"model": "ollama_chat/qwen3:30b", "max_tokens": 300, "metadata": {"model_group": "jarvis-main"}}
    assert cb.apply_cloud_token_floor(dict(local)) is None
    assert cb.apply_cloud_token_floor({**cloud, "max_tokens": 4000}) is None
    assert cb.apply_cloud_token_floor({**cloud, "max_tokens": True}) is None
    assert cb.apply_cloud_token_floor({"model": "anthropic/claude-opus-5-5"}) is None  # no max_tokens sent
    # stand-in "cloud" of config.local.yaml (Ollama model in the jarvis-cloud group)
    standin = {"model": "ollama_chat/qwen3:4b-instruct", "max_tokens": 80,
               "litellm_metadata": {"model_group": "jarvis-cloud"}}
    assert cb.apply_cloud_token_floor(dict(standin))["max_tokens"] == 1024
    monkeypatch.setenv("JARVIS_CLOUD_MIN_MAX_TOKENS", "0")
    assert cb.apply_cloud_token_floor(dict(cloud)) is None
    monkeypatch.setenv("JARVIS_CLOUD_MIN_MAX_TOKENS", "512")
    assert cb.apply_cloud_token_floor(dict(cloud))["max_tokens"] == 512
    monkeypatch.setenv("JARVIS_CLOUD_MIN_MAX_TOKENS", "lots")
    assert cb.apply_cloud_token_floor(dict(cloud))["max_tokens"] == 1024


def test_deployment_hook(cb):
    logger = cb.JarvisCallLogger()
    out = asyncio.run(logger.async_pre_call_deployment_hook(
        {"model": "anthropic/claude-sonnet-5-5", "max_tokens": 300}, None))
    assert out["max_tokens"] == 1024
    assert asyncio.run(logger.async_pre_call_deployment_hook({"model": "ollama_chat/x", "max_tokens": 300}, None)) \
        is None
    assert asyncio.run(logger.async_pre_call_deployment_hook("garbage", None)) is None


# ---- emitting -----------------------------------------------------------------------------------
def test_success_logs_cloud_and_optionally_local(cb, capsys, monkeypatch):
    logger = cb.JarvisCallLogger()
    asyncio.run(logger.async_log_success_event(kwargs_for(payload()), None, None, None))
    asyncio.run(logger.async_log_success_event(
        kwargs_for(payload(model_group="jarvis-main", custom_llm_provider="ollama_chat")), None, None, None))
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["event"] for line in lines] == ["cloud_call"]
    monkeypatch.setenv("JARVIS_LOG_LOCAL_CALLS", "1")
    asyncio.run(logger.async_log_success_event(
        kwargs_for(payload(model_group="jarvis-main", custom_llm_provider="ollama_chat")), None, None, None))
    assert json.loads(capsys.readouterr().out)["event"] == "local_call"


def test_failure_and_fallback_hooks(cb, capsys):
    logger = cb.JarvisCallLogger()
    asyncio.run(logger.async_log_failure_event({NO_UPSTREAM: True, "standard_logging_object": payload()},
                                               None, None, None))
    asyncio.run(logger.async_log_failure_event(kwargs_for(payload(error_str="crossed budget")), None, None, None))
    asyncio.run(logger.log_success_fallback_event("jarvis-main", {"model": "jarvis-cloud"}, ValueError("x")))
    asyncio.run(logger.log_failure_fallback_event("jarvis-main", {"model": "jarvis-cloud"}, ValueError("x")))
    events = [json.loads(line)["event"] for line in capsys.readouterr().out.splitlines()]
    assert events == ["proxy_reject", "budget_exceeded", "fallback", "fallback"]


def test_hooks_never_raise(cb, capsys):
    logger = cb.JarvisCallLogger()
    for bad in (None, "text", {"standard_logging_object": "not-a-dict"}, {"standard_logging_object": {
            "startTime": "x", "endTime": object(), "response_cost": "free"}}):
        asyncio.run(logger.async_log_success_event(bad, None, None, None))
        asyncio.run(logger.async_log_failure_event(bad, None, None, None))
        asyncio.run(logger.log_success_fallback_event(None, bad, None))
    capsys.readouterr()


def test_log_file(cb, tmp_path, monkeypatch, capsys):
    target = tmp_path / "llm_calls.jsonl"
    monkeypatch.setenv("JARVIS_LLM_LOG_FILE", str(target))
    logger = cb.JarvisCallLogger()
    asyncio.run(logger.async_log_success_event(kwargs_for(payload()), None, None, None))
    asyncio.run(logger.async_log_success_event(kwargs_for(payload()), None, None, None))
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2 and rows[0]["event"] == "cloud_call"
    capsys.readouterr()


def test_proxy_handler_instance(cb):
    assert isinstance(cb.proxy_handler_instance, cb.JarvisCallLogger)
