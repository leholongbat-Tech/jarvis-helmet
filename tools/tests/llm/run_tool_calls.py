#!/usr/bin/env python3
"""Tool-calling test for the Jarvis LLM stack (SV3 acceptance #2).

Builds exactly the chat request xiaozhi-server v0.9.7 (with the Jarvis patches) sends to
LiteLLM in `Intent: function_call` mode, sends every utterance of tool_calls.yaml and scores
the tool name + arguments of the first LLM response.

Request = what ConnectionHandler.chat() sends at depth 0:
  messages: system prompt (server/xiaozhi/prompts/jarvis-prompt.txt rendered with Jinja2 like
            PromptManager.build_enhanced_prompt, {{current_time}} -> HH:MM) + the temporary
            few-shot messages (strings from server/xiaozhi/patches/strings_vi.json, else the
            upstream Chinese ones) + the user utterance
  tools:    server plugins (handle_exit_intent, get_lunar, task_* from server/tasks/tasks.yaml,
            ask_expert) + device MCP tools (device_tools.json, names sanitized self.x.y ->
            self_x_y) + the virtual direct_answer tool (last)
  params:   stream=true, max_tokens/temperature from the xiaozhi config template, no tool_choice
Scoring: a direct_answer call counts as "no tool". Exit code 0 = targets met, 1 = below target,
2 = infrastructure error (LiteLLM unreachable, every request failed, bad input files).

Usage:
  python tools/tests/llm/run_tool_calls.py --env-file server/.env
  python tools/tests/llm/run_tool_calls.py --base-url http://127.0.0.1:4000/v1 --api-key sk-... \
      --only open_vi,multi_e3 --repeat 3 --json-out results.json
  python tools/tests/llm/run_tool_calls.py --dry-run --dump-request request.json
"""

from __future__ import annotations

import argparse
import ast
import copy
import datetime as dt
import json
import os
import random
import re
import statistics
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

try:  # optional: only needed for real runs
    import httpx
except ImportError:  # pragma: no cover
    httpx = None

try:  # optional: xiaozhi renders the prompt with Jinja2
    import jinja2
except ImportError:  # pragma: no cover
    jinja2 = None

try:  # optional: validate call arguments against the offered tool schema (device rejects bad ranges)
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SERVER = REPO / "server"

DEFAULTS = {
    "cases": HERE / "tool_calls.yaml",
    "device_tools": HERE / "device_tools.json",
    "prompt": SERVER / "xiaozhi" / "prompts" / "jarvis-prompt.txt",
    "strings": SERVER / "xiaozhi" / "patches" / "strings_vi.json",
    "config_template": SERVER / "xiaozhi" / "data" / ".config.yaml.tmpl",
    "tasks": SERVER / "tasks" / "tasks.yaml",
    "ask_expert_plugin": SERVER / "xiaozhi" / "plugins" / "ask_expert.py",
}

# ---------------------------------------------------------------------------------------------
# Upstream xiaozhi-esp32-server v0.9.7 content (used when the Jarvis files are absent)
# ---------------------------------------------------------------------------------------------
UPSTREAM_DIRECT_ANSWER_DESC = "当用户的请求不匹配其他任何工具时，可用此选项直接回复。将回复内容写在response参数里。"
UPSTREAM_DIRECT_ANSWER_PARAM = "你回复用户的完整内容"
UPSTREAM_FEWSHOT = {
    "direct_user": "给我讲个故事吧",
    "direct_response": "好呀，你想听什么类型的呀？童话、冒险还是搞笑的？选一个我给你开讲~",
    "direct_tool_result": "已直接回复",
    "exit_user": "拜拜",
    "exit_goodbye": "再见，下次再聊~",
    "exit_tool_result": "退出意图已处理",
}
UPSTREAM_EXIT = {
    "description": "当用户想结束对话或需要退出系统时调用",
    "say_goodbye_description": "和用户友好结束对话的告别语",
}
UPSTREAM_LUNAR = {
    "description": (
        "用于具体日期的阴历/农历和黄历信息。"
        "用户可以指定查询内容，如：阴历日期、天干地支、节气、生肖、星座、八字、宜忌等。"
        "如果没有指定查询内容，则默认查询干支年和农历日期。"
        "对于'今天农历是多少'、'今天农历日期'这样的基本查询，请直接使用context中的信息，不要调用此工具。"
    ),
    "date_description": "要查询的日期，格式为YYYY-MM-DD，例如2024-01-01。如果不提供，则使用当前日期",
    "query_description": "要查询的内容，例如阴历日期、天干地支、节日、节气、生肖、星座、八字、宜忌等",
}
UPSTREAM_WEEKDAYS = {
    "Monday": "星期一", "Tuesday": "星期二", "Wednesday": "星期三", "Thursday": "星期四",
    "Friday": "星期五", "Saturday": "星期六", "Sunday": "星期日",
}
EMOJI_LIST = ["😶", "🙂", "😆", "😂", "😔", "😠", "😭", "😍", "😳", "😲", "😱", "🤔", "😉", "😎",
              "😌", "🤤", "😘", "😏", "😴", "😜", "🙄"]
