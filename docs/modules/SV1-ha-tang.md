# SV1 – Hạ tầng server (GPU, Docker)

| | |
|---|---|
| **Mục tiêu** | Chuẩn bị máy Ubuntu + RTX 5090 để chạy các service AI bằng Docker Compose, chia GPU hợp lý, tự khởi động lại |
| **Phụ thuộc** | – |
| **Kết quả giao nộp** | `server/docker-compose.yml` (khung), `server/.env.example`, `server/README.md` (hướng dẫn cài) |

## 1. Cài đặt
- Ubuntu 24.04 LTS. (cập nhật khi triển khai: máy `batai` chạy Ubuntu 26.04.1, kernel 7.0, Docker Engine 29.)
- **NVIDIA driver ≥570**: RTX 5090 (Blackwell, sm_120) bắt buộc dùng driver và CUDA ≥12.8. (batai: driver 595.84 bản open, CUDA 13.2; image container dùng CUDA runtime 12.8/12.9, phải ≤ 13.2.)
- Docker Engine + Compose plugin + `nvidia-container-toolkit`. Kiểm tra: `docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi`.
  (cập nhật khi triển khai: dockerd trên batai khởi động trước khi có hook NVIDIA và đang chạy container của dự án khác, nên GPU
  đi qua **CDI**, không restart dockerd – D20. Kiểm tra: `docker run --rm --device nvidia.com/gpu=all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi -L`
  hoặc `bash server/scripts/gpu_check.sh`.)
- Đặt IP LAN tĩnh cho server, ví dụ `192.168.1.10`, bằng DHCP reservation trên router. (batai: `192.168.1.27`, hiện vẫn lấy qua DHCP – cần đặt reservation.)

## 2. Khung `docker-compose.yml`

| Service | Image / build | Cổng (chỉ mở trong mạng Docker, trừ khi ghi khác) | GPU |
|---|---|---|---|
| `xiaozhi` | `server/xiaozhi/Dockerfile` (FROM image chính thức v0.9.7, ghim digest) | 8000 (WS), 8003 (OTA): **mở ra LAN** qua cổng host **8100** → 8000 và 8003 → 8003 (cập nhật khi triển khai: cổng 8000 của host thuộc dự án khác, D21) | Không |
| `asr` | `server/asr/Dockerfile` | 9000 | Có |
| `ollama` | `ollama/ollama:0.35.1` (hỗ trợ sm_120) | 11434, **không bao giờ** mở ra host (host đã có Ollama riêng ở 11434) | Có |
| `litellm` | `ghcr.io/berriai/litellm:v1.104.0` | 4000 | Không |
| `redis` | `redis:7.4.11-alpine` (thêm khi triển khai: lưu chi phí cloud của LiteLLM, D25) | 6379 | Không |
| `tts-router` + `vieneu` | router tự viết `server/tts/router` + VieNeu-TTS build từ tag `app-v0.18.9` (cập nhật khi triển khai: service `tts` tách làm hai, D18) | 8880 (router), 8000 (vieneu) | vieneu: Có |
| `kokoro`, `asr-qwen` | tùy chọn: profile `english-tts` (SV4), profile `asr-qwen` (SV2) | 8880 / 8000 | Có |
| `cloudflared` | `cloudflare/cloudflared:2026.10.0`, profile `tunnel` | – | Không |

- Tất cả: `restart: unless-stopped`, có healthcheck, log xoay vòng (`max-size: 20m`, `max-file: 3`). Mọi image bên thứ ba ghim digest.
- Model lưu trong volume riêng (`ollama_models`, `hf_cache`) để không phải tải lại khi build lại image. (Thêm khi triển khai:
  `vieneu_hf_cache`, `asr_cuda_cache`, `asr_qwen_hf_cache` – ASR chạy uid 10001, VieNeu uid 1000, nên mỗi service một volume cache.)
- Biến bí mật (API key cloud, token tunnel, token thiết bị) để trong `server/.env`, **không commit**. Chỉ commit `.env.example`.
  (cập nhật khi triển khai: compose không nạp nguyên `.env` vào container nào; mỗi service chỉ nhận các biến liệt kê cho nó –
  khóa Anthropic chỉ vào `litellm`, `TUNNEL_TOKEN` chỉ vào `cloudflared`.)

