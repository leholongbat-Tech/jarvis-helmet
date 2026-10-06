# Bản vá xiaozhi-esp32-server v0.9.7 cho Jarvis Helmet (SV-C)

Thư mục này chứa **mọi thay đổi** của dự án đối với mã nguồn xiaozhi-server gốc. Không fork file
upstream nào: image `jarvis/xiaozhi:dev` lấy image chính thức `server_0.9.7` (ghim digest) rồi chạy
`apply_patches.py` lúc build.

| File | Vai trò |
|---|---|
| `apply_patches.py` | Áp các bản vá dạng "neo" (anchor): tìm đúng đoạn văn bản gốc, khẳng định số lần xuất hiện, thay thế, gắn dấu `# [jarvis-patch:<id>]` |
| `strings_vi.json` | Toàn bộ chuỗi Việt hóa được chèn vào mã (mô tả công cụ, few-shot, câu báo lỗi, câu đáp từ đánh thức...). Công cụ khác (vd. `tools/tests/llm`) đọc file này để gửi cho LLM đúng nội dung server gửi |
| `jarvis_support.py` | Module hỗ trợ, được cài vào `core/utils/jarvis_support.py`: dòng log `[LAT]`, che header nhạy cảm. Mọi hàm tự nuốt lỗi |
| `scan_cjk.py` | Quét chuỗi tiếng Trung còn sót trong các file đang dùng (báo cáo, không chặn build) |

## 1. Nguyên tắc

- Mỗi bản vá = (file, anchor, đoạn thay thế, số lần anchor phải xuất hiện, mặc định 1).
- Đã có dấu `# [jarvis-patch:<id>]` trong file → bỏ qua (`already`): chạy lại bao nhiêu lần cũng được.
- Anchor không thấy hoặc thấy sai số lần → **FAIL**, không ghi file nào (tất cả hoặc không gì cả),
  mã thoát 1 → `docker build` dừng. Nhờ vậy nâng cấp upstream không bao giờ "vá trượt" âm thầm.
- Mọi file sau khi vá phải biên dịch được (dùng trình biên dịch Python đang chạy: 3.10 trong image).
- File CRLF được giữ nguyên kiểu xuống dòng.
- Hai hook gọi plugin (`helmet_tasks`) luôn nằm trong `try/except`: plugin lỗi không làm hỏng luồng hội thoại.

## 2. Cách chạy

```bash
# trong Docker build (Dockerfile làm sẵn):
python /opt/jarvis/patches/apply_patches.py --root /opt/xiaozhi-esp32-server

# kiểm tra một cây mã (không ghi gì): pending = sẽ áp, already = đã áp
python server/xiaozhi/patches/apply_patches.py --check --root <thư mục xiaozhi-server>

# in bảng bản vá (Markdown dùng cho mục 3 của file này)
python server/xiaozhi/patches/apply_patches.py --list --markdown

# báo cáo chuỗi tiếng Trung còn lại (OTHER/EXC cần xem lại; --all để xem cả LOG/DOC)
python server/xiaozhi/patches/scan_cjk.py --root <thư mục xiaozhi-server>
docker run --rm --entrypoint cat jarvis/xiaozhi:dev /opt/jarvis/patches/cjk_report.txt

# test (bản sao tạm của mã upstream; đặt XIAOZHI_SRC nếu clone ở chỗ khác)
.venv/Scripts/python -m pytest server/tests/test_patches.py server/tests/test_entrypoint.py -q
```

## 3. Danh sách bản vá

Cột "anchor" chỉ là dòng đầu của đoạn neo; đoạn đầy đủ nằm trong `build_edits()` của `apply_patches.py`.
Bảng sinh bằng `apply_patches.py --list --markdown` (test `test_readme_lists_every_patch` bắt lỗi nếu thiếu id).

