"""Static checks of server/litellm/config.yaml (production) and config.local.yaml (Docker Desktop)
against the contract (D1, D2, D25, section 4.6) and against what was verified in LiteLLM v1.104.0.
The live checks (proxy start, chat, tool call, fallback, budget in Redis, request sent to Anthropic)
are documented in server/README.md; they need Docker and are not part of this unit test.

    .venv/Scripts/python -m pytest server/litellm/tests -q
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

LITELLM = Path(__file__).resolve().parents[1]
SERVER = LITELLM.parent
CONFIGS = {"prod": LITELLM / "config.yaml", "local": LITELLM / "config.local.yaml"}
CLAUDE_IDS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"}
OPUS_PRICE = (4.0e-06, 2.0e-05)  # USD per token: $4 / $20 per MTok


def load(name: str) -> dict:
    return yaml.safe_load(CONFIGS[name].read_text(encoding="utf-8"))


def deployment(cfg: dict, group: str) -> dict:
    found = [m for m in cfg["model_list"] if m["model_name"] == group]
    assert len(found) == 1, f"exactly one {group} deployment expected"
    return found[0]


@pytest.fixture(params=sorted(CONFIGS))
def cfg(request):
    return load(request.param)


def test_model_groups_and_ids(cfg):
    assert sorted(m["model_name"] for m in cfg["model_list"]) == ["jarvis-cloud", "jarvis-main"]
    ids = [m["model_info"]["id"] for m in cfg["model_list"]]
    assert len(set(ids)) == len(ids), "model_info.id must be unique (budget keys use it)"


def test_router_settings(cfg):
    router = cfg["router_settings"]
    assert router["fallbacks"] == [{"jarvis-main": ["jarvis-cloud"]}]
    assert router["num_retries"] == 1
    assert router["timeout"] == 15
    assert router["retry_policy"] == {"TimeoutErrorRetries": 0}
    assert router["redis_host"] == "redis" and router["redis_port"] == 6379


def test_litellm_and_general_settings(cfg):
    settings = cfg["litellm_settings"]
    assert settings["drop_params"] is True
    assert settings["turn_off_message_logging"] is True
    assert settings["callbacks"] == "jarvis_callbacks.proxy_handler_instance"
    source = (LITELLM / "jarvis_callbacks.py").read_text(encoding="utf-8")
    assert re.search(r"^proxy_handler_instance = JarvisCallLogger\(\)$", source, re.M)
    assert cfg["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"


def test_main_deployment(cfg):
    main = deployment(cfg, "jarvis-main")
    params = main["litellm_params"]
    assert params["think"] is False
    assert {"frequency_penalty", "presence_penalty"} <= set(params["additional_drop_params"])
    info = main["model_info"]
    assert info["input_cost_per_token"] == 0 and info["output_cost_per_token"] == 0
    assert info["allowed_fails_policy"] == {"TimeoutErrorAllowedFails": 1}
    # LiteLLM keeps the fail counter for cooldown_time seconds: it must outlive one timeout, or two
    # consecutive timeouts never meet and the local model is never skipped (found in a live test).
    assert info["cooldown_time"] > params["stream_timeout"]
    assert info["cooldown_time"] > params["timeout"]


def test_cloud_deployment_budget_and_price(cfg):
    cloud = deployment(cfg, "jarvis-cloud")
    params = cloud["litellm_params"]
    assert params["max_budget"] == "os.environ/CLOUD_MONTHLY_BUDGET_USD"
    assert params["budget_duration"] == "1mo"
    assert {"temperature", "top_p", "top_k"} <= set(params["additional_drop_params"])
    info = cloud["model_info"]
    assert (info["input_cost_per_token"], info["output_cost_per_token"]) == OPUS_PRICE


def test_production_values():
    cfg = load("prod")
    main = deployment(cfg, "jarvis-main")["litellm_params"]
    assert main["model"] == "os.environ/LITELLM_MAIN_MODEL"
    assert main["api_base"] == "os.environ/OLLAMA_API_BASE"
    assert "keep_alive" not in main, "the Ollama server keeps the model (OLLAMA_KEEP_ALIVE in compose)"
    assert main["timeout"] == 15 and main["stream_timeout"] == 10
    assert "num_ctx" not in main, "a per-request num_ctx different from OLLAMA_CONTEXT_LENGTH reloads the model"
    cloud = deployment(cfg, "jarvis-cloud")["litellm_params"]
    assert cloud["model"] == "os.environ/CLOUD_MODEL"
    # Renamed so LiteLLM's pass-through routes cannot pick the key up (they bypass max_budget).
    assert cloud["api_key"] == "os.environ/JARVIS_ANTHROPIC_API_KEY"
    assert cloud["reasoning_effort"] == "low"  # -> thinking adaptive + output_config.effort=low
    assert "api_base" not in cloud


def test_local_config_points_to_local_ollama():
    cfg = load("local")
    for group in ("jarvis-main", "jarvis-cloud"):
        params = deployment(cfg, group)["litellm_params"]
        assert params["model"].startswith("os.environ/LOCAL_")
        assert params["api_base"].startswith("os.environ/LOCAL_")
        assert params["num_ctx"] == 8192
    assert "ANTHROPIC" not in CONFIGS["local"].read_text(encoding="utf-8")


def test_only_current_claude_ids_are_named():
    text = CONFIGS["prod"].read_text(encoding="utf-8")
    named = set(re.findall(r"claude-[a-z0-9-]*[a-z0-9]", text))
    assert named and named <= CLAUDE_IDS, named - CLAUDE_IDS
    # alternatives documented with their prices
    assert "claude-sonnet-5-5" in text and "2.0e-06" in text and "1.0e-05" in text
    assert "claude-haiku-4-5" in text and "1.0e-06" in text and "5.0e-06" in text


def test_env_example_matches_default_cloud_model():
    env = (SERVER / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^CLOUD_MODEL=anthropic/claude-opus-5-5$", env, re.M)
    budget = re.search(r"^CLOUD_MONTHLY_BUDGET_USD=(\d+(\.\d+)?)$", env, re.M)
    assert budget and float(budget.group(1)) > 0, "LiteLLM reads a budget of 0 as unlimited"


def test_xiaozhi_llm_block_respects_d25():
    template = (SERVER / "xiaozhi" / "data" / ".config.yaml.tmpl").read_text(encoding="utf-8")
    data = yaml.safe_load(re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", r"placeholder-\1", template))
    llm = data["LLM"][data["selected_module"]["LLM"]]
    assert llm["base_url"] == "http://litellm:4000/v1" and llm["model_name"] == "jarvis-main"
    assert "top_p" not in llm and "frequency_penalty" not in llm
    expert = data["plugins"]["ask_expert"]
    assert expert["model"] == "jarvis-cloud" and expert["base_url"] == "http://litellm:4000/v1"
