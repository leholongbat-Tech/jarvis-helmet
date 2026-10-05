# SV1 – Hạ tầng server (GPU, Docker)

| | |
|---|---|
| **Mục tiêu** | Chuẩn bị máy Ubuntu + RTX 5090 để chạy các service AI bằng Docker Compose, chia GPU hợp lý, tự khởi động lại |
| **Phụ thuộc** | – |
| **Kết quả giao nộp** | `server/docker-compose.yml` (khung), `server/.env.example`, `server/README.md` (hướng dẫn cài) |

## 1. Cài đặt
- Ubuntu 24.04 LTS.
- **NVIDIA driver ≥570**: RTX 5090 (Blackwell, sm_120) bắt buộc dùng driver và CUDA ≥12.8.
- Docker Engine + Compose plugin + `nvidia-container-toolkit`. Kiểm tra: `docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi`.
- Đặt IP LAN tĩnh cho server, ví dụ `192.168.1.10`, bằng DHCP reservation trên router.

## 2. Khung `docker-compose.yml`

| Service | Image / build | Cổng (chỉ mở trong mạng Docker, trừ khi ghi khác) | GPU |
|---|---|---|---|
| `xiaozhi` | `server/xiaozhi/Dockerfile` (FROM image chính thức v0.9.7) | 8000 (WS), 8003 (OTA): **mở ra LAN** | Không |
| `asr` | `server/asr/Dockerfile` | 9000 | Có |
| `ollama` | `ollama/ollama` (bản hỗ trợ sm_120) | 11434 | Có |
| `litellm` | `ghcr.io/berriai/litellm` | 4000 | Không |
| `tts` | VieNeu-TTS (image Docker của dự án gốc) | 8880 | Có |
| `cloudflared` | `cloudflare/cloudflared` | – | Không |

- Tất cả: `restart: unless-stopped`, có healthcheck, log xoay vòng (`max-size: 20m`).
- Model lưu trong volume riêng (`ollama_models`, `hf_cache`) để không phải tải lại khi build lại image.
- Biến bí mật (API key cloud, token tunnel, token thiết bị) để trong `server/.env`, **không commit**. Chỉ commit `.env.example`.

## 3. Ngân sách VRAM (32GB)

| Thành phần | VRAM ước tính |
|---|---|
| LLM MoE ~30–35B-A3B lượng tử 4-bit (SV3) | 18–22 GB |
| faster-whisper large-v3 float16 (SV2) | 3–5 GB |
| VieNeu-TTS v3 Turbo (SV4) | 1–2 GB |
| Dự phòng (context, đỉnh tạm thời) | 3–5 GB |

Ollama: đặt `OLLAMA_KEEP_ALIVE=-1` để model luôn nằm trên GPU (tránh mất vài giây nạp lại model), `OLLAMA_NUM_PARALLEL=1`.

## 4. Công việc
- [ ] Cài driver, Docker, nvidia-container-toolkit; kiểm tra `nvidia-smi` trong container
- [ ] Viết khung compose với healthcheck, volume, `.env.example`
- [ ] Viết `server/README.md`: cài từ đầu, khởi động, xem log, cập nhật
- [ ] Đặt IP tĩnh; mở firewall (ufw) chỉ cho cổng 8000 và 8003 từ mạng LAN

## 5. Tiêu chí nghiệm thu
1. `docker compose up -d`: mọi service lên trạng thái healthy trong vòng 3 phút (sau lần tải model đầu tiên).
2. Khởi động lại máy: các service tự chạy lại, không cần làm gì thêm.
3. `nvidia-smi` cho thấy tổng VRAM dùng ≤28GB khi cả 3 model đang nạp.
4. Từ máy khác trong LAN chỉ truy cập được cổng 8000 và 8003.