## 3. Ngân sách VRAM (32GB)

| Thành phần | VRAM ước tính (kế hoạch) | Ước tính khi triển khai (chưa đo trên RTX 5090) |
|---|---|---|
| LLM MoE ~30–35B-A3B lượng tử 4-bit (SV3) | 18–22 GB | `qwen3:30b-a3b-instruct-2507-q4_K_M`, ctx 8192, KV f16: ≈ 20–21 GB |
| faster-whisper large-v3 float16 (SV2) | 3–5 GB | ≈ 4.5 GB (`int8_float16` ≈ 2.9 GB) |
| VieNeu-TTS v3 Turbo (SV4) | 1–2 GB | ≈ 1.0–1.3 GB (4 luồng) |
| Dự phòng (context, đỉnh tạm thời) | 3–5 GB | tổng mặc định ≈ 26–27 GB |
| Tùy chọn | – | Kokoro + ≈ 3.1 GB; Qwen3-ASR (`--gpu-memory-utilization 0.2`) + ≈ 6.4 GB |

Ollama: đặt `OLLAMA_KEEP_ALIVE=-1` để model luôn nằm trên GPU (tránh mất vài giây nạp lại model), `OLLAMA_NUM_PARALLEL=1`.
(Thêm khi triển khai: `OLLAMA_CONTEXT_LENGTH=8192`, `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=f16`, `OLLAMA_MAX_LOADED_MODELS=1`.)

> **GPU của batai đang dùng chung.** Ghi nhận ngày 2026-09-12: một vLLM ≈ 8.7 GiB, Ollama của host ≈ 5.8 GiB, một job huấn luyện
> ≈ 7.8 GiB. Khi đó stack Jarvis (≈ 26–27 GB) **không đủ chỗ**: phải dừng/chuyển các tiến trình đó hoặc giảm
> (`gemma4:26b-a4b-it-qat`, `ASR_COMPUTE_TYPE=int8_float16`, `OLLAMA_KV_CACHE_TYPE=q8_0`). Kiểm tra `nvidia-smi` trước khi `docker compose up`.

## 4. Công việc
- [ ] Cài driver, Docker, nvidia-container-toolkit; kiểm tra `nvidia-smi` trong container (batai đã có driver + Docker 29; script `bootstrap_host.sh` sẵn sàng, chưa chạy)
- [x] Viết khung compose với healthcheck, volume, `.env.example`
- [x] Viết `server/README.md`: cài từ đầu, khởi động, xem log, cập nhật
- [ ] Đặt IP tĩnh; mở firewall (ufw) chỉ cho cổng 8000 và 8003 từ mạng LAN (cập nhật khi triển khai: cổng 8100/8003; **không** bật ufw, xem Ghi chú triển khai)

## 5. Tiêu chí nghiệm thu
1. `docker compose up -d`: mọi service lên trạng thái healthy trong vòng 3 phút (sau lần tải model đầu tiên).
2. Khởi động lại máy: các service tự chạy lại, không cần làm gì thêm.
3. `nvidia-smi` cho thấy tổng VRAM dùng ≤28GB khi cả 3 model đang nạp.
4. Từ máy khác trong LAN chỉ truy cập được cổng 8000 và 8003. (cập nhật khi triển khai: cổng **8100** (WS) và 8003 (OTA), D21.)

## Ghi chú triển khai (10/2026)

**Trạng thái:** phần mềm xong, đã kiểm chứng trên máy dev (Docker Desktop, GTX 1660 SUPER 6 GB). **Chưa chạy gì trên batai.**

**File chính:**

| File | Nội dung |
|---|---|
| [server/docker-compose.yml](../../server/docker-compose.yml) | Stack production (project `jarvis`), GPU qua CDI |
| [server/docker-compose.local.yml](../../server/docker-compose.local.yml) | Máy dev Docker Desktop: GPU qua driver `nvidia`, LLM nhỏ trên Ollama của máy, LiteLLM `config.local.yaml` |
| [server/docker-compose.debug.yml](../../server/docker-compose.debug.yml) | Mở ASR / LiteLLM / tts-router trên `127.0.0.1` để thử bằng curl |
| [server/.env.example](../../server/.env.example) | Mẫu biến môi trường, có chú thích từng biến |
| [server/README.md](../../server/README.md) | Cài lần đầu, triển khai, vận hành, sao lưu, cổng, firewall, bảo mật, sự cố |
| [server/scripts/](../../server/scripts/) | `bootstrap_host.sh`, `deploy.sh`, `gpu_check.sh`, `pull_models.sh`, `firewall_lan.sh`, `vram_report.sh` |
| [server/ollama/](../../server/ollama/) | README chọn model, `entrypoint.sh` nạp sẵn model |

