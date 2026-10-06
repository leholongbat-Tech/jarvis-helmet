"""Tests for server/xiaozhi/patches: apply_patches.py, strings_vi.json, jarvis_support.py, scan_cjk.py.

apply_patches.py runs against a temp copy of the upstream xiaozhi-server v0.9.7 tree (env
XIAOZHI_SRC, default: the scratchpad clone; skipped when absent). Besides structural checks
(every edit applied exactly once, idempotent, all-or-nothing, still compiles), several patched
upstream functions are extracted from the patched files and executed with small stubs, so the
hooks, the hello sample rate, the localized errors and the [LAT] lines are tested for real
without the full xiaozhi runtime.
"""
import ast
import asyncio
import hashlib
import importlib.util
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import textwrap
import types
import uuid

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
PATCHES = os.path.join(REPO, "server", "xiaozhi", "patches")
APPLY = os.path.join(PATCHES, "apply_patches.py")
STRINGS = os.path.join(PATCHES, "strings_vi.json")
README = os.path.join(PATCHES, "README.md")
DEFAULT_XIAOZHI_SRC = (
    "C:/Users/Longlhvip/AppData/Local/Temp/claude/c--Users-Longlhvip-0-Projects-Jarvis-helmet-jarvis-helmet/"
    "c2a0007c-ccbb-4442-8698-fefc420b462d/scratchpad/upstream/xiaozhi-esp32-server/main/xiaozhi-server"
)
COPY_ITEMS = ("app.py", "config", "core", "plugins_func")
LAT_STAGES = {"vad_end", "asr_done", "llm_first_token", "llm_done", "tts_first_audio", "first_packet_sent",
              "quick_phrase", "mcp_call"}
LAT_LINE_RE = re.compile(r"^\[LAT\] sid=\S+ stage=(\S+) t_ms=\d+( [A-Za-z0-9_]+=\S+)*$")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ap = _load("jarvis_apply_patches", APPLY)
js = _load("jarvis_support_under_test", os.path.join(PATCHES, "jarvis_support.py"))
scan = _load("jarvis_scan_cjk", os.path.join(PATCHES, "scan_cjk.py"))
EDITS = ap.build_edits(ap.load_strings(STRINGS))
PATCHED_FILES = sorted({e.file for e in EDITS})
with open(STRINGS, "r", encoding="utf-8") as _fh:
    S = json.load(_fh)


def _src():
    src = os.environ.get("XIAOZHI_SRC", DEFAULT_XIAOZHI_SRC)
    if not os.path.isfile(os.path.join(src, "app.py")):
        pytest.skip("upstream xiaozhi-server tree not available (set XIAOZHI_SRC)")
    return src


def _copy_tree(dest):
    src = _src()
    os.makedirs(dest, exist_ok=True)
    for item in COPY_ITEMS:
        source = os.path.join(src, item)
        target = os.path.join(dest, item)
        if os.path.isdir(source):
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(source, target)
    return dest