| # | id | file | anchor (first line) | purpose |
|---|---|---|---|---|
| 1 | `support_import` | `core/connection.py` | `TAG = __name__` | import core/utils/jarvis_support.py (no-op fallback) |
| 2 | `redact_headers` | `core/connection.py` | `self.logger.bind(tag=TAG).info(` | D14: mask Authorization / Cookie / CF-Access-* in the connection header log line |
| 3 | `direct_answer_desc` | `core/connection.py` | `"description": "当用户的请求不匹配其他任何工具时，可用此选项直接回复。将回复内容写在response参数里。",` | D17: direct_answer tool description (LLM) -> VI/EN |
| 4 | `direct_answer_param` | `core/connection.py` | `"description": "你回复用户的完整内容",` | D17: direct_answer.response parameter description (LLM) -> VI/EN |
| 5 | `fewshot_direct_user` | `core/connection.py` | `self.dialogue.put(Message(role="user", content="给我讲个故事吧", is_tempor...` | D17: few-shot #1 user message (LLM) |
| 6 | `fewshot_direct_args` | `core/connection.py` | `"function": {"arguments": '{"response": "好呀，你想听什么类型的呀？童话、冒险还是搞笑的？选一...` | D17: few-shot #1 direct_answer arguments (LLM) |
| 7 | `fewshot_direct_result` | `core/connection.py` | `content="已直接回复", is_temporary=True,` | D17: few-shot #1 tool result (LLM) |
| 8 | `fewshot_exit_user` | `core/connection.py` | `self.dialogue.put(Message(role="user", content="拜拜", is_temporary=T...` | D17: few-shot #2 user message (LLM) |
| 9 | `fewshot_exit_args` | `core/connection.py` | `"function": {"arguments": '{"say_goodbye": "再见，下次再聊~"}', "name": "h...` | D17: few-shot #2 handle_exit_intent arguments (LLM) |
| 10 | `fewshot_exit_result` | `core/connection.py` | `content="退出意图已处理", is_temporary=True,` | D17: few-shot #2 tool result (LLM) |
| 11 | `fewshot_exit_reply` | `core/connection.py` | `role="assistant", content="再见，下次再聊~", is_temporary=True,` | D17: few-shot #2 assistant reply (LLM) |
| 12 | `max_depth_hint` | `core/connection.py` | `content="[系统提示] 已达到最大工具调用次数限制，请你基于目前已经获取的所有信息，直接给出最终答案。不要再尝试调用任何工具。",` | D17: hint injected after 5 recursive tool rounds (LLM) |
| 13 | `lat_llm_first_token` | `core/connection.py` | `emotion_flag = True` | IT1: [LAT] stage=llm_first_token (first content or tool-call chunk) |
| 14 | `lat_llm_done` | `core/connection.py` | `except Exception as e:` | IT1: [LAT] stage=llm_done (LLM stream finished) |
| 15 | `tool_call_failed` | `core/connection.py` | `ActionResponse(action=Action.ERROR, result="哎呀，网络遇到点问题，请稍后再试下！"),` | D17: spoken text when a tool call times out / raises |
| 16 | `wakeup_responses` | `core/handle/helloHandle.py` | `"responses": [` | D15: wake-word reply list (TTS-generated cache, spoken + displayed) |
| 17 | `hello_sample_rate` | `core/handle/helloHandle.py` | `conn.welcome_msg["audio_params"] = audio_params` | D3: server hello audio_params.sample_rate = server output rate (24000) |
| 18 | `wakeup_fallback_text` | `core/handle/helloHandle.py` | `"text": "我在这里哦！",` | D15: text shown with the fallback wake sound (no cache yet) |
| 19 | `quick_phrase_hook` | `core/handle/intentHandler.py` | `# 检查是否是唤醒词` | 4.7: await helmet_tasks.try_quick_phrase(conn, text) before the function_call return (guarded) |
| 20 | `support_import` | `core/handle/textHandler/listenMessageHandler.py` | `TAG = __name__` | import core/utils/jarvis_support.py (no-op fallback) |
| 21 | `lat_vad_end_manual` | `core/handle/textHandler/listenMessageHandler.py` | `elif msg_json["state"] == "stop":` | IT1: [LAT] stage=vad_end src=listen_stop (PTT release / device end of speech) |
| 22 | `greeting_text` | `core/handle/textHandler/listenMessageHandler.py` | `enqueue_asr_report(conn, "嘿，你好呀", [])` | D17: greeting user text when enable_greeting=true (displayed + sent to the LLM) |
| 23 | `wake_no_echo` | `core/handle/textHandler/listenMessageHandler.py` | `if is_wakeup_words and not enable_greeting:` | D15: wake word with enable_greeting=false sends nothing (keeps the device Listening, no dropped audio) |
| 24 | `asr_openai_import` | `core/providers/asr/openai.py` | `import requests` | event loop: import asyncio for the threaded ASR request |
| 25 | `asr_openai_timeout` | `core/providers/asr/openai.py` | `self.output_dir = config.get("output_dir")` | event loop: bounded ASR request time (ASR.<name>.timeout_s, default 30 s) |
| 26 | `asr_openai_to_thread` | `core/providers/asr/openai.py` | `response = requests.post(` | event loop: OpenaiASR requests.post runs in asyncio.to_thread with a timeout |
| 27 | `ws_auth_failed_text` | `core/websocket_server.py` | `await websocket.send("认证失败")` | D17: text frame sent to a client whose WebSocket token is rejected |
| 28 | `support_import` | `core/handle/sendAudioHandle.py` | `TAG = __name__` | import core/utils/jarvis_support.py (no-op fallback) |
| 29 | `lat_first_packet` | `core/handle/sendAudioHandle.py` | `# 更新流控状态` | IT1: [LAT] stage=first_packet_sent (first Opus frame of the turn written to the socket) |
| 30 | `end_prompt_fallback` | `core/handle/receiveAudioHandle.py` | `prompt = "请你以'''时间过得真快'''未来头，用富有感情、依依不舍的话来结束这场对话吧。！"` | D17: idle-close prompt used when end_prompt.prompt is empty (LLM) |
| 31 | `support_import` | `core/providers/asr/base.py` | `TAG = __name__` | import core/utils/jarvis_support.py (no-op fallback) |
| 32 | `lat_vad_end` | `core/providers/asr/base.py` | `if len(pcm_bytes) > 1920 * 15:` | IT1: [LAT] stage=vad_end src=vad (server VAD end of speech, auto mode) |
| 33 | `lat_asr_done` | `core/providers/asr/base.py` | `logger.bind(tag=TAG).debug(f"总处理耗时: {total_time:.3f}s")` | IT1: [LAT] stage=asr_done text_len= dur_ms= |
| 34 | `support_import` | `core/providers/tts/base.py` | `TAG = __name__` | import core/utils/jarvis_support.py (no-op fallback) |
| 35 | `lat_tts_first_audio` | `core/providers/tts/base.py` | `logger.bind(tag=TAG).debug(f"推送数据到队列里面帧数～～ {len(opus_data)}")` | IT1: [LAT] stage=tts_first_audio (first Opus frame from TTS PCM in the turn; via handle_opus) |
| 36 | `mcp_error_prefix_const` | `core/providers/tools/device_mcp/mcp_handler.py` | `TAG = __name__` | D17: constant replacing the Chinese device-error prefix (empty = raw device message) |
| 37 | `mcp_ready_hook` | `core/providers/tools/device_mcp/mcp_handler.py` | `if hasattr(conn, "func_handler") and conn.func_handler:` | 4.7: call helmet_tasks.on_mcp_ready(conn) after set_ready(True) + tool refresh (guarded) |
| 38 | `mcp_unknown_error` | `core/providers/tools/device_mcp/mcp_handler.py` | `error_msg = error_data.get("message", "未知错误")` | D17: default device error text |
| 39 | `mcp_error_prefix` | `core/providers/tools/device_mcp/mcp_handler.py` | `msg_id, Exception(f"MCP错误: {error_msg}")` | D17: device JSON-RPC errors use JARVIS_MCP_ERROR_PREFIX instead of the Chinese prefix |
| 40 | `mcp_client_not_ready` | `core/providers/tools/device_mcp/mcp_handler.py` | `raise RuntimeError("MCP客户端尚未准备就绪")` | D17: call_mcp_tool before MCP ready |
| 41 | `mcp_tool_not_found` | `core/providers/tools/device_mcp/mcp_handler.py` | `raise ValueError(f"工具 {tool_name} 不存在")` | D17: unknown device tool (NOTFOUND) |
| 42 | `mcp_args_unparsable` | `core/providers/tools/device_mcp/mcp_handler.py` | `raise ValueError(f"无法解析任何有效的JSON对象: {args}")` | D17: tool arguments: no JSON object |
| 43 | `mcp_args_json_error` | `core/providers/tools/device_mcp/mcp_handler.py` | `raise ValueError(f"参数JSON解析失败: {args}")` | D17: tool arguments: invalid JSON |
| 44 | `mcp_args_parse_failed` | `core/providers/tools/device_mcp/mcp_handler.py` | `raise ValueError(f"参数JSON解析失败: {str(e)}")` | D17: tool arguments: parse failure |
| 45 | `mcp_args_type_error` | `core/providers/tools/device_mcp/mcp_handler.py` | `raise ValueError(f"参数类型错误，期望字符串或字典，实际类型: {type(args)}")` | D17: tool arguments: wrong type |
| 46 | `mcp_args_not_dict` | `core/providers/tools/device_mcp/mcp_handler.py` | `raise ValueError(f"参数必须是字典类型，实际类型: {type(arguments)}")` | D17: tool arguments: not an object |
| 47 | `mcp_args_failed` | `core/providers/tools/device_mcp/mcp_handler.py` | `raise ValueError(f"参数处理失败: {str(e)}")` | D17: tool arguments: processing failure |
| 48 | `mcp_tool_error_no_detail` | `core/providers/tools/device_mcp/mcp_handler.py` | `"error", "工具调用返回错误，但未提供具体错误信息"` | D17: device result isError without message |
| 49 | `mcp_tool_error` | `core/providers/tools/device_mcp/mcp_handler.py` | `raise RuntimeError(f"工具调用错误: {error_msg}")` | D17: device result isError |
| 50 | `mcp_timeout` | `core/providers/tools/device_mcp/mcp_handler.py` | `raise TimeoutError("工具调用请求超时")` | D17: device tool call timeout |
| 51 | `support_import` | `core/providers/tools/device_mcp/mcp_executor.py` | `from .mcp_handler import call_mcp_tool` | import core/utils/jarvis_support.py (no-op fallback) |
| 52 | `lat_mcp_call` | `core/providers/tools/device_mcp/mcp_executor.py` | `result = await call_mcp_tool(conn, conn.mcp_client, tool_name, args...` | IT1: [LAT] stage=mcp_call src=llm tool= ms= (direct LLM device-tool call; t_ms = request sent) |
| 53 | `mcp_exec_not_initialized` | `core/providers/tools/device_mcp/mcp_executor.py` | `response="设备端MCP客户端未初始化",` | D17: spoken when a device tool is called without MCP support |
| 54 | `mcp_exec_not_ready` | `core/providers/tools/device_mcp/mcp_executor.py` | `response="设备端MCP客户端未准备就绪",` | D17: spoken when a device tool is called before tools/list finished |
| 55 | `mcp_exec_errors` | `core/providers/tools/device_mcp/mcp_executor.py` | `except ValueError as e:` | D17: spoken device-tool failures wrapped in a polite Vietnamese sentence |
| 56 | `tool_not_found` | `core/providers/tools/unified_tool_manager.py` | `response=f"工具 {tool_name} 不存在",` | D17: spoken NOTFOUND when the LLM calls an unknown tool |
| 57 | `tool_executor_missing` | `core/providers/tools/unified_tool_manager.py` | `response=f"工具类型 {tool_type.value} 的执行器未注册",` | D17: spoken ERROR when no executor exists for a tool type |
| 58 | `tool_args_invalid` | `core/providers/tools/unified_tool_handler.py` | `response="无法解析函数参数",` | D17: spoken ERROR for unparsable tool arguments |
| 59 | `tool_display` | `core/providers/tools/unified_tool_handler.py` | `await send_display_message(self.conn, f"% {function_name}")` | D17: '% <tool>' stt display line only when config jarvis.tool_display is true (default off) |
| 60 | `plugin_not_found` | `core/providers/tools/server_plugins/plugin_executor.py` | `action=Action.NOTFOUND, response=f"插件函数 {tool_name} 不存在"` | D17: spoken NOTFOUND for a plugin function missing from the registry |
| 61 | `system_error_default` | `core/utils/util.py` | `return config.get("system_error_response", "主人，小智现在有点忙，我们稍后再试吧。")` | D17: default system_error_response (config normally overrides it) |
| 62 | `md_table_single` | `core/utils/tts.py` | `lines_for_tts.append(f"单行表格：{only_line_str}")` | D17: spoken Markdown-table rewrite (single row) |
| 63 | `md_table_header` | `core/utils/tts.py` | `lines_for_tts.append(f"表头是：{', '.join(headers)}")` | D17: spoken Markdown-table rewrite (header) |
| 64 | `md_table_row` | `core/utils/tts.py` | `lines_for_tts.append(f"第 {i} 行：{', '.join(row_str_list)}")` | D17: spoken Markdown-table rewrite (rows) |
| 65 | `tool_interrupted` | `core/utils/dialogue.py` | `content='{"status": "interrupted", "message": "动作已取消/被打断"}',` | D17: filler tool result for dangling tool calls (LLM) |
| 66 | `support_import` | `core/api/ota_handler.py` | `TAG = __name__` | import core/utils/jarvis_support.py (no-op fallback) |
| 67 | `redact_ota_headers` | `core/api/ota_handler.py` | `self.logger.bind(tag=TAG).debug(f"OTA请求头: {request.headers}")` | D14: mask credentials in the OTA header log line (DEBUG level) |
| 68 | `exit_description` | `plugins_func/functions/handle_exit_intent.py` | `"description": "当用户想结束对话或需要退出系统时调用",` | D17: handle_exit_intent description (config plugins.handle_exit_intent.description overrides it) |
| 69 | `exit_param_description` | `plugins_func/functions/handle_exit_intent.py` | `"description": "和用户友好结束对话的告别语",` | D17: handle_exit_intent.say_goodbye parameter description (LLM) |
| 70 | `exit_default_goodbye` | `plugins_func/functions/handle_exit_intent.py` | `if say_goodbye is None:` | D17: spoken default goodbye (also used for an empty say_goodbye) |
| 71 | `exit_result` | `plugins_func/functions/handle_exit_intent.py` | `action=Action.RESPONSE, result="退出意图已处理", response=say_goodbye` | D17: handle_exit_intent result text |
| 72 | `lunar_description` | `plugins_func/functions/get_time.py` | `"description": (` | D17: get_lunar description (config plugins.get_lunar.description overrides it) |
| 73 | `lunar_date_description` | `plugins_func/functions/get_time.py` | `"description": "要查询的日期，格式为YYYY-MM-DD，例如2024-01-01。如果不提供，则使用当前日期",` | D17: get_lunar.date parameter description (LLM) |
| 74 | `lunar_query_description` | `plugins_func/functions/get_time.py` | `"description": "要查询的内容，例如阴历日期、天干地支、节日、节气、生肖、星座、八字、宜忌等",` | D17: get_lunar.query parameter description (LLM) |
| 75 | `lunar_date_error` | `plugins_func/functions/get_time.py` | `f"日期格式错误，请使用YYYY-MM-DD格式，例如：2024-01-01",` | D17: get_lunar date format error (LLM) |
| 76 | `lunar_default_query` | `plugins_func/functions/get_time.py` | `query = "默认查询干支年和农历日期"` | D17: get_lunar default query (LLM) |
| 77 | `lunar_response_header` | `plugins_func/functions/get_time.py` | `response_text = f"根据以下信息回应用户的查询请求，并提供与{query}相关的信息：\n"` | D17: get_lunar result header telling the LLM to translate (data stays Chinese) |