NECESSARY_FUNCTIONS = ["handle_exit_intent", "get_lunar"]
DEFAULT_FUNCTIONS = ["helmet_tasks", "ask_expert"]
DEFAULT_PERSONA = (
    "Bạn là J.A.R.V.I.S, trợ lý AI tích hợp trong mũ giáp kiểu Iron Man của ngài chủ nhân. "
    "Phong cách quản gia người Anh lịch lãm, trả lời ngắn, đúng ngôn ngữ người dùng."
)
DEFAULT_ASK_EXPERT = {
    "type": "function",
    "function": {
        "name": "ask_expert",
        "description": (
            "Hỏi chuyên gia (model cloud mạnh hơn) khi câu hỏi cần kiến thức chuyên sâu hoặc thông tin "
            "mới, khi không chắc chắn, hoặc khi người dùng nói \"hỏi chuyên gia\". / Ask the expert (a "
            "stronger cloud model) for in-depth or up-to-date knowledge, when unsure, or when the user "
            "says \"ask the expert\"."
        ),
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string",
                                        "description": "Câu hỏi đầy đủ / The full question"}},
            "required": ["question"],
        },
    },
}
ARG_SPEC_KEYS = {"min", "max", "contains", "contains_any", "one_of"}


# ---------------------------------------------------------------------------------------------
# Helpers shared with xiaozhi (mirrors of upstream behaviour)
# ---------------------------------------------------------------------------------------------
def sanitize_tool_name(name: str) -> str:
    """core/utils/util.py sanitize_tool_name (v0.9.7)."""
    return re.sub(r"[^a-zA-Z0-9_\-一-鿿]", "_", name)


def extract_json_from_string(text: str) -> Optional[str]:
    """core/utils/util.py extract_json_from_string (v0.9.7)."""
    match = re.search(r"(\{.*\})", text, re.DOTALL)
    return match.group(1) if match else None


def merge_tool_calls(calls: List[Dict[str, str]], deltas: Iterable[Dict[str, Any]]) -> None:
    """ConnectionHandler._merge_tool_calls on raw JSON deltas (raises IndexError like upstream)."""
    for delta in deltas:
        fn = delta.get("function") or {}
        index = delta.get("index")
        if index is None:
            if fn.get("name"):
                index = len(calls)
            else:
                index = len(calls) - 1 if calls else 0
        if index >= len(calls):
            calls.append({"id": "", "name": "", "arguments": ""})
        if delta.get("id"):
            calls[index]["id"] = delta["id"]
        if fn.get("name"):
            calls[index]["name"] = fn["name"]
        if fn.get("arguments"):
            calls[index]["arguments"] += fn["arguments"]