def _run(root, *args):
    proc = subprocess.run(
        [sys.executable, APPLY, "--root", str(root)] + list(args),
        capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
    return proc.returncode, proc.stdout + proc.stderr


def _hashes(root, files):
    out = {}
    for rel in files:
        path = os.path.join(root, rel)
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                out[rel] = hashlib.sha256(fh.read()).hexdigest()
        else:
            out[rel] = None
    return out


def _read(root, rel):
    with open(os.path.join(root, rel), "r", encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def patched(tmp_path_factory):
    root = _copy_tree(str(tmp_path_factory.mktemp("xiaozhi_patched")))
    rc, out = _run(root)
    return root, rc, out


# ----------------------------------------------------------------------------- engine


def test_list_shows_every_edit():
    rc, out = _run(".", "--list")
    assert rc == 0
    for e in EDITS:
        assert e.id in out


def test_edit_definitions_are_consistent():
    seen = set()
    for e in EDITS:
        assert (e.file, e.id) not in seen
        seen.add((e.file, e.id))
        assert e.replacement.count(e.marker) == 1
        assert e.marker not in e.anchor
        assert e.count == 1


def test_check_on_pristine_tree_writes_nothing(tmp_path):
    root = _copy_tree(str(tmp_path))
    before = _hashes(root, PATCHED_FILES + list(ap.SUPPORT_FILES))
    rc, out = _run(root, "--check")
    assert rc == 0, out
    assert "CHECK OK (pending=%d)" % len(EDITS) in out
    assert _hashes(root, PATCHED_FILES + list(ap.SUPPORT_FILES)) == before


def test_every_edit_applied_exactly_once(patched):
    root, rc, out = patched
    assert rc == 0, out
    assert "OK (applied=%d;" % len(EDITS) in out
    for e in EDITS:
        text = _read(root, e.file)
        assert text.count(e.marker) == 1, (e.file, e.id)
        assert text.count(e.anchor) == e.replacement.count(e.anchor), (e.file, e.id)


def test_support_file_installed(patched):
    root, _, _ = patched
    for rel, src in ap.SUPPORT_FILES.items():
        with open(os.path.join(PATCHES, src), "rb") as a, open(os.path.join(root, rel), "rb") as b:
            assert a.read() == b.read()


def test_second_run_is_a_noop(patched):
    root, _, _ = patched
    before = _hashes(root, PATCHED_FILES + list(ap.SUPPORT_FILES))
    rc, out = _run(root)
    assert rc == 0, out
    assert "OK (already=%d; 0 file(s) written)" % len(EDITS) in out
    assert _hashes(root, PATCHED_FILES + list(ap.SUPPORT_FILES)) == before
    rc, out = _run(root, "--check")
    assert rc == 0 and "CHECK OK (already=%d)" % len(EDITS) in out


def test_patched_files_compile(patched):
    root, _, _ = patched
    for rel in PATCHED_FILES + list(ap.SUPPORT_FILES):
        path = os.path.join(root, rel)
        py_compile.compile(path, cfile=os.path.join(root, "_compiled.pyc"), doraise=True)
        with open(path, "r", encoding="utf-8") as fh:
            ast.parse(fh.read(), filename=path, feature_version=(3, 10))  # image runs Python 3.10


def test_anchor_mismatch_fails_and_writes_nothing(tmp_path):
    root = _copy_tree(str(tmp_path))
    target = os.path.join(root, "core", "handle", "intentHandler.py")
    with open(target, "r", encoding="utf-8") as fh:
        text = fh.read()
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text.replace('    if conn.intent_type == "function_call":\n', '    if conn.intent_type in ("function_call",):\n'))
    before = _hashes(root, PATCHED_FILES + list(ap.SUPPORT_FILES))
    rc, out = _run(root)
    assert rc == 1
    assert "FAILED" in out and "quick_phrase_hook" in out and "found 0 time(s)" in out
    assert _hashes(root, PATCHED_FILES + list(ap.SUPPORT_FILES)) == before


def test_duplicate_anchor_fails(tmp_path):
    root = _copy_tree(str(tmp_path))
    target = os.path.join(root, "core", "handle", "helloHandle.py")
    with open(target, "a", encoding="utf-8", newline="\n") as fh:
        fh.write('\n\ndef _dup(conn, audio_params):\n    if True:\n        conn.welcome_msg["audio_params"] = audio_params\n')
    rc, out = _run(root, "--check")
    assert rc == 1 and "hello_sample_rate" in out and "found 2 time(s)" in out


def test_crlf_files_are_patched_and_keep_crlf(tmp_path):
    root = _copy_tree(str(tmp_path))
    target = os.path.join(root, "core", "utils", "dialogue.py")
    with open(target, "rb") as fh:
        data = fh.read()
    with open(target, "wb") as fh:
        fh.write(data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    rc, out = _run(root)
    assert rc == 0, out
    with open(target, "rb") as fh:
        patched_data = fh.read()
    assert b"[jarvis-patch:tool_interrupted]" in patched_data
    assert patched_data.count(b"\n") == patched_data.count(b"\r\n")


def _strings_variant(tmp_path, mutate):
    data = json.loads(json.dumps(S))
    mutate(data)
    path = tmp_path / "strings_variant.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_unknown_placeholder_in_strings_fails(tmp_path):
    root = _copy_tree(str(tmp_path / "tree"))
    path = _strings_variant(tmp_path, lambda d: d.__setitem__("device_tool_failed", "Lỗi: {nope}"))
    rc, out = _run(root, "--check", "--strings", path)
    assert rc == 1 and "{nope}" in out and "device_tool_failed" in out


def test_missing_string_key_fails(tmp_path):
    root = _copy_tree(str(tmp_path / "tree"))
    path = _strings_variant(tmp_path, lambda d: d.pop("tool_call_failed"))
    rc, out = _run(root, "--check", "--strings", path)
    assert rc == 1 and "tool_call_failed" in out


def test_template_expr_generates_format_calls():
    strings = ap.Strings({"a": "Dòng {i}: ", "b": "x {{literal}}", "c": "plain"}, "t")
    assert ap.template_expr(strings, "a", i="i") == '"Dòng {i}: ".format(i=i)'
    assert ap.template_expr(strings, "b", i="i") == '"x {{literal}}".format()'
    assert ap.template_expr(strings, "c", i="i") == '"plain"'
    with pytest.raises(ap.PatchError):
        ap.template_expr(ap.Strings({"a": "{i:>3}"}, "t"), "a", i="i")


# ----------------------------------------------------------------------------- content


def _flatten(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            if key.startswith("_"):
                continue
            yield from _flatten(value, prefix + key + ".")
    elif isinstance(node, list):
        for item in node:
            yield from _flatten(item, prefix)
    elif isinstance(node, str) and node:
        yield prefix.rstrip("."), node


def test_every_localized_string_reaches_the_code(patched):
    root, _, _ = patched
    corpus = "\n".join(_read(root, rel) for rel in PATCHED_FILES)
    for key, value in _flatten(S):
        escaped = json.dumps(value, ensure_ascii=False)[1:-1]
        twice = json.dumps(escaped, ensure_ascii=False)[1:-1]  # values embedded in JSON arguments
        assert escaped in corpus or twice in corpus, key


def _non_log_strings(root, rel):
    return [e["text"] for e in scan.scan_file(os.path.join(root, rel)) if e["kind"] in ("OTHER", "EXC")]


UPSTREAM_USER_FACING = [
    "哎呀，网络遇到点问题", "我在这里哦", "我一直都在呢", "请问您需要什么帮助", "MCP错误", "工具调用请求超时",
    "设备端MCP客户端未准备就绪", "设备端MCP客户端未初始化", "MCP客户端尚未准备就绪", "给我讲个故事吧",
    "再见，下次再聊", "已直接回复", "当用户的请求不匹配其他任何工具时", "你回复用户的完整内容", "无法解析函数参数",
    "插件函数", "工具类型", "单行表格", "表头是", "动作已取消", "主人，小智现在有点忙", "再见，祝您生活愉快",
    "退出意图已处理", "嘿，你好呀", "已达到最大工具调用次数限制", "当用户想结束对话", "和用户友好结束对话",
    "用于具体日期的阴历", "要查询的日期", "要查询的内容", "日期格式错误", "默认查询干支年", "根据以下信息回应用户",
    "工具调用返回错误", "工具调用错误", "参数JSON解析失败", "无法解析任何有效的JSON对象", "参数类型错误",
    "参数必须是字典类型", "参数处理失败", "时间过得真快",
]


def test_upstream_chinese_user_facing_strings_are_gone(patched):
    root, _, _ = patched
    for rel in PATCHED_FILES:
        for text in _non_log_strings(root, rel):
            for needle in UPSTREAM_USER_FACING:
                assert needle not in text, (rel, needle, text)


# Chinese literals that may stay in patched files: inactive paths with the Jarvis config.
ALLOWED_LEFTOVERS = {
    "core/connection.py": ["服务器重启中"],  # manager-api server restart command
    "core/handle/intentHandler.py": ["当前时间", "今天日期", "今天农历", "请根据以上信息回答", "工具调用超时"],  # intent_llm
    "core/handle/receiveAudioHandle.py": ["不好意思", "绑定码", "请登录控制面板", "绑定设备", "没有找到该设备"],  # manager-api
    "core/handle/sendAudioHandle.py": ["客户端已中止"],  # internal CancelledError text
    "core/providers/asr/base.py": ["磁盘空间不足"],  # logged OSError
    "core/providers/tools/server_plugins/plugin_executor.py": ["澎湃新闻", "新闻源", "等。可选参数"],  # news plugin
    "core/providers/tools/unified_tool_handler.py": ["无响应"],  # dead _combine_responses
    "core/api/ota_handler.py": ["OTA请求设备ID为空", "OTA请求ClientID为空", "OTA接口运行正常", "OTA接口异常"],  # HTTP only
    "core/utils/util.py": ["配置错误", "API key", "ffmpeg", "conda", "libiconv", "解决方法", "错误详情", "未知错误。",
                           "建议您"],  # startup console messages
    "core/utils/dialogue.py": ["当前说话人"],  # voiceprint (disabled)
    "core/utils/tts.py": ["不支持的TTS类型", "请检查该配置的type是否设置正确"],  # factory error at startup
    "plugins_func/functions/get_time.py": ["农历", "干支", "生肖", "八字", "节日", "节气", "月", "日", "冲煞", "星座",
                                           "纳音", "彭祖", "值日", "值神", "廿八宿", "吉神", "胎神", "宜", "忌",
                                           "默认返回"],  # get_lunar data (LLM only, translated)
    "plugins_func/functions/handle_exit_intent.py": ["退出意图处理失败"],  # Action.NONE: never spoken
    "core/websocket_server.py": ["端口正常"],  # raw frame for query-string test clients without device-id
    "core/providers/asr/openai.py": ["API请求失败"],  # exception text, caught and only logged
}


def test_remaining_chinese_in_patched_files_is_known_inactive(patched):
    root, _, _ = patched
    for rel in PATCHED_FILES:
        for text in _non_log_strings(root, rel):
            allowed = ALLOWED_LEFTOVERS.get(rel, [])
            assert any(a in text for a in allowed), "unexpected Chinese literal in %s: %r" % (rel, text)


def test_lat_stages_used_by_patches_are_contract_stages(patched):
    root, _, _ = patched
    stages = set()
    for rel in PATCHED_FILES:
        stages.update(re.findall(r'_jarvis\.lat\([^,]+, "([a-z_]+)"', _read(root, rel)))
    with open(os.path.join(PATCHES, "jarvis_support.py"), "r", encoding="utf-8") as fh:
        stages.update(re.findall(r'lat\([a-z_]+, "([a-z_]+)"', fh.read()))
    assert stages and stages <= LAT_STAGES, stages
    assert {"vad_end", "asr_done", "llm_first_token", "llm_done", "tts_first_audio", "first_packet_sent",
            "mcp_call"} <= stages


def test_quick_phrase_hook_position(patched):
    root, _, _ = patched
    text = _read(root, "core/handle/intentHandler.py")
    wake = text.index("if await checkWakeupWords(conn, filtered_text):")
    hook = text.index("await _jarvis_try_quick_phrase(conn, text)")
    branch = text.index('if conn.intent_type == "function_call":')
    assert wake < hook < branch


def test_mcp_ready_hook_position(patched):
    root, _, _ = patched
    text = _read(root, "core/providers/tools/device_mcp/mcp_handler.py")
    ready = text.index("await mcp_client.set_ready(True)")
    refresh = text.index("conn.func_handler.tool_manager.refresh_tools()")
    hook = text.index("_jarvis_on_mcp_ready(conn)")
    assert ready < refresh < hook


def test_direct_answer_tool_and_fewshot_are_localized(patched):
    root, _, _ = patched
    source = _read(root, "core/connection.py")
    tree = ast.parse(source)
    tool = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "DIRECT_ANSWER_TOOL" for t in node.targets):
            tool = ast.literal_eval(node.value)
    assert tool["function"]["description"] == S["direct_answer"]["description"]
    assert tool["function"]["parameters"]["properties"]["response"]["description"] == S["direct_answer"]["response_description"]

    class Msg(object):
        def __init__(self, role, content=None, tool_calls=None, tool_call_id=None, is_temporary=False):
            self.role, self.content, self.tool_calls, self.tool_call_id = role, content, tool_calls, tool_call_id

    fake_self = types.SimpleNamespace(
        intent_type="function_call",
        func_handler=types.SimpleNamespace(get_functions=lambda: [{"function": {"name": "handle_exit_intent"}}]),
        dialogue=types.SimpleNamespace(items=[]),
        logger=FakeLogger(),
    )
    fake_self.dialogue.put = fake_self.dialogue.items.append
    fn = _function(source, "_inject_tool_call_fewshot", {"Message": Msg}, cls="ConnectionHandler")
    fn(fake_self)
    msgs = fake_self.dialogue.items
    assert msgs[0].content == S["fewshot"]["direct_user"]
    assert json.loads(msgs[1].tool_calls[0]["function"]["arguments"]) == {"response": S["fewshot"]["direct_response"]}
    assert msgs[2].content == S["fewshot"]["direct_tool_result"]
    assert msgs[3].content == S["fewshot"]["exit_user"]
    assert json.loads(msgs[4].tool_calls[0]["function"]["arguments"]) == {"say_goodbye": S["fewshot"]["exit_goodbye"]}
    assert msgs[5].content == S["fewshot"]["exit_tool_result"] and msgs[6].content == S["fewshot"]["exit_goodbye"]


# ----------------------------------------------------------------------------- behaviour (stubs)


class FakeLogger(object):
    def __init__(self):
        self.records = []

    def bind(self, **_kwargs):
        return self

    def _log(self, level, msg, *args):
        self.records.append((level, msg))

    def debug(self, msg, *a):
        self._log("DEBUG", msg)

    def info(self, msg, *a):
        self._log("INFO", msg)

    def warning(self, msg, *a):
        self._log("WARNING", msg)

    def error(self, msg, *a):
        self._log("ERROR", msg)

    def lat(self):
        return [m for level, m in self.records if level == "INFO" and str(m).startswith("[LAT]")]


class FakeWebSocket(object):
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(data)


def _conn(**kwargs):
    conn = types.SimpleNamespace(session_id="sid-1", logger=FakeLogger(), websocket=FakeWebSocket())
    for key, value in kwargs.items():
        setattr(conn, key, value)
    return conn


def _function(source, name, namespace, cls=None):
    """Extract a (possibly nested-in-class) function from source and exec it with namespace."""
    tree = ast.parse(source)
    body = tree.body
    if cls:
        body = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls).body
    node = next(n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    # node.lineno is the "def" line (decorators excluded): register_function etc. are not needed
    code = textwrap.dedent("\n".join(source.splitlines()[node.lineno - 1:node.end_lineno]))
    code = "from __future__ import annotations\n" + code  # annotations name upstream types we do not load
    ns = {"TAG": "test", "_jarvis": js}
    ns.update(namespace)
    exec(compile(code, "<%s>" % name, "exec"), ns)
    return ns[name]


def _install_helmet_tasks(monkeypatch, **attrs):
    module = types.ModuleType("plugins_func.functions.helmet_tasks")
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, "plugins_func", types.ModuleType("plugins_func"))
    monkeypatch.setitem(sys.modules, "plugins_func.functions", types.ModuleType("plugins_func.functions"))
    monkeypatch.setitem(sys.modules, "plugins_func.functions.helmet_tasks", module)
    return module


def test_hello_announces_server_output_rate(patched):
    root, _, _ = patched

    async def init(_conn):
        return None

    fn = _function(_read(root, "core/handle/helloHandle.py"), "handleHelloMessage",
                   {"json": json, "asyncio": asyncio, "MCPClient": lambda: "mcp", "send_mcp_initialize_message": init})
    conn = _conn(sample_rate=24000, welcome_msg={"type": "hello", "version": 1, "transport": "websocket",
                                                  "audio_params": {"format": "opus", "sample_rate": 24000,
                                                                   "channels": 1, "frame_duration": 60},
                                                  "session_id": "sid-1"})
    device = {"type": "hello", "features": {"mcp": True},
              "audio_params": {"format": "opus", "sample_rate": 16000, "channels": 1, "frame_duration": 60}}
    asyncio.run(fn(conn, device))
    hello = json.loads(conn.websocket.sent[0])
    assert hello["audio_params"] == {"format": "opus", "sample_rate": 24000, "channels": 1, "frame_duration": 60}
    assert device["audio_params"]["sample_rate"] == 16000  # the device's dict is not mutated
    assert conn.mcp_client == "mcp"


def _intent_fn(root, exit_hit=False, wake_hit=False):
    async def check_direct_exit(conn, text):
        return exit_hit

    async def check_wake(conn, text):
        return wake_hit

    async def analyze(conn, text):
        return None

    def remove_punct(text):
        out = re.sub(r"[\s!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~，。！？]", "", text)
        return len(out), out

    return _function(_read(root, "core/handle/intentHandler.py"), "handle_user_intent", {
        "json": json, "uuid": uuid, "check_direct_exit": check_direct_exit, "checkWakeupWords": check_wake,
        "remove_punctuation_and_length": remove_punct, "analyze_intent_with_llm": analyze,
        "process_intent_result": None,
    })


def test_quick_phrase_hook_short_circuits_with_original_text(patched, monkeypatch):
    root, _, _ = patched
    seen = []

    async def try_quick_phrase(conn, text):
        seen.append(text)
        return True

    _install_helmet_tasks(monkeypatch, try_quick_phrase=try_quick_phrase)
    conn = _conn(intent_type="function_call")
    assert asyncio.run(_intent_fn(root)(conn, "Mở mặt nạ, Jarvis!")) is True
    assert seen == ["Mở mặt nạ, Jarvis!"]
    seen.clear()
    wrapped = json.dumps({"speaker": "A", "content": "open mask"}, ensure_ascii=False)
    assert asyncio.run(_intent_fn(root)(_conn(intent_type="function_call"), wrapped)) is True
    assert seen == ["open mask"]


def test_quick_phrase_hook_falls_through_to_llm(patched, monkeypatch):
    root, _, _ = patched

    async def no_match(conn, text):
        return False

    _install_helmet_tasks(monkeypatch, try_quick_phrase=no_match)
    assert asyncio.run(_intent_fn(root)(_conn(intent_type="function_call"), "mặt nạ này nặng không?")) is False


def test_quick_phrase_hook_failure_never_breaks_the_pipeline(patched, monkeypatch):
    root, _, _ = patched

    async def boom(conn, text):
        raise RuntimeError("plugin bug")

    _install_helmet_tasks(monkeypatch, try_quick_phrase=boom)
    conn = _conn(intent_type="function_call")
    assert asyncio.run(_intent_fn(root)(conn, "xin chào")) is False
    assert any(level == "ERROR" and "plugin bug" in msg for level, msg in conn.logger.records)
    monkeypatch.setitem(sys.modules, "plugins_func.functions.helmet_tasks", None)  # import fails
    conn = _conn(intent_type="function_call")
    assert asyncio.run(_intent_fn(root)(conn, "xin chào")) is False
    assert any(level == "ERROR" for level, _ in conn.logger.records)


def test_quick_phrase_hook_runs_after_exit_and_wake_checks(patched, monkeypatch):
    root, _, _ = patched
    calls = []

    async def try_quick_phrase(conn, text):
        calls.append(text)
        return True

    _install_helmet_tasks(monkeypatch, try_quick_phrase=try_quick_phrase)
    assert asyncio.run(_intent_fn(root, exit_hit=True)(_conn(intent_type="function_call"), "tạm biệt")) is True
    assert asyncio.run(_intent_fn(root, wake_hit=True)(_conn(intent_type="function_call"), "Jarvis")) is True
    assert calls == []


class FakeMCPClient(object):
    def __init__(self):
        self.tools, self.name_mapping, self.call_results, self.ready = {}, {}, {}, False
        self.rejected = []

    async def add_tool(self, tool):
        name = re.sub(r"[^a-zA-Z0-9_\-]", "_", tool["name"])
        self.tools[name] = tool
        self.name_mapping[name] = tool["name"]

    async def set_ready(self, value):
        self.ready = value

    async def reject_call_result(self, msg_id, exc):
        self.rejected.append((msg_id, exc))


def _mcp_fn(root, logger):
    async def noop(*_a, **_k):
        return None

    return _function(_read(root, "core/providers/tools/device_mcp/mcp_handler.py"), "handle_mcp_message", {
        "logger": logger, "asyncio": asyncio, "send_mcp_tools_list_request": noop,
        "send_mcp_tools_list_continue_request": noop, "JARVIS_MCP_ERROR_PREFIX": S["mcp_error_prefix"],
    })


TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "result": {"tools": [
    {"name": "self.mask.open", "description": "Mở mặt nạ / Open the mask", "inputSchema": {"type": "object", "properties": {}}}
]}}


