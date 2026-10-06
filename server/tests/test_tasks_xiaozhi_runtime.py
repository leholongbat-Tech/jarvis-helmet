"""helmet_tasks inside the real xiaozhi-esp32-server v0.9.7 runtime (SV-D smoke test).

Skipped unless the upstream server modules can be imported, i.e. run it inside the server image
with the plugin installed at plugins_func/functions/helmet_tasks.py, taskslib on PYTHONPATH and a
data/.config.yaml present (cwd = /opt/xiaozhi-esp32-server):

    python -m pytest -q /path/to/server/tests/test_tasks_xiaozhi_runtime.py

Uses the genuine register.py, plugin auto-import (via core.connection, as app.py does),
ServerPluginExecutor, MCPClient, handle_mcp_message, call_mcp_tool, send_stt_message, speak_txt,
Dialogue and a TTSProviderBase subclass. Only the websocket / firmware side is simulated.
"""
import asyncio
import json
import re
import sys

import pytest

try:
    import core.connection  # noqa: F401  same import chain as app.py: auto-imports every plugin
    import plugins_func.functions.helmet_tasks as helmet
    import taskslib
    from core.providers.tools.device_mcp import MCPClient, handle_mcp_message
    from core.providers.tools.server_plugins import ServerPluginExecutor
    from core.providers.tts.base import TTSProviderBase
    from core.providers.tts.dto.dto import SentenceType
    from core.utils.dialogue import Dialogue
    from plugins_func.register import Action, ToolType, all_function_registry, module_func_map
except Exception as exc:  # pragma: no cover - outside the server image
    pytest.skip(f"xiaozhi runtime not available: {type(exc).__name__}: {exc}", allow_module_level=True)

CJK = re.compile(r"[㐀-鿿＀-￯]")
TOOL_SCHEMA = {"type": "object", "properties": {"steps": {"type": "string", "maxLength": 4000}},
               "required": ["steps"]}


class SilentTTS(TTSProviderBase):
    async def text_to_speak(self, text, output_file):
        return b""


class SimDevice:
    """Websocket stand-in that answers MCP tools/call like firmware v2.5.0 (via handle_mcp_message)."""

    def __init__(self, conn, error=None):
        self.conn = conn
        self.error = error
        self.sent = []
        self.calls = []

    async def send(self, message):
        data = json.loads(message)
        self.sent.append(data)
        payload = data.get("payload") or {}
        if data.get("type") == "mcp" and payload.get("method") == "tools/call":
            self.calls.append(payload["params"])
            asyncio.get_running_loop().call_later(0.01, lambda: asyncio.ensure_future(self._reply(payload)))

    async def _reply(self, payload):
        if self.error:
            reply = {"jsonrpc": "2.0", "id": payload["id"],
                     "error": {"code": -32602, "message": self.error}}
        else:
            text = "saved" if payload["params"]["name"] == "self.sequence.save" else "started"
            reply = {"jsonrpc": "2.0", "id": payload["id"],
                     "result": {"content": [{"type": "text", "text": text}], "isError": False}}
        await handle_mcp_message(self.conn, self.conn.mcp_client, reply)


class Conn:
    def __init__(self, device_id="11:22:33:44:55:66", error=None):
        self.config = {
            "selected_module": {"Intent": "function_call"},
            "Intent": {"function_call": {"type": "function_call", "functions": ["helmet_tasks"]}},
            "plugins": {"helmet_tasks": {"merge_window_ms": 80, "mcp_timeout_s": 2,
                                         "mcp_ready_wait_s": 1, "sync_delay_s": 0}},
        }
        self.session_id = "runtime-session"
        self.device_id = device_id
        self.headers = {"device-id": device_id}
        self.features = {"mcp": True}
        self.websocket = SimDevice(self, error)
        self.mcp_client = MCPClient()
        self.dialogue = Dialogue()
        self.tts = SilentTTS({"output_dir": "tmp/"}, True)
        self.sentence_id = "turn-1"
        self.client_abort = True
        self.client_is_speaking = False
        self.func_handler = None
        self.loop = None

    def clearSpeakStatus(self):
        self.client_is_speaking = False

    async def make_ready(self):
        for name in ("self.sequence.run", "self.sequence.save"):
            await self.mcp_client.add_tool({"name": name, "description": name, "inputSchema": TOOL_SCHEMA})
        await self.mcp_client.set_ready(True)
        return self


def queued(conn):
    items = []
    while True:
        try:
            items.append(conn.tts.tts_text_queue.get_nowait())
        except Exception:
            return items