Ghi chú:
- **D3** `hello_sample_rate`: firmware v2.5.0 lấy tốc độ giải mã từ hello của server; upstream trả lại
  tham số của chính thiết bị (16 kHz) nên mũ giải mã 16 kHz dù server mã hóa 24 kHz. Bản vá giữ các
  tham số khác của thiết bị, chỉ thay `sample_rate` bằng `conn.sample_rate` (= `xiaozhi.audio_params.sample_rate` = 24000).
- **D14** header: `Authorization`, `Proxy-Authorization`, `Cookie`, `X-Api-Key` và mọi `CF-Access-*` được ghi là `***`.
- **D17** lỗi thiết bị: `JARVIS_MCP_ERROR_PREFIX` = `mcp_error_prefix` trong `strings_vi.json` (mặc định rỗng): lỗi JSON-RPC
  của mũ (vd. `invalid step #2: ...`) đến plugin nguyên văn để `helmet_tasks` diễn giải; khi LLM gọi thẳng công cụ của mũ,
  `DeviceMCPExecutor` đọc `device_tool_failed` ("Xin lỗi thưa ngài, tôi không thực hiện được: ...").
- **D17** dòng `% <tên công cụ>`: chỉ gửi lên màn hình khi `jarvis.tool_display: true` trong config (mặc định tắt).
- `exit_default_goodbye` đổi `is None` thành `not say_goodbye`: LLM truyền chuỗi rỗng thì vẫn nói lời chào mặc định
  (trước đây sẽ đọc kết quả nội bộ tiếng Trung).