def test_mcp_ready_hook_called_once_after_refresh(patched, monkeypatch):
    root, _, _ = patched
    order = []
    _install_helmet_tasks(monkeypatch, on_mcp_ready=lambda conn: order.append(("hook", conn.ready_flag())))
    client = FakeMCPClient()
    handler = types.SimpleNamespace(
        tool_manager=types.SimpleNamespace(refresh_tools=lambda: order.append(("refresh", client.ready))),
        current_support_functions=lambda: None,
    )
    conn = _conn(func_handler=handler, ready_flag=lambda: client.ready)
    asyncio.run(_mcp_fn(root, FakeLogger())(conn, client, TOOLS_LIST))
    assert client.ready is True and "self_mask_open" in client.tools
    assert order == [("refresh", True), ("hook", True)]


def test_mcp_ready_hook_failure_is_swallowed(patched, monkeypatch):
    root, _, _ = patched

    def boom(conn):
        raise RuntimeError("sync failed")

    _install_helmet_tasks(monkeypatch, on_mcp_ready=boom)
    logger = FakeLogger()
    client = FakeMCPClient()
    asyncio.run(_mcp_fn(root, logger)(_conn(func_handler=None), client, TOOLS_LIST))
    assert client.ready is True
    assert any(level == "ERROR" and "sync failed" in msg for level, msg in logger.records)


