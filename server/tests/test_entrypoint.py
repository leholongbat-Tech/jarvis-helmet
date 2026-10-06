"""Tests for server/xiaozhi/entrypoint.py, the config template and the Jarvis prompt (SV-C).

Pure Python (PyYAML, jinja2 optional); the upstream merge test uses the xiaozhi-server
v0.9.7 tree from env XIAOZHI_SRC (default: out/upstream/...) and is skipped without it.
"""
import importlib.util
import io
import json
import os
import re
import string
import unicodedata
from contextlib import redirect_stderr

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
XIAOZHI = os.path.join(REPO, "server", "xiaozhi")
TEMPLATE = os.path.join(XIAOZHI, "data", ".config.yaml.tmpl")
PROMPT = os.path.join(XIAOZHI, "prompts", "jarvis-prompt.txt")
STRINGS = os.path.join(XIAOZHI, "patches", "strings_vi.json")
# Upstream xiaozhi-esp32-server v0.9.7 (CI clones it; locally see server/xiaozhi/patches/README.md).
DEFAULT_XIAOZHI_SRC = os.path.join(REPO, "out", "upstream", "xiaozhi-esp32-server", "main", "xiaozhi-server")

CJK_RE = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")
D15_WAKE = ["Jarvis", "jarvis", "JARVIS", "giắcvít", "Giắcvít", "quảngia", "Quảngia", "Quảnggia"]
D15_EXIT = ["tạmbiệt", "Tạmbiệt", "kếtthúc", "Kếtthúc", "goodbye", "Goodbye", "thatsall", "Thatsall"]
EXPECTED_VARS = {
    "PUBLIC_WS_URL",
    "PUBLIC_VISION_URL",
    "XIAOZHI_AUTH_KEY",
    "XIAOZHI_LOG_LEVEL",
    "LITELLM_MASTER_KEY",
    "TTS_VOICE",
    "VAD_MIN_SILENCE_MS",
}
FULL_ENV = {
    "PUBLIC_WS_URL": "ws://192.168.1.27:8100/xiaozhi/v1/",
    "PUBLIC_OTA_URL": "http://192.168.1.27:8003/xiaozhi/ota/",
    "XIAOZHI_AUTH_KEY": "test-auth-key-0123456789abcdef0123",
    "LITELLM_MASTER_KEY": "sk-test-master-key-0123456789",
    "TTS_VOICE": "Thiền Tâm Đức",
}


