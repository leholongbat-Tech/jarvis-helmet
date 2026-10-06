# Service ASR (nhận dạng giọng nói)

Service `asr` trong `server/docker-compose.yml`: FastAPI trên cổng nội bộ `9000`, image `jarvis/asr:dev`
(CUDA 12.8, `faster-whisper==1.2.1`, `ctranslate2==4.8.2`). xiaozhi gọi nó qua provider `OpenaiASR`
(`base_url: http://asr:9000/v1/audio/transcriptions`) với một file WAV 16 kHz cho mỗi câu nói.

Công cụ đánh giá (WER, độ chính xác từ khóa, độ trễ) và cách thu bộ dữ liệu thật:
`tools/tests/asr/README.md`. Cấu hình chung của cả stack: `server/README.md`.

## API

| Đường dẫn | Mô tả |
|---|---|
| `POST /v1/audio/transcriptions` | multipart: `file` (WAV) bắt buộc, `model` (bị bỏ qua). Tùy chọn để thử trực tiếp: `language` (`vi`, `en`, `auto`, `vi,en`), `prompt` (`none` = tắt câu mồi), `hotwords`, `response_format` (`json` mặc định, `text`, `verbose_json`). Trả về `{"text": "..."}` |
| `GET /health` | `{"status": "ok", "engine", "model", "device", ...}`; mã 503 khi model chưa nạp xong |

xiaozhi chỉ gửi `file` + `model` và không đặt timeout, nên mọi thông số nhận dạng đến từ biến môi trường
bên dưới. Mỗi request ghi một dòng log `[ASR] rid=... audio_ms=... infer_ms=... total_ms=... text="..."`.

## Engine

- `faster_whisper` (mặc định): `ASR_MODEL=large-v3` (ghim revision sẵn), `large-v3-turbo`, hoặc id
  HuggingFace của một model CTranslate2 (PhoWhisper: `qbsmlabs/PhoWhisper-large`, cũng đã ghim revision).
- `qwen3_vllm`: chuyển tiếp tới sidecar vLLM `asr-qwen` (`docker compose --profile asr-qwen up -d`,
  `ASR_VLLM_URL`, mặc định `http://asr-qwen:8000`). Khi đó faster-whisper không nạp model.
- `fake`: chỉ dùng cho kiểm thử.

Trước khi gọi model, "cổng âm thanh" (RMS + Silero VAD) trả về chuỗi rỗng cho đoạn im lặng/tiếng ồn;
sau khi nhận dạng, bộ lọc ảo giác bỏ các câu kiểu "Thanks for watching!" (`hallucinations.txt`),
đoạn có logprob thấp, lặp vòng, chữ CJK hoặc chép lại câu mồi.

## Biến môi trường