def test_device_error_reaches_plugins_without_chinese_prefix(patched):
    root, _, _ = patched
    client = FakeMCPClient()
    client.call_results[7] = object()
    error = {"jsonrpc": "2.0", "id": 7, "error": {"code": -32602, "message": "invalid step #2: bad speed"}}
    asyncio.run(_mcp_fn(root, FakeLogger())(_conn(), client, error))
    (msg_id, exc), = client.rejected
    assert msg_id == 7 and str(exc) == S["mcp_error_prefix"] + "invalid step #2: bad speed"


class Action(object):
    ERROR, NOTFOUND, NONE, RESPONSE, REQLLM, RECORD = "ERROR", "NOTFOUND", "NONE", "RESPONSE", "REQLLM", "RECORD"


class ActionResponse(object):
    def __init__(self, action, result=None, response=None):
        self.action, self.result, self.response = action, result, response


def _executor(root, call):
    return _function(_read(root, "core/providers/tools/device_mcp/mcp_executor.py"), "execute",
                     {"Action": Action, "ActionResponse": ActionResponse, "call_mcp_tool": call},
                     cls="DeviceMCPExecutor")


class ReadyClient(object):
    async def is_ready(self):
        return True


def test_device_tool_errors_are_spoken_in_vietnamese(patched):
    root, _, _ = patched

    async def no_tool(conn, client, name, args):
        raise ValueError(S["mcp_tool_not_found"])

    async def device_error(conn, client, name, args):
        raise Exception("invalid step #2: bad speed")

    prefix = S["device_tool_failed"].split("{")[0]
    res = asyncio.run(_executor(root, no_tool)(None, _conn(mcp_client=ReadyClient()), "self_x", {}))
    assert res.action == Action.NOTFOUND and res.response == prefix + S["mcp_tool_not_found"]
    res = asyncio.run(_executor(root, device_error)(None, _conn(mcp_client=ReadyClient()), "self_x", {"a": 1}))
    assert res.action == Action.ERROR and res.response == prefix + "invalid step #2: bad speed"
    res = asyncio.run(_executor(root, device_error)(None, _conn(mcp_client=None), "self_x", {}))
    assert res.action == Action.ERROR and res.response == S["mcp_not_initialized"]


