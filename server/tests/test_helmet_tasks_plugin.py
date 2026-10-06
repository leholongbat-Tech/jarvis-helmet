"""Tests for server/xiaozhi/plugins/helmet_tasks.py without the xiaozhi runtime (SV-D).

Minimal fakes of the upstream modules the plugin touches (plugins_func.register, config.logger,
device_mcp.call_mcp_tool, speak_txt, send_stt_message, Message, ContentType) are injected into
sys.modules with monkeypatch, so they are removed again after every test. The plugin is loaded
fresh for each test under its real module name ``plugins_func.functions.helmet_tasks``.
"""
import asyncio
import importlib.util
import json
import os
import re
import shutil
import sys
import time
import types
from enum import Enum
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TASKS_DIR = REPO / "server" / "tasks"
PLUGIN_FILE = REPO / "server" / "xiaozhi" / "plugins" / "helmet_tasks.py"
CJK = re.compile(r"[㐀-鿿＀-￯]")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeLogger:
    def __init__(self):
        self.records = []

    def bind(self, **_):
        return self

    def _add(self, level, msg):
        self.records.append((level, str(msg)))

    def debug(self, msg, *a, **k):
        self._add("DEBUG", msg)

    def info(self, msg, *a, **k):
        self._add("INFO", msg)

    def warning(self, msg, *a, **k):
        self._add("WARNING", msg)

    def error(self, msg, *a, **k):
        self._add("ERROR", msg)

    def text(self, level=None):
        return "\n".join(m for lv, m in self.records if level is None or lv == level)


class _CodeEnum(Enum):
    def __init__(self, code, message):
        self.code = code
        self.message = message


class ToolType(_CodeEnum):
    NONE = (1, "none")
    WAIT = (2, "wait")
    CHANGE_SYS_PROMPT = (3, "prompt")
    SYSTEM_CTL = (4, "system control, receives conn")
    IOT_CTL = (5, "iot")
    MCP_CLIENT = (6, "mcp")


class Action(_CodeEnum):
    ERROR = (-1, "error")
    NOTFOUND = (0, "not found")
    NONE = (1, "none")
    RESPONSE = (2, "response")
    REQLLM = (3, "request llm")
    RECORD = (4, "record")


class ActionResponse:
    def __init__(self, action, result=None, response=None):
        self.action = action
        self.result = result
        self.response = response


class FunctionItem:
    def __init__(self, name, description, func, type):
        self.name = name
        self.description = description
        self.func = func
        self.type = type


class ContentType(Enum):
    TEXT = "TEXT"
    FILE = "FILE"
    ACTION = "ACTION"


class Message:
    def __init__(self, role, content=None, uniq_id=None, tool_calls=None, tool_call_id=None,
                 is_temporary=False):
        self.role = role
        self.content = content
        self.tool_calls = tool_calls
        self.tool_call_id = tool_call_id


class FakeDialogue:
    def __init__(self):
        self.dialogue = []

    def put(self, message):
        self.dialogue.append(message)


class FakeTTS:
    def __init__(self):
        self.queued = []      # (sentence_id, text) of MIDDLE items queued by the plugin
        self.stored = []

    def tts_one_sentence(self, conn, content_type, content_detail=None, content_file=None,
                         sentence_id=None):
        self.queued.append((sentence_id or conn.sentence_id, content_detail))

    def store_tts_text(self, sentence_id, text):
        self.stored.append((sentence_id, text))


class FakeToolManager:
    def __init__(self):
        self.refreshes = 0

    def refresh_tools(self):
        self.refreshes += 1


class FakeFuncHandler:
    def __init__(self):
        self.tool_manager = FakeToolManager()


class FakeMCPClient:
    """Device side: records tools/call requests and answers like firmware v2.5.0 would."""

    def __init__(self, tools=("self_sequence_run", "self_sequence_save"), ready=True,
                 delay=0.0, error=None):
        self.tools = set(tools)
        self.ready = ready
        self.delay = delay
        self.error = error          # exception instance to raise, or callable(tool, args)
        self.calls = []             # (tool, args dict, timeout, monotonic time)

    def has_tool(self, name):
        return name in self.tools

    async def handle(self, tool, args, timeout):
        self.calls.append((tool, json.loads(args) if isinstance(args, str) else args, timeout,
                           time.monotonic()))
        if self.delay:
            await asyncio.sleep(self.delay)
        error = self.error(tool, args) if callable(self.error) else self.error
        if error is not None:
            raise error
        return "saved" if tool == "self_sequence_save" else "started"


class FakeConn:
    def __init__(self, client=None, plugin_cfg=None, sentence_id="turn-1"):
        self.session_id = "sess-123"
        self.device_id = "aa:bb:cc:dd:ee:ff"
        self.headers = {"device-id": self.device_id}
        cfg = {"merge_window_ms": 30, "mcp_timeout_s": 1, "mcp_ready_wait_s": 0.2,
               "sync_delay_s": 0}
        cfg.update(plugin_cfg or {})
        self.config = {"plugins": {"helmet_tasks": cfg}}
        self.logger = FakeLogger()
        if client is not None:
            self.mcp_client = client
        self.sentence_id = sentence_id
        self.client_abort = True
        self.dialogue = FakeDialogue()
        self.tts = FakeTTS()
        self.func_handler = FakeFuncHandler()
        self.stt = []
        self.spoken = []
        self.loop = None