- **D15** `wake_no_echo`: với `enable_greeting: false`, upstream vẫn trả `stt` + `tts start` + (sau ~420 ms chờ hàng đợi
  âm thanh) `tts stop` cho mỗi lần đánh thức. Firmware chuyển sang Speaking (tắt mic) rồi về Listening và gửi thêm một
  `listen start`, server xóa bộ đệm âm thanh → mất phần đầu của lệnh nói liền một hơi ("Jarvis, mở mặt nạ" chỉ còn
  "mặt nạ"), HUD nháy SPEAKING. Bản vá không gửi gì cả (mũ vẫn ở Listening), chỉ ghi một dòng log INFO. Không gửi
  `stt` hiển thị: HUD coi mọi `stt` của người dùng là "đang suy nghĩ".
- `asr_openai_*`: provider `OpenaiASR` của upstream gọi `requests.post` (đồng bộ, không timeout) ngay trên event loop:
  trong lúc ASR chạy, mọi kết nối, OTA, MCP đều đứng; ASR treo thì server treo theo. Bản vá chạy request trong
  `asyncio.to_thread` với timeout `(3, ASR.<tên>.timeout_s)` (template: 30 s); hết giờ → văn bản rỗng như lỗi ASR.
- `ws_auth_failed_text`: câu gửi cho client bị từ chối token (trước: "认证失败") lấy từ `ws_auth_failed`.
- **Tách câu TTS** (dấu `.` ASCII): KHÔNG vá `core/providers/tts/base.py`; provider `jarvis_stream` (SV-B) tự tách câu
  theo `. ! ? ; :` và xuống dòng (D18). Nếu đổi sang provider upstream, cần vá thêm.
