# Ollama – LLM local của Jarvis

Container `ollama` (image `ollama/ollama:0.35.1`, ghim digest trong `docker-compose.yml`) chạy model
ngôn ngữ local. LiteLLM gọi nó qua nhóm model `jarvis-main` (`ollama_chat/<OLLAMA_MODEL>`,
`http://ollama:11434`). Khi model local lỗi hoặc quá thời gian, LiteLLM tự chuyển sang `jarvis-cloud`
(Claude), xem `server/README.md`.

- **Không mở cổng 11434 ra máy host**: máy `batai` đã có một Ollama riêng chạy ở cổng đó. Muốn gõ lệnh
  thì dùng `docker compose exec ollama ollama ...`.
- GPU qua CDI (`nvidia.com/gpu=all`), không cần khởi động lại dockerd.
- Model lưu trong volume `ollama_models` (`/root/.ollama`), build lại image không mất model.

## 1. Chọn model

| Tag Ollama | Dung lượng | VRAM ước tính (ctx 8k) | Ghi chú |
|---|---|---|---|
| **`qwen3:30b-a3b-instruct-2507-q4_K_M`** (mặc định) | 19 GB | ≈ 20–21 GB | MoE 30.5B, ~3.3B tham số chạy mỗi token → nhanh. Bản *instruct* không có chế độ "thinking" → độ trễ thấp. Gọi tool tốt. |
| `gemma4:26b-a4b-it-qat` | 16 GB | ≈ 17.5–18 GB | Phương án dự phòng khi thiếu VRAM. MoE ~4B active, có vision/thinking (LiteLLM đã gửi `think: false`). |
| `qwen3.6:35b-a3b-q4_K_M` | 24 GB | ≈ 25–26 GB | Chất lượng cao nhất, **chỉ dùng khi đủ VRAM**: cùng ASR (≈ 4.5 GB) + VieNeu (≈ 1–1.3 GB) là ≈ 31–32 GB, không vừa RTX 5090 32 GB nếu còn tiến trình khác dùng GPU. Mặc định có thinking (đã tắt bằng `think: false`). |

Tiêu chí chọn: tiếng Việt tốt, gọi tool ổn định, vừa ngân sách VRAM. Không có điểm benchmark tiếng Việt
chính thức cho các model này: hãy chạy bộ thử gọi tool (`tools/tests/llm`, xem README trong thư mục đó)
với từng model trước khi đổi hẳn. Mục tiêu SV3: ≥ 95 % câu ra lệnh đúng tool và đúng tham số,
10/10 câu không gọi tool.

Các con số VRAM là ước tính (trọng số + KV cache f16 ở 8192 token + bộ đệm tính toán), cần đo lại trên
máy thật bằng `scripts/vram_report.sh` và `docker compose exec ollama ollama ps`.

## 2. Biến môi trường (đặt trong `docker-compose.yml`)

| Biến | Giá trị | Vì sao |
|---|---|---|
| `OLLAMA_MODEL` (trong `server/.env`) | `qwen3:30b-a3b-instruct-2507-q4_K_M` | model của `jarvis-main`; cũng là model được nạp sẵn |
| `OLLAMA_KEEP_ALIVE` | `-1` | giữ model trên GPU mãi, tránh mất vài giây nạp lại |
| `OLLAMA_CONTEXT_LENGTH` | `8192` | mặc định của Ollama trên GPU 24–48 GiB là 32k → tốn VRAM vô ích; prompt + tool của xiaozhi khoảng 5k token |
| `OLLAMA_NUM_PARALLEL` | `1` | một người dùng; VRAM tăng theo `NUM_PARALLEL × CONTEXT_LENGTH` |
| `OLLAMA_FLASH_ATTENTION` | `1` | giảm VRAM, cần cho KV cache lượng tử |
| `OLLAMA_KV_CACHE_TYPE` | `f16` | đổi sang `q8_0` (≈ ½ KV) nếu thiếu VRAM |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | không để model thứ hai chiếm VRAM |
| `OLLAMA_NO_CLOUD` | `1` | chỉ chạy local |
| `OLLAMA_VULKAN` | `0` | không dùng backend Vulkan |
| `JARVIS_PRELOAD_MODEL` | `= OLLAMA_MODEL` | model mà `entrypoint.sh` nạp sẵn khi container khởi động |

Các biến tùy chọn `OLLAMA_CONTEXT_LENGTH`, `OLLAMA_NUM_PARALLEL`, `OLLAMA_FLASH_ATTENTION`,
`OLLAMA_KV_CACHE_TYPE` có thể ghi đè trong `server/.env` (xem `.env.example`).

**Không** đặt `num_ctx` trong `litellm/config.yaml`: nếu một request xin `num_ctx` khác
`OLLAMA_CONTEXT_LENGTH`, Ollama sẽ nạp lại model (mất vài giây, request đó bị chuyển sang cloud).

