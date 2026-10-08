"""ask_expert: forward deep or up-to-date questions to the cloud model behind LiteLLM.

The local LLM calls `ask_expert(question)`; the plugin asks LiteLLM model `jarvis-cloud`
(OpenAI-compatible /chat/completions) for a short, speakable answer and returns it as
Action.RESPONSE, so it is spoken right away without a second local-LLM round trip.

Config (`plugins.ask_expert` in data/.config.yaml):
  base_url (http://litellm:4000/v1), model (jarvis-cloud), api_key (LiteLLM master key,
  falls back to env LITELLM_MASTER_KEY), timeout_s (20, keep below tool_call_timeout),
  max_tokens (400); optional: system_prompt, max_chars (700), filler_vi / filler_en
  (spoken while waiting; empty string disables).
Python 3.10 compatible (runs inside the xiaozhi image).
"""
import asyncio
import os
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING

import httpx

from config.logger import setup_logging
from core.providers.tts.dto.dto import ContentType, SentenceType, TTSMessageDTO
from plugins_func.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

DEFAULTS = {
    "base_url": "http://litellm:4000/v1",
    "model": "jarvis-cloud",
    "timeout_s": 20,
    "max_tokens": 400,
    "max_chars": 700,
}

SYSTEM_PROMPT = (
    "You are the expert consultant behind a refined British-style butler AI built into the owner's helmet. "
    "Your answer is read aloud by a speech synthesizer. Answer briefly for speech: one to three short "
    "sentences of plain text, with no markdown, lists, emoji or symbols. Always answer in the same language "
    "as the question (Vietnamese question -> Vietnamese answer, English -> English; mixed Vietnamese-English "
    "-> Vietnamese, keeping English technical terms). Butler style: polite, calm, precise; address the user "
    "as 'thưa ngài' in Vietnamese or 'Sir' in English. If you are not sure, say so briefly instead of "
    "guessing. Never say the words 'Jarvis', 'quản gia' or 'hàu ơi'. Today is {today} (Asia/Ho_Chi_Minh)."
)

TEXTS = {
    "vi": {
        "filler": "Vâng thưa ngài, để tôi hỏi ý kiến chuyên gia.",
        "empty": "Ngài muốn hỏi chuyên gia điều gì ạ?",
        "timeout": "Xin lỗi thưa ngài, chuyên gia chưa trả lời kịp. Ngài vui lòng thử lại sau.",
        "error": "Xin lỗi thưa ngài, hiện tôi chưa liên lạc được với chuyên gia.",
        "no_answer": "Xin lỗi thưa ngài, chuyên gia không đưa ra được câu trả lời.",
    },
    "en": {
        "filler": "One moment, Sir, let me consult the expert.",
        "empty": "What would you like me to ask the expert, Sir?",
        "timeout": "I'm sorry, Sir, the expert did not answer in time. Please try again later.",
        "error": "I'm sorry, Sir, I cannot reach the expert at the moment.",
        "no_answer": "I'm sorry, Sir, the expert could not provide an answer.",
    },
}

ASK_EXPERT_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "ask_expert",
        "description": (
            "Hỏi chuyên gia (model cloud mạnh hơn). Chỉ gọi khi câu hỏi cần kiến thức chuyên sâu hoặc thông tin "
            "mới, khi bạn không chắc chắn, hoặc khi người dùng nói 'hỏi chuyên gia', 'hỏi cloud', 'ask the "
            "expert'. Không dùng để điều khiển mũ. / Ask the expert (a stronger cloud model): call only for deep "
            "or up-to-date knowledge, when you are unsure, or when the user explicitly asks for the expert; "
            "never for helmet control."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Câu hỏi đầy đủ, giữ nguyên ngôn ngữ của người dùng / The full question, in the user's language",
                }
            },
            "required": ["question"],
        },
    },
}

_VI_LETTERS = set(
    "ăâđêôơư"
    "áàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"
)
_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)
_CODE_RE = re.compile(r"```.*?```", re.S)
_LIST_RE = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s+", re.M)


def _lang(text):
    """'vi' unless the text is clearly English (ASCII letters, no Vietnamese letters)."""
    low = (text or "").lower()
    if any(ch in _VI_LETTERS for ch in low):
        return "vi"
    letters = [ch for ch in low if ch.isalpha()]
    if letters and all(ch.isascii() for ch in letters):
        return "en"
    return "vi"