- Âm thanh dự phòng khi đánh thức (`config/assets/wakeup_words_short.wav`, vốn là giọng tiếng Trung) được thay bằng tiếng
  "ting" ngắn do `server/xiaozhi/assets/make_wakeup_chime.py` tạo lúc build; câu đáp tiếng Việt bằng giọng TTS được tạo
  tự động sau lần đánh thức đầu (bộ đệm `config/assets/wakeup_words/`).

## 4. Hook cho plugin (hợp đồng 4.7)

| Vị trí | Gọi | Kiểu |
|---|---|---|
| `core/handle/intentHandler.py::handle_user_intent`, ngay trước `if conn.intent_type == "function_call":` (sau kiểm tra câu thoát và từ đánh thức) | `await plugins_func.functions.helmet_tasks.try_quick_phrase(conn, text)`; `True` → bỏ qua LLM | async, nhận câu gốc (đã gỡ JSON speaker), phải trả về nhanh |
| `core/providers/tools/device_mcp/mcp_handler.py::handle_mcp_message`, sau `set_ready(True)` và refresh danh sách công cụ | `helmet_tasks.on_mcp_ready(conn)` | sync, tự lên lịch tác vụ async |

## 5. Dòng log độ trễ `[LAT]` (hợp đồng 4.10)

