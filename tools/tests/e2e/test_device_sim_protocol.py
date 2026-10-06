"""Protocol tests for device_sim.py (no external server needed).

Checks the simulator against the firmware sources (xiaozhi-esp32 v2.5.0):
main/mcp_server.{h,cc} (tool JSON, tools/list paging, tools/call errors), main/ota.cc,
main/protocols/{protocol,websocket_protocol}.cc, plus contract §3.5 (tool list), D13, D23.
The last section runs the CLI end to end against a scripted in-process server.
"""

import asyncio
import base64
import hashlib
import hmac
import http.server
import json
import math
import re
import threading
import wave
from pathlib import Path

import pytest

import device_sim as ds

try:
    import av  # noqa: F401
    import numpy as np
    HAVE_AUDIO = True
except ImportError:  # pragma: no cover - audio extras missing
    HAVE_AUDIO = False

needs_audio = pytest.mark.skipif(not HAVE_AUDIO, reason="PyAV/numpy not installed")
FIXTURES_DIR = ds.REPO_ROOT / "tools" / "tests" / "fixtures" / "sequences"

I, S, B = "integer", "string", "boolean"
# contract §3.5: name -> ([(prop, type, default, minimum, maximum, maxLength)], user_only)
CONTRACT_TOOLS = {
    "self.mask.open": ([("speed", I, 70, 10, 100, None)], False),
    "self.mask.close": ([("speed", I, 70, 10, 100, None)], False),
    "self.mask.set_position": ([("percent", I, None, 0, 100, None),
                                ("speed", I, 70, 10, 100, None)], False),
    "self.mask.get_state": ([], False),
    "self.servo.calibrate": ([("servo", I, None, 0, 1, None),
                              ("angle", I, None, 0, 180, None)], True),
    "self.servo.save_calibration": ([("servo", I, None, 0, 1, None),
                                     ("closed_angle", I, None, 0, 180, None),
                                     ("open_angle", I, None, 0, 180, None)], True),
    "self.eyes.set": ([("on", B, None, None, None, None),
                       ("brightness", I, 100, 0, 100, None)], False),
    "self.eyes.effect": ([("name", S, None, None, None, 16),
                          ("duration_ms", I, 1000, 100, 10000, None)], False),
    "self.output.set": ([("on", B, None, None, None, None)], False),
    "self.sequence.run": ([("steps", S, None, None, None, 4000)], False),
    "self.sequence.run_saved": ([("name", S, None, None, None, 11)], False),
    "self.sequence.save": ([("name", S, None, None, None, 11),
                            ("steps", S, None, None, None, 4000)], False),
    "self.sequence.stop": ([], False),
    "self.display.show_info": ([("title", S, None, None, None, 96),
                                ("text", S, None, None, None, 800),
                                ("seconds", I, 5, 1, 30, None)], False),
    "self.system.get_status": ([], False),
}
UPSTREAM_COMMON = ["self.get_device_status", "self.audio_speaker.set_volume",
                   "self.screen.set_brightness", "self.screen.set_theme"]
UPSTREAM_USER_ONLY = ["self.get_system_info", "self.reboot", "self.upgrade_firmware",
                      "self.screen.get_info", "self.screen.snapshot",
                      "self.screen.preview_image", "self.assets.set_download_url"]

OPEN_MASK = [{"eyes": {"effect": "fade_out", "duration_ms": 300}},
             {"mask": {"action": "open", "speed": 80}}]
EYES_ON = [{"eyes": {"effect": "power_up", "duration_ms": 1000}}]
INTERFACES_SAMPLE = [  # docs/interfaces.md §3
    {"eyes": {"effect": "fade_out", "duration_ms": 300}},
    {"mask": {"action": "open", "speed": 80}},
    {"delay": 300},
    {"eyes": {"on": True, "brightness": 100}},
    {"output": {"on": True}},
    {"servo": {"id": 0, "angle": 45, "duration_ms": 500}},
    {"display": {"title": "COMBAT MODE", "text": "Hệ thống vũ khí sẵn sàng", "seconds": 3}},
    {"sound": {"name": "power_up"}},
]


def new_sim(page_size=ds.MCP_MAX_PAYLOAD):
    box = ds.ToolBox(validator=ds.SequenceValidator("builtin"))
    return ds.McpServerSim(box.build(), max_payload=page_size), box


def rpc(sim, method, params=None, rid=5, raw=False):
    msg = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        msg["params"] = params
    reply = sim.handle(msg)
    return reply if raw else (json.loads(reply) if reply else None)


def call(sim, name, args=None, rid=5, omit_args=False):
    params = {"name": name}
    if not omit_args:
        params["arguments"] = {} if args is None else args
    return rpc(sim, "tools/call", params, rid)


def err(reply):
    assert "error" in reply, reply
    return reply["error"]["code"], reply["error"]["message"]


def ok_text(reply):
    assert "result" in reply, reply
    assert reply["result"]["isError"] is False
    assert list(reply["result"]) == ["content", "isError"]
    content = reply["result"]["content"]
    assert len(content) == 1 and content[0]["type"] == "text"
    return content[0]["text"]


# --------------------------------------------------------------------------- tool list format

def test_contract_tools_have_exact_property_schemas():
    tools = {t.name: t for t in ds.build_helmet_tool_list()}
    for name, (props, user_only) in CONTRACT_TOOLS.items():
        assert name in tools, name
        tool = tools[name]
        assert tool.user_only is user_only, name
        doc = json.loads(tool.to_json())
        schema = doc["inputSchema"]
        assert list(schema["properties"]) == [p[0] for p in props], name
        for pname, ptype, default, lo, hi, max_len in props:
            pj = schema["properties"][pname]
            expected_keys = ["type"] + (["default"] if default is not None else []) + \
                (["minimum"] if lo is not None else []) + (["maximum"] if hi is not None else []) + \
                (["maxLength"] if max_len is not None else [])
            assert list(pj) == expected_keys, (name, pname, pj)
            assert pj["type"] == ptype
            if default is not None:
                assert pj["default"] == default
            if lo is not None:
                assert (pj["minimum"], pj["maximum"]) == (lo, hi)
            if max_len is not None:
                assert pj["maxLength"] == max_len
        required = [p[0] for p in props if p[2] is None]
        assert schema.get("required", []) == required, name
        if not required:
            assert "required" not in schema
        # bilingual description (VI / EN), contract §0.4
        assert " / " in tool.description and ds.VI_DIACRITIC_RE.search(tool.description)


