# SV4 – Tổng hợp giọng nói (TTS)

| | |
|---|---|
| **Mục tiêu** | Đọc câu trả lời bằng giọng nam trầm, tự nhiên, đọc được câu tiếng Việt có xen từ tiếng Anh. Độ trễ thấp vì đọc theo từng câu |
| **Phụ thuộc** | SV1 |
| **Kết quả giao nộp** | Service `vieneu` + `tts-router` (+ `kokoro`, profile `english-tts`) trong compose, `server/tts/` (README, router, image VieNeu, `clone_voice.py`), provider `server/xiaozhi/providers/tts/jarvis_stream.py`, bộ thử `tools/tests/tts/` |
| **Trạng thái** | Code xong (D18), đã chạy thật trên máy dev: stream PCM 24 kHz, chuẩn hóa văn bản, chọn giọng, clone giọng, Kokoro + dự phòng. Chưa nghe qua loa trong mũ và chưa đo trên RTX 5090 |

## 1. Thiết kế

**Engine chính: VieNeu-TTS v3 Turbo** (Apache-2.0):
- Có API kiểu OpenAI `POST /v1/audio/speech`. Image build từ tag `app-v0.18.9` (target `prod`), chạy `apps.openai_speech`, trả PCM 24 kHz.
- Khoảng 1GB VRAM (đo ~760 MiB trên máy dev). Đọc được từ tiếng Anh xen trong câu tiếng Việt.
- Lưu ý: bản v4 là bản đóng, chỉ dùng qua API. **Ghim v3 Turbo.** VieNeu nạp model bằng `trust_remote_code` không ghim revision, nên entrypoint chuyển sang offline (`HF_HUB_OFFLINE`) ngay khi model đã có trong cache (`TTS_HF_OFFLINE=auto`). Healthcheck khởi động lại container khi bộ lập lịch stream của VieNeu chết.

**Giọng:**
- Chọn một giọng nam trầm có sẵn của VieNeu: mặc định `TTS_VOICE=Thiền Tâm Đức` (bảng cao độ các giọng nam và cách nghe thử: `server/tts/README.md`).
- Hoặc clone từ mẫu giọng 10–30 giây mà bạn **có quyền sử dụng** (giọng của bạn, hoặc người đồng ý cho dùng) bằng `server/tts/vieneu/clone_voice.py`. Không clone giọng diễn viên hay người thật khi chưa được phép.
- Tốc độ đọc: VieNeu v3 Turbo không có `speed`, nên router co giãn âm thanh VieNeu (WSOLA, giữ cao độ). Mũ đặt 1.5 (10/2026, theo yêu cầu người dùng) trong `server/xiaozhi/data/.config.yaml.tmpl`.

**Kết nối với xiaozhi-server:**
- Provider riêng `jarvis_stream` (dựa trên `index_stream.py` của upstream): stream PCM 24 kHz thật từ `http://tts-router:8880/v1/audio/speech`, tự tách câu (cả dấu `.` `!` `?` ASCII và xuống dòng; đoạn đầu tiên còn cắt ở dấu phẩy để có tiếng sớm), rồi xiaozhi đổi sang Opus 24 kHz gửi xuống mũ.

**Chuẩn hóa văn bản** (làm nếu VieNeu đọc sai):
- Số và đơn vị ("5V" → "năm vôn", "30%" → "ba mươi phần trăm").
- Viết tắt ("AI", "WiFi").
- Ký hiệu, emoji (bỏ đi).
- Làm trong một lớp mỏng `server/tts/router` (FastAPI, cùng API `/v1/audio/speech`) đặt trước VieNeu. Đã có: số kiểu miền Bắc (có thể đổi "lẻ"/"ngàn"), số thập phân/hàng nghìn, đơn vị, giờ, ngày (cả "tháng 3/2026", "Q1/2025"), khoảng, phép tính, tỉ số, số điện thoại theo nhóm, ký hiệu phòng/lớp ("phòng 12A"), viết tắt (thêm qua `TTS_ABBREV_FILE`), bỏ markdown/emoji.

**Phương án B, giọng tiếng Anh chuẩn:**
- Nếu câu trả lời **toàn tiếng Anh** mà VieNeu đọc giọng lơ lớ, router phát hiện ngôn ngữ từng câu và gửi sang một TTS tiếng Anh, ví dụ Kokoro với giọng nam Anh-Anh, hợp với kiểu Jarvis.
- Câu tiếng Việt hoặc trộn ngôn ngữ vẫn đi qua VieNeu.
- Đã làm: Kokoro-FastAPI (`bm_george`), bật bằng profile `english-tts` + `TTS_ENGLISH_ENGINE=kokoro`. Kokoro lỗi (kể cả trả 200 rồi không có âm thanh) → câu đó đọc bằng VieNeu và Kokoro bị đánh dấu down tới lần kiểm tra sức khỏe sau.

## 2. Bộ thử (`tools/tests/tts/sentences.txt`)
- 20 câu (thêm vài câu sau này), gồm:
  - tiếng Việt thuần;
  - câu trộn ("Đã kích hoạt combat mode, thưa ngài");
  - tiếng Anh thuần;
  - câu có số và đơn vị;
  - câu dài 40 từ.
- Script `tools/tests/tts/bench.py` đo thời gian tới âm đầu tiên và tổng thời gian, lưu file wav để nghe đánh giá, quét cao độ các giọng (`--voices-scan`). Trên máy dev (GTX 1660): TTFA trung vị 159 ms, RTF 0,75.

## 3. Công việc
- [x] Chạy VieNeu-TTS v3 Turbo trong compose, chọn giọng mặc định
- [x] Cấu hình provider trong xiaozhi-server; [ ] thử qua mũ thật
- [x] Chạy bộ thử; viết router chuẩn hóa văn bản
- [x] (Tùy chọn) Phương án B cho câu tiếng Anh (Kokoro)

## 4. Tiêu chí nghiệm thu
1. Thời gian tới âm đầu tiên ≤300ms cho câu ≤15 từ.
2. Đọc đúng 20/20 câu trong bộ thử, không đọc sai số và đơn vị. Từ tiếng Anh trong câu trộn nghe hiểu được.
3. Nghe qua loa trong mũ: rõ, không rè, âm lượng ổn ở mức 70%.
4. Một giờ hội thoại liên tục: VRAM ổn định, không rò rỉ bộ nhớ.
