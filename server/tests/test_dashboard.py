"""Tests of the status page (server/dashboard): log parsing, log following, device and service
probes, auth. Log lines are copies of real tmp/server.log lines of the stack on batai.

    .venv/Scripts/python -m pytest server/tests/test_dashboard.py -q
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

import httpx
import pytest
import websockets
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))

from jarvis_dashboard.config import Settings  # noqa: E402
from jarvis_dashboard.device import DeviceLink, DeviceProbe, McpError, probe, query_device, refresh  # noqa: E402
from jarvis_dashboard.health import _detail, check_one  # noqa: E402
from jarvis_dashboard.logstate import LogFollower, LogState  # noqa: E402
from jarvis_dashboard.main import Monitor, create_app, is_virtual  # noqa: E402

MAC = "28:84:85:50:a1:38"
SID = "53928981-743e-4f98-b105-43f52947a73e"


def line(time: str, name: str, msg: str, level: str = "INFO", ver: str = "0.9.7_SiOpJaJanocaCh") -> str:
    return f"2026-10-08 {time} - {ver} - {name} - {level} - {name} - {msg}"


def lat(time: str, stage: str, t_ms: int, extra: str = "") -> str:
    return line(time, "core.utils.jarvis_support", f"[LAT] sid={SID} stage={stage} t_ms={t_ms} {extra}".rstrip())


CONNECT = line("10:50:12.169", "core.connection",
               "192.168.1.28 conn - Headers: {'host': '192.168.1.27', 'authorization': '***', 'client-id': "
               "'d48d40a2-9a30-4444-b28c-26f53cded566', 'connection': 'Upgrade', 'device-id': '" + MAC + "', "
               "'protocol-version': '1', 'user-agent': 'jarvis-helmet/2.5.0'}", ver="0.9.7_00000000000000")

TOOL_TURN = [
    line("10:49:01.000", "core.api.ota_handler", f"OTA请求设备ID: {MAC}", ver="0.9.7_00000000000000"),
    CONNECT,
    line("10:50:13.300", "core.handle.textMessageProcessor",
         '收到listen消息：{"session_id":"' + SID + '","type":"listen","state":"start","mode":"auto"}'),
    lat("10:50:16.500", "vad_end", 1791431416500, "src=vad mode=auto speech_end_ms=1791431415900 audio_ms=4020"),
    line("10:50:18.245", "core.providers.asr.base", "识别文本: Jarvis, xả mở lượng dụng 30.",
         ver="0.9.7_00000000000000"),
    lat("10:50:18.245", "asr_done", 1791431418245, "text_len=28 dur_ms=1658"),
    line("10:50:18.246", "core.connection", "大模型收到用户消息: Jarvis, xả mở lượng dụng 30."),
    lat("10:50:19.111", "llm_first_token", 1791431419111, "depth=0 turn=d579 kind=tool"),
    line("10:50:19.112", "core.providers.tools.unified_tool_manager",
         "执行工具: self_mask_open，参数: {'speed': 30}", ver="0.9.7_00000000000000"),
    lat("10:50:19.122", "mcp_call", 1791431419112, "tool=self_mask_open ms=10 ok=1 src=llm"),
    lat("10:50:22.571", "llm_first_token", 1791431422571, "depth=1 turn=d579 kind=content"),
    lat("10:50:22.809", "tts_first_audio", 1791431422809, "seg_chars=13 ttfb_ms=191"),
    line("10:50:22.810", "core.handle.sendAudioHandle", "发送第一段语音: Đã mở mặt nạ,"),
    line("10:50:22.811", "core.handle.sendAudioHandle", "发送音频消息: SentenceType.FIRST, Đã mở mặt nạ,"),
    lat("10:50:22.811", "first_packet_sent", 1791431422811, "turn=d579"),
    line("10:50:23.236", "core.handle.sendAudioHandle", "发送音频消息: SentenceType.FIRST, thưa ngài."),
    line("10:50:23.391", "core.handle.sendAudioHandle", "发送音频消息: SentenceType.LAST, None"),
]


def feed(state: LogState, lines) -> LogState:
    for text in lines:
        state.feed(text + "\n")
    return state


# ---- log parsing -------------------------------------------------------------------------------
def test_tool_turn_is_rebuilt_with_latency():
    snap = feed(LogState(), TOOL_TURN).snapshot()
    dev = snap["devices"][0]
    assert dev["id"] == MAC and dev["ip"] == "192.168.1.28"
    assert dev["user_agent"] == "jarvis-helmet/2.5.0"
    assert dev["last_ota"] == "2026-10-08 10:49:01"
    assert dev["session"]["state"] == "listening" and not dev["session"]["ended"]
    (turn,) = snap["turns"]
    assert turn["user"] == "Jarvis, xả mở lượng dụng 30." and turn["source"] == "voice"
    assert turn["reply"] == ["Đã mở mặt nạ,", "thưa ngài."]  # "发送第一段语音" is not counted twice
    assert turn["tools"] == ["self_mask_open {'speed': 30}", "self_mask_open 10 ms"]
    # LLM until the round that speaks (after the tool call), TTS from there to the first audio
    assert turn["latency"] == {"asr_ms": 1658, "llm_ms": 4326, "tts_ms": 238, "total_ms": 6911}
    assert turn["done"] and turn["device"] == MAC
    assert "sid" not in turn


def test_quick_phrase_end_prompt_wake_word_and_disconnect():
    lines = [
        CONNECT,
        line("10:50:13.000", "core.handle.textMessageProcessor",
             '收到listen消息：{"session_id":"x","type":"listen","state":"detect","text":"Jarvis"}'),
        lat("10:50:14.000", "vad_end", 1000, "speech_end_ms=900"),
        lat("10:50:14.500", "asr_done", 1500, "dur_ms=500"),
        line("10:50:14.500", "core.providers.asr.base", "识别文本: Mở mặt nạ."),
        line("10:50:14.510", "plugins_func.functions.helmet_tasks",
             f"[LAT] sid={SID} stage=quick_phrase t_ms=1510 task=open_mask score=100"),
        line("10:50:14.700", "core.handle.sendAudioHandle", "发送音频消息: SentenceType.FIRST, Vâng."),
        line("10:50:15.000", "core.handle.sendAudioHandle", "发送音频消息: SentenceType.LAST, None"),
        line("10:52:25.975", "core.connection",
             "大模型收到用户消息: Ngài chủ nhân đã im lặng một lúc. Hãy nói một câu chào tạm biệt thật ngắn gọn"),
        line("10:52:29.901", "core.handle.sendAudioHandle", "发送音频消息: SentenceType.FIRST, Thưa ngài, tạm biệt."),
        line("10:52:30.000", "core.handle.sendAudioHandle", "发送音频消息: SentenceType.LAST, None"),
        line("10:52:45.046", "core.connection", "客户端断开连接"),
    ]
    snap = feed(LogState(), lines).snapshot()
    goodbye, quick = snap["turns"]  # newest first
    assert quick["user"] == "Mở mặt nạ." and quick["quick"] == "open_mask"
    assert quick["latency"]["asr_ms"] == 500 and quick["reply"] == ["Vâng."]
    assert goodbye["source"] == "system" and goodbye["reply"] == ["Thưa ngài, tạm biệt."]
    session = snap["devices"][0]["session"]
    assert session["wake_word"] == "Jarvis"
    assert session["state"] == "closed" and session["ended"] == "2026-10-08 10:52:45"


def test_hau_oi_detection_is_a_wake_word_not_a_typed_turn():
    lines = [
        CONNECT,
        line("10:50:13.000", "core.handle.textMessageProcessor",
             '收到listen消息：{"session_id":"x","type":"listen","state":"detect","text":"Hàu ơi"}'),
    ]
    snap = feed(LogState(), lines).snapshot()
    assert snap["turns"] == []
    assert snap["devices"][0]["session"]["wake_word"] == "Hàu ơi"


def test_typed_command_and_unanswered_turns():
    lines = [
        CONNECT,
        line("10:50:13.000", "core.handle.textMessageProcessor",
             '收到listen消息：{"session_id":"x","type":"listen","state":"detect","text":"bật đèn mắt"}'),
        line("10:50:13.100", "core.connection", "大模型收到用户消息: bật đèn mắt"),  # same utterance
        line("10:50:20.000", "core.providers.asr.base", "识别文本: "),  # empty ASR result: ignored
        line("10:50:21.000", "core.providers.asr.base", "识别文本: câu thứ hai"),
    ]
    snap = feed(LogState(), lines).snapshot()
    second, first = snap["turns"]
    assert first["user"] == "bật đèn mắt" and first["source"] == "text"
    assert first["done"] and first["reply"] == []  # superseded by the next utterance
    assert second["user"] == "câu thứ hai" and not second["done"]
    assert snap["devices"][0]["session"]["state"] == "thinking"


def test_server_initiated_turns_do_not_borrow_old_speech_times():
    """Seen on batai: the end_prompt turn got llm_ms=122930 from an utterance two minutes older."""
    lines = TOOL_TURN + [
        lat("10:51:00.000", "vad_end", 1791431460000, "speech_end_ms=1791431459000"),
        lat("10:51:01.000", "asr_done", 1791431461000, "text_len=0 dur_ms=5"),  # empty result: no turn
        line("10:52:25.975", "core.connection",
             "大模型收到用户消息: Ngài chủ nhân đã im lặng một lúc. Hãy nói một câu chào tạm biệt thật ngắn gọn"),
        lat("10:52:26.000", "llm_first_token", 1791431546000, "depth=0 kind=content"),
        lat("10:52:26.300", "tts_first_audio", 1791431546300, "ttfb_ms=220"),
        lat("10:52:26.310", "first_packet_sent", 1791431546310),
        line("10:52:26.310", "core.handle.sendAudioHandle", "发送音频消息: SentenceType.FIRST, Xin phép, thưa ngài."),
    ]
    goodbye = feed(LogState(), lines).snapshot()["turns"][0]
    assert goodbye["source"] == "system"
    assert goodbye["latency"] == {"tts_ms": 300}


def test_a_session_without_its_disconnect_line_goes_stale():
    sim = CONNECT.replace("192.168.1.28 conn", "192.168.1.32 conn").replace(MAC, "02:4a:48:4d:00:01")
    lines = [sim, CONNECT.replace("10:50:12.169", "10:51:00.000"),
             line("11:05:00.000", "core.connection", "大模型收到用户消息: còn đó không"),
             line("11:05:01.000", "core.connection", "客户端断开连接")]  # whose? taken as the helmet's
    devices = {d["id"]: d for d in feed(LogState(), lines).snapshot()["devices"]}
    assert devices[MAC]["session"]["state"] == "closed"
    assert devices["02:4a:48:4d:00:01"]["session"]["state"] == "stale"


def test_errors_keep_their_traceback_and_garbage_is_ignored():
    lines = [
        line("11:00:00.000", "core.providers.asr.base", "处理语音停止失败: no close frame received or sent",
             level="ERROR", ver="0.9.7_00000000000000"),
        "Traceback (most recent call last):",
        '  File "x.py", line 1, in <module>',
        "not a log line at all { ",
        line("11:00:01.000", "core.connection", "收到listen消息：{not json"),
        line("11:00:02.000", "core.connection", "1.2.3.4 conn - Headers: {broken"),
    ]
    state = feed(LogState(), lines)
    (event,) = state.snapshot()["events"]
    assert event["level"] == "ERROR" and "no close frame" in event["text"]
    assert "Traceback" in event["detail"] and "not a log line" in event["detail"]
    assert state.lines == 3


# ---- log follower ------------------------------------------------------------------------------
def test_follower_reads_complete_lines_and_survives_rotation(tmp_path):
    path = tmp_path / "server.log"
    path.write_text("\n".join(TOOL_TURN[:2]) + "\n" + TOOL_TURN[2][:40], encoding="utf-8")
    state = LogState()
    follower = LogFollower(str(path), state)
    assert follower.poll() == 2 and state.lines == 2
    with path.open("a", encoding="utf-8") as fh:  # finish the cut line
        fh.write(TOOL_TURN[2][40:] + "\n")
    assert follower.poll() == 1 and state.lines == 3
    path.write_text(TOOL_TURN[3] + "\n", encoding="utf-8")  # rotated/truncated: read from the start
    assert follower.poll() == 1 and state.lines == 4
    path.unlink()
    assert follower.poll() == 0 and "server.log" in follower.error


def test_follower_starts_with_the_recent_backlog_only(tmp_path):
    path = tmp_path / "server.log"
    old = [line(f"09:00:{i % 60:02d}.000", "x", f"old {i}") for i in range(2000)]
    path.write_text("\n".join(old + TOOL_TURN) + "\n", encoding="utf-8")
    state = LogState()
    LogFollower(str(path), state, backlog_bytes=4096).poll()
    assert 0 < state.lines < 100  # whole lines only: the first, cut line is skipped
    assert state.snapshot()["turns"][0]["reply"] == ["Đã mở mặt nạ,", "thưa ngài."]


# ---- device probe ------------------------------------------------------------------------------
STATUS = {"mask": {"state": "closed", "position": 0}, "rssi": -49, "uptime_s": 32, "free_heap": 4318095,
          "audio": {"mic_dbfs": -24.2, "speaker_dbfs": -100, "mic_probe": "left"}}


async def _fake_helmet(ws, fail_status=False):
    calls = []
    async for raw in ws:
        req = json.loads(raw)
        calls.append(req["method"] if req["method"] != "tools/call" else req["params"]["name"])
        if req["method"] == "initialize":
            result = {"protocolVersion": "2024-11-05"}
        elif req["params"]["name"] == "self.system.get_status":
            text = "boom" if fail_status else json.dumps(STATUS)
            result = {"content": [{"type": "text", "text": text}], "isError": fail_status}
        else:
            result = {"content": [{"type": "text", "text": json.dumps({"audio_speaker": {"volume": 70}})}],
                      "isError": False}
        await ws.send(json.dumps({"type": "notification"}))  # noise the client must skip
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}))
    assert calls[0] == "initialize"
    assert set(calls[1:]) <= {"self.system.get_status", "self.get_device_status"}  # read-only tools only


def test_query_device_reads_both_status_tools():
    async def scenario():
        async with websockets.serve(_fake_helmet, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            return await query_device("127.0.0.1", port, timeout=5)

    result = asyncio.run(scenario())
    assert result["status"] == STATUS
    assert result["device_status"] == {"audio_speaker": {"volume": 70}}
    assert result["rtt_ms"] >= 0


def test_probe_reports_tool_errors_and_dead_hosts():
    async def scenario():
        async def failing(ws):
            await _fake_helmet(ws, fail_status=True)

        async with websockets.serve(failing, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with pytest.raises(McpError, match="boom"):
                await query_device("127.0.0.1", port, timeout=5)
            good = DeviceProbe(host="127.0.0.1", online=True, status=STATUS)
            await probe(good, port, "", "2026-10-08 11:00:00", timeout=5)
        dead = await probe(DeviceProbe(host="127.0.0.1"), port, "", "2026-10-08 11:00:05", timeout=2)
        return good, dead

    good, dead = asyncio.run(scenario())
    assert not good.online and "boom" in good.error and good.status == STATUS  # last good data kept
    assert not dead.online and dead.error and dead.checked == "2026-10-08 11:00:05"


def test_device_link_keeps_one_connection_and_reconnects():
    async def scenario():
        connections = []

        async def handler(ws):
            connections.append(ws)
            await _fake_helmet(ws)

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            link = DeviceLink("127.0.0.1", port, timeout=5, max_backoff=0.2)
            target = DeviceProbe(host="127.0.0.1")
            for i in range(3):  # once a second in the dashboard
                await refresh(link, target, f"12:00:0{i}", full=(i == 0))
            assert len(connections) == 1, "one persistent connection, not one per poll"
            assert target.online and target.status == STATUS
            assert target.device_status == {"audio_speaker": {"volume": 70}}
            await connections[0].close()  # the helmet rebooted
            await refresh(link, target, "12:00:03", full=False)
            assert not target.online and target.error
            error = target.error
            await refresh(link, target, "12:00:04", full=False)  # still backing off: error kept
            assert not target.online and target.error == error and len(connections) == 1
            await asyncio.sleep(1.1)  # first retry after 1 s
            await refresh(link, target, "12:00:05", full=False)
            assert target.online and target.updated == "12:00:05" and len(connections) == 2
            await link.close()

    asyncio.run(scenario())


# ---- services ----------------------------------------------------------------------------------
def test_service_details_and_failures():
    asr = httpx.Response(200, json={"status": "ok", "engine": "faster_whisper", "model": "medium", "device": "cpu",
                                    "compute_type": "int8"})
    assert _detail("asr", asr) == "faster_whisper · medium · cpu · int8"
    tts = httpx.Response(200, json={"status": "ok", "voice": "Thiền Tâm Đức",
                                    "backends": {"vieneu": {"healthy": True, "detail": {"backend": "onnx"}}}})
    assert _detail("tts-router", tts) == "VieNeu onnx · giọng Thiền Tâm Đức"
    assert _detail("litellm", httpx.Response(200, json="I'm alive!")) == "I'm alive!"
    assert _detail("xiaozhi", httpx.Response(200, text="OTA ok")) == "OK"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "down":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(503, json={"status": "loading"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return (await check_one(client, "asr", "http://asr:9000/health"),
                    await check_one(client, "litellm", "http://down:4000/health/liveliness"))

    loading, down = asyncio.run(scenario())
    assert not loading["ok"] and loading["detail"].startswith("HTTP 503")
    assert not down["ok"] and down["detail"] == "ConnectError"


# ---- app ---------------------------------------------------------------------------------------
def auth(user: str, password: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def test_settings_from_env():
    s = Settings.from_env({"DASHBOARD_PASSWORD": " s3cret ", "JARVIS_DEVICE_HOSTS": "192.168.1.28, ,10.0.0.9",
                           "OLLAMA_MODEL": "qwen3.6:35b", "DASHBOARD_DEVICE_POLL_S": "0.1"})
    assert s.password == "s3cret" and s.user == "jarvis"
    assert s.device_hosts == ["192.168.1.28", "10.0.0.9"]
    assert s.llm_model == "qwen3.6:35b" and s.device_poll_s == 0.5  # floor: never hammer the helmet
    assert Settings.from_env({}).device_poll_s == 1.0
    assert Settings.from_env({}).password == ""


def test_pages_need_the_password(tmp_path):
    log = tmp_path / "server.log"
    log.write_text("\n".join(TOOL_TURN) + "\n", encoding="utf-8")
    settings = Settings(password="s3cret", log_file=str(log), device_hosts=["192.168.1.99"])
    monitor = Monitor(settings)
    monitor.follower.poll()
    with TestClient(create_app(settings, monitor, background=False)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        for path in ("/", "/api/status"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers=auth("jarvis", "wrong")).status_code == 401
            assert client.get(path, headers=auth("admin", "s3cret")).status_code == 401
        page = client.get("/", headers=auth("jarvis", "s3cret"))
        assert page.status_code == 200 and "Jarvis Dashboard" in page.text
        assert page.headers["x-frame-options"] == "DENY" and page.headers["cache-control"] == "no-store"
        data = client.get("/api/status", headers=auth("jarvis", "s3cret")).json()
    assert set(data) >= {"now", "services", "devices", "turns", "events", "log", "llm"}
    assert data["turns"][0]["reply"] == ["Đã mở mặt nạ,", "thưa ngài."]
    assert data["log"]["lines"] == len(TOOL_TURN)


def test_empty_password_never_lets_anyone_in(tmp_path):
    settings = Settings(password="", log_file=str(tmp_path / "missing.log"))
    with TestClient(create_app(settings, background=False)) as client:
        assert client.get("/api/status", headers=auth("jarvis", "")).status_code == 401


def test_device_hosts_skip_virtual_devices_and_merge_probes(tmp_path):
    settings = Settings(password="x", log_file=str(tmp_path / "server.log"), device_hosts=["192.168.1.50"])
    monitor = Monitor(settings)
    sim = CONNECT.replace("192.168.1.28 conn", "192.168.1.32 conn").replace(MAC, "02:4a:48:4d:00:01")
    feed(monitor.logs, [CONNECT, sim])
    assert is_virtual("02:4a:48:4d:00:01") and not is_virtual(MAC)
    assert monitor.device_hosts() == ["192.168.1.50", "192.168.1.28"]  # the simulator's PC is not polled
    monitor.probes["192.168.1.28"] = DeviceProbe(host="192.168.1.28", online=True, status=STATUS)
    monitor.probes["192.168.1.50"] = DeviceProbe(host="192.168.1.50", error="TimeoutError")
    devices = {d["id"]: d for d in monitor.snapshot()["devices"]}
    assert devices[MAC]["mcp"]["status"] == STATUS
    assert devices["02:4a:48:4d:00:01"]["virtual"] and "mcp" not in devices["02:4a:48:4d:00:01"]
    assert devices["192.168.1.50"]["mcp"]["error"] == "TimeoutError"