## 3. Nạp sẵn model (`entrypoint.sh`)

Ollama chỉ nạp model khi có request đầu tiên. Nạp 19 GB lên GPU mất vài giây, đủ để LiteLLM hết
`stream_timeout` (10 s) và đẩy câu hỏi đầu tiên sau khi khởi động lại máy sang cloud (tốn tiền, chậm).
Vì vậy `entrypoint.sh` (gắn read-only vào container) chạy `ollama serve` rồi nạp sẵn `OLLAMA_MODEL`
ngay khi server sẵn sàng:

```bash
docker compose logs ollama | grep jarvis-preload
#   jarvis-preload: loading 'qwen3:30b-a3b-instruct-2507-q4_K_M' ...
#   jarvis-preload: 'qwen3:30b-a3b-instruct-2507-q4_K_M' loaded in 7s
docker compose exec ollama ollama ps      # SIZE ~20 GB, PROCESSOR "100% GPU", CONTEXT 8192, UNTIL "Forever"
```

- Model chưa tải về → dòng log `not pulled yet (run scripts/pull_models.sh)`, không làm gì thêm.
- Tắt nạp sẵn: thêm `JARVIS_PRELOAD: "0"` vào `environment` của service `ollama` (file override riêng).

## 4. Tải model

```bash
cd /opt/jarvis/server
bash scripts/pull_models.sh                 # dry-run: in ra các lệnh sẽ chạy
bash scripts/pull_models.sh --apply         # tải OLLAMA_MODEL, nạp sẵn, in `ollama ps`
bash scripts/pull_models.sh --apply --model gemma4:26b-a4b-it-qat   # tải thêm một model khác (không nạp)
```

Lệnh tay tương đương:

```bash
docker compose exec ollama ollama pull qwen3:30b-a3b-instruct-2507-q4_K_M
docker compose exec ollama ollama run qwen3:30b-a3b-instruct-2507-q4_K_M ""   # prompt rỗng = chỉ nạp
docker compose exec ollama ollama list
docker compose exec ollama ollama show qwen3:30b-a3b-instruct-2507-q4_K_M     # tham số, khả năng (tools, thinking)
```

## 5. Đổi model

1. Sửa `OLLAMA_MODEL=` trong `server/.env`.
2. `bash scripts/pull_models.sh --apply` (tải + nạp model mới).
3. `docker compose up -d ollama litellm`: cả hai container đọc `OLLAMA_MODEL` (Ollama để nạp sẵn,
   LiteLLM cho `jarvis-main`) nên đều được tạo lại.
4. Chạy bộ thử gọi tool (`tools/tests/llm/README.md`) và so sánh với model cũ.
5. Xóa model cũ để lấy lại ổ đĩa: `docker compose exec ollama ollama rm <tag cũ>`.

Model có chế độ thinking (Qwen3.6, Gemma 4): LiteLLM đã gửi `think: false` ở cấp cao nhất của
`/api/chat` (`litellm_params.think`). Qwen3.6 không hỗ trợ công tắc `/think` `/nothink` trong prompt.

## 6. Dùng Ollama có sẵn trên máy host (không khuyến nghị)

Có thể trỏ `jarvis-main` sang Ollama của host bằng `OLLAMA_API_BASE=http://host.docker.internal:11434`
trong `.env`, kèm một file override thêm `extra_hosts: ["host.docker.internal:host-gateway"]` cho service
`litellm` (như `docker-compose.local.yml`). Nhược điểm: không kiểm soát được biến môi trường, model và
VRAM của Ollama đó (nó thuộc dự án khác), nên mặc định Jarvis dùng container riêng.

## 7. Sự cố thường gặp

| Hiện tượng | Nguyên nhân / cách xử lý |
|---|---|
| Câu đầu tiên sau khi khởi động luôn do cloud trả lời | model chưa được nạp: xem log `jarvis-preload`, chạy `scripts/pull_models.sh --apply` |
| `ollama ps` báo `PROCESSOR 40%/60% CPU/GPU` | thiếu VRAM: kiểm tra `scripts/vram_report.sh` (tiến trình khác trên GPU?), giảm `OLLAMA_CONTEXT_LENGTH`, đặt `OLLAMA_KV_CACHE_TYPE=q8_0` hoặc dùng `gemma4:26b-a4b-it-qat` |
| LiteLLM log `fallback` với `Timeout` liên tục | model quá chậm (CPU offload) hoặc GPU bị chiếm; hai lần timeout liên tiếp → LiteLLM bỏ qua model local 30 s |
| `model ... not found` trong log LiteLLM | `OLLAMA_MODEL` chưa được tải hoặc gõ sai tag |
| Container `ollama` không lên, lỗi CDI | xem `scripts/gpu_check.sh` và mục GPU trong `server/README.md` |
