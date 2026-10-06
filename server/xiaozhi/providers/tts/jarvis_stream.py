"""Jarvis streaming TTS provider for xiaozhi-esp32-server v0.9.7 (``type: jarvis_stream``).

Installed in the image as ``core/providers/tts/jarvis_stream.py`` (the TTS factory imports
``core/providers/tts/<type>.py`` and instantiates ``TTSProvider``). It streams raw PCM
(s16le mono) from the Jarvis tts-router (``POST /v1/audio/speech``, OpenAI-style body with
``response_format: pcm``, ``sample_rate: <connection output rate>``) and encodes 60 ms Opus
frames as chunks arrive, so the first packet leaves about one TTFA after a segment is ready.

Modelled on upstream ``core/providers/tts/index_stream.py`` with:
- the base-class guards restored: messages of stale turns (``sentence_id`` changed) and
  everything while ``conn.client_abort`` is set are dropped; an abort also stops the HTTP
  stream in the middle of a segment (closing it tells the router/VieNeu to stop);
- a Vietnamese/English-aware sentence segmenter (upstream ignores ASCII "." and splits the
  first segment at any comma): split on . ! ? ; : and newline when followed by whitespace,
  first segment of a turn also at the first comma (fast first audio), never inside 5.02,
  14:32 or J.A.R.V.I.S;
- the Opus encoder rate taken from the connection (``conn.sample_rate``, i.e.
  ``xiaozhi.audio_params.sample_rate`` = 24000), the same rate is requested from the router;
- timeouts, a retry before the first audio byte, and log-and-skip on errors (the turn goes on,
  the segment text is still shown on the device);
- complete sentences queued with ``tts_one_sentence`` (speak_txt, tool replies) are spoken at
  once and never glued to the text before or after them;
- a ``[LAT] ... stage=tts_first_audio`` line on the first PCM of each turn.

Config (``TTS.<name>`` block): api_url, voice, sample_rate (24000), timeout (read timeout in
seconds, default tts_timeout), output_dir; optional: model, api_key, speed (Kokoro only),
connect_timeout (3), retries (1), max_segment_seconds (60), first_segment_min_chars (8),
max_segment_chars (220), correct_words (upstream "from|to" list).

The pure helpers at the top (segmenter, WAV writer) import nothing from xiaozhi, so unit tests
and tools can import this module outside the xiaozhi runtime.
"""
from __future__ import annotations

import io
import os
import queue
import re
import time
import traceback
import wave
from typing import List, Optional

# =============================================================================================
# Pure helpers (no xiaozhi imports)
# =============================================================================================

OPUS_RATES = (8000, 12000, 16000, 24000, 48000)
TERMINALS = ".!?;:…"
CJK_TERMINALS = "。！？；："  # 。！？；：
CLOSERS = "\"'”’»)]}"
OPENERS = "\"'“‘«([{"
COMMAS = ",，、"  # , ， 、
ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "st", "jr", "sr", "prof", "vs", "etc", "no", "fig", "approx",
    "inc", "ltd", "co", "tp", "ths", "ts", "pgs", "gs", "bs", "ks", "nxb",
})
_INITIALISM_RE = re.compile(r"(?:[A-Za-z]\.)+[A-Za-z]")
_LEADING_JUNK = " \t\r\n,.;:!?…-–—*•·" + CLOSERS + COMMAS + CJK_TERMINALS


def _word_before(text: str, i: int) -> str:
    j = i
    while j > 0 and not text[j - 1].isspace():
        j -= 1
    return text[j:i].lstrip(OPENERS)


def _protected_dot(text: str, i: int, run_end: int, seg_start: int) -> bool:
    """A single '.' at text[i] followed by whitespace that still does not end a sentence."""
    word = _word_before(text, i)
    if word.lower() in ABBREVIATIONS:
        return True
    nxt = text[run_end:].lstrip()[:1]
    if _INITIALISM_RE.fullmatch(word):  # J.A.R.V.I.S, v.v, e.g
        return not nxt.isupper()
    if len(word) == 1 and word.isalpha() and word.isupper():
        # initial ("A. Einstein") unless it is a unit after a number ("5,02 V. Hết.")
        prev = text[seg_start:i - 1].split()
        return not (prev and prev[-1][:1].isdigit())
    if word.isdigit() and text[seg_start:i].strip() == word:  # list marker "1. "
        return True
    return False