def install_fakes(monkeypatch, logger):
    def module(name, **attrs):
        mod = types.ModuleType(name)
        if "." not in name or name.count(".") < 3:
            mod.__path__ = []           # make it a package so sub-imports resolve
        for key, value in attrs.items():
            setattr(mod, key, value)
        monkeypatch.setitem(sys.modules, name, mod)
        return mod

    registry = {}
    func_map = {}
    module("config")
    module("config.logger", setup_logging=lambda config=None: logger)
    module("plugins_func")
    module("plugins_func.functions")
    module("plugins_func.register", ToolType=ToolType, Action=Action, ActionResponse=ActionResponse,
           FunctionItem=FunctionItem, all_function_registry=registry, module_func_map=func_map)

    async def call_mcp_tool(conn, mcp_client, tool_name, args="{}", timeout=30):
        return await mcp_client.handle(tool_name, args, timeout)

    async def send_stt_message(conn, text):
        conn.stt.append(text)

    def speak_txt(conn, text):
        conn.spoken.append((conn.sentence_id, text))
        conn.dialogue.put(Message(role="assistant", content=text))

    for name in ("core", "core.providers", "core.providers.tools", "core.handle", "core.utils",
                 "core.providers.tts", "core.providers.tts.dto"):
        module(name)
    module("core.providers.tools.device_mcp", call_mcp_tool=call_mcp_tool)
    module("core.handle.intentHandler", speak_txt=speak_txt)
    module("core.handle.sendAudioHandle", send_stt_message=send_stt_message)
    module("core.utils.dialogue", Message=Message)
    module("core.providers.tts.dto.dto", ContentType=ContentType)
    return types.SimpleNamespace(registry=registry, func_map=func_map)


class Env(types.SimpleNamespace):
    def write(self, text):
        """Rewrite tasks.yaml and force a distinct mtime."""
        self.tasks_file.write_text(text, encoding="utf-8")
        self.bump += 1
        stamp = time.time_ns() + self.bump * 10_000_000_000
        os.utime(self.tasks_file, ns=(stamp, stamp))

    def load(self):
        spec = importlib.util.spec_from_file_location("plugins_func.functions.helmet_tasks", PLUGIN_FILE)
        module = importlib.util.module_from_spec(spec)
        self.monkeypatch.setitem(sys.modules, "plugins_func.functions.helmet_tasks", module)
        spec.loader.exec_module(module)
        self.plugin = module
        return module


@pytest.fixture
def env(tmp_path, monkeypatch):
    tasks_file = tmp_path / "tasks.yaml"
    shutil.copy(TASKS_DIR / "tasks.yaml", tasks_file)
    shutil.copy(TASKS_DIR / "schema.json", tmp_path / "schema.json")
    monkeypatch.setenv("HELMET_TASKS_FILE", str(tasks_file))
    monkeypatch.delenv("HELMET_TASKS_SCHEMA", raising=False)
    monkeypatch.chdir(tmp_path)              # no data/.config.yaml here
    monkeypatch.syspath_prepend(str(TASKS_DIR))
    logger = FakeLogger()
    fakes = install_fakes(monkeypatch, logger)
    e = Env(tmp_path=tmp_path, tasks_file=tasks_file, monkeypatch=monkeypatch, logger=logger,
            fakes=fakes, bump=0, plugin=None)
    e.load()
    return e


def run(coro):
    return asyncio.run(coro)


