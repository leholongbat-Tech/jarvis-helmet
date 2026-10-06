"""helmet_tasks: tasks.yaml as LLM functions, quick phrases and device sync (SV6).

Installed as ``plugins_func/functions/helmet_tasks.py`` in xiaozhi-esp32-server v0.9.7
(Python 3.10). The pure logic (validation with line numbers, normalization, quick-phrase
matching, merging) lives in ``taskslib``, copied to ``/opt/jarvis/lib`` (on PYTHONPATH).

* Every task becomes a SYSTEM_CTL function ``task_<id>``; with more than
  ``max_task_functions`` tasks a single ``run_helmet_task(task: enum)`` is registered instead.
  Removed tasks stay in the registry (not in the module list) so a stale tool list gets a
  polite Vietnamese answer instead of upstream's Chinese NOTFOUND (tolerant dispatcher).
* tasks.yaml is reloaded when its mtime changes, checked on every utterance in
  ``try_quick_phrase``; the connection's tool list is refreshed. An invalid file keeps the last
  good table and logs every problem with its line number.
* All task calls of one LLM turn (same ``conn.sentence_id``, within ``merge_window_ms``) are
  merged into ONE ``self.sequence.run`` in arrival order (contract D4); each call still returns
  its own result. Device calls go through ``call_mcp_tool(conn, conn.mcp_client,
  "self_sequence_run", <json>, timeout)`` after a readiness check.
* Hooks called by server/xiaozhi/patches (contract 4.7), both guarded by the caller:
    ``await try_quick_phrase(conn, text) -> bool``  (core/handle/intentHandler.py)
    ``on_mcp_ready(conn)``                          (device_mcp/mcp_handler.py, sync)
* Latency lines (contract 4.10):
    ``[LAT] sid=<sid> stage=quick_phrase t_ms=<match time> task=<id> score=<0..100>``
    ``[LAT] sid=<sid> stage=mcp_call t_ms=<request sent> tool=<name> ms=<round trip> ok=<0|1>``

Config ``plugins.helmet_tasks`` in data/.config.yaml (never put ``description`` there: upstream
would copy it onto every task_* function): tasks_file, schema_file, quick_phrase_threshold (90),
merge_window_ms (80), mcp_timeout_s (5); optional mcp_ready_wait_s (3),
quick_phrase_accent_floor (75), quick_phrase_margin (3), quick_phrases_enabled (true),
wake_words ([]), reply_mode ("record" | "response"), quick_phrase_history ("tool_chain" | "text"),
sync_on_ready (true), sync_delay_s (0.3), sync_cache_ttl_s (3600, 0 = always),
max_task_functions (20). Environment: HELMET_TASKS_FILE (wins over tasks_file),
HELMET_TASKS_SCHEMA, JARVIS_LIB_DIR (default /opt/jarvis/lib).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from config.logger import setup_logging
from plugins_func.register import (
    Action,
    ActionResponse,
    FunctionItem,
    ToolType,
    all_function_registry,
    module_func_map,
)

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()
_MOD = __name__.rsplit(".", 1)[-1]  # "helmet_tasks": the module_func_map key upstream expands

TASK_PREFIX = "task_"
RUN_TASK_NAME = "run_helmet_task"
SEQ_RUN_TOOL = "self_sequence_run"    # sanitized form of self.sequence.run
SEQ_SAVE_TOOL = "self_sequence_save"  # sanitized form of self.sequence.save
DEFAULT_TASKS_FILE = "/opt/jarvis/tasks/tasks.yaml"
DEFAULT_ACK = "Vâng, thưa ngài."
APOLOGY_FALLBACK = "Xin lỗi thưa ngài, tôi không thực hiện được: "

DEFAULTS: Dict[str, Any] = {
    "tasks_file": DEFAULT_TASKS_FILE,
    "schema_file": None,
    "quick_phrase_threshold": 90,
    "quick_phrase_accent_floor": 75,
    "quick_phrase_margin": 3,
    "quick_phrases_enabled": True,
    "wake_words": [],
    "merge_window_ms": 80,
    "mcp_timeout_s": 5,
    "mcp_ready_wait_s": 3,
    "reply_mode": "record",
    "quick_phrase_history": "tool_chain",
    "sync_on_ready": True,
    "sync_delay_s": 0.3,
    "sync_cache_ttl_s": 3600,
    "max_task_functions": 20,
}


def _import_taskslib():
    try:
        import taskslib as lib
    except ImportError:
        lib_dir = os.environ.get("JARVIS_LIB_DIR", "/opt/jarvis/lib")
        if lib_dir not in sys.path:
            sys.path.insert(0, lib_dir)
        import taskslib as lib
    return lib


try:
    taskslib = _import_taskslib()
except Exception as _exc:  # never raise at import: the whole server would not start
    taskslib = None
    logger.bind(tag=TAG).error(f"[helmet_tasks] cannot import taskslib ({_exc}); plugin disabled")


class HelmetError(Exception):
    """A task could not run: ``reason`` is short Vietnamese (spoken), ``detail`` is technical.

    ``duplicate``: another call of the same merged batch already announces this reason (spoken once).
    """

    def __init__(self, kind: str, reason: str, detail: str = "", duplicate: bool = False):
        super().__init__(detail or reason)
        self.kind = kind
        self.reason = reason
        self.detail = detail or reason
        self.duplicate = duplicate

    def copy(self, duplicate: bool = False) -> "HelmetError":
        return HelmetError(self.kind, self.reason, self.detail, duplicate)


class _Batch:
    """Task calls of one LLM turn waiting to be merged into one self.sequence.run."""

    __slots__ = ("items", "closed", "announced")

    def __init__(self) -> None:
        self.items: List[Any] = []  # (Task, asyncio.Future) in arrival order
        self.closed = False
        self.announced: set = set()  # failure reasons already given to a caller of this batch


class _State:
    def __init__(self) -> None:
        self.table = None              # last good taskslib.TaskTable
        self.version = 0               # bumped on every applied table
        self.signature = None          # (path, mtime_ns, size) of the last load attempt
        self.last_error: Optional[str] = None
        self.tasks_path = DEFAULT_TASKS_FILE
        self.schema_path: Optional[str] = None
        self.global_cfg: Dict[str, Any] = {}
        self.mode = "functions"        # "functions" (task_<id>) or "single" (run_helmet_task)
        self.batches: Dict[Any, _Batch] = {}
        self.sync_cache: Dict[str, Dict[str, Any]] = {}   # device -> task id -> (hash, monotonic)
        self.bg_tasks: set = set()


_STATE = _State()


# ---------------------------------------------------------------------------
# Config, logging helpers
# ---------------------------------------------------------------------------
def _read_global_config() -> Dict[str, Any]:
    """plugins.helmet_tasks from the merged config cached by xiaozhi (fallback: data/.config.yaml)."""
    cfg: Any = None
    try:
        from core.utils.cache.manager import CacheType, cache_manager

        cfg = cache_manager.get(CacheType.CONFIG, "main_config")
    except Exception:
        cfg = None
    if not isinstance(cfg, dict):
        try:
            import yaml

            path = os.path.join(os.getcwd(), "data", ".config.yaml")
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as fh:
                    cfg = yaml.safe_load(fh)
        except Exception:
            cfg = None
    plugins = cfg.get("plugins") if isinstance(cfg, dict) else None
    section = plugins.get(_MOD) if isinstance(plugins, dict) else None
    return dict(section) if isinstance(section, dict) else {}


def _settings(conn: Any = None) -> Dict[str, Any]:
    merged = dict(DEFAULTS)
    merged.update(_STATE.global_cfg)
    if conn is not None:
        try:
            section = (getattr(conn, "config", None) or {}).get("plugins", {}).get(_MOD)
            if isinstance(section, dict):
                merged.update(section)
        except Exception:
            pass
    return merged


def _num(settings: Dict[str, Any], key: str) -> float:
    try:
        return float(settings.get(key, DEFAULTS[key]))
    except (TypeError, ValueError):
        return float(DEFAULTS[key])


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def _wake_words(settings: Dict[str, Any]) -> List[str]:
    words = settings.get("wake_words") or []
    return [w for w in words if isinstance(w, str)] if isinstance(words, list) else []


def _log(conn: Any = None):
    base = getattr(conn, "logger", None) if conn is not None else None
    try:
        return (base or logger).bind(tag=TAG)
    except Exception:
        return logger.bind(tag=TAG)


def _lat_value(value: Any) -> str:
    return "_".join(str(value).split()) or "-"


def _lat(conn: Any, stage: str, t_ms: Optional[int] = None, **fields: Any) -> None:
    """One ``[LAT]`` line (contract 4.10); values never contain spaces."""
    try:
        sid = getattr(conn, "session_id", None) or "-"
        stamp = int(time.time() * 1000) if t_ms is None else int(t_ms)
        extra = "".join(f" {k}={_lat_value(v)}" for k, v in fields.items() if v is not None)
        _log(conn).info(f"[LAT] sid={_lat_value(sid)} stage={stage} t_ms={stamp}{extra}")
    except Exception:
        pass


def _apology(reason: str) -> str:
    if taskslib is not None:
        return taskslib.apology(reason)
    return APOLOGY_FALLBACK + (reason or "lỗi không xác định") + "."


def _spawn(coro) -> "asyncio.Task":
    task = asyncio.get_running_loop().create_task(coro)
    _STATE.bg_tasks.add(task)  # keep a strong reference until done
    task.add_done_callback(_STATE.bg_tasks.discard)
    return task


# ---------------------------------------------------------------------------
# Loading and registration
# ---------------------------------------------------------------------------
_RUN_TASK_DESC: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": RUN_TASK_NAME,
        "description": "Chạy một tác vụ của mũ Jarvis / Run a Jarvis helmet task.",
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "enum": [], "description": "Mã tác vụ / task id"},
            },
            "required": ["task"],
        },
    },
}


def _task_function_desc(task: Any) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TASK_PREFIX + task.id,
            "description": task.description,
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


def _update_run_task_desc(table: Any) -> None:
    # Mutated in place on purpose: connections that cached the dict see the new enum at
    # their next LLM call. Only values are rebound, the dict size never changes.
    fn = _RUN_TASK_DESC["function"]
    catalogue = "; ".join(f"{t.id}: {t.description}" for t in table.tasks.values())
    fn["description"] = ("Chạy một tác vụ của mũ Jarvis / Run a Jarvis helmet task. "
                         f"Tác vụ / tasks: {catalogue}")
    fn["parameters"]["properties"]["task"]["enum"] = list(table.ids)


def _make_task_function(task_id: str):
    async def _task_function(conn: "ConnectionHandler", **kwargs: Any):
        # SYSTEM_CTL: called as func(conn, **arguments); extra LLM arguments are ignored.
        return await _dispatch(conn, task_id)

    _task_function.__name__ = _task_function.__qualname__ = TASK_PREFIX + task_id
    return _task_function


def _is_dynamic_name(name: str) -> bool:
    return name.startswith(TASK_PREFIX) or name == RUN_TASK_NAME


def _apply_table(table: Any) -> None:
    single = len(table.tasks) > int(_num(_settings(), "max_task_functions"))
    names: List[str] = []
    if single:
        _update_run_task_desc(table)
        all_function_registry[RUN_TASK_NAME] = FunctionItem(
            RUN_TASK_NAME, _RUN_TASK_DESC, _run_helmet_task, ToolType.SYSTEM_CTL)
        names.append(RUN_TASK_NAME)
    else:
        for task in table.tasks.values():
            name = TASK_PREFIX + task.id
            all_function_registry[name] = FunctionItem(
                name, _task_function_desc(task), _make_task_function(task.id), ToolType.SYSTEM_CTL)
            names.append(name)
    static = [n for n in module_func_map.get(_MOD, []) if not _is_dynamic_name(n)]
    module_func_map[_MOD] = static + names  # atomic rebind; never mutate the list in place
    _STATE.table = table
    _STATE.mode = "single" if single else "functions"
    _STATE.version += 1


def _signature(path: str):
    try:
        st = os.stat(path)
        return (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return (path, None, None)


def _load_schema():
    path = _STATE.schema_path or taskslib.find_schema(_STATE.tasks_path)
    if not path:
        return None, "schema.json not found: schema cross-check skipped"
    try:
        return taskslib.load_schema(path), None
    except Exception as exc:
        return None, f"cannot read schema {path} ({exc}): schema cross-check skipped"


def reload_tasks(force: bool = False) -> bool:
    """Reload tasks.yaml if it changed (or ``force``); True when a new table was applied.

    On any error the previous table stays active and the problems are logged with line numbers.
    """
    if taskslib is None:
        return False
    path = _STATE.tasks_path
    sig = _signature(path)
    if not force and sig == _STATE.signature:
        return False
    _STATE.signature = sig
    log = logger.bind(tag=TAG)
    kept = len(_STATE.table.tasks) if _STATE.table is not None else 0
    if sig[1] is None:
        _STATE.last_error = f"tasks file not found: {path}"
        log.error(f"[helmet_tasks] {_STATE.last_error}; keeping the previous table ({kept} tasks)")
        return False
    schema, note = _load_schema()
    if note:
        log.warning(f"[helmet_tasks] {note}")
    try:
        table = taskslib.load_tasks(path, schema=schema)
    except taskslib.TasksError as exc:
        _STATE.last_error = exc.format()
        log.error(f"[helmet_tasks] {path} is invalid, keeping the previous table ({kept} tasks):\n"
                  f"{exc.format()}")
        return False
    except Exception as exc:
        _STATE.last_error = f"{type(exc).__name__}: {exc}"
        log.error(f"[helmet_tasks] cannot load {path}, keeping the previous table ({kept} tasks): "
                  f"{_STATE.last_error}")
        return False
    _apply_table(table)
    _STATE.last_error = None
    for warning in table.warnings:
        log.warning(f"[helmet_tasks] {warning}")
    log.info(f"[helmet_tasks] loaded {len(table.tasks)} task(s) from {path} "
             f"(table v{_STATE.version}, {_STATE.mode} mode): {', '.join(table.ids)}")
    return True


def get_table():
    """The active taskslib.TaskTable (None until a valid file was loaded)."""
    return _STATE.table


def _refresh_connection_tools(conn: Any) -> None:
    handler = getattr(conn, "func_handler", None)
    manager = getattr(handler, "tool_manager", None) if handler is not None else None
    if manager is None:
        return
    try:
        manager.refresh_tools()
    except Exception as exc:
        _log(conn).warning(f"[helmet_tasks] refresh_tools failed: {exc}")


def _sync_connection(conn: Any) -> None:
    """Reload on mtime change and make sure this connection's tool list is current."""
    changed = reload_tasks()
    if changed or getattr(conn, "_helmet_tasks_version", None) != _STATE.version:
        _refresh_connection_tools(conn)
        try:
            conn._helmet_tasks_version = _STATE.version
        except Exception:
            pass