def find_sentence_end(text: str, final: bool = False) -> int:
    """Index just past the first sentence boundary in `text`, or -1 if there is none yet.

    A run of . ! ? ; : (plus closing quotes) ends a sentence only when whitespace follows; a
    run at the very end is undecidable while more text may come ("5." -> "5.02") and counts
    only when `final` is True. Newlines and CJK terminal marks always end a sentence.
    """
    n = len(text)
    i = 0
    while i < n:
        c = text[i]
        if c == "\n" or c == "\r":
            return i + 1
        if c in CJK_TERMINALS:
            j = i + 1
            while j < n and (text[j] in CJK_TERMINALS or text[j] in TERMINALS or text[j] in CLOSERS):
                j += 1
            return j
        if c in TERMINALS:
            j = i + 1
            while j < n and (text[j] in TERMINALS or text[j] in CLOSERS):
                j += 1
            if j >= n:
                return n if final else -1
            if text[j].isspace():
                if c == "." and j == i + 1 and _protected_dot(text, i, j, 0):
                    i = j
                    continue
                return j
            i = j
            continue
        i += 1
    return -1


def find_first_comma(text: str, min_chars: int) -> int:
    """Index just past the first comma that may end the first (fast) segment of a turn, or -1.

    The fragment must have at least `min_chars` characters and contain a non-ASCII letter:
    a pure-ASCII fragment ("Stealth mode engaged,") may be English inside a Vietnamese sentence
    and must not be routed to another voice on its own. An ASCII comma must be followed by
    whitespace (never "3,5" or "1,000").
    """
    n = len(text)
    for i, c in enumerate(text):
        if c not in COMMAS:
            continue
        if c == ",":
            if i + 1 >= n or not text[i + 1].isspace():
                continue
        fragment = text[: i + 1].strip()
        if len(fragment) >= min_chars and any(ord(ch) > 127 and ch.isalpha() for ch in fragment):
            return i + 1
    return -1


def soft_cut(text: str, max_chars: int) -> int:
    """Cut point for an over-long run without punctuation: last comma, else last space."""
    window = text[:max_chars]
    k = max(window.rfind(", "), window.rfind("，"), window.rfind("、"))
    if k > max_chars // 3:
        return k + 1
    k = window.rfind(" ")
    return k if k > 0 else max_chars


def clean_segment(text: str) -> str:
    """Strip whitespace and leading separators; '' if nothing speakable is left."""
    text = text.strip().lstrip(_LEADING_JUNK).strip()
    return text if any(ch.isalnum() for ch in text) else ""


class SentenceSegmenter:
    """Incremental segmenter for streamed LLM text (one instance per provider, reset per turn).

    feed() returns the segments completed by new text; flush() returns the rest at turn end.
    """

    def __init__(self, first_min_chars: int = 8, max_chars: int = 220):
        self.first_min_chars = max(1, int(first_min_chars))
        self.max_chars = max(40, int(max_chars))
        self._buf = ""
        self._first = True

    def reset(self) -> None:
        self._buf = ""
        self._first = True

    @property
    def pending(self) -> str:
        return self._buf

    def feed(self, text: str) -> List[str]:
        if text:
            self._buf += text
        return self._drain(final=False)

    def flush(self) -> List[str]:
        return self._drain(final=True)

    def _drain(self, final: bool) -> List[str]:
        out: List[str] = []
        while self._buf:
            cut = find_sentence_end(self._buf, final=final)
            if cut == -1 and self._first and not final:
                cut = find_first_comma(self._buf, self.first_min_chars)
            if cut == -1 and len(self._buf) > self.max_chars:
                cut = soft_cut(self._buf, self.max_chars)
            if cut == -1:
                if not final:
                    break
                cut = len(self._buf)
            segment = clean_segment(self._buf[:cut])
            self._buf = self._buf[cut:]
            if segment:
                out.append(segment)
                self._first = False
        return out


