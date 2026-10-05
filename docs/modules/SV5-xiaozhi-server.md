# SV5 – xiaozhi-server: cấu hình & tính cách

| | |
|---|---|
| **Mục tiêu** | Chạy xiaozhi-esp32-server v0.9.7 (chế độ tối giản, chỉ module Python). Nối ASR, LLM, TTS của dự án vào. Việt hóa toàn bộ câu nói mặc định. Đặt tính cách "quản gia lịch lãm" |
| **Phụ thuộc** | SV2, SV3, SV4 |
| **Kết quả giao nộp** | `server/xiaozhi/Dockerfile`, `server/xiaozhi/data/.config.yaml`, `server/xiaozhi/prompts/jarvis-prompt.txt` |

## 1. Đóng gói
- `server/xiaozhi/Dockerfile`: `FROM` image chính thức của xiaozhi-server, **ghim đúng bản v0.9.7** (dùng digest, không dùng `server_latest`). Trên nền đó:
  - `COPY` plugin của dự án vào `plugins_func/functions/` (SV6 + `ask_expert`);
  - `COPY` file prompt;
  - áp bản vá nhỏ cho chức năng khớp nhanh (SV6).
- Mount `data/.config.yaml`. File này ghi đè `config.yaml` mặc định.
- Thư mục tạm `tmp/` để trong volume, dùng để lấy file wav làm dữ liệu thử ASR (SV2).

## 2. Các khóa chính trong `.config.yaml`

| Khóa | Giá trị |
|---|---|
| `server.port` / `http_port` | 8000 / 8003 |
| `server.websocket` | Địa chỉ WS mà OTA trả cho thiết bị (xem SV7) |
| `server.auth` | `enabled: true` + `allowed_devices` (MAC của mũ) + token (SV7) |
| `selected_module` | `VAD: SileroVAD`, `ASR: OpenaiASR`, `LLM: OpenAILLM`, `TTS: VieNeuTTS` (khối provider tự đặt tên), `Intent: function_call`, `Memory: nomem` (có thể đổi sang bộ nhớ ngắn hạn sau) |
| `ASR.OpenaiASR` | `base_url: http://asr:9000/v1/audio/transcriptions`, `model_name: whisper-large-v3` |
| `LLM.OpenAILLM` | `base_url: http://litellm:4000/v1`, `model_name: jarvis-main`, `max_tokens: 300`, `temperature: 0.6` |
| `TTS.VieNeuTTS` | `type: openai` hoặc `custom` → `http://tts:8880/v1/audio/speech` |
| `Intent.function_call.functions` | `helmet_tasks`, `ask_expert`, `get_time`, `web_search` (nếu muốn) |
| `VAD.SileroVAD` | Chỉnh `min_silence_duration_ms` ~600–800ms để cân bằng giữa trả lời nhanh và không cắt ngang khi người nói ngập ngừng |
| `prompt_template` | `prompts/jarvis-prompt.txt` (thay `agent-base-prompt.txt` mặc định) |
| `prompt` | Mô tả nhân vật (mục 3) |
| `wakeup_words` | `["jarvis", "giắc vít", "quản gia"]`: thêm các cách ASR hay viết sai |
| `exit_commands` | `["tạm biệt", "kết thúc", "goodbye", "that's all"]` |
| `system_error_response` | "Xin lỗi thưa ngài, hệ thống đang gặp sự cố, xin thử lại sau." |
| `end_prompt.prompt` | Câu dặn LLM chào tạm biệt kiểu quản gia, bằng tiếng Việt |
| `enable_greeting` / `enable_wakeup_words_response_cache` | Bật, nhưng **ghi lại câu chào bằng giọng VieNeu** (thay file tiếng Trung) |

**Việt hóa:**
- `config.yaml` mặc định có nhiều chuỗi tiếng Trung: câu nhân vật, lỗi hệ thống, câu kết thúc, câu trả lời wake word, mô tả plugin.
- Rà toàn bộ bằng `grep` các ký tự CJK trong config và trong các plugin được bật. Thay bằng tiếng Việt hoặc tắt đi.

## 3. Tính cách (bản nháp cho `prompt`)
```
Bạn là J.A.R.V.I.S — quản gia AI tích hợp trong mũ giáp của ngài chủ nhân.
- Phong cách: lịch lãm, điềm tĩnh, hài hước nhẹ kiểu Anh quốc. Gọi người dùng là "thưa ngài" (tiếng Việt) hoặc "Sir" (tiếng Anh).
- Trả lời NGẮN: 1–3 câu, vì câu trả lời sẽ được đọc thành tiếng. Không dùng markdown, emoji, ký hiệu.
- Ngôn ngữ: trả lời bằng ngôn ngữ người dùng vừa dùng. Nếu câu trộn Việt–Anh, trả lời tiếng Việt và giữ nguyên thuật ngữ tiếng Anh.
- Khi được yêu cầu điều khiển mũ (mặt nạ, đèn mắt, chế độ…), LUÔN gọi tool tương ứng; không được nói là đã làm khi chưa gọi tool.
- Không bịa trạng thái thiết bị; khi cần thì gọi self.system.get_status.
- Câu hỏi cần kiến thức chuyên sâu/cập nhật → dùng tool ask_expert.
- Đầu vào đến từ nhận dạng giọng nói, có thể sai chính tả gần âm; hãy đoán ý hợp lý nhất.
```
`prompts/jarvis-prompt.txt` dựa trên `agent-base-prompt.txt` gốc (tiếng Anh), giữ các biến (`{{base_prompt}}`…), bỏ những phần không hợp phong cách Jarvis.

## 4. Khử vọng phía server (ngắt lời bằng giọng nói)
- Từ v0.9.6, server có khử vọng cho thiết bị chỉ có một mic. Đọc tài liệu và release note để bật, kết hợp với `USE_SERVER_AEC` trên firmware (FW7).
- Thử ở mức âm lượng 50% và 80%. Ghi kết quả; nếu không ổn định thì tắt và chỉ ngắt lời bằng nút.

## 5. Công việc
- [ ] Dockerfile mỏng, ghim digest v0.9.7
- [ ] Viết `.config.yaml` đầy đủ các khối trên
- [ ] Việt hóa: rà chuỗi CJK, ghi lại câu chào và câu báo lỗi bằng giọng mới
- [ ] Viết prompt và thử 20 câu hội thoại xem phong cách có ổn không
- [ ] Kết nối mũ qua LAN (`http://<IP>:8003/xiaozhi/ota/`) và kiểm tra **firmware v2.5.0 có tương thích** không (hello, MCP, audio v2/v3)
- [ ] Thử khử vọng phía server

## 6. Tiêu chí nghiệm thu
1. Mũ kết nối, hội thoại Việt–Anh trọn vẹn qua LAN. Log không còn lỗi lệch giao thức.
2. Không còn câu tiếng Trung nào được nói ra hoặc hiện lên màn hình trong 30 phút dùng thử.
3. 20 câu hội thoại mẫu: ≥18 câu đúng phong cách (ngắn, lịch lãm, đúng ngôn ngữ).
4. Thời gian từ lúc nói xong đến âm thanh trả lời đầu tiên ≤1.5 giây qua LAN (đo ở IT1).
