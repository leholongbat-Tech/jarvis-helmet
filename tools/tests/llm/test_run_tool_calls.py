"""Tests for the LLM tool-call test kit (tools/tests/llm): case file, request builder, scoring, and
device_tools.json kept in sync with the firmware's MCP tool registrations. No network access.

    .venv/Scripts/python -m pytest tools/tests/llm -q
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BOARD = REPO / "firmware" / "main" / "boards" / "jarvis-helmet"
sys.path.insert(0, str(HERE))

import run_tool_calls as rtc  # noqa: E402

COMMON_TOOLS = ["self.get_device_status", "self.audio_speaker.set_volume", "self.screen.set_brightness",
                "self.screen.set_theme"]  # McpServer::AddCommonTools for a board with LCD + backlight, no camera


@pytest.fixture(scope="module")
def cases():
    return rtc.load_cases(HERE / "tool_calls.yaml")


@pytest.fixture(scope="module")
def ctx(cases):
    settings, _ = cases
    args = rtc.parse_args([])
    return rtc.build_context(args, settings, rtc.Sources())


# ---- case file ----------------------------------------------------------------------------------
def test_case_counts(cases):
    settings, items = cases
    assert sum(c["kind"] == "command" for c in items) == 30
    assert sum(c["kind"] == "no_tool" for c in items) == 10
    assert settings["threshold"] == 0.95 and settings["min_no_tool_rate"] == 1.0
    assert settings["max_tokens"] == 300 and settings["temperature"] == 0.6


def test_every_expected_tool_is_offered(cases, ctx):
    offered = {t["function"]["name"] for t in ctx.tools}
    expected = {opt["tool"] for c in cases[1] if c["kind"] == "command"
                for exp in c["calls"] for opt in rtc.options_of(exp)}
    assert expected <= offered, sorted(expected - offered)
    assert set(cases[0]["tolerated_extra_tools"]) <= offered


def test_expected_args_satisfy_the_tool_schemas(cases, ctx):
    """A literal expected value must itself be valid for the tool (else the case can never pass)."""
    jsonschema = pytest.importorskip("jsonschema")
    schemas = {t["function"]["name"]: t["function"]["parameters"] for t in ctx.tools}
    for case in cases[1]:
        for exp in case.get("calls") or []:
            for opt in rtc.options_of(exp):
                literal = {k: v for k, v in (opt.get("args") or {}).items() if not isinstance(v, dict)}
                schema = dict(schemas[opt["tool"]], required=[])
                errors = list(jsonschema.Draft202012Validator(schema).iter_errors(literal))
                assert not errors, (case["id"], opt, errors[0].message if errors else "")


# ---- request builder ----------------------------------------------------------------------------
def test_request_shape(ctx):
    body = rtc.build_request(ctx, "mở mặt nạ")
    assert body["model"] == "jarvis-main" and body["stream"] is True
    assert body["max_tokens"] == 300 and body["temperature"] == 0.6
    assert "top_p" not in body and "frequency_penalty" not in body and "tool_choice" not in body
    names = [t["function"]["name"] for t in body["tools"]]
    assert names[-1] == "direct_answer"
    assert names[:2] == ["handle_exit_intent", "get_lunar"]
    assert {"task_open_mask", "ask_expert", "self_mask_open", "self_eyes_set"} <= set(names)
    assert all(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", n) for n in names), "Anthropic tool-name rule"
    msgs = body["messages"]
    assert msgs[0]["role"] == "system" and "{{current_time}}" not in msgs[0]["content"]
    assert msgs[-1] == {"role": "user", "content": "mở mặt nạ"}
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "direct_answer"


def test_device_functions_sanitize_names_and_descriptions():
    tools = [{"name": "self.a.b", "description": "call self.c.d first", "inputSchema": {"type": "object"}},
             {"name": "self.c.d", "description": "x", "inputSchema": {"properties": {"v": {"type": "integer"}},
                                                                       "required": ["v", 3]}}]
    funcs = rtc.device_functions(tools)
    assert [f["function"]["name"] for f in funcs] == ["self_a_b", "self_c_d"]
    assert funcs[0]["function"]["description"] == "call self_c_d first"
    assert funcs[1]["function"]["parameters"]["required"] == ["v"]


def test_sanitize_tool_name():
    assert rtc.sanitize_tool_name("self.mask.set_position") == "self_mask_set_position"
    assert rtc.sanitize_tool_name("a b/c-d") == "a_b_c-d"
    assert rtc.sanitize_tool_name("工具.x") == "工具_x"


# ---- streaming / parsing ------------------------------------------------------------------------
def test_merge_tool_calls():
    calls: list = []
    rtc.merge_tool_calls(calls, [{"index": 0, "id": "c1", "function": {"name": "self_eyes_set", "arguments": "{\"on\""}}])
    rtc.merge_tool_calls(calls, [{"index": 0, "function": {"arguments": ": true}"}}])
    rtc.merge_tool_calls(calls, [{"function": {"name": "task_open_mask", "arguments": "{}"}}])  # no index
    assert calls == [{"id": "c1", "name": "self_eyes_set", "arguments": "{\"on\": true}"},
                     {"id": "", "name": "task_open_mask", "arguments": "{}"}]
    with pytest.raises(IndexError):  # like upstream ConnectionHandler._merge_tool_calls
        rtc.merge_tool_calls(calls, [{"index": 5, "function": {"name": "x"}}])


def test_parse_stream_and_interpret():
    lines = [
        'data: {"choices":[{"delta":{"content":"Vâng"}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"a","function":{"name":"direct_answer","arguments":"{\\"response\\": \\"hi\\"}"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":1,"id":"b","function":{"name":"self_output_set","arguments":"{\\"on\\": false}"}}]}}]}',
        "data: [DONE]",
    ]
    reply = rtc.Reply()
    rtc.parse_stream(lines, reply, t0=0.0)
    real, direct, err = rtc.interpret(reply)
    assert err is None and reply.content == "Vâng"
    assert real == [{"name": "self_output_set", "args": {"on": False}, "raw": '{"on": false}'}]
    assert direct[0]["args"] == {"response": "hi"}
    bad = rtc.Reply()
    rtc.parse_stream(['data: {"error": {"message": "boom"}}'], bad, t0=0.0)
    assert "boom" in bad.error


def test_interpret_qwen_text_tool_call():
    reply = rtc.Reply(content='<tool_call>\n{"name": "self_eyes_set", "arguments": {"on": true}}\n</tool_call>')
    real, direct, err = rtc.interpret(reply)
    assert err is None and real[0]["name"] == "self_eyes_set" and real[0]["args"] == {"on": True}


# ---- scoring ------------------------------------------------------------------------------------
def test_match_value_is_type_strict():
    assert rtc.match_value(False, False) and not rtc.match_value(False, "false") and not rtc.match_value(False, 0)
    assert rtc.match_value(30, 30) and rtc.match_value(30, 30.0) and not rtc.match_value(30, "30")
    assert not rtc.match_value(1, True)
    assert rtc.match_value({"min": 10, "max": 40}, 25) and not rtc.match_value({"min": 10, "max": 40}, 5)
    assert not rtc.match_value({"max": 40}, True)
    assert rtc.match_value({"contains": "XIN CHÀO"}, "hiện chữ xin chào lên")
    assert rtc.match_value({"contains_any": ["sky", "bầu trời"]}, "Tại sao Bầu Trời xanh?")
    assert rtc.match_value({"one_of": ["blink", "flicker"]}, "flicker")


def call(name, args):
    return {"name": name, "args": args, "raw": json.dumps(args)}


def test_score_case():
    case = {"kind": "command", "calls": [{"one_of": [{"tool": "task_open_mask"}, {"tool": "self_mask_open"}]}]}
    assert rtc.score_case(case, [call("task_open_mask", {})], [])[0] == "PASS"
    assert rtc.score_case(case, [call("self_mask_open", {"speed": 70})], [])[0] == "ALT"
    assert rtc.score_case(case, [call("self_eyes_set", {"on": True})], [])[0] == "FAIL"
    verdict, note = rtc.score_case(case, [call("self_mask_get_state", {}), call("task_open_mask", {})],
                                   ["self_mask_get_state"])
    assert verdict == "PASS" and "ignored self_mask_get_state" in note
    assert rtc.score_case(case, [], [])[0] == "FAIL"
    multi = {"kind": "command", "calls": [{"tool": "task_open_mask"}, {"tool": "task_eyes_on"}]}
    assert rtc.score_case(multi, [call("task_eyes_on", {}), call("task_open_mask", {})], [])[0] == "FAIL"
    none = {"kind": "no_tool"}
    assert rtc.score_case(none, [], [])[0] == "PASS"
    assert rtc.score_case(none, [call("get_lunar", {})], [])[0] == "FAIL"


def test_schema_errors(ctx):
    pytest.importorskip("jsonschema")
    assert rtc.schema_errors(ctx.tools, [call("self_mask_open", {"speed": 70})]) == []
    assert "minimum" in rtc.schema_errors(ctx.tools, [call("self_mask_open", {"speed": 5})])[0]
    assert rtc.schema_errors(ctx.tools, [call("self_eyes_set", {"on": "true"})])
    assert rtc.schema_errors(ctx.tools, [call("self_eyes_set", {})]), "missing required 'on'"
    assert "unknown tool" in rtc.schema_errors(ctx.tools, [call("self_fly", {})])[0]
    bad_json = {"name": "self_output_set", "args": None, "raw": "{oops"}
    assert "not a JSON object" in rtc.schema_errors(ctx.tools, [bad_json])[0]


def test_llm_params_follow_the_xiaozhi_template(ctx):
    assert ctx.params == {"max_tokens": 300, "temperature": 0.6}


def test_dry_run_main(capsys):
    assert rtc.main(["--dry-run", "--only", "open_vi,none_name"]) == 0
    assert "[dry-run] built 2 requests" in capsys.readouterr().out


# ---- device_tools.json vs firmware ----------------------------------------------------------------
INT_RE = re.compile(r"^-?\d+$")


def _constants() -> dict:
    """Integer constexpr constants of the board (literals or simple expressions of other constants)."""
    raw = {}
    for path in [BOARD / "helmet_common.h", *sorted(BOARD.glob("*.cc"))]:
        for name, expr in re.findall(r"constexpr\s+(?:int|size_t|uint\d+_t|int\d+_t)\s+(k\w+)\s*=\s*([^;]+);",
                                     path.read_text(encoding="utf-8")):
            raw[name] = expr
    values: dict = {}
    for _ in range(5):  # constants may refer to constants defined in other files
        for name, expr in raw.items():
            if name not in values:
                try:
                    values[name] = _resolve(expr, values)
                except (KeyError, AssertionError):
                    pass
    return values


def _resolve(expr: str, consts: dict) -> int:
    expr = expr.strip().replace("helmet::", "")
    expr = re.sub(r"\bk\w+\b", lambda m: str(consts[m.group(0)]), expr)
    assert re.fullmatch(r"[\d\s+\-*()]+", expr), expr
    return int(eval(expr, {"__builtins__": {}}))  # digits and + - * only


def _split_args(text: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _firmware_tools() -> dict:
    """{tool name: {prop: schema}} for every AddTool (not AddUserOnlyTool) of the board sources."""
    consts = _constants()
    tools = {}
    for path in sorted(BOARD.glob("*.cc")):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"\b(AddTool|AddUserOnlyTool)\(\s*\"([^\"]+)\"", src):
            if m.group(1) != "AddTool":
                continue
            nxt = re.search(r"\b(?:AddTool|AddUserOnlyTool)\(", src[m.end():])
            block = src[m.end(): m.end() + nxt.start()] if nxt else src[m.end():]
            props = {}
            for pm in re.finditer(r"Property\(", block):
                depth, i = 1, pm.end()
                while depth:
                    depth += {"(": 1, ")": -1}.get(block[i], 0)
                    i += 1
                args = _split_args(block[pm.end(): i - 1])
                name = args[0].strip('"')
                kind = {"kPropertyTypeInteger": "integer", "kPropertyTypeString": "string",
                        "kPropertyTypeBoolean": "boolean"}[args[1]]
                schema = {"type": kind}
                nums = [_resolve(a, consts) for a in args[2:]]
                if kind == "integer" and len(nums) == 3:
                    schema.update(default=nums[0], minimum=nums[1], maximum=nums[2])
                elif kind == "integer" and len(nums) == 2:
                    schema.update(minimum=nums[0], maximum=nums[1])
                elif nums:
                    schema["default"] = nums[0]
                ml = re.match(r"\s*\.SetMaxLength\(([^)]+)\)", block[i:])
                if ml:
                    schema["maxLength"] = _resolve(ml.group(1), consts)
                props[name] = schema
            tools[m.group(2)] = props
    return tools


@pytest.mark.skipif(not BOARD.is_dir(), reason="firmware board sources not present")
def test_device_tools_match_firmware():
    data = json.loads((HERE / "device_tools.json").read_text(encoding="utf-8"))
    listed = {t["name"]: t for t in data["tools"]}
    firmware = _firmware_tools()
    common_src = (REPO / "firmware" / "main" / "mcp_server.cc").read_text(encoding="utf-8")
    for name in COMMON_TOOLS:
        assert f'"{name}"' in common_src
    assert set(listed) == set(firmware) | set(COMMON_TOOLS), (
        f"missing: {sorted(set(firmware) | set(COMMON_TOOLS) - set(listed))}, "
        f"extra: {sorted(set(listed) - set(firmware) - set(COMMON_TOOLS))}")
    for name, props in firmware.items():
        schema = listed[name]["inputSchema"]
        assert schema.get("properties", {}) == props, name
        required = sorted(p for p, s in props.items() if "default" not in s)
        assert sorted(schema.get("required", [])) == required, name
