#!/usr/bin/env python3
"""Apply the Jarvis patches to an xiaozhi-esp32-server v0.9.7 tree.

Every edit is an exact, asserted string replacement:
  (file, anchor, replacement, expected anchor count)
and each replacement carries an idempotency marker `# [jarvis-patch:<id>]`.

Rules:
- marker already present        -> edit skipped ("already")
- anchor found `count` times    -> replaced ("applied")
- anything else                 -> FAIL: nothing is written, exit code 1
- every modified file must still compile (Python grammar of the running interpreter)
- support modules (jarvis_support.py) are installed next to the patched code

Usage:
  python apply_patches.py [--root DIR] [--check] [--strings FILE] [--list [--markdown]]

--root     xiaozhi-server root (default: $XIAOZHI_ROOT, /opt/xiaozhi-esp32-server, or cwd)
--check    verify that every edit is applicable or already applied; write nothing
--strings  localized strings (default: strings_vi.json next to this script)
--list     print the patch table (ids, files, purposes) and exit

Python 3.8+ (runs inside the xiaozhi image, Python 3.10).
"""
import argparse
import hashlib
import json
import os
import string
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MARK = "# [jarvis-patch:%s]"
EXPECTED_SERVER_VERSION = '"0.9.7"'

# installed file (relative to root) -> source file next to this script
SUPPORT_FILES = {"core/utils/jarvis_support.py": "jarvis_support.py"}

# The import block that patched modules use to reach core/utils/jarvis_support.py.
SUPPORT_IMPORT = '''
# [jarvis-patch:support_import] Jarvis helpers ([LAT] lines, header redaction); never raise
try:
    from core.utils import jarvis_support as _jarvis
except Exception:  # keep upstream behaviour if the helper is missing

    class _JarvisNoop:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    _jarvis = _JarvisNoop()
'''


class PatchError(Exception):
    pass


class Edit(object):
    """One anchored replacement inside one file."""

    def __init__(self, pid, path, anchor, replacement, purpose, count=1):
        self.id = pid
        self.file = path
        self.anchor = anchor
        self.replacement = replacement
        self.purpose = purpose
        self.count = count
        self.marker = MARK % pid
        if replacement.count(self.marker) != 1:
            raise PatchError("edit %s/%s: replacement must contain its marker exactly once" % (path, pid))
        if self.marker in anchor:
            raise PatchError("edit %s/%s: anchor must not contain the marker" % (path, pid))


# --------------------------------------------------------------------------- strings


class Strings(object):
    def __init__(self, data, source):
        self.data = data
        self.source = source

    def get(self, path):
        cur = self.data
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                raise PatchError("%s: missing key '%s'" % (self.source, path))
            cur = cur[part]
        return cur

    def get_str(self, path, allow_empty=False):
        value = self.get(path)
        if not isinstance(value, str) or (not allow_empty and not value.strip()):
            raise PatchError("%s: '%s' must be a %sstring" % (self.source, path, "" if allow_empty else "non-empty "))
        return value

    def text(self, path):
        value = self.get(path)
        if not isinstance(value, str) or not value.strip():
            raise PatchError("%s: '%s' must be a non-empty string" % (self.source, path))
        return value


def lit(value):
    """Python string literal (double quotes, UTF-8 kept)."""
    return json.dumps(value, ensure_ascii=False)


def template_expr(strings, path, **allowed):
    """Literal (or literal.format(...)) for a string that may use the allowed placeholders.

    `allowed` maps placeholder name -> Python expression available at the call site.
    """
    value = strings.text(path)
    used = []
    try:
        parsed = list(string.Formatter().parse(value))
    except ValueError as e:
        raise PatchError("%s: '%s' has a malformed placeholder: %s" % (strings.source, path, e))
    for _text, field, spec, conv in parsed:
        if field is None:
            continue
        if field not in allowed:
            raise PatchError(
                "%s: '%s' uses placeholder {%s}; allowed: %s"
                % (strings.source, path, field, ", ".join("{%s}" % k for k in sorted(allowed)) or "none")
            )
        if spec or conv:
            raise PatchError("%s: '%s' placeholder {%s} must not use format specs" % (strings.source, path, field))
        if field not in used:
            used.append(field)
    if "{" not in value and "}" not in value:
        return lit(value)
    return "%s.format(%s)" % (lit(value), ", ".join("%s=%s" % (k, allowed[k]) for k in used))


# --------------------------------------------------------------------------- edits


def support_import_edit(path, anchor="\nTAG = __name__\n"):
    return Edit(
        "support_import",
        path,
        anchor,
        anchor + SUPPORT_IMPORT,
        "import core/utils/jarvis_support.py (no-op fallback)",
    )


