#!/usr/bin/env python3
"""Jarvis Helmet device simulator for end-to-end tests (IT1).

Speaks the same wire protocol as the helmet firmware (xiaozhi-esp32 v2.5.0 + board
``jarvis-helmet``):

1. OTA check: ``POST <ota-url>`` with the firmware headers and system-info JSON body,
   which returns the WebSocket URL and token (firmware ``main/ota.cc``).
2. WebSocket, binary protocol v1 (raw Opus frames) with ``Authorization: Bearer``,
   ``Protocol-Version``, ``Device-Id``, ``Client-Id`` (+ optional Cloudflare Access headers)
   and the device ``hello`` (``main/protocols/websocket_protocol.cc``).
3. MCP server: answers ``initialize``, ``tools/list`` (8000-byte pages with ``nextCursor``)
   and ``tools/call`` byte-for-byte like ``main/mcp_server.{h,cc}``, with the Jarvis tool
   list of docs/interfaces.md. ``self.sequence.run`` steps are validated with
   ``server/tasks/taskslib.py`` when importable, otherwise with a built-in validator.
4. Audio: a 16 kHz mono WAV is sent as Opus 60 ms frames in real time (``listen`` start
   ``auto``/``manual`` and ``stop``), or a text is injected with ``listen`` ``detect``.
   The downlink Opus is decoded and saved as a WAV.
5. Latency from end of speech to: ``stt``, first TTS audio frame, first MCP ``tools/call``.

Usage: ``python device_sim.py --help``. Documentation (Vietnamese): README.md and
scenarios.md in this directory.

Exit codes: 0 = all assertions passed, 1 = an assertion failed, 2 = usage error,
3 = connection or protocol error.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import dataclasses
import datetime as _dt
import hashlib
import hmac
import json
import math
import os
import re
import statistics
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import uuid as _uuid
import wave
from pathlib import Path
from typing import Any, Callable, Optional

# --------------------------------------------------------------------------------------
# Firmware identity and protocol constants
# --------------------------------------------------------------------------------------

SIM_VERSION = "1.0.0"
FW_VERSION = "2.5.0"             # PROJECT_VER in firmware/CMakeLists.txt
PROJECT_NAME = "xiaozhi"         # project() name, reported in application.name
BOARD_TYPE = "jarvis-helmet"     # config.json "type" -> BOARD_TYPE
BOARD_NAME = "jarvis-helmet"     # build.py --name -> BOARD_NAME (User-Agent, MCP serverInfo)
IDF_VERSION = "v6.1"
LANGUAGE = "vi-VN"               # Lang::CODE for --language vi-VN
DEFAULT_MAC = "02:4a:48:4d:00:01"  # locally administered address
UPLINK_RATE = 16000
OPUS_FRAME_MS = 60               # OPUS_FRAME_DURATION_MS
MCP_MAX_PAYLOAD = 8000           # McpServer::GetToolsList max_payload_size
HELLO_TIMEOUT_S = 10.0           # WebsocketProtocol::OpenAudioChannel waits 10 s
SUPPORTED_OPUS_RATES = (8000, 12000, 16000, 24000, 48000)

E2E_DIR = Path(__file__).resolve().parent
REPO_ROOT = E2E_DIR.parents[2]
DEFAULT_AUDIO_DIR = E2E_DIR / "audio"
DEFAULT_OUT_DIR = E2E_DIR / "out"
DEFAULT_TASKS_DIR = REPO_ROOT / "server" / "tasks"

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_ERROR = 0, 1, 2, 3


class SimError(Exception):
    """Connection or protocol failure: the run cannot continue (exit code 3)."""


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------

def now_ms() -> int:
    return int(time.time() * 1000)


def cj(value: str) -> str:
    """Prints a string the way cJSON does (UTF-8 kept, quotes/backslash/control escaped)."""
    return json.dumps(value, ensure_ascii=False)


def compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def utf8_len(s: str) -> int:
    return len(s.encode("utf-8"))


def sanitize_message(s: str) -> str:
    """Python port of helmet::SanitizeMessage (helmet_common.h)."""
    out = []
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append("'")
        elif ch == "\\":
            out.append("/")
        elif o < 0x20 or o == 0x7F:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def cjson_valueint(value: float) -> int:
    """cJSON valueint: truncation toward zero, saturated to the int32 range."""
    if value >= 2147483647:
        return 2147483647
    if value <= -2147483648:
        return -2147483648
    return int(math.trunc(value))


def is_json_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def cjson_get(obj: dict, key: str) -> tuple[bool, Any]:
    """cJSON_GetObjectItem: case-insensitive key lookup, first match wins."""
    if key in obj:
        return True, obj[key]
    low = key.lower()
    for k, v in obj.items():
        if isinstance(k, str) and k.lower() == low:
            return True, v
    return False, None


def percentile(values: list[float], pct: float) -> Optional[float]:
    """Linear interpolation between closest ranks (same as numpy's default)."""
    if not values:
        return None
    data = sorted(values)
    if len(data) == 1:
        return float(data[0])
    rank = (len(data) - 1) * pct / 100.0
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return float(data[lo])
    return data[lo] + (data[hi] - data[lo]) * (rank - lo)


def stats_of(values: list[float]) -> dict:
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "median": round(statistics.median(vals), 1),
        "p90": round(percentile(vals, 90), 1),
        "min": round(min(vals), 1),
        "max": round(max(vals), 1),
    }