def test_direct_device_tool_call_logs_mcp_call(patched):
    root, _, _ = patched

    async def ok(conn, client, name, args):
        return "opening"

    conn = _conn(mcp_client=ReadyClient())
    res = asyncio.run(_executor(root, ok)(None, conn, "self_mask_open", {"speed": 70}))
    assert res.action == Action.REQLLM and res.result == "opening"
    (line,) = conn.logger.lat()
    assert LAT_LINE_RE.match(line) and " stage=mcp_call " in line
    assert " tool=self_mask_open " in line and " ok=1" in line and " src=llm" in line and " ms=" in line


def test_first_packet_logged_once_per_turn(patched):
    root, _, _ = patched

    async def mqtt(*_a):
        raise AssertionError("not mqtt")

    fn = _function(_read(root, "core/handle/sendAudioHandle.py"), "_do_send_audio",
                   {"time": __import__("time"), "_send_to_mqtt_gateway": mqtt})
    conn = _conn(conn_from_mqtt_gateway=False, sentence_id="turn-1")
    flow = {"packet_count": 0, "sequence": 0, "sentence_id": "turn-1"}
    for _ in range(3):
        asyncio.run(fn(conn, b"\x01\x02", flow))
    flow2 = {"packet_count": 0, "sequence": 0, "sentence_id": "turn-2"}
    asyncio.run(fn(conn, b"\x01", flow2))
    lines = conn.logger.lat()
    assert len(lines) == 2 and all(" stage=first_packet_sent " in l for l in lines)
    assert "turn=turn-1" in lines[0] and "turn=turn-2" in lines[1]
    assert len(conn.websocket.sent) == 4 and flow["packet_count"] == 3