def test_tool_json_is_byte_exact_with_firmware_layout():
    tools = {t.name: t for t in ds.build_helmet_tool_list()}
    t = tools["self.mask.set_position"]
    assert t.to_json() == (
        '{"name":"self.mask.set_position","description":' + json.dumps(t.description,
                                                                        ensure_ascii=False)
        + ',"inputSchema":{"type":"object","properties":{"percent":{"type":"integer",'
        '"minimum":0,"maximum":100},"speed":{"type":"integer","default":70,"minimum":10,'
        '"maximum":100}},"required":["percent"]}}')
    t = tools["self.servo.calibrate"]
    assert t.to_json().endswith(',"required":["servo","angle"]},"annotations":{"audience":'
                                '["user"]}}')
    t = tools["self.sequence.stop"]
    assert t.to_json().endswith('"inputSchema":{"type":"object","properties":{}}}')
    t = tools["self.eyes.set"]
    assert '"properties":{"on":{"type":"boolean"},"brightness":{"type":"integer","default":100,' \
           '"minimum":0,"maximum":100}},"required":["on"]' in t.to_json()
    t = tools["self.get_device_status"]
    assert "\\n" in t.to_json() and "\n" not in t.to_json()  # escaped like cJSON


def test_upstream_quirks_and_order():
    names = [t.name for t in ds.build_helmet_tool_list()]
    assert names[:4] == UPSTREAM_COMMON          # AddCommonTools moves them to the front
    assert names[-len(UPSTREAM_USER_ONLY):] == UPSTREAM_USER_ONLY
    assert set(CONTRACT_TOOLS) <= set(names)
    upgrade = next(t for t in ds.build_helmet_tool_list() if t.name == "self.upgrade_firmware")
    doc = json.loads(upgrade.to_json())
    assert doc["inputSchema"]["properties"]["url"]["default"].startswith("The URL of")
    assert "required" not in doc["inputSchema"]
    # the server sanitizes names; they must stay unique
    sanitized = [re.sub(r"[^a-zA-Z0-9_\-]", "_", n) for n in names]
    assert len(set(sanitized)) == len(sanitized)
    assert "self_sequence_run" in sanitized and "self_mask_open" in sanitized


def test_property_rejects_invalid_declarations():
    with pytest.raises(ValueError):
        ds.Property("x", S, minimum=0, maximum=1)
    with pytest.raises(ValueError):
        ds.Property("x", I, max_length=3)
    with pytest.raises(ValueError):
        ds.Property("x", I, 200, 0, 100)


# --------------------------------------------------------------------------- initialize / tools/list

def test_initialize_reply_exact_and_vision_captured():
    sim, _ = new_sim()
    reply = rpc(sim, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {
        "vision": {"url": "http://srv/mcp/vision/explain", "token": "t"}}}, rid=1, raw=True)
    assert reply == ('{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05",'
                     '"capabilities":{"tools":{}},"serverInfo":{"name":"jarvis-helmet",'
                     '"version":"2.5.0"}}}')
    assert sim.vision == {"url": "http://srv/mcp/vision/explain", "token": "t"}


def test_tools_list_hides_user_only_tools_by_default():
    sim, _ = new_sim()
    reply = rpc(sim, "tools/list", rid=2, raw=True)
    assert ds.utf8_len(reply) < ds.MCP_MAX_PAYLOAD
    result = json.loads(reply)["result"]
    assert "nextCursor" not in result
    names = [t["name"] for t in result["tools"]]
    assert names == [t.name for t in sim.tools if not t.user_only]
    assert all("annotations" not in t for t in result["tools"])
    with_user = rpc(sim, "tools/list", {"withUserTools": True}, rid=2)["result"]["tools"]
    annotated = [t["name"] for t in with_user if t.get("annotations") == {"audience": ["user"]}]
    assert annotated == [t.name for t in sim.tools if t.user_only]


@pytest.mark.parametrize("page_size", [1500, 2500, 4000])
def test_tools_list_pagination_matches_firmware(page_size):
    sim, _ = new_sim(page_size)
    seen, cursor, pages = [], "", 0
    while True:
        params = {"cursor": cursor} if cursor else None
        reply = rpc(sim, "tools/list", params, rid=2, raw=True)
        result = json.loads(reply)["result"]
        body = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        assert ds.utf8_len(body) + 30 <= page_size + 60  # page payload bounded
        names = [t["name"] for t in result["tools"]]
        assert names
        if seen:
            assert names[0] == cursor  # the cursor is the first tool of the next page
        seen += names
        pages += 1
        cursor = result.get("nextCursor", "")
        if not cursor:
            break
    assert pages > 1
    assert seen == [t.name for t in sim.tools if not t.user_only]


def test_tools_list_cursor_errors():
    sim, _ = new_sim()
    code, msg = err(rpc(sim, "tools/list", {"cursor": "no.such.tool"}, rid=2))
    assert (code, msg) == (-32603, "Failed to add tool  because of payload size limit")
    tiny, _ = new_sim(page_size=120)
    code, msg = err(rpc(tiny, "tools/list", rid=2))
    assert msg == "Failed to add tool self.get_device_status because of payload size limit"


def test_messages_the_firmware_ignores():
    sim, _ = new_sim()
    assert sim.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert sim.handle({"jsonrpc": "1.0", "id": 1, "method": "initialize"}) is None
    assert sim.handle({"jsonrpc": "2.0", "id": 1}) is None
    assert sim.handle({"jsonrpc": "2.0", "id": "1", "method": "tools/list"}) is None
    assert sim.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": []}) is None
    assert sim.handle(["not", "an", "object"]) is None
    code, msg = err(rpc(sim, "resources/list"))
    assert (code, msg) == (-32601, "Method not implemented: resources/list")


# --------------------------------------------------------------------------- tools/call

def test_tools_call_success_payload_exact():
    sim, box = new_sim()
    reply = sim.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                        "params": {"name": "self.mask.open", "arguments": {"speed": 80}}})
    assert reply == ('{"jsonrpc":"2.0","id":7,"result":{"content":[{"type":"text","text":'
                     '"opening"}],"isError":false}}')
    assert box.helmet.mask_state()["state"] == "moving"


