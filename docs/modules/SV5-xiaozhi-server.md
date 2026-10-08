# SV5 – xiaozhi-server: cấu hình & tính cách

| | |
|---|---|
| **Mục tiêu** | Chạy xiaozhi-esp32-server v0.9.7 (chế độ tối giản, chỉ module Python). Nối ASR, LLM, TTS của dự án vào. Việt hóa toàn bộ câu nói mặc định. Đặt tính cách "quản gia lịch lãm" |
| **Phụ thuộc** | SV2, SV3, SV4 |
| **Kết quả giao nộp** | `server/xiaozhi/Dockerfile`, `server/xiaozhi/data/.config.yaml.tmpl` + `entrypoint.py`, `server/xiaozhi/prompts/jarvis-prompt.txt`, `server/xiaozhi/patches/` |
| **Trạng thái** | Code xong. Đã chạy cả stack trên máy dev với bộ giả lập mũ (`tools/tests/e2e/device_sim.py`): khớp nhanh, gọi tool qua LLM, gộp nhiều tác vụ, hỏi chuyên gia, 50 lệnh liên tiếp. Chưa thử với mũ thật và trên batai |

## 1. Đóng gói
- `server/xiaozhi/Dockerfile`: `FROM` image chính thức `ghcr.io/xinnan-tech/xiaozhi-esp32-server:server_0.9.7@sha256:…` (ghim digest). Trên nền đó:
  - `COPY` plugin của dự án vào `plugins_func/functions/` (`helmet_tasks` của SV6, `ask_expert`) và provider TTS `jarvis_stream` (SV4);
  - `COPY` prompt, template cấu hình, `taskslib.py` + `tasks.yaml` (bản dự phòng; compose mount `server/tasks` đè lên để sửa nóng);
  - áp **77 bản vá có neo** bằng `patches/apply_patches.py` lúc build (danh sách và lý do: `server/xiaozhi/patches/README.md`): hook khớp nhanh và MCP sẵn sàng (SV6), `hello` báo 24 kHz (D3), dòng log `[LAT]` đo độ trễ, che header bí mật, Việt hóa chuỗi tiếng Trung, wake word không trả lời riêng (`wake_no_echo`), ASR OpenAI chạy ngoài vòng lặp asyncio và có timeout. Build dừng nếu một bản vá không áp được; `scan_cjk.py` báo cáo chuỗi tiếng Trung còn lại.
- Cấu hình **không mount từ host**: `entrypoint.py` sinh `data/.config.yaml` từ `.config.yaml.tmpl` mỗi lần container khởi động, lấy bí mật từ biến môi trường (D14). Thiếu biến hoặc khóa yếu/placeholder → container thoát (trừ khi `JARVIS_ALLOW_WEAK_SECRETS=1`, chỉ cho máy dev).
- Dữ liệu wav để thử ASR do service `asr` tự lưu (`ASR_SAVE_DIR`, SV2); xiaozhi xóa file tạm (`delete_audio: true`).

## 2. Các khóa chính trong `.config.yaml`

| Khóa | Giá trị |
|---|---|
| `server.port` / `http_port` | 8000 / 8003 trong container; host mở **8100** → 8000 và 8003 → 8003 vì cổng 8000 của batai thuộc dự án khác (D21) |
| `server.websocket` | Địa chỉ WS mà OTA trả cho thiết bị: `PUBLIC_WS_URL` trong `.env` (SV7) |
| `server.auth` | `enabled: true`, `auth_key` cố định từ `.env`, `allowed_devices: []`: thiết bị trong danh sách được bỏ qua kiểm tra token mà `Device-Id` thì giả được, nên không dùng (SV7) |
| `selected_module` | `VAD: SileroVAD`, `ASR: OpenaiASR`, `LLM: JarvisLLM` (OpenAI-compatible → LiteLLM), `TTS: JarvisTTS` (provider `jarvis_stream`), `Intent: function_call`, `Memory: nomem` |
| `ASR.OpenaiASR` | `base_url: http://asr:9000/v1/audio/transcriptions`, timeout 30 s (bản vá: chạy trong thread) |
| `LLM.JarvisLLM` | `base_url: http://litellm:4000/v1`, `model_name: jarvis-main`, `max_tokens: 300`, `temperature: 0.6`; không đặt `top_p`/`frequency_penalty` (D25) |
| `TTS.JarvisTTS` | `jarvis_stream` → `http://tts-router:8880/v1/audio/speech`, PCM 24 kHz, giọng `TTS_VOICE` |
| `Intent.function_call.functions` | `helmet_tasks` (mở rộng thành mọi `task_*`), `ask_expert`; `handle_exit_intent` và `get_lunar` upstream luôn thêm (mô tả đã Việt hóa) |
| `VAD.SileroVAD` | `min_silence_duration_ms` = `VAD_MIN_SILENCE_MS` (mặc định 700, D28): thấp hơn thì trả lời nhanh hơn nhưng dễ cắt ngang người nói chậm |
| `prompt` | `prompts/jarvis-prompt.txt` (mục 3) |
| `wakeup_words` | So khớp **sau khi bỏ khoảng trắng và dấu câu**, phân biệt hoa thường, nên liệt kê đủ biến thể: `Jarvis`, `jarvis`, `JARVIS`, `giắcvít`, `Giắcvít`, `quảngia`, `Quảngia`, `Quảnggia`, `hàuơi`, `Hàuơi` và cách ASR hay viết nhầm `hậuơi`, `Hậuơi`, `Hàoơi` (D15, D33) |
| `exit_commands` | `tạmbiệt`, `Tạmbiệt`, `kếtthúc`, `Kếtthúc`, `goodbye`, `Goodbye`, `thatsall`, `Thatsall` |
| `system_error_response` | "Xin lỗi thưa ngài, hệ thống đang gặp sự cố, xin thử lại sau." |
| `end_prompt.prompt` | Câu dặn LLM chào tạm biệt kiểu quản gia, bằng tiếng Việt |
| `enable_greeting` | **`false`** (D15): câu chào sau wake word làm mất khoảng 2 s âm thanh và hỏng lệnh nói liền một hơi ("Jarvis, mở mặt nạ"). Bản vá `wake_no_echo` khiến server không gửi gì khi nhận wake word; câu đáp wake word (khi bật lại) đã đổi sang tiếng Việt |

