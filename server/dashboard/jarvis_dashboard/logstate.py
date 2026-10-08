"""Live picture rebuilt from xiaozhi's tmp/server.log: devices, sessions, turns, latency, errors.

File format (log_format_file in server/xiaozhi/data/.config.yaml.tmpl):

    2026-10-08 10:50:18.246 - 0.9.7_SiOpJaJanocaCh - core.connection - INFO - core.connection - <message>

The messages used here are upstream's (Chinese) plus the [LAT] lines of our patches
(server/xiaozhi/patches/jarvis_support.py). Ordinary lines carry no device or session id, so a turn
belongs to the device of the most recent open session: right for one helmet per server, mixed up
when a second client (e.g. tools/tests/e2e/device_sim.py) talks at the same time.
"""
from __future__ import annotations

import ast
import json
import os
import re
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Deque, Dict, List, Optional

LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?) - (?P<ver>\S+) - (?P<name>\S+) - "
    r"(?P<level>[A-Z]+) - (?P<tag>\S*) - (?P<msg>.*)$")
CONN_RE = re.compile(r"^(?P<ip>\S+) conn - Headers: (?P<headers>\{.*\})$")
LAT_RE = re.compile(r"\[LAT\] sid=(?P<sid>\S+) stage=(?P<stage>\w+) t_ms=(?P<t>\d+)(?P<rest>.*)$")
KV_RE = re.compile(r"(\w+)=(\S+)")
SENTENCE_RE = re.compile(r"^发送音频消息: SentenceType\.(?P<kind>\w+), (?P<text>.*)$")

# end_prompt of server/xiaozhi/data/.config.yaml.tmpl: the server sends it to the LLM as if the
# user had said it, when the user stays silent.
END_PROMPT_PREFIX = "Ngài chủ nhân đã im lặng"
# The firmware reports wake word detections as listen/detect with the word as text.
WAKE_WORDS = {"jarvis", "quản gia"}
# Log lines at these levels become events on the page.
EVENT_LEVELS = {"ERROR", "CRITICAL"}
# "客户端断开连接" names no device: with two clients a session can miss its end. One silent for this
# long (log time) is reported as stale.
STALE_SESSION_S = 600


@dataclass
class Turn:
    time: str
    device: str
    user: str = ""
    source: str = "voice"  # voice | text (listen/detect) | system (end_prompt, greeting)
    quick: str = ""  # quick phrase task id: answered without the LLM
    tools: List[str] = field(default_factory=list)
    reply: List[str] = field(default_factory=list)
    latency: Dict[str, int] = field(default_factory=dict)  # asr_ms, llm_ms, tts_ms, total_ms
    done: bool = False
    sid: str = ""
    marks: Dict[str, int] = field(default_factory=dict)  # epoch ms of this turn's stages


@dataclass
class Session:
    device: str
    ip: str
    started: str
    ended: str = ""
    state: str = "connecting"  # connecting | listening | thinking | speaking | closed | stale
    wake_word: str = ""
    last_activity: str = ""


@dataclass
class Device:
    id: str
    ip: str = ""
    user_agent: str = ""
    last_seen: str = ""
    last_ota: str = ""


