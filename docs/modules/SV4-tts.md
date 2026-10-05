# SV4 – Tổng hợp giọng nói (TTS)

| | |
|---|---|
| **Mục tiêu** | Đọc câu trả lời bằng giọng nam trầm, tự nhiên, đọc được câu tiếng Việt có xen từ tiếng Anh. Độ trễ thấp vì đọc theo từng câu |
| **Phụ thuộc** | SV1 |
| **Kết quả giao nộp** | Service `tts` trong compose, `server/tts/` (cấu hình, và router nếu cần), cấu hình TTS trong xiaozhi-server |

## 1. Thiết kế

**Engine chính: VieNeu-TTS v3 Turbo** (Apache-2.0):
- Có API kiểu OpenAI `POST /v1/audio/speech`, có image Docker.
- Khoảng 1–2GB VRAM, độ trễ tới âm đầu tiên ~100ms. Đọc được từ tiếng Anh xen trong câu tiếng Việt.
- Lưu ý: bản v4 là bản đóng, chỉ dùng qua API. **Ghim v3 Turbo.**

**Giọng:**
- Chọn một giọng nam trầm có sẵn của VieNeu.
- Hoặc clone từ mẫu giọng 10–30 giây mà bạn **có quyền sử dụng** (giọng của bạn, hoặc người đồng ý cho dùng). Không clone giọng diễn viên hay người thật khi chưa được phép.
- Tốc độ đọc khoảng 1.05: quản gia nói gọn và dứt khoát.

**Kết nối với xiaozhi-server:**
- Dùng provider `openai` (`api_url: http://tts:8880/v1/audio/speech`), hoặc `custom` nếu tham số giọng không khớp.
- xiaozhi-server tự tách câu, gọi TTS cho từng câu, rồi đổi sang Opus 24kHz để gửi xuống thiết bị.

**Chuẩn hóa văn bản** (làm nếu VieNeu đọc sai):
- Số và đơn vị ("5V" → "năm vôn", "30%" → "ba mươi phần trăm").
- Viết tắt ("AI", "WiFi").
- Ký hiệu, emoji (bỏ đi).
- Làm trong một lớp mỏng `server/tts/router` (FastAPI, cùng API `/v1/audio/speech`) đặt trước VieNeu.

**Phương án B, giọng tiếng Anh chuẩn:**
- Nếu câu trả lời **toàn tiếng Anh** mà VieNeu đọc giọng lơ lớ, router phát hiện ngôn ngữ từng câu và gửi sang một TTS tiếng Anh, ví dụ Kokoro với giọng nam Anh-Anh, hợp với kiểu Jarvis.
- Câu tiếng Việt hoặc trộn ngôn ngữ vẫn đi qua VieNeu.

## 2. Bộ thử (`tools/tests/tts/sentences.txt`)
- 20 câu, gồm:
  - tiếng Việt thuần;
  - câu trộn ("Đã kích hoạt combat mode, thưa ngài");
  - tiếng Anh thuần;
  - câu có số và đơn vị;
  - câu dài 40 từ.
- Script đo thời gian tới âm đầu tiên và tổng thời gian; lưu file wav để nghe đánh giá.

## 3. Công việc
- [ ] Chạy VieNeu-TTS v3 Turbo trong compose, chọn giọng
- [ ] Cấu hình provider trong xiaozhi-server, thử qua thiết bị
- [ ] Chạy bộ thử; nếu cần thì viết router chuẩn hóa văn bản
- [ ] (Tùy chọn) Phương án B cho câu tiếng Anh

## 4. Tiêu chí nghiệm thu
1. Thời gian tới âm đầu tiên ≤300ms cho câu ≤15 từ.
2. Đọc đúng 20/20 câu trong bộ thử, không đọc sai số và đơn vị. Từ tiếng Anh trong câu trộn nghe hiểu được.
3. Nghe qua loa trong mũ: rõ, không rè, âm lượng ổn ở mức 70%.
4. Một giờ hội thoại liên tục: VRAM ổn định, không rò rỉ bộ nhớ.