Định dạng (log INFO của xiaozhi, giá trị không chứa khoảng trắng):
`[LAT] sid=<session_id> stage=<stage> t_ms=<epoch ms> [key=value ...]`

| stage | Nơi ghi | Khóa thêm |
|---|---|---|
| `vad_end` | `asr/base.py` khi VAD server kết thúc câu (chế độ auto) / `listenMessageHandler.py` khi nhận `listen stop` (PTT) | `src=vad\|listen_stop`, `mode=`, `speech_end_ms=` (lúc còn giọng cuối), `audio_ms=` |
| `asr_done` | `asr/base.py` sau khi ASR trả kết quả | `text_len=`, `dur_ms=` |
| `llm_first_token` | `connection.py` chunk đầu có nội dung hoặc tool call | `depth=`, `turn=`, `kind=content\|tool` |
| `llm_done` | `connection.py` hết stream LLM | `depth=`, `turn=`, `tools=`, `aborted=` |
| `tts_first_audio` | `jarvis_stream.py` (SV-B) ở chunk PCM đầu tiên của lượt; dự phòng: `tts/base.py::handle_opus` cho provider upstream | `seg_chars=`, `ttfb_ms=` / `turn=` |
| `first_packet_sent` | `sendAudioHandle.py` gói Opus đầu tiên của lượt được gửi | `turn=` |
| `quick_phrase` | `helmet_tasks.py` (SV-D) khi khớp nhanh | `task=`, `score=` |
| `mcp_call` | `helmet_tasks.py` (SV-D) cho lệnh của plugin; `mcp_executor.py` (bản vá) khi LLM gọi thẳng công cụ của mũ | `tool=`, `ms=` (khứ hồi), `ok=`, `src=llm` (bản vá); `t_ms` = lúc gửi lệnh |