class LogState:
    """Feed it log lines in order; snapshot() gives the JSON-ready picture."""

    def __init__(self, max_turns: int = 40, max_events: int = 30) -> None:
        self.devices: Dict[str, Device] = {}
        self.sessions: Dict[str, Session] = {}
        self.turns: Deque[Turn] = deque(maxlen=max_turns)
        self.events: Deque[dict] = deque(maxlen=max_events)
        self.current: Optional[str] = None  # device of the most recent session
        self.lines = 0
        self._utterance: Dict[str, Dict[str, int]] = {}  # sid -> speech_end / asr_done / asr_ms
        self._last_sid = ""
        self._last_ts = ""
        self._last_event: Optional[dict] = None

    # ---- feeding ------------------------------------------------------------------------------
    def feed(self, line: str) -> None:
        line = line.rstrip("\r\n")
        m = LINE_RE.match(line)
        if not m:  # traceback lines continue the previous ERROR event
            if self._last_event is not None and line.strip():
                self._last_event["detail"] = (self._last_event.get("detail", "") + line.strip() + "\n")[-1500:]
            return
        self.lines += 1
        ts, level, msg = m["ts"][:19], m["level"], m["msg"]
        self._last_event = None
        if level in EVENT_LEVELS:
            self._last_event = {"time": ts, "level": level, "module": m["name"], "text": msg[:400]}
            self.events.append(self._last_event)
        self._last_ts = ts
        session = self._session()
        if session is not None:
            session.last_activity = ts
        try:
            self._dispatch(ts, msg)
        except (ValueError, KeyError, TypeError, SyntaxError):
            pass  # an unexpected message shape never stops the dashboard

    def _dispatch(self, ts: str, msg: str) -> None:
        if msg.startswith("[LAT] "):
            self._on_lat(msg)
        elif " conn - Headers: " in msg:
            self._on_connect(ts, msg)
        elif msg.startswith("收到listen消息"):
            self._on_listen(ts, json.loads(msg.split("：", 1)[1]))
        elif msg.startswith("识别文本:"):
            text = msg.split(":", 1)[1].strip()
            if text:
                self._new_turn(ts, text, "voice")
        elif msg.startswith("大模型收到用户消息:"):
            self._on_llm_input(ts, msg.split(":", 1)[1].strip())
        elif msg.startswith("执行工具:"):
            turn = self._open_turn()
            if turn is not None:
                turn.tools.append(msg.split(":", 1)[1].strip().replace("，参数: ", " ")[:120])
        elif msg.startswith("发送音频消息:"):
            self._on_sentence(ts, msg)
        elif msg.startswith("客户端断开连接"):
            session = self.sessions.get(self.current or "")
            if session is not None and not session.ended:
                session.ended, session.state = ts, "closed"
        elif msg.startswith("OTA请求设备ID:"):
            dev = self._device(msg.split(":", 1)[1].strip())
            dev.last_ota = dev.last_seen = ts

    def _device(self, device_id: str) -> Device:
        dev = self.devices.get(device_id)
        if dev is None:
            dev = self.devices[device_id] = Device(id=device_id)
        return dev

    def _on_connect(self, ts: str, msg: str) -> None:
        m = CONN_RE.match(msg)
        if not m:
            return
        headers = ast.literal_eval(m["headers"])  # a Python dict repr written by the server
        device_id = str(headers.get("device-id") or m["ip"])
        dev = self._device(device_id)
        dev.ip, dev.last_seen = m["ip"], ts
        dev.user_agent = str(headers.get("user-agent", ""))[:60]
        self.sessions[device_id] = Session(device=device_id, ip=m["ip"], started=ts, last_activity=ts)
        self.current = device_id
        self._last_sid = ""  # [LAT] lines of the new session bring its id

    def _session(self) -> Optional[Session]:
        session = self.sessions.get(self.current or "")
        return session if session is not None and not session.ended else None

    def _on_listen(self, ts: str, body: dict) -> None:
        session = self._session()
        state = body.get("state")
        if state == "detect":
            text = str(body.get("text") or "").strip()
            if text.lower() in WAKE_WORDS:
                if session is not None:
                    session.wake_word = text
            elif text:
                self._new_turn(ts, text, "text")
        elif session is not None and state in ("start", "stop"):
            session.state = "listening" if state == "start" else "thinking"

    def _on_llm_input(self, ts: str, text: str) -> None:
        turn = self._open_turn()
        if turn is not None and turn.user == text and not turn.reply:
            return  # the same utterance, already opened by the ASR or listen/detect line
        self._new_turn(ts, text, "system" if text.startswith(END_PROMPT_PREFIX) else "text")

    def _on_sentence(self, ts: str, msg: str) -> None:
        m = SENTENCE_RE.match(msg)
        if not m:
            return
        session = self._session()
        if m["kind"] == "LAST":
            turn = self._open_turn()
            if turn is not None:
                turn.done = True
            if session is not None:
                session.state = "listening"
            return
        text = m["text"].strip()
        if not text or text == "None":
            return
        turn = self._open_turn() or self._new_turn(ts, "", "system")
        turn.reply.append(text)
        if session is not None:
            session.state = "speaking"

    def _new_turn(self, ts: str, text: str, source: str) -> Turn:
        open_turn = self._open_turn()
        if open_turn is not None:
            open_turn.done = True  # superseded (e.g. barge-in) or never answered
        turn = Turn(time=ts, device=self.current or "", user=text[:500], source=source, sid=self._last_sid)
        if source == "voice":  # stage times of the utterance just recognized
            utterance = self._utterance.get(self._last_sid, {})
            turn.marks.update({k: v for k, v in utterance.items() if k in ("speech_end", "asr_done")})
            if "asr_ms" in utterance and "asr_done" in turn.marks:
                turn.latency["asr_ms"] = utterance["asr_ms"]
        self.turns.append(turn)
        session = self._session()
        if session is not None:
            session.state = "thinking"
        return turn

    def _open_turn(self) -> Optional[Turn]:
        if self.turns and not self.turns[-1].done:
            return self.turns[-1]
        return None

    def _on_lat(self, msg: str) -> None:
        m = LAT_RE.search(msg)
        if not m:
            return
        sid, stage, t = m["sid"], m["stage"], int(m["t"])
        kv = dict(KV_RE.findall(m["rest"]))
        self._last_sid = sid
        if stage == "vad_end":  # a new utterance of this session
            self._utterance[sid] = {"speech_end": int(kv.get("speech_end_ms", t))}
            return
        turn = self._open_turn()
        if stage == "asr_done":
            utterance = self._utterance.setdefault(sid, {})
            utterance["asr_done"] = t
            if kv.get("dur_ms", "").isdigit():
                utterance["asr_ms"] = int(kv["dur_ms"])
            # xiaozhi logs the recognized text (which opens the turn) just before this line
            if (turn is not None and turn.source == "voice" and "asr_done" not in turn.marks
                    and turn.sid in ("", sid)):
                turn.marks["asr_done"] = t
                if "speech_end" in utterance:
                    turn.marks.setdefault("speech_end", utterance["speech_end"])
                if "asr_ms" in utterance:
                    turn.latency["asr_ms"] = utterance["asr_ms"]
            return
        if turn is None:
            return
        if not turn.sid:
            turn.sid = sid  # a typed (listen/detect) turn learns its session from the first stage
        if turn.sid != sid:
            return
        if stage == "quick_phrase":
            turn.quick = kv.get("task", "?")
        elif stage == "mcp_call":
            ok = "" if kv.get("ok", "1") == "1" else " lỗi"
            turn.tools.append(f"{kv.get('tool', '?')} {kv.get('ms', '?')} ms{ok}")
        marks = turn.marks
        if stage == "llm_first_token" and "tts_first" not in marks:
            # With tool calls the LLM runs again after the tools: the round that produces the
            # spoken answer is the last one before the first audio.
            marks["llm_first"] = t
            if "asr_done" in marks:
                turn.latency["llm_ms"] = t - marks["asr_done"]
        elif stage == "tts_first_audio" and "tts_first" not in marks:
            marks["tts_first"] = t
            start = marks.get("llm_first", marks.get("asr_done"))
            if start is not None:
                turn.latency["tts_ms"] = t - start
        elif stage == "first_packet_sent" and "first_packet" not in marks:
            marks["first_packet"] = t
            start = marks.get("speech_end", marks.get("asr_done"))
            if start is not None:
                turn.latency["total_ms"] = t - start

    # ---- output -------------------------------------------------------------------------------
    def snapshot(self) -> dict:
        devices = []
        for dev in sorted(self.devices.values(), key=lambda d: d.last_seen, reverse=True):
            item = asdict(dev)
            session = self.sessions.get(dev.id)
            item["session"] = asdict(session) if session is not None else None
            if session is not None and not session.ended and _seconds_between(
                    session.last_activity, self._last_ts) > STALE_SESSION_S:
                item["session"]["state"] = "stale"
            devices.append(item)
        turns = []
        for turn in reversed(self.turns):
            item = asdict(turn)
            item.pop("sid")
            item.pop("marks")
            turns.append(item)
        return {"devices": devices, "turns": turns, "events": list(reversed(self.events)), "lines": self.lines}