def xz_token(auth_key: str, client_id: str, device_id: str, ts: Optional[int] = None) -> str:
    """Token as issued by xiaozhi-server v0.9.7 core/auth.py (HMAC-SHA256, base64url)."""
    ts = int(time.time()) if ts is None else ts
    sig = hmac.new(auth_key.encode("utf-8"), f"{client_id}|{device_id}|{ts}".encode("utf-8"),
                   hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=") + f".{ts}"


def cf_access_headers(env: Optional[dict] = None) -> list[tuple[str, str]]:
    """Cloudflare Access service-token headers (D13), taken from the environment."""
    env = os.environ if env is None else env
    cid = env.get("CF_ACCESS_CLIENT_ID", "").strip()
    secret = env.get("CF_ACCESS_CLIENT_SECRET", "").strip()
    if cid and secret:
        return [("CF-Access-Client-Id", cid), ("CF-Access-Client-Secret", secret)]
    return []


# --------------------------------------------------------------------------------------
# Device identity, OTA request/response
# --------------------------------------------------------------------------------------

@dataclasses.dataclass
class DeviceIdentity:
    mac: str = DEFAULT_MAC
    uuid: str = ""
    board_type: str = BOARD_TYPE
    board_name: str = BOARD_NAME
    fw_version: str = FW_VERSION
    language: str = LANGUAGE
    ssid: str = "JarvisLab"
    rssi: int = -52
    channel: int = 6
    ip: str = "192.168.1.99"

    def __post_init__(self) -> None:
        self.mac = self.mac.lower()
        if not re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", self.mac):
            raise ValueError(f"invalid MAC address: {self.mac}")
        if not self.uuid:
            self.uuid = str(_uuid.uuid5(_uuid.NAMESPACE_URL, "jarvis-helmet-sim/" + self.mac))

    @property
    def user_agent(self) -> str:
        return f"{self.board_name}/{self.fw_version}"  # SystemInfo::GetUserAgent


# partitions_jarvis_16m.csv (contract §3.2): label, type, subtype, address, size
PARTITIONS = [
    ("nvs", 1, 2, 0x9000, 0x4000),
    ("otadata", 1, 0, 0xD000, 0x2000),
    ("phy_init", 1, 1, 0xF000, 0x1000),
    ("helmet", 1, 2, 0x10000, 0xC000),
    ("jsecrets", 1, 2, 0x1C000, 0x4000),
    ("ota_0", 0, 0x10, 0x20000, 0x3F0000),
    ("ota_1", 0, 0x11, 0x410000, 0x3F0000),
    ("assets", 1, 0x82, 0x800000, 0x800000),
]


def build_ota_headers(dev: DeviceIdentity, env: Optional[dict] = None) -> list[tuple[str, str]]:
    """Headers of Ota::SetupHttp (ota.cc:55-72) plus the D13 Cloudflare Access headers."""
    headers = [
        ("Activation-Version", "1"),  # no serial number in efuse
        ("Device-Id", dev.mac),
        ("Client-Id", dev.uuid),
        ("User-Agent", dev.user_agent),
        ("Accept-Language", dev.language),
        ("Content-Type", "application/json"),
    ]
    return headers + cf_access_headers(env)


def build_system_info(dev: DeviceIdentity, free_heap: int = 98304) -> dict:
    """Body of the OTA check: Board::GetSystemInfoJson + WifiBoard::GetBoardJson."""
    return {
        "version": 2,
        "language": dev.language,
        "flash_size": 16 * 1024 * 1024,
        "minimum_free_heap_size": str(free_heap),  # a string in the firmware
        "mac_address": dev.mac,
        "uuid": dev.uuid,
        "chip_model_name": "esp32s3",
        "chip_info": {"model": 9, "cores": 2, "revision": 2, "features": 18},
        "application": {
            "name": PROJECT_NAME,
            "version": dev.fw_version,
            "compile_time": "Oct  6 2026T00:00:00Z",
            "idf_version": IDF_VERSION,
            "elf_sha256": hashlib.sha256(f"{dev.board_name}/{dev.fw_version}".encode()).hexdigest(),
        },
        "partition_table": [
            {"label": label, "type": ptype, "subtype": sub, "address": addr, "size": size}
            for label, ptype, sub, addr, size in PARTITIONS
        ],
        "ota": {"label": "ota_0"},
        "display": {"monochrome": False, "width": 240, "height": 240},
        "board": {
            "type": dev.board_type,
            "name": dev.board_name,
            "manufacturer": "",
            "ssid": dev.ssid,
            "rssi": dev.rssi,
            "channel": dev.channel,
            "ip": dev.ip,
            "mac": dev.mac,
        },
    }


@dataclasses.dataclass
class OtaResult:
    status: int
    elapsed_ms: float
    ws_url: str = ""
    token: str = ""
    ws_version: Optional[int] = None
    raw: Any = None
    warnings: list = dataclasses.field(default_factory=list)


def parse_ota_response(status: int, body: str, elapsed_ms: float = 0.0) -> OtaResult:
    """Interprets an OTA reply like Ota::CheckVersion + Application (MQTT vs WebSocket)."""
    res = OtaResult(status=status, elapsed_ms=elapsed_ms)
    if status != 200:
        # HttpClient does not follow redirects: Cloudflare Access answers 302/401/403.
        raise SimError(f"OTA HTTP {status} (firmware treats any non-200 as failure)")
    try:
        data = json.loads(body)
    except ValueError as e:
        raise SimError(f"OTA reply is not JSON: {body[:200]!r}") from e
    res.raw = data
    if not isinstance(data, dict):
        raise SimError("OTA reply is not a JSON object")
    if data.get("success") is False:
        raise SimError(f"OTA request rejected: {data.get('message')} (missing Device-Id/Client-Id?)")
    if isinstance(data.get("mqtt"), dict):
        raise SimError("OTA returned an 'mqtt' section: the firmware would switch to MQTT "
                       "(keep server.mqtt_gateway empty)")
    ws = data.get("websocket")
    if not isinstance(ws, dict):
        raise SimError("OTA reply has no 'websocket' section: the firmware would fall back to MQTT")
    res.ws_url = ws.get("url", "") if isinstance(ws.get("url"), str) else ""
    res.token = ws.get("token", "") if isinstance(ws.get("token"), str) else ""
    if isinstance(ws.get("version"), int) and not isinstance(ws.get("version"), bool):
        res.ws_version = ws["version"]
        if res.ws_version not in (0, 1):
            res.warnings.append(f"OTA sets websocket.version={res.ws_version}; server v0.9.7 only "
                                "parses binary protocol v1 on WebSocket")
    if not res.ws_url.startswith(("ws://", "wss://")):
        raise SimError(f"OTA websocket.url is not a ws:// or wss:// URL: {res.ws_url!r}")
    st = data.get("server_time")
    if isinstance(st, dict):
        if not is_json_number(st.get("timezone_offset", 0)):
            res.warnings.append("server_time.timezone_offset is not a number (firmware ignores it)")
    else:
        res.warnings.append("OTA reply has no server_time section")
    fw = data.get("firmware")
    if isinstance(fw, dict) and fw.get("url"):
        res.warnings.append(f"OTA offers a firmware download ({fw.get('version')}): "
                            "a real helmet would upgrade itself")
    return res


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def ota_check(url: str, dev: DeviceIdentity, timeout: float = 10.0) -> OtaResult:
    body = compact(build_system_info(dev)).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    for k, v in build_ota_headers(dev):
        req.add_header(k, v)
    opener = urllib.request.build_opener(_NoRedirect)
    t0 = time.perf_counter()
    try:
        with opener.open(req, timeout=timeout) as resp:
            status, text = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        raise SimError(f"OTA request to {url} failed: {e}") from e
    return parse_ota_response(status, text, (time.perf_counter() - t0) * 1000)


# --------------------------------------------------------------------------------------
# WebSocket messages (byte-exact with protocol.cc / websocket_protocol.cc)
# --------------------------------------------------------------------------------------

def build_ws_headers(dev: DeviceIdentity, token: str, protocol_version: int = 1,
                     env: Optional[dict] = None) -> list[tuple[str, str]]:
    headers = []
    if token:
        if " " not in token:          # "If token not has a space, add Bearer prefix"
            token = "Bearer " + token
        headers.append(("Authorization", token))
    headers += [
        ("Protocol-Version", str(protocol_version)),
        ("Device-Id", dev.mac),
        ("Client-Id", dev.uuid),
    ]
    return headers + cf_access_headers(env)


DEFAULT_TEXT_FONT = {"bundle": "noto-v1", "charset": "basic", "size": 16, "bpp": 4}


def build_hello(protocol_version: int = 1, aec: bool = False, glyph_push: bool = True,
                text_font: Optional[dict] = None) -> str:
    """WebsocketProtocol::GetHelloMessage + Protocol::AddTextFontCapabilities."""
    features: dict = {}
    if aec:
        features["aec"] = True
    features["mcp"] = True
    features["glyph_push"] = glyph_push
    root: dict = {"type": "hello", "version": protocol_version, "features": features}
    if glyph_push:
        root["text_font"] = dict(text_font or DEFAULT_TEXT_FONT)
    root["transport"] = "websocket"
    root["audio_params"] = {"format": "opus", "sample_rate": UPLINK_RATE, "channels": 1,
                            "frame_duration": OPUS_FRAME_MS}
    return compact(root)


def _raw_text(text: str) -> str:
    # The firmware pastes the text unescaped; escaping only changes texts that would
    # otherwise produce invalid JSON (quotes, backslashes, control characters).
    return cj(text)[1:-1]


def msg_listen_start(session_id: str, mode: str) -> str:
    if mode not in ("auto", "manual", "realtime"):
        raise ValueError(f"invalid listening mode: {mode}")
    return ('{"session_id":"' + session_id + '","type":"listen","state":"start","mode":"'
            + mode + '"}')


def msg_listen_stop(session_id: str) -> str:
    return '{"session_id":"' + session_id + '","type":"listen","state":"stop"}'


def msg_listen_detect(session_id: str, text: str) -> str:
    return ('{"session_id":"' + session_id + '","type":"listen","state":"detect","text":"'
            + _raw_text(text) + '"}')


def msg_abort(session_id: str, wake_word: bool = False) -> str:
    message = '{"session_id":"' + session_id + '","type":"abort"'
    if wake_word:
        message += ',"reason":"wake_word_detected"'
    return message + "}"


def msg_mcp(session_id: str, payload: str) -> str:
    return '{"session_id":"' + session_id + '","type":"mcp","payload":' + payload + "}"


# --------------------------------------------------------------------------------------
# MCP server simulation (mcp_server.h / mcp_server.cc)
# --------------------------------------------------------------------------------------

BOOLEAN, INTEGER, STRING = "boolean", "integer", "string"
_NO_DEFAULT = object()


class Property:
    """Mirror of the firmware Property (types boolean/integer/string)."""

    def __init__(self, name: str, ptype: str, default: Any = _NO_DEFAULT,
                 minimum: Optional[int] = None, maximum: Optional[int] = None,
                 max_length: Optional[int] = None) -> None:
        if ptype not in (BOOLEAN, INTEGER, STRING):
            raise ValueError(ptype)
        if (minimum is not None or maximum is not None) and ptype != INTEGER:
            raise ValueError("MCP property range is only valid for integer properties")
        if max_length is not None and ptype != STRING:
            raise ValueError("Max length only applies to string properties")
        if default is not _NO_DEFAULT and minimum is not None and maximum is not None:
            if not minimum <= default <= maximum:
                raise ValueError("MCP property default value is outside its declared range")
        self.name, self.type = name, ptype
        self.default = default
        self.minimum, self.maximum, self.max_length = minimum, maximum, max_length

    @property
    def has_default(self) -> bool:
        return self.default is not _NO_DEFAULT

    def validate(self, value: Any) -> str:
        """Property::Validate: '' on success, otherwise the firmware's error text."""
        if self.type == INTEGER:
            if self.minimum is not None and value < self.minimum:
                return (f"Property '{self.name}': value {value} is below minimum "
                        f"{self.minimum}")
            if self.maximum is not None and value > self.maximum:
                return f"Property '{self.name}': value {value} exceeds maximum {self.maximum}"
        elif self.type == STRING and self.max_length is not None:
            n = utf8_len(value)
            if n > self.max_length:
                return (f"Property '{self.name}': string length {n} exceeds maximum "
                        f"{self.max_length}")
        return ""

    def to_json(self) -> str:
        parts = ['{"type":"' + self.type + '"']
        if self.type == BOOLEAN:
            if self.has_default:
                parts.append(',"default":' + ("true" if self.default else "false"))
        elif self.type == INTEGER:
            if self.has_default:
                parts.append(',"default":' + str(int(self.default)))
            if self.minimum is not None:
                parts.append(',"minimum":' + str(self.minimum))
            if self.maximum is not None:
                parts.append(',"maximum":' + str(self.maximum))
        else:
            if self.has_default:
                parts.append(',"default":' + cj(self.default))
            if self.max_length is not None:
                parts.append(',"maxLength":' + str(self.max_length))
        parts.append("}")
        return "".join(parts)


# A tool callback gets the argument values (defaults applied) and returns
# (True, text) on success or (False, error message) for std::unexpected(...).
ToolCallback = Callable[[dict], tuple]


class McpTool:
    def __init__(self, name: str, description: str, properties: list[Property],
                 callback: ToolCallback, user_only: bool = False) -> None:
        self.name, self.description = name, description
        self.properties = list(properties)
        self.callback = callback
        self.user_only = user_only

    def required(self) -> list[str]:
        return [p.name for p in self.properties if not p.has_default]

    def to_json(self) -> str:
        """McpTool::to_json: name, description, inputSchema{type,properties,required}, annotations."""
        props = ",".join(cj(p.name) + ":" + p.to_json() for p in self.properties)
        parts = ['{"name":', cj(self.name), ',"description":', cj(self.description),
                 ',"inputSchema":{"type":"object","properties":{', props, "}"]
        required = self.required()
        if required:
            parts += [',"required":[', ",".join(cj(r) for r in required), "]"]
        parts.append("}")
        if self.user_only:
            parts.append(',"annotations":{"audience":["user"]}')
        parts.append("}")
        return "".join(parts)


class McpServerSim:
    """Firmware MCP dispatcher. handle() returns the JSON-RPC response payload or None."""

    def __init__(self, tools: list[McpTool], board_name: str = BOARD_NAME,
                 version: str = FW_VERSION, max_payload: int = MCP_MAX_PAYLOAD) -> None:
        self.tools: list[McpTool] = []
        for tool in tools:
            if any(t.name == tool.name for t in self.tools):
                continue  # "Tool %s already added"
            self.tools.append(tool)
        self.board_name, self.version, self.max_payload = board_name, version, max_payload
        self.vision: Optional[dict] = None
        self.log: list[dict] = []          # every request with its reply (for the summary)
        self.warnings: list[str] = []

    def tool(self, name: str) -> Optional[McpTool]:
        return next((t for t in self.tools if t.name == name), None)

    # JSON-RPC envelopes (no escaping of error messages, exactly like the firmware)
    @staticmethod
    def reply_result(rid: int, result: str) -> str:
        return '{"jsonrpc":"2.0","id":' + str(rid) + ',"result":' + result + "}"

    @staticmethod
    def reply_error(rid: int, code: int, message: str) -> str:
        return ('{"jsonrpc":"2.0","id":' + str(rid) + ',"error":{"code":' + str(code)
                + ',"message":"' + message + '"}}')

    def handle(self, payload: Any) -> Optional[str]:
        reply = self._handle(payload)
        if reply is not None:
            try:
                json.loads(reply)
            except ValueError:
                self.warnings.append("the firmware would send invalid JSON (unescaped error "
                                     f"message): {reply[:120]}")
        return reply

    def _handle(self, msg: Any) -> Optional[str]:
        if not isinstance(msg, dict):
            return None
        if msg.get("jsonrpc") != "2.0":
            return None  # "Invalid JSONRPC version"
        method = msg.get("method")
        if not isinstance(method, str):
            return None  # "Missing method"
        if method.startswith("notifications"):
            return None
        params = msg.get("params")
        if "params" in msg and not isinstance(params, dict):
            return None  # "Invalid params for method"
        rid_raw = msg.get("id")
        if not is_json_number(rid_raw):
            return None  # "Invalid id for method"
        rid = cjson_valueint(rid_raw)

        if method == "initialize":
            if isinstance(params, dict):
                caps = params.get("capabilities")
                if isinstance(caps, dict) and isinstance(caps.get("vision"), dict):
                    self.vision = caps["vision"]
            result = ('{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},'
                      '"serverInfo":{"name":"' + self.board_name + '","version":"'
                      + self.version + '"}}')
            return self.reply_result(rid, result)
        if method == "tools/list":
            cursor, with_user = "", False
            if isinstance(params, dict):
                if isinstance(params.get("cursor"), str):
                    cursor = params["cursor"]
                if isinstance(params.get("withUserTools"), bool):
                    with_user = params["withUserTools"]
            return self.tools_list(rid, cursor, with_user)
        if method == "tools/call":
            if not isinstance(params, dict):
                return self.reply_error(rid, -32602, "Missing params")
            name = params.get("name")
            if not isinstance(name, str):
                return self.reply_error(rid, -32602, "Missing tool name")
            has_args = "arguments" in params
            args = params.get("arguments")
            if has_args and not isinstance(args, dict):
                return self.reply_error(rid, -32602, "Invalid arguments: expected object")
            return self.tool_call(rid, name, args if isinstance(args, dict) else None)
        return self.reply_error(rid, -32601, "Method not implemented: " + method)

    def tools_list(self, rid: int, cursor: str, with_user: bool) -> str:
        """McpServer::GetToolsList: pages of at most max_payload bytes, nextCursor."""
        out = '{"tools":['
        found = cursor == ""
        next_cursor = ""
        for tool in self.tools:
            if not found:
                if tool.name == cursor:
                    found = True
                else:
                    continue
            if not with_user and tool.user_only:
                continue
            tool_json = tool.to_json() + ","
            if utf8_len(out) + utf8_len(tool_json) + 30 > self.max_payload:
                next_cursor = tool.name
                break
            out += tool_json
        if out.endswith(","):
            out = out[:-1]
        if out.endswith("[") and self.tools:
            return self.reply_error(rid, -32603, "Failed to add tool " + next_cursor
                                    + " because of payload size limit")
        out += "]}" if not next_cursor else '],"nextCursor":"' + next_cursor + '"}'
        return self.reply_result(rid, out)

    def tool_call(self, rid: int, name: str, args: Optional[dict]) -> str:
        """McpServer::DoToolCall + McpTool::Call."""
        tool = self.tool(name)
        if tool is None:
            return self.reply_error(rid, -32602, "Unknown tool: " + name)
        values: dict = {}
        for prop in tool.properties:
            found, error = False, ""
            if args is not None:
                present, value = cjson_get(args, prop.name)
                if prop.type == BOOLEAN and isinstance(value, bool):
                    values[prop.name], found = value, True
                elif prop.type == INTEGER and is_json_number(value):
                    iv = cjson_valueint(value)
                    error, found = prop.validate(iv), True
                    values[prop.name] = iv
                elif prop.type == STRING and isinstance(value, str):
                    error, found = prop.validate(value), True
                    values[prop.name] = value
                elif present:
                    return self.reply_error(rid, -32602, "Invalid type for argument: " + prop.name)
            if found and error:
                return self.reply_error(rid, -32602, error)
            if not found:
                if not prop.has_default:
                    return self.reply_error(rid, -32602,
                                            "Missing required argument: " + prop.name)
                values[prop.name] = prop.default
        try:
            ok, text = tool.callback(values)
        except Exception as e:  # a crash in the simulated callback is reported, not fatal
            ok, text = False, sanitize_message(f"simulator error: {e}")
        if not ok:
            return self.reply_error(rid, -32603, text)
        result = '{"content":[{"type":"text","text":' + cj(str(text)) + '}],"isError":false}'
        return self.reply_result(rid, result)


# --------------------------------------------------------------------------------------
# Sequence validation (interfaces.md §3, contract D23) and the taskslib adapter
# --------------------------------------------------------------------------------------

EYE_EFFECTS = ("breathe", "blink", "flicker", "power_up", "fade_out")
STEP_TYPES = ("mask", "servo", "eyes", "output", "delay", "display", "sound")
MAX_STEPS = 64
MAX_SEQUENCE_MS = 60000
DEFAULT_SPEED = 70
MASK_BASE_MS = 800


def eye_effect_default_ms(effect: Any) -> int:
    """Effect length when duration_ms is 0 or absent (D26), like helmet::EyeEffectDefaultMs."""
    return {"breathe": 2500, "flicker": 600}.get(effect, 1000)


def mask_full_travel_ms(speed: int) -> int:
    return MASK_BASE_MS * 100 // max(10, min(100, speed))


def mask_move_ms(from_pos: int, to_pos: int, speed: int) -> int:
    distance = abs(max(0, min(100, to_pos)) - max(0, min(100, from_pos)))
    if distance == 0:
        return 0
    return max(mask_full_travel_ms(speed) * distance // 100, 150)


def _as_int(value: Any) -> Optional[int]:
    """JSON integer as the cJSON validator sees it: numbers with an integral value."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (not math.isfinite(value) or value != int(value)):
        return None
    return int(value)


def _check_fields(obj: dict, allowed: tuple) -> Optional[str]:
    for key in obj:
        if key not in allowed:
            return f"unknown field: {key}"
    return None


def _int_field(obj: dict, key: str, lo: int, hi: int, required: bool) -> tuple:
    if key not in obj:
        return (None, f"missing field: {key}") if required else (None, None)
    v = _as_int(obj[key])
    if v is None:
        return None, f"{key} must be an integer"
    if not lo <= v <= hi:
        return None, f"{key} out of range {lo}..{hi}"
    return v, None


def estimate_step_ms(step_type: str, body: Any) -> int:
    if step_type == "mask":
        speed = _as_int(body.get("speed")) if "speed" in body else DEFAULT_SPEED
        return mask_full_travel_ms(speed or DEFAULT_SPEED)
    if step_type == "servo":
        return _as_int(body.get("duration_ms", 0)) or 0
    if step_type == "eyes" and "effect" in body:
        if body.get("effect") == "breathe":
            return 0
        return _as_int(body.get("duration_ms", 0)) or eye_effect_default_ms(body.get("effect"))
    if step_type == "delay":
        return _as_int(body) or 0
    return 0


def _validate_step(step_type: str, body: Any) -> Optional[str]:
    if step_type == "delay":
        v = _as_int(body)
        if v is None:
            return "delay must be an integer number of ms"
        if not 0 <= v <= 10000:
            return "delay out of range 0..10000"
        return None
    if not isinstance(body, dict):
        return f"{step_type} must be an object"
    if step_type == "mask":
        err = _check_fields(body, ("action", "position", "speed"))
        if err:
            return err
        if ("action" in body) == ("position" in body):
            return "mask needs exactly one of action or position"
        if "action" in body and body["action"] not in ("open", "close"):
            return "action must be open or close"
        if "position" in body:
            _, err = _int_field(body, "position", 0, 100, True)
            if err:
                return err
        _, err = _int_field(body, "speed", 10, 100, False)
        return err
    if step_type == "servo":
        err = _check_fields(body, ("id", "angle", "duration_ms"))
        for key, lo, hi, req in (("id", 0, 1, True), ("angle", 0, 180, True),
                                 ("duration_ms", 0, 5000, False)):
            if err:
                break
            _, err = _int_field(body, key, lo, hi, req)
        return err
    if step_type == "eyes":
        err = _check_fields(body, ("on", "brightness", "effect", "duration_ms"))
        if err:
            return err
        if ("on" in body) == ("effect" in body):
            return "eyes needs either on or effect"
        if "on" in body:
            if not isinstance(body["on"], bool):
                return "on must be a boolean"
            if "duration_ms" in body:
                return "duration_ms only applies to effect"
            _, err = _int_field(body, "brightness", 0, 100, False)
            return err
        if "brightness" in body:
            return "brightness only applies to on"
        if body["effect"] not in EYE_EFFECTS:
            return "unknown effect: " + str(body["effect"])
        ms, err = _int_field(body, "duration_ms", 0, 10000, False)
        if err is None and ms is not None and 0 < ms < 100:
            return "duration_ms must be 0 (effect default) or 100..10000"
        return err
    if step_type == "output":
        err = _check_fields(body, ("on",))
        if err:
            return err
        if not isinstance(body.get("on"), bool):
            return "on must be a boolean"
        return None
    if step_type == "display":
        err = _check_fields(body, ("title", "text", "seconds"))
        if err:
            return err
        for key, limit in (("title", 24), ("text", 200)):
            if not isinstance(body.get(key), str):
                return f"{key} must be a string"
            if len(body[key]) > limit:
                return f"{key} longer than {limit} characters"
        _, err = _int_field(body, "seconds", 1, 30, False)
        return err
    if step_type == "sound":
        err = _check_fields(body, ("name",))
        if err:
            return err
        name = body.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_]{1,32}", name):
            return "sound name must match [a-z0-9_]{1,32}"
        return None
    return "unknown step type: " + step_type


def builtin_validate_steps(steps: Any) -> tuple:
    """Validates a *parsed* step list: (ok, error, estimated_ms).

    Errors use 'invalid step #n: ...' (n from 1, D23); top-level errors mention 'array',
    '64' or '60000' like tools/tests/fixtures/sequences/README.md requires.
    """
    if not isinstance(steps, list):
        return False, "steps must be a JSON array", 0
    if len(steps) > MAX_STEPS:
        return False, f"too many steps: {len(steps)} (max {MAX_STEPS})", 0
    total = 0
    for i, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or len(step) != 1:
            return False, f"invalid step #{i}: a step is an object with exactly one key", 0
        (step_type, body), = step.items()
        if step_type not in STEP_TYPES:
            return False, sanitize_message(f"invalid step #{i}: unknown step type: {step_type}"), 0
        err = _validate_step(step_type, body)
        if err:
            return False, sanitize_message(f"invalid step #{i}: {err}"), 0
        total += estimate_step_ms(step_type, body)
    if total > MAX_SEQUENCE_MS:
        return False, f"sequence too long: estimated {total} ms (max {MAX_SEQUENCE_MS})", total
    return True, None, total


# server/tasks/taskslib.py (SV-D): check_steps(steps) -> SequenceCheck(ok, estimated_ms,
# error, ...) is preferred; validate_steps(steps) -> int raises StepError.
TASKSLIB_VALIDATORS = ("check_steps", "validate_steps", "validate_sequence")


def _interpret_validator_result(result: Any) -> tuple:
    """Maps common validator return conventions to (ok, error)."""
    if hasattr(result, "ok") and hasattr(result, "error"):  # e.g. taskslib.SequenceCheck
        return bool(result.ok), (None if result.ok else str(result.error or "invalid steps"))
    if result is None or result is True or result == "" or result == [] or result == ():
        return True, None
    if result is False:
        return False, "invalid steps"
    if isinstance(result, str):
        return False, result
    if isinstance(result, tuple) and result and isinstance(result[0], bool):
        ok = result[0]
        err = next((x for x in result[1:] if isinstance(x, str) and x), None)
        return ok, (None if ok else (err or "invalid steps"))
    if isinstance(result, list):
        if all(isinstance(x, dict) and len(x) == 1 and next(iter(x)) in STEP_TYPES
               for x in result):
            return True, None  # a normalized step list
        msgs = [str(x.get("message") or x.get("error") or x) if isinstance(x, dict) else str(x)
                for x in result if x]
        return (not msgs), (msgs[0] if msgs else None)
    if isinstance(result, dict):
        if "valid" in result or "ok" in result:
            ok = bool(result.get("valid", result.get("ok")))
            return ok, (None if ok else str(result.get("error") or result.get("errors")
                                            or "invalid steps"))
        if "error" in result:
            return (not result["error"]), (str(result["error"]) if result["error"] else None)
    return True, None  # e.g. a normalized step list


class SequenceValidator:
    """Validates self.sequence.run steps with taskslib when available (else built-in)."""

    def __init__(self, mode: str = "auto", tasks_dir: Optional[Path] = None) -> None:
        self.name = "builtin"
        self._fn: Optional[Callable] = None
        self.load_error: Optional[str] = None
        if mode == "builtin":
            return
        tasks_dir = Path(tasks_dir) if tasks_dir else DEFAULT_TASKS_DIR
        try:
            if str(tasks_dir) not in sys.path:
                sys.path.insert(0, str(tasks_dir))
            import taskslib  # type: ignore  # noqa: WPS433 (optional dependency)
            for fn_name in TASKSLIB_VALIDATORS:
                fn = getattr(taskslib, fn_name, None)
                if callable(fn):
                    self._fn, self.name = fn, f"taskslib.{fn_name}"
                    break
            else:
                self.load_error = ("taskslib has none of " + ", ".join(TASKSLIB_VALIDATORS))
        except Exception as e:  # ImportError or an error inside taskslib
            self.load_error = f"taskslib not importable from {tasks_dir}: {e}"
        if self._fn is None and mode == "taskslib":
            raise SimError(self.load_error or "taskslib validator not found")

    def validate(self, steps_json: str) -> tuple:
        """Returns (ok, error_message). The message is JSON-safe (sanitized)."""
        try:
            steps = json.loads(steps_json)
        except ValueError:
            return False, "steps is not valid JSON"
        if self._fn is None:
            ok, err, _ = builtin_validate_steps(steps)
            return ok, err
        try:
            ok, err = _interpret_validator_result(self._fn(steps))
        except Exception as e:  # validators that raise on invalid input
            ok, err = False, str(e)
        return ok, (sanitize_message(err) if err else None)


# --------------------------------------------------------------------------------------
# Simulated helmet state + the Jarvis tool list
# --------------------------------------------------------------------------------------

# Built-in sequences (sequence_runner.h BuiltinSequence, same steps as tasks.yaml)
BUILTIN_SEQUENCES = {
    "open_mask": [{"eyes": {"effect": "fade_out", "duration_ms": 300}},
                  {"mask": {"action": "open", "speed": 80}}],
    "close_mask": [{"mask": {"action": "close"}}, {"delay": 300},
                   {"eyes": {"effect": "power_up", "duration_ms": 1200}}],
}


class HelmetSim:
    """Actuator state that evolves in (monotonic) time, enough for get_state/get_status."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.boot = clock()
        self.mask_from = self.mask_to = 0
        self.mask_t0 = self.mask_t1 = self.boot
        self.eyes_on, self.eyes_brightness, self.eyes_effect = False, 0, ""
        self.output_on = False
        self.seq_name, self.seq_until = "", 0.0
        self.saved: dict = {k: compact(v) for k, v in BUILTIN_SEQUENCES.items()}
        self.calibration = {0: (80, 100), 1: (100, 80)}
        self.volume, self.brightness, self.theme = 70, 100, "dark"
        self.free_heap = 98304

    def mask_position(self) -> int:
        t = self.clock()
        if t >= self.mask_t1 or self.mask_t1 <= self.mask_t0:
            return self.mask_to
        frac = (t - self.mask_t0) / (self.mask_t1 - self.mask_t0)
        eased = (1 - math.cos(math.pi * frac)) / 2
        return round(self.mask_from + (self.mask_to - self.mask_from) * eased)

    def mask_state(self) -> dict:
        pos = self.mask_position()
        if self.clock() < self.mask_t1:
            kind = "moving"
        else:
            kind = "closed" if pos == 0 else "open"
        return {"state": kind, "position": pos}

    def move_mask(self, target: int, speed: int, delay_s: float = 0.0) -> float:
        start = self.mask_position()
        dur = mask_move_ms(start, target, speed) / 1000.0
        self.mask_from, self.mask_to = start, target
        self.mask_t0 = self.clock() + delay_s
        self.mask_t1 = self.mask_t0 + dur
        return dur

    def run_steps(self, steps: list, name: str = "") -> None:
        """Applies a validated sequence; the sequence counts as running for its estimate."""
        t = 0.0
        for step in steps:
            (kind, body), = step.items()
            if kind == "mask":
                target = 100 if body.get("action") == "open" else 0
                if "position" in body:
                    target = int(body["position"])
                t += self.move_mask(target, int(body.get("speed", DEFAULT_SPEED)), delay_s=t)
            elif kind == "eyes":
                if "on" in body:
                    self.eyes_on = bool(body["on"])
                    self.eyes_brightness = int(body.get("brightness", 100)) if self.eyes_on else 0
                    self.eyes_effect = ""
                else:
                    effect = body["effect"]
                    self.eyes_effect = effect if effect == "breathe" else ""
                    self.eyes_on = effect != "fade_out"
                    self.eyes_brightness = 100 if self.eyes_on else 0
                    if effect != "breathe":
                        ms = int(body.get("duration_ms", 0)) or eye_effect_default_ms(effect)
                        t += ms / 1000.0
            elif kind == "output":
                self.output_on = bool(body["on"])
            elif kind == "delay":
                t += int(body) / 1000.0
            elif kind == "servo":
                t += int(body.get("duration_ms", 0)) / 1000.0
        self.seq_name, self.seq_until = name, self.clock() + t

    def status_json(self) -> str:
        """helmet_tools::BuildStatusJson layout."""
        running = self.clock() < self.seq_until
        return compact({
            "mask": self.mask_state(),
            "eyes": {"on": self.eyes_on, "brightness": self.eyes_brightness,
                     "effect": self.eyes_effect},
            "output": {"on": self.output_on},
            "sequence": {"running": running, "name": self.seq_name if running else ""},
            "vbus_mv": 5040,
            "rssi": -52,
            "uptime_s": int(self.clock() - self.boot),
            "free_heap": self.free_heap,
        })


# Short bilingual descriptions like the ones FW-B/FW-C register (exact texts may differ).
TOOL_DESCRIPTIONS = {
    "self.sequence.run": (
        "Chạy chuỗi bước JSON trên mũ (mặt nạ, servo, đèn mắt, output, delay, hiển thị, âm "
        "thanh); chạy nền, chuỗi mới hủy chuỗi cũ. / Run a JSON step sequence on the helmet in "
        "the background; a new one cancels the running one. steps: JSON array of single-key "
        "objects, e.g. [{\"mask\":{\"action\":\"open\"}},{\"delay\":300}]."),
    "self.sequence.run_saved": "Chạy chuỗi đã lưu trên mũ (vd. open_mask). / Run a sequence "
                               "saved on the helmet.",
    "self.sequence.save": "Lưu chuỗi bước vào mũ để dùng khi offline. / Save a step sequence "
                          "on the helmet for offline use.",
    "self.sequence.stop": "Dừng chuỗi đang chạy. / Stop the running sequence.",
    "self.display.show_info": "Hiện thẻ thông tin trên HUD (tiêu đề ≤24 ký tự, nội dung ≤200 "
                              "ký tự). / Show an info card on the HUD.",
    "self.system.get_status": "Trạng thái mũ: mặt nạ, đèn mắt, output, điện áp, Wi-Fi, thời "
                              "gian chạy. / Helmet status: mask, eyes, output, voltage, Wi-Fi, "
                              "uptime.",
    "self.mask.open": "Mở mặt nạ / Open the mask (faceplate). Chạy nền / runs in background.",
    "self.mask.close": "Đóng mặt nạ / Close the mask. Chạy nền / runs in background.",
    "self.mask.set_position": "Đặt vị trí mặt nạ 0 (đóng) đến 100 (mở). / Move the mask to a "
                              "position, 0 = closed, 100 = open.",
    "self.mask.get_state": "Trạng thái mặt nạ (open/closed/moving) và vị trí. / Mask state "
                           "and position.",
    "self.servo.calibrate": "Hiệu chỉnh: đưa một servo tới góc thô. / Calibration: drive one "
                            "servo to a raw angle.",
    "self.servo.save_calibration": "Lưu góc đóng/mở của một servo. / Save one servo's "
                                   "closed/open angles.",
    "self.eyes.set": "Bật/tắt đèn mắt, độ sáng 0-100. / Turn the eye lights on/off, "
                     "brightness 0-100.",
    "self.eyes.effect": "Hiệu ứng đèn mắt: breathe, blink, flicker, power_up, fade_out. / Eye "
                        "light effect.",
    "self.output.set": "Bật/tắt kênh output dự phòng. / Switch the spare output on/off.",
}

UPSTREAM_DEVICE_STATUS_DESC = (
    "Provides the real-time information of the device, including the current status of the "
    "audio speaker, screen, battery, network, etc.\n"
    "Use this tool for: \n"
    "1. Answering questions about current condition (e.g. what is the current volume of "
    "the audio speaker?)\n"
    "2. As the first step to control the device (e.g. turn up / down the volume of the "
    "audio speaker, etc.)")


class ToolBox:
    """Builds the firmware tool list and records every tools/call (with timing)."""

    def __init__(self, helmet: Optional[HelmetSim] = None,
                 validator: Optional[SequenceValidator] = None,
                 dev: Optional[DeviceIdentity] = None) -> None:
        self.helmet = helmet or HelmetSim()
        self.validator = validator or SequenceValidator("builtin")
        self.dev = dev or DeviceIdentity()

    # --- callbacks -------------------------------------------------------------------
    def _mask_move(self, target: int, word: str):
        def cb(a: dict):
            self.helmet.move_mask(target if target >= 0 else a["percent"], a["speed"])
            return True, word
        return cb

    def _run_sequence(self, a: dict):
        ok, err = self.validator.validate(a["steps"])
        if not ok:
            return False, err
        self.helmet.run_steps(json.loads(a["steps"]))
        return True, "started"

    def _run_saved(self, a: dict):
        name = a["name"]
        if not re.fullmatch(r"[a-z0-9_]{1,11}", name):
            return False, "invalid sequence name: " + sanitize_message(name)
        steps = self.helmet.saved.get(name)
        if steps is None:
            return False, "unknown sequence: " + name
        self.helmet.run_steps(json.loads(steps), name)
        return True, "started"

    def _save(self, a: dict):
        if not re.fullmatch(r"[a-z0-9_]{1,11}", a["name"]):
            return False, "invalid sequence name: " + sanitize_message(a["name"])
        ok, err = self.validator.validate(a["steps"])
        if not ok:
            return False, err
        self.helmet.saved[a["name"]] = a["steps"]
        return True, "saved"

    def _show_info(self, a: dict):
        if len(a["title"]) > 24:
            return False, "title longer than 24 characters"
        if len(a["text"]) > 200:
            return False, "text longer than 200 characters"
        return True, "ok"

    def _eyes_set(self, a: dict):
        self.helmet.run_steps([{"eyes": {"on": a["on"], "brightness": a["brightness"]}}])
        return True, "ok"

    def _eyes_effect(self, a: dict):
        if a["name"] not in EYE_EFFECTS:
            return False, "unknown effect: " + sanitize_message(a["name"])
        if 0 < a["duration_ms"] < 100:
            return False, "duration_ms must be 0 (effect default) or 100..10000"
        self.helmet.run_steps([{"eyes": {"effect": a["name"], "duration_ms": a["duration_ms"]}}])
        return True, "ok"

    def _calibrate(self, a: dict):
        return True, "ok"

    def _save_calibration(self, a: dict):
        self.helmet.calibration[a["servo"]] = (a["closed_angle"], a["open_angle"])
        return True, "saved"

    def _device_status(self, a: dict):
        return True, compact({
            "audio_speaker": {"volume": self.helmet.volume},
            "screen": {"brightness": self.helmet.brightness, "theme": self.helmet.theme},
            "network": {"type": "wifi", "ssid": self.dev.ssid, "signal": "strong"},
        })

    def _set_volume(self, a: dict):
        self.helmet.volume = a["volume"]
        return True, "true"

    def _set_brightness(self, a: dict):
        self.helmet.brightness = a["brightness"]
        return True, "true"

    def _set_theme(self, a: dict):
        if a["theme"] not in ("light", "dark"):
            return True, "false"
        self.helmet.theme = a["theme"]
        return True, "true"

    # --- the list --------------------------------------------------------------------
    def build(self) -> list[McpTool]:
        h, d = self.helmet, TOOL_DESCRIPTIONS
        speed = lambda: Property("speed", INTEGER, 70, 10, 100)  # noqa: E731
        common = [  # McpServer::AddCommonTools: moved to the front of the list
            McpTool("self.get_device_status", UPSTREAM_DEVICE_STATUS_DESC, [],
                    self._device_status),
            McpTool("self.audio_speaker.set_volume",
                    "Set the volume of the audio speaker. If the current volume is unknown, you "
                    "must call `self.get_device_status` tool first and then call this tool.",
                    [Property("volume", INTEGER, minimum=0, maximum=100)], self._set_volume),
            McpTool("self.screen.set_brightness", "Set the brightness of the screen.",
                    [Property("brightness", INTEGER, minimum=0, maximum=100)],
                    self._set_brightness),
            McpTool("self.screen.set_theme",
                    "Set the theme of the screen. The theme can be `light` or `dark`.",
                    [Property("theme", STRING)], self._set_theme),
        ]
        board = [  # board InitializeTools: helmet_tools::Register, then the controllers
            McpTool("self.sequence.run", d["self.sequence.run"],
                    [Property("steps", STRING, max_length=4000)], self._run_sequence),
            McpTool("self.sequence.run_saved", d["self.sequence.run_saved"],
                    [Property("name", STRING, max_length=11)], self._run_saved),
            McpTool("self.sequence.save", d["self.sequence.save"],
                    [Property("name", STRING, max_length=11),
                     Property("steps", STRING, max_length=4000)], self._save),
            McpTool("self.sequence.stop", d["self.sequence.stop"], [],
                    lambda a: (setattr(h, "seq_until", 0.0) or True, "stopped")),
            McpTool("self.display.show_info", d["self.display.show_info"],
                    [Property("title", STRING, max_length=96),
                     Property("text", STRING, max_length=800),
                     Property("seconds", INTEGER, 5, 1, 30)], self._show_info),
            McpTool("self.system.get_status", d["self.system.get_status"], [],
                    lambda a: (True, h.status_json())),
            McpTool("self.mask.open", d["self.mask.open"], [speed()],
                    self._mask_move(100, "opening")),
            McpTool("self.mask.close", d["self.mask.close"], [speed()],
                    self._mask_move(0, "closing")),
            McpTool("self.mask.set_position", d["self.mask.set_position"],
                    [Property("percent", INTEGER, minimum=0, maximum=100), speed()],
                    self._mask_move(-1, "moving")),
            McpTool("self.mask.get_state", d["self.mask.get_state"], [],
                    lambda a: (True, compact(h.mask_state()))),
            McpTool("self.servo.calibrate", d["self.servo.calibrate"],
                    [Property("servo", INTEGER, minimum=0, maximum=1),
                     Property("angle", INTEGER, minimum=0, maximum=180)],
                    self._calibrate, user_only=True),
            McpTool("self.servo.save_calibration", d["self.servo.save_calibration"],
                    [Property("servo", INTEGER, minimum=0, maximum=1),
                     Property("closed_angle", INTEGER, minimum=0, maximum=180),
                     Property("open_angle", INTEGER, minimum=0, maximum=180)],
                    self._save_calibration, user_only=True),
            McpTool("self.eyes.set", d["self.eyes.set"],
                    [Property("on", BOOLEAN), Property("brightness", INTEGER, 100, 0, 100)],
                    self._eyes_set),
            McpTool("self.eyes.effect", d["self.eyes.effect"],
                    [Property("name", STRING, max_length=16),
                     Property("duration_ms", INTEGER, 0, 0, 10000)], self._eyes_effect),
            McpTool("self.output.set", d["self.output.set"], [Property("on", BOOLEAN)],
                    lambda a: (setattr(h, "output_on", a["on"]) or True, "ok")),
        ]
        info = compact(build_system_info(self.dev))
        user_only = [  # McpServer::AddUserOnlyTools: appended at the end
            McpTool("self.get_system_info", "Get the system information", [],
                    lambda a: (True, info), user_only=True),
            McpTool("self.reboot", "Reboot the system", [], lambda a: (True, "true"),
                    user_only=True),
            McpTool("self.upgrade_firmware",
                    "Upgrade firmware from a specific URL. This will download and install the "
                    "firmware, then reboot the device.",
                    # upstream quirk: the third argument becomes the *default value*
                    [Property("url", STRING,
                              "The URL of the firmware binary file to download and install")],
                    lambda a: (True, "true"), user_only=True),
            McpTool("self.screen.get_info",
                    "Information about the screen, including width, height, etc.", [],
                    lambda a: (True, '{"width":240,"height":240,"monochrome":false}'),
                    user_only=True),
            McpTool("self.screen.snapshot", "Snapshot the screen and upload it to a specific URL",
                    [Property("url", STRING), Property("quality", INTEGER, 80, 1, 100)],
                    lambda a: (False, "Failed to snapshot screen"), user_only=True),
            McpTool("self.screen.preview_image", "Preview an image on the screen",
                    [Property("url", STRING)],
                    lambda a: (False, "Failed to open URL: " + a["url"]), user_only=True),
            McpTool("self.assets.set_download_url", "Set the download url for the assets",
                    [Property("url", STRING)], lambda a: (True, "true"), user_only=True),
        ]
        return common + board + user_only


def build_helmet_tool_list(**kwargs) -> list[McpTool]:
    return ToolBox(**kwargs).build()


# --------------------------------------------------------------------------------------
# Actions (what a call does to the helmet), HUD glyph coverage
# --------------------------------------------------------------------------------------

def steps_to_actions(steps: Any) -> list[str]:
    actions: list[str] = []
    if not isinstance(steps, list):
        return actions
    for step in steps:
        if not isinstance(step, dict) or len(step) != 1:
            continue
        (kind, body), = step.items()
        if kind == "mask" and isinstance(body, dict):
            if body.get("action") in ("open", "close"):
                actions.append("mask_" + body["action"])
            elif _as_int(body.get("position")) is not None:
                pos = _as_int(body["position"])
                actions.append("mask_open" if pos == 100 else "mask_close" if pos == 0
                               else f"mask_pos_{pos}")
        elif kind == "eyes" and isinstance(body, dict):
            if "on" in body:
                actions.append("eyes_on" if body["on"] else "eyes_off")
            else:
                eff = body.get("effect")
                actions.append({"fade_out": "eyes_off", "blink": "eyes_blink"}.get(eff, "eyes_on"))
        elif kind == "output" and isinstance(body, dict):
            actions.append("output_on" if body.get("on") else "output_off")
        elif kind in ("display", "servo", "sound"):
            actions.append(kind)
    return actions


def call_to_actions(name: str, args: Any, saved: Optional[dict] = None) -> list[str]:
    args = args if isinstance(args, dict) else {}
    if name == "self.mask.open":
        return ["mask_open"]
    if name == "self.mask.close":
        return ["mask_close"]
    if name == "self.mask.set_position":
        return steps_to_actions([{"mask": {"position": args.get("percent")}}])
    if name == "self.eyes.set":
        return ["eyes_on" if args.get("on") else "eyes_off"]
    if name == "self.eyes.effect":
        return steps_to_actions([{"eyes": {"effect": args.get("name")}}])
    if name == "self.output.set":
        return ["output_on" if args.get("on") else "output_off"]
    if name == "self.display.show_info":
        return ["display"]
    if name in ("self.sequence.run", "self.sequence.save"):
        try:
            steps = json.loads(args.get("steps", ""))
        except (TypeError, ValueError):
            return []
        return steps_to_actions(steps) if name == "self.sequence.run" else []
    if name == "self.sequence.run_saved":
        seq = (saved or {}).get(args.get("name"), compact(BUILTIN_SEQUENCES.get(args.get("name"), [])))
        return steps_to_actions(json.loads(seq))
    return []


def is_subsequence(needle: list, haystack: list) -> bool:
    it = iter(haystack)
    return all(any(x == y for y in it) for x in needle)


# HUD font ranges (contract D10). Text outside them renders as missing glyphs.
HUD_RANGES = ((0x20, 0x7E), (0xA0, 0xFF), (0x102, 0x103), (0x110, 0x111), (0x128, 0x129),
              (0x168, 0x169), (0x1A0, 0x1A1), (0x1AF, 0x1B0), (0x300, 0x303), (0x309, 0x309),
              (0x323, 0x323), (0x1EA0, 0x1EF9), (0x2010, 0x2027), (0x20AB, 0x20AB))
VI_DIACRITIC_RE = re.compile("[À-ỹĂăĐđĨĩŨũƠơƯư]")


def missing_glyphs(text: str) -> list[str]:
    out = []
    for ch in text:
        if ch == "\n":
            continue
        o = ord(ch)
        if not any(lo <= o <= hi for lo, hi in HUD_RANGES) and ch not in out:
            out.append(ch)
    return out


# --------------------------------------------------------------------------------------
# Audio: WAV I/O, resampling, speech bounds, Opus via PyAV
# --------------------------------------------------------------------------------------

def _np():
    import numpy  # noqa: WPS433 (lazy: text mode works without numpy)
    return numpy


def _av():
    try:
        import av  # noqa: WPS433 (lazy: text mode works without PyAV)
    except ImportError as e:
        raise SimError("PyAV is required for audio (pip install -r requirements.txt)") from e
    return av


def resample_int16(pcm, src_rate: int, dst_rate: int):
    """Resamples mono int16 PCM with FFmpeg's swresample (via PyAV)."""
    np = _np()
    if src_rate == dst_rate or len(pcm) == 0:
        return pcm.astype(np.int16)
    av = _av()
    frame = av.AudioFrame.from_ndarray(pcm.astype(np.int16).reshape(1, -1), format="s16",
                                       layout="mono")
    frame.sample_rate = src_rate
    rs = av.AudioResampler(format="s16", layout="mono", rate=dst_rate)
    chunks = [f.to_ndarray().reshape(-1) for f in rs.resample(frame)]
    chunks += [f.to_ndarray().reshape(-1) for f in rs.resample(None)]
    return np.concatenate(chunks).astype(np.int16) if chunks else np.zeros(0, np.int16)


def load_audio_16k(path: Path):
    """Loads any audio file as 16 kHz mono int16 (stdlib wave for PCM16, PyAV otherwise)."""
    np = _np()
    try:
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() != 2:
                raise wave.Error("not 16-bit PCM")
            rate, channels = w.getframerate(), w.getnchannels()
            data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1).astype(np.int16)
        return resample_int16(data, rate, UPLINK_RATE)
    except (wave.Error, EOFError):
        pass
    av = _av()
    rs = av.AudioResampler(format="s16", layout="mono", rate=UPLINK_RATE)
    chunks = []
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            chunks += [f.to_ndarray().reshape(-1) for f in rs.resample(frame)]
    chunks += [f.to_ndarray().reshape(-1) for f in rs.resample(None)]
    if not chunks:
        raise SimError(f"no audio in {path}")
    return np.concatenate(chunks).astype(np.int16)


def write_wav(path: Path, pcm, rate: int) -> None:
    np = _np()
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.asarray(pcm, dtype="<i2").tobytes())