Để trống = giá trị mặc định trong `jarvis_asr/config.py` (nguồn chính xác nhất).

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `ASR_ENGINE` | `faster_whisper` | `faster_whisper` \| `qwen3_vllm` \| `fake` |
| `ASR_MODEL` | `large-v3` | alias hoặc id HuggingFace / thư mục model |
| `ASR_MODEL_REVISION` | revision ghim của alias | commit HuggingFace; để trống khi đổi sang model khác |
| `ASR_DEVICE` | `cuda` | `cuda` \| `cpu` \| `auto` |
| `ASR_DEVICE_INDEX` | `0` | chỉ số GPU |
| `ASR_COMPUTE_TYPE` | `auto` | `auto` = `float16` trên GPU, `int8` trên CPU; `int8_float16` tiết kiệm VRAM |
| `ASR_CPU_THREADS` | `0` | số luồng CPU (0 = tự chọn) |
| `ASR_LANGUAGE` | `vi` | `vi`, `en`, `auto`, hoặc `vi,en` (tự nhận nhưng chỉ trong danh sách) |
| `ASR_INITIAL_PROMPT` | (không) | câu mồi từ vựng; `none` = tắt |
| `ASR_HOTWORDS` | (không) | hotwords của faster-whisper |
| `ASR_BEAM_SIZE` | `5` | 1–16 |
| `ASR_MAX_TOKENS_PER_S` | `30` | giới hạn token theo độ dài audio (chặn vòng lặp); `off` = tắt |
| `ASR_LOCAL_FILES_ONLY` | `false` | `true` = không truy cập HuggingFace (model phải có sẵn trong cache) |
| `ASR_HALLUCINATIONS_FILE` | `/app/hallucinations.txt` | danh sách câu ảo giác (tự nạp lại khi file đổi) |
| `ASR_FILTER` | `true` | bật/tắt bộ lọc ảo giác |
| `ASR_FILTER_LOGPROB_MIN` | `-1.0` | bỏ đoạn có avg logprob thấp hơn (`off` = tắt) |
| `ASR_FILTER_NO_SPEECH_MAX` | `0.6` | bỏ đoạn có no_speech_prob cao hơn ... |
| `ASR_FILTER_NO_SPEECH_LOGPROB` | `-0.5` | ... khi logprob cũng thấp hơn ngưỡng này |
| `ASR_FILTER_COMPRESSION_MAX` | `2.4` | bỏ đoạn lặp nhiều (`off` = tắt) |
| `ASR_FILTER_REPEAT_MIN` | `4` | số lần lặp liên tiếp của một cụm từ = vòng lặp |
| `ASR_FILTER_DROP_CJK` | `true` | bỏ kết quả chứa chữ Trung/Nhật/Hàn |
| `ASR_FILTER_PROMPT_ECHO_WORDS` | `4` | số từ liên tiếp chép từ câu mồi thì coi là ảo giác (0 = tắt) |
| `ASR_MIN_RMS_DBFS` | `-60` | audio nhỏ hơn mức này = im lặng (`off` = tắt) |
| `ASR_VAD_GATE` | `true` | dùng Silero VAD để bỏ đoạn không có tiếng nói |
| `ASR_VAD_THRESHOLD` | `0.5` | ngưỡng VAD 0–1 |
| `ASR_VAD_MIN_SPEECH_MS` | `250` | tối thiểu bao nhiêu ms tiếng nói |
| `ASR_MAX_AUDIO_S` | `120` | độ dài audio tối đa |
| `ASR_MAX_UPLOAD_MB` | `50` | kích thước upload tối đa (413 nếu vượt) |
| `ASR_REQUEST_TIMEOUT_S` | `60` | quá thời gian -> 504 (0 = không giới hạn) |
| `ASR_SAVE_DIR` | (tắt) | thu dữ liệu: wav + `transcripts.jsonl`; trong compose dùng `/data/asr-dataset` |
| `ASR_VLLM_URL` / `ASR_VLLM_TIMEOUT_S` / `ASR_VLLM_WAIT_S` | `http://asr-qwen:8000` (compose) / `30` / `300` | engine `qwen3_vllm` |
| `ASR_WARMUP` | `1` | số lần chạy nóng khi khởi động (0 = tắt) |
| `ASR_LOG_TEXT` | `true` | ghi văn bản nhận được vào log |
| `ASR_LOG_LEVEL` | `INFO` | mức log |
| `HF_TOKEN` | (không) | chỉ cần với model bị giới hạn truy cập |

Biến nào muốn đặt trong `server/.env` thì phải có tên trong phần `environment` của service `asr`
trong `docker-compose.yml` (compose không nạp nguyên file `.env` vào container).

## Volume và quyền ghi

Image chạy bằng user `asr` (uid 10001) và tạo sẵn các thư mục thuộc user này. Compose mount:
`hf_cache` -> `/cache/hf` (model HuggingFace), `asr_cuda_cache` -> `/cache/nv` (cache JIT CUDA),
`asr_dataset` -> `/data` (dữ liệu thu được). Volume mới tạo sẽ nhận quyền của thư mục trong image;
nếu mount vào đường dẫn khác thì phải `chown 10001` thư mục đó, nếu không sẽ lỗi "Permission denied".

## Lệnh hay dùng

```bash
# Tải trước model vào volume (không nạp lên GPU)
docker compose run --rm --no-deps asr python -m jarvis_asr.prefetch

# Thử nhanh khi chạy kèm docker-compose.debug.yml (cổng 127.0.0.1:9000)
curl -s http://127.0.0.1:9000/health
curl -s -F "file=@cau_noi.wav" -F model=whisper-large-v3 http://127.0.0.1:9000/v1/audio/transcriptions

# Kiểm thử trên máy dev (engine giả lập)
.venv/Scripts/python -m pytest server/tests/test_asr_*.py -q

# Kiểm thử trong image (faster-whisper và Silero VAD thật); Git Bash: thêm MSYS_NO_PATHCONV=1
docker run --rm --user root --entrypoint sh -v "$PWD/server:/repo/server:ro" \
  -v "$PWD/tools/tests/asr:/repo/tools/tests/asr:ro" jarvis/asr:dev \
  -c "pip install -q pytest requests && cd /tmp && python -m pytest -q -p no:cacheprovider /repo/server/tests/test_asr_*.py"
```

## Đã kiểm chứng / chưa kiểm chứng

- Máy dev (Docker Desktop, GTX 1660 SUPER 6 GB): model `small`, `int8_float16`, nạp 2 s, mỗi câu 2–3 s
  mất khoảng 0,3–0,7 s; câu tiếng Việt do VieNeu đọc được nhận đúng ("Mở mặt nạ.", "Mấy giờ rồi?").
  207 test chạy trong image đều đạt.
- Chưa đo trên máy chủ thật (RTX 5090, `large-v3` float16): tiêu chí độ trễ và WER của SV2 phải đo
  bằng bộ thu âm thật theo `tools/tests/asr/README.md`.