@pytest.mark.parametrize("name,args,code,message", [
    ("self.nope", {}, -32602, "Unknown tool: self.nope"),
    ("self.sequence.run", {}, -32602, "Missing required argument: steps"),
    ("self.eyes.set", {"on": 1}, -32602, "Invalid type for argument: on"),
    ("self.mask.open", {"speed": True}, -32602, "Invalid type for argument: speed"),
    ("self.mask.open", {"speed": "50"}, -32602, "Invalid type for argument: speed"),
    ("self.mask.open", {"speed": None}, -32602, "Invalid type for argument: speed"),
    ("self.mask.open", {"speed": 5}, -32602, "Property 'speed': value 5 is below minimum 10"),
    ("self.mask.open", {"speed": 101}, -32602, "Property 'speed': value 101 exceeds maximum 100"),
    ("self.mask.open", {"SPEED": 5}, -32602, "Property 'speed': value 5 is below minimum 10"),
    ("self.mask.set_position", {"speed": 50}, -32602, "Missing required argument: percent"),
    ("self.sequence.run", {"steps": "x" * 4001}, -32602,
     "Property 'steps': string length 4001 exceeds maximum 4000"),
    ("self.eyes.effect", {"name": "x" * 17}, -32602,
     "Property 'name': string length 17 exceeds maximum 16"),
    ("self.display.show_info", {"title": "ệ" * 33, "text": "a"}, -32602,
     "Property 'title': string length 99 exceeds maximum 96"),
    ("self.display.show_info", {"title": "ệ" * 25, "text": "a"}, -32603,
     "title longer than 24 characters"),
    ("self.eyes.effect", {"name": "disco"}, -32603, "unknown effect: disco"),
    ("self.sequence.run_saved", {"name": "nope"}, -32603, "unknown sequence: nope"),
    ("self.sequence.run", {"steps": "not json"}, -32603, "steps is not valid JSON"),
    ("self.sequence.run", {"steps": '[{"delay":100},{"mask":{"action":"fly"}}]'}, -32603,
     "invalid step #2: action must be open or close"),
])
def test_tools_call_errors_match_firmware(name, args, code, message):
    sim, _ = new_sim()
    assert err(call(sim, name, args)) == (code, message)


def test_tools_call_envelope_errors():
    sim, _ = new_sim()
    assert err(rpc(sim, "tools/call")) == (-32602, "Missing params")
    assert err(rpc(sim, "tools/call", {"arguments": {}})) == (-32602, "Missing tool name")
    assert err(rpc(sim, "tools/call", {"name": "self.mask.open", "arguments": [1]})) == \
        (-32602, "Invalid arguments: expected object")


def test_tools_call_defaults_and_number_coercion():
    sim, box = new_sim()
    assert ok_text(call(sim, "self.mask.open", omit_args=True)) == "opening"
    assert ok_text(call(sim, "self.mask.open", {"speed": 70.9})) == "opening"  # valueint
    assert ok_text(call(sim, "self.display.show_info", {"title": "ệ" * 24, "text": "Xin chào"})) \
        == "ok"
    assert ok_text(call(sim, "self.eyes.set", {"on": True})) == "ok"
    assert box.helmet.eyes_brightness == 100  # default brightness
    assert ok_text(call(sim, "self.audio_speaker.set_volume", {"volume": 40})) == "true"
    status = json.loads(ok_text(call(sim, "self.get_device_status")))
    assert status["audio_speaker"]["volume"] == 40


def test_sequence_tools_and_status():
    sim, box = new_sim()
    steps = json.dumps(INTERFACES_SAMPLE, ensure_ascii=False)
    assert ok_text(call(sim, "self.sequence.run", {"steps": steps})) == "started"
    status = json.loads(ok_text(call(sim, "self.system.get_status")))
    assert list(status) == ["mask", "eyes", "output", "sequence", "vbus_mv", "rssi",
                            "uptime_s", "free_heap"]
    assert status["sequence"]["running"] is True and status["output"]["on"] is True
    assert ok_text(call(sim, "self.sequence.stop")) == "stopped"
    assert json.loads(ok_text(call(sim, "self.system.get_status")))["sequence"]["running"] \
        is False
    assert ok_text(call(sim, "self.sequence.run_saved", {"name": "open_mask"})) == "started"
    assert err(call(sim, "self.sequence.save", {"name": "Bad-Name", "steps": "[]"}))[0] == -32603
    assert ok_text(call(sim, "self.sequence.save", {"name": "party", "steps": steps})) == "saved"
    assert ok_text(call(sim, "self.sequence.run_saved", {"name": "party"})) == "started"
    state = json.loads(ok_text(call(sim, "self.mask.get_state")))
    assert set(state) == {"state", "position"} and state["state"] in ("open", "closed", "moving")


def test_user_only_tools_are_callable():
    sim, box = new_sim()
    assert ok_text(call(sim, "self.servo.calibrate", {"servo": 1, "angle": 95})) == "ok"
    assert ok_text(call(sim, "self.servo.save_calibration",
                        {"servo": 1, "closed_angle": 100, "open_angle": 80})) == "saved"
    assert box.helmet.calibration[1] == (100, 80)
    info = json.loads(ok_text(call(sim, "self.get_system_info")))
    assert info["board"]["type"] == "jarvis-helmet"


def test_unescaped_error_messages_are_reported():
    sim, _ = new_sim()
    reply = rpc(sim, "tools/call", {"name": 'self."quoted', "arguments": {}}, raw=True)
    with pytest.raises(ValueError):
        json.loads(reply)  # the firmware pastes the name into JSON unescaped
    assert sim.warnings and "invalid JSON" in sim.warnings[0]


def test_sanitize_message_matches_helmet_common():
    assert ds.sanitize_message('a"b\\c\nd\x7f') == "a'b/c d "


# --------------------------------------------------------------------------- sequence validator