`sid` = session id của kết nối WebSocket (mỗi lần đánh thức một kết nối). `turn` = `sentence_id` của lượt.
Định dạng thời gian của file log đã có mili-giây (`log_format_file` trong template).

## 6. Quy trình nâng cấp upstream

1. Đọc release note; lấy digest mới: `docker buildx imagetools inspect ghcr.io/xinnan-tech/xiaozhi-esp32-server:server_<ver>`.
2. Clone tag mới, chạy `apply_patches.py --check --root <clone>/main/xiaozhi-server`.
3. Với mỗi dòng `FAIL`: mở file upstream, tìm đoạn tương ứng, sửa `anchor`/`replacement` trong `build_edits()`
   (giữ nguyên `id` để dấu idempotency không đổi). Nếu chức năng đã có sẵn ở upstream thì xóa bản vá đó.
4. `XIAOZHI_SRC=<clone>/main/xiaozhi-server pytest server/tests/test_patches.py` → sửa `ALLOWED_LEFTOVERS` nếu upstream
   thêm chuỗi tiếng Trung mới (chỉ cho phép khi chuỗi đó không tới loa/màn hình với cấu hình Jarvis).
5. `scan_cjk.py` trên cây mới; chuỗi `OTHER`/`EXC` mới mà người dùng có thể nghe/thấy → thêm bản vá + khóa trong `strings_vi.json`.
6. Đổi `FROM ...@sha256:` trong `server/xiaozhi/Dockerfile`, build lại, chạy thử (mục 8 của `server/README.md`).