def test_vad_end_logged_before_asr(patched):
    root, _, _ = patched
    order = []

    class Self(object):
        async def handle_voice_stop(self, conn, audio):
            order.append(("asr", len(audio[0])))

    fn = _function(_read(root, "core/providers/asr/base.py"), "receive_audio",
                   {"InterfaceType": types.SimpleNamespace(STREAM="stream")}, cls="ASRProviderBase")
    conn = _conn(client_listen_mode="auto", asr_audio=[b"\x00" * 1920] * 20, client_have_voice=True,
                 client_voice_stop=True, vad_last_voice_time=1759700000123.4,
                 asr=types.SimpleNamespace(interface_type="non_stream"))
    conn.reset_audio_states = lambda: order.append(("reset", None))
    conn.logger.info = lambda msg, *a: order.append(("lat", msg))
    asyncio.run(fn(Self(), conn, b"\x00" * 1920, False))
    assert [k for k, _ in order] == ["lat", "asr", "reset"]
    line = order[0][1]
    assert LAT_LINE_RE.match(line) and " stage=vad_end " in line
    assert " src=vad " in line and " speech_end_ms=1759700000123 " in line and " audio_ms=1260" in line


def test_tts_first_audio_fallback_once_per_turn_and_skips_jarvis_stream(patched):
    root, _, _ = patched
    fn = _function(_read(root, "core/providers/tts/base.py"), "handle_opus",
                   {"logger": FakeLogger(), "SentenceType": types.SimpleNamespace(MIDDLE="MIDDLE")},
                   cls="TTSProviderBase")

    class Provider(object):
        def __init__(self, conn):
            self.conn, self.current_sentence_id, self.queue = conn, "turn-1", []
            self.tts_audio_queue = types.SimpleNamespace(put=self.queue.append)

    conn = _conn(sentence_id="turn-1")
    tts = Provider(conn)
    for _ in range(3):
        fn(tts, b"opus")
    tts.current_sentence_id = "turn-2"
    fn(tts, b"opus")
    lines = conn.logger.lat()
    assert len(lines) == 2 and all(" stage=tts_first_audio " in l for l in lines) and len(tts.queue) == 4
    JarvisStream = type("TTSProvider", (Provider,), {"__module__": "core.providers.tts.jarvis_stream"})
    conn2 = _conn(sentence_id="t")
    fn(JarvisStream(conn2), b"opus")
    assert conn2.logger.lat() == []  # jarvis_stream logs its own line at the first PCM chunk


