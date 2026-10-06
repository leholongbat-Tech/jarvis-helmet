"""Runtime test of the `jarvis_stream` provider INSIDE the xiaozhi-server image (Python 3.10).

Skipped everywhere else (needs xiaozhi's `core` package, opuslib_next, loguru ...). It drives
the real provider class (real TTSProviderBase, real OpusEncoderUtils) through its text thread
and checks the audio queue: FIRST/MIDDLE/LAST items, Opus frames decodable at the connection
rate with the expected duration, abort mid-stream (the HTTP stream is closed), stale-turn
filtering, error handling (log + skip, subtitle still sent), base.to_tts() for the wake-reply
cache and text_to_speak(). By default it talks to a built-in fake router (stdlib HTTP server
streaming a tone); set JARVIS_TTS_URL to also synthesize through a real tts-router.

Run it inside the image (pytest is not installed there, so the file also runs as a script):
  docker run --rm --entrypoint python -w /opt/xiaozhi-esp32-server \
      -v "$PWD/server/xiaozhi/providers/tts/jarvis_stream.py:/opt/xiaozhi-esp32-server/core/providers/tts/jarvis_stream.py:ro" \
      -v "$PWD/server/tests/test_tts_provider_runtime.py:/tmp/test_tts_provider_runtime.py:ro" \
      [-e JARVIS_TTS_URL=http://tts-router:8880/v1/audio/speech --network <net>] \
      jarvis/xiaozhi:dev /tmp/test_tts_provider_runtime.py
(with jarvis/xiaozhi:dev the provider is already in the image; the first -v is only needed
for the upstream image). When data/.config.yaml is missing (entrypoint not run) a minimal one
is written so xiaozhi's logger can start; do not run this in a live container.
"""
import asyncio
import json
import os
import queue
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

XIAOZHI_ROOT = os.environ.get("XIAOZHI_ROOT", "/opt/xiaozhi-esp32-server")


def _runtime_available() -> bool:
    return os.path.isfile(os.path.join(XIAOZHI_ROOT, "core", "providers", "tts", "base.py"))


if _runtime_available():
    os.chdir(XIAOZHI_ROOT)
    if XIAOZHI_ROOT not in sys.path:
        sys.path.insert(0, XIAOZHI_ROOT)
    cfg_path = os.path.join(XIAOZHI_ROOT, "data", ".config.yaml")
    if not os.path.exists(cfg_path):
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write("# minimal config written by test_tts_provider_runtime.py\nlog:\n  log_level: INFO\n")

try:
    import pytest
except ImportError:  # plain script inside the image
    pytest = None

if pytest is not None and not _runtime_available():
    pytest.skip("xiaozhi-server runtime not available (run inside the xiaozhi image)", allow_module_level=True)

RATE = 24000


# --- fake router ----------------------------------------------------------------------------

class FakeRouter:
    def __init__(self):
        self.requests = []
        self.fail_status = 0
        self.seconds = 0.6
        self.pace = 0.0  # seconds between 100 ms chunks
        self.disconnects = 0
        self.finished = 0
        router = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                router.requests.append(body)
                if router.fail_status:
                    msg = b'{"error": {"message": "boom"}}'
                    self.send_response(router.fail_status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(msg)))
                    self.end_headers()
                    self.wfile.write(msg)
                    return
                rate = int(body.get("sample_rate", RATE))
                pcm = tone(rate, router.seconds)
                if not body.get("stream", True):
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/pcm")
                    self.send_header("Content-Length", str(len(pcm)))
                    self.end_headers()
                    self.wfile.write(pcm)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "audio/pcm")
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("X-Sample-Rate", str(rate))
                self.end_headers()
                step = rate // 10 * 2 + 1  # odd size on purpose: chunks split samples
                try:
                    for i in range(0, len(pcm), step):
                        piece = pcm[i:i + step]
                        self.wfile.write(b"%x\r\n" % len(piece) + piece + b"\r\n")
                        self.wfile.flush()
                        if router.pace:
                            time.sleep(router.pace)
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                    router.finished += 1
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    router.disconnects += 1

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1/audio/speech"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


def tone(rate, seconds):
    import numpy as np
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * 220 * t) * 8000).astype("<i2").tobytes()


# --- harness --------------------------------------------------------------------------------

class FakeConn:
    def __init__(self, rate=RATE):
        self.stop_event = threading.Event()
        self.client_abort = False
        self.sentence_id = None
        self.sample_rate = rate
        self.session_id = "test-session"
        self.audio_format = "opus"
        self.headers = {}
        self.config = {}
        self.max_output_size = 0


def make_provider(url, **extra):
    from core.utils.tts import create_instance
    cfg = {"type": "jarvis_stream", "api_url": url, "voice": "Hải Đăng", "sample_rate": RATE,
           "timeout": 5, "output_dir": "tmp/tts", "tts_timeout": 15}
    cfg.update(extra)
    provider = create_instance("jarvis_stream", cfg, True)
    conn = FakeConn()
    provider.conn = conn
    provider._ensure_encoder()
    thread = threading.Thread(target=provider.tts_text_priority_thread, daemon=True)
    thread.start()
    return provider, conn