def _load_entrypoint():
    spec = importlib.util.spec_from_file_location("jarvis_entrypoint", os.path.join(XIAOZHI, "entrypoint.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ep = _load_entrypoint()


def _template():
    with open(TEMPLATE, "r", encoding="utf-8") as fh:
        return fh.read()


def _render(env=None):
    text, names, warnings = ep.render(_template(), dict(FULL_ENV if env is None else env))
    return yaml.safe_load(text), text, names, warnings


def _strings():
    with open(STRINGS, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ----------------------------------------------------------------------------- rendering


def test_template_placeholders_are_the_documented_set():
    assert set(ep.placeholders(_template())) == EXPECTED_VARS


def test_render_full_environment():
    cfg, text, names, warnings = _render()
    assert set(names) == EXPECTED_VARS
    assert warnings == []
    assert cfg["server"]["websocket"] == FULL_ENV["PUBLIC_WS_URL"]
    assert cfg["server"]["vision_explain"] == "http://192.168.1.27:8003/mcp/vision/explain"
    assert cfg["server"]["auth_key"] == FULL_ENV["XIAOZHI_AUTH_KEY"]
    assert cfg["LLM"]["JarvisLLM"]["api_key"] == FULL_ENV["LITELLM_MASTER_KEY"]
    assert cfg["plugins"]["ask_expert"]["api_key"] == FULL_ENV["LITELLM_MASTER_KEY"]
    assert cfg["TTS"]["JarvisTTS"]["voice"] == FULL_ENV["TTS_VOICE"]
    assert cfg["log"]["log_level"] == "INFO"
    assert "${" not in json.dumps(cfg, ensure_ascii=False)  # no unrendered placeholder in any value


def test_missing_variables_are_all_listed():
    env = {"TTS_VOICE": "x"}
    with pytest.raises(ep.MissingVariables) as info:
        ep.render(_template(), env)
    assert info.value.names == sorted(
        ["LITELLM_MASTER_KEY", "PUBLIC_OTA_URL (or PUBLIC_VISION_URL)", "PUBLIC_WS_URL", "XIAOZHI_AUTH_KEY"]
    )


def test_empty_value_counts_as_missing():
    env = dict(FULL_ENV, LITELLM_MASTER_KEY="   ")
    with pytest.raises(ep.MissingVariables) as info:
        ep.render(_template(), env)
    assert info.value.names == ["LITELLM_MASTER_KEY"]


def test_optional_log_level_default_and_override():
    cfg, *_ = _render(dict(FULL_ENV, XIAOZHI_LOG_LEVEL="debug"))
    assert cfg["log"]["log_level"] == "DEBUG"
    with pytest.raises(ep.RenderError):
        ep.render(_template(), dict(FULL_ENV, XIAOZHI_LOG_LEVEL="loud"))


def test_vision_url_derived_or_explicit():
    cfg, *_ = _render(dict(FULL_ENV, PUBLIC_OTA_URL="https://jarvis.example.com/xiaozhi/ota/"))
    assert cfg["server"]["vision_explain"] == "https://jarvis.example.com/mcp/vision/explain"
    cfg, *_ = _render(dict(FULL_ENV, PUBLIC_VISION_URL="http://10.0.0.5:8003/mcp/vision/explain"))
    assert cfg["server"]["vision_explain"] == "http://10.0.0.5:8003/mcp/vision/explain"


@pytest.mark.parametrize(
    "value",
    [
        'quote" and \\backslash',
        "hash # not a comment: colon",
        "line1\nline2\ttab",
        "đô-la $ và ${KHONG_PHAI_BIEN}",
        "'single' {brace} [list] &anchor *alias !tag %percent @at `tick`",
    ],
)
def test_special_characters_round_trip(value):
    # escaping test, not a strength test: short values are allowed explicitly
    cfg, *_ = _render(dict(FULL_ENV, LITELLM_MASTER_KEY=value, TTS_VOICE=value, JARVIS_ALLOW_WEAK_SECRETS="1"))
    assert cfg["LLM"]["JarvisLLM"]["api_key"] == value
    assert cfg["plugins"]["ask_expert"]["api_key"] == value
    assert cfg["TTS"]["JarvisTTS"]["voice"] == value


def test_invalid_dollar_in_template_is_rejected():
    with pytest.raises(ep.RenderError):
        ep.placeholders("price: $ 5\n")


def test_unquoted_placeholder_breaking_yaml_is_detected():
    with pytest.raises(ep.RenderError):
        ep.render("a: ${X}\n", {"X": "b: c: d"})


def test_websocket_url_must_be_ws():
    with pytest.raises(ep.RenderError):
        ep.render(_template(), dict(FULL_ENV, PUBLIC_WS_URL="http://192.168.1.27:8100/xiaozhi/v1/"))


@pytest.mark.parametrize("key, value", [
    ("XIAOZHI_AUTH_KEY", "change-me-long-random"),   # .env.example placeholders
    ("LITELLM_MASTER_KEY", "sk-change-me-long-random"),
    ("LITELLM_MASTER_KEY", "sk-1234"),               # LiteLLM's own published default
    ("XIAOZHI_AUTH_KEY", "short-key-15chr"),          # 15 characters
])
def test_placeholder_or_short_secret_is_refused(key, value):
    with pytest.raises(ep.RenderError) as info:
        _render(dict(FULL_ENV, **{key: value}))
    assert key in str(info.value) and value not in str(info.value)  # never echo the secret


def test_weak_secret_allowed_only_with_explicit_override():
    env = dict(FULL_ENV, XIAOZHI_AUTH_KEY="change-me-long-random", JARVIS_ALLOW_WEAK_SECRETS="1")
    cfg, _, _, warnings = _render(env)
    assert cfg["server"]["auth_key"] == "change-me-long-random"
    assert any("XIAOZHI_AUTH_KEY" in w and "JARVIS_ALLOW_WEAK_SECRETS" in w for w in warnings)
    with pytest.raises(ep.RenderError):  # any other value than "1" does not count
        _render(dict(env, JARVIS_ALLOW_WEAK_SECRETS="yes"))


def test_main_refuses_placeholder_secret_with_exit_2(tmp_path):
    template = tmp_path / "t.tmpl"
    template.write_text(_template(), encoding="utf-8")
    env = dict(FULL_ENV, JARVIS_CONFIG_TEMPLATE=str(template), XIAOZHI_DIR=str(tmp_path / "xz"),
               LITELLM_MASTER_KEY="sk-change-me-long-random")
    err = io.StringIO()
    with redirect_stderr(err):
        rc = ep.main([], env, exec_fn=lambda *a: pytest.fail("must not exec"))
    assert rc == 2 and "LITELLM_MASTER_KEY" in err.getvalue()
    assert not (tmp_path / "xz" / "data" / ".config.yaml").exists()


def test_vad_min_silence_default_and_override():
    cfg, *_ = _render()
    assert cfg["VAD"]["SileroVAD"]["min_silence_duration_ms"] == "700"  # upstream does int(...)
    cfg, *_ = _render(dict(FULL_ENV, VAD_MIN_SILENCE_MS=" 600 "))
    assert cfg["VAD"]["SileroVAD"]["min_silence_duration_ms"] == "600"
    for bad in ("abc", "50", "7000", "700ms", "-1"):
        with pytest.raises(ep.RenderError):
            _render(dict(FULL_ENV, VAD_MIN_SILENCE_MS=bad))


# ----------------------------------------------------------------------------- main()


def test_main_renders_and_execs_upstream_command(tmp_path):
    calls = []
    env = dict(FULL_ENV, JARVIS_CONFIG_TEMPLATE=TEMPLATE, XIAOZHI_DIR=str(tmp_path))
    cwd = os.getcwd()
    try:
        rc = ep.main(argv=[], environ=env, exec_fn=lambda prog, args: calls.append((prog, args, os.getcwd())))
    finally:
        os.chdir(cwd)
    assert rc == 0
    assert calls == [("python", ["python", "app.py"], str(tmp_path))]
    out = tmp_path / "data" / ".config.yaml"
    cfg = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert cfg["server"]["auth_key"] == FULL_ENV["XIAOZHI_AUTH_KEY"]
    if os.name == "posix":
        assert (out.stat().st_mode & 0o777) == 0o600


def test_main_passes_custom_command(tmp_path):
    calls = []
    env = dict(FULL_ENV, JARVIS_CONFIG_TEMPLATE=TEMPLATE, XIAOZHI_DIR=str(tmp_path))
    cwd = os.getcwd()
    try:
        ep.main(argv=["python", "-c", "print(1)"], environ=env, exec_fn=lambda p, a: calls.append(a))
    finally:
        os.chdir(cwd)
    assert calls == [["python", "-c", "print(1)"]]


def test_main_missing_variables_exit_2_and_lists_them(tmp_path):
    env = {"JARVIS_CONFIG_TEMPLATE": TEMPLATE, "XIAOZHI_DIR": str(tmp_path), "TTS_VOICE": "x"}
    err = io.StringIO()
    with redirect_stderr(err):
        rc = ep.main(argv=[], environ=env, exec_fn=lambda p, a: pytest.fail("must not exec"))
    assert rc == 2
    message = err.getvalue()
    for name in ("LITELLM_MASTER_KEY", "PUBLIC_WS_URL", "XIAOZHI_AUTH_KEY", "PUBLIC_OTA_URL"):
        assert name in message
    assert not (tmp_path / "data" / ".config.yaml").exists()


def test_main_never_prints_secrets(tmp_path):
    env = dict(FULL_ENV, JARVIS_CONFIG_TEMPLATE=TEMPLATE, XIAOZHI_DIR=str(tmp_path))
    err = io.StringIO()
    cwd = os.getcwd()
    try:
        with redirect_stderr(err):
            ep.main(argv=[], environ=env, exec_fn=lambda p, a: None)
    finally:
        os.chdir(cwd)
    assert FULL_ENV["XIAOZHI_AUTH_KEY"] not in err.getvalue()
    assert FULL_ENV["LITELLM_MASTER_KEY"] not in err.getvalue()


def test_main_skip_render(tmp_path):
    calls = []
    env = {"JARVIS_SKIP_RENDER": "1", "XIAOZHI_DIR": str(tmp_path)}
    cwd = os.getcwd()
    try:
        assert ep.main(argv=["true"], environ=env, exec_fn=lambda p, a: calls.append(a)) == 0
    finally:
        os.chdir(cwd)
    assert calls == [["true"]]


# ----------------------------------------------------------------------------- template content


def test_template_contains_no_secrets():
    text = _template()
    assert not re.search(r"sk-[A-Za-z0-9]{12,}", text)
    assert "cfast_" not in text and "Bearer " not in text and "AKIA" not in text
    assert not re.search(r"\b[0-9a-f]{32,}\b", text, re.I)
    assert not re.search(r"\b[A-Za-z0-9+/_-]{40,}\b", text)
    for match in re.finditer(r"^\s*(api_key|auth_key|secret|token|password)\s*:\s*(.+)$", text, re.M):
        value = match.group(2).strip()
        assert value in ('"none"', "none", "null") or re.fullmatch(r'"\$\{[A-Z0-9_]+\}"', value), match.group(0)


def test_every_placeholder_is_double_quoted_and_dollars_are_escaped():
    text = _template()
    for line in text.splitlines():
        for match in re.finditer(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", line):
            if line[: match.start()].endswith("$"):
                continue  # $${X}: literal in a comment
            before, after = line[: match.start()], line[match.end():]
            assert before.count('"') % 2 == 1 and '"' in after, "unquoted placeholder: %s" % line
    stripped = text.replace("$$", "")
    assert not re.search(r"\$(?!\{[A-Za-z_][A-Za-z0-9_]*\})", stripped)


def test_template_static_contract_keys():
    cfg, *_ = _render()
    server = cfg["server"]
    assert (server["ip"], server["port"], server["http_port"]) == ("0.0.0.0", 8000, 8003)
    assert server["timezone_offset"] == 7 and isinstance(server["timezone_offset"], int)
    assert server["auth"]["enabled"] is True
    assert server["auth"]["allowed_devices"] == []
    assert isinstance(server["auth"]["expire_seconds"], int) and server["auth"]["expire_seconds"] > 0
    assert server["mqtt_gateway"] is None
    assert ".SSS" in cfg["log"]["log_format_file"] and ".SSS" in cfg["log"]["log_format"]
    assert cfg["delete_audio"] is True
    assert cfg["enable_greeting"] is False
    assert cfg["enable_wakeup_words_response_cache"] is True
    assert cfg["wakeup_words"] == D15_WAKE
    assert cfg["exit_commands"] == D15_EXIT
    for word in D15_WAKE + D15_EXIT:
        assert unicodedata.is_normalized("NFC", word)
        assert not re.search(r"[\s!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~]", word)
    assert cfg["mcp_endpoint"] is None
    assert cfg["xiaozhi"]["audio_params"]["sample_rate"] == 24000
    assert cfg["xiaozhi"]["audio_params"]["frame_duration"] == 60
    assert cfg["jarvis"]["tool_display"] is False
    assert cfg["prompt_template"] == "prompts/jarvis-prompt.txt"
    assert "thưa ngài" in cfg["system_error_response"]
    assert cfg["end_prompt"]["enable"] is True and cfg["end_prompt"]["prompt"].strip()
    assert "J.A.R.V.I.S" in cfg["prompt"]
    assert cfg["selected_module"] == {
        "VAD": "SileroVAD",
        "ASR": "OpenaiASR",
        "LLM": "JarvisLLM",
        "TTS": "JarvisTTS",
        "Memory": "nomem",
        "Intent": "function_call",
    }
    assert int(cfg["VAD"]["SileroVAD"]["min_silence_duration_ms"]) == 700
    asr = cfg["ASR"]["OpenaiASR"]
    assert asr == {
        "type": "openai",
        "api_key": "none",
        "base_url": "http://asr:9000/v1/audio/transcriptions",
        "model_name": "whisper-large-v3",
        "output_dir": "tmp/asr",
        "timeout_s": 30,
    }
    llm = cfg["LLM"]["JarvisLLM"]
    assert llm["type"] == "openai" and llm["base_url"] == "http://litellm:4000/v1"
    assert llm["model_name"] == "jarvis-main" and llm["max_tokens"] == 300 and llm["temperature"] == 0.6
    assert "top_p" not in llm and "frequency_penalty" not in llm
    tts = cfg["TTS"]["JarvisTTS"]
    assert tts["type"] == "jarvis_stream" and tts["api_url"] == "http://tts-router:8880/v1/audio/speech"
    assert tts["sample_rate"] == 24000 and tts["output_dir"] == "tmp/tts"
    assert "language" not in tts  # would feed {{language}}; the Jarvis prompt answers in the user's language
    assert cfg["Intent"]["function_call"]["functions"] == ["helmet_tasks", "ask_expert"]
    plugins = cfg["plugins"]
    assert plugins["helmet_tasks"] == {
        "tasks_file": "/opt/jarvis/tasks/tasks.yaml",
        "schema_file": "/opt/jarvis/tasks/schema.json",
        "quick_phrase_threshold": 90,
        "merge_window_ms": 80,
        "mcp_timeout_s": 5,
    }
    ask = plugins["ask_expert"]
    assert (ask["base_url"], ask["model"], ask["timeout_s"], ask["max_tokens"]) == (
        "http://litellm:4000/v1", "jarvis-cloud", 20, 400)
    assert ask["timeout_s"] < cfg["tool_call_timeout"]


def test_always_loaded_tool_descriptions_match_strings_file():
    cfg, *_ = _render()
    strings = _strings()
    assert cfg["plugins"]["handle_exit_intent"]["description"] == strings["handle_exit_intent"]["description"]
    assert cfg["plugins"]["get_lunar"]["description"] == strings["get_lunar"]["description"]


def test_rendered_config_has_no_cjk():
    _, text, *_ = _render()
    assert not CJK_RE.search(text)


def _merge(default, custom):
    """Same semantics as upstream config/config_loader.py merge_configs."""
    if not isinstance(default, dict) or not isinstance(custom, dict):
        return custom
    merged = dict(default)
    for key, value in custom.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def test_merged_with_upstream_defaults_active_keys_have_no_chinese():
    src = os.environ.get("XIAOZHI_SRC", DEFAULT_XIAOZHI_SRC)
    path = os.path.join(src, "config.yaml")
    if not os.path.isfile(path):
        pytest.skip("upstream xiaozhi-server tree not available (set XIAOZHI_SRC)")
    with open(path, "r", encoding="utf-8") as fh:
        default = yaml.safe_load(fh)
    cfg = _merge(default, _render()[0])
    sel = cfg["selected_module"]
    active = {
        "prompt": cfg["prompt"],
        "system_error_response": cfg["system_error_response"],
        "end_prompt": cfg["end_prompt"],
        "wakeup_words": cfg["wakeup_words"],
        "exit_commands": cfg["exit_commands"],
        "server": cfg["server"],
        "log": cfg["log"],
        "xiaozhi": cfg["xiaozhi"],
        "selected_module": sel,
        "Intent": cfg["Intent"][sel["Intent"]],
        "plugins": {k: cfg["plugins"][k] for k in ("helmet_tasks", "ask_expert", "get_lunar", "handle_exit_intent")},
    }
    for module in ("VAD", "ASR", "LLM", "TTS", "Memory"):
        active[module] = cfg[module][sel[module]]
    dumped = json.dumps(active, ensure_ascii=False)
    assert not CJK_RE.search(dumped), dumped
    assert "你的" not in cfg["server"]["websocket"]
    assert cfg["VAD"]["SileroVAD"]["model_dir"] == "models/snakers4_silero-vad"  # inherited by deep merge
    assert cfg["server"]["auth"]["allowed_devices"] == []  # list replaced, upstream dummy MAC gone
    assert cfg.get("manager-api", {}).get("url") in (None, "")  # minimal mode


# ----------------------------------------------------------------------------- prompt


def _prompt():
    with open(PROMPT, "r", encoding="utf-8") as fh:
        return fh.read()


def test_prompt_variables_and_forbidden_substrings():
    text = _prompt()
    assert "{{base_prompt}}" in text and "{{current_time}}" in text
    assert "<memory>" in text and "</memory>" in text
    for forbidden in ("local_address", "weather_info", "dynamic_context", "today_weekday", "lunar_date", "language}}",
                      "{{ language", "{{language"):
        assert forbidden not in text, forbidden
    assert "{%" not in text and "{#" not in text


def test_prompt_has_no_emoji_and_no_chinese():
    text = _prompt()
    assert not CJK_RE.search(text)
    assert not re.search("[\U0001F300-\U0001FAFF\u2600-\u27BF]", text)


def test_prompt_covers_jarvis_rules():
    text = _prompt()
    for needle in ("thưa ngài", "Sir", "task_", "self_system_get_status", "ask_expert", "handle_exit_intent",
                   "direct_answer", "ASR", "Jarvis", "quản gia", "không markdown"):
        assert needle in text, needle


def test_prompt_renders_like_upstream_prompt_manager():
    jinja2 = pytest.importorskip("jinja2")
    persona = _render()[0]["prompt"]
    rendered = jinja2.Template(_prompt()).render(
        base_prompt=persona,
        current_time="{{current_time}}",
        today_date="2026-10-06",
        today_weekday="@@WEEKDAY@@",
        lunar_date="@@LUNAR@@",
        local_address="@@ADDRESS@@",
        weather_info="@@WEATHER@@",
        emojiList=["@@EMOJI@@"],
        device_id="aa:bb",
        client_ip="1.2.3.4",
        dynamic_context="@@CONTEXT@@",
        language="@@LANGUAGE@@",
        emoji_enabled=True,
    )
    assert persona.strip() in rendered
    assert "{{current_time}}" in rendered  # replaced per LLM call by core/utils/dialogue.py
    assert "2026-10-06" in rendered
    assert "@@" not in rendered  # no Chinese-valued / network-backed / forced-language variable is used


def test_template_is_valid_string_template_text():
    names = ep.placeholders(_template())
    assert names
    string.Template(_template()).substitute({n: "v" for n in names})