def build_edits(S):
    E = []
    add = E.append

    # ---------------------------------------------------------------- core/connection.py
    f = "core/connection.py"
    add(support_import_edit(f))
    add(Edit(
        "redact_headers", f,
        '            self.logger.bind(tag=TAG).info(\n'
        '                f"{self.client_ip} conn - Headers: {self.headers}"\n'
        '            )\n',
        '            self.logger.bind(tag=TAG).info(  # [jarvis-patch:redact_headers]\n'
        '                f"{self.client_ip} conn - Headers: {_jarvis.redact_headers(self.headers)}"\n'
        '            )\n',
        "D14: mask Authorization / Cookie / CF-Access-* in the connection header log line",
    ))
    add(Edit(
        "direct_answer_desc", f,
        '        "description": "当用户的请求不匹配其他任何工具时，可用此选项直接回复。将回复内容写在response参数里。",\n',
        '        "description": %s,  # [jarvis-patch:direct_answer_desc]\n' % lit(S.text("direct_answer.description")),
        "D17: direct_answer tool description (LLM) -> VI/EN",
    ))
    add(Edit(
        "direct_answer_param", f,
        '                    "description": "你回复用户的完整内容",\n',
        '                    "description": %s,  # [jarvis-patch:direct_answer_param]\n'
        % lit(S.text("direct_answer.response_description")),
        "D17: direct_answer.response parameter description (LLM) -> VI/EN",
    ))
    add(Edit(
        "fewshot_direct_user", f,
        '        self.dialogue.put(Message(role="user", content="给我讲个故事吧", is_temporary=True))\n',
        '        self.dialogue.put(Message(role="user", content=%s, is_temporary=True))'
        '  # [jarvis-patch:fewshot_direct_user]\n' % lit(S.text("fewshot.direct_user")),
        "D17: few-shot #1 user message (LLM)",
    ))
    add(Edit(
        "fewshot_direct_args", f,
        '                "function": {"arguments": \'{"response": "好呀，你想听什么类型的呀？童话、冒险还是搞笑的？选一个我给你开讲~"}\', "name": "direct_answer"},\n',
        '                "function": {"arguments": %s, "name": "direct_answer"},  # [jarvis-patch:fewshot_direct_args]\n'
        % lit(json.dumps({"response": S.text("fewshot.direct_response")}, ensure_ascii=False)),
        "D17: few-shot #1 direct_answer arguments (LLM)",
    ))
    add(Edit(
        "fewshot_direct_result", f,
        '            content="已直接回复", is_temporary=True,\n',
        '            content=%s, is_temporary=True,  # [jarvis-patch:fewshot_direct_result]\n'
        % lit(S.text("fewshot.direct_tool_result")),
        "D17: few-shot #1 tool result (LLM)",
    ))
    add(Edit(
        "fewshot_exit_user", f,
        '            self.dialogue.put(Message(role="user", content="拜拜", is_temporary=True))\n',
        '            self.dialogue.put(Message(role="user", content=%s, is_temporary=True))'
        '  # [jarvis-patch:fewshot_exit_user]\n' % lit(S.text("fewshot.exit_user")),
        "D17: few-shot #2 user message (LLM)",
    ))
    add(Edit(
        "fewshot_exit_args", f,
        '                    "function": {"arguments": \'{"say_goodbye": "再见，下次再聊~"}\', "name": "handle_exit_intent"},\n',
        '                    "function": {"arguments": %s, "name": "handle_exit_intent"},'
        '  # [jarvis-patch:fewshot_exit_args]\n'
        % lit(json.dumps({"say_goodbye": S.text("fewshot.exit_goodbye")}, ensure_ascii=False)),
        "D17: few-shot #2 handle_exit_intent arguments (LLM)",
    ))
    add(Edit(
        "fewshot_exit_result", f,
        '                content="退出意图已处理", is_temporary=True,\n',
        '                content=%s, is_temporary=True,  # [jarvis-patch:fewshot_exit_result]\n'
        % lit(S.text("fewshot.exit_tool_result")),
        "D17: few-shot #2 tool result (LLM)",
    ))
    add(Edit(
        "fewshot_exit_reply", f,
        '                role="assistant", content="再见，下次再聊~", is_temporary=True,\n',
        '                role="assistant", content=%s, is_temporary=True,  # [jarvis-patch:fewshot_exit_reply]\n'
        % lit(S.text("fewshot.exit_goodbye")),
        "D17: few-shot #2 assistant reply (LLM)",
    ))
    add(Edit(
        "max_depth_hint", f,
        '                    content="[系统提示] 已达到最大工具调用次数限制，请你基于目前已经获取的所有信息，直接给出最终答案。不要再尝试调用任何工具。",\n',
        '                    content=%s,  # [jarvis-patch:max_depth_hint]\n' % lit(S.text("max_depth_hint")),
        "D17: hint injected after 5 recursive tool rounds (LLM)",
    ))
    add(Edit(
        "lat_llm_first_token", f,
        "        emotion_flag = True\n"
        "        try:\n"
        "            for response in llm_responses:\n"
        "                if self.client_abort:\n"
        "                    break\n",
        "        emotion_flag = True\n"
        "        _jarvis_first_token = False  # [jarvis-patch:lat_llm_first_token]\n"
        "        try:\n"
        "            for response in llm_responses:\n"
        "                if self.client_abort:\n"
        "                    break\n"
        "                if not _jarvis_first_token:\n"
        "                    _jarvis_first_token = _jarvis.llm_first_token(self, response, depth, current_sentence_id)\n",
        "IT1: [LAT] stage=llm_first_token (first content or tool-call chunk)",
    ))
    add(Edit(
        "lat_llm_done", f,
        "        except Exception as e:\n"
        '            self.logger.bind(tag=TAG).error(f"LLM stream processing error: {e}")\n',
        '            _jarvis.lat(self, "llm_done", depth=depth, turn=current_sentence_id, tools=len(tool_calls_list),'
        " aborted=bool(self.client_abort))  # [jarvis-patch:lat_llm_done]\n"
        "        except Exception as e:\n"
        '            self.logger.bind(tag=TAG).error(f"LLM stream processing error: {e}")\n',
        "IT1: [LAT] stage=llm_done (LLM stream finished)",
    ))
    add(Edit(
        "tool_call_failed", f,
        '                            ActionResponse(action=Action.ERROR, result="哎呀，网络遇到点问题，请稍后再试下！"),\n',
        "                            ActionResponse(action=Action.ERROR, result=%s),  # [jarvis-patch:tool_call_failed]\n"
        % lit(S.text("tool_call_failed")),
        "D17: spoken text when a tool call times out / raises",
    ))

    # ---------------------------------------------------------------- core/handle/helloHandle.py
    f = "core/handle/helloHandle.py"
    responses = S.get("wakeup_responses")
    if not isinstance(responses, list) or not responses or not all(isinstance(r, str) and r.strip() for r in responses):
        raise PatchError("%s: 'wakeup_responses' must be a non-empty list of strings" % S.source)
    add(Edit(
        "wakeup_responses", f,
        '    "responses": [\n'
        '        "我一直都在呢，您请说。",\n'
        '        "在的呢，请随时吩咐我。",\n'
        '        "来啦来啦，请告诉我吧。",\n'
        '        "您请说，我正听着。",\n'
        '        "请您讲话，我准备好了。",\n'
        '        "请您说出指令吧。",\n'
        '        "我认真听着呢，请讲。",\n'
        '        "请问您需要什么帮助？",\n'
        '        "我在这里，等候您的指令。",\n'
        "    ],\n",
        '    "responses": [  # [jarvis-patch:wakeup_responses]\n'
        + "".join("        %s,\n" % lit(r) for r in responses)
        + "    ],\n",
        "D15: wake-word reply list (TTS-generated cache, spoken + displayed)",
    ))
    add(Edit(
        "hello_sample_rate", f,
        '        conn.welcome_msg["audio_params"] = audio_params\n',
        "        # [jarvis-patch:hello_sample_rate] announce the real downlink (TTS Opus) rate, not the device's\n"
        "        _jarvis_audio_params = dict(audio_params)\n"
        '        _jarvis_audio_params["sample_rate"] = conn.sample_rate\n'
        '        conn.welcome_msg["audio_params"] = _jarvis_audio_params\n',
        "D3: server hello audio_params.sample_rate = server output rate (24000)",
    ))
    add(Edit(
        "wakeup_fallback_text", f,
        '            "text": "我在这里哦！",\n',
        '            "text": %s,  # [jarvis-patch:wakeup_fallback_text]\n' % lit(S.text("wakeup_fallback_text")),
        "D15: text shown with the fallback wake sound (no cache yet)",
    ))

    # ---------------------------------------------------------------- core/handle/intentHandler.py
    f = "core/handle/intentHandler.py"
    add(Edit(
        "quick_phrase_hook", f,
        "    # 检查是否是唤醒词\n"
        "    if await checkWakeupWords(conn, filtered_text):\n"
        "        return True\n"
        "\n"
        '    if conn.intent_type == "function_call":\n',
        "    # 检查是否是唤醒词\n"
        "    if await checkWakeupWords(conn, filtered_text):\n"
        "        return True\n"
        "\n"
        "    # [jarvis-patch:quick_phrase_hook] helmet_tasks quick phrases run before the LLM (original text)\n"
        "    try:\n"
        "        from plugins_func.functions.helmet_tasks import try_quick_phrase as _jarvis_try_quick_phrase\n"
        "\n"
        "        if await _jarvis_try_quick_phrase(conn, text):\n"
        "            return True\n"
        "    except Exception as _jarvis_error:\n"
        '        conn.logger.bind(tag=TAG).error(f"[jarvis] quick-phrase hook failed: {_jarvis_error}")\n'
        "\n"
        '    if conn.intent_type == "function_call":\n',
        "4.7: await helmet_tasks.try_quick_phrase(conn, text) before the function_call return (guarded)",
    ))

    # ---------------------------------------------------------------- listenMessageHandler.py
    f = "core/handle/textHandler/listenMessageHandler.py"
    add(support_import_edit(f))
    add(Edit(
        "lat_vad_end_manual", f,
        '        elif msg_json["state"] == "stop":\n',
        '        elif msg_json["state"] == "stop":\n'
        '            _jarvis.lat(conn, "vad_end", src="listen_stop", mode=conn.client_listen_mode)'
        "  # [jarvis-patch:lat_vad_end_manual]\n",
        "IT1: [LAT] stage=vad_end src=listen_stop (PTT release / device end of speech)",
    ))
    add(Edit(
        "greeting_text", f,
        '                    enqueue_asr_report(conn, "嘿，你好呀", [])\n'
        '                    await startToChat(conn, "嘿，你好呀")\n',
        "                    enqueue_asr_report(conn, %s, [])  # [jarvis-patch:greeting_text]\n"
        "                    await startToChat(conn, %s)\n"
        % (lit(S.text("greeting_user_text")), lit(S.text("greeting_user_text"))),
        "D17: greeting user text when enable_greeting=true (displayed + sent to the LLM)",
    ))
    add(Edit(
        "wake_no_echo", f,
        "                if is_wakeup_words and not enable_greeting:\n"
        "                    # 如果是唤醒词，且关闭了唤醒词回复，就不用回答\n"
        "                    await send_stt_message(conn, original_text)\n"
        '                    await send_tts_message(conn, "stop", None)\n'
        "                    conn.client_is_speaking = False\n",
        "                if is_wakeup_words and not enable_greeting:\n"
        "                    # [jarvis-patch:wake_no_echo] no stt / tts start / tts stop echo: the device is already\n"
        "                    # Listening. A tts start flips it to Speaking (mic off) and its second listen start\n"
        "                    # resets the audio buffer, which cuts the start of a one-breath command (D15).\n"
        "                    conn.client_is_speaking = False\n"
        '                    conn.logger.bind(tag=TAG).info(f"[jarvis] wake word {original_text!r}: no greeting, listening")\n',
        "D15: wake word with enable_greeting=false sends nothing (keeps the device Listening, no dropped audio)",
    ))

    # ---------------------------------------------------------------- core/providers/asr/openai.py
    f = "core/providers/asr/openai.py"
    add(Edit(
        "asr_openai_import", f,
        "\nimport requests\n",
        "\nimport requests\n"
        "import asyncio  # [jarvis-patch:asr_openai_import] HTTP call in a worker thread\n",
        "event loop: import asyncio for the threaded ASR request",
    ))
    add(Edit(
        "asr_openai_timeout", f,
        '        self.output_dir = config.get("output_dir")\n',
        '        self.output_dir = config.get("output_dir")\n'
        "        # [jarvis-patch:asr_openai_timeout] (connect, read) seconds; read = config timeout_s (default 30)\n"
        "        try:\n"
        '            self.jarvis_timeout = (3.0, float(config.get("timeout_s") or 30))\n'
        "        except (TypeError, ValueError):\n"
        "            self.jarvis_timeout = (3.0, 30.0)\n",
        "event loop: bounded ASR request time (ASR.<name>.timeout_s, default 30 s)",
    ))
    add(Edit(
        "asr_openai_to_thread", f,
        "                response = requests.post(\n"
        "                    self.api_url,\n"
        "                    files=files,\n"
        "                    data=data,\n"
        "                    headers=headers\n"
        "                )\n",
        "                # [jarvis-patch:asr_openai_to_thread] blocking requests.post off the event loop, with a\n"
        "                # timeout: a stalled ASR service must not freeze every connection (Timeout -> \"\")\n"
        "                response = await asyncio.to_thread(\n"
        "                    requests.post,\n"
        "                    self.api_url,\n"
        "                    files=files,\n"
        "                    data=data,\n"
        "                    headers=headers,\n"
        '                    timeout=getattr(self, "jarvis_timeout", (3.0, 30.0)),\n'
        "                )\n",
        "event loop: OpenaiASR requests.post runs in asyncio.to_thread with a timeout",
    ))

    # ---------------------------------------------------------------- core/websocket_server.py
    f = "core/websocket_server.py"
    add(Edit(
        "ws_auth_failed_text", f,
        '            await websocket.send("认证失败")\n',
        "            await websocket.send(%s)  # [jarvis-patch:ws_auth_failed_text]\n" % lit(S.text("ws_auth_failed")),
        "D17: text frame sent to a client whose WebSocket token is rejected",
    ))

    # ---------------------------------------------------------------- sendAudioHandle.py
    f = "core/handle/sendAudioHandle.py"
    add(support_import_edit(f))
    add(Edit(
        "lat_first_packet", f,
        "    # 更新流控状态\n"
        '    flow_control["packet_count"] = packet_index + 1\n',
        "    if packet_index == 0:  # [jarvis-patch:lat_first_packet]\n"
        "        _jarvis.first_packet_sent(conn, flow_control)\n"
        "    # 更新流控状态\n"
        '    flow_control["packet_count"] = packet_index + 1\n',
        "IT1: [LAT] stage=first_packet_sent (first Opus frame of the turn written to the socket)",
    ))

    # ---------------------------------------------------------------- receiveAudioHandle.py
    f = "core/handle/receiveAudioHandle.py"
    add(Edit(
        "end_prompt_fallback", f,
        '                prompt = "请你以```时间过得真快```未来头，用富有感情、依依不舍的话来结束这场对话吧。！"\n',
        "                prompt = %s  # [jarvis-patch:end_prompt_fallback]\n" % lit(S.text("end_prompt_fallback")),
        "D17: idle-close prompt used when end_prompt.prompt is empty (LLM)",
    ))

    # ---------------------------------------------------------------- core/providers/asr/base.py
    f = "core/providers/asr/base.py"
    add(support_import_edit(f))
    add(Edit(
        "lat_vad_end", f,
        "                if len(pcm_bytes) > 1920 * 15:\n"
        "                    await self.handle_voice_stop(conn, [pcm_bytes])\n",
        "                if len(pcm_bytes) > 1920 * 15:\n"
        '                    _jarvis.lat(conn, "vad_end", src="vad", mode=conn.client_listen_mode,'
        ' speech_end_ms=_jarvis.as_int(getattr(conn, "vad_last_voice_time", None)),'
        " audio_ms=len(pcm_bytes) // 32)  # [jarvis-patch:lat_vad_end]\n"
        "                    await self.handle_voice_stop(conn, [pcm_bytes])\n",
        "IT1: [LAT] stage=vad_end src=vad (server VAD end of speech, auto mode)",
    ))
    add(Edit(
        "lat_asr_done", f,
        '            logger.bind(tag=TAG).debug(f"总处理耗时: {total_time:.3f}s")\n',
        '            logger.bind(tag=TAG).debug(f"总处理耗时: {total_time:.3f}s")\n'
        '            _jarvis.lat(conn, "asr_done", text_len=_jarvis.safe_len(content_for_length_check),'
        " dur_ms=_jarvis.as_int(total_time * 1000))  # [jarvis-patch:lat_asr_done]\n",
        "IT1: [LAT] stage=asr_done text_len= dur_ms=",
    ))

    # ---------------------------------------------------------------- core/providers/tts/base.py
    f = "core/providers/tts/base.py"
    add(support_import_edit(f))
    add(Edit(
        "lat_tts_first_audio", f,
        '        logger.bind(tag=TAG).debug(f"推送数据到队列里面帧数～～ {len(opus_data)}")\n',
        '        logger.bind(tag=TAG).debug(f"推送数据到队列里面帧数～～ {len(opus_data)}")\n'
        "        _jarvis.tts_first_audio(self)  # [jarvis-patch:lat_tts_first_audio]\n",
        "IT1: [LAT] stage=tts_first_audio (first Opus frame from TTS PCM in the turn; via handle_opus)",
    ))

    # ---------------------------------------------------------------- device_mcp/mcp_handler.py
    f = "core/providers/tools/device_mcp/mcp_handler.py"
    add(Edit(
        "mcp_error_prefix_const", f,
        "\nTAG = __name__\n",
        "\nTAG = __name__\n"
        "# [jarvis-patch:mcp_error_prefix_const] prefix of device JSON-RPC error messages (upstream: Chinese)\n"
        "JARVIS_MCP_ERROR_PREFIX = %s\n" % lit(S.get_str("mcp_error_prefix", allow_empty=True)),
        "D17: constant replacing the Chinese device-error prefix (empty = raw device message)",
    ))
    add(Edit(
        "mcp_ready_hook", f,
        '                    if hasattr(conn, "func_handler") and conn.func_handler:\n'
        "                        conn.func_handler.tool_manager.refresh_tools()\n"
        "                        conn.func_handler.current_support_functions()\n"
        "            return\n",
        '                    if hasattr(conn, "func_handler") and conn.func_handler:\n'
        "                        conn.func_handler.tool_manager.refresh_tools()\n"
        "                        conn.func_handler.current_support_functions()\n"
        "\n"
        "                    # [jarvis-patch:mcp_ready_hook] helmet_tasks.on_mcp_ready (sync; schedules its own tasks)\n"
        "                    try:\n"
        "                        from plugins_func.functions.helmet_tasks import on_mcp_ready as _jarvis_on_mcp_ready\n"
        "\n"
        "                        _jarvis_on_mcp_ready(conn)\n"
        "                    except Exception as _jarvis_error:\n"
        '                        logger.bind(tag=TAG).error(f"[jarvis] on_mcp_ready hook failed: {_jarvis_error}")\n'
        "            return\n",
        "4.7: call helmet_tasks.on_mcp_ready(conn) after set_ready(True) + tool refresh (guarded)",
    ))
    add(Edit(
        "mcp_unknown_error", f,
        '        error_msg = error_data.get("message", "未知错误")\n',
        '        error_msg = error_data.get("message", %s)  # [jarvis-patch:mcp_unknown_error]\n'
        % lit(S.text("mcp_unknown_error")),
        "D17: default device error text",
    ))
    add(Edit(
        "mcp_error_prefix", f,
        '                msg_id, Exception(f"MCP错误: {error_msg}")\n',
        "                msg_id, Exception(JARVIS_MCP_ERROR_PREFIX + str(error_msg))  # [jarvis-patch:mcp_error_prefix]\n",
        "D17: device JSON-RPC errors use JARVIS_MCP_ERROR_PREFIX instead of the Chinese prefix",
    ))
    add(Edit(
        "mcp_client_not_ready", f,
        '        raise RuntimeError("MCP客户端尚未准备就绪")\n',
        "        raise RuntimeError(%s)  # [jarvis-patch:mcp_client_not_ready]\n" % lit(S.text("mcp_client_not_ready")),
        "D17: call_mcp_tool before MCP ready",
    ))
    add(Edit(
        "mcp_tool_not_found", f,
        '        raise ValueError(f"工具 {tool_name} 不存在")\n',
        "        raise ValueError(%s)  # [jarvis-patch:mcp_tool_not_found]\n"
        % template_expr(S, "mcp_tool_not_found", tool_name="tool_name"),
        "D17: unknown device tool (NOTFOUND)",
    ))
    add(Edit(
        "mcp_args_unparsable", f,
        '                                raise ValueError(f"无法解析任何有效的JSON对象: {args}")\n',
        "                                raise ValueError(%s)  # [jarvis-patch:mcp_args_unparsable]\n"
        % template_expr(S, "mcp_args_unparsable", args="args"),
        "D17: tool arguments: no JSON object",
    ))
    add(Edit(
        "mcp_args_json_error", f,
        '                            raise ValueError(f"参数JSON解析失败: {args}")\n',
        "                            raise ValueError(%s)  # [jarvis-patch:mcp_args_json_error]\n"
        % template_expr(S, "mcp_args_json_error", args="args"),
        "D17: tool arguments: invalid JSON",
    ))
    add(Edit(
        "mcp_args_parse_failed", f,
        '                        raise ValueError(f"参数JSON解析失败: {str(e)}")\n',
        "                        raise ValueError(%s)  # [jarvis-patch:mcp_args_parse_failed]\n"
        % template_expr(S, "mcp_args_parse_failed", error="e"),
        "D17: tool arguments: parse failure",
    ))
    add(Edit(
        "mcp_args_type_error", f,
        '            raise ValueError(f"参数类型错误，期望字符串或字典，实际类型: {type(args)}")\n',
        "            raise ValueError(%s)  # [jarvis-patch:mcp_args_type_error]\n"
        % template_expr(S, "mcp_args_type_error", type="type(args)"),
        "D17: tool arguments: wrong type",
    ))
    add(Edit(
        "mcp_args_not_dict", f,
        '            raise ValueError(f"参数必须是字典类型，实际类型: {type(arguments)}")\n',
        "            raise ValueError(%s)  # [jarvis-patch:mcp_args_not_dict]\n"
        % template_expr(S, "mcp_args_not_dict", type="type(arguments)"),
        "D17: tool arguments: not an object",
    ))
    add(Edit(
        "mcp_args_failed", f,
        '            raise ValueError(f"参数处理失败: {str(e)}")\n',
        "            raise ValueError(%s)  # [jarvis-patch:mcp_args_failed]\n"
        % template_expr(S, "mcp_args_failed", error="e"),
        "D17: tool arguments: processing failure",
    ))
    add(Edit(
        "mcp_tool_error_no_detail", f,
        '                    "error", "工具调用返回错误，但未提供具体错误信息"\n',
        '                    "error", %s  # [jarvis-patch:mcp_tool_error_no_detail]\n'
        % lit(S.text("mcp_tool_error_no_detail")),
        "D17: device result isError without message",
    ))
    add(Edit(
        "mcp_tool_error", f,
        '                raise RuntimeError(f"工具调用错误: {error_msg}")\n',
        "                raise RuntimeError(%s)  # [jarvis-patch:mcp_tool_error]\n"
        % template_expr(S, "mcp_tool_error", error_msg="error_msg"),
        "D17: device result isError",
    ))
    add(Edit(
        "mcp_timeout", f,
        '        raise TimeoutError("工具调用请求超时")\n',
        "        raise TimeoutError(%s)  # [jarvis-patch:mcp_timeout]\n" % lit(S.text("mcp_timeout")),
        "D17: device tool call timeout",
    ))

    # ---------------------------------------------------------------- device_mcp/mcp_executor.py
    f = "core/providers/tools/device_mcp/mcp_executor.py"
    add(support_import_edit(f, anchor="from .mcp_handler import call_mcp_tool\n"))
    add(Edit(
        "lat_mcp_call", f,
        "            result = await call_mcp_tool(conn, conn.mcp_client, tool_name, args_str)\n",
        "            _jarvis_t0 = _jarvis.now_ms()  # [jarvis-patch:lat_mcp_call]\n"
        "            _jarvis_ok = False\n"
        "            try:\n"
        "                result = await call_mcp_tool(conn, conn.mcp_client, tool_name, args_str)\n"
        "                _jarvis_ok = True\n"
        "            finally:\n"
        '                _jarvis.mcp_call_done(conn, tool_name, _jarvis_t0, _jarvis_ok, src="llm")\n',
        "IT1: [LAT] stage=mcp_call src=llm tool= ms= (direct LLM device-tool call; t_ms = request sent)",
    ))
    add(Edit(
        "mcp_exec_not_initialized", f,
        '                response="设备端MCP客户端未初始化",\n',
        "                response=%s,  # [jarvis-patch:mcp_exec_not_initialized]\n" % lit(S.text("mcp_not_initialized")),
        "D17: spoken when a device tool is called without MCP support",
    ))
    add(Edit(
        "mcp_exec_not_ready", f,
        '                response="设备端MCP客户端未准备就绪",\n',
        "                response=%s,  # [jarvis-patch:mcp_exec_not_ready]\n" % lit(S.text("mcp_not_ready")),
        "D17: spoken when a device tool is called before tools/list finished",
    ))
    add(Edit(
        "mcp_exec_errors", f,
        "        except ValueError as e:\n"
        "            return ActionResponse(action=Action.NOTFOUND, response=str(e))\n"
        "        except Exception as e:\n"
        "            return ActionResponse(action=Action.ERROR, response=str(e))\n",
        "        except ValueError as e:  # [jarvis-patch:mcp_exec_errors]\n"
        "            return ActionResponse(action=Action.NOTFOUND, response=%s)\n"
        "        except Exception as e:\n"
        "            return ActionResponse(action=Action.ERROR, response=%s)\n"
        % (template_expr(S, "device_tool_failed", reason="e"), template_expr(S, "device_tool_failed", reason="e")),
        "D17: spoken device-tool failures wrapped in a polite Vietnamese sentence",
    ))

    # ---------------------------------------------------------------- unified_tool_manager.py
    f = "core/providers/tools/unified_tool_manager.py"
    add(Edit(
        "tool_not_found", f,
        '                    response=f"工具 {tool_name} 不存在",\n',
        "                    response=%s,  # [jarvis-patch:tool_not_found]\n"
        % template_expr(S, "tool_not_found", tool_name="tool_name"),
        "D17: spoken NOTFOUND when the LLM calls an unknown tool",
    ))
    add(Edit(
        "tool_executor_missing", f,
        '                    response=f"工具类型 {tool_type.value} 的执行器未注册",\n',
        "                    response=%s,  # [jarvis-patch:tool_executor_missing]\n"
        % template_expr(S, "tool_executor_missing", tool_name="tool_name", tool_type="tool_type.value"),
        "D17: spoken ERROR when no executor exists for a tool type",
    ))

    # ---------------------------------------------------------------- unified_tool_handler.py
    f = "core/providers/tools/unified_tool_handler.py"
    add(Edit(
        "tool_args_invalid", f,
        '                        response="无法解析函数参数",\n',
        "                        response=%s,  # [jarvis-patch:tool_args_invalid]\n" % lit(S.text("tool_args_invalid")),
        "D17: spoken ERROR for unparsable tool arguments",
    ))
    add(Edit(
        "tool_display", f,
        '                await send_display_message(self.conn, f"% {function_name}")\n',
        '                if (self.conn.config.get("jarvis") or {}).get("tool_display", False):'
        "  # [jarvis-patch:tool_display]\n"
        '                    await send_display_message(self.conn, f"% {function_name}")\n',
        "D17: '% <tool>' stt display line only when config jarvis.tool_display is true (default off)",
    ))

    # ---------------------------------------------------------------- server_plugins/plugin_executor.py
    f = "core/providers/tools/server_plugins/plugin_executor.py"
    add(Edit(
        "plugin_not_found", f,
        '                action=Action.NOTFOUND, response=f"插件函数 {tool_name} 不存在"\n',
        "                action=Action.NOTFOUND, response=%s  # [jarvis-patch:plugin_not_found]\n"
        % template_expr(S, "plugin_not_found", tool_name="tool_name"),
        "D17: spoken NOTFOUND for a plugin function missing from the registry",
    ))

    # ---------------------------------------------------------------- core/utils/util.py
    f = "core/utils/util.py"
    add(Edit(
        "system_error_default", f,
        '    return config.get("system_error_response", "主人，小智现在有点忙，我们稍后再试吧。")',
        '    return config.get("system_error_response", %s)  # [jarvis-patch:system_error_default]'
        % lit(S.text("system_error_default")),
        "D17: default system_error_response (config normally overrides it)",
    ))

    # ---------------------------------------------------------------- core/utils/tts.py
    f = "core/utils/tts.py"
    add(Edit(
        "md_table_single", f,
        '            lines_for_tts.append(f"单行表格：{only_line_str}")\n',
        "            lines_for_tts.append(%s + only_line_str)  # [jarvis-patch:md_table_single]\n"
        % lit(S.text("md_table_single")),
        "D17: spoken Markdown-table rewrite (single row)",
    ))
    add(Edit(
        "md_table_header", f,
        "            lines_for_tts.append(f\"表头是：{', '.join(headers)}\")\n",
        '            lines_for_tts.append(%s + ", ".join(headers))  # [jarvis-patch:md_table_header]\n'
        % lit(S.text("md_table_header")),
        "D17: spoken Markdown-table rewrite (header)",
    ))
    add(Edit(
        "md_table_row", f,
        "                lines_for_tts.append(f\"第 {i} 行：{', '.join(row_str_list)}\")\n",
        '                lines_for_tts.append(%s + ", ".join(row_str_list))  # [jarvis-patch:md_table_row]\n'
        % template_expr(S, "md_table_row", i="i"),
        "D17: spoken Markdown-table rewrite (rows)",
    ))

    # ---------------------------------------------------------------- core/utils/dialogue.py
    f = "core/utils/dialogue.py"
    add(Edit(
        "tool_interrupted", f,
        "                content='{\"status\": \"interrupted\", \"message\": \"动作已取消/被打断\"}',\n",
        "                content=%s,  # [jarvis-patch:tool_interrupted]\n"
        % lit(json.dumps({"status": "interrupted", "message": S.text("tool_interrupted")}, ensure_ascii=False)),
        "D17: filler tool result for dangling tool calls (LLM)",
    ))

    # ---------------------------------------------------------------- core/api/ota_handler.py
    f = "core/api/ota_handler.py"
    add(support_import_edit(f))
    add(Edit(
        "redact_ota_headers", f,
        '            self.logger.bind(tag=TAG).debug(f"OTA请求头: {request.headers}")\n',
        '            self.logger.bind(tag=TAG).debug(f"OTA请求头: {_jarvis.redact_headers(request.headers)}")'
        "  # [jarvis-patch:redact_ota_headers]\n",
        "D14: mask credentials in the OTA header log line (DEBUG level)",
    ))

    # ---------------------------------------------------------------- handle_exit_intent.py
    f = "plugins_func/functions/handle_exit_intent.py"
    add(Edit(
        "exit_description", f,
        '        "description": "当用户想结束对话或需要退出系统时调用",\n',
        '        "description": %s,  # [jarvis-patch:exit_description]\n' % lit(S.text("handle_exit_intent.description")),
        "D17: handle_exit_intent description (config plugins.handle_exit_intent.description overrides it)",
    ))
    add(Edit(
        "exit_param_description", f,
        '                    "description": "和用户友好结束对话的告别语",\n',
        '                    "description": %s,  # [jarvis-patch:exit_param_description]\n'
        % lit(S.text("handle_exit_intent.say_goodbye_description")),
        "D17: handle_exit_intent.say_goodbye parameter description (LLM)",
    ))
    add(Edit(
        "exit_default_goodbye", f,
        "        if say_goodbye is None:\n"
        '            say_goodbye = "再见，祝您生活愉快！"\n',
        "        if not say_goodbye:  # [jarvis-patch:exit_default_goodbye] empty -> default too\n"
        "            say_goodbye = %s\n" % lit(S.text("handle_exit_intent.default_goodbye")),
        "D17: spoken default goodbye (also used for an empty say_goodbye)",
    ))
    add(Edit(
        "exit_result", f,
        '            action=Action.RESPONSE, result="退出意图已处理", response=say_goodbye\n',
        "            action=Action.RESPONSE, result=%s, response=say_goodbye  # [jarvis-patch:exit_result]\n"
        % lit(S.text("handle_exit_intent.result")),
        "D17: handle_exit_intent result text",
    ))

    # ---------------------------------------------------------------- get_time.py (get_lunar)
    f = "plugins_func/functions/get_time.py"
    add(Edit(
        "lunar_description", f,
        '        "description": (\n'
        '            "用于具体日期的阴历/农历和黄历信息。"\n'
        '            "用户可以指定查询内容，如：阴历日期、天干地支、节气、生肖、星座、八字、宜忌等。"\n'
        '            "如果没有指定查询内容，则默认查询干支年和农历日期。"\n'
        "            \"对于'今天农历是多少'、'今天农历日期'这样的基本查询，请直接使用context中的信息，不要调用此工具。\"\n"
        "        ),\n",
        '        "description": %s,  # [jarvis-patch:lunar_description]\n' % lit(S.text("get_lunar.description")),
        "D17: get_lunar description (config plugins.get_lunar.description overrides it)",
    ))
    add(Edit(
        "lunar_date_description", f,
        '                    "description": "要查询的日期，格式为YYYY-MM-DD，例如2024-01-01。如果不提供，则使用当前日期",\n',
        '                    "description": %s,  # [jarvis-patch:lunar_date_description]\n'
        % lit(S.text("get_lunar.date_description")),
        "D17: get_lunar.date parameter description (LLM)",
    ))
    add(Edit(
        "lunar_query_description", f,
        '                    "description": "要查询的内容，例如阴历日期、天干地支、节日、节气、生肖、星座、八字、宜忌等",\n',
        '                    "description": %s,  # [jarvis-patch:lunar_query_description]\n'
        % lit(S.text("get_lunar.query_description")),
        "D17: get_lunar.query parameter description (LLM)",
    ))
    add(Edit(
        "lunar_date_error", f,
        '                f"日期格式错误，请使用YYYY-MM-DD格式，例如：2024-01-01",\n',
        "                %s,  # [jarvis-patch:lunar_date_error]\n" % lit(S.text("get_lunar.date_format_error")),
        "D17: get_lunar date format error (LLM)",
    ))
    add(Edit(
        "lunar_default_query", f,
        '        query = "默认查询干支年和农历日期"\n',
        "        query = %s  # [jarvis-patch:lunar_default_query]\n" % lit(S.text("get_lunar.default_query")),
        "D17: get_lunar default query (LLM)",
    ))
    add(Edit(
        "lunar_response_header", f,
        '    response_text = f"根据以下信息回应用户的查询请求，并提供与{query}相关的信息：\\n"\n',
        "    response_text = %s  # [jarvis-patch:lunar_response_header]\n"
        % template_expr(S, "get_lunar.response_header", query="query"),
        "D17: get_lunar result header telling the LLM to translate (data stays Chinese)",
    ))

    seen = set()
    for e in E:
        key = (e.file, e.id)
        if key in seen:
            raise PatchError("duplicate edit id %s in %s" % (e.id, e.file))
        seen.add(key)
    return E


