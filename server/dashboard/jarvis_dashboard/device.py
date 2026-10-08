"""Reads the helmet's own status over its local MCP WebSocket (firmware CONFIG_JARVIS_LOCAL_MCP_WS).

One persistent connection per helmet, asked self.system.get_status about once a second (the mic and
speaker meters move with it) and self.get_device_status now and then. Only read-only tools are
called. The firmware serves two clients (LRU-purged), so the calibration CLI still gets in; a lost
connection is retried with a growing delay.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

try:  # websockets >= 14
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover - older websockets
    from websockets import connect as ws_connect  # type: ignore[no-redef]

# The helmet is on the LAN: never go through a proxy taken from the environment.
_NO_PROXY = {"proxy": None} if "proxy" in inspect.signature(ws_connect).parameters else {}


class McpError(Exception):
    """The device answered with an error or not at all."""


class Backoff(McpError):
    """Not retrying yet: the last attempt failed recently."""


@dataclass
class DeviceProbe:
    host: str
    online: bool = False
    checked: str = ""  # local time of the last attempt
    updated: str = ""  # local time of the last answer
    error: str = ""
    rtt_ms: int = 0  # round trip of the last get_status
    status: Dict[str, Any] = field(default_factory=dict)  # self.system.get_status
    device_status: Dict[str, Any] = field(default_factory=dict)  # self.get_device_status


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


class DeviceLink:
    """A persistent MCP WebSocket to one helmet. Not for concurrent use: one caller at a time."""

    def __init__(self, host: str, port: int = 8080, token: str = "", timeout: float = 4.0,
                 max_backoff: float = 30.0) -> None:
        self.url = f"ws://{host}:{port}/ws"
        if token:
            self.url += "?token=" + urllib.parse.quote(token, safe="")
        self.timeout = timeout
        self.max_backoff = max_backoff
        self._ws = None
        self._next_id = 0
        self._backoff = 1.0
        self._retry_at = 0.0

    async def call_tool(self, name: str) -> Dict[str, Any]:
        if self._ws is None:
            if time.monotonic() < self._retry_at:
                raise Backoff("reconnecting")
            try:
                # The calls keep the link alive: no WebSocket pings (the firmware answers them,
                # but they would only add traffic on the helmet's power-saving Wi-Fi).
                self._ws = await ws_connect(self.url, open_timeout=self.timeout, close_timeout=1,
                                            ping_interval=None, **_NO_PROXY)
                self._next_id = 0
                await self._request("initialize", {
                    "protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "jarvis-dashboard", "version": "1.1"},
                })
            except Exception:
                await self._failed()
                raise
        try:
            result = _tool_json(await self._request("tools/call", {"name": name, "arguments": {}}))
        except Exception:
            await self._failed()
            raise
        self._backoff = 1.0
        return result

    async def _request(self, method: str, params: dict) -> Any:
        self._next_id += 1
        req_id = self._next_id
        await self._ws.send(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}))
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpError(f"no reply to {method}")
            raw = await asyncio.wait_for(self._ws.recv(), remaining)
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

    async def _failed(self) -> None:
        await self.close()
        self._retry_at = time.monotonic() + self._backoff
        self._backoff = min(self._backoff * 2, self.max_backoff)

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001 - already broken
                pass


async def query_device(host: str, port: int = 8080, token: str = "", timeout: float = 6.0) -> Dict[str, Any]:
    """One-off read of both status tools (tools, tests)."""
    link = DeviceLink(host, port, token, timeout)
    try:
        started = time.monotonic()
        status = await link.call_tool("self.system.get_status")
        device_status = await link.call_tool("self.get_device_status")
        return {"status": status, "device_status": device_status,
                "rtt_ms": int((time.monotonic() - started) * 1000)}
    finally:
        await link.close()


async def refresh(link: DeviceLink, target: DeviceProbe, now: str, full: bool) -> DeviceProbe:
    """One poll: get_status always, get_device_status when `full` (or never read yet). Updates
    `target` in place; the last good data stays visible while the helmet is offline."""
    target.checked = now
    started = time.monotonic()
    try:
        status = await link.call_tool("self.system.get_status")
        rtt = int((time.monotonic() - started) * 1000)
        if full or not target.device_status:
            target.device_status = await link.call_tool("self.get_device_status")
    except Backoff:
        target.online = False  # keep the error of the attempt that failed
        return target
    except Exception as exc:  # noqa: BLE001 - any failure just means "offline" on the page
        target.online = False
        target.error = f"{type(exc).__name__}: {exc}"[:200]
        return target
    target.online, target.error, target.updated = True, "", now
    target.status, target.rtt_ms = status, rtt
    return target


async def probe(target: DeviceProbe, port: int, token: str, now: str, timeout: float = 6.0,
                link: Optional[DeviceLink] = None) -> DeviceProbe:
    """refresh() over a throw-away link (kept for callers without a persistent one)."""
    own = link is None
    link = link or DeviceLink(target.host, port, token, timeout)
    try:
        return await refresh(link, target, now, full=True)
    finally:
        if own:
            await link.close()