def _initialize() -> None:
    try:
        _STATE.global_cfg = _read_global_config()
        if "description" in _STATE.global_cfg:
            logger.bind(tag=TAG).warning(
                "[helmet_tasks] plugins.helmet_tasks.description is set: upstream copies it onto "
                "every task_* function; remove it from the config")
        env_path = os.environ.get("HELMET_TASKS_FILE")
        _STATE.tasks_path = str(env_path or _STATE.global_cfg.get("tasks_file") or DEFAULT_TASKS_FILE)
        schema = os.environ.get("HELMET_TASKS_SCHEMA") or _STATE.global_cfg.get("schema_file")
        _STATE.schema_path = str(schema) if schema else None
        reload_tasks(force=True)
    except Exception as exc:
        logger.bind(tag=TAG).error(f"[helmet_tasks] initial load failed: {exc}")


# ---------------------------------------------------------------------------
# Device calls
# ---------------------------------------------------------------------------
def _map_exception(exc: BaseException) -> HelmetError:
    if isinstance(exc, HelmetError):
        return exc
    msg = str(exc)
    detail = f"{type(exc).__name__}: {msg}"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "超时" in msg:
        return HelmetError("timeout", "mũ không phản hồi kịp", detail)
    if "准备就绪" in msg:
        return HelmetError("not_ready", "mũ chưa sẵn sàng, xin thử lại sau giây lát", detail)
    if "未初始化" in msg:
        return HelmetError("no_mcp", "mũ chưa kết nối điều khiển", detail)
    if msg.startswith("工具 ") and msg.endswith("不存在"):
        return HelmetError("no_tool", "mũ chưa có lệnh này, cần cập nhật firmware", detail)
    reason = taskslib.describe_device_error(msg) if taskslib is not None else "lỗi không xác định"
    return HelmetError("device", reason, detail)


