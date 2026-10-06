#!/usr/bin/env python3
"""Servo calibration and manual MCP console for the Jarvis Helmet.

Talks MCP (JSON-RPC 2.0) to the helmet's local endpoint ws://<helmet-ip>:8080/ws
(firmware option CONFIG_JARVIS_LOCAL_MCP_WS). Unlike the xiaozhi server it lists and
calls the user-only tools (tools/list with {"withUserTools": true}), e.g.
self.servo.calibrate and self.servo.save_calibration.

If the helmet has an access token (tools/provision_secrets.py --mcp-token or
--generate-mcp-token), pass it with the environment variable JARVIS_MCP_TOKEN (preferred,
stays out of the shell history) or --token; it is sent as ws://...:8080/ws?token=<value>
and never printed.

Examples:
  set JARVIS_MCP_TOKEN=...            (Windows cmd; Git Bash: export JARVIS_MCP_TOKEN=...)
  python tools/servo_calibrate.py --host 192.168.1.50
  python tools/servo_calibrate.py --host 192.168.1.50 --list
  python tools/servo_calibrate.py --host 192.168.1.50 --call self.mask.get_state
  python tools/servo_calibrate.py --host 192.168.1.50 --call self.servo.calibrate '{"servo": 0, "angle": 95}'

Exit codes: 0 ok, 1 MCP or usage error, 2 cannot connect, 3 refused by the helmet
(token missing or wrong).

Requires Python 3.12 and `websockets` (pip install websockets).
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import sys
import urllib.parse
from typing import Any

try:  # websockets >= 13
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover - older websockets
    from websockets import connect as ws_connect  # type: ignore[no-redef]
from websockets.exceptions import (
    ConnectionClosed,
    InvalidHandshake,
    InvalidURI,
    WebSocketException,
)

ANGLE_MIN, ANGLE_MAX = 0, 180
SPEED_MIN, SPEED_MAX = 10, 100
TOKEN_ENV = "JARVIS_MCP_TOKEN"
EXIT_REFUSED = 3
# The helmet is on the LAN: never go through a system proxy (websockets >= 15 uses
# HTTP(S)_PROXY / the Windows proxy setting by default), which would also see the token.
try:
    _NO_PROXY = {"proxy": None} if "proxy" in inspect.signature(ws_connect).parameters else {}
except (TypeError, ValueError):  # pragma: no cover - signature not introspectable
    _NO_PROXY = {}

HELP = """\
Lệnh (commands):
  cal <servo> <angle>           đặt góc thô 0..180 cho servo 0 (trái) hoặc 1 (phải)
                                (self.servo.calibrate, KHÔNG bị giới hạn: tăng từng chút)
  + [bước] | - [bước]           tăng / giảm góc servo vừa chỉnh (mặc định 1 độ)
  save <servo> <closed> <open>  lưu góc đóng / mở vào NVS (self.servo.save_calibration)
  state                         trạng thái mặt nạ (self.mask.get_state)
  open [speed] | close [speed]  mở / đóng mặt nạ theo hiệu chỉnh đã lưu (speed 10..100)
  pos <percent> [speed]         đặt vị trí 0 (đóng) .. 100 (mở)
  status                        self.system.get_status
  list                          liệt kê tool, kể cả tool user-only
  call <tool> [json]            gọi tool bất kỳ, ví dụ: call self.eyes.set {"on": true}
  help | quit