def device_functions(tools: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """MCP tools/list -> LLM functions, like mcp_handler.handle_mcp_message + MCPClient."""
    store: Dict[str, Dict[str, Any]] = {}
    mapping: Dict[str, str] = {}
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name", "")
        schema = {"type": "object", "properties": {}, "required": []}
        raw = tool.get("inputSchema")
        if isinstance(raw, dict):
            schema["type"] = raw.get("type", "object")
            schema["properties"] = raw.get("properties", {})
            schema["required"] = [s for s in raw.get("required", []) if isinstance(s, str)]
        sanitized = sanitize_tool_name(name)
        store[sanitized] = {"name": name, "description": tool.get("description", ""), "inputSchema": schema}
        mapping[sanitized] = name
    for data in store.values():  # original names inside descriptions -> sanitized names
        text = data["description"]
        for sanitized, original in mapping.items():
            text = text.replace(original, sanitized)
        data["description"] = text
    return [
        {"type": "function", "function": {
            "name": sanitized,
            "description": data["description"],
            "parameters": {
                "type": data["inputSchema"].get("type", "object"),
                "properties": data["inputSchema"].get("properties", {}),
                "required": data["inputSchema"].get("required", []),
            }}}
        for sanitized, data in store.items()
    ]


# ---------------------------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------------------------
@dataclass
class Sources:
    notes: List[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        self.notes.append(text)


def read_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_config_template(path: Path, src: Sources) -> Dict[str, Any]:
    """Parse the xiaozhi config template (string.Template placeholders replaced by dummies)."""
    if not path.is_file():
        src.note(f"config template: {path} not found -> defaults (functions={DEFAULT_FUNCTIONS})")
        return {}
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", r"placeholder-\1", text).replace("$$", "$")
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        src.note(f"config template: {path} unreadable ({exc}) -> defaults")
        return {}
    src.note(f"config template: {path}")
    return data if isinstance(data, dict) else {}


def load_strings(path: Optional[Path], src: Sources) -> Dict[str, Any]:
    if path is None:
        src.note("strings: upstream (Chinese) direct_answer/few-shot texts (--upstream-strings)")
        return {}
    if not path.is_file():
        src.note(f"strings: {path} not found -> upstream (Chinese) texts")
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    src.note(f"strings: {path}")
    return data if isinstance(data, dict) else {}


def pick(data: Dict[str, Any], dotted: str, default: str) -> str:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node if isinstance(node, str) and node else default


def load_tasks(path: Path, src: Sources) -> Tuple[List[Tuple[str, str]], Any]:
    """Return [(task_id, description)] in file order and the taskslib table (or None)."""
    table = None
    tasks_dir = path.parent
    if str(tasks_dir) not in sys.path:
        sys.path.insert(0, str(tasks_dir))
    try:
        import taskslib  # type: ignore  # server/tasks/taskslib.py (pure Python)
        table = taskslib.load_tasks(str(path))
        items = [(tid, t.description) for tid, t in table.tasks.items()]
        src.note(f"tasks: {path} via taskslib ({len(items)} tasks)")
        return items, table
    except Exception as exc:  # missing file/lib or invalid tasks -> plain YAML
        if not path.is_file():
            src.note(f"tasks: {path} not found -> no task_* tools")
            return [], None
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        items = [(str(tid), str((t or {}).get("description", "")))
                 for tid, t in (data.get("tasks") or {}).items()]
        src.note(f"tasks: {path} via PyYAML ({len(items)} tasks; taskslib unavailable: {exc!r})")
        return items, None


def load_plugin_descriptor(path: Path, func_name: str) -> Optional[Dict[str, Any]]:
    """Find a literal `{"type": "function", "function": {"name": func_name, ...}}` in a plugin."""
    if not path.is_file():
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        value_node = getattr(node, "value", None)
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(value_node, ast.Dict):
            try:
                value = ast.literal_eval(value_node)
            except (ValueError, SyntaxError, TypeError):
                continue
            fn = value.get("function") if isinstance(value, dict) else None
            if isinstance(fn, dict) and fn.get("name") == func_name:
                return value
    return None


def load_device_tools(path: Path, src: Sources) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "result" in data:  # raw JSON-RPC response
        data = data["result"]
    tools = data.get("tools") if isinstance(data, dict) else data
    if not isinstance(tools, list):
        raise ValueError(f"{path}: expected a tools/list result with a 'tools' array")
    src.note(f"device tools: {path} ({len(tools)} tools)")
    return tools


# ---------------------------------------------------------------------------------------------
# Request builder
# ---------------------------------------------------------------------------------------------
@dataclass
class RequestContext:
    system_prompt_template: str          # rendered once, {{current_time}} still literal
    fewshot: List[Dict[str, Any]]
    tools: List[Dict[str, Any]]
    params: Dict[str, Any]
    model: str
    quick_table: Any = None
    quick_threshold: float = 90.0
    wake_words: Optional[List[str]] = None


def render_prompt(template: str, base_prompt: str, now: dt.datetime, src: Sources) -> str:
    variables = {
        "base_prompt": base_prompt,
        "current_time": "{{current_time}}",
        "today_date": now.strftime("%Y-%m-%d"),
        "today_weekday": UPSTREAM_WEEKDAYS[now.strftime("%A")],
        "lunar_date": "",
        "local_address": "",
        "weather_info": "",
        "emojiList": EMOJI_LIST,
        "device_id": "aa:bb:cc:dd:ee:ff",
        "client_ip": "192.168.1.50",
        "dynamic_context": "",
        "language": "中文",
        "emoji_enabled": True,   # firmware v2.5.0 does not send features.emoji -> True
    }
    if jinja2 is not None:
        return jinja2.Template(template).render(**variables)
    src.note("prompt: jinja2 missing -> approximate rendering (pip install jinja2==3.1.6)")
    text = re.sub(r"{%-?.*?-?%}", "", template, flags=re.DOTALL)
    return re.sub(r"{{\s*(\w+)\s*}}", lambda m: str(variables.get(m.group(1), "")), text)


def fewshot_messages(strings: Dict[str, Any], has_exit: bool) -> List[Dict[str, Any]]:
    """_inject_tool_call_fewshot + Dialogue.getMessages + normalize_dialogue (content="")."""
    fs = {k: pick(strings, f"fewshot.{k}", v) for k, v in UPSTREAM_FEWSHOT.items()}
    msgs: List[Dict[str, Any]] = [
        {"role": "user", "content": fs["direct_user"]},
        {"role": "assistant", "tool_calls": [{
            "id": "fewshot_da_001",
            "function": {"arguments": json.dumps({"response": fs["direct_response"]}, ensure_ascii=False),
                         "name": "direct_answer"},
            "type": "function", "index": 0}], "content": ""},
        {"role": "tool", "tool_call_id": "fewshot_da_001", "content": fs["direct_tool_result"]},
    ]
    if has_exit:
        msgs += [
            {"role": "user", "content": fs["exit_user"]},
            {"role": "assistant", "tool_calls": [{
                "id": "fewshot_exit_001",
                "function": {"arguments": json.dumps({"say_goodbye": fs["exit_goodbye"]}, ensure_ascii=False),
                             "name": "handle_exit_intent"},
                "type": "function", "index": 0}], "content": ""},
            {"role": "tool", "tool_call_id": "fewshot_exit_001", "content": fs["exit_tool_result"]},
            {"role": "assistant", "content": fs["exit_goodbye"]},
        ]
    return msgs


def plugin_functions(cfg: Dict[str, Any], strings: Dict[str, Any], tasks: List[Tuple[str, str]],
                     ask_expert: Dict[str, Any], src: Sources) -> List[Dict[str, Any]]:
    """ServerPluginExecutor.get_tools(): necessary + configured functions, config description overrides."""
    plugins_cfg = cfg.get("plugins") if isinstance(cfg.get("plugins"), dict) else {}
    intent_cfg = (cfg.get("Intent") or {}).get((cfg.get("selected_module") or {}).get("Intent", ""), {}) or {}
    configured = intent_cfg.get("functions") if cfg else None
    if not isinstance(configured, list):
        configured = DEFAULT_FUNCTIONS
    registry: Dict[str, Dict[str, Any]] = {}
    module_of: Dict[str, str] = {}

    exit_desc = {"type": "function", "function": {
        "name": "handle_exit_intent",
        "description": pick(strings, "handle_exit_intent.description", UPSTREAM_EXIT["description"]),
        "parameters": {"type": "object", "properties": {"say_goodbye": {
            "type": "string",
            "description": pick(strings, "handle_exit_intent.say_goodbye_description",
                                UPSTREAM_EXIT["say_goodbye_description"])}},
            "required": ["say_goodbye"]}}}
    lunar_desc = {"type": "function", "function": {
        "name": "get_lunar",
        "description": pick(strings, "get_lunar.description", UPSTREAM_LUNAR["description"]),
        "parameters": {"type": "object", "properties": {
            "date": {"type": "string", "description": pick(strings, "get_lunar.date_description",
                                                           UPSTREAM_LUNAR["date_description"])},
            "query": {"type": "string", "description": pick(strings, "get_lunar.query_description",
                                                            UPSTREAM_LUNAR["query_description"])}},
            "required": []}}}
    registry["handle_exit_intent"], module_of["handle_exit_intent"] = exit_desc, "handle_exit_intent"
    registry["get_lunar"], module_of["get_lunar"] = lunar_desc, "get_time"
    task_names = []
    for tid, desc in tasks:
        name = f"task_{tid}"
        registry[name] = {"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": {}, "required": []}}}
        module_of[name] = "helmet_tasks"
        task_names.append(name)
    registry["ask_expert"], module_of["ask_expert"] = copy.deepcopy(ask_expert), "ask_expert"
    module_map = {"helmet_tasks": task_names, "ask_expert": ["ask_expert"],
                  "handle_exit_intent": ["handle_exit_intent"], "get_time": ["get_lunar"]}

    expanded: List[str] = []
    for name in configured:  # _expand_plugin_names
        if name in module_map:
            expanded.extend(module_map[name])
        else:
            expanded.append(name)
    ordered: List[str] = []
    for name in NECESSARY_FUNCTIONS + expanded:
        if name not in ordered:
            ordered.append(name)
    functions = []
    for name in ordered:
        item = registry.get(name)
        if item is None:
            src.note(f"function {name!r} from the config is unknown to this runner -> skipped")
            continue
        item = copy.deepcopy(item)
        override = ""
        if name in plugins_cfg and isinstance(plugins_cfg[name], dict):  # _get_plugin_description
            override = plugins_cfg[name].get("description", "") or ""
        elif isinstance(plugins_cfg.get(module_of[name]), dict):
            override = plugins_cfg[module_of[name]].get("description", "") or ""
        if override:
            item["function"]["description"] = override
        functions.append(item)
    return functions