async def _device_call(conn: Any, tool: str, args: Dict[str, Any], settings: Dict[str, Any],
                       label: str = "") -> str:
    """call_mcp_tool with readiness checks, Vietnamese errors and a ``[LAT] mcp_call`` line."""
    client = getattr(conn, "mcp_client", None)
    if client is None:
        raise HelmetError("no_mcp", "mũ chưa kết nối điều khiển",
                          "connection has no mcp_client (hello without features.mcp)")
    wait_s = max(0.0, _num(settings, "mcp_ready_wait_s"))
    deadline = time.monotonic() + wait_s
    while not getattr(client, "ready", False):
        if time.monotonic() >= deadline:
            raise HelmetError("not_ready", "mũ chưa sẵn sàng, xin thử lại sau giây lát",
                              f"device MCP not ready after {wait_s:.1f} s")
        await asyncio.sleep(0.05)
        client = getattr(conn, "mcp_client", None) or client
    has_tool = getattr(client, "has_tool", None)
    if callable(has_tool) and not has_tool(tool):
        raise HelmetError("no_tool", "mũ chưa có lệnh này, cần cập nhật firmware",
                          f"device has no tool {tool}")
    from core.providers.tools.device_mcp import call_mcp_tool

    payload = json.dumps(args, ensure_ascii=False)
    timeout = max(0.5, _num(settings, "mcp_timeout_s"))
    sent_ms = int(time.time() * 1000)
    started = time.monotonic()
    ok = False
    try:
        result = await call_mcp_tool(conn, client, tool, payload, timeout=timeout)
        ok = True
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        raise _map_exception(exc) from exc
    finally:
        _lat(conn, "mcp_call", t_ms=sent_ms, tool=tool,
             ms=int((time.monotonic() - started) * 1000), ok=int(ok), tasks=label or None)