def split_segments(text: str, first_min_chars: int = 8, max_chars: int = 220) -> List[str]:
    """Segment a complete text the way a streamed reply would be (feed all, then flush)."""
    seg = SentenceSegmenter(first_min_chars, max_chars)
    return seg.feed(text) + seg.flush()


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap s16le mono PCM in a WAV container (exact sizes)."""
    pcm = pcm[: len(pcm) - len(pcm) % 2]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def opus_rate(value, default: int) -> int:
    """`value` as an Opus-compatible sample rate, else `default`."""
    try:
        rate = int(value)
    except (TypeError, ValueError):
        return default
    return rate if rate in OPUS_RATES else default


def _positive(value, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


# =============================================================================================
# xiaozhi runtime
# =============================================================================================

try:
    import httpx
    from config.logger import setup_logging
    from core.providers.tts.base import TTSProviderBase
    from core.providers.tts.dto.dto import ContentType, InterfaceType, SentenceType
    from core.utils import opus_encoder_utils

    _RUNTIME_ERROR: Optional[BaseException] = None
except ImportError as _exc:  # outside the xiaozhi image: only the pure helpers are usable
    TTSProviderBase = object  # type: ignore[assignment,misc]
    _RUNTIME_ERROR = _exc

TAG = __name__
logger = setup_logging() if _RUNTIME_ERROR is None else None


class _HTTPStatusError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status


class _SegmentState:
    __slots__ = ("t0", "ttfb", "nbytes", "got_audio", "aborted", "truncated")

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.ttfb: Optional[float] = None
        self.nbytes = 0
        self.got_audio = False
        self.aborted = False
        self.truncated = False


class TTSProvider(TTSProviderBase):  # type: ignore[misc,valid-type]
    def __init__(self, config, delete_audio_file):
        if _RUNTIME_ERROR is not None:
            raise RuntimeError("jarvis_stream needs the xiaozhi-server runtime") from _RUNTIME_ERROR
        super().__init__(config, delete_audio_file)
        self.interface_type = InterfaceType.SINGLE_STREAM
        self.api_url = str(config.get("api_url") or "http://tts-router:8880/v1/audio/speech")
        self.voice = str(config.get("private_voice") or config.get("voice") or "default")
        self.model = str(config.get("model") or "vieneu-v3-turbo")
        self.api_key = str(config.get("api_key") or "")
        self.sample_rate = opus_rate(config.get("sample_rate"), 24000)
        self.read_timeout = _positive(config.get("timeout"), self.tts_timeout)
        self.connect_timeout = _positive(config.get("connect_timeout"), 3.0)
        self.max_segment_seconds = _positive(config.get("max_segment_seconds"), 60.0)
        try:
            self.retries = max(0, int(config.get("retries", 1)))
        except (TypeError, ValueError):
            self.retries = 1
        speed = config.get("speed")
        self.speed = _positive(speed, 0.0) or None
        self.audio_file_type = "wav"  # text_to_speak() returns WAV for base.to_tts()
        self.opus_encoder = None  # created in open_audio_channels() at the connection's rate
        self.current_sentence_id = None
        self._segmenter = SentenceSegmenter(config.get("first_segment_min_chars", 8),
                                            config.get("max_segment_chars", 220))
        self._http = None
        self._pcm = bytearray()
        self._turn_audio_started = False
        self._rate_warned = False

    # ----- channels / encoder -----------------------------------------------------------------

    async def open_audio_channels(self, conn):
        self.conn = conn
        self._ensure_encoder()
        await super().open_audio_channels(conn)  # keeps our encoder, starts the two threads

    def _target_rate(self) -> int:
        conn_rate = opus_rate(getattr(self.conn, "sample_rate", None), 0)
        if not conn_rate:
            return self.sample_rate
        if conn_rate != self.sample_rate and not self._rate_warned:
            self._rate_warned = True
            logger.bind(tag=TAG).warning(
                f"jarvis_stream: connection output rate {conn_rate} Hz != configured sample_rate "
                f"{self.sample_rate} Hz; using {conn_rate} Hz")
        return conn_rate

    def _ensure_encoder(self) -> int:
        rate = self._target_rate()
        enc = self.opus_encoder
        if enc is None or getattr(enc, "sample_rate", None) != rate or getattr(enc, "encoder", None) is None:
            if enc is not None and hasattr(enc, "close"):
                enc.close()
            self.opus_encoder = opus_encoder_utils.OpusEncoderUtils(sample_rate=rate, channels=1, frame_size_ms=60)
        return rate

    def _client(self):
        if self._http is None:
            self._http = httpx.Client(
                timeout=httpx.Timeout(connect=self.connect_timeout, read=self.read_timeout,
                                      write=10.0, pool=self.connect_timeout),
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2, keepalive_expiry=30.0),
            )
        return self._http

    def _headers(self) -> dict:
        if self.api_key and self.api_key.lower() not in ("none", "null"):
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    # ----- text thread ------------------------------------------------------------------------

    def tts_one_sentence(self, conn, content_type, content_detail=None, content_file=None, sentence_id=None):
        """Queue one complete sentence (speak_txt, tool RESPONSE texts, plugin replies).

        Upstream splits these only at CJK marks, ! ? ; and newlines, and the streaming segmenter
        cannot tell a final ASCII '.' from the "5." of a streamed "5.02", so two queued replies
        would be glued into one segment ("thưa ngài.Đèn mắt ...") and a sentence would wait for
        the next text. Surrounding newlines end the pending text and this sentence right away.
        """
        if content_type == ContentType.TEXT and isinstance(content_detail, str) and content_detail.strip():
            content_detail = "\n" + content_detail.strip("\n") + "\n"
        return super().tts_one_sentence(conn, content_type, content_detail=content_detail,
                                        content_file=content_file, sentence_id=sentence_id)

    def tts_text_priority_thread(self):
        while not self.conn.stop_event.is_set():
            try:
                message = self.tts_text_queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self._on_text_message(message)
            except Exception as exc:  # the thread must survive anything
                logger.bind(tag=TAG).error(
                    f"jarvis_stream: message failed: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")

    def _on_text_message(self, message) -> None:
        conn = self.conn
        if conn.client_abort:
            return  # like the base class: drop everything until the next turn resets the flag
        if message.sentence_id != conn.sentence_id:
            return  # stale turn
        if message.sentence_type == SentenceType.FIRST:
            self._start_turn(message.sentence_id)
        elif message.content_type == ContentType.TEXT:
            if self.current_sentence_id != message.sentence_id:
                self._start_turn(message.sentence_id)  # text queued without FIRST
            if message.content_detail:
                for segment in self._segmenter.feed(message.content_detail):
                    self._speak(segment)
        elif message.content_type == ContentType.FILE:
            self._flush_text()
            if message.content_file and os.path.exists(message.content_file):
                self._process_audio_file_stream(message.content_file, callback=self.handle_opus)
        if message.sentence_type == SentenceType.LAST:
            self._flush_text()
            self.tts_audio_queue.put((SentenceType.LAST, [], message.content_detail, message.sentence_id))

    def _start_turn(self, sentence_id) -> None:
        self.current_sentence_id = sentence_id
        self.tts_stop_request = False
        self.processed_chars = 0
        self.tts_text_buff = []
        self.is_first_sentence = True
        self.tts_audio_first_sentence = True
        self._segmenter.reset()
        self._turn_audio_started = False
        self._pcm.clear()
        if self.opus_encoder is not None:
            self.opus_encoder.reset_state()

    def _flush_text(self) -> None:
        for segment in self._segmenter.flush():
            self._speak(segment)

    def _aborted(self, sentence_id) -> bool:
        conn = self.conn
        return bool(conn.client_abort or conn.stop_event.is_set() or conn.sentence_id != sentence_id)

    # ----- synthesis --------------------------------------------------------------------------

    def _speak(self, segment: str) -> None:
        """Stream one segment from the router into Opus frames. Errors are logged, never raised."""
        sid = self.current_sentence_id
        if self._aborted(sid):
            return
        text = segment
        if self._correct_words_pattern is not None:
            text = self._correct_words_pattern.sub(lambda m: self.correct_words[m.group(0)], text)
        text = text.strip()
        if not text:
            return
        rate = self._ensure_encoder()
        payload = {"model": self.model, "input": text, "response_format": "pcm",
                   "sample_rate": rate, "stream": True}
        if self.voice and self.voice != "default":
            payload["voice"] = self.voice
        if self.speed:
            payload["speed"] = self.speed
        state = _SegmentState()
        for attempt in range(self.retries + 1):
            if self._aborted(sid):
                break
            try:
                self._stream(payload, segment, sid, rate, state)
                break
            except Exception as exc:
                retryable = isinstance(exc, httpx.TransportError) or (
                    isinstance(exc, _HTTPStatusError) and (exc.status >= 500 or exc.status == 429))
                if self._aborted(sid):
                    state.aborted = True
                    self._pcm.clear()
                    break
                if state.got_audio:  # partial audio already queued: do not repeat it
                    logger.bind(tag=TAG).error(f"jarvis_stream: stream broke after audio: {exc}")
                    self._pcm.clear()
                    break
                if retryable and attempt < self.retries:
                    logger.bind(tag=TAG).warning(f"jarvis_stream: {exc}; retrying")
                    time.sleep(0.2)
                    continue
                logger.bind(tag=TAG).error(f"jarvis_stream: TTS failed, segment skipped ({exc}): {segment[:80]!r}")
                break
        if not state.got_audio and not self._aborted(sid):
            # no audio: still show the text on the device
            self.tts_audio_queue.put((SentenceType.FIRST, None, segment, sid))
        total_ms = (time.monotonic() - state.t0) * 1000
        logger.bind(tag=TAG).info(
            f"jarvis_stream: segment chars={len(segment)} ttfb_ms="
            f"{int(state.ttfb * 1000) if state.ttfb is not None else -1} total_ms={int(total_ms)} "
            f"audio_ms={int(state.nbytes / 2 / rate * 1000)}"
            f"{' aborted' if state.aborted else ''}{' truncated' if state.truncated else ''} "
            f"text={segment[:60]!r}")

    def _stream(self, payload: dict, segment: str, sid, rate: int, state: _SegmentState) -> None:
        frame_bytes = rate * 60 // 1000 * 2
        deadline = state.t0 + self.max_segment_seconds
        self._pcm.clear()
        with self._client().stream("POST", self.api_url, json=payload, headers=self._headers()) as resp:
            if resp.status_code != 200:
                body = resp.read()[:300].decode("utf-8", "replace")
                raise _HTTPStatusError(resp.status_code, body)
            for chunk in resp.iter_bytes():
                if self._aborted(sid):
                    state.aborted = True
                    break
                if not chunk:
                    continue
                if not state.got_audio:
                    state.got_audio = True
                    state.ttfb = time.monotonic() - state.t0
                    self.tts_audio_queue.put((SentenceType.FIRST, None, segment, sid))
                    if not self._turn_audio_started:
                        self._turn_audio_started = True
                        logger.bind(tag=TAG).info(
                            f"[LAT] sid={getattr(self.conn, 'session_id', '-')} stage=tts_first_audio "
                            f"t_ms={int(time.time() * 1000)} seg_chars={len(segment)} "
                            f"ttfb_ms={int(state.ttfb * 1000)}")
                state.nbytes += len(chunk)
                self._pcm.extend(chunk)
                whole = len(self._pcm) - len(self._pcm) % frame_bytes
                if whole:
                    self.opus_encoder.encode_pcm_to_opus_stream(
                        bytes(self._pcm[:whole]), end_of_stream=False, callback=self.handle_opus)
                    del self._pcm[:whole]
                if time.monotonic() > deadline:
                    state.truncated = True
                    logger.bind(tag=TAG).warning(
                        f"jarvis_stream: segment exceeded {self.max_segment_seconds:.0f}s, truncated")
                    break
        if state.aborted:
            self._pcm.clear()
            self.opus_encoder.reset_state()
            return
        tail = len(self._pcm) - len(self._pcm) % 2
        if tail:  # last partial frame, zero-padded by the encoder
            self.opus_encoder.encode_pcm_to_opus_stream(
                bytes(self._pcm[:tail]), end_of_stream=True, callback=self.handle_opus)
        self._pcm.clear()

    async def text_to_speak(self, text, output_file):
        """Non-streaming synthesis (base.to_tts() for the wake-reply cache, tests): WAV bytes,
        or written to `output_file` when given."""
        rate = self._target_rate()
        payload = {"model": self.model, "input": text, "response_format": "pcm",
                   "sample_rate": rate, "stream": False}
        if self.voice and self.voice != "default":
            payload["voice"] = self.voice
        timeout = httpx.Timeout(connect=self.connect_timeout, read=self.read_timeout,
                                write=10.0, pool=self.connect_timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(self.api_url, json=payload, headers=self._headers())
        if resp.status_code != 200:
            raise RuntimeError(f"jarvis_stream TTS request failed: {resp.status_code} {resp.text[:200]}")
        wav = pcm_to_wav(resp.content, rate)
        if output_file:
            with open(output_file, "wb") as f:
                f.write(wav)
            return None
        return wav

    async def close(self):
        await super().close()
        client, self._http = self._http, None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        if self.opus_encoder is not None:
            self.opus_encoder.close()  # encode() becomes a no-op if the thread is still running