def _config(conn):
    cfg = dict(DEFAULTS)
    try:
        cfg.update({k: v for k, v in ((conn.config.get("plugins") or {}).get("ask_expert") or {}).items() if v is not None})
    except Exception:
        pass
    if not cfg.get("api_key"):
        cfg["api_key"] = os.environ.get("LITELLM_MASTER_KEY", "")
    return cfg


def _text(cfg, lang, key):
    custom = cfg.get("%s_%s" % (key, lang))
    if isinstance(custom, str):
        return custom
    return TEXTS[lang][key]


def _clean_for_speech(text, max_chars):
    text = _THINK_RE.sub(" ", text or "")
    text = _CODE_RE.sub(" ", text)
    text = _LIST_RE.sub("", text)
    text = re.sub(r"[*#`]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if max_chars and len(text) > max_chars:
        cut = text[:max_chars]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind("; "))
        text = cut[: end + 1] if end >= max_chars // 3 else cut.rstrip() + "..."
    return text


def _extract_content(data):
    message = ((data or {}).get("choices") or [{}])[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):  # multi-part content
        content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content if isinstance(content, str) else ""


def _speak_filler(conn, text):
    """Queue a short sentence inside the current chat turn (spoken while we wait)."""
    try:
        tts = getattr(conn, "tts", None)
        if not text or tts is None or getattr(conn, "client_abort", False):
            return
        tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=conn.sentence_id,
                sentence_type=SentenceType.MIDDLE,
                content_type=ContentType.TEXT,
                # newlines: a streaming TTS speaks the filler now instead of holding a final "."
                # until the expert answer arrives (and gluing both into one sentence)
                content_detail="\n" + text.strip() + "\n",
            )
        )
    except Exception as e:
        logger.bind(tag=TAG).warning(f"ask_expert filler failed: {e}")


async def _ask_cloud(cfg, question):
    url = str(cfg["base_url"]).rstrip("/") + "/chat/completions"
    timeout_s = float(cfg["timeout_s"])
    system_prompt = cfg.get("system_prompt") or SYSTEM_PROMPT
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt.replace("{today}", datetime.now().strftime("%Y-%m-%d"))},
            {"role": "user", "content": question},
        ],
        "max_tokens": int(cfg["max_tokens"]),
        "stream": False,
    }
    headers = {"Authorization": "Bearer %s" % (cfg.get("api_key") or "none")}
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=min(5.0, timeout_s))) as client:
        resp = await asyncio.wait_for(client.post(url, json=payload, headers=headers), timeout=timeout_s)
    resp.raise_for_status()
    return _extract_content(resp.json())


@register_function("ask_expert", ASK_EXPERT_FUNCTION_DESC, ToolType.SYSTEM_CTL)
async def ask_expert(conn: "ConnectionHandler", question: str = "", **kwargs):
    cfg = _config(conn)
    question = str(question or kwargs.get("query") or "").strip()[:2000]
    lang = _lang(question)
    if not question:
        reply = _text(cfg, lang, "empty")
        return ActionResponse(Action.RESPONSE, result=reply, response=reply)

    _speak_filler(conn, _text(cfg, lang, "filler"))
    started = time.monotonic()
    try:
        raw = await _ask_cloud(cfg, question)
        answer = _clean_for_speech(raw, int(cfg.get("max_chars") or 0))
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if not answer:
            logger.bind(tag=TAG).warning(f"ask_expert: empty answer after {elapsed_ms} ms")
            answer = _text(cfg, lang, "no_answer")
        else:
            logger.bind(tag=TAG).info(f"ask_expert: {elapsed_ms} ms, {len(answer)} chars, model={cfg['model']}")
    except (asyncio.TimeoutError, httpx.TimeoutException):
        logger.bind(tag=TAG).error(f"ask_expert: timeout after {cfg['timeout_s']} s")
        answer = _text(cfg, lang, "timeout")
    except httpx.HTTPStatusError as e:
        body = e.response.text[:200] if e.response is not None else ""
        logger.bind(tag=TAG).error(f"ask_expert: HTTP {e.response.status_code if e.response is not None else '?'}: {body}")
        answer = _text(cfg, lang, "error")
    except Exception as e:
        logger.bind(tag=TAG).error(f"ask_expert: {type(e).__name__}: {e}")
        answer = _text(cfg, lang, "error")
    return ActionResponse(Action.RESPONSE, result=answer, response=answer)