# ---------------------------------------------------------------------------
# LLM function calls (one chat turn)
# ---------------------------------------------------------------------------
def _close_batch(key: Any, batch: _Batch) -> None:
    batch.closed = True
    if _STATE.batches.get(key) is batch:
        del _STATE.batches[key]


def _fail(batch: _Batch, fut: "asyncio.Future", err: HelmetError) -> None:
    """Fail one call of the batch; a reason already given to an earlier call is marked duplicate,
    so N merged task calls that fail together apologise once (arrival order = LLM order)."""
    if fut.done():
        return
    duplicate = err.reason in batch.announced
    batch.announced.add(err.reason)
    fut.set_exception(err.copy(duplicate=duplicate))


def _fail_all(batch: _Batch, err: HelmetError) -> None:
    for _, fut in batch.items:
        _fail(batch, fut, err)


async def _flush_batch(conn: Any, batch: _Batch, settings: Dict[str, Any]) -> None:
    items = list(batch.items)
    try:
        plan = taskslib.plan_merge([task.steps for task, _ in items])
        for idx, reason in plan.rejected:
            task, fut = items[idx]
            _fail(batch, fut, HelmetError(
                "limit", "chuỗi lệnh gộp vượt giới hạn của mũ (tối đa 64 bước, 60 giây)",
                f"task {task.id} not merged: {reason}"))
        if plan.accepted:
            ids = [items[i][0].id for i in plan.accepted]
            if len(items) > 1:
                _log(conn).info(f"[helmet_tasks] merged {len(ids)} task call(s) into one sequence: "
                                f"{', '.join(ids)} ({len(plan.steps)} steps, ~{plan.estimated_ms} ms)")
            text = await _device_call(conn, SEQ_RUN_TOOL, {"steps": plan.steps_json}, settings,
                                      label=",".join(ids))
            for i in plan.accepted:
                fut = items[i][1]
                if not fut.done():
                    fut.set_result(text)
    except BaseException as exc:  # also CancelledError: never leave a caller waiting
        err = _map_exception(exc) if isinstance(exc, Exception) else HelmetError(
            "cancelled", "lệnh đã bị hủy", "cancelled")
        _fail_all(batch, err)
        if not isinstance(exc, Exception):
            raise