## 7. Chuỗi tiếng Trung còn lại (sau khi vá, các file đang dùng)

Kết quả `scan_cjk.py` trên image: không còn chuỗi tiếng Trung nào được **đọc ra loa hoặc hiện lên màn hình** với cấu hình
Jarvis. Những gì còn lại (ngoài log, docstring, bảng dấu câu, phép so sánh) đều thuộc nhánh không dùng hoặc không tới thiết bị:

| Nhóm | Ví dụ / file | Vì sao để nguyên |
|---|---|---|
| Chế độ manager-api (智控台) | `receiveAudioHandle.py` (giới hạn ký tự, mã gắn thiết bị), `connection.py` "服务器重启中...", `serverMessageHandler.py`, `config/manage_api_client.py` | Chế độ tối giản không có `manager-api.url` |
| Intent `intent_llm` | `intentHandler.py` (当前时间…, 工具调用超时…) | Dùng `Intent: function_call` |
| Vision / voiceprint / news / weather | `vision_handler.py`, `dialogue.py` 当前说话人, `plugin_executor.py` 新闻源, `prompt_manager.py` 未知位置/天气 | Không có camera, voiceprint tắt, plugin không bật, prompt không dùng biến vị trí/thời tiết |
| Biến prompt tiếng Trung | `current_time.py`, `prompt_manager.py` (星期一…, 中文) | Prompt Jarvis không dùng `today_weekday`, `lunar_date`, `language` (D16) |
| Dữ liệu `get_lunar` | `get_time.py` (农历信息…) | Chỉ gửi cho LLM; mô tả công cụ + tiêu đề kết quả đã yêu cầu dịch sang ngôn ngữ người dùng |
| Phản hồi HTTP / khung thô | `ota_handler.py` GET "OTA接口运行正常…", `websocket_server.py` "端口正常…" (client thử nghiệm không có device-id) | Không hiển thị trên mũ (firmware v2.5.0 bỏ qua khung text không có `type`); câu từ chối token đã Việt hóa (`ws_auth_failed_text`) |
| Lỗi HTTP của ASR | `asr/openai.py` "API请求失败: …" | Ngoại lệ bị bắt và chỉ ghi log |
| Lỗi khởi động / nội bộ | factory `不支持的…类型`, kiểm tra ffmpeg, `磁盘空间不足`, `客户端已中止`, `register.py` mô tả enum, `handle_exit_intent` 退出意图处理失败 (Action.NONE) | Chỉ ra console/log hoặc không bao giờ được đọc |
| So khớp lỗi upstream | `helmet_tasks.py` (SV-D) `"工具 "…"不存在"`, `"超时"` | So sánh chuỗi, không phát ra |

Nguồn tiếng Trung **động** không nằm trong mã: kết quả ASR (Whisper có thể "bịa" câu tiếng Trung khi im lặng → bộ lọc của
service `asr`), chuỗi từ đánh thức firmware gửi lên (FW7 phải đặt chuỗi ASCII/tiếng Việt), và câu trả lời của LLM.