async def drain(plugin):
    """Wait for every background task the plugin spawned."""
    for _ in range(50):
        pending = [t for t in list(plugin._STATE.bg_tasks) if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def assert_valid_response(resp, Action=Action):
    """Upstream crashes on None text: check the contract of every ActionResponse."""
    assert isinstance(resp, ActionResponse)
    if resp.action in (Action.RESPONSE, Action.ERROR, Action.NOTFOUND):
        assert (resp.response or resp.result), "RESPONSE needs text"
        assert not CJK.search(resp.response or "")
    if resp.action in (Action.REQLLM, Action.RECORD):
        assert isinstance(resp.result, str) and resp.result
    if resp.action == Action.RECORD:
        assert resp.response


TASKS_WITH_PARTY = (TASKS_DIR / "tasks.yaml").read_text(encoding="utf-8") + """
  party_mode:
    description: "Chế độ tiệc tùng / Party mode"
    quick_phrases: ["bật party mode", "party mode on"]
    steps:
      - eyes: {effect: breathe, duration_ms: 1500}
"""


def call_task(env, conn, name, **kwargs):
    item = env.fakes.registry[name]
    assert item.type.code == 4                     # SYSTEM_CTL: receives conn
    return item.func(conn, **kwargs)


# ---------------------------------------------------------------------------
# Registration and reload
# ---------------------------------------------------------------------------
IDS = ["open_mask", "close_mask", "eyes_on", "eyes_off", "combat_mode", "stealth_mode", "salute"]


def test_import_registers_every_task(env):
    names = [f"task_{i}" for i in IDS]
    assert env.fakes.func_map["helmet_tasks"] == names
    for name in names:
        item = env.fakes.registry[name]
        assert item.type is ToolType.SYSTEM_CTL
        assert item.description["type"] == "function"
        fn = item.description["function"]
        assert fn["name"] == name and " / " in fn["description"]
        assert fn["parameters"] == {"type": "object", "properties": {}, "required": []}
        assert asyncio.iscoroutinefunction(item.func)
    assert env.plugin._STATE.mode == "functions"
    assert "loaded 7 task(s)" in env.logger.text("INFO")


def test_import_with_missing_file_does_not_raise(env, monkeypatch):
    monkeypatch.setenv("HELMET_TASKS_FILE", str(env.tmp_path / "missing.yaml"))
    plugin = env.load()
    assert plugin.get_table() is None
    assert "tasks file not found" in env.logger.text("ERROR")
    conn = FakeConn(FakeMCPClient())
    assert run(plugin.try_quick_phrase(conn, "mở mặt nạ")) is False


def test_config_file_fallback(env, monkeypatch):
    monkeypatch.delenv("HELMET_TASKS_FILE")
    other = env.tmp_path / "other"
    other.mkdir()
    shutil.copy(env.tasks_file, other / "tasks.yaml")
    (env.tmp_path / "data").mkdir()
    (env.tmp_path / "data" / ".config.yaml").write_text(
        f"plugins:\n  helmet_tasks:\n    tasks_file: {json.dumps(str(other / 'tasks.yaml'))}\n"
        "    description: oops\n", encoding="utf-8")
    plugin = env.load()
    assert plugin._STATE.tasks_path == str(other / "tasks.yaml")
    assert len(plugin.get_table().tasks) == 7
    assert "plugins.helmet_tasks.description is set" in env.logger.text("WARNING")


def test_reload_on_mtime_change_refreshes_connection(env):
    conn = FakeConn(FakeMCPClient())
    assert run(env.plugin.try_quick_phrase(conn, "xin chào")) is False
    assert conn.func_handler.tool_manager.refreshes == 1          # first sight of this conn
    assert run(env.plugin.try_quick_phrase(conn, "xin chào")) is False
    assert conn.func_handler.tool_manager.refreshes == 1          # unchanged file: no refresh
    env.write(TASKS_WITH_PARTY)
    assert run(env.plugin.try_quick_phrase(conn, "kể chuyện đi")) is False
    assert "task_party_mode" in env.fakes.registry
    assert env.fakes.func_map["helmet_tasks"][-1] == "task_party_mode"
    assert conn.func_handler.tool_manager.refreshes == 2
    assert conn._helmet_tasks_version == env.plugin._STATE.version
    # The new quick phrase works on the very next utterance of the same connection.
    assert run(env.plugin.try_quick_phrase(conn, "Jarvis, bật party mode")) is True


def test_bad_yaml_keeps_last_good_table_and_logs_line(env):
    conn = FakeConn(FakeMCPClient())
    good_version = env.plugin._STATE.version
    broken = TASKS_WITH_PARTY.replace("      - eyes: {effect: breathe, duration_ms: 1500}",
                                      "      - eyes: {effect: breathe, duration_ms: 1500")
    env.write(broken)
    assert run(env.plugin.try_quick_phrase(conn, "xin chào")) is False
    assert env.plugin._STATE.version == good_version
    assert "task_party_mode" not in env.fakes.registry
    assert env.fakes.func_map["helmet_tasks"] == [f"task_{i}" for i in IDS]
    errors = env.logger.text("ERROR")
    assert "keeping the previous table (7 tasks)" in errors
    assert re.search(r"tasks\.yaml:\d+:\d+: YAML syntax error", errors)
    # Quick phrases of the old table still work.
    assert run(env.plugin.try_quick_phrase(conn, "mở mặt nạ")) is True
    # The broken file is reported once, not on every utterance.
    count = errors.count("keeping the previous table")
    run(env.plugin.try_quick_phrase(conn, "xin chào"))
    assert env.logger.text("ERROR").count("keeping the previous table") == count
    # Fixing the file loads it.
    env.write(TASKS_WITH_PARTY)
    run(env.plugin.try_quick_phrase(conn, "xin chào"))
    assert "task_party_mode" in env.fakes.registry


def test_invalid_step_keeps_table_and_reports_line(env):
    text = (TASKS_DIR / "tasks.yaml").read_text(encoding="utf-8")
    line = next(i for i, l in enumerate(text.splitlines(), 1) if "mask: {action: open, speed: 80}" in l)
    env.write(text.replace("mask: {action: open, speed: 80}", "mask: {action: open, speed: 5}"))
    run(env.plugin.try_quick_phrase(FakeConn(), "xin chào"))
    errors = env.logger.text("ERROR")
    assert f"tasks.yaml:{line}:" in errors
    assert "task 'open_mask': invalid step #2: mask.speed out of range 10..100" in errors
    assert json.loads(env.plugin.get_table().tasks["open_mask"].steps_json)[1]["mask"]["speed"] == 80


def test_removed_task_answers_politely(env):
    text = (TASKS_DIR / "tasks.yaml").read_text(encoding="utf-8")
    env.write(text.split("  salute:")[0])
    run(env.plugin.try_quick_phrase(FakeConn(), "xin chào"))
    assert "task_salute" not in env.fakes.func_map["helmet_tasks"]
    assert "task_salute" in env.fakes.registry               # stale tool lists still reach us
    client = FakeMCPClient()
    resp = run(call_task(env, FakeConn(client), "task_salute"))
    assert_valid_response(resp)
    assert resp.action is Action.RESPONSE
    assert resp.response == "Xin lỗi thưa ngài, tôi không thực hiện được: tác vụ salute không còn trong danh sách."
    assert client.calls == []


# ---------------------------------------------------------------------------
# LLM turn: per-turn merge (D4), results, errors
# ---------------------------------------------------------------------------
def test_two_concurrent_calls_are_merged_in_order(env):
    client = FakeMCPClient()
    conn = FakeConn(client)

    async def turn():
        # Same turn (same sentence_id), started back to back like connection.py does.
        return await asyncio.gather(call_task(env, conn, "task_open_mask"),
                                    call_task(env, conn, "task_eyes_on", unexpected="ignored"))

    r1, r2 = run(turn())
    assert len(client.calls) == 1
    tool, args, timeout, _ = client.calls[0]
    assert tool == "self_sequence_run" and timeout == 1.0
    table = env.plugin.get_table()
    assert json.loads(args["steps"]) == table.tasks["open_mask"].steps + table.tasks["eyes_on"].steps
    for resp in (r1, r2):
        assert_valid_response(resp)
        assert resp.action is Action.RECORD
    assert r1.response == "Đã mở mặt nạ, thưa ngài."
    assert r2.response == "Đèn mắt đã bật, thưa ngài."
    assert [text for _, text in conn.tts.queued] == [r1.response, r2.response]
    assert all(sid == "turn-1" for sid, _ in conn.tts.queued)
    logs = conn.logger.text()
    assert "merged 2 task call(s) into one sequence: open_mask, eyes_on" in logs
    assert re.search(r"\[LAT\] sid=sess-123 stage=mcp_call t_ms=\d+ tool=self_sequence_run ms=\d+ ok=1 "
                     r"tasks=open_mask,eyes_on$", logs, re.M)


def test_three_calls_merge_and_order_is_arrival_order(env):
    client = FakeMCPClient()
    conn = FakeConn(client)

    async def turn():
        return await asyncio.gather(*(call_task(env, conn, n) for n in
                                      ("task_close_mask", "task_eyes_off", "task_salute")))

    results = run(turn())
    assert len(client.calls) == 1
    table = env.plugin.get_table()
    expected = sum((table.tasks[i].steps for i in ("close_mask", "eyes_off", "salute")), [])
    assert json.loads(client.calls[0][1]["steps"]) == expected
    assert [r.response for r in results] == [table.tasks[i].reply for i in ("close_mask", "eyes_off", "salute")]


def test_different_turns_are_not_merged(env):
    client = FakeMCPClient()
    conn_a = FakeConn(client, sentence_id="turn-a")
    conn_b = FakeConn(client, sentence_id="turn-b")

    async def both():
        return await asyncio.gather(call_task(env, conn_a, "task_open_mask"),
                                    call_task(env, conn_b, "task_close_mask"))

    run(both())
    assert len(client.calls) == 2


def test_late_call_after_window_gets_its_own_sequence(env):
    client = FakeMCPClient()
    conn = FakeConn(client, plugin_cfg={"merge_window_ms": 10})

    async def turn():
        first = asyncio.ensure_future(call_task(env, conn, "task_open_mask"))
        await asyncio.sleep(0.1)
        second = await call_task(env, conn, "task_eyes_on")
        return await first, second

    run(turn())
    assert len(client.calls) == 2


def test_merge_over_limit_runs_what_fits(env):
    env.write("""\
version: 1
tasks:
  long_a:
    description: "A / a"
    reply: "Xong A."
    steps: [{delay: 10000}, {delay: 10000}, {delay: 10000}, {delay: 10000}]
  long_b:
    description: "B / b"
    reply: "Xong B."
    steps: [{delay: 10000}, {delay: 10000}, {delay: 10000}]
""")
    client = FakeMCPClient()
    conn = FakeConn(client)
    run(env.plugin.try_quick_phrase(conn, "xin chào"))

    async def turn():
        return await asyncio.gather(call_task(env, conn, "task_long_a"), call_task(env, conn, "task_long_b"))

    ra, rb = run(turn())
    assert len(client.calls) == 1 and len(json.loads(client.calls[0][1]["steps"])) == 4
    assert ra.action is Action.RECORD and ra.response == "Xong A."
    assert_valid_response(rb)
    assert rb.action is Action.RESPONSE
    assert rb.response.startswith("Xin lỗi thưa ngài, tôi không thực hiện được: chuỗi lệnh gộp vượt giới hạn")


def test_failed_merged_batch_apologises_once(env):
    # E3 "mở mặt nạ rồi bật đèn mắt" while the helmet does not answer: one merged device call fails for
    # both task calls; upstream speaks every RESPONSE, so only the first call may carry the apology.
    client = FakeMCPClient(error=TimeoutError("工具调用请求超时"))
    conn = FakeConn(client)

    async def turn():
        return await asyncio.gather(call_task(env, conn, "task_open_mask"), call_task(env, conn, "task_eyes_on"),
                                    call_task(env, conn, "task_salute"))

    results = run(turn())
    assert len(client.calls) == 1                      # still one merged self.sequence.run
    for resp in results:
        assert_valid_response(resp)
        assert resp.result.startswith("error: ")
    assert [r.action for r in results] == [Action.RESPONSE, Action.RECORD, Action.RECORD]
    apology = "Xin lỗi thưa ngài, tôi không thực hiện được: mũ không phản hồi kịp."
    assert [r.response for r in results] == [apology] * 3  # RECORD: history text, never spoken
    assert conn.tts.queued == []


def test_two_rejected_tasks_apologise_once_and_device_errors_still_speak(env):
    env.write("""\
version: 1
tasks:
  long_a:
    description: "A / a"
    reply: "Xong A."
    steps: [{delay: 10000}, {delay: 10000}, {delay: 10000}, {delay: 10000}]
  long_b:
    description: "B / b"
    reply: "Xong B."
    steps: [{delay: 10000}, {delay: 10000}, {delay: 10000}]
  long_c:
    description: "C / c"
    reply: "Xong C."
    steps: [{delay: 10000}, {delay: 10000}, {delay: 10000}]
""")
    client = FakeMCPClient(error=RuntimeError("工具调用错误: busy"))
    conn = FakeConn(client)
    run(env.plugin.try_quick_phrase(conn, "xin chào"))

    async def turn():
        return await asyncio.gather(*(call_task(env, conn, n) for n in ("task_long_a", "task_long_b", "task_long_c")))

    ra, rb, rc = run(turn())
    assert len(client.calls) == 1                      # long_a runs (and fails: busy)
    assert ra.action is Action.RESPONSE and ra.response.endswith("busy.")
    assert rb.action is Action.RESPONSE and "vượt giới hạn" in rb.response  # first "limit" rejection
    assert rc.action is Action.RECORD and rc.response == rb.response       # same reason: not spoken again


@pytest.mark.parametrize("error, expected", [
    (Exception("MCP错误: invalid step #2: mask.speed out of range 10..100 (got 5)"),
     "Xin lỗi thưa ngài, tôi không thực hiện được: bước 2 không hợp lệ (mask.speed out of range 10..100 (got 5))."),
    (TimeoutError("工具调用请求超时"), "Xin lỗi thưa ngài, tôi không thực hiện được: mũ không phản hồi kịp."),
    (asyncio.TimeoutError(), "Xin lỗi thưa ngài, tôi không thực hiện được: mũ không phản hồi kịp."),
    (RuntimeError("MCP客户端尚未准备就绪"),
     "Xin lỗi thưa ngài, tôi không thực hiện được: mũ chưa sẵn sàng, xin thử lại sau giây lát."),
    (ValueError("工具 self_sequence_run 不存在"),
     "Xin lỗi thưa ngài, tôi không thực hiện được: mũ chưa có lệnh này, cần cập nhật firmware."),
    (RuntimeError("工具调用错误: busy"), "Xin lỗi thưa ngài, tôi không thực hiện được: busy."),
])
def test_device_errors_are_mapped_to_vietnamese(env, error, expected):
    client = FakeMCPClient(error=error)
    conn = FakeConn(client)
    resp = run(call_task(env, conn, "task_open_mask"))
    assert_valid_response(resp)
    assert resp.action is Action.RESPONSE
    assert resp.response == expected
    assert resp.result.startswith("error: ")
    assert conn.tts.queued == []                     # upstream speaks RESPONSE itself
    assert re.search(r"stage=mcp_call .* ok=0", conn.logger.text())


def test_readiness_checks(env):
    # No MCP client at all (hello without features.mcp).
    resp = run(call_task(env, FakeConn(None), "task_open_mask"))
    assert resp.response.endswith("mũ chưa kết nối điều khiển.")
    # Never ready within mcp_ready_wait_s.
    client = FakeMCPClient(ready=False)
    started = time.monotonic()
    resp = run(call_task(env, FakeConn(client), "task_open_mask"))
    assert time.monotonic() - started >= 0.2
    assert "mũ chưa sẵn sàng" in resp.response and client.calls == []
    # Becomes ready while we wait.
    client = FakeMCPClient(ready=False)
    conn = FakeConn(client, plugin_cfg={"mcp_ready_wait_s": 2})

    async def later():
        task = asyncio.ensure_future(call_task(env, conn, "task_open_mask"))
        await asyncio.sleep(0.15)
        client.ready = True
        return await task

    resp = run(later())
    assert resp.action is Action.RECORD and len(client.calls) == 1
    # Firmware without the sequence tool.
    resp = run(call_task(env, FakeConn(FakeMCPClient(tools=())), "task_open_mask"))
    assert "cần cập nhật firmware" in resp.response


def test_reply_mode_response_and_missing_tts(env):
    client = FakeMCPClient()
    conn = FakeConn(client, plugin_cfg={"reply_mode": "response"})
    resp = run(call_task(env, conn, "task_open_mask"))
    assert resp.action is Action.RESPONSE and resp.response == "Đã mở mặt nạ, thưa ngài."
    assert conn.tts.queued == []
    conn = FakeConn(client)
    conn.tts = None
    resp = run(call_task(env, conn, "task_open_mask"))
    assert resp.action is Action.RESPONSE
    assert_valid_response(resp)


def test_task_without_reply_asks_llm(env):
    env.write((TASKS_DIR / "tasks.yaml").read_text(encoding="utf-8").replace(
        '    reply: "Xin kính chào ngài."\n', ""))
    client = FakeMCPClient()
    conn = FakeConn(client)
    run(env.plugin.try_quick_phrase(conn, "xin chào"))
    resp = run(call_task(env, conn, "task_salute"))
    assert_valid_response(resp)
    assert resp.action is Action.REQLLM and resp.response is None
    assert "salute" in resp.result and "4 steps" in resp.result


def test_reply_language_follows_user(env):
    env.write((TASKS_DIR / "tasks.yaml").read_text(encoding="utf-8").replace(
        'reply: "Đã mở mặt nạ, thưa ngài."', 'reply: {vi: "Đã mở mặt nạ, thưa ngài.", en: "Mask open, sir."}'))
    client = FakeMCPClient()
    conn = FakeConn(client)
    run(env.plugin.try_quick_phrase(conn, "xin chào"))
    conn.dialogue.put(Message(role="user", content="open the mask please"))
    assert run(call_task(env, conn, "task_open_mask")).response == "Mask open, sir."
    conn.dialogue.put(Message(role="user", content="Jarvis, open mask rồi bật đèn"))
    assert run(call_task(env, conn, "task_open_mask")).response == "Đã mở mặt nạ, thưa ngài."


# ---------------------------------------------------------------------------
# Quick phrases
# ---------------------------------------------------------------------------
def test_quick_phrase_returns_fast_and_runs_in_background(env):
    client = FakeMCPClient(delay=0.3)               # slow device answer
    conn = FakeConn(client)

    async def scenario():
        started = time.monotonic()
        handled = await env.plugin.try_quick_phrase(conn, "Jarvis, mở mặt nạ.")
        elapsed = time.monotonic() - started
        assert conn.spoken == []                    # nothing spoken yet: work is in the background
        await drain(env.plugin)
        return handled, elapsed

    handled, elapsed = run(scenario())
    assert handled is True
    assert elapsed < 0.1
    assert len(client.calls) == 1
    tool, args, _, _ = client.calls[0]
    assert tool == "self_sequence_run"
    assert args["steps"] == env.plugin.get_table().tasks["open_mask"].steps_json
    assert conn.stt == ["Jarvis, mở mặt nạ."]
    assert conn.client_abort is False
    assert conn.sentence_id != "turn-1"
    assert conn.spoken == [(conn.sentence_id, "Đã mở mặt nạ, thưa ngài.")]
    roles = [(m.role, m.content) for m in conn.dialogue.dialogue]
    assert roles[0] == ("user", "Jarvis, mở mặt nạ.")
    assert conn.dialogue.dialogue[1].tool_calls[0]["function"] == {"name": "task_open_mask", "arguments": "{}"}
    assert conn.dialogue.dialogue[2].role == "tool" and conn.dialogue.dialogue[2].content == "started"
    assert conn.dialogue.dialogue[2].tool_call_id == conn.dialogue.dialogue[1].tool_calls[0]["id"]
    assert roles[3] == ("assistant", "Đã mở mặt nạ, thưa ngài.")
    logs = conn.logger.text()
    assert re.search(r"^\[LAT\] sid=sess-123 stage=quick_phrase t_ms=\d{13} task=open_mask score=100$",
                     logs, re.M)
    assert re.search(r"^\[LAT\] sid=sess-123 stage=mcp_call t_ms=\d{13} tool=self_sequence_run ms=\d+ ok=1 "
                     r"tasks=open_mask$", logs, re.M)


@pytest.mark.parametrize("text", ["mặt nạ này nặng không?", "mở mặt nạ rồi bật đèn mắt", "Jarvis", "",
                                  "Jarvis, mấy giờ rồi?", "ặt đèn mắt"])
def test_quick_phrase_non_matches_go_to_llm(env, text):
    client = FakeMCPClient()
    conn = FakeConn(client)
    assert run(env.plugin.try_quick_phrase(conn, text)) is False
    assert client.calls == [] and conn.stt == [] and conn.spoken == []


def test_quick_phrase_json_wrapped_text(env):
    conn = FakeConn(FakeMCPClient())

    async def scenario():
        handled = await env.plugin.try_quick_phrase(conn, json.dumps({"speaker": "Long", "content": "tắt đèn mắt"}))
        await drain(env.plugin)
        return handled

    assert run(scenario()) is True
    assert conn.spoken[0][1] == "Đèn mắt đã tắt, thưa ngài."


def test_quick_phrase_device_error_speaks_apology(env):
    client = FakeMCPClient(error=Exception("MCP错误: invalid step #1: eyes.effect must be one of breathe"))
    conn = FakeConn(client)

    async def scenario():
        assert await env.plugin.try_quick_phrase(conn, "eyes on") is True
        await drain(env.plugin)

    run(scenario())
    text = conn.spoken[0][1]
    assert text.startswith("Xin lỗi thưa ngài, tôi không thực hiện được: bước 1 không hợp lệ")
    assert not CJK.search(text)
    assert conn.dialogue.dialogue[2].content.startswith("error: ")


def test_quick_phrase_without_mcp_client_apologizes(env):
    conn = FakeConn(None)

    async def scenario():
        assert await env.plugin.try_quick_phrase(conn, "đóng mặt nạ") is True
        await drain(env.plugin)

    run(scenario())
    assert conn.spoken[0][1] == "Xin lỗi thưa ngài, tôi không thực hiện được: mũ chưa kết nối điều khiển."


def test_quick_phrase_config_switches(env):
    conn = FakeConn(FakeMCPClient(), plugin_cfg={"quick_phrases_enabled": False})
    assert run(env.plugin.try_quick_phrase(conn, "mở mặt nạ")) is False
    conn = FakeConn(FakeMCPClient(), plugin_cfg={"quick_phrase_threshold": 101})
    assert run(env.plugin.try_quick_phrase(conn, "mở mặt nạ")) is False
    conn = FakeConn(FakeMCPClient(), plugin_cfg={"wake_words": ["friday"]})

    async def scenario():
        handled = await env.plugin.try_quick_phrase(conn, "Friday, mở mặt nạ")
        await drain(env.plugin)
        return handled

    assert run(scenario()) is True


def test_quick_phrase_history_text_mode(env):
    conn = FakeConn(FakeMCPClient(), plugin_cfg={"quick_phrase_history": "text"})

    async def scenario():
        await env.plugin.try_quick_phrase(conn, "open the mask")
        await drain(env.plugin)

    run(scenario())
    assert [m.role for m in conn.dialogue.dialogue] == ["user", "assistant"]


def test_quick_phrase_reply_dropped_when_new_turn_started(env):
    client = FakeMCPClient(delay=0.2)
    conn = FakeConn(client)

    async def scenario():
        await env.plugin.try_quick_phrase(conn, "mở mặt nạ")
        await asyncio.sleep(0.05)
        conn.sentence_id = "new-llm-turn"            # user spoke again meanwhile
        await drain(env.plugin)

    run(scenario())
    assert conn.spoken == []
    assert conn.dialogue.dialogue[-1].content == "Đã mở mặt nạ, thưa ngài."


def test_hooks_never_raise(env):
    class Bare:            # a connection object without any of the usual attributes
        pass

    async def scenario():
        handled = await env.plugin.try_quick_phrase(Bare(), "mở mặt nạ")
        await drain(env.plugin)                   # background part must not blow up either
        return handled

    assert run(scenario()) is True
    assert run(env.plugin.try_quick_phrase(FakeConn(), None)) is False
    assert run(env.plugin.try_quick_phrase(FakeConn(), 12345)) is False
    assert env.plugin.on_mcp_ready(Bare()) is None
    assert run(_await_hook(env, Bare())) is None


# ---------------------------------------------------------------------------
# Device sync on MCP ready
# ---------------------------------------------------------------------------
def test_on_mcp_ready_saves_sync_tasks(env):
    client = FakeMCPClient()
    conn = FakeConn(client)

    async def scenario():
        task = env.plugin.on_mcp_ready(conn)      # sync hook: schedules and returns at once
        assert isinstance(task, asyncio.Task)
        assert client.calls == []
        await task

    run(scenario())
    saves = [(tool, args) for tool, args, _, _ in client.calls]
    table = env.plugin.get_table()
    assert [tool for tool, _ in saves] == ["self_sequence_save"] * 4
    assert [args["name"] for _, args in saves] == ["open_mask", "close_mask", "eyes_on", "eyes_off"]
    for _, args in saves:
        assert len(args["name"]) <= 11
        assert args["steps"] == table.tasks[args["name"]].steps_json
    assert "device sync (aa:bb:cc:dd:ee:ff): saved [open_mask, close_mask, eyes_on, eyes_off]" in conn.logger.text()

    # Next connection of the same device: nothing changed -> nothing re-sent.
    conn2 = FakeConn(client)
    run(_await_hook(env, conn2))
    assert len(client.calls) == 4
    # Changed steps are re-sent, unchanged ones skipped.
    env.write((TASKS_DIR / "tasks.yaml").read_text(encoding="utf-8").replace(
        "eyes: {effect: fade_out, duration_ms: 800}", "eyes: {effect: fade_out, duration_ms: 900}"))
    run(_await_hook(env, FakeConn(client)))
    assert [args["name"] for _, args, _, _ in client.calls[4:]] == ["eyes_off"]
    # TTL 0: always sync.
    run(_await_hook(env, FakeConn(client, plugin_cfg={"sync_cache_ttl_s": 0})))
    assert len(client.calls) == 9


async def _await_hook(env, conn):
    task = env.plugin.on_mcp_ready(conn)
    if task is not None:
        await task


def test_on_mcp_ready_stops_when_device_lacks_save_tool(env):
    client = FakeMCPClient(tools=("self_sequence_run",))
    conn = FakeConn(client)
    run(_await_hook(env, conn))
    assert client.calls == []
    assert "device has no tool self_sequence_save" in conn.logger.text("WARNING")


def test_on_mcp_ready_failure_is_not_cached(env):
    client = FakeMCPClient(error=Exception("MCP错误: NVS full"))
    run(_await_hook(env, FakeConn(client)))
    assert len(client.calls) == 4                        # each failure logged, loop continues
    client.error = None
    run(_await_hook(env, FakeConn(client)))
    assert len(client.calls) == 8


def test_on_mcp_ready_without_loop_and_disabled(env):
    conn = FakeConn(FakeMCPClient())
    assert env.plugin.on_mcp_ready(conn) is None         # no running loop, conn.loop is None
    assert "no running event loop" in conn.logger.text("WARNING")
    conn = FakeConn(FakeMCPClient(), plugin_cfg={"sync_on_ready": False})

    async def scenario():
        return env.plugin.on_mcp_ready(conn)

    assert run(scenario()) is None


# ---------------------------------------------------------------------------
# More than max_task_functions tasks -> single run_helmet_task(task enum)
# ---------------------------------------------------------------------------
def many_tasks(n):
    body = "".join(f'  t{i:02d}:\n    description: "Tác vụ {i} / task {i}"\n    steps: [{{delay: {i}}}]\n'
                   for i in range(n))
    return "version: 1\ntasks:\n" + body + (
        '  open_mask:\n    description: "Mở mặt nạ / open"\n    quick_phrases: ["mở mặt nạ"]\n'
        '    reply: "Đã mở mặt nạ, thưa ngài."\n    steps: [{mask: {action: open}}]\n')


def test_single_function_mode(env):
    env.write(many_tasks(20))                             # 21 tasks > 20
    client = FakeMCPClient()
    conn = FakeConn(client)
    run(env.plugin.try_quick_phrase(conn, "xin chào"))
    assert env.plugin._STATE.mode == "single"
    assert env.fakes.func_map["helmet_tasks"] == ["run_helmet_task"]
    desc = env.fakes.registry["run_helmet_task"].description["function"]
    assert desc["parameters"]["required"] == ["task"]
    assert desc["parameters"]["properties"]["task"]["enum"] == [f"t{i:02d}" for i in range(20)] + ["open_mask"]
    assert "open_mask: Mở mặt nạ / open" in desc["description"]

    resp = run(call_task(env, conn, "run_helmet_task", task="t05"))
    assert resp.action is Action.REQLLM and json.loads(client.calls[0][1]["steps"]) == [{"delay": 5}]
    for bad in ({}, {"task": "nope"}, {"task": 7}):
        resp = run(call_task(env, conn, "run_helmet_task", **bad))
        assert_valid_response(resp)
        assert resp.action is Action.RESPONSE and resp.response.startswith("Xin lỗi thưa ngài")

    async def quick():
        await env.plugin.try_quick_phrase(conn, "mở mặt nạ")
        await drain(env.plugin)

    run(quick())
    chain = [m for m in conn.dialogue.dialogue if m.tool_calls]
    assert chain[-1].tool_calls[0]["function"] == {"name": "run_helmet_task", "arguments": '{"task": "open_mask"}'}

    # Back to <= 20 tasks: task_<id> functions again; enum dict mutated in place for old lists.
    env.write(many_tasks(3))
    run(env.plugin.try_quick_phrase(conn, "xin chào"))
    assert env.plugin._STATE.mode == "functions"
    assert env.fakes.func_map["helmet_tasks"] == ["task_t00", "task_t01", "task_t02", "task_open_mask"]