**Việt hóa:** mọi câu nói/hiển thị cứng bằng tiếng Trung của upstream (lỗi tool, thông báo timeout, mô tả plugin, câu đáp wake word, lỗi xác thực WebSocket…) được bản vá thay bằng chuỗi trong `patches/strings_vi.json` (D17); test kiểm tra không còn chuỗi tiếng Trung trong các khóa cấu hình đang dùng.

## 3. Tính cách

`prompts/jarvis-prompt.txt` (thay `agent-base-prompt.txt` mặc định, D16): quản gia J.A.R.V.I.S lịch lãm, gọi "thưa ngài"/"Sir", trả lời 1–3 câu văn bản thuần (không markdown, không emoji), trả lời theo ngôn ngữ người dùng (câu trộn → tiếng Việt, giữ thuật ngữ tiếng Anh), luôn gọi tool khi điều khiển mũ và không nói đã làm khi chưa gọi, không bịa trạng thái (gọi `self.system.get_status`), câu cần kiến thức sâu → `ask_expert`, đoán ý khi ASR sai gần âm, không tự nói wake word. Giữ `{{current_time}}`; không dùng `{{language}}`, thời tiết, vị trí, âm lịch.

## 4. Khử vọng phía server (ngắt lời bằng giọng nói)
- Khử vọng phía server của v0.9.7 chỉ có với kết nối MQTT + UDP; mũ dùng WebSocket nên **không dùng được**. Ngắt lời bằng giọng nói vì vậy là giới hạn đã biết: firmware mặc định tắt nhận wake word khi đang nói (`CONFIG_JARVIS_WAKE_WORD_BARGE_IN=n`, D12), nút PTT luôn ngắt được (D8).

## 5. Công việc
- [x] Dockerfile mỏng, ghim digest v0.9.7
- [x] Template `.config.yaml` đầy đủ các khối trên, sinh khi khởi động
- [x] Việt hóa: bản vá chuỗi tiếng Trung + báo cáo `scan_cjk`
- [x] Viết prompt (thử với model 4B trên máy dev; cần thử lại 20 câu với model 30B trên batai)
- [x] Kiểm tra giao thức với firmware v2.5.0 bằng bộ giả lập (hello, MCP phân trang, Opus 16/24 kHz)
- [ ] Kết nối mũ thật qua LAN (`http://<IP>:8003/xiaozhi/ota/`)
- [x] Khử vọng phía server: không áp dụng được với WebSocket (mục 4)

## 6. Tiêu chí nghiệm thu
1. Mũ kết nối, hội thoại Việt–Anh trọn vẹn qua LAN. Log không còn lỗi lệch giao thức.
2. Không còn câu tiếng Trung nào được nói ra hoặc hiện lên màn hình trong 30 phút dùng thử.
3. 20 câu hội thoại mẫu: ≥18 câu đúng phong cách (ngắn, lịch lãm, đúng ngôn ngữ).
4. Thời gian từ lúc nói xong đến âm thanh trả lời đầu tiên ≤1.5 giây qua LAN (đo ở IT1).