@pytest.mark.parametrize("steps,ok,fragment", [
    (INTERFACES_SAMPLE, True, None),
    ([], True, None),                                       # contract §4.11: empty = valid
    ([{"delay": 300.0}], True, None),                       # cJSON cannot tell 300 from 300.0
    ({"mask": {"action": "open"}}, False, "JSON array"),
    ([{"blink": {}}], False, "invalid step #1: unknown step type"),
    ([{"delay": 1, "mask": {"action": "open"}}], False, "invalid step #1"),
    ([{"delay": 10}, {"mask": {"action": "open", "fast": 1}}], False,
     "invalid step #2: unknown field"),
    ([{"mask": {"action": "open", "position": 5}}], False, "exactly one of"),
    ([{"mask": {"position": 101}}], False, "position out of range"),
    ([{"mask": {"action": "open", "speed": 9}}], False, "speed out of range"),
    ([{"servo": {"id": 2, "angle": 10}}], False, "id out of range"),
    ([{"servo": {"id": 0, "angle": 10, "duration_ms": 5001}}], False, "duration_ms out of range"),
    ([{"eyes": {"on": "yes"}}], False, "on must be a boolean"),
    ([{"eyes": {"effect": "disco"}}], False, "unknown effect"),
    ([{"eyes": {"effect": "blink", "duration_ms": 99}}], False, "duration_ms out of range"),
    ([{"eyes": {"on": True, "effect": "blink"}}], False, "either on or effect"),
    ([{"output": {}}], False, "on must be a boolean"),
    ([{"delay": 10001}], False, "delay out of range"),
    ([{"delay": 12.5}], False, "integer"),
    ([{"delay": True}], False, "integer"),
    ([{"display": {"title": "Đ" * 25, "text": "x"}}], False, "title longer than 24"),
    ([{"display": {"title": "Đ" * 24, "text": "ệ" * 200, "seconds": 30}}], True, None),
    ([{"display": {"title": "a", "text": "b", "seconds": 0}}], False, "seconds out of range"),
    ([{"sound": {"name": "Power-Up"}}], False, "sound name"),
    ([{"delay": 0}] * 65, False, "too many steps"),
    ([{"delay": 10000}] * 6 + [{"delay": 1}], False, "sequence too long"),
])
def test_builtin_validator(steps, ok, fragment):
    valid, error, _ = ds.builtin_validate_steps(steps)
    assert valid is ok, error
    if fragment:
        assert fragment in error
        assert '"' not in error and "\\" not in error


def test_validator_estimates_and_d23_numbering():
    ok, _, est = ds.builtin_validate_steps(INTERFACES_SAMPLE)
    assert ok and est == 300 + 1000 + 300 + 500  # fade_out + mask@80 + delay + servo
    ok, error, _ = ds.builtin_validate_steps([{"delay": 1}, {"delay": 2}, {"bogus": 1}])
    assert error.startswith("invalid step #3:")


def _fixtures():
    return sorted(FIXTURES_DIR.glob("*.json")) if FIXTURES_DIR.is_dir() else []


def _check_fixture(fx, valid, error, est):
    """Harness rules of tools/tests/fixtures/sequences/README.md §2."""
    assert valid is fx["valid"], f"{fx.get('name')}: {error} ({fx.get('note')})"
    if fx["valid"]:
        if fx.get("estimated_ms") is not None:
            assert est == fx["estimated_ms"], fx["name"]
        return
    if fx.get("error_step") is not None:
        assert error.startswith(f"invalid step #{fx['error_step']}: "), error
    else:
        assert not error.startswith("invalid step #"), error
    if fx.get("error_contains") is not None:
        assert fx["error_contains"] in error, (fx["name"], error)


@pytest.mark.skipif(not _fixtures(), reason="shared fixtures (SV-D) not present yet")
@pytest.mark.parametrize("path", _fixtures(), ids=lambda p: p.stem)
def test_builtin_validator_agrees_with_shared_fixtures(path):
    fx = json.loads(path.read_text(encoding="utf-8"))
    _check_fixture(fx, *ds.builtin_validate_steps(fx["steps"]))


@pytest.mark.skipif(not _fixtures(), reason="shared fixtures (SV-D) not present yet")
@pytest.mark.parametrize("path", _fixtures(), ids=lambda p: p.stem)
def test_tool_call_validation_agrees_with_shared_fixtures(path):
    """The simulated self.sequence.run gives the fixture's verdict (with taskslib if present)."""
    fx = json.loads(path.read_text(encoding="utf-8"))
    box = ds.ToolBox(validator=ds.SequenceValidator("auto"))
    sim = ds.McpServerSim(box.build())
    steps = json.dumps(fx["steps"], ensure_ascii=False, separators=(",", ":"))
    reply = call(sim, "self.sequence.run", {"steps": steps})
    if fx["valid"]:
        assert ok_text(reply) == "started", fx["name"]
    else:
        code, message = err(reply)
        assert code == -32603
        _check_fixture(fx, False, message, None)


def test_taskslib_adapter_when_available():
    validator = ds.SequenceValidator("auto")
    if validator.name == "builtin":
        pytest.skip(f"taskslib not usable: {validator.load_error}")
    ok, error = validator.validate(json.dumps(OPEN_MASK))
    assert ok, error
    ok, error = validator.validate(json.dumps([{"delay": 1}, {"mask": {"action": "fly"}}]))
    assert not ok and error


@pytest.mark.parametrize("result,expected", [
    (None, (True, None)), ([], (True, None)), (True, (True, None)),
    ("invalid step #1: x", (False, "invalid step #1: x")),
    (["invalid step #2: y"], (False, "invalid step #2: y")),
    ((False, "bad"), (False, "bad")), ((True, None), (True, None)),
    ({"valid": False, "error": "e"}, (False, "e")), ({"ok": True}, (True, None)),
    ([{"delay": 1}, {"mask": {"action": "open"}}], (True, None)),   # normalized steps
    ([{"step": 3, "message": "invalid step #3: z"}], (False, "invalid step #3: z")),
])
def test_validator_result_conventions(result, expected):
    assert ds._interpret_validator_result(result) == expected


# --------------------------------------------------------------------------- OTA and WebSocket messages

def test_ota_headers_and_body():
    dev = ds.DeviceIdentity(mac="AA:BB:CC:DD:EE:FF")
    headers = dict(ds.build_ota_headers(dev, env={}))
    assert list(headers) == ["Activation-Version", "Device-Id", "Client-Id", "User-Agent",
                             "Accept-Language", "Content-Type"]
    assert headers["Device-Id"] == "aa:bb:cc:dd:ee:ff"
    assert headers["User-Agent"] == "jarvis-helmet/2.5.0"
    assert headers["Accept-Language"] == "vi-VN"
    assert re.fullmatch(r"[0-9a-f-]{36}", headers["Client-Id"])
    body = ds.build_system_info(dev)
    assert body["board"]["type"] == "jarvis-helmet" and body["board"]["name"] == "jarvis-helmet"
    assert body["application"]["version"] == "2.5.0" and body["application"]["name"] == "xiaozhi"
    assert body["mac_address"] == dev.mac and body["uuid"] == dev.uuid
    assert isinstance(body["minimum_free_heap_size"], str)
    labels = [p["label"] for p in body["partition_table"]]
    assert labels == ["nvs", "otadata", "phy_init", "helmet", "jsecrets", "ota_0", "ota_1",
                      "assets"]


