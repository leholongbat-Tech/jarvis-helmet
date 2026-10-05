# SV2 – Nhận dạng giọng nói Việt–Anh (ASR)

| | |
|---|---|
| **Mục tiêu** | Chuyển giọng nói thành chữ cho câu trộn Việt–Anh. Nhận dạng đúng các từ lệnh ("open mask", "mặt nạ", "đèn mắt"…). Độ trễ thấp |
| **Phụ thuộc** | SV1 |
| **Kết quả giao nộp** | `server/asr/` (Dockerfile + `app.py`), bộ dữ liệu thử `tools/tests/asr/`, báo cáo so sánh A/B |

## 1. Thiết kế

**Cách kết nối với xiaozhi-server:**
- Dùng provider có sẵn `OpenaiASR` (`type: openai`), trỏ `base_url: http://asr:9000/v1/audio/transcriptions`.
- Provider này **chỉ gửi file wav và tham số `model`**: không gửi ngôn ngữ, không gửi prompt. Vì vậy mọi cấu hình nhận dạng phải đặt ở phía service ASR, đọc từ biến môi trường.

**Service `asr`** (FastAPI, ~150 dòng):
- Endpoint `POST /v1/audio/transcriptions` (multipart: `file`, `model`), trả về `{"text": "..."}`.
- Thêm `GET /health`.
- Engine mặc định: **faster-whisper `large-v3`**, `compute_type=float16`.
  - CTranslate2 phải **≥4.8, bản build cho CUDA 12.8**. Bản cũ lỗi `CUBLAS_STATUS_NOT_SUPPORTED` trên sm_120 khi dùng INT8.
  - Nạp model một lần lúc khởi động và làm nóng bằng một file mẫu.
- Tham số nhận dạng (đặt qua biến môi trường):
  - `ASR_LANGUAGE=vi`: ép tiếng Việt. Câu trộn vẫn giữ nguyên từ tiếng Anh (chữ Latin) nhưng không bị dịch. **Phải so sánh với `auto`** (để model tự đoán ngôn ngữ).
  - `ASR_INITIAL_PROMPT`: câu mồi chứa từ vựng hay dùng, ví dụ "Jarvis, quản gia, open mask, close mask, mặt nạ, đèn mắt, combat mode, thưa ngài".
  - `beam_size=5`, `vad_filter=false` (xiaozhi-server đã cắt câu bằng SileroVAD), `condition_on_previous_text=false`.
- **Lọc ảo giác:** bỏ kết quả rỗng hoặc các câu Whisper hay bịa ra khi gặp khoảng lặng ("Cảm ơn các bạn đã xem", "Hãy subscribe…"). Danh sách để trong file cấu hình.
- Ghi log thời gian xử lý từng request.

**Engine phương án B** (cùng API, đổi bằng biến `ASR_ENGINE`):
- Qwen3-ASR-1.7B chạy qua vLLM (có hỗ trợ tiếng Việt, Apache-2.0).
- PhoWhisper-large (bản CT2): giỏi tiếng Việt thuần nhưng nhận từ tiếng Anh kém hơn.

## 2. Bộ dữ liệu thử và so sánh A/B
- Tự ghi **≥50 câu** bằng mic INMP441 thật (thu qua thiết bị, lấy file wav từ `output_dir` của xiaozhi-server), chia làm 3 nhóm:
  1. Lệnh điều khiển trộn ngôn ngữ: "open mask rồi bật đèn", "mở mặt nạ đi Jarvis"…
  2. Câu hỏi tiếng Việt thuần.
  3. Câu tiếng Anh thuần.
- Mỗi câu có kèm văn bản đúng. Script `tools/tests/asr/eval.py` tính **WER**, **tỷ lệ đúng từ khóa lệnh** và độ trễ.
- Các cấu hình cần so sánh:
  - large-v3 + `vi`
  - large-v3 + `auto`
  - large-v3 + `vi` + prompt
  - Qwen3-ASR
  - (tùy chọn) PhoWhisper

## 3. Công việc
- [ ] Viết `server/asr/app.py` + Dockerfile (CUDA 12.8, ctranslate2 ≥4.8)
- [ ] Viết lọc ảo giác, healthcheck, log thời gian
- [ ] Ghi bộ thử 50 câu, viết `eval.py`
- [ ] Chạy so sánh A/B, chọn cấu hình mặc định, ghi kết quả vào `tools/tests/asr/RESULTS.md`

## 4. Tiêu chí nghiệm thu
1. Từ khóa lệnh (mask, mặt nạ, đèn mắt, open, close…) nhận đúng ≥95% trên bộ thử.
2. WER chung ≤15% với câu trộn ngôn ngữ, ≤10% với câu tiếng Việt thuần.
3. Thời gian xử lý một câu 3 giây ≤300ms trên RTX 5090 (đã nạp sẵn model).
4. Gửi 30 giây im lặng hoặc tiếng ồn: trả về chuỗi rỗng, không bịa câu.