async def _run_in_turn(conn: Any, task: Any, settings: Dict[str, Any]) -> str:
    """Join (or open) the merge batch of this turn; returns the device answer ("started")."""
    loop = asyncio.get_running_loop()
    window = max(0.0, _num(settings, "merge_window_ms") / 1000.0)
    key = (getattr(conn, "session_id", None) or id(conn), getattr(conn, "sentence_id", None))
    fut = loop.create_future()
    batch = _STATE.batches.get(key)
    if batch is not None and not batch.closed:
        batch.items.append((task, fut))
        return await fut
    batch = _Batch()
    batch.items.append((task, fut))
    _STATE.batches[key] = batch
    try:
        await asyncio.sleep(window)  # siblings of the same LLM reply join during this window
    except BaseException:
        _close_batch(key, batch)
        _fail_all(batch, HelmetError("cancelled", "lệnh đã bị hủy", "cancelled while merging"))
        raise
    _close_batch(key, batch)
    await _flush_batch(conn, batch, settings)
    return await fut


def _error_response(err: HelmetError) -> ActionResponse:
    if err.duplicate:
        # Same failure as an earlier call of this merged batch, which speaks the apology: RECORD keeps
        # the tool chain in the history without speaking it again (RESPONSE with response=None would
        # make upstream speak the technical result).
        return ActionResponse(Action.RECORD, result=f"error: {err.detail}", response=_apology(err.reason))
    return ActionResponse(Action.RESPONSE, result=f"error: {err.detail}", response=_apology(err.reason))