def test_cf_access_headers_from_env():
    env = {"CF_ACCESS_CLIENT_ID": "id.access", "CF_ACCESS_CLIENT_SECRET": "cfast_x"}
    dev = ds.DeviceIdentity()
    assert ("CF-Access-Client-Id", "id.access") in ds.build_ota_headers(dev, env)
    assert ("CF-Access-Client-Secret", "cfast_x") in ds.build_ws_headers(dev, "t", env=env)
    assert not ds.cf_access_headers({"CF_ACCESS_CLIENT_ID": "only-id"})


def test_ws_headers_token_rules():
    dev = ds.DeviceIdentity()
    h = ds.build_ws_headers(dev, "abc.123", env={})
    assert h[0] == ("Authorization", "Bearer abc.123")
    assert [k for k, _ in h] == ["Authorization", "Protocol-Version", "Device-Id", "Client-Id"]
    assert dict(h)["Protocol-Version"] == "1"
    assert ds.build_ws_headers(dev, "Token x", env={})[0] == ("Authorization", "Token x")
    assert "Authorization" not in dict(ds.build_ws_headers(dev, "", env={}))


def test_hello_is_byte_exact():
    assert ds.build_hello() == (
        '{"type":"hello","version":1,"features":{"mcp":true,"glyph_push":true},'
        '"text_font":{"bundle":"noto-v1","charset":"basic","size":16,"bpp":4},'
        '"transport":"websocket","audio_params":{"format":"opus","sample_rate":16000,'
        '"channels":1,"frame_duration":60}}')
    assert ds.build_hello(aec=True, glyph_push=False) == (
        '{"type":"hello","version":1,"features":{"aec":true,"mcp":true,"glyph_push":false},'
        '"transport":"websocket","audio_params":{"format":"opus","sample_rate":16000,'
        '"channels":1,"frame_duration":60}}')


def test_listen_abort_mcp_messages_are_byte_exact():
    assert ds.msg_listen_start("s1", "auto") == \
        '{"session_id":"s1","type":"listen","state":"start","mode":"auto"}'
    assert ds.msg_listen_start("s1", "manual").endswith('"mode":"manual"}')
    assert ds.msg_listen_stop("s1") == '{"session_id":"s1","type":"listen","state":"stop"}'
    assert ds.msg_listen_detect("s1", "Quản gia") == \
        '{"session_id":"s1","type":"listen","state":"detect","text":"Quản gia"}'
    assert json.loads(ds.msg_listen_detect("", 'say "hi"\n'))["text"] == 'say "hi"\n'
    assert ds.msg_abort("s1") == '{"session_id":"s1","type":"abort"}'
    assert ds.msg_abort("s1", wake_word=True) == \
        '{"session_id":"s1","type":"abort","reason":"wake_word_detected"}'
    assert ds.msg_mcp("s1", '{"jsonrpc":"2.0","id":1,"result":{}}') == \
        '{"session_id":"s1","type":"mcp","payload":{"jsonrpc":"2.0","id":1,"result":{}}}'
    with pytest.raises(ValueError):
        ds.msg_listen_start("s1", "push")


def test_parse_ota_response():
    good = {"server_time": {"timestamp": 1, "timezone_offset": 420},
            "firmware": {"version": "2.5.0", "url": ""},
            "websocket": {"url": "ws://192.168.1.27:8100/xiaozhi/v1/", "token": "sig.1"}}
    res = ds.parse_ota_response(200, json.dumps(good))
    assert (res.ws_url, res.token, res.warnings) == ("ws://192.168.1.27:8100/xiaozhi/v1/",
                                                     "sig.1", [])
    for status, body, fragment in [
        (302, "", "OTA HTTP 302"), (401, "", "OTA HTTP 401"),
        (200, "<html>", "not JSON"),
        (200, '{"success":false,"message":"request error."}', "rejected"),
        (200, json.dumps({**good, "mqtt": {"endpoint": "x"}}), "MQTT"),
        (200, json.dumps({"server_time": {}}), "fall back to MQTT"),
        (200, json.dumps({**good, "websocket": {"url": "http://x/", "token": ""}}), "ws://"),
    ]:
        with pytest.raises(ds.SimError, match=fragment):
            ds.parse_ota_response(status, body)
    warn = ds.parse_ota_response(200, json.dumps({
        **good, "websocket": {**good["websocket"], "version": 2},
        "firmware": {"version": "2.6.0", "url": "http://x/fw.bin"},
        "server_time": {"timestamp": 1, "timezone_offset": "+7"}})).warnings
    assert len(warn) == 3


def test_token_matches_upstream_verify():
    # core/auth.py AuthManager.verify_token, re-implemented independently
    def verify(key, token, client_id, device_id):
        sig, ts = token.split(".")
        expected = base64.urlsafe_b64encode(hmac.new(
            key.encode(), f"{client_id}|{device_id}|{int(ts)}".encode(),
            hashlib.sha256).digest()).decode().rstrip("=")
        return hmac.compare_digest(sig, expected)
    token = ds.xz_token("secret", "uuid-1", "aa:bb:cc:dd:ee:ff", ts=1791000000)
    assert token.endswith(".1791000000") and "=" not in token
    assert verify("secret", token, "uuid-1", "aa:bb:cc:dd:ee:ff")
    assert not verify("other", token, "uuid-1", "aa:bb:cc:dd:ee:ff")


def test_device_identity_validation():
    with pytest.raises(ValueError):
        ds.DeviceIdentity(mac="not-a-mac")
    a, b = ds.DeviceIdentity(), ds.DeviceIdentity()
    assert a.uuid == b.uuid  # stable Client-Id for a MAC (like NVS board/uuid)


# --------------------------------------------------------------------------- audio

