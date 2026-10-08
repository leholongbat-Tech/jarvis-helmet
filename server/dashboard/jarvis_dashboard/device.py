"""Reads the helmet's own status over its local MCP WebSocket (firmware CONFIG_JARVIS_LOCAL_MCP_WS).

The firmware serves at most two clients (the calibration CLI plus one spare, LRU-purged), so every
poll opens a short connection, asks self.system.get_status and self.get_device_status, and closes.
Only read-only tools are called.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict

try:  # websockets >= 14
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover - older websockets
    from websockets import connect as ws_connect  # type: ignore[no-redef]

# The helmet is on the LAN: never go through a proxy taken from the environment.
_NO_PROXY = {"proxy": None} if "proxy" in inspect.signature(ws_connect).parameters else {}


class McpError(Exception):
    """The device answered with an error or not at all."""


@dataclass
class DeviceProbe:
    host: str
    online: bool = False
    checked: str = ""  # local time of the last attempt
    updated: str = ""  # local time of the last answer
    error: str = ""
    rtt_ms: int = 0
    status: Dict[str, Any] = field(default_factory=dict)  # self.system.get_status
    device_status: Dict[str, Any] = field(default_factory=dict)  # self.get_device_status


async def _request(ws, req_id: int, method: str, params: dict, timeout: float) -> Any:
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}))
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise McpError(f"no reply to {method}")
        raw = await asyncio.wait_for(ws.recv(), remaining)
        try:
            reply = json.loads(raw)
        except ValueError:
            continue
        if isinstance(reply, dict) and reply.get("type") == "mcp" and isinstance(reply.get("payload"), dict):
            reply = reply["payload"]  # wrapped form
        if not isinstance(reply, dict) or reply.get("id") != req_id:
            continue  # a notification or a late reply
        if "error" in reply:
            raise McpError(str(reply["error"])[:200])
        return reply.get("result")


def _tool_json(result: Any) -> Dict[str, Any]:
    """The JSON text of a tools/call result."""
    if not isinstance(result, dict):
        return {}
    content = result.get("content")
    if not (isinstance(content, list) and content and isinstance(content[0], dict)):
        return {}
    text = str(content[0].get("text", ""))
    if result.get("isError"):
        raise McpError(text[:200])
    try:
        value = json.loads(text)
    except ValueError:
        return {"text": text[:200]}
    return value if isinstance(value, dict) else {"value": value}


async def query_device(host: str, port: int = 8080, token: str = "", timeout: float = 6.0) -> Dict[str, Any]:
    url = f"ws://{host}:{port}/ws"
    if token:
        url += "?token=" + urllib.parse.quote(token, safe="")
    started = time.monotonic()
    async with ws_connect(url, open_timeout=timeout, close_timeout=1, **_NO_PROXY) as ws:
        await _request(ws, 1, "initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "jarvis-dashboard", "version": "1.0"},
        }, timeout)
        status = _tool_json(await _request(ws, 2, "tools/call",
                                           {"name": "self.system.get_status", "arguments": {}}, timeout))
        device_status = _tool_json(await _request(ws, 3, "tools/call",
                                                  {"name": "self.get_device_status", "arguments": {}}, timeout))
    return {"status": status, "device_status": device_status,
            "rtt_ms": int((time.monotonic() - started) * 1000)}


async def probe(target: DeviceProbe, port: int, token: str, now: str, timeout: float = 6.0) -> DeviceProbe:
    """Updates `target` in place; the last good status stays visible while the device is offline."""
    target.checked = now
    try:
        result = await query_device(target.host, port, token, timeout)
    except Exception as exc:  # noqa: BLE001 - any failure just means "offline" on the page
        target.online = False
        target.error = f"{type(exc).__name__}: {exc}"[:200]
        return target
    target.online, target.error = True, ""
    target.updated = now
    target.rtt_ms = result["rtt_ms"]
    target.status = result["status"]
    target.device_status = result["device_status"]
    return target