def _last_user_text(conn: Any) -> str:
    try:
        for msg in reversed(conn.dialogue.dialogue):
            if getattr(msg, "role", None) == "user" and isinstance(getattr(msg, "content", None), str):
                return msg.content
    except Exception:
        pass
    return ""


def _queue_reply(conn: Any, reply: str) -> bool:
    """Queue the fixed reply as MIDDLE TTS items of the current turn (play_music pattern)."""
    tts = getattr(conn, "tts", None)
    if tts is None:
        return False
    try:
        from core.providers.tts.dto.dto import ContentType
    except Exception:
        return False
    try:
        tts.store_tts_text(conn.sentence_id, reply)
    except Exception:
        pass
    try:
        tts.tts_one_sentence(conn, ContentType.TEXT, content_detail=reply)
        return True
    except Exception as exc:
        _log(conn).warning(f"[helmet_tasks] cannot queue reply: {exc}")
        return False


async def _dispatch(conn: Any, task_id: str) -> ActionResponse:
    """Tolerant dispatcher shared by every task_* function and run_helmet_task."""
    try:
        table = _STATE.table
        if taskslib is None or table is None:
            return _error_response(HelmetError("no_table", "danh sách tác vụ chưa được nạp",
                                               "tasks table not loaded"))
        task = table.get(task_id)
        if task is None:
            return _error_response(HelmetError("unknown_task",
                                               f"tác vụ {task_id} không còn trong danh sách",
                                               f"unknown task {task_id}"))
        settings = _settings(conn)
        try:
            device_text = await _run_in_turn(conn, task, settings)
        except HelmetError as exc:
            _log(conn).warning(f"[helmet_tasks] task {task_id} failed: {exc.detail}")
            return _error_response(exc)
        result = f"Helmet task '{task_id}' started (device: {device_text})."
        reply = taskslib.pick_reply(task.reply, taskslib.detect_lang(_last_user_text(conn)))
        if reply:
            if str(settings.get("reply_mode", "record")).lower() != "response" and _queue_reply(conn, reply):
                return ActionResponse(Action.RECORD, result=result, response=reply)
            return ActionResponse(Action.RESPONSE, result=result, response=reply)
        seconds = max(1, round(task.estimated_ms / 1000))
        return ActionResponse(Action.REQLLM, result=(
            f"Helmet task '{task_id}' started on the helmet ({len(task.steps)} steps, about "
            f"{seconds} s). Confirm it to the user in one short sentence."), response=None)
    except Exception as exc:
        _log(conn).error(f"[helmet_tasks] task {task_id} crashed: {type(exc).__name__}: {exc}")
        return _error_response(_map_exception(exc))


async def _run_helmet_task(conn: "ConnectionHandler", task: Any = None, **kwargs: Any):
    task_id = task.strip() if isinstance(task, str) else ""
    if not task_id:
        return _error_response(HelmetError("bad_args", "chưa rõ cần chạy tác vụ nào",
                                           "missing 'task' argument"))
    return await _dispatch(conn, task_id)