def test_exit_intent_defaults_to_vietnamese_goodbye(patched):
    root, _, _ = patched
    fn = _function(_read(root, "plugins_func/functions/handle_exit_intent.py"), "handle_exit_intent",
                   {"logger": FakeLogger(), "Action": Action, "ActionResponse": ActionResponse})
    for value in (None, ""):
        res = fn(types.SimpleNamespace(close_after_chat=False), value)
        assert res.response == S["handle_exit_intent"]["default_goodbye"] and res.result == S["handle_exit_intent"]["result"]
    res = fn(types.SimpleNamespace(close_after_chat=False), "Goodbye, Sir.")
    assert res.response == "Goodbye, Sir." and res.action == Action.RESPONSE


def _strip_punct(text):
    out = re.sub(r"[\s!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~，。！？]", "", text)
    return len(out), out


def _listen_handle(root, events):
    async def send_stt_message(conn, text):
        events.append(("stt", text))

    async def send_tts_message(conn, state, text=None):
        events.append(("tts", state))

    async def start_to_chat(conn, text):
        events.append(("chat", text))

    return _function(_read(root, "core/handle/textHandler/listenMessageHandler.py"), "handle", {
        "time": __import__("time"), "uuid": uuid, "asyncio": asyncio, "send_stt_message": send_stt_message,
        "send_tts_message": send_tts_message, "startToChat": start_to_chat,
        "enqueue_asr_report": lambda conn, text, audio: events.append(("report", text)),
        "remove_punctuation_and_length": _strip_punct, "InterfaceType": types.SimpleNamespace(STREAM="stream"),
        "Message": None, "TTSMessageDTO": None, "SentenceType": None, "ContentType": None,
    }, cls="ListenTextMessageHandler")


def test_wake_word_without_greeting_sends_nothing(patched):
    # D15: a stt / tts start / tts stop echo would flip the helmet to Speaking and back (mic off, audio
    # buffer reset by the second listen start) and cut the start of "Jarvis, mở mặt nạ".
    root, _, _ = patched
    events = []
    fn = _listen_handle(root, events)
    conn = _conn(config={"wakeup_words": ["Jarvis", "Quảngia"], "enable_greeting": False},
                 client_is_speaking=True, client_have_voice=True, client_listen_mode="auto")
    resets = []
    conn.reset_audio_states = lambda: resets.append(1)
    asyncio.run(fn(None, conn, {"type": "listen", "state": "detect", "text": "Jarvis"}))
    assert events == [] and conn.websocket.sent == []
    assert conn.client_is_speaking is False and conn.client_have_voice is False and resets == [1]
    asyncio.run(fn(None, conn, {"type": "listen", "state": "detect", "text": "Quản gia!"}))
    assert events == [] and conn.websocket.sent == []
    asyncio.run(fn(None, conn, {"type": "listen", "state": "detect", "text": "mở mặt nạ"}))
    assert events == [("report", "mở mặt nạ"), ("chat", "mở mặt nạ")]  # not a wake word: LLM as before


class _AsrResponse(object):
    status_code = 200
    text = '{"text": "mở mặt nạ"}'

    def json(self):
        return {"text": "mở mặt nạ"}


def _openai_asr(root, post, tmp_path, **config):
    source = _read(root, "core/providers/asr/openai.py")
    init = _function(source, "__init__", {"InterfaceType": types.SimpleNamespace(NON_STREAM="non_stream"),
                                          "os": os}, cls="ASRProvider")
    stt = _function(source, "speech_to_text", {"requests": types.SimpleNamespace(post=post), "asyncio": asyncio,
                                                "time": __import__("time"), "logger": FakeLogger()},
                    cls="ASRProvider")
    provider = types.SimpleNamespace()
    cfg = {"api_key": "none", "base_url": "http://asr:9000/v1/audio/transcriptions", "model_name": "whisper",
           "output_dir": str(tmp_path / "asr")}
    cfg.update(config)
    init(provider, cfg, True)
    wav = tmp_path / "utt.wav"
    wav.write_bytes(b"RIFF0000WAVE")
    return provider, stt, types.SimpleNamespace(file_path=str(wav))