def speech_bounds_ms(pcm, rate: int = UPLINK_RATE, window_ms: int = 20) -> tuple:
    """Energy detector: (start_ms, end_ms) of the speech in a recording, or (0, 0)."""
    np = _np()
    n = int(rate * window_ms / 1000)
    if len(pcm) < n:
        return 0, 0
    frames = pcm[: len(pcm) // n * n].astype(np.float64).reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    peak = float(rms.max())
    if peak < 200:  # quieter than about -44 dBFS: no speech
        return 0, 0
    floor = float(np.percentile(rms, 10))
    threshold = max(peak * 0.1, floor * 3.0, 150.0)
    idx = np.nonzero(rms >= threshold)[0]
    return int(idx[0] * window_ms), int((idx[-1] + 1) * window_ms)


OPUS_CELT_MS = (2.5, 5.0, 10.0, 20.0)
OPUS_SILK_MS = (10.0, 20.0, 40.0, 60.0)


def opus_packet_duration_ms(packet: bytes) -> Optional[float]:
    """Duration encoded in an Opus packet's TOC byte (RFC 6716 §3.1)."""
    if not packet:
        return None
    toc = packet[0]
    config = toc >> 3
    if config < 12:
        frame = OPUS_SILK_MS[config % 4]
    elif config < 16:
        frame = (10.0, 20.0)[config % 2]
    else:
        frame = OPUS_CELT_MS[config % 4]
    code = toc & 0x3
    if code == 0:
        count = 1
    elif code in (1, 2):
        count = 2
    else:
        if len(packet) < 2:
            return None
        count = packet[1] & 0x3F
    return frame * count


class OpusEncoder:
    """16 kHz mono Opus, 60 ms frames (firmware: VBR, DTX, application audio)."""

    def __init__(self, rate: int = UPLINK_RATE, frame_ms: int = OPUS_FRAME_MS,
                 bitrate: int = 24000) -> None:
        import fractions
        av = _av()
        self.rate, self.frame_samples = rate, rate * frame_ms // 1000
        self.ctx = av.CodecContext.create("libopus", "w")
        self.ctx.sample_rate = rate
        self.ctx.layout = "mono"
        self.ctx.format = "s16"
        self.ctx.bit_rate = bitrate
        self.ctx.time_base = fractions.Fraction(1, rate)
        self.ctx.options = {"frame_duration": str(frame_ms), "application": "audio",
                            "vbr": "on", "dtx": "1"}
        self.ctx.open()
        self.pts = 0

    def encode(self, pcm) -> list[bytes]:
        np = _np()
        av = _av()
        chunk = np.asarray(pcm, dtype=np.int16)
        if len(chunk) < self.frame_samples:
            chunk = np.pad(chunk, (0, self.frame_samples - len(chunk)))
        frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = self.rate
        frame.pts = self.pts
        self.pts += self.frame_samples
        return [bytes(p) for p in self.ctx.encode(frame)]


class OpusDecoder:
    """Decodes Opus packets (FFmpeg decodes at 48 kHz) and resamples to out_rate."""

    def __init__(self, out_rate: int) -> None:
        av = _av()
        name = "libopus" if "libopus" in av.codecs_available else "opus"
        self.ctx = av.CodecContext.create(name, "r")
        self.ctx.sample_rate = 48000
        self.ctx.layout = "mono"
        self.ctx.open()
        self.rs = av.AudioResampler(format="s16", layout="mono", rate=out_rate)
        self.errors = 0

    def decode(self, packet: bytes):
        np = _np()
        av = _av()
        out = []
        try:
            for frame in self.ctx.decode(av.Packet(packet)):
                out += [f.to_ndarray().reshape(-1) for f in self.rs.resample(frame)]
        except Exception:  # corrupt or non-Opus frame
            self.errors += 1
        return np.concatenate(out).astype(np.int16) if out else np.zeros(0, np.int16)


# --------------------------------------------------------------------------------------
# Session: one WebSocket connection, the device state machine and turn recording
# --------------------------------------------------------------------------------------

@dataclasses.dataclass
class SimConfig:
    ota_url: str = ""
    ws_url: str = ""
    token: Optional[str] = None
    auth_key: str = ""
    protocol_version: int = 1
    aec: bool = False
    glyph_push: bool = True
    mcp_page_size: int = MCP_MAX_PAYLOAD
    mcp_delay_ms: int = 0
    connect_timeout: float = HELLO_TIMEOUT_S
    turn_timeout: float = 30.0
    settle_ms: int = 600
    tail_ms: int = 4000
    ptt_release_ms: int = 0
    opus_bitrate: int = 24000
    save_audio: bool = True
    out_dir: Optional[Path] = None
    verbose: bool = True


@dataclasses.dataclass
class TurnInput:
    text: Optional[str] = None
    wav: Optional[Path] = None
    wake: Optional[str] = None
    mode: str = "auto"
    abort_after_ms: Optional[int] = None
    abort_wake_word: bool = False
    speech_end_ms: Optional[int] = None


class Turn:
    """Everything observed from end of speech until the turn is over."""

    def __init__(self, index: int, inp: TurnInput) -> None:
        self.index, self.inp = index, inp
        self.eos: Optional[float] = None     # perf_counter of the end of speech
        self.eos_wall_ms: Optional[int] = None
        self.started = time.perf_counter()
        self.stt: list = []                  # (t, text)
        self.tool_displays: list = []        # (t, text) stt messages "% <tool>"
        self.sentences: list = []            # (t, text)
        self.emotions: list = []
        self.tts_start: list = []
        self.tts_stop: list = []
        self.frames: list = []               # (t, len, accepted)
        self.mcp_calls: list = []            # dicts
        self.pcm_chunks: list = []
        self.uplink_frames = 0
        self.speech_end_in_wav_ms: Optional[int] = None
        self.wav_duration_ms: Optional[int] = None
        self.abort_t: Optional[float] = None
        self.errors: list = []
        self.warnings: list = []
        self.completed = False
        self.timed_out = False
        self.reply_wav: Optional[str] = None
        self.stop_event = asyncio.Event()
        self.first_audio_event = asyncio.Event()
        self.stt_event = asyncio.Event()

    def after_eos(self, t: float) -> bool:
        return self.eos is not None and t >= self.eos

    def rel(self, t: Optional[float]) -> Optional[float]:
        if t is None or self.eos is None:
            return None
        return round((t - self.eos) * 1000, 1)

    def first(self, items: list) -> Optional[float]:
        return next((x[0] for x in items if self.after_eos(x[0])), None)

    def metrics(self) -> dict:
        first_mcp = next((c["_t"] for c in self.mcp_calls if self.after_eos(c["_t"])), None)
        m = {
            "eos_to_stt_ms": self.rel(self.first(self.stt)),
            "eos_to_tts_start_ms": self.rel(self.first(self.tts_start)),
            "eos_to_first_audio_ms": self.rel(self.first(self.frames)),
            "eos_to_first_mcp_ms": self.rel(first_mcp),
            "eos_to_tts_stop_ms": self.rel(self.first(self.tts_stop)),
        }
        if self.abort_t is not None:
            ack = next((t for t, *_ in self.tts_stop if t >= self.abort_t), None)
            last = max((t for t, *_ in self.frames if t >= self.abort_t), default=None)
            m["abort_to_tts_stop_ms"] = (round((ack - self.abort_t) * 1000, 1)
                                         if ack is not None else None)
            m["abort_to_last_audio_ms"] = (round((last - self.abort_t) * 1000, 1)
                                           if last is not None else 0.0)
            m["frames_after_abort"] = sum(1 for t, *_ in self.frames if t >= self.abort_t)
        return m

    def actions(self, saved: Optional[dict] = None) -> list[str]:
        out: list[str] = []
        for call in self.mcp_calls:
            if call.get("error") is None:
                out += call_to_actions(call["name"], call.get("arguments"), saved)
        return out

    def to_dict(self, frame_ms: int) -> dict:
        accepted = [f for f in self.frames if f[2]]
        stt_after = [t for (tt, t) in self.stt if self.after_eos(tt)]
        return {
            "index": self.index,
            "input": "audio" if self.inp.wav else "text",
            "text": self.inp.text,
            "wav": str(self.inp.wav) if self.inp.wav else None,
            "wake": self.inp.wake,
            "mode": self.inp.mode,
            "eos_wall_ms": self.eos_wall_ms,
            "speech_end_in_wav_ms": self.speech_end_in_wav_ms,
            "wav_duration_ms": self.wav_duration_ms,
            "uplink_frames": self.uplink_frames,
            "stt": stt_after[0] if stt_after else None,
            "stt_all": [t for _, t in self.stt],
            "reply_text": " ".join(t for _, t in self.sentences if t),
            "sentences": [t for _, t in self.sentences],
            "emotions": self.emotions,
            "tool_displays": [t for _, t in self.tool_displays],
            "audio_frames": len(self.frames),
            "audio_frames_dropped": len(self.frames) - len(accepted),
            "reply_audio_ms": len(accepted) * frame_ms,
            "reply_wav": self.reply_wav,
            "mcp_calls": [{k: v for k, v in c.items() if not k.startswith("_")}
                          for c in self.mcp_calls],
            "actions": self.actions(),
            "metrics": self.metrics(),
            "completed": self.completed,
            "timed_out": self.timed_out,
            "errors": self.errors,
            "warnings": self.warnings,
        }


class DeviceSession:
    def __init__(self, cfg: SimConfig, dev: DeviceIdentity, toolbox: ToolBox,
                 log: Callable[[str], None]) -> None:
        self.cfg, self.dev, self.toolbox, self.log = cfg, dev, toolbox, log
        self.mcp = McpServerSim(toolbox.build(), board_name=dev.board_name,
                                version=dev.fw_version, max_payload=cfg.mcp_page_size)
        self.ws = None
        self.reader_task: Optional[asyncio.Task] = None
        self.session_id = ""
        self.server_hello: Optional[dict] = None
        self.server_rate = 24000           # Protocol defaults (protocol.h:79-80)
        self.server_frame_ms = OPUS_FRAME_MS
        self.state = "idle"
        self.listen_mode = "auto"
        self.turn: Optional[Turn] = None
        self.closed = asyncio.Event()
        self.hello_event = asyncio.Event()
        self.mcp_ready = asyncio.Event()
        self.timeline: dict = {}
        self.protocol_warnings: list = []
        self.protocol_errors: list = []
        self.mcp_requests: list = []
        self.all_calls: list = []
        self.decoder: Optional[OpusDecoder] = None
        self.last_playback_end = 0.0
        self.close_reason: Optional[str] = None
        self.listen_starts = 0               # listen/start messages sent (incl. restarts)
        self._bg_tasks: set = set()
        self._hello_t = time.perf_counter()

    # --- connection ------------------------------------------------------------------
    async def open(self, ws_url: str, token: str) -> None:
        try:
            from websockets.asyncio.client import connect  # noqa: WPS433
        except ImportError as e:
            raise SimError("the websockets package (>=13) is required") from e
        headers = build_ws_headers(self.dev, token, self.cfg.protocol_version)
        kwargs = dict(additional_headers=headers, user_agent_header=self.dev.user_agent,
                      compression=None, ping_interval=None, max_size=None,
                      open_timeout=self.cfg.connect_timeout)
        import inspect
        if "proxy" in inspect.signature(connect.__init__).parameters:
            kwargs["proxy"] = None  # the firmware never uses a proxy
        t0 = time.perf_counter()
        try:
            self.ws = await connect(ws_url, **kwargs)
        except Exception as e:
            raise SimError(f"WebSocket connect to {ws_url} failed: {e}") from e
        self.timeline["ws_connect_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        self.reader_task = asyncio.create_task(self._reader())
        t1 = time.perf_counter()
        try:
            await self.ws.send(build_hello(self.cfg.protocol_version, self.cfg.aec,
                                           self.cfg.glyph_push))
        except Exception as e:  # closed right away (e.g. authentication failure)
            try:
                await asyncio.wait_for(self.closed.wait(), 2)
            except asyncio.TimeoutError:
                pass
            raise SimError("; ".join(self.protocol_errors + [f"hello not sent: {e}"])) from e
        try:
            await asyncio.wait_for(self.hello_event.wait(), self.cfg.connect_timeout)
        except asyncio.TimeoutError:
            raise SimError("no server hello within 10 s (firmware: SERVER_TIMEOUT) "
                           + "; ".join(self.protocol_errors)) from None
        if self.server_hello is None:  # closed before the hello (e.g. authentication)
            raise SimError("; ".join(self.protocol_errors + [self.close_reason or "no hello"]))
        self.timeline["hello_ms"] = round((time.perf_counter() - t1) * 1000, 1)
        self._hello_t = time.perf_counter()
        self.state = "connected"
        self.log(f"connected, session_id={self.session_id or '-'}, downlink "
                 f"{self.server_rate} Hz / {self.server_frame_ms} ms")

    async def close(self) -> None:
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
        if self.reader_task:
            try:
                await asyncio.wait_for(self.reader_task, 2)
            except (asyncio.TimeoutError, Exception):
                self.reader_task.cancel()

    async def send_text(self, text: str) -> None:
        if self.ws is None:
            raise SimError("not connected")
        try:
            await self.ws.send(text)
        except Exception as e:
            raise SimError(f"send failed: {e}") from e

    # --- receive path ----------------------------------------------------------------
    async def _reader(self) -> None:
        try:
            async for message in self.ws:
                t = time.perf_counter()
                if isinstance(message, bytes):
                    self._on_audio(t, message)
                else:
                    await self._on_text(t, message)
        except Exception as e:  # connection closed abnormally
            self.close_reason = f"connection closed: {e}"
        finally:
            if self.close_reason is None:
                rcvd = getattr(self.ws, "close_code", None)
                self.close_reason = f"connection closed (code {rcvd})"
            self.closed.set()
            if self.server_hello is None:
                self.hello_event.set()  # wake up open(), which reports the failure
            if self.turn is not None:
                self.turn.stop_event.set()
                self.turn.first_audio_event.set()
                self.turn.stt_event.set()

    def _on_audio(self, t: float, data: bytes) -> None:
        accepted = self.state == "speaking"  # frames are dropped unless Speaking
        dur = opus_packet_duration_ms(data)
        if dur is not None and abs(dur - self.server_frame_ms) > 0.1:
            self._warn_once(f"downlink Opus packet of {dur} ms (hello says "
                            f"{self.server_frame_ms} ms)")
        turn = self.turn
        if turn is None:
            return
        turn.frames.append((t, len(data), accepted))
        if turn.after_eos(t):
            turn.first_audio_event.set()
        if accepted and self.cfg.save_audio:
            try:
                if self.decoder is None:
                    self.decoder = OpusDecoder(self.server_rate)
                turn.pcm_chunks.append(self.decoder.decode(data))
            except SimError as e:
                self._warn_once(str(e))
        self.last_playback_end = max(self.last_playback_end, t) + self.server_frame_ms / 1000

    def _warn_once(self, text: str) -> None:
        if text not in self.protocol_warnings:
            self.protocol_warnings.append(text)
            self.log("warning: " + text)

    async def _on_text(self, t: float, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            if raw.strip() == "认证失败":
                self.protocol_errors.append("authentication failed (server sent '认证失败')")
            else:
                self.protocol_errors.append(f"non-JSON text frame: {raw[:120]!r}")
            self.log(f"server text frame: {raw[:120]}")
            return
        if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
            self._warn_once(f"message without type: {raw[:120]}")
            return
        mtype = msg["type"]
        if mtype == "hello":
            self._on_hello(msg)
        elif mtype == "mcp":
            await self._on_mcp(t, msg)
        elif mtype == "stt":
            self._on_stt(t, msg)
        elif mtype == "llm":
            if self.turn is not None and msg.get("emotion"):
                self.turn.emotions.append(msg.get("emotion"))
        elif mtype == "tts":
            await self._on_tts(t, msg)
        elif mtype in ("system", "alert", "custom", "notify"):
            self.log(f"server {mtype}: {raw[:200]}")
            if self.turn is not None:
                self.turn.warnings.append(f"{mtype} message: {raw[:200]}")
        else:
            self._warn_once(f"unknown message type: {mtype}")

    def _on_hello(self, msg: dict) -> None:
        if msg.get("transport") != "websocket":
            self.protocol_errors.append(f"server hello has transport={msg.get('transport')!r}"
                                        " (firmware requires 'websocket')")
            return
        self.server_hello = msg
        if isinstance(msg.get("session_id"), str):
            self.session_id = msg["session_id"]
        else:
            self._warn_once("server hello has no session_id")
        ap = msg.get("audio_params")
        if isinstance(ap, dict):
            if is_json_number(ap.get("sample_rate")):
                self.server_rate = int(ap["sample_rate"])
            if is_json_number(ap.get("frame_duration")):
                self.server_frame_ms = int(ap["frame_duration"])
        if self.server_rate not in SUPPORTED_OPUS_RATES:
            self.protocol_errors.append(f"server hello sample_rate {self.server_rate} is not "
                                        "an Opus rate")
        elif self.server_rate != 24000:
            self._warn_once(f"server hello announces {self.server_rate} Hz downlink "
                            "(contract D3 expects 24000)")
        if self.server_frame_ms != OPUS_FRAME_MS:
            self._warn_once(f"server hello frame_duration {self.server_frame_ms} ms")
        self.hello_event.set()

    async def _on_mcp(self, t: float, msg: dict) -> None:
        payload = msg.get("payload")
        if not isinstance(payload, dict):
            self._warn_once("mcp message without object payload")
            return
        method = payload.get("method")
        rel_hello = round((t - self._hello_t) * 1000, 1)
        if self.cfg.mcp_delay_ms:
            await asyncio.sleep(self.cfg.mcp_delay_ms / 1000)
        reply = self.mcp.handle(payload)
        entry = {"method": method, "id": payload.get("id"), "t_after_hello_ms": rel_hello}
        params = payload.get("params")
        if method == "tools/list" and isinstance(params, dict):
            entry["cursor"] = params.get("cursor")
            entry["withUserTools"] = params.get("withUserTools")
        self.mcp_requests.append(entry)
        if method == "tools/call":
            self._record_call(t, payload, reply)
        if reply is not None:
            await self.send_text(msg_mcp(self.session_id, reply))
        if method == "initialize":
            self.timeline["mcp_initialize_ms"] = rel_hello
        if method == "tools/list" and reply is not None and '"nextCursor"' not in reply:
            self.timeline["mcp_tools_list_done_ms"] = rel_hello
            self.timeline["mcp_tools_list_pages"] = sum(
                1 for r in self.mcp_requests if r["method"] == "tools/list")
            self.mcp_ready.set()

    def _record_call(self, t: float, payload: dict, reply: Optional[str]) -> None:
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        call = {"_t": t, "t_wall_ms": now_ms(), "id": payload.get("id"),
                "name": params.get("name"), "arguments": params.get("arguments"),
                "result": None, "error": None}
        try:
            parsed = json.loads(reply) if reply else None
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            if "error" in parsed:
                call["error"] = parsed["error"]
            elif isinstance(parsed.get("result"), dict):
                content = parsed["result"].get("content") or [{}]
                call["result"] = content[0].get("text")
        elif reply:
            call["error"] = {"code": None, "message": "invalid JSON reply (firmware quirk)"}
        if call["name"] == "self.sequence.run" and isinstance(call["arguments"], dict):
            try:
                call["steps"] = json.loads(call["arguments"].get("steps", ""))
            except (TypeError, ValueError):
                call["steps"] = None
        self.all_calls.append(call)
        if self.turn is not None:
            call["t_rel_eos_ms"] = self.turn.rel(t)
            self.turn.mcp_calls.append(call)
        outcome = call["result"] if call["error"] is None else f"ERROR {call['error']}"
        self.log(f"tools/call {call['name']} {compact(call['arguments'])} -> {outcome}")

    def _on_stt(self, t: float, msg: dict) -> None:
        text = msg.get("text") if isinstance(msg.get("text"), str) else ""
        if self.turn is None:
            return
        if text.startswith("% "):
            self.turn.tool_displays.append((t, text))
        else:
            self.turn.stt.append((t, text))
            if self.turn.after_eos(t):
                self.turn.stt_event.set()
        self._check_display_text(text, "stt")

    def _check_display_text(self, text: str, where: str) -> None:
        missing = missing_glyphs(text)
        if missing and self.turn is not None:
            w = f"{where} text has characters the HUD font lacks: {''.join(missing)!r}"
            if w not in self.turn.warnings:
                self.turn.warnings.append(w)
        if text and unicodedata.normalize("NFC", text) != text and self.turn is not None:
            self.turn.warnings.append(f"{where} text is not NFC-normalized: {text[:60]!r}")

    async def _on_tts(self, t: float, msg: dict) -> None:
        state = msg.get("state")
        turn = self.turn
        if state == "start":
            self.state = "speaking"
            if turn is not None:
                turn.tts_start.append((t,))
        elif state == "stop":
            if turn is not None:
                turn.tts_stop.append((t,))
            if self.state == "speaking":  # the firmware ignores stop in other states
                if self.listen_mode == "manual":
                    self.state = "idle"
                else:
                    self.state = "listening"
                    task = asyncio.create_task(self._restart_listening())
                    self._bg_tasks.add(task)
                    task.add_done_callback(self._bg_tasks.discard)
            if turn is not None and turn.after_eos(t):
                turn.stop_event.set()
        elif state == "sentence_start":
            text = msg.get("text") if isinstance(msg.get("text"), str) else ""
            if turn is not None:
                turn.sentences.append((t, text))
            self._check_display_text(text, "tts sentence_start")

    async def _restart_listening(self) -> None:
        # Auto mode: Listening again once playback drains (StartListeningAudio sends
        # listen/start, which also resets the server's audio buffer).
        delay = max(0.0, self.last_playback_end - time.perf_counter())
        await asyncio.sleep(min(delay, 5.0))
        if self.state == "listening" and not self.closed.is_set():
            try:
                await self.listen_start(self.listen_mode)
            except SimError:
                pass

    async def listen_start(self, mode: str) -> None:
        await self.send_text(msg_listen_start(self.session_id, mode))
        self.listen_starts += 1
        self.state = "listening"

    # --- one utterance ---------------------------------------------------------------
    async def run_turn(self, index: int, inp: TurnInput) -> Turn:
        turn = Turn(index, inp)
        self.turn = turn
        try:
            await self._run_turn(turn)
        except SimError as e:
            turn.errors.append(str(e))
        finally:
            if self.cfg.save_audio and turn.pcm_chunks and self.cfg.out_dir:
                np = _np()
                path = self.cfg.out_dir / f"turn{index:03d}_reply.wav"
                write_wav(path, np.concatenate(turn.pcm_chunks), self.server_rate)
                turn.reply_wav = str(path)
                turn.pcm_chunks = []
        return turn

    async def _run_turn(self, turn: Turn) -> None:
        inp = turn.inp
        self.listen_mode = inp.mode
        if self.closed.is_set():
            raise SimError(self.close_reason or "connection closed")
        if inp.wake:
            await self._wake(inp.wake, wait_ack=inp.wav is None)
        if inp.wav is not None:
            await self._speak_wav(turn)
        else:
            if self.state != "listening" or inp.mode == "manual":
                await self.listen_start(inp.mode)
            turn.eos = time.perf_counter()
            turn.eos_wall_ms = now_ms()
            await self.send_text(msg_listen_detect(self.session_id, inp.text or ""))
            self.log(f"turn {turn.index}: text {inp.text!r}")
        await self._await_turn_end(turn)

    async def _wake(self, word: str, wait_ack: bool) -> None:
        # CONFIG_SEND_WAKE_WORD_DATA=y: listen/detect with the wake word, then Listening in
        # the default mode (listen/start) without waiting for the server. Server v0.9.7 with
        # enable_greeting=false answers stt + tts start + tts stop: the device flips to
        # Speaking (mic audio is not sent) and back to Listening (a second listen/start).
        turn = self.turn
        stop_before = len(turn.tts_stop) if turn else 0
        await self.send_text(msg_listen_detect(self.session_id, word))
        self.log(f"wake word {word!r}")
        await self.listen_start(turn.inp.mode if turn else "auto")
        if wait_ack and turn is not None:
            # Text injection only: let the wake echo finish so it is not taken for the
            # command's stt/tts (audio mode streams right away, like the firmware).
            t_end = time.perf_counter() + 2.0
            while time.perf_counter() < t_end and not self.closed.is_set():
                if len(turn.tts_stop) > stop_before and self.state != "speaking":
                    break
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.15)  # the restarted listen/start goes out first
            turn.stt.clear()
            turn.tts_start.clear()
            turn.tts_stop.clear()

    async def _speak_wav(self, turn: Turn) -> None:
        inp = turn.inp
        np = _np()
        pcm = load_audio_16k(Path(inp.wav))
        speech_start, speech_end = speech_bounds_ms(pcm)
        if inp.speech_end_ms is not None:
            speech_end = inp.speech_end_ms
        turn.speech_end_in_wav_ms = speech_end
        turn.wav_duration_ms = int(len(pcm) * 1000 / UPLINK_RATE)
        if not speech_end:
            turn.warnings.append("no speech detected in the WAV (energy detector)")
            speech_end = turn.wav_duration_ms
        if self.state != "listening" or inp.mode == "manual":
            await self.listen_start(inp.mode)
        enc = OpusEncoder(bitrate=self.cfg.opus_bitrate)
        n = enc.frame_samples
        frames = [pcm[i:i + n] for i in range(0, len(pcm), n)]
        tail_frames = 0 if inp.mode == "manual" else int(self.cfg.tail_ms / OPUS_FRAME_MS)
        silence = np.zeros(n, dtype=np.int16)
        t0 = time.perf_counter()
        turn.eos = t0 + speech_end / 1000.0
        turn.eos_wall_ms = now_ms() + speech_end
        self.log(f"turn {turn.index}: streaming {inp.wav} ({turn.wav_duration_ms} ms, speech "
                 f"{speech_start}-{speech_end} ms, mode {inp.mode})")
        dropped_speech = 0
        i = 0
        while i < len(frames) + tail_frames:
            # Frame i is captured during [i*60, (i+1)*60) ms and sent when it is complete.
            due = t0 + (i + 1) * OPUS_FRAME_MS / 1000.0
            delay = due - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            if self.closed.is_set():
                raise SimError(self.close_reason or "connection closed")
            if self.state == "speaking" and inp.mode != "realtime":
                if time.perf_counter() >= turn.eos:
                    break  # the reply started: voice processing is off in Speaking
                # Speaking before the end of speech (wake echo, greeting): the firmware
                # does not send mic audio, so this frame is lost.
                if i < len(frames) and speech_start <= i * OPUS_FRAME_MS < speech_end:
                    dropped_speech += 1
                i += 1
                continue
            if i >= len(frames) and turn.stt_event.is_set():
                break  # the server detected the end of speech
            chunk = frames[i] if i < len(frames) else silence
            for packet in enc.encode(chunk):
                try:
                    await self.ws.send(packet)
                except Exception as e:
                    raise SimError(f"audio send failed: {e}") from e
                turn.uplink_frames += 1
            i += 1
        if dropped_speech:
            turn.warnings.append(f"{dropped_speech * OPUS_FRAME_MS} ms of speech not sent: the "
                                 "device was in Speaking state (wake-word echo/greeting)")
        if inp.mode == "manual":
            if self.cfg.ptt_release_ms:
                await asyncio.sleep(self.cfg.ptt_release_ms / 1000)
            await self.send_text(msg_listen_stop(self.session_id))
        elif i >= len(frames) + tail_frames:
            turn.warnings.append(f"server did not react within {self.cfg.tail_ms} ms of "
                                 "trailing silence (VAD end of speech not detected?)")

    async def _await_turn_end(self, turn: Turn) -> None:
        deadline = turn.started + self.cfg.turn_timeout
        if turn.inp.abort_after_ms is not None:
            await self._abort_flow(turn, deadline)
            return
        while True:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                turn.timed_out = True
                turn.errors.append(f"turn timed out after {self.cfg.turn_timeout:.0f} s")
                return
            try:
                await asyncio.wait_for(turn.stop_event.wait(), remaining)
            except asyncio.TimeoutError:
                continue
            if self.closed.is_set() and turn.first(turn.tts_stop) is None:
                turn.errors.append(self.close_reason or "connection closed")
                return
            # Settle: more speech may follow (a new tts start after this stop).
            await asyncio.sleep(self.cfg.settle_ms / 1000)
            last_start = turn.tts_start[-1][0] if turn.tts_start else 0
            last_stop = turn.tts_stop[-1][0] if turn.tts_stop else 0
            if last_start > last_stop and not self.closed.is_set():
                turn.stop_event.clear()
                continue
            turn.completed = True
            return

    async def _abort_flow(self, turn: Turn, deadline: float) -> None:
        try:
            await asyncio.wait_for(turn.first_audio_event.wait(),
                                   max(0.1, deadline - time.perf_counter()))
        except asyncio.TimeoutError:
            turn.timed_out = True
            turn.errors.append("no reply audio to interrupt")
            return
        if self.closed.is_set():
            turn.errors.append(self.close_reason or "connection closed")
            return
        await asyncio.sleep(turn.inp.abort_after_ms / 1000)
        turn.abort_t = time.perf_counter()
        # PTT click while speaking (D8): abort and flush locally, then listen again.
        await self.send_text(msg_abort(self.session_id, turn.inp.abort_wake_word))
        await self.listen_start(turn.inp.mode)
        self.log(f"turn {turn.index}: abort sent")
        await asyncio.sleep(1.5)  # collect the ack and any frames still in flight
        turn.completed = any(t >= turn.abort_t for t, *_ in turn.tts_stop)


# --------------------------------------------------------------------------------------
# Scenarios and assertions
# --------------------------------------------------------------------------------------

@dataclasses.dataclass
class Scenario:
    id: str
    title: str
    texts: list
    wake: Optional[str] = "Jarvis"
    mode: str = "auto"
    repeat: int = 1
    turn_timeout: Optional[float] = None
    abort_after_ms: Optional[int] = None
    abort_wake_word: bool = False
    checks: tuple = ()
    soft_checks: tuple = ("glyphs",)
    manual_part: str = ""


SCENARIOS = {
    "E1": Scenario("E1", "Hỏi giờ bằng tiếng Việt", ["mấy giờ rồi?"],
                   checks=("reply", "no_device_action"), soft_checks=("glyphs", "reply_has_hour"),
                   manual_part="giọng quản gia, nghe đúng giờ: nghe bằng tai"),
    "E2": Scenario("E2", "Hỏi thời tiết bằng tiếng Anh", ["what's the weather like?"],
                   checks=("reply",), soft_checks=("glyphs", "reply_english")),
    "E3": Scenario("E3", "Hai tác vụ trong một câu", ["open mask rồi bật đèn mắt lên"],
                   checks=("actions:mask_open,eyes_on",),
                   manual_part="mặt nạ mở, đèn sáng, HUD cập nhật: cần mũ thật"),
    "E4": Scenario("E4", "Khớp nhanh 'Mở mặt nạ'", ["mở mặt nạ"],
                   checks=("actions:mask_open", "max:eos_to_first_mcp_ms:800"),
                   manual_part="servo chạy <=800 ms: đo bằng video trên mũ thật"),
    "E5": Scenario("E5", "Tác vụ mới party_mode (YAML)", ["bật party mode"],
                   checks=("tool:self.sequence.run", "task_steps")),
    "E6": Scenario("E6", "Hỏi chuyên gia (ask_expert)",
                   ["hỏi chuyên gia: vì sao bầu trời có màu xanh?"], turn_timeout=60,
                   checks=("reply", "no_device_action")),
    "E7": Scenario("E7", "Ngắt lời bằng PTT (phần server)",
                   ["kể cho tôi nghe một câu chuyện thật dài về Iron Man"], abort_after_ms=1500,
                   checks=("max:abort_to_tts_stop_ms:300", "max:abort_to_last_audio_ms:500"),
                   manual_part="tiếng dừng <=300 ms trên loa: cần mũ thật (D8)"),
    "E8": Scenario("E8", "Ngắt lời bằng wake word (phần server)",
                   ["kể cho tôi nghe một câu chuyện thật dài về Iron Man"], abort_after_ms=1500,
                   abort_wake_word=True, checks=("max:abort_to_tts_stop_ms:300",),
                   manual_part="tự kích hoạt bởi loa (không có AEC): giới hạn đã biết (D12)"),
    "E11": Scenario("E11", "Chữ tiếng Việt đủ dấu",
                    ["hãy đọc nguyên văn câu này: Ừ, đã bật chế độ chiến đấu – tôi sẵn sàng, "
                     "thưa ngài!"],
                    checks=("reply", "glyphs", "vi_diacritics"), soft_checks=(),
                    manual_part="ảnh chụp HUD: không có ô vuông"),
    "E12": Scenario("E12", "Wake word thứ hai 'Quản gia'", ["close the mask"], wake="Quản gia",
                    checks=("actions:mask_close",),
                    manual_part="nhận wake word bằng giọng thật: cần mũ thật (FW7)"),
    "E13": Scenario("E13", "50 lệnh liên tiếp",
                    ["mở mặt nạ", "đóng mặt nạ", "bật đèn mắt", "tắt đèn mắt"], repeat=50,
                    checks=("all_completed", "each_turn_responds"), soft_checks=(),
                    manual_part="heap thiết bị: heap_trend.py trên log serial của mũ thật"),
}


@dataclasses.dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    soft: bool = False


def _turn_metric(turns: list, key: str) -> list:
    return [t.metrics().get(key) for t in turns]


def run_check(name: str, turns: list, ctx: dict, soft: bool = False) -> CheckResult:
    """Evaluates one named check over the recorded turns."""
    saved = ctx.get("saved")
    if name == "reply":
        bad = [t.index for t in turns if not (t.frames or t.sentences)]
        return CheckResult(name, not bad, "every turn got a spoken reply" if not bad
                           else f"no reply in turns {bad}", soft)
    if name in ("no_device_action", "no_mcp"):
        calls = [c["name"] for t in turns for c in t.mcp_calls]
        acts = [a for t in turns for a in t.actions(saved)]
        ok = not acts if name == "no_device_action" else not calls
        return CheckResult(name, ok, "no device action" if ok else f"calls: {calls}", soft)
    if name.startswith("actions:"):
        want = name.split(":", 1)[1].split(",")
        bad = []
        for t in turns:
            acts = t.actions(saved)
            if not is_subsequence(want, acts):
                bad.append(f"turn {t.index}: {acts}")
        return CheckResult(name, not bad, f"actions {want} in order" if not bad
                           else "; ".join(bad), soft)
    if name.startswith("tool:"):
        tool = name.split(":", 1)[1]
        bad = [t.index for t in turns if not any(c["name"] == tool for c in t.mcp_calls)]
        return CheckResult(name, not bad, f"{tool} called" if not bad
                           else f"{tool} not called in turns {bad}", soft)
    if name.startswith("max:"):
        _, key, limit = name.split(":")
        values = _turn_metric(turns, key)
        missing = [t.index for t, v in zip(turns, values) if v is None]
        over = [v for v in values if v is not None and v > float(limit)]
        ok = not missing and not over
        detail = f"{key} = {[v for v in values]} (limit {limit} ms)"
        return CheckResult(name, ok, detail, soft)
    if name == "all_completed":
        bad = [t.index for t in turns if not t.completed or t.errors]
        return CheckResult(name, not bad and len(turns) == ctx.get("expected_turns", len(turns)),
                           f"{len(turns) - len(bad)}/{ctx.get('expected_turns', len(turns))} "
                           f"turns completed" + (f", failed: {bad[:10]}" if bad else ""), soft)
    if name == "each_turn_responds":
        bad = [t.index for t in turns if not (t.mcp_calls or t.frames)]
        return CheckResult(name, not bad, "every turn produced a device call or audio"
                           if not bad else f"silent turns {bad[:10]}", soft)
    if name == "glyphs":
        bad = [w for t in turns for w in t.warnings if "HUD font lacks" in w or "NFC" in w]
        return CheckResult(name, not bad, "all displayed text renders with the HUD font"
                           if not bad else "; ".join(bad[:3]), soft)
    if name == "vi_diacritics":
        bad = [t.index for t in turns
               if not VI_DIACRITIC_RE.search("".join(x for _, x in t.sentences))]
        return CheckResult(name, not bad, "reply contains Vietnamese diacritics" if not bad
                           else f"no diacritics in turns {bad}", soft)
    if name == "reply_has_hour":
        tz = _dt.timezone(_dt.timedelta(hours=ctx.get("tz_offset", 7)))
        hour = _dt.datetime.now(tz).hour
        candidates = {str(hour), str(hour % 12 or 12), f"{hour:02d}"}
        bad = []
        for t in turns:
            nums = set(re.findall(r"\d+", "".join(x for _, x in t.sentences)))
            if not nums & candidates:
                bad.append(t.index)
        return CheckResult(name, not bad, f"reply mentions hour {hour}" if not bad
                           else f"hour {hour} not found in replies of turns {bad}", soft)
    if name == "reply_english":
        bad = []
        for t in turns:
            text = "".join(x for _, x in t.sentences)
            letters = [c for c in text if c.isalpha()]
            if not letters or VI_DIACRITIC_RE.search(text) or \
                    sum(c.isascii() for c in letters) / len(letters) < 0.9:
                bad.append(t.index)
        return CheckResult(name, not bad, "reply is in English" if not bad
                           else f"reply not English in turns {bad}", soft)
    if name == "task_steps":
        expected = ctx.get("expected_steps")
        if expected is None:
            return CheckResult(name, True, "skipped (no --expect-task/--tasks-file)", True)
        got = [c.get("steps") for t in turns for c in t.mcp_calls
               if c["name"] == "self.sequence.run"]
        if any(normalize_steps(s) == normalize_steps(expected) for s in got):
            return CheckResult(name, True, "steps match the task", soft)
        # The server merges all task calls of one turn (D4): accept the task as a
        # contiguous part of a merged sequence with the same actions.
        want = steps_to_actions(expected)
        ok = bool(want) and any(is_subsequence(want, steps_to_actions(s)) for s in got)
        return CheckResult(name, ok, "task actions found in the sequence (parameters differ)"
                           if ok else f"expected {compact(expected)}, got {compact(got)}", soft)
    return CheckResult(name, False, "unknown check", soft)


def normalize_steps(steps: Any) -> Any:
    """Fills the defaults (eyes effect duration per effect, display 5 s) for comparison."""
    if not isinstance(steps, list):
        return steps
    out = []
    for step in steps:
        if isinstance(step, dict) and len(step) == 1:
            (kind, body), = step.items()
            if isinstance(body, dict):
                body = dict(body)
                if kind == "eyes" and "effect" in body and body.get("duration_ms", 0) == 0:
                    body["duration_ms"] = eye_effect_default_ms(body["effect"])
                if kind == "display":
                    body.setdefault("seconds", 5)
            step = {kind: body}
        out.append(step)
    return out


def load_task_steps(task_id: str, tasks_file: Path) -> list:
    """Steps of one task in tasks.yaml (taskslib when available, else PyYAML)."""
    try:
        import yaml  # noqa: WPS433
    except ImportError as e:
        raise SimError("PyYAML is needed for --expect-task") from e
    data = yaml.safe_load(Path(tasks_file).read_text(encoding="utf-8"))
    tasks = (data or {}).get("tasks") or {}
    if task_id not in tasks:
        raise SimError(f"task {task_id!r} not found in {tasks_file}")
    return json.loads(json.dumps(tasks[task_id].get("steps", [])))


# --------------------------------------------------------------------------------------
# Runner and CLI
# --------------------------------------------------------------------------------------

def _resolve_inputs(args, scenario: Optional[Scenario]) -> list:
    """Builds the list of TurnInput for every turn (cycling texts/WAVs)."""
    texts = [args.text] if args.text else (scenario.texts if scenario else [])
    wavs: list = []
    if args.wav:
        wavs = [Path(w) for w in args.wav]
    elif args.wav_dir:
        wavs = sorted(Path(args.wav_dir).glob("*.wav"))
        if not wavs:
            raise SimError(f"no .wav files in {args.wav_dir}")
    elif scenario and args.input in ("auto", "audio"):
        names = [f"{scenario.id}.wav"] + [f"{scenario.id}_{i + 1}.wav" for i in range(len(texts))]
        wavs = [DEFAULT_AUDIO_DIR / n for n in names if (DEFAULT_AUDIO_DIR / n).exists()]
    if args.input == "text":
        wavs = []
    if args.input == "audio" and not wavs:
        raise SimError("--input audio but no WAV found (use --wav/--wav-dir or put "
                       f"{scenario.id if scenario else '<id>'}.wav in {DEFAULT_AUDIO_DIR})")
    if not texts and not wavs:
        raise SimError("nothing to say: give --scenario, --text or --wav")
    repeat = args.repeat or (scenario.repeat if scenario else 1)
    wake = None if args.no_wake else (args.wake if args.wake is not None
                                      else (scenario.wake if scenario else None))
    mode = args.mode or (scenario.mode if scenario else "auto")
    abort_after = args.abort_after_ms if args.abort_after_ms is not None else \
        (scenario.abort_after_ms if scenario else None)
    abort_wake = args.abort_reason == "wake_word_detected" or bool(
        scenario and scenario.abort_wake_word and args.abort_reason is None)
    inputs = []
    for i in range(repeat):
        wav = wavs[i % len(wavs)] if wavs else None
        text = texts[i % len(texts)] if texts else None
        inputs.append(TurnInput(text=text, wav=wav, wake=wake, mode=mode,
                                abort_after_ms=abort_after, abort_wake_word=abort_wake,
                                speech_end_ms=args.speech_end_ms))
    return inputs


async def run_simulation(args, scenario: Optional[Scenario], log) -> dict:
    dev = DeviceIdentity(mac=args.mac, uuid=args.uuid or "", fw_version=args.fw_version)
    validator = SequenceValidator(args.validator, Path(args.taskslib) if args.taskslib else None)
    toolbox = ToolBox(validator=validator, dev=dev)
    run_id = _dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "_" + (scenario.id if scenario
                                                                    else "custom")
    out_dir = None
    if not args.no_save:
        out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUT_DIR / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
    cfg = SimConfig(ota_url=args.ota_url or "", ws_url=args.ws_url or "", token=args.token,
                    auth_key=args.auth_key or "", aec=args.aec, glyph_push=not args.no_glyph_push,
                    mcp_page_size=args.mcp_page_size, mcp_delay_ms=args.mcp_delay_ms,
                    turn_timeout=args.turn_timeout or (scenario.turn_timeout if scenario and
                                                       scenario.turn_timeout else 30.0),
                    settle_ms=args.settle_ms, tail_ms=args.tail_ms,
                    ptt_release_ms=args.ptt_release_ms, opus_bitrate=args.opus_bitrate,
                    save_audio=out_dir is not None, out_dir=out_dir)
    inputs = _resolve_inputs(args, scenario)
    summary: dict = {
        "tool": "device_sim", "version": SIM_VERSION,
        "scenario": scenario.id if scenario else None,
        "title": scenario.title if scenario else None,
        "started": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "device": {"mac": dev.mac, "uuid": dev.uuid, "board": dev.board_name,
                   "firmware": dev.fw_version, "user_agent": dev.user_agent},
        "validator": validator.name,
        "validator_note": validator.load_error,
        "out_dir": str(out_dir) if out_dir else None,
    }
    ws_url, token = cfg.ws_url, cfg.token or ""
    if cfg.ota_url:
        ota = await asyncio.to_thread(ota_check, cfg.ota_url, dev)
        summary["ota"] = {"status": ota.status, "elapsed_ms": round(ota.elapsed_ms, 1),
                          "ws_url": ota.ws_url, "token_present": bool(ota.token),
                          "warnings": ota.warnings}
        for w in ota.warnings:
            log("OTA warning: " + w)
        ws_url = cfg.ws_url or ota.ws_url
        token = cfg.token if cfg.token is not None else ota.token
        log(f"OTA ok in {ota.elapsed_ms:.0f} ms -> {ota.ws_url} (token "
            f"{'yes' if ota.token else 'no'})")
    if not token and cfg.auth_key:
        token = xz_token(cfg.auth_key, dev.uuid, dev.mac)
    if not ws_url:
        raise SimError("no WebSocket URL: give --ota-url or --ws-url")
    summary["ws_url"] = ws_url

    turns: list = []
    sessions: list = []
    session: Optional[DeviceSession] = None
    try:
        for i, inp in enumerate(inputs, start=1):
            if session is None or session.closed.is_set() or (args.reconnect and i > 1):
                if session is not None:
                    await session.close()
                session = DeviceSession(cfg, dev, toolbox, log)
                sessions.append(session)
                await session.open(ws_url, token)
                try:
                    await asyncio.wait_for(session.mcp_ready.wait(), args.mcp_wait)
                except asyncio.TimeoutError:
                    session.protocol_warnings.append(
                        f"MCP tools/list not finished {args.mcp_wait:.0f} s after hello")
            turn = await session.run_turn(i, inp)
            turns.append(turn)
            m = turn.metrics()
            stt = next((x for tt, x in turn.stt if turn.after_eos(tt)), None)
            log(f"turn {i}: stt={stt!r} eos->stt={m['eos_to_stt_ms']} "
                f"eos->audio={m['eos_to_first_audio_ms']} eos->mcp={m['eos_to_first_mcp_ms']} "
                f"completed={turn.completed}")
            if i < len(inputs):
                await asyncio.sleep(args.pause_ms / 1000)
    finally:
        if session is not None:
            await session.close()

    frame_ms = sessions[-1].server_frame_ms if sessions else OPUS_FRAME_MS
    summary["connections"] = [{
        "session_id": s.session_id, "server_hello": s.server_hello, "timeline": s.timeline,
        "mcp_requests": s.mcp_requests, "protocol_warnings": s.protocol_warnings + s.mcp.warnings,
        "protocol_errors": s.protocol_errors, "close_reason": s.close_reason,
        "mcp_vision": bool(s.mcp.vision),
    } for s in sessions]
    summary["turns"] = [t.to_dict(frame_ms) for t in turns]
    summary["mcp_calls"] = [{k: v for k, v in c.items() if not k.startswith("_")}
                            for s in sessions for c in s.all_calls]
    keys = ("eos_to_stt_ms", "eos_to_first_audio_ms", "eos_to_first_mcp_ms", "eos_to_tts_stop_ms")
    summary["stats"] = {k: stats_of(_turn_metric(turns, k)) for k in keys}

    ctx = {"saved": toolbox.helmet.saved, "expected_turns": len(inputs),
           "tz_offset": args.tz_offset}
    if args.expect_task:
        tasks_file = Path(args.tasks_file) if args.tasks_file else DEFAULT_TASKS_DIR / "tasks.yaml"
        ctx["expected_steps"] = load_task_steps(args.expect_task, tasks_file)
    checks = list(scenario.checks) if scenario else []
    soft = list(scenario.soft_checks) if scenario else ["glyphs"]
    checks += [f"tool:{t}" for t in args.expect_tool or []]
    if args.expect_actions:
        checks.append("actions:" + args.expect_actions)
    if args.expect_no_mcp:
        checks.append("no_mcp")
    if args.expect_reply:
        checks.append("reply")
    if args.max_eos_to_mcp_ms is not None:
        checks.append(f"max:eos_to_first_mcp_ms:{args.max_eos_to_mcp_ms}")
    if args.max_eos_to_audio_ms is not None:
        checks.append(f"max:eos_to_first_audio_ms:{args.max_eos_to_audio_ms}")
    if args.expect_task and "task_steps" not in checks:
        checks.append("task_steps")
    if args.max_eos_to_mcp_ms is not None:  # an explicit limit replaces the scenario's
        checks = [c for c in checks if not c.startswith("max:eos_to_first_mcp_ms:")
                  or c.endswith(f":{args.max_eos_to_mcp_ms}")]
    results = [run_check(c, turns, ctx) for c in dict.fromkeys(checks)]
    results += [run_check(c, turns, ctx, soft=True) for c in soft if c not in checks]
    errors = [e for s in sessions for e in s.protocol_errors] + \
             [f"turn {t.index}: {e}" for t in turns for e in t.errors]
    results.append(CheckResult("no_errors", not errors, "no protocol/turn errors" if not errors
                               else "; ".join(errors[:5])))
    summary["assertions"] = [dataclasses.asdict(r) for r in results]
    summary["pass"] = all(r.ok for r in results if not r.soft)
    summary["finished"] = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    if out_dir:
        (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    return summary


def dump_tools(args) -> int:
    """Prints the tools/list pages exactly as the firmware sends them (offline)."""
    sim = McpServerSim(build_helmet_tool_list(), max_payload=args.mcp_page_size)
    cursor, page = "", 1
    while True:
        params: dict = {"withUserTools": bool(args.with_user_tools)}
        if cursor:
            params["cursor"] = cursor
        reply = sim.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": params})
        print(f"# page {page}: {utf8_len(reply)} bytes")
        print(reply)
        result = json.loads(reply).get("result", {})
        cursor = result.get("nextCursor", "")
        if not cursor:
            return EXIT_OK
        page += 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="device_sim.py",
        description="Jarvis Helmet device simulator (xiaozhi WebSocket + MCP + Opus).",
        epilog="Exit codes: 0 pass, 1 assertion failed, 2 usage error, 3 connection/protocol "
               "error. Env: CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET add Cloudflare Access "
               "headers to OTA and WebSocket requests.")
    g = p.add_argument_group("server")
    g.add_argument("--ota-url", help="OTA endpoint, e.g. http://192.168.1.27:8003/xiaozhi/ota/")
    g.add_argument("--ws-url", help="WebSocket URL (overrides the one from OTA; skips OTA if "
                                    "--ota-url is not given)")
    g.add_argument("--token", help="Bearer token (overrides OTA; '' = send none)")
    g.add_argument("--auth-key", help="compute the token locally from server.auth_key "
                                      "(when OTA is not used)")
    g = p.add_argument_group("device")
    g.add_argument("--mac", default=DEFAULT_MAC, help="Device-Id (default %(default)s)")
    g.add_argument("--uuid", help="Client-Id (default: derived from the MAC)")
    g.add_argument("--fw-version", default=FW_VERSION)
    g.add_argument("--aec", action="store_true", help="announce features.aec (USE_SERVER_AEC)")
    g.add_argument("--no-glyph-push", action="store_true", help="hello without text_font")
    g.add_argument("--mcp-page-size", type=int, default=MCP_MAX_PAYLOAD,
                   help="tools/list page size in bytes (firmware: 8000)")
    g.add_argument("--mcp-delay-ms", type=int, default=0,
                   help="extra delay before each MCP reply (main-loop latency)")
    g.add_argument("--mcp-wait", type=float, default=8.0,
                   help="seconds to wait for tools/list after hello before the first turn")
    g.add_argument("--validator", choices=("auto", "builtin", "taskslib"), default="auto")
    g.add_argument("--taskslib", help="directory containing taskslib.py (default server/tasks)")
    g = p.add_argument_group("input")
    g.add_argument("--scenario", type=str.upper, choices=sorted(SCENARIOS, key=lambda s: int(s[1:])))
    g.add_argument("--text", help="inject this text with listen/detect (no ASR)")
    g.add_argument("--wav", action="append", help="16 kHz mono WAV to stream (repeatable)")
    g.add_argument("--wav-dir", help="stream every .wav in this directory, cycling")
    g.add_argument("--input", choices=("auto", "text", "audio"), default="auto",
                   help="auto: WAV if available, else text")
    g.add_argument("--wake", help="wake word text sent with listen/detect (default per scenario)")
    g.add_argument("--no-wake", action="store_true", help="do not send a wake word first")
    g.add_argument("--mode", choices=("auto", "manual", "realtime"),
                   help="listening mode: auto = server VAD, manual = PTT hold (listen stop)")
    g.add_argument("--speech-end-ms", type=int,
                   help="end of speech inside the WAV (default: energy detector)")
    g.add_argument("--ptt-release-ms", type=int, default=0,
                   help="manual mode: delay between the last frame and listen stop")
    g.add_argument("--tail-ms", type=int, default=4000,
                   help="auto mode: max trailing silence streamed after the WAV")
    g.add_argument("--repeat", type=int, help="number of turns (E13 default 50)")
    g.add_argument("--reconnect", action="store_true",
                   help="new WebSocket for every turn (default: one connection)")
    g.add_argument("--pause-ms", type=int, default=1000, help="pause between turns")
    g.add_argument("--abort-after-ms", type=int,
                   help="send abort this long after the first reply audio (E7/E8)")
    g.add_argument("--abort-reason", choices=("none", "wake_word_detected"))
    g.add_argument("--opus-bitrate", type=int, default=24000)
    g = p.add_argument_group("expectations")
    g.add_argument("--expect-tool", action="append", help="device tool that must be called")
    g.add_argument("--expect-actions", help="ordered actions, e.g. mask_open,eyes_on")
    g.add_argument("--expect-no-mcp", action="store_true")
    g.add_argument("--expect-reply", action="store_true")
    g.add_argument("--expect-task", help="task id whose steps self.sequence.run must carry")
    g.add_argument("--tasks-file", help="tasks.yaml for --expect-task (default server/tasks)")
    g.add_argument("--max-eos-to-mcp-ms", type=float)
    g.add_argument("--max-eos-to-audio-ms", type=float)
    g.add_argument("--tz-offset", type=float, default=7.0, help="hours, for the E1 hour check")
    g = p.add_argument_group("timing and output")
    g.add_argument("--turn-timeout", type=float, help="seconds per turn (default 30, E6 60)")
    g.add_argument("--settle-ms", type=int, default=600,
                   help="wait after tts stop for further speech")
    g.add_argument("--out-dir", help="where summary.json and reply WAVs go "
                                     "(default tools/tests/e2e/out/<run>)")
    g.add_argument("--no-save", action="store_true", help="write nothing to disk")
    g.add_argument("--quiet", action="store_true", help="only print the JSON summary")
    g = p.add_argument_group("offline helpers")
    g.add_argument("--list-scenarios", action="store_true")
    g.add_argument("--dump-tools", action="store_true",
                   help="print the tools/list pages the firmware would send, then exit")
    g.add_argument("--with-user-tools", action="store_true", help="with --dump-tools")
    return p


def main(argv: Optional[list] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_scenarios:
        for sid in sorted(SCENARIOS, key=lambda s: int(s[1:])):
            s = SCENARIOS[sid]
            print(f"{sid:4} {s.title} | texts={s.texts[:2]} wake={s.wake} repeat={s.repeat} "
                  f"checks={list(s.checks)}")
        return EXIT_OK
    if args.dump_tools:
        return dump_tools(args)
    if not args.ota_url and not args.ws_url:
        parser.error("give --ota-url or --ws-url (or an offline helper option)")
    if args.repeat is not None and args.repeat < 1:
        parser.error("--repeat must be >= 1")
    scenario = SCENARIOS.get(args.scenario) if args.scenario else None

    def log(msg: str) -> None:
        if not args.quiet:
            stamp = _dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"[{stamp}] {msg}", file=sys.stderr, flush=True)

    try:
        summary = asyncio.run(run_simulation(args, scenario, log))
    except SimError as e:
        print(json.dumps({"tool": "device_sim", "error": str(e), "pass": False},
                         ensure_ascii=False, indent=2))
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_ERROR
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not args.quiet:
        for a in summary["assertions"]:
            mark = "PASS" if a["ok"] else ("WARN" if a["soft"] else "FAIL")
            print(f"  [{mark}] {a['name']}: {a['detail']}", file=sys.stderr)
        print("RESULT: " + ("PASS" if summary["pass"] else "FAIL"), file=sys.stderr)
    if any(c["protocol_errors"] for c in summary.get("connections", [])) and not summary["turns"]:
        return EXIT_ERROR
    return EXIT_OK if summary["pass"] else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