# ---------------------------------------------------------------------------
# Quick phrases (hook in core/handle/intentHandler.py::handle_user_intent)
# ---------------------------------------------------------------------------
def _record_quick_history(conn: Any, text: str, task: Any, error: Optional[HelmetError],
                          settings: Dict[str, Any], mode: str) -> None:
    try:
        from core.utils.dialogue import Message

        dialogue = getattr(conn, "dialogue", None)
        if dialogue is None:
            return
        dialogue.put(Message(role="user", content=text))
        if str(settings.get("quick_phrase_history", "tool_chain")).lower() != "tool_chain":
            return
        # Synthetic tool call so the LLM learns from history that the phrase means a tool call.
        call_id = "call_" + uuid.uuid4().hex[:24]
        if mode == "single":
            name, args = RUN_TASK_NAME, json.dumps({"task": task.id})
        else:
            name, args = TASK_PREFIX + task.id, "{}"
        dialogue.put(Message(role="assistant", tool_calls=[{
            "id": call_id, "function": {"name": name, "arguments": args},
            "type": "function", "index": 0}]))
        dialogue.put(Message(role="tool", tool_call_id=call_id,
                             content="started" if error is None else f"error: {error.detail}"))
    except Exception as exc:
        _log(conn).warning(f"[helmet_tasks] cannot record quick-phrase history: {exc}")


async def _run_quick_phrase(conn: Any, text: str, task: Any, lang: str, mode: str) -> None:
    """Background part of a quick phrase: device I/O first, then the spoken reply."""
    log = _log(conn)
    settings = _settings(conn)
    job = asyncio.ensure_future(_device_call(conn, SEQ_RUN_TOOL, {"steps": task.steps_json},
                                             settings, label=task.id))
    await asyncio.sleep(0)  # let the MCP request leave before the stt / tts messages
    try:
        from core.handle.sendAudioHandle import send_stt_message

        await send_stt_message(conn, text)  # device shows the user text, tts start
    except Exception as exc:
        log.warning(f"[helmet_tasks] send_stt_message failed: {exc}")
    sentence_id = uuid.uuid4().hex  # speak_txt pattern: fresh turn id, abort flag cleared
    conn.client_abort = False
    conn.sentence_id = sentence_id
    error: Optional[HelmetError] = None
    try:
        await job
    except HelmetError as exc:
        error = exc
    except Exception as exc:
        error = _map_exception(exc)
    if error is None:
        reply = taskslib.pick_reply(task.reply, lang) or DEFAULT_ACK
    else:
        log.warning(f"[helmet_tasks] quick phrase task {task.id} failed: {error.detail}")
        reply = _apology(error.reason)
    _record_quick_history(conn, text, task, error, settings, mode)
    if getattr(conn, "tts", None) is None or getattr(conn, "sentence_id", None) != sentence_id:
        # No TTS, or a newer turn took over meanwhile: keep the history consistent, stay silent.
        try:
            from core.utils.dialogue import Message

            conn.dialogue.put(Message(role="assistant", content=reply))
        except Exception:
            pass
        log.info(f"[helmet_tasks] quick-phrase reply not spoken: {reply}")
        return
    try:
        from core.handle.intentHandler import speak_txt

        speak_txt(conn, reply)  # FIRST + text + LAST, adds the assistant message
    except Exception as exc:
        log.error(f"[helmet_tasks] cannot speak quick-phrase reply: {exc}")