**Sai khác so với kế hoạch:**

| Kế hoạch | Thực tế | Lý do |
|---|---|---|
| Ubuntu 24.04, `--gpus all` | Ubuntu 26.04.1; compose xin GPU bằng `driver: cdi`, `device_ids: ["nvidia.com/gpu=all"]`; `bootstrap_host.sh --apply` sinh CDI spec (`nvidia-cdi-refresh` hoặc `nvidia-ctk cdi generate`) mà không restart dockerd | D20 |
| xiaozhi mở 8000/8003 | Host `XIAOZHI_WS_PORT` 8100 → 8000, `XIAOZHI_OTA_PORT` 8003 → 8003; `XIAOZHI_BIND_IP` để chỉ bind card LAN | D21: 8000 của host thuộc dự án khác |
| ufw chỉ mở 8000/8003 cho LAN | Không bật ufw (cổng Docker publish đi vòng qua ufw). Router không port-forward; `XIAOZHI_BIND_IP`; luật chuỗi `DOCKER-USER` bằng `scripts/firewall_lan.sh` (chỉ đụng luật nhãn `jarvis-lan`, `--persist` qua crontab `@reboot`) | Máy dùng chung; ufw không chặn được cổng Docker |
| Một service `tts` | `tts-router` + `vieneu`; thêm `redis`, `kokoro`, `asr-qwen` | D18, D25, D19 |
| `.env` cho mọi service | Mỗi service một danh sách biến; test đối chiếu danh sách với mã nguồn | Service hướng ra mạng (xiaozhi) không thấy khóa cloud / token tunnel |
| – | `ollama/entrypoint.sh` nạp sẵn `OLLAMA_MODEL` sau mỗi lần khởi động | Câu đầu sau reboot không bị đẩy sang cloud (chậm, tốn tiền) |
| – | `deploy.sh` chép `server/` lên `batai:/opt/jarvis` (rsync / tar / `git archive`), không bao giờ chép đè `.env` | Triển khai lặp lại được từ PC, mặc định chỉ chạy thử |

**Tiêu chí nghiệm thu:**

| # | Đã kiểm chứng bằng phần mềm | Còn chờ |
|---|---|---|
| 1 | Mọi service có healthcheck (`start_period` 900 s cho lần đầu tải model ASR/VieNeu). Stack đầy đủ `redis`, `litellm`, `asr`, `vieneu`, `tts-router`, `xiaozhi` lên healthy trên máy dev (model nhỏ) | Chờ batai: đo thời gian với model thật |
| 2 | `restart: unless-stopped`; `entrypoint.sh` chạy thử trong image `ollama/ollama:0.35.1` (nạp model nhỏ trong 11 s, `ollama ps` báo `UNTIL Forever`) | Chờ batai: reboot thật |
| 3 | Ước tính 26–27 GB; `scripts/vram_report.sh` đối chiếu theo container | Chờ batai: đo thật; rủi ro GPU dùng chung |
| 4 | `server/scripts/tests`: chỉ `xiaozhi` publish cổng, file debug chỉ bind `127.0.0.1`, `docker compose config` hợp lệ cho mọi biến thể (base, profile, local, debug) | Chờ batai: quét cổng từ máy khác trong LAN và từ 4G |

**Cách kiểm tra:**

```bash
.venv/Scripts/python -m pytest server/scripts/tests -q        # trên PC: script, compose, biến môi trường, bí mật
# Trên batai:
bash /opt/jarvis/server/scripts/bootstrap_host.sh               # dry-run, rồi --apply
bash /opt/jarvis/server/scripts/gpu_check.sh                    # GPU qua CDI
cd /opt/jarvis/server && docker compose up -d && docker compose ps
bash scripts/vram_report.sh --budget-gb 28 --strict             # tiêu chí 3
curl -m 3 http://<IP-public>:8100/                              # từ 4G: phải thất bại (tiêu chí 4)
```