async def drain():
    for _ in range(50):
        pending = [t for t in list(helmet._STATE.bg_tasks) if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def test_runtime_versions():
    assert sys.version_info[:2] == (3, 10)
    assert taskslib._jsonschema is not None, "jsonschema missing in the image"
    assert taskslib._rf_fuzz is not None, "rapidfuzz missing in the image (fallback is slower)"


def test_plugin_loaded_by_auto_import_and_registered():
    table = helmet.get_table()
    assert table is not None, helmet._STATE.last_error
    names = module_func_map["helmet_tasks"]
    assert names == [f"task_{tid}" for tid in table.ids]
    item = all_function_registry["task_open_mask"]
    assert item.type is ToolType.SYSTEM_CTL
    assert helmet._STATE.global_cfg.get("merge_window_ms") == 80     # read from the cached config


def test_server_plugin_executor_exposes_tasks():
    tools = ServerPluginExecutor(Conn()).get_tools()
    for tid in helmet.get_table().ids:
        assert f"task_{tid}" in tools
    desc = tools["task_open_mask"].description["function"]
    assert desc["name"] == "task_open_mask" and " / " in desc["description"]


def test_execute_task_through_real_mcp_stack():
    async def scenario():
        conn = await Conn().make_ready()
        executor = ServerPluginExecutor(conn)
        resp = await executor.execute(conn, "task_open_mask", {})
        return conn, resp

    conn, resp = asyncio.run(scenario())
    assert resp.action is Action.RECORD and resp.response == "Đã mở mặt nạ, thưa ngài."
    assert len(conn.websocket.calls) == 1
    params = conn.websocket.calls[0]
    assert params["name"] == "self.sequence.run"          # sanitized name mapped back by call_mcp_tool
    assert params["arguments"]["steps"] == helmet.get_table().tasks["open_mask"].steps_json
    middle = [m for m in queued(conn) if m.sentence_type is SentenceType.MIDDLE]
    assert "".join(m.content_detail for m in middle) == "Đã mở mặt nạ, thưa ngài."


def test_merge_through_real_executor():
    async def scenario():
        conn = await Conn().make_ready()
        executor = ServerPluginExecutor(conn)
        results = await asyncio.gather(executor.execute(conn, "task_open_mask", {}),
                                       executor.execute(conn, "task_eyes_on", {}))
        return conn, results

    conn, results = asyncio.run(scenario())
    assert [r.action for r in results] == [Action.RECORD, Action.RECORD]
    assert len(conn.websocket.calls) == 1
    table = helmet.get_table()
    steps = json.loads(conn.websocket.calls[0]["arguments"]["steps"])
    assert steps == table.tasks["open_mask"].steps + table.tasks["eyes_on"].steps


def test_device_error_through_real_handle_mcp_message():
    async def scenario():
        conn = await Conn(error="invalid step #2: mask.speed out of range 10..100 (got 5)").make_ready()
        return await ServerPluginExecutor(conn).execute(conn, "task_open_mask", {})

    resp = asyncio.run(scenario())
    assert resp.action is Action.RESPONSE
    assert resp.response.startswith("Xin lỗi thưa ngài, tôi không thực hiện được: bước 2 không hợp lệ")
    assert not CJK.search(resp.response)
    # technical detail kept for logs/history; upstream prefixes it with "MCP错误: ", the Jarvis
    # image drops that prefix (patch mcp_error_prefix, strings_vi.json "mcp_error_prefix": "")
    assert "invalid step #2: mask.speed out of range" in resp.result


def test_not_ready_is_vietnamese():
    async def scenario():
        conn = Conn()                                  # tools never listed -> not ready
        return await ServerPluginExecutor(conn).execute(conn, "task_close_mask", {})

    resp = asyncio.run(scenario())
    assert resp.response == "Xin lỗi thưa ngài, tôi không thực hiện được: mũ chưa sẵn sàng, xin thử lại sau giây lát."


def test_quick_phrase_with_real_send_stt_and_speak_txt():
    async def scenario():
        conn = await Conn().make_ready()
        handled = await helmet.try_quick_phrase(conn, "Jarvis, mở mặt nạ.")
        await drain()
        return conn, handled

    conn, handled = asyncio.run(scenario())
    assert handled is True
    sent = conn.websocket.sent
    mcp = next(i for i, m in enumerate(sent) if m.get("type") == "mcp")
    stt = next(i for i, m in enumerate(sent) if m.get("type") == "stt")
    assert mcp < stt                                   # device command leaves first
    assert sent[stt]["session_id"] == "runtime-session"
    assert {"type": "tts", "state": "start", "session_id": "runtime-session"} in sent
    assert conn.client_abort is False and conn.sentence_id != "turn-1"
    items = queued(conn)
    assert [m.sentence_type for m in items][0] is SentenceType.FIRST
    assert items[-1].sentence_type is SentenceType.LAST
    assert all(m.sentence_id == conn.sentence_id for m in items)
    assert "".join(m.content_detail or "" for m in items if m.sentence_type is SentenceType.MIDDLE) == \
        "Đã mở mặt nạ, thưa ngài."
    roles = [m.role for m in conn.dialogue.dialogue]
    assert roles == ["user", "assistant", "tool", "assistant"]
    llm_view = conn.dialogue.get_llm_dialogue()
    assert any(m.get("tool_calls") for m in llm_view)


def test_on_mcp_ready_with_real_mcp_client():
    async def scenario():
        conn = await Conn(device_id="66:55:44:33:22:11").make_ready()
        task = helmet.on_mcp_ready(conn)
        assert task is not None
        await task
        return conn

    conn = asyncio.run(scenario())
    names = [c["arguments"]["name"] for c in conn.websocket.calls if c["name"] == "self.sequence.save"]
    assert names == ["open_mask", "close_mask", "eyes_on", "eyes_off"]