async def try_quick_phrase(conn: "ConnectionHandler", text: str) -> bool:
    """Run a task directly when the whole utterance matches one of its quick phrases.

    Returns quickly: device I/O and the reply run in a background task, because in PTT mode this
    hook runs inside the websocket receive loop (awaiting a device reply here would deadlock).
    Also the reload point for tasks.yaml.
    """
    try:
        if taskslib is None:
            return False
        _sync_connection(conn)
        if not isinstance(text, str) or not text.strip():
            return False
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                data = json.loads(stripped)
                if isinstance(data, dict) and isinstance(data.get("content"), str):
                    text = data["content"]
            except ValueError:
                pass
        table = _STATE.table
        if table is None or not table.phrase_index:
            return False
        settings = _settings(conn)
        if not _truthy(settings.get("quick_phrases_enabled", True)):
            return False
        analysis = taskslib.analyze_quick_phrase(
            text, table,
            threshold=_num(settings, "quick_phrase_threshold"),
            wake_words=_wake_words(settings),
            accent_floor=_num(settings, "quick_phrase_accent_floor"),
            margin=_num(settings, "quick_phrase_margin"))
        match = analysis.match
        if match is None:
            if analysis.reason in ("ambiguous", "multi_command"):
                _log(conn).info(f"[helmet_tasks] no quick phrase ({analysis.reason}): "
                                f"'{analysis.normalized}' -> LLM")
            return False
        task = table.get(match.task_id)
        if task is None:
            return False
        _lat(conn, "quick_phrase", task=task.id, score=int(round(match.score)))
        _log(conn).info(f"[helmet_tasks] quick phrase '{match.phrase}' -> {task.id} "
                        f"(score {match.score:.1f}, {match.form})")
        _spawn(_run_quick_phrase(conn, text, task, match.lang, _STATE.mode))
        return True
    except Exception as exc:
        _log(conn).error(f"[helmet_tasks] try_quick_phrase failed: {type(exc).__name__}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Device sync (hook in device_mcp/mcp_handler.py after set_ready(True))
# ---------------------------------------------------------------------------
def _device_key(conn: Any) -> str:
    headers = getattr(conn, "headers", None) or {}
    value = getattr(conn, "device_id", None) or (headers.get("device-id") if isinstance(headers, dict) else None)
    return str(value or getattr(conn, "session_id", None) or "unknown")


async def _sync_to_device(conn: Any) -> None:
    """self.sequence.save(id, steps) for every sync_to_device task (skips unchanged ones)."""
    log = _log(conn)
    try:
        reload_tasks()
        table = _STATE.table
        if table is None:
            return
        tasks = [t for t in table.tasks.values() if t.sync_to_device]
        if not tasks:
            return
        settings = _settings(conn)
        delay = max(0.0, _num(settings, "sync_delay_s"))
        if delay:
            await asyncio.sleep(delay)  # do not compete with the user's first command
        device = _device_key(conn)
        cache = _STATE.sync_cache.setdefault(device, {})
        ttl = _num(settings, "sync_cache_ttl_s")
        saved: List[str] = []
        unchanged: List[str] = []
        for task in tasks:
            if len(task.id) > taskslib.SEQUENCE_NAME_MAX_LEN:
                log.warning(f"[helmet_tasks] cannot sync {task.id}: id longer than "
                            f"{taskslib.SEQUENCE_NAME_MAX_LEN} characters")
                continue
            digest = task.steps_hash
            previous = cache.get(task.id)
            if previous and previous[0] == digest and ttl > 0 and time.monotonic() - previous[1] < ttl:
                unchanged.append(task.id)
                continue
            try:
                await _device_call(conn, SEQ_SAVE_TOOL, {"name": task.id, "steps": task.steps_json},
                                   settings, label=task.id)
            except HelmetError as exc:
                log.warning(f"[helmet_tasks] device sync of {task.id} failed: {exc.detail}")
                if exc.kind in ("no_mcp", "not_ready", "no_tool", "timeout"):
                    break
                continue
            cache[task.id] = (digest, time.monotonic())
            saved.append(task.id)
        log.info(f"[helmet_tasks] device sync ({device}): saved [{', '.join(saved)}], "
                 f"unchanged [{', '.join(unchanged)}]")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.error(f"[helmet_tasks] device sync failed: {type(exc).__name__}: {exc}")


def on_mcp_ready(conn: "ConnectionHandler"):
    """Sync hook called right after ``mcp_client.set_ready(True)``: schedules the device sync.

    Returns the scheduled task/future (or None); never raises.
    """
    try:
        if taskslib is None or not _truthy(_settings(conn).get("sync_on_ready", True)):
            return None
        coro = _sync_to_device(conn)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            task = loop.create_task(coro)
            _STATE.bg_tasks.add(task)
            task.add_done_callback(_STATE.bg_tasks.discard)
            return task
        conn_loop = getattr(conn, "loop", None)
        if conn_loop is not None and conn_loop.is_running():
            return asyncio.run_coroutine_threadsafe(coro, conn_loop)
        coro.close()
        _log(conn).warning("[helmet_tasks] on_mcp_ready: no running event loop, device sync skipped")
    except Exception as exc:
        _log(conn).error(f"[helmet_tasks] on_mcp_ready failed: {type(exc).__name__}: {exc}")
    return None


if taskslib is not None:
    _initialize()