def _seconds_between(earlier: str, later: str) -> float:
    try:
        fmt = "%Y-%m-%d %H:%M:%S"
        return (datetime.strptime(later, fmt) - datetime.strptime(earlier, fmt)).total_seconds()
    except ValueError:
        return 0.0


class LogFollower:
    """Reads the new complete lines of a growing file at every poll().

    The file is opened for each poll only (a rotated-away file is never kept open), and its
    identity comes from fstat() of the handle actually read, so a rotation between two polls
    restarts at the top of the new file. The first poll starts `backlog_bytes` before the end.
    """

    def __init__(self, path: str, state: LogState, backlog_bytes: int = 512 * 1024) -> None:
        self.path = path
        self.state = state
        self.backlog_bytes = backlog_bytes
        self._ino: Optional[int] = None
        self._pos = 0
        self._rest = b""
        self.error = ""

    def poll(self) -> int:
        try:
            fh = open(self.path, "rb")
        except OSError as exc:
            self.error = f"{type(exc).__name__}: {self.path}"
            return 0
        with fh:
            st = os.fstat(fh.fileno())
            if self._ino is None:  # first poll: the recent past only, whole lines
                self._ino = st.st_ino
                if st.st_size > self.backlog_bytes:
                    fh.seek(st.st_size - self.backlog_bytes)
                    fh.readline()
                    self._pos = fh.tell()
            elif st.st_ino != self._ino or st.st_size < self._pos:  # rotated or truncated
                self._ino, self._pos, self._rest = st.st_ino, 0, b""
            self.error = ""
            fh.seek(self._pos)
            data = fh.read()
            self._pos = fh.tell()
        if not data:
            return 0
        lines = (self._rest + data).split(b"\n")
        self._rest = lines.pop()
        for raw in lines:
            self.state.feed(raw.decode("utf-8", errors="replace"))
        return len(lines)