def test_openai_asr_request_runs_off_the_event_loop(patched, tmp_path):
    import time as _time

    root, _, _ = patched
    calls = []

    def slow_post(url, **kwargs):
        calls.append(dict(kwargs, t0=_time.monotonic()))
        _time.sleep(0.4)
        calls[-1]["t1"] = _time.monotonic()
        return _AsrResponse()

    provider, stt, artifacts = _openai_asr(root, slow_post, tmp_path, timeout_s=12)
    ticks = []

    async def main():
        async def ticker():
            for _ in range(60):
                ticks.append(_time.monotonic())
                await asyncio.sleep(0.01)

        task = asyncio.ensure_future(ticker())
        result = await stt(provider, [], "sid-1", artifacts)
        task.cancel()
        return result

    assert asyncio.run(main()) == ("mở mặt nạ", artifacts.file_path)
    assert calls[0]["timeout"] == (3.0, 12.0)
    during = [t for t in ticks if calls[0]["t0"] < t < calls[0]["t1"]]
    assert len(during) >= 10, "the event loop was blocked while the ASR request was in flight"
    provider, _, _ = _openai_asr(root, slow_post, tmp_path)  # no timeout_s -> 30 s read timeout
    assert provider.jarvis_timeout == (3.0, 30.0)


def test_openai_asr_hanging_service_returns_empty_text(patched, tmp_path):
    import socket
    import threading
    import time as _time

    requests = pytest.importorskip("requests")
    root, _, _ = patched
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    accepted = []

    def accept():  # accept and never answer: a stuck ASR service holding the socket
        try:
            while True:
                accepted.append(server.accept()[0])
        except OSError:
            pass

    threading.Thread(target=accept, daemon=True).start()
    try:
        provider, stt, artifacts = _openai_asr(root, requests.post, tmp_path, timeout_s=0.5,
                                               base_url="http://127.0.0.1:%d/v1/audio/transcriptions"
                                               % server.getsockname()[1])
        started = _time.monotonic()
        assert asyncio.run(stt(provider, [], "sid-1", artifacts)) == ("", None)
        assert _time.monotonic() - started < 5
    finally:
        server.close()
        for sock in accepted:
            sock.close()


def test_websocket_auth_failure_text_is_vietnamese(patched):
    root, _, _ = patched
    source = _read(root, "core/websocket_server.py")
    assert "认证失败" not in source
    assert "await websocket.send(%s)" % json.dumps(S["ws_auth_failed"], ensure_ascii=False) in source


def test_markdown_table_is_spoken_in_vietnamese(patched):
    root, _, _ = patched
    fn = _function(_read(root, "core/utils/tts.py"), "_replace_table_block", {"re": re}, cls="MarkdownCleaner")
    table = "| Tên | Giá |\n|---|---|\n| A | 1 |\n| B | 2 |\n"
    out = fn(types.SimpleNamespace(group=lambda name: table))
    assert out.startswith(S["md_table_header"]) and S["md_table_row"].format(i=2) in out
    single = fn(types.SimpleNamespace(group=lambda name: "| A | B |\n"))
    assert single.startswith(S["md_table_single"])


# ----------------------------------------------------------------------------- jarvis_support


def test_lat_line_format_matches_contract():
    line = js.format_line("3f2a-uuid", "asr_done", 1759700000123, text_len=12, note="two words", flag=True, skip=None)
    assert line == "[LAT] sid=3f2a-uuid stage=asr_done t_ms=1759700000123 text_len=12 note=two_words flag=1"
    assert LAT_LINE_RE.match(line)
    assert js.format_line("", "x", 1) == "[LAT] sid=- stage=x t_ms=1"


def test_lat_uses_connection_logger_and_never_raises():
    conn = _conn()
    js.lat(conn, "vad_end", src="vad")
    (line,) = conn.logger.lat()
    assert LAT_LINE_RE.match(line) and "sid=sid-1" in line

    class Broken(object):
        session_id = "s"

        @property
        def logger(self):
            raise RuntimeError("boom")

    js.lat(Broken(), "vad_end")  # swallowed
    assert js.safe_len(None) == 0 and js.safe_len(object()) == 0 and js.as_int("x") == 0


def test_llm_first_token_detection():
    conn = _conn()
    assert js.llm_first_token(conn, ("", None)) is False
    assert js.llm_first_token(conn, (None, [])) is False
    assert js.llm_first_token(conn, ("Vâng", None), 0, "turn") is True
    assert js.llm_first_token(conn, (None, [object()]), 1, "turn") is True
    lines = conn.logger.lat()
    assert "kind=content" in lines[0] and "kind=tool" in lines[1] and "depth=1" in lines[1]


def test_redact_headers():
    headers = {"Authorization": "Bearer abc.123", "cf-access-client-id": "id.access", "CF-Access-Client-Secret": "s3",
               "Cf-Access-Jwt-Assertion": "jwt", "Cookie": "CF_Authorization=x", "device-id": "aa:bb", "client-id": "c"}
    out = js.redact_headers(headers)
    assert out["device-id"] == "aa:bb" and out["client-id"] == "c"
    for key in ("Authorization", "cf-access-client-id", "CF-Access-Client-Secret", "Cf-Access-Jwt-Assertion", "Cookie"):
        assert out[key] == "***"
    assert "abc.123" not in str(out) and "s3" not in str(out)
    assert js.redact_headers(None) == "<redacted>"


# ----------------------------------------------------------------------------- docs


def test_readme_lists_every_patch():
    with open(README, "r", encoding="utf-8") as fh:
        text = fh.read()
    for e in EDITS:
        assert "`%s`" % e.id in text, e.id