def direct_answer_tool(strings: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "function", "function": {
        "name": "direct_answer",
        "description": pick(strings, "direct_answer.description", UPSTREAM_DIRECT_ANSWER_DESC),
        "parameters": {"type": "object", "properties": {"response": {
            "type": "string",
            "description": pick(strings, "direct_answer.response_description", UPSTREAM_DIRECT_ANSWER_PARAM)}},
            "required": ["response"]}}}


def llm_params(cfg: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """Optional params of LLMProvider.response_with_functions (None values are omitted)."""
    llm_cfg = (cfg.get("LLM") or {}).get((cfg.get("selected_module") or {}).get("LLM", ""), {}) or {}
    params: Dict[str, Any] = {}
    for key, conv in (("max_tokens", int), ("temperature", float), ("top_p", float),
                      ("frequency_penalty", float)):
        value = llm_cfg.get(key, settings.get(key))
        if value in (None, ""):
            continue
        try:
            value = conv(value)
        except (TypeError, ValueError):
            continue
        params[key] = round(value, 1) if conv is float else value
    return params


def build_context(args: argparse.Namespace, settings: Dict[str, Any], src: Sources) -> RequestContext:
    cfg = load_config_template(Path(args.config_template), src)
    strings = load_strings(None if args.upstream_strings else Path(args.strings), src)
    tasks, table = load_tasks(Path(args.tasks), src)
    ask = load_plugin_descriptor(Path(args.ask_expert_plugin), "ask_expert")
    if ask is None:
        src.note(f"ask_expert: no literal descriptor in {args.ask_expert_plugin} -> built-in default")
        ask = DEFAULT_ASK_EXPERT
    else:
        src.note(f"ask_expert: descriptor from {args.ask_expert_plugin}")
    plugins = plugin_functions(cfg, strings, tasks, ask, src)
    if args.shuffle_plugins is not None:  # xiaozhi orders plugins via list(set(...)): per-process
        random.Random(args.shuffle_plugins).shuffle(plugins)
    device = device_functions(load_device_tools(Path(args.device_tools), src))
    tools = plugins + device + [direct_answer_tool(strings)]

    base_prompt = cfg.get("prompt") if isinstance(cfg.get("prompt"), str) else DEFAULT_PERSONA
    prompt_path = Path(args.prompt)
    if prompt_path.is_file():
        rendered = render_prompt(prompt_path.read_text(encoding="utf-8"), base_prompt, dt.datetime.now(), src)
        src.note(f"prompt: {prompt_path}")
    else:  # PromptManager falls back to the raw persona when the template file is missing
        rendered = base_prompt
        src.note(f"prompt: {prompt_path} not found -> raw persona only")
    threshold = float(((cfg.get("plugins") or {}).get("helmet_tasks") or {}).get("quick_phrase_threshold", 90))
    wake = cfg.get("wakeup_words") if isinstance(cfg.get("wakeup_words"), list) else None
    return RequestContext(
        system_prompt_template=rendered,
        fewshot=fewshot_messages(strings, has_exit=any(t["function"]["name"] == "handle_exit_intent" for t in tools)),
        tools=tools, params=llm_params(cfg, settings), model=args.model or settings.get("model", "jarvis-main"),
        quick_table=table, quick_threshold=threshold, wake_words=wake)


def build_request(ctx: RequestContext, text: str, now: Optional[dt.datetime] = None,
                  extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now = now or dt.datetime.now()
    system = ctx.system_prompt_template.replace("{{current_time}}", now.strftime("%H:%M"))
    system = re.sub(r"<memory>.*?</memory>", "<memory>\n\n</memory>", system, flags=re.DOTALL)  # nomem
    messages = [{"role": "system", "content": system}] + copy.deepcopy(ctx.fewshot) + \
        [{"role": "user", "content": text}]
    body: Dict[str, Any] = {"model": ctx.model, "messages": messages, "stream": True, "tools": ctx.tools}
    body.update(ctx.params)
    if extra:
        body.update(extra)
    return body


# ---------------------------------------------------------------------------------------------
# Calling LiteLLM
# ---------------------------------------------------------------------------------------------
@dataclass
class Reply:
    content: str = ""
    calls: List[Dict[str, str]] = field(default_factory=list)
    ttft_s: Optional[float] = None
    total_s: Optional[float] = None
    status: int = 0
    error: Optional[str] = None
    model_group: Optional[str] = None
    fallbacks: int = 0


def parse_stream(lines: Iterable[str], reply: Reply, t0: float) -> None:
    for line in lines:
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(chunk, dict) and chunk.get("error"):
            reply.error = json.dumps(chunk["error"], ensure_ascii=False)[:300]
            break
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            content = delta.get("content") or ""
            deltas = delta.get("tool_calls") or []
            if (content or deltas) and reply.ttft_s is None:
                reply.ttft_s = time.monotonic() - t0
            reply.content += content
            if deltas:
                merge_tool_calls(reply.calls, deltas)


def call_llm(client: Any, base_url: str, api_key: str, body: Dict[str, Any], timeout: float) -> Reply:
    reply = Reply()
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    t0 = time.monotonic()
    try:
        with client.stream("POST", base_url.rstrip("/") + "/chat/completions", headers=headers,
                           content=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                           timeout=timeout) as resp:
            reply.status = resp.status_code
            reply.model_group = resp.headers.get("x-litellm-model-group")
            try:
                reply.fallbacks = int(resp.headers.get("x-litellm-attempted-fallbacks", "0") or 0)
            except ValueError:
                reply.fallbacks = 0
            if resp.status_code != 200:
                reply.error = f"HTTP {resp.status_code}: {resp.read().decode('utf-8', 'replace')[:300]}"
            else:
                parse_stream(resp.iter_lines(), reply, t0)
    except IndexError as exc:
        reply.error = f"tool-call delta merge failed like xiaozhi would: {exc}"
    except Exception as exc:  # connection refused, timeout ...
        reply.error = f"{type(exc).__name__}: {exc}"
    reply.total_s = time.monotonic() - t0
    return reply


def is_retryable(reply: Reply) -> bool:
    """Transient proxy/backend errors: timeouts, cooldown 429, 5xx, connection errors."""
    if not reply.error or "crossed budget" in reply.error:
        return False
    return reply.status in (0, 408, 429, 500, 502, 503, 504)


def call_with_retries(client: Any, base_url: str, api_key: str, body: Dict[str, Any], timeout: float,
                      retries: int, log: Any = print) -> Reply:
    reply = call_llm(client, base_url, api_key, body, timeout)
    for _ in range(max(0, retries)):
        if not is_retryable(reply):
            break
        # LiteLLM cools a failing deployment down for cooldown_time (30 s) before retrying it.
        wait = 31.0 if reply.status == 429 and "cooldown" in (reply.error or "") else 3.0
        log(f"  [retry in {wait:.0f}s] {trunc(reply.error or '', 100)}")
        time.sleep(wait)
        reply = call_llm(client, base_url, api_key, body, timeout)
    return reply


def interpret(reply: Reply) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[str]]:
    """Return (real_calls, direct_answer_calls, error) like ConnectionHandler.chat() would see them."""
    calls = list(reply.calls)
    if not calls and reply.content.startswith("<tool_call>"):  # Qwen text tool-call fallback
        blob = extract_json_from_string(reply.content)
        if blob is None:
            return [], [], "text tool call without JSON"
        try:
            obj = json.loads(blob)
            calls = [{"id": "", "name": obj["name"],
                      "arguments": json.dumps(obj.get("arguments", {}), ensure_ascii=False)}]
        except (ValueError, KeyError, TypeError) as exc:
            return [], [], f"text tool call unparsable: {exc}"
    parsed = []
    for c in calls:
        raw = c.get("arguments") or ""
        try:
            args = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            args = None
        parsed.append({"name": c.get("name", ""), "args": args, "raw": raw})
    real = [c for c in parsed if c["name"] != "direct_answer"]
    direct = [c for c in parsed if c["name"] == "direct_answer"]
    return real, direct, None


# ---------------------------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------------------------
def _norm_text(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def match_value(spec: Any, actual: Any) -> bool:
    if isinstance(spec, dict) and spec and set(spec) <= ARG_SPEC_KEYS:
        if "one_of" in spec and not any(match_value(v, actual) for v in spec["one_of"]):
            return False
        if "min" in spec or "max" in spec:
            if isinstance(actual, bool) or not isinstance(actual, (int, float)):
                return False
            if "min" in spec and actual < spec["min"]:
                return False
            if "max" in spec and actual > spec["max"]:
                return False
        if "contains" in spec:
            if not isinstance(actual, str) or _norm_text(spec["contains"]) not in _norm_text(actual):
                return False
        if "contains_any" in spec:
            if not isinstance(actual, str) or not any(_norm_text(s) in _norm_text(actual)
                                                      for s in spec["contains_any"]):
                return False
        return True
    if isinstance(spec, bool):
        return isinstance(actual, bool) and actual is spec
    if isinstance(spec, (int, float)):
        return not isinstance(actual, bool) and isinstance(actual, (int, float)) and actual == spec
    return actual == spec


def match_option(option: Dict[str, Any], call: Dict[str, Any]) -> bool:
    if call["name"] != option["tool"]:
        return False
    args = call["args"]
    if not isinstance(args, dict):
        return False
    for key, spec in (option.get("args") or {}).items():
        if key not in args or not match_value(spec, args[key]):
            return False
    groups = option.get("args_any")
    if groups:
        return any(all(k in args and match_value(v, args[k]) for k, v in g.items()) for g in groups)
    return True


def options_of(expected_call: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(expected_call["one_of"]) if "one_of" in expected_call else [expected_call]


def describe_call(call: Dict[str, Any]) -> str:
    args = call["args"]
    if args is None:
        return f"{call['name']}(<bad json: {call['raw'][:30]}>)"
    if not args:
        return call["name"]
    inner = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items())
    return f"{call['name']}({inner})"


def describe_expected(case: Dict[str, Any]) -> str:
    if case["kind"] == "no_tool":
        return "(no tool)"
    parts = []
    for exp in case["calls"]:
        opts = []
        for opt in options_of(exp):
            spec = opt.get("args") or {}
            inner = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in spec.items())
            opts.append(f"{opt['tool']}({inner})" if inner else opt["tool"])
        parts.append(" | ".join(opts))
    return " -> ".join(parts)


def device_rule_error(call: Dict[str, Any]) -> Optional[str]:
    """Checks the firmware callbacks make beyond the advertised schema (docs/interfaces.md section 2):
    an MCP property has a single min/max range and counts bytes, not characters."""
    name, args = call["name"], call["args"]
    if name == "self_eyes_effect":
        ms = args.get("duration_ms")
        if isinstance(ms, int) and not isinstance(ms, bool) and 0 < ms < 100:
            return f"{name}.duration_ms: {ms} is neither 0 (effect default) nor 100..10000"
    if name == "self_display_show_info":
        for key, limit in (("title", 24), ("text", 200)):
            value = args.get(key)
            if isinstance(value, str) and len(value) > limit:
                return f"{name}.{key}: longer than {limit} characters"
    return None


def schema_errors(tools: Sequence[Dict[str, Any]], calls: Sequence[Dict[str, Any]]) -> List[str]:
    """Arguments the device or plugin would reject: they break the offered tool's JSON schema (e.g.
    speed 5 < minimum 10; skipped when jsonschema is not installed) or a rule of the firmware
    callback (device_rule_error)."""
    schemas = {t["function"]["name"]: t["function"].get("parameters") or {} for t in tools}
    errors = []
    for call in calls:
        schema = schemas.get(call["name"])
        if schema is None:
            errors.append(f"{call['name']}: unknown tool")
            continue
        if not isinstance(call["args"], dict):
            errors.append(f"{call['name']}: arguments are not a JSON object")
            continue
        found = []
        if jsonschema is not None:
            found = sorted(jsonschema.Draft202012Validator(schema).iter_errors(call["args"]),
                           key=lambda e: list(e.path))
        if found:
            where = ".".join(str(p) for p in found[0].path) or "(root)"
            errors.append(f"{call['name']}.{where}: {found[0].message}")
            continue
        rule = device_rule_error(call)
        if rule:
            errors.append(rule)
    return errors


def score_case(case: Dict[str, Any], real: List[Dict[str, Any]], tolerated: Sequence[str]) -> Tuple[str, str]:
    """Return (verdict, note): PASS, ALT (accepted alternative) or FAIL."""
    if case["kind"] == "no_tool":
        if real:
            return "FAIL", "called " + ", ".join(describe_call(c) for c in real)
        return "PASS", ""
    expected = case["calls"]
    referenced = {opt["tool"] for exp in expected for opt in options_of(exp)}
    ignored = [c for c in real if c["name"] in tolerated and c["name"] not in referenced]
    calls = [c for c in real if c not in ignored]
    note = ("ignored " + ", ".join(c["name"] for c in ignored)) if ignored else ""
    if len(calls) != len(expected):
        got = ", ".join(describe_call(c) for c in calls) or "no tool"
        return "FAIL", f"expected {len(expected)} call(s), got {len(calls)}: {got}"
    alt = False
    for i, (exp, call) in enumerate(zip(expected, calls)):
        opts = options_of(exp)
        hit = next((j for j, opt in enumerate(opts) if match_option(opt, call)), None)
        if hit is None:
            return "FAIL", f"call #{i + 1}: {describe_call(call)}"
        alt = alt or hit > 0
    return ("ALT" if alt else "PASS"), note


# ---------------------------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------------------------
def load_cases(path: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    settings = data.get("settings") or {}
    cases = []
    for kind, key in (("command", "commands"), ("no_tool", "no_tool")):
        for item in data.get(key) or []:
            case = dict(item)
            case["kind"] = kind
            cases.append(case)
    ids = [c["id"] for c in cases]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ValueError(f"duplicate case ids: {dup}")
    for c in cases:
        if not isinstance(c.get("text"), str) or not c["text"].strip():
            raise ValueError(f"case {c.get('id')}: missing text")
        if c["kind"] == "command":
            if not c.get("calls"):
                raise ValueError(f"case {c['id']}: missing calls")
            for exp in c["calls"]:
                for opt in options_of(exp):
                    if not isinstance(opt, dict) or "tool" not in opt:
                        raise ValueError(f"case {c['id']}: every expected call needs a tool")
    return settings, cases


def quick_phrase_task(ctx: RequestContext, text: str) -> Optional[str]:
    """Task id the helmet_tasks quick-phrase hook would run before the LLM (informational)."""
    if ctx.quick_table is None:
        return None
    try:
        import taskslib  # type: ignore
        match = taskslib.match_quick_phrase(text, ctx.quick_table, threshold=ctx.quick_threshold)
        return match.task_id if match else None
    except Exception:
        return None


# ---------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------
def percentile(values: List[float], q: float) -> float:
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(q * (len(ordered) - 1)))))
    return ordered[k]