def start_turn(provider, conn, texts, last=True):
    from core.providers.tts.dto.dto import ContentType, SentenceType, TTSMessageDTO
    sid = uuid.uuid4().hex
    conn.sentence_id = sid
    conn.client_abort = False
    q = provider.tts_text_queue
    q.put(TTSMessageDTO(sentence_id=sid, sentence_type=SentenceType.FIRST, content_type=ContentType.ACTION))
    for t in texts:
        q.put(TTSMessageDTO(sentence_id=sid, sentence_type=SentenceType.MIDDLE,
                            content_type=ContentType.TEXT, content_detail=t))
    if last:
        q.put(TTSMessageDTO(sentence_id=sid, sentence_type=SentenceType.LAST, content_type=ContentType.ACTION))
    return sid


def collect(provider, until_last=True, timeout=15.0, idle=1.5):
    """Drain the audio queue until LAST (or `idle` seconds without items)."""
    from core.providers.tts.dto.dto import SentenceType
    items, deadline = [], time.time() + timeout
    while time.time() < deadline:
        try:
            item = provider.tts_audio_queue.get(timeout=idle)
        except queue.Empty:
            if not until_last:
                break
            continue
        items.append(item)
        if until_last and item[0] == SentenceType.LAST:
            break
    return items


def decode_ms(packets, rate=RATE):
    import opuslib_next
    dec = opuslib_next.Decoder(rate, 1)
    samples = 0
    for p in packets:
        samples += len(dec.decode(bytes(p), rate * 60 // 1000)) // 2
    return samples * 1000 // rate


def summarize(items):
    from core.providers.tts.dto.dto import SentenceType
    firsts = [it[2] for it in items if it[0] == SentenceType.FIRST]
    packets = [it[1] for it in items if it[0] == SentenceType.MIDDLE and isinstance(it[1], (bytes, bytearray))]
    lasts = [it for it in items if it[0] == SentenceType.LAST]
    return firsts, packets, lasts


# --- tests ----------------------------------------------------------------------------------

def test_streams_segments_into_opus_frames():
    router = FakeRouter()
    try:
        provider, conn = make_provider(router.url)
        sid = start_turn(provider, conn, ["Vâng, thưa ngài", ". Điện áp là 5.02 V", ". Pin còn 85%."])
        firsts, packets, lasts = summarize(collect(provider))
        assert firsts == ["Vâng, thưa ngài.", "Điện áp là 5.02 V.", "Pin còn 85%."], firsts
        assert len(lasts) == 1 and lasts[0][3] == sid
        assert all(it_sid == sid for it_sid in [sid])
        ms = decode_ms(packets)
        assert 3 * 600 <= ms <= 3 * 600 + 3 * 60, ms  # 3 segments, last frame of each padded
        req = router.requests[0]
        assert req["sample_rate"] == RATE and req["response_format"] == "pcm" and req["voice"] == "Hải Đăng"
        conn.stop_event.set()
    finally:
        router.close()


def test_queued_sentences_are_not_glued():
    """tts_one_sentence items (task replies, tool RESPONSE texts) after streamed LLM text."""
    from core.providers.tts.dto.dto import ContentType, SentenceType, TTSMessageDTO
    router = FakeRouter()
    try:
        provider, conn = make_provider(router.url)
        sid = start_turn(provider, conn, ["Vâng."], last=False)  # streamed text, final '.' undecided
        provider.tts_one_sentence(conn, ContentType.TEXT, content_detail="Đã mở mặt nạ, thưa ngài.")
        provider.tts_one_sentence(conn, ContentType.TEXT, content_detail="Đèn mắt đã bật, thưa ngài.")
        firsts, _, _ = summarize(collect(provider, until_last=False, idle=2.0))
        # every complete sentence is spoken without waiting for LAST
        assert firsts == ["Vâng.", "Đã mở mặt nạ, thưa ngài.", "Đèn mắt đã bật, thưa ngài."], firsts
        provider.tts_text_queue.put(TTSMessageDTO(sentence_id=sid, sentence_type=SentenceType.LAST,
                                                  content_type=ContentType.ACTION))
        _, _, lasts = summarize(collect(provider))
        assert len(lasts) == 1 and len(router.requests) == 3
        conn.stop_event.set()
    finally:
        router.close()


def test_encoder_follows_connection_rate():
    router = FakeRouter()
    try:
        provider, conn = make_provider(router.url, sample_rate=24000)
        conn.sample_rate = 16000  # e.g. hello patch not applied
        start_turn(provider, conn, ["Xin chào thưa ngài."])
        firsts, packets, _ = summarize(collect(provider))
        assert router.requests[-1]["sample_rate"] == 16000
        assert provider.opus_encoder.sample_rate == 16000
        assert 600 <= decode_ms(packets, 16000) <= 660
        conn.stop_event.set()
    finally:
        router.close()


def test_abort_stops_stream_and_drops_rest_of_turn():
    router = FakeRouter()
    router.seconds, router.pace = 5.0, 0.05
    try:
        provider, conn = make_provider(router.url)
        start_turn(provider, conn, ["Một câu rất dài để thử ngắt lời. Câu thứ hai không được đọc."])
        # wait for the first audio, then abort like handleAbortMessage does
        deadline = time.time() + 5
        while provider.tts_audio_queue.qsize() < 3 and time.time() < deadline:
            time.sleep(0.01)
        conn.client_abort = True
        t_abort = time.time()
        items = collect(provider, until_last=False, idle=1.0)
        deadline = time.time() + 3
        while router.disconnects == 0 and time.time() < deadline:
            time.sleep(0.02)
        firsts, packets, lasts = summarize(items)
        assert router.disconnects == 1 and router.finished == 0
        assert len(router.requests) == 1, "second sentence must not be synthesized"
        assert not lasts, "an aborted turn queues no LAST (same as the base class)"
        assert time.time() - t_abort < 4
        conn.stop_event.set()
    finally:
        router.close()


def test_stale_turn_messages_are_dropped():
    from core.providers.tts.dto.dto import ContentType, SentenceType, TTSMessageDTO
    router = FakeRouter()
    try:
        provider, conn = make_provider(router.url)
        conn.sentence_id = "new-turn"
        provider.tts_text_queue.put(TTSMessageDTO(sentence_id="old-turn", sentence_type=SentenceType.MIDDLE,
                                                  content_type=ContentType.TEXT, content_detail="Cũ rồi."))
        provider.tts_text_queue.put(TTSMessageDTO(sentence_id="old-turn", sentence_type=SentenceType.LAST,
                                                  content_type=ContentType.ACTION))
        assert collect(provider, until_last=False, idle=1.0) == []
        assert router.requests == []
        conn.stop_event.set()
    finally:
        router.close()


def test_router_error_is_logged_and_skipped():
    router = FakeRouter()
    router.fail_status = 500
    try:
        provider, conn = make_provider(router.url, retries=1)
        sid = start_turn(provider, conn, ["Hệ thống lỗi. Câu sau vẫn hiện chữ."])
        firsts, packets, lasts = summarize(collect(provider))
        assert firsts == ["Hệ thống lỗi.", "Câu sau vẫn hiện chữ."]  # subtitles still sent
        assert packets == [] and len(lasts) == 1 and lasts[0][3] == sid
        assert len(router.requests) == 4  # 2 segments x (1 try + 1 retry)
        conn.stop_event.set()
    finally:
        router.close()


def test_to_tts_and_text_to_speak():
    import wave
    router = FakeRouter()
    try:
        provider, conn = make_provider(router.url)
        packets = provider.to_tts("Vâng, thưa ngài.")  # wake-reply cache path (base class)
        assert packets and 560 <= decode_ms(packets) <= 700
        assert router.requests[-1]["stream"] is False
        out = os.path.join("tmp", f"jarvis-stream-test-{uuid.uuid4().hex}.wav")
        os.makedirs("tmp", exist_ok=True)
        asyncio.run(provider.text_to_speak("Xin chào.", out))
        with wave.open(out) as w:
            assert w.getframerate() == RATE and w.getnframes() == int(RATE * router.seconds)
        os.remove(out)
        conn.stop_event.set()
    finally:
        router.close()


def test_real_router_if_configured():
    url = os.environ.get("JARVIS_TTS_URL")
    if not url:
        if pytest is not None:
            pytest.skip("JARVIS_TTS_URL not set")
        print("  (skipped: JARVIS_TTS_URL not set)")
        return
    import numpy as np
    provider, conn = make_provider(url, timeout=30)
    t0 = time.time()
    start_turn(provider, conn, ["Xin chào, tôi là J.A.R.V.I.S. Điện áp hiện tại là 5,02 V."])
    items = collect(provider, timeout=60)
    firsts, packets, lasts = summarize(items)
    ms = decode_ms(packets)
    import opuslib_next
    dec = opuslib_next.Decoder(RATE, 1)
    pcm = b"".join(dec.decode(bytes(p), RATE * 60 // 1000) for p in packets)
    rms = float(np.sqrt(np.mean(np.frombuffer(pcm, dtype="<i2").astype(np.float64) ** 2))) if pcm else 0.0
    print(f"  real router: segments={firsts} audio_ms={ms} rms={rms:.0f} wall_s={time.time() - t0:.2f}")
    assert lasts and ms > 1000 and rms > 100
    conn.stop_event.set()


def main() -> int:
    if not _runtime_available():
        print("xiaozhi runtime not available")
        return 2
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}\n{traceback.format_exc()}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
