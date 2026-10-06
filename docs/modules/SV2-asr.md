# SV2 – Nhận dạng giọng nói Việt–Anh (ASR)

| | |
|---|---|
| **Mục tiêu** | Chuyển giọng nói thành chữ cho câu trộn Việt–Anh. Nhận dạng đúng các từ lệnh ("open mask", "mặt nạ", "đèn mắt"…). Độ trễ thấp |
| **Phụ thuộc** | SV1 |
| **Kết quả giao nộp** | `server/asr/` (Dockerfile + `app.py` + gói `jarvis_asr`, README), công cụ đánh giá `tools/tests/asr/` (`eval.py`, `capture_to_manifest.py`, `make_synthetic.py`, 63 câu mẫu trong `sentences.md`), báo cáo so sánh A/B |
| **Trạng thái** | Code xong (D19), chạy trong stack trên máy dev. Đã đo thử bằng bộ câu tổng hợp từ TTS (chỉ là smoke test). Chưa có: bộ ghi âm thật bằng mic INMP441 → chưa so sánh A/B, chưa chọn cấu hình cuối, chưa đo trên RTX 5090 |

## 1. Thiết kế

**Cách kết nối với xiaozhi-server:**
- Dùng provider có sẵn `OpenaiASR` (`type: openai`), trỏ `base_url: http://asr:9000/v1/audio/transcriptions`.
- Provider này **chỉ gửi file wav và tham số `model`**: không gửi ngôn ngữ, không gửi prompt. Vì vậy mọi cấu hình nhận dạng phải đặt ở phía service ASR, đọc từ biến môi trường.

**Service `asr`** (FastAPI, cổng nội bộ 9000; chi tiết API và biến môi trường: `server/asr/README.md`):
- Endpoint `POST /v1/audio/transcriptions` (multipart: `file`, `model`), trả về `{"text": "..."}`; thêm `language`, `prompt`, `hotwords`, `response_format=verbose_json` để thử trực tiếp.
- Thêm `GET /health` (503 khi model chưa nạp xong).
- Engine mặc định: **faster-whisper 1.2.1 `large-v3`** (ghim revision), `compute_type=float16`, chạy với user không phải root (uid 10001). WAV được giải mã bằng soundfile (tránh lỗi PyAV 19).
  - CTranslate2 phải **≥4.8, bản build cho CUDA 12.8**. Bản cũ lỗi `CUBLAS_STATUS_NOT_SUPPORTED` trên sm_120 khi dùng INT8.
  - Nạp model một lần lúc khởi động và làm nóng bằng một file mẫu.
- Tham số nhận dạng (đặt qua biến môi trường):
  - `ASR_LANGUAGE=vi`: ép tiếng Việt. Câu trộn vẫn giữ nguyên từ tiếng Anh (chữ Latin) nhưng không bị dịch. **Phải so sánh với `auto`** (để model tự đoán ngôn ngữ).
  - `ASR_INITIAL_PROMPT`: câu mồi chứa từ vựng hay dùng, ví dụ "Jarvis, quản gia, open mask, close mask, mặt nạ, đèn mắt, combat mode, thưa ngài".
  - `beam_size=5`, `vad_filter=false` (xiaozhi-server đã cắt câu bằng SileroVAD), `condition_on_previous_text=false`; `ASR_HOTWORDS`; giới hạn số token theo độ dài audio (chống lặp vòng).
- **Cổng âm thanh trước khi nhận dạng:** RMS + Silero VAD trả chuỗi rỗng cho đoạn im lặng/tiếng ồn.
- **Lọc ảo giác:** bỏ các câu Whisper hay bịa ra khi gặp khoảng lặng ("Cảm ơn các bạn đã xem", "Hãy subscribe…"), đoạn logprob thấp, lặp vòng, chữ CJK, chép lại câu mồi. Danh sách ở `server/asr/config/hallucinations.txt`: cụm từ thường xóa mọi câu chứa nó, `~cụm` chỉ xóa câu trùng nguyên văn (cho cụm ngắn có thể nằm trong lệnh thật), `=câu` chỉ chặn khi cả lượt nói đúng bằng câu đó. Compose mount cả thư mục nên sửa trên server là có hiệu lực trong ~2 s; test kiểm tra mọi câu mẫu đều lọt qua bộ lọc.
- Ghi log thời gian xử lý từng request (`[ASR] rid=… audio_ms=… infer_ms=…`).
- **Thu dữ liệu:** đặt `ASR_SAVE_DIR=/data/asr-dataset` thì service lưu wav + transcript (jsonl) vào volume `asr_dataset` để làm bộ thử thật (`tools/tests/asr/README.md`).

**Engine phương án B** (cùng API, đổi bằng biến `ASR_ENGINE`):
- Qwen3-ASR-1.7B chạy qua vLLM (có hỗ trợ tiếng Việt, Apache-2.0).
- PhoWhisper-large (bản CT2): giỏi tiếng Việt thuần nhưng nhận từ tiếng Anh kém hơn.

## 2. Bộ dữ liệu thử và so sánh A/B
- Tự ghi **≥50 câu** bằng mic INMP441 thật (thu qua thiết bị: service ASR lưu wav khi bật `ASR_SAVE_DIR`, rồi `capture_to_manifest.py` ghép với câu mẫu), chia làm 3 nhóm:
  1. Lệnh điều khiển trộn ngôn ngữ: "open mask rồi bật đèn", "mở mặt nạ đi Jarvis"…
  2. Câu hỏi tiếng Việt thuần.
  3. Câu tiếng Anh thuần.
- Mỗi câu có kèm văn bản đúng. Script `tools/tests/asr/eval.py` tính **WER** (chữ số được đọc thành chữ, viết liền/rời chỉnh theo câu mẫu), **tỷ lệ đúng từ khóa lệnh**, WER trước bộ lọc ảo giác và độ trễ. Qwen3-ASR và PhoWhisper chạy bằng container ASR thứ hai/thứ ba để so sánh cùng lúc.
- Các cấu hình cần so sánh:
  - large-v3 + `vi`
  - large-v3 + `auto`
  - large-v3 + `vi` + prompt
  - Qwen3-ASR
  - (tùy chọn) PhoWhisper

## 3. Công việc
- [x] Viết `server/asr/app.py` + Dockerfile (CUDA 12.8, ctranslate2 4.8.2)
- [x] Viết lọc ảo giác, healthcheck, log thời gian
- [x] Viết `eval.py` và bộ câu mẫu; [ ] ghi âm 50 câu bằng mic thật (cần mũ)
- [ ] Chạy so sánh A/B, chọn cấu hình mặc định, ghi kết quả vào `tools/tests/asr/RESULTS.md`

## 4. Tiêu chí nghiệm thu
1. Từ khóa lệnh (mask, mặt nạ, đèn mắt, open, close…) nhận đúng ≥95% trên bộ thử.
2. WER chung ≤15% với câu trộn ngôn ngữ, ≤10% với câu tiếng Việt thuần.
3. Thời gian xử lý một câu 3 giây ≤300ms trên RTX 5090 (đã nạp sẵn model).
4. Gửi 30 giây im lặng hoặc tiếng ồn: trả về chuỗi rỗng, không bịa câu.