"""


class McpError(Exception):
    """JSON-RPC error returned by the device (or a protocol problem)."""


class McpClient:
    """Minimal MCP client: one request at a time, replies matched by id."""

    def __init__(self, ws: Any, timeout: float, verbose: bool = False) -> None:
        self._ws = ws
        self._timeout = timeout
        self._verbose = verbose
        self._next_id = 1

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        request_id = self._next_id
        self._next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        text = json.dumps(message, ensure_ascii=False)
        if self._verbose:
            print(f">> {text}")
        await self._ws.send(text)
        try:
            return await asyncio.wait_for(self._wait_reply(request_id), self._timeout)
        except TimeoutError as exc:
            raise McpError(f"no reply to {method} within {self._timeout:g} s") from exc

    async def _wait_reply(self, request_id: int) -> Any:
        while True:
            raw = await self._ws.recv()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            if self._verbose:
                print(f"<< {raw}")
            try:
                reply = json.loads(raw)
            except json.JSONDecodeError:
                continue  # not JSON: ignore
            if not isinstance(reply, dict):
                continue
            if reply.get("type") == "mcp" and isinstance(reply.get("payload"), dict):
                reply = reply["payload"]  # wrapped form
            if reply.get("id") != request_id:
                if reply.get("id") is None and "error" in reply:
                    raise McpError(_format_error(reply["error"]))
                continue  # notification or a late reply to something else
            if "error" in reply:
                raise McpError(_format_error(reply["error"]))
            return reply.get("result")

    async def initialize(self) -> dict[str, Any]:
        result = await self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "servo_calibrate", "version": "1.0"},
            },
        )
        return result if isinstance(result, dict) else {}

    async def list_tools(self, with_user_tools: bool = True) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"withUserTools": with_user_tools}
            if cursor:
                params["cursor"] = cursor
            result = await self.request("tools/list", params)
            if not isinstance(result, dict):
                raise McpError("tools/list: unexpected reply")
            tools.extend(t for t in result.get("tools", []) if isinstance(t, dict))
            cursor = result.get("nextCursor") or None
            if not cursor:
                return tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        result = await self.request("tools/call", {"name": name, "arguments": arguments or {}})
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list) and content and isinstance(content[0], dict):
                text = content[0].get("text", "")
                if result.get("isError"):
                    raise McpError(str(text))
                return str(text)
        return json.dumps(result, ensure_ascii=False)


def _format_error(error: Any) -> str:
    if isinstance(error, dict):
        return f"{error.get('code', '?')}: {error.get('message', '')}"
    return str(error)


def _is_user_only(tool: dict[str, Any]) -> bool:
    annotations = tool.get("annotations")
    return isinstance(annotations, dict) and "user" in (annotations.get("audience") or [])


def format_tools(tools: list[dict[str, Any]]) -> str:
    lines = []
    for tool in tools:
        description = str(tool.get("description", "")).strip().splitlines()
        tag = " [user]" if _is_user_only(tool) else ""
        lines.append(f"  {tool.get('name', '?')}{tag}")
        if description:
            lines.append(f"      {description[0][:110]}")
        schema = tool.get("inputSchema", {})
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
        if props:
            params = ", ".join(
                f"{name}{'' if name in required else '?'}:{spec.get('type', '?')}"
                for name, spec in props.items()
                if isinstance(spec, dict)
            )
            lines.append(f"      ({params})")
    return "\n".join(lines)


def parse_int(text: str, name: str, lo: int, hi: int) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise ValueError(f"{name} phải là số nguyên: {text!r}") from exc
    if not lo <= value <= hi:
        raise ValueError(f"{name} ngoài khoảng {lo}..{hi}: {value}")
    return value


def parse_json_object(text: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("tham số phải là một JSON object, ví dụ {\"servo\": 0}")
    return value


class Console:
    """Interactive calibration session."""

    def __init__(self, client: McpClient) -> None:
        self.client = client
        self.servo: int | None = None
        self.angles: dict[int, int] = {}

    async def calibrate(self, servo: int, angle: int) -> None:
        angle = max(ANGLE_MIN, min(ANGLE_MAX, angle))
        reply = await self.client.call_tool("self.servo.calibrate", {"servo": servo, "angle": angle})
        self.servo = servo
        self.angles[servo] = angle
        print(f"servo {servo} -> {angle}°  ({reply})")

    async def handle(self, line: str) -> bool:
        """Runs one command. Returns False to quit."""
        line = line.strip()
        if not line or line.startswith("#"):
            return True
        command, _, rest = line.partition(" ")
        command = command.lower()
        args = rest.split()

        if command in ("quit", "exit", "q"):
            return False
        if command in ("help", "?", "h"):
            print(HELP)
        elif command == "cal":
            if len(args) != 2:
                raise ValueError("cú pháp: cal <servo> <angle>")
            await self.calibrate(parse_int(args[0], "servo", 0, 1),
                                 parse_int(args[1], "angle", ANGLE_MIN, ANGLE_MAX))
        elif command in ("+", "-") or (command[0] in "+-" and command[1:].isdigit()):
            if self.servo is None:
                raise ValueError("chưa chọn servo: dùng 'cal <servo> <angle>' trước")
            step_text = command[1:] or (args[0] if args else "1")
            step = parse_int(step_text, "bước", 1, 90)
            sign = 1 if command[0] == "+" else -1
            await self.calibrate(self.servo, self.angles[self.servo] + sign * step)
        elif command == "save":
            if len(args) != 3:
                raise ValueError("cú pháp: save <servo> <closed> <open>")
            servo = parse_int(args[0], "servo", 0, 1)
            closed = parse_int(args[1], "closed", ANGLE_MIN, ANGLE_MAX)
            opened = parse_int(args[2], "open", ANGLE_MIN, ANGLE_MAX)
            reply = await self.client.call_tool(
                "self.servo.save_calibration",
                {"servo": servo, "closed_angle": closed, "open_angle": opened},
            )
            print(f"servo {servo}: closed={closed}° open={opened}° -> {reply}")
        elif command == "state":
            print(await self.client.call_tool("self.mask.get_state"))
        elif command in ("open", "close"):
            arguments = {}
            if args:
                arguments["speed"] = parse_int(args[0], "speed", SPEED_MIN, SPEED_MAX)
            print(await self.client.call_tool(f"self.mask.{command}", arguments))
        elif command == "pos":
            if not args:
                raise ValueError("cú pháp: pos <percent> [speed]")
            arguments = {"percent": parse_int(args[0], "percent", 0, 100)}
            if len(args) > 1:
                arguments["speed"] = parse_int(args[1], "speed", SPEED_MIN, SPEED_MAX)
            print(await self.client.call_tool("self.mask.set_position", arguments))
        elif command == "status":
            print(await self.client.call_tool("self.system.get_status"))
        elif command == "list":
            print(format_tools(await self.client.list_tools(with_user_tools=True)))
        elif command == "call":
            tool, _, json_text = rest.strip().partition(" ")
            if not tool:
                raise ValueError("cú pháp: call <tool> [json]")
            print(await self.client.call_tool(tool, parse_json_object(json_text)))
        else:
            print(f"Lệnh không rõ: {command!r}. Gõ 'help'.")
        return True

    async def run(self) -> None:
        print(HELP)
        while True:
            try:
                line = await asyncio.to_thread(input, "jarvis> ")
            except EOFError:
                print()
                return
            try:
                if not await self.handle(line):
                    return
            except (ValueError, json.JSONDecodeError, McpError) as exc:
                print(f"Lỗi: {exc}")


def _query_keys(url: str) -> list[str]:
    query = urllib.parse.urlsplit(url).query
    return [key for key, _ in urllib.parse.parse_qsl(query, keep_blank_values=True)]


def add_token(url: str, token: str) -> str:
    """Appends ?token=<token> (percent-encoded) to the WebSocket URL."""
    parts = urllib.parse.urlsplit(url)
    pair = "token=" + urllib.parse.quote(token, safe="")
    query = f"{parts.query}&{pair}" if parts.query else pair
    return urllib.parse.urlunsplit(parts._replace(query=query))


def redact_url(url: str) -> str:
    """The URL with the token value replaced by ***, for messages."""
    parts = urllib.parse.urlsplit(url)
    if not parts.query:
        return url
    pairs = ["token=***" if pair.split("=", 1)[0] == "token" else pair
             for pair in parts.query.split("&")]
    return urllib.parse.urlunsplit(parts._replace(query="&".join(pairs)))


def token_forms(url: str) -> list[str]:
    """Raw and decoded forms of every token value in the URL, longest first."""
    forms: set[str] = set()
    for pair in urllib.parse.urlsplit(url).query.split("&"):
        key, _, value = pair.partition("=")
        if key == "token" and value:
            forms.update((value, urllib.parse.unquote_plus(value)))
    return sorted((f for f in forms if f), key=len, reverse=True)


def scrub(text: str, secrets: list[str]) -> str:
    """Hides the token in text that comes from a library (exception messages)."""
    for secret in secrets:
        text = text.replace(secret, "***")
    return text


def build_url(args: argparse.Namespace) -> tuple[str, bool]:
    """Returns the URL to open and whether it carries a token."""
    if args.url:
        url = args.url
    else:
        host = args.host or os.environ.get("JARVIS_HELMET_HOST")
        if not host:
            raise SystemExit("Thiếu --host (hoặc biến môi trường JARVIS_HELMET_HOST, hoặc --url).")
        path = args.path if args.path.startswith("/") else "/" + args.path
        url = f"ws://{host}:{args.port}{path}"
    try:
        scheme = urllib.parse.urlsplit(url).scheme.lower()
    except ValueError as exc:  # e.g. an unbalanced IPv6 bracket; the URL may hold a token
        raise SystemExit(f"URL không hợp lệ ({exc}): kiểm tra --host / --url.") from None
    if scheme not in ("ws", "wss"):
        raise SystemExit(f"--url phải bắt đầu bằng ws:// (hoặc wss://): {redact_url(url)}")

    if "token" in _query_keys(url):
        if args.token:
            raise SystemExit("URL đã có tham số token=: bỏ --token hoặc bỏ token khỏi --url.")
        return url, True  # the token in --url wins over JARVIS_MCP_TOKEN
    token = (args.token if args.token is not None else os.environ.get(TOKEN_ENV, "")).strip()
    if not token:
        return url, False
    if args.token:
        print(f"Lưu ý: --token nằm trong lịch sử shell; nên dùng biến môi trường {TOKEN_ENV}.",
              file=sys.stderr)
    return add_token(url, token), True


def _http_status(exc: InvalidHandshake) -> int | None:
    response = getattr(exc, "response", None)  # websockets >= 14: InvalidStatus
    status = getattr(response, "status_code", None)
    if status is None:
        status = getattr(exc, "status_code", None)  # legacy InvalidStatusCode
    return status if isinstance(status, int) else None


def describe_refusal(exc: InvalidHandshake, shown_url: str, has_token: bool,
                     detail: str) -> tuple[str, int]:
    """Message and exit code for a failed WebSocket handshake (`detail`: scrubbed str(exc))."""
    hint = (f"kiểm tra token (biến môi trường {TOKEN_ENV} hoặc --token) có đúng là token đã nạp "
            "bằng tools/provision_secrets.py không"
            if has_token else
            f"mũ cần token: đặt biến môi trường {TOKEN_ENV} hoặc dùng --token (token nạp bằng "
            "tools/provision_secrets.py --mcp-token / --generate-mcp-token)")
    status = _http_status(exc)
    if status in (401, 403):
        reason = "sai token" if has_token else "thiếu token"
        return f"Mũ từ chối kết nối {shown_url} (HTTP {status}, {reason}): {hint}.", EXIT_REFUSED
    if status is not None:
        return (f"{shown_url} trả HTTP {status} thay vì mở WebSocket: kiểm tra --host/--port/--path "
                "và CONFIG_JARVIS_LOCAL_MCP_WS.", 2)
    return (f"Mũ đóng kết nối {shown_url} trong lúc bắt tay WebSocket ({detail}). Thường là bị từ "
            f"chối: {hint}; hoặc server MCP của mũ đang bận/khởi động lại.", EXIT_REFUSED)


async def run(args: argparse.Namespace) -> int:
    url, has_token = build_url(args)
    shown_url = redact_url(url)
    secrets = token_forms(url)
    try:
        async with ws_connect(url, max_size=None, ping_interval=None,
                              open_timeout=args.timeout, **_NO_PROXY) as ws:
            client = McpClient(ws, args.timeout, args.verbose)
            info = await client.initialize()
            server = info.get("serverInfo", {}) if isinstance(info, dict) else {}

            if args.call:
                tool = args.call[0]
                arguments = parse_json_object(args.call[1]) if len(args.call) > 1 else {}
                print(await client.call_tool(tool, arguments))
                return 0
            if args.list:
                print(format_tools(await client.list_tools(with_user_tools=True)))
                return 0

            print(f"Đã kết nối {shown_url}: {server.get('name', '?')} {server.get('version', '')}")
            tools = await client.list_tools(with_user_tools=True)
            names = {t.get("name") for t in tools}
            missing = {"self.servo.calibrate", "self.servo.save_calibration"} - names
            print(f"{len(tools)} tool ({sum(_is_user_only(t) for t in tools)} user-only).")
            if missing:
                print(f"Cảnh báo: thiếu tool {', '.join(sorted(missing))}")
            await Console(client).run()
            return 0
    # Library messages may quote the URL, so every str(exc) below goes through scrub().
    except InvalidHandshake as exc:
        message, code = describe_refusal(exc, shown_url, has_token, scrub(str(exc), secrets))
        print(message, file=sys.stderr)
        return code
    except ConnectionClosed as exc:
        print(f"Mũ đóng kết nối {shown_url} ({scrub(str(exc), secrets)}). Nếu xảy ra ngay sau "
              f"khi kết nối: kiểm tra token ({TOKEN_ENV} / --token).", file=sys.stderr)
        return EXIT_REFUSED
    except InvalidURI as exc:
        print(f"URL không hợp lệ {shown_url}: {scrub(str(exc.msg), secrets)}", file=sys.stderr)
        return 1
    except (OSError, TimeoutError) as exc:
        print(f"Không kết nối được {shown_url}: {scrub(str(exc), secrets)}", file=sys.stderr)
        return 2
    except (McpError, ValueError, json.JSONDecodeError) as exc:
        print(f"Lỗi: {scrub(str(exc), secrets)}", file=sys.stderr)
        return 1
    except WebSocketException as exc:
        print(f"Lỗi WebSocket với {shown_url}: {type(exc).__name__}: "
              f"{scrub(str(exc), secrets)}", file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Hiệu chỉnh servo / gọi MCP tool trên Jarvis Helmet qua ws://<ip>:8080/ws",
    )
    parser.add_argument("--host", help="IP hoặc tên máy của mũ (env JARVIS_HELMET_HOST)")
    parser.add_argument("--port", type=int, default=8080, help="cổng (mặc định 8080)")
    parser.add_argument("--path", default="/ws", help="đường dẫn WebSocket (mặc định /ws)")
    parser.add_argument("--url", help="URL đầy đủ, thay cho --host/--port/--path")
    parser.add_argument("--token",
                        help=f"token truy cập của mũ, gửi dạng ?token=... (mặc định env {TOKEN_ENV}; "
                             "nên dùng env để token không nằm trong lịch sử shell)")
    parser.add_argument("--timeout", type=float, default=10.0, help="giây chờ mỗi phản hồi")
    parser.add_argument("--call", nargs="+", metavar=("TOOL", "JSON"),
                        help="gọi một tool rồi thoát, ví dụ --call self.mask.get_state")
    parser.add_argument("--list", action="store_true", help="liệt kê tool (kể cả user-only)")
    parser.add_argument("-v", "--verbose", action="store_true", help="in JSON gửi / nhận")
    args = parser.parse_args(argv)
    if args.call and len(args.call) > 2:
        # Allow unquoted JSON split by the shell: --call tool {"a": 1}
        args.call = [args.call[0], " ".join(args.call[1:])]
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