def trunc(text: str, width: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Score the Jarvis LLM tool calls through LiteLLM.")
    p.add_argument("--base-url", default=os.environ.get("LITELLM_BASE_URL", "http://127.0.0.1:4000/v1"))
    p.add_argument("--api-key", default=None, help="LiteLLM key (default: env LITELLM_MASTER_KEY / --env-file)")
    p.add_argument("--env-file", default=None, help="read LITELLM_MASTER_KEY (and LITELLM_BASE_URL) from a .env file")
    p.add_argument("--model", default=os.environ.get("JARVIS_LLM_MODEL"), help="model group (default: settings.model)")
    p.add_argument("--cases", default=str(DEFAULTS["cases"]))
    p.add_argument("--device-tools", default=str(DEFAULTS["device_tools"]))
    p.add_argument("--prompt", default=str(DEFAULTS["prompt"]))
    p.add_argument("--strings", default=str(DEFAULTS["strings"]))
    p.add_argument("--upstream-strings", action="store_true", help="ignore strings_vi.json (upstream Chinese texts)")
    p.add_argument("--config-template", default=str(DEFAULTS["config_template"]))
    p.add_argument("--tasks", default=str(DEFAULTS["tasks"]))
    p.add_argument("--ask-expert-plugin", default=str(DEFAULTS["ask_expert_plugin"]))
    p.add_argument("--only", default=None, help="comma-separated case ids")
    p.add_argument("--repeat", type=int, default=1, help="run every case N times")
    p.add_argument("--timeout", type=float, default=60.0, help="per request, seconds")
    p.add_argument("--retries", type=int, default=1,
                   help="retries per case on transient errors (timeout, cooldown 429, 5xx)")
    p.add_argument("--no-warmup", action="store_true",
                   help="skip the unscored warm-up request (loads the model, fills Ollama's prompt cache)")
    p.add_argument("--threshold", type=float, default=None, help="command pass rate target (default settings)")
    p.add_argument("--no-fallbacks", action="store_true", help="send disable_fallbacks=true (local model only)")
    p.add_argument("--shuffle-plugins", type=int, default=None, metavar="SEED",
                   help="shuffle the server plugin order like a fresh xiaozhi process")
    p.add_argument("--json-out", default=None, help="write detailed results here")
    p.add_argument("--dump-request", default=None, help="write the request body of the first case here")
    p.add_argument("--dry-run", action="store_true", help="build the requests, do not call LiteLLM")
    p.add_argument("--list-tools", action="store_true", help="print the tool list and exit")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # Vietnamese on Windows consoles
        except (AttributeError, ValueError):
            pass
    args = parse_args(argv)
    src = Sources()
    try:
        settings, cases = load_cases(Path(args.cases))
        ctx = build_context(args, settings, src)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        unknown = wanted - {c["id"] for c in cases}
        if unknown:
            print(f"unknown case ids: {sorted(unknown)}", file=sys.stderr)
            return 2
        cases = [c for c in cases if c["id"] in wanted]
    tool_names = [t["function"]["name"] for t in ctx.tools]
    missing = sorted({opt["tool"] for c in cases if c["kind"] == "command"
                      for exp in c["calls"] for opt in options_of(exp)} - set(tool_names))
    for note in src.notes:
        print(f"[source] {note}")
    print(f"[tools] {len(tool_names)}: {', '.join(tool_names)}")
    if missing:
        print(f"[warn] expected tools not offered to the LLM: {', '.join(missing)}")
    if args.list_tools:
        print(json.dumps(ctx.tools, ensure_ascii=False, indent=2))
        return 0
    extra = {"disable_fallbacks": True} if args.no_fallbacks else None
    if args.dump_request and cases:
        Path(args.dump_request).write_text(
            json.dumps(build_request(ctx, cases[0]["text"], extra=extra), ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"[dump] request for {cases[0]['id']} -> {args.dump_request}")
    if args.dry_run:
        for c in cases:
            build_request(ctx, c["text"], extra=extra)
        print(f"[dry-run] built {len(cases)} requests; model={ctx.model}, params={ctx.params}")
        return 0
    if httpx is None:
        print("httpx is required for real runs (pip install httpx)", file=sys.stderr)
        return 2

    env_values = read_env_file(Path(args.env_file)) if args.env_file else {}
    api_key = args.api_key or os.environ.get("LITELLM_MASTER_KEY") or env_values.get("LITELLM_MASTER_KEY")
    base_url = args.base_url
    if args.env_file and "LITELLM_BASE_URL" in env_values and "LITELLM_BASE_URL" not in os.environ \
            and args.base_url == "http://127.0.0.1:4000/v1":
        base_url = env_values["LITELLM_BASE_URL"]
    if not api_key:
        print("no API key: pass --api-key, set LITELLM_MASTER_KEY or use --env-file server/.env", file=sys.stderr)
        return 2
    tolerated = list(settings.get("tolerated_extra_tools") or [])
    threshold = args.threshold if args.threshold is not None else float(settings.get("threshold", 0.95))
    min_no_tool = float(settings.get("min_no_tool_rate", 1.0))

    print(f"[run] {base_url} model={ctx.model} cases={len(cases)} repeat={args.repeat} params={ctx.params}"
          + (" no-fallbacks" if args.no_fallbacks else ""))
    header = f"{'ID':<18} {'RESULT':<6} {'TTFT':>5} {'TOTAL':>6}  {'GOT':<46} {'EXPECTED'}"
    print(header)
    print("-" * min(140, len(header) + 40))
    results = []
    infra_errors = 0
    schema_violations = 0
    if jsonschema is None:
        print("[warn] jsonschema missing: call arguments are not checked against the tool schemas")
    with httpx.Client() as client:
        if not args.no_warmup and cases:
            warm = call_with_retries(client, base_url, api_key, build_request(ctx, cases[0]["text"], extra=extra),
                                     args.timeout, retries=2)
            print(f"[warmup] {'ok' if not warm.error else 'FAILED: ' + trunc(warm.error, 120)} "
                  f"({warm.total_s:.1f}s, not scored)")
        for rep in range(args.repeat):
            for case in cases:
                body = build_request(ctx, case["text"], extra=extra)
                reply = call_with_retries(client, base_url, api_key, body, args.timeout, args.retries)
                real, direct, interp_error = interpret(reply)
                if reply.error or interp_error:
                    verdict, note = "ERROR", reply.error or interp_error
                    if reply.error:
                        infra_errors += 1
                else:
                    verdict, note = score_case(case, real, tolerated)
                    bad_args = schema_errors(ctx.tools, real)
                    if bad_args:
                        schema_violations += 1
                        if verdict in ("PASS", "ALT"):
                            verdict = "FAIL"
                        note = "; ".join(filter(None, [note, "schema: " + bad_args[0]]))
                got = ", ".join(describe_call(c) for c in real)
                if not got:
                    got = ("direct_answer: " + trunc((direct[0]["args"] or {}).get("response", ""), 30)) if direct \
                        else ("text: " + trunc(reply.content, 36) if reply.content else "(empty)")
                qp = quick_phrase_task(ctx, case["text"])
                flags = []
                if reply.fallbacks:
                    flags.append(f"fallback->{reply.model_group}")
                if qp:
                    flags.append(f"quick-phrase:{qp}")
                if note:
                    flags.append(note)
                ttft = f"{reply.ttft_s:.2f}" if reply.ttft_s is not None else "-"
                print(f"{trunc(case['id'], 18):<18} {verdict:<6} {ttft:>5} {reply.total_s:>6.2f}  "
                      f"{trunc(got, 46):<46} {trunc(describe_expected(case), 60)}"
                      + (f"   [{'; '.join(flags)}]" if flags else ""))
                results.append({
                    "id": case["id"], "kind": case["kind"], "repeat": rep, "text": case["text"],
                    "verdict": verdict, "note": note, "expected": describe_expected(case),
                    "calls": [{"name": c["name"], "args": c["args"], "raw": c["raw"]} for c in real],
                    "direct_answer": [(d["args"] or {}).get("response") for d in direct],
                    "content": reply.content, "ttft_s": reply.ttft_s, "total_s": reply.total_s,
                    "http_status": reply.status, "model_group": reply.model_group,
                    "fallbacks": reply.fallbacks, "quick_phrase_task": qp,
                })

    def rate(kind: str) -> Tuple[int, int, int]:
        sel = [r for r in results if r["kind"] == kind]
        ok = [r for r in sel if r["verdict"] in ("PASS", "ALT")]
        return len(ok), len(sel), len([r for r in sel if r["verdict"] == "ALT"])

    cmd_ok, cmd_n, cmd_alt = rate("command")
    neg_ok, neg_n, _ = rate("no_tool")
    cmd_rate = cmd_ok / cmd_n if cmd_n else 1.0
    neg_rate = neg_ok / neg_n if neg_n else 1.0
    ttfts = [r["ttft_s"] for r in results if r["ttft_s"] is not None]
    totals = [r["total_s"] for r in results if r["verdict"] != "ERROR" and r["total_s"] is not None]
    fallback_n = sum(1 for r in results if r["fallbacks"])
    print("-" * min(140, len(header) + 40))
    print(f"Commands : {cmd_ok}/{cmd_n} = {cmd_rate:.1%} (accepted alternatives: {cmd_alt})   target >= {threshold:.0%}")
    print(f"No tool  : {neg_ok}/{neg_n} = {neg_rate:.1%}   target >= {min_no_tool:.0%}")
    if ttfts and totals:
        print(f"Latency  : first token median {statistics.median(ttfts):.2f}s p90 {percentile(ttfts, 0.9):.2f}s; "
              f"total median {statistics.median(totals):.2f}s p90 {percentile(totals, 0.9):.2f}s")
    if schema_violations:
        print(f"[warn] {schema_violations} answer(s) had arguments outside the tool schema (counted as FAIL)")
    if fallback_n:
        print(f"[warn] {fallback_n} answer(s) came from the fallback model, not {ctx.model}")
    if infra_errors:
        print(f"[warn] {infra_errors} request(s) failed (HTTP/connection errors)")
    passed = cmd_rate >= threshold and neg_rate >= min_no_tool
    if args.json_out:
        Path(args.json_out).write_text(json.dumps({
            "base_url": base_url, "model": ctx.model, "params": ctx.params, "tools": tool_names,
            "sources": src.notes, "command_rate": cmd_rate, "no_tool_rate": neg_rate,
            "schema_violations": schema_violations,
            "passed": passed, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[json] {args.json_out}")
    if results and infra_errors == len(results):
        print("RESULT   : ERROR (no request succeeded)")
        return 2
    print("RESULT   : " + ("PASS" if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