@needs_audio
def test_opus_encoder_produces_60ms_packets_that_decode_back():
    rate = ds.UPLINK_RATE
    t = np.arange(rate) / rate
    pcm = (0.3 * 32767 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    enc = ds.OpusEncoder()
    assert enc.frame_samples == 960
    packets = []
    for i in range(0, len(pcm) - 959, 960):
        packets += enc.encode(pcm[i:i + 960])
    assert len(packets) >= 15
    assert all(ds.opus_packet_duration_ms(p) == 60.0 for p in packets)
    dec = ds.OpusDecoder(rate)
    out = np.concatenate([dec.decode(p) for p in packets])
    assert abs(len(out) - 960 * len(packets)) <= 960
    spec = np.abs(np.fft.rfft(out.astype(float)))
    peak = np.fft.rfftfreq(len(out), 1 / rate)[np.argmax(spec)]
    assert abs(peak - 440) < 10
    assert dec.errors == 0


@needs_audio
def test_opus_decoder_resamples_24k_downlink():
    t = np.arange(24000) / 24000
    pcm = (0.25 * 32767 * np.sin(2 * np.pi * 220 * t)).astype(np.int16)
    enc = ds.OpusEncoder(rate=24000)
    packets = [p for i in range(0, 24000 - 1439, 1440) for p in enc.encode(pcm[i:i + 1440])]
    for out_rate in (24000, 16000):
        dec = ds.OpusDecoder(out_rate)
        out = np.concatenate([dec.decode(p) for p in packets])
        assert abs(len(out) / out_rate - len(packets) * 0.06) < 0.07


@pytest.mark.parametrize("packet,ms", [
    (bytes([0xBB, 0x03]), 60.0),   # CELT WB 20 ms, code 3, 3 frames
    (bytes([0x58]), 60.0),         # SILK WB 60 ms, 1 frame
    (bytes([0x59]), 120.0),        # SILK WB 60 ms, 2 frames
    (bytes([0xE0]), 2.5),          # CELT FB 2.5 ms
    (bytes([0x60]), 10.0),         # hybrid SWB 10 ms
    (bytes([0x68]), 20.0),         # hybrid SWB 20 ms
    (b"", None),
])
def test_opus_toc_parser(packet, ms):
    assert ds.opus_packet_duration_ms(packet) == ms


@needs_audio
def test_speech_bounds_and_audio_loading(tmp_path):
    rate = 16000
    tone = (0.3 * 32767 * np.sin(2 * np.pi * 300 * np.arange(rate) / rate)).astype(np.int16)
    pcm = np.concatenate([np.zeros(int(0.3 * rate), np.int16), tone,
                          np.zeros(int(0.5 * rate), np.int16)])
    start, end = ds.speech_bounds_ms(pcm)
    assert abs(start - 300) <= 20 and abs(end - 1300) <= 20
    assert ds.speech_bounds_ms(np.zeros(rate, np.int16)) == (0, 0)
    # 44.1 kHz stereo input is downmixed and resampled to 16 kHz mono
    path = tmp_path / "in.wav"
    stereo = np.repeat(tone[:, None], 2, axis=1)
    hi = ds.resample_int16(stereo[:, 0], rate, 44100)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(np.repeat(hi[:, None], 2, axis=1).astype("<i2").tobytes())
    loaded = ds.load_audio_16k(path)
    assert abs(len(loaded) - rate) < 200
    out = tmp_path / "out.wav"
    ds.write_wav(out, loaded, rate)
    with wave.open(str(out)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, rate)


# --------------------------------------------------------------------------- actions, glyphs, checks

def test_actions_from_steps_and_calls():
    assert ds.steps_to_actions(OPEN_MASK + EYES_ON) == ["eyes_off", "mask_open", "eyes_on"]
    assert ds.steps_to_actions([{"mask": {"position": 0}}, {"mask": {"position": 40}},
                                {"output": {"on": False}}, {"display": {}}]) == \
        ["mask_close", "mask_pos_40", "output_off", "display"]
    assert ds.call_to_actions("self.eyes.effect", {"name": "blink"}) == ["eyes_blink"]
    assert ds.call_to_actions("self.sequence.run_saved", {"name": "close_mask"}) == \
        ["mask_close", "eyes_on"]
    assert ds.call_to_actions("self.sequence.save", {"steps": json.dumps(OPEN_MASK)}) == []
    assert ds.is_subsequence(["mask_open", "eyes_on"], ["eyes_off", "mask_open", "x", "eyes_on"])
    assert not ds.is_subsequence(["eyes_on", "mask_open"], ["mask_open", "eyes_on"])


def test_hud_glyph_coverage():
    assert ds.missing_glyphs("Ừ, đã bật chế độ chiến đấu – tôi sẵn sàng, thưa ngài! 25.000₫") \
        == []
    assert ds.missing_glyphs("你好 🙂") == ["你", "好", "🙂"]


def _turn(calls=(), frames=0, sentences=(), eos=100.0):
    turn = ds.Turn(1, ds.TurnInput(text="x"))
    turn.eos = eos
    for offset, name, args in calls:
        turn.mcp_calls.append({"_t": eos + offset, "name": name, "arguments": args,
                               "error": None})
    turn.frames = [(eos + 0.4 + i * 0.06, 100, True) for i in range(frames)]
    turn.sentences = [(eos + 0.3, s) for s in sentences]
    return turn


def test_checks_on_recorded_turns():
    merged = json.dumps(OPEN_MASK + EYES_ON)
    t = _turn([(0.5, "self.sequence.run", {"steps": merged})], frames=5,
              sentences=["Đã mở mặt nạ và bật đèn mắt, thưa ngài."])
    assert t.metrics()["eos_to_first_mcp_ms"] == 500.0
    assert t.metrics()["eos_to_first_audio_ms"] == 400.0
    ctx = {"expected_turns": 1}
    assert ds.run_check("actions:mask_open,eyes_on", [t], ctx).ok
    assert not ds.run_check("actions:eyes_on,mask_open", [t], ctx).ok
    assert ds.run_check("max:eos_to_first_mcp_ms:800", [t], ctx).ok
    assert not ds.run_check("max:eos_to_first_mcp_ms:400", [t], ctx).ok
    assert ds.run_check("reply", [t], ctx).ok
    assert ds.run_check("tool:self.sequence.run", [t], ctx).ok
    assert ds.run_check("vi_diacritics", [t], ctx).ok
    assert not ds.run_check("no_device_action", [t], ctx).ok
    silent = _turn()
    assert not ds.run_check("reply", [silent], ctx).ok
    assert ds.run_check("no_device_action", [silent], ctx).ok
    assert not ds.run_check("max:eos_to_first_mcp_ms:800", [silent], ctx).ok  # missing value
    silent.warnings.append("stt text has characters the HUD font lacks: '你'")
    assert not ds.run_check("glyphs", [silent], ctx).ok
    english = _turn(sentences=["I am afraid I have no weather data, Sir."])
    assert ds.run_check("reply_english", [english], ctx).ok
    assert not ds.run_check("reply_english", [t], ctx).ok
    assert not ds.run_check("all_completed", [t], ctx).ok  # completed flag not set
    t.completed = True
    assert ds.run_check("all_completed", [t], ctx).ok


def test_task_steps_check_accepts_defaults_and_merges():
    ctx = {"expected_steps": [{"eyes": {"effect": "breathe"}},
                              {"display": {"title": "PARTY", "text": "x"}}]}
    exact = _turn([(0.2, "self.sequence.run", {"steps": json.dumps(
        [{"eyes": {"effect": "breathe", "duration_ms": 1000}},
         {"display": {"title": "PARTY", "text": "x", "seconds": 5}}])})])
    exact.mcp_calls[0]["steps"] = json.loads(exact.mcp_calls[0]["arguments"]["steps"])
    assert ds.run_check("task_steps", [exact], ctx).ok
    other = _turn([(0.2, "self.sequence.run", {"steps": json.dumps(OPEN_MASK)})])
    other.mcp_calls[0]["steps"] = OPEN_MASK
    assert not ds.run_check("task_steps", [other], ctx).ok
    assert ds.run_check("task_steps", [other], {}).ok  # skipped without expectation


# --------------------------------------------------------------------------- CLI end to end (scripted server)

class ScriptedServer:
    """Minimal xiaozhi stand-in on 127.0.0.1 (own thread + event loop): OTA + WebSocket."""

    def __init__(self, auth_key="unit-key"):
        self.auth_key = auth_key
        self.texts, self.audio, self.ota_requests, self.tool_pages = [], [], [], []
        self.frames = []
        if HAVE_AUDIO:
            t = np.arange(24000) / 24000
            pcm = (0.2 * 32767 * np.sin(2 * np.pi * 220 * t)).astype(np.int16)
            enc = ds.OpusEncoder(rate=24000)
            self.frames = [p for i in range(0, 24000 - 1439, 1440)
                           for p in enc.encode(pcm[i:i + 1440])]
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    # --- lifecycle
    def __enter__(self):
        self.thread.start()
        assert self.ready.wait(10)
        return self

    def __exit__(self, *exc):
        self.loop.call_soon_threadsafe(self.stop_event.set)
        self.thread.join(5)
        self.httpd.shutdown()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    async def _main(self):
        import websockets
        self.stop_event = asyncio.Event()
        server = await websockets.serve(self._handler, "127.0.0.1", 0, max_size=None)
        self.ws_port = server.sockets[0].getsockname()[1]
        outer = self

        class Ota(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.ota_requests.append((dict(self.headers.items()), json.loads(body)))
                token = ds.xz_token(outer.auth_key, self.headers["Client-Id"],
                                    self.headers["Device-Id"])
                out = json.dumps({"server_time": {"timestamp": 1, "timezone_offset": 420},
                                  "firmware": {"version": "2.5.0", "url": ""},
                                  "websocket": {"url": outer.ws_url, "token": token}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Ota)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.ready.set()
        await self.stop_event.wait()
        server.close()
        await server.wait_closed()

    @property
    def ws_url(self):
        return f"ws://127.0.0.1:{self.ws_port}/xiaozhi/v1/"

    @property
    def ota_url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/xiaozhi/ota/"

    # --- protocol
    async def _handler(self, ws):
        headers = {k.lower(): v for k, v in ws.request.headers.items()}
        token = headers.get("authorization", "")
        good = ds.xz_token(self.auth_key, headers.get("client-id", ""),
                           headers.get("device-id", ""), ts=int(token.rsplit(".", 1)[-1])
                           if token.rsplit(".", 1)[-1].isdigit() else 0)
        if token != "Bearer " + good:
            await ws.send("认证失败")
            await ws.close()
            return
        self.speak_task = None
        sid = "unit-session"
        async for msg in ws:
            if isinstance(msg, bytes):
                self.audio.append(msg)
                continue
            self.texts.append(msg)
            m = json.loads(msg)
            kind = m.get("type")
            if kind == "hello":
                await ws.send(json.dumps({"type": "hello", "version": 1, "transport": "websocket",
                                          "session_id": sid, "audio_params": {
                                              "format": "opus", "sample_rate": 24000,
                                              "channels": 1, "frame_duration": 60}}))
                await ws.send(json.dumps({"type": "mcp", "payload": {
                    "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                        "protocolVersion": "2024-11-05", "capabilities": {}}}}))
            elif kind == "mcp":
                p = m["payload"]
                if p.get("id") == 1:
                    await ws.send(json.dumps({"type": "mcp", "payload": {
                        "jsonrpc": "2.0", "id": 2, "method": "tools/list"}}))
                elif p.get("id") == 2:
                    self.tool_pages.append([t["name"] for t in p["result"]["tools"]])
                    if p["result"].get("nextCursor"):
                        await ws.send(json.dumps({"type": "mcp", "payload": {
                            "jsonrpc": "2.0", "id": 2, "method": "tools/list",
                            "params": {"cursor": p["result"]["nextCursor"]}}}))
            elif kind == "listen" and m["state"] == "detect" and m["text"] == "Jarvis":
                for state in ("start", "stop"):
                    if state == "start":
                        await ws.send(json.dumps({"type": "stt", "text": "Jarvis",
                                                  "session_id": sid}))
                    await ws.send(json.dumps({"type": "tts", "state": state, "session_id": sid}))
            elif kind == "listen" and m["state"] in ("detect", "stop"):
                text = m.get("text", "mở mặt nạ")
                self.speak_task = asyncio.ensure_future(self._reply(ws, sid, text))
            elif kind == "abort":
                if self.speak_task:
                    self.speak_task.cancel()
                await ws.send(json.dumps({"type": "tts", "state": "stop", "session_id": sid}))

    async def _reply(self, ws, sid, text):
        await ws.send(json.dumps({"type": "stt", "text": text, "session_id": sid}))
        await ws.send(json.dumps({"type": "tts", "state": "start", "session_id": sid}))
        if "mặt nạ" in text:
            await ws.send(json.dumps({"type": "mcp", "payload": {
                "jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {
                    "name": "self.sequence.run",
                    "arguments": {"steps": json.dumps(OPEN_MASK)}}}}))
        long_story = "câu chuyện" in text
        await ws.send(json.dumps({"type": "tts", "state": "sentence_start", "session_id": sid,
                                  "text": "Đã mở mặt nạ, thưa ngài."}))
        frames = self.frames * (6 if long_story else 1)
        for i, pkt in enumerate(frames):
            await ws.send(pkt)
            await asyncio.sleep(0.06 if long_story else 0.005)
        await asyncio.sleep(0.05)
        await ws.send(json.dumps({"type": "tts", "state": "stop", "session_id": sid}))


def run_cli(capsys, argv):
    code = ds.main(argv + ["--no-save", "--quiet", "--settle-ms", "200", "--mcp-wait", "5"])
    out = capsys.readouterr().out
    return code, json.loads(out)


def _listen_trace(texts):
    trace = []
    for raw in texts:
        m = json.loads(raw)
        if m["type"] == "listen":
            trace.append((m["state"], m.get("mode") or m.get("text")))
        elif m["type"] in ("abort", "hello"):
            trace.append((m["type"], None))
    return trace


def test_cli_e4_text_scenario_end_to_end(capsys):
    with ScriptedServer() as srv:
        code, summary = run_cli(capsys, ["--ota-url", srv.ota_url, "--scenario", "E4",
                                         "--input", "text"])
    assert code == 0, summary["assertions"]
    assert summary["pass"] is True
    headers, body = srv.ota_requests[0]
    assert headers["User-Agent"] == "jarvis-helmet/2.5.0" and body["board"]["type"] == \
        "jarvis-helmet"
    # firmware sequence: detect(wake) + listen start, restart after the wake echo flip,
    # then the injected text (the restart after the reply waits for playback to drain)
    assert _listen_trace(srv.texts)[:5] == [("hello", None), ("detect", "Jarvis"),
                                            ("start", "auto"), ("start", "auto"),
                                            ("detect", "mở mặt nạ")]
    assert json.loads(srv.texts[0]) == json.loads(ds.build_hello())
    turn = summary["turns"][0]
    assert turn["completed"] and turn["stt"] == "mở mặt nạ"
    assert turn["actions"] == ["eyes_off", "mask_open"]
    assert turn["mcp_calls"][0]["result"] == "started"
    assert summary["connections"][0]["timeline"]["mcp_tools_list_pages"] == 1
    replies = [json.loads(t) for t in srv.texts if '"type":"mcp"' in t]
    call_reply = next(r for r in replies if r["payload"].get("id") == 10)
    assert call_reply["session_id"] == "unit-session"
    assert call_reply["payload"]["result"]["content"][0]["text"] == "started"
    if HAVE_AUDIO:
        assert turn["audio_frames"] == len(srv.frames) and turn["reply_audio_ms"] > 0


def test_cli_paginated_tools_list_and_expectation_failure(capsys):
    with ScriptedServer() as srv:
        code, summary = run_cli(capsys, ["--ws-url", srv.ws_url, "--auth-key", srv.auth_key,
                                         "--text", "mở mặt nạ", "--no-wake",
                                         "--mcp-page-size", "1500",
                                         "--expect-actions", "eyes_on"])
    assert code == 1 and summary["pass"] is False
    names = [n for page in srv.tool_pages for n in page]
    assert len(srv.tool_pages) > 2
    assert names == [t.name for t in ds.build_helmet_tool_list() if not t.user_only]


def test_cli_wrong_token_is_a_connection_error(capsys):
    with ScriptedServer() as srv:
        code = ds.main(["--ws-url", srv.ws_url, "--token", "bad.123", "--text", "x",
                        "--no-save", "--quiet"])
    out = json.loads(capsys.readouterr().out)
    assert code == 3 and "认证失败" in out["error"]


def test_cli_abort_flow_e7(capsys):
    with ScriptedServer() as srv:
        code, summary = run_cli(capsys, ["--ws-url", srv.ws_url, "--auth-key", srv.auth_key,
                                         "--scenario", "E7", "--input", "text",
                                         "--abort-after-ms", "300"])
    if not HAVE_AUDIO:
        pytest.skip("needs downlink audio frames")
    assert code == 0, summary["assertions"]
    metrics = summary["turns"][0]["metrics"]
    assert metrics["abort_to_tts_stop_ms"] is not None and metrics["abort_to_tts_stop_ms"] < 300
    assert ("abort", None) in _listen_trace(srv.texts)


@needs_audio
def test_cli_manual_mode_streams_realtime_opus(capsys, tmp_path):
    rate = 16000
    tone = (0.3 * 32767 * np.sin(2 * np.pi * 300 * np.arange(int(0.6 * rate)) / rate))
    pcm = np.concatenate([np.zeros(int(0.2 * rate)), tone, np.zeros(int(0.2 * rate))])
    wav = tmp_path / "cmd.wav"
    ds.write_wav(wav, pcm.astype(np.int16), rate)
    with ScriptedServer() as srv:
        code, summary = run_cli(capsys, ["--ws-url", srv.ws_url, "--auth-key", srv.auth_key,
                                         "--wav", str(wav), "--mode", "manual", "--no-wake",
                                         "--expect-actions", "mask_open"])
    assert code == 0, summary["assertions"]
    assert len(srv.audio) == math.ceil(len(pcm) / 960)
    assert all(ds.opus_packet_duration_ms(p) == 60.0 for p in srv.audio)
    trace = _listen_trace(srv.texts)
    assert trace[:3] == [("hello", None), ("start", "manual"), ("stop", None)]
    turn = summary["turns"][0]
    assert turn["speech_end_in_wav_ms"] == 800 and turn["uplink_frames"] == len(srv.audio)
    assert turn["metrics"]["eos_to_first_mcp_ms"] >= 200  # includes the trailing 200 ms


def test_cli_offline_helpers(capsys):
    assert ds.main(["--list-scenarios"]) == 0
    assert "E13" in capsys.readouterr().out
    assert ds.main(["--dump-tools", "--with-user-tools"]) == 0
    out = capsys.readouterr().out
    assert "# page 1" in out and "self.servo.calibrate" in out
    with pytest.raises(SystemExit) as exc:
        ds.main(["--text", "x"])
    assert exc.value.code == 2