# --------------------------------------------------------------------------- engine


def default_root():
    env = os.environ.get("XIAOZHI_ROOT")
    if env:
        return env
    if os.path.isfile("/opt/xiaozhi-esp32-server/app.py"):
        return "/opt/xiaozhi-esp32-server"
    return os.getcwd()


def load_strings(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise PatchError("cannot read strings file %s: %s" % (path, e))
    if not isinstance(data, dict):
        raise PatchError("%s: top level must be an object" % path)
    return Strings(data, os.path.basename(path))


def read_text(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("utf-8")
    crlf = "\r\n" in text
    if crlf:
        text = text.replace("\r\n", "\n")
    return text, crlf


def plan(root, edits):
    """Compute new file contents. Returns (results, new_texts, errors)."""
    by_file = {}
    for e in edits:
        by_file.setdefault(e.file, []).append(e)
    results, new_texts, errors = [], {}, []
    for rel, file_edits in by_file.items():
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            for e in file_edits:
                results.append((e, "FAIL", "file not found"))
            errors.append("%s: file not found" % rel)
            continue
        text, crlf = read_text(path)
        original = text
        for e in file_edits:
            if e.marker in text:
                results.append((e, "already", ""))
                continue
            found = text.count(e.anchor)
            if found != e.count:
                msg = "anchor found %d time(s), expected %d" % (found, e.count)
                results.append((e, "FAIL", msg))
                errors.append("%s [%s]: %s" % (rel, e.id, msg))
                continue
            text = text.replace(e.anchor, e.replacement)
            results.append((e, "applied", ""))
        if text != original:
            try:
                compile(text, path, "exec")
            except SyntaxError as se:
                errors.append("%s: patched file does not compile: %s (line %s)" % (rel, se.msg, se.lineno))
            new_texts[rel] = (text, crlf)
    return results, new_texts, errors


def support_plan(root):
    out = []
    for rel, src in SUPPORT_FILES.items():
        with open(os.path.join(HERE, src), "rb") as fh:
            data = fh.read()
        dest = os.path.join(root, rel)
        same = False
        if os.path.isfile(dest):
            with open(dest, "rb") as fh:
                same = hashlib.sha256(fh.read()).digest() == hashlib.sha256(data).digest()
        out.append((rel, data, same))
    return out


def write_atomic(path, data):
    tmp = path + ".jarvis-tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def anchor_summary(edit, width=70):
    """First non-blank line of the anchor (for the README table)."""
    for line in edit.anchor.split("\n"):
        if line.strip():
            text = line.strip()
            return text if len(text) <= width else text[: width - 3] + "..."
    return ""


def print_table(edits, markdown=False):
    if markdown:
        print("| # | id | file | anchor (first line) | purpose |")
        print("|---|---|---|---|---|")
        for i, e in enumerate(edits, 1):
            anchor = anchor_summary(e).replace("|", "\\|").replace("`", "'")
            print("| %d | `%s` | `%s` | `%s` | %s |" % (i, e.id, e.file, anchor, e.purpose.replace("|", "\\|")))
    else:
        for i, e in enumerate(edits, 1):
            print("%3d  %-26s %-58s %s" % (i, e.id, e.file, e.purpose))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="xiaozhi-server root directory")
    ap.add_argument("--check", action="store_true", help="verify only, write nothing")
    ap.add_argument("--strings", default=os.path.join(HERE, "strings_vi.json"), help="localized strings JSON")
    ap.add_argument("--list", action="store_true", help="print the patch table and exit")
    ap.add_argument("--markdown", action="store_true", help="with --list: Markdown table")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    try:
        edits = build_edits(load_strings(args.strings))
    except PatchError as e:
        print("ERROR: %s" % e)
        return 1
    if args.list:
        print_table(edits, args.markdown)
        return 0

    root = os.path.abspath(args.root or default_root())
    if not os.path.isfile(os.path.join(root, "app.py")):
        print("ERROR: %s does not look like an xiaozhi-server root (no app.py)" % root)
        return 1
    logger_py = os.path.join(root, "config", "logger.py")
    if os.path.isfile(logger_py):
        with open(logger_py, "r", encoding="utf-8") as fh:
            if EXPECTED_SERVER_VERSION not in fh.read():
                print("WARNING: SERVER_VERSION is not %s; anchors decide whether the patches still fit"
                      % EXPECTED_SERVER_VERSION)

    results, new_texts, errors = plan(root, edits)
    supports = support_plan(root)

    if args.check:
        results = [(e, "pending" if status == "applied" else status, msg) for e, status, msg in results]
    for e, status, msg in results:
        print("%-8s %-26s %s%s" % (status, e.id, e.file, ("  -- " + msg) if msg else ""))
    for rel, _data, same in supports:
        print("%-8s %-26s %s" % ("same" if same else ("pending" if args.check else "installed"), "support_file", rel))

    counts = {}
    for _e, status, _msg in results:
        counts[status] = counts.get(status, 0) + 1
    summary = ", ".join("%s=%d" % kv for kv in sorted(counts.items()))
    if errors:
        print("FAILED (%s); nothing was written:" % summary)
        for err in errors:
            print("  - " + err)
        return 1
    if args.check:
        print("CHECK OK (%s)" % summary)
        return 0

    for rel, (text, crlf) in sorted(new_texts.items()):
        data = (text.replace("\n", "\r\n") if crlf else text).encode("utf-8")
        write_atomic(os.path.join(root, rel), data)
    for rel, data, same in supports:
        if not same:
            dest = os.path.join(root, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            write_atomic(dest, data)
    print("OK (%s; %d file(s) written)" % (summary, len(new_texts) + sum(1 for s in supports if not s[2])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
