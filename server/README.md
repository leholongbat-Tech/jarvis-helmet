# Jarvis Helmet – server (xiaozhi-esp32-server + AI chạy local)

Toàn bộ phần server của mũ: nhận giọng nói từ mũ, nhận dạng (ASR), hỏi LLM (local, dự phòng bằng
Claude), đọc câu trả lời (TTS) và điều khiển mũ qua MCP. Chạy bằng Docker Compose trên máy `batai`
(Ubuntu 26.04 + RTX 5090), dự án compose tên `jarvis`.

**Chạy nhanh** (chi tiết ở các mục sau):

```bash
# Trên PC (Git Bash / Linux), lần đầu:
ssh -t batai 'sudo install -d -m 0750 -o "$USER" -g "$USER" /opt/jarvis'
bash server/scripts/deploy.sh --copy-only            # xem trước (dry-run)
bash server/scripts/deploy.sh --apply --copy-only    # chép server/ lên batai:/opt/jarvis
# Trên batai:
bash /opt/jarvis/server/scripts/bootstrap_host.sh            # xem trước
bash /opt/jarvis/server/scripts/bootstrap_host.sh --apply    # docker group, CDI GPU, kiểm tra GPU
cd /opt/jarvis/server && cp .env.example .env && chmod 600 .env && nano .env
docker compose build && bash scripts/pull_models.sh --apply --asr   # build + tải trước model (lâu)
docker compose up -d && docker compose ps            # mọi service (healthy)
```

## Mục lục

1. [Kiến trúc](#1-kiến-trúc)
2. [Máy chủ và ngân sách VRAM](#2-máy-chủ-và-ngân-sách-vram)
3. [Cài đặt lần đầu trên batai](#3-cài-đặt-lần-đầu-trên-batai)
4. [Triển khai và cập nhật](#4-triển-khai-và-cập-nhật)
5. [Vận hành, log](#5-vận-hành-log)
6. [Sao lưu](#6-sao-lưu)
7. [Cổng mạng](#7-cổng-mạng)
8. [Firewall](#8-firewall)
9. [Bảo mật](#9-bảo-mật)
10. [Đổi nhà cung cấp / model cloud](#10-đổi-nhà-cung-cấp--model-cloud)
11. [Thử trên máy dev (Docker Desktop)](#11-thử-trên-máy-dev-docker-desktop)
12. [Kiểm thử và những gì đã xác minh](#12-kiểm-thử-và-những-gì-đã-xác-minh)
13. [Sự cố thường gặp](#13-sự-cố-thường-gặp)

## 1. Kiến trúc

```
                 LAN: ws://<IP>:8100/xiaozhi/v1/ , http://<IP>:8003/xiaozhi/ota/
 Mũ ESP32-S3 ────────────────────────────────────────────────────┐
     │  WAN: wss/https://jarvis.<domain> (Cloudflare Access)      │
     └──► Cloudflare ──► cloudflared (profile tunnel) ───────────┤
                                                                  ▼
                                       xiaozhi  (WebSocket 8000, OTA 8003)   [CPU]
                        ┌──────────────────┬───────────┴───────────┬──────────────────┐
                        ▼                  ▼                       ▼
                  asr :9000          litellm :4000 ── redis    tts-router :8880      [CPU]
               faster-whisper        │         │                   │
                 [GPU]               ▼         ▼                   ▼
          (asr-qwen :8000,     ollama :11434  Claude API      vieneu :8000  [GPU]
           profile asr-qwen)    [GPU]         (jarvis-cloud)  (kokoro :8880, profile english-tts)
```

Một lượt nói: mũ gửi Opus 16 kHz → xiaozhi (VAD) → `asr` trả văn bản → LiteLLM `jarvis-main` (Ollama,
có tool: lệnh MCP của mũ, các tác vụ `task_*`, `ask_expert`) → câu trả lời được tách câu và đưa tới
`tts-router` (chuẩn hóa tiếng Việt, chọn engine) → `vieneu` sinh PCM 24 kHz dạng stream → xiaozhi mã hóa
Opus gửi về mũ. Lệnh điều khiển (mở mặt nạ, đèn mắt…) đi ngược về mũ qua MCP trên cùng WebSocket.

| Service | Image / build | Cổng trong | Cổng host | GPU | Vai trò |
|---|---|---|---|---|---|
| `xiaozhi` | build `xiaozhi/Dockerfile` (FROM xiaozhi-esp32-server 0.9.7) | 8000, 8003 | **8100**, **8003** | – | server hội thoại, OTA |
| `asr` | build `asr/` | 9000 | – | có | faster-whisper large-v3 |
| `ollama` | `ollama/ollama:0.35.1` | 11434 | – | có | LLM local (`ollama/README.md`) |
| `litellm` | `ghcr.io/berriai/litellm:v1.104.0` | 4000 | – | – | định tuyến LLM, dự phòng cloud, ngân sách |
| `redis` | `redis:7.4.11-alpine` | 6379 | – | – | lưu chi phí cloud của LiteLLM |
| `tts-router` | build `tts/router/` | 8880 | – | – | chuẩn hóa + định tuyến TTS |
| `vieneu` | build `tts/vieneu/` | 8000 | – | có | VieNeu-TTS v3 Turbo |
| `kokoro` | `kokoro-fastapi-gpu:v0.9.0-cu128` (profile `english-tts`) | 8880 | – | có | TTS tiếng Anh (tùy chọn) |
| `asr-qwen` | `vllm/vllm-openai:v0.31.0-cu129` (profile `asr-qwen`) | 8000 | – | có | Qwen3-ASR (tùy chọn) |
| `cloudflared` | `cloudflare/cloudflared:2026.10.0` (profile `tunnel`) | – | – | – | đường vào từ Internet (`cloudflared/README.md`) |

Mọi image bên thứ ba được ghim bằng digest. Mọi service: `restart: unless-stopped`, có healthcheck, log
`json-file` xoay vòng 20 MB × 3. Tài liệu từng phần: `xiaozhi/patches/README.md` (các bản vá xiaozhi),
`ollama/README.md`, `cloudflared/README.md`, `tts/README.md`, `../tools/tests/llm/README.md`,
kế hoạch gốc `../docs/modules/SV1…SV7`.

| File | Dùng khi |
|---|---|
| `docker-compose.yml` | production (batai): GPU qua CDI |
| `docker-compose.local.yml` | máy dev với Docker Desktop: GPU qua driver `nvidia`, LLM nhỏ trên Ollama của máy, LiteLLM `config.local.yaml` |
| `docker-compose.debug.yml` | mở API nội bộ (ASR, LiteLLM, TTS) trên `127.0.0.1` để thử bằng curl |
| `cloudflared/compose.locally-managed.yml` | tunnel quản lý cục bộ (phương án B) |
| `.env.example` | mẫu biến môi trường (chép thành `.env`, không commit) |

## 2. Máy chủ và ngân sách VRAM

Máy `batai`: Ubuntu 26.04.1, kernel 7.0, driver NVIDIA 595.84 (open), CUDA 13.2, RTX 5090 32 GB,
Docker Engine 29, IP LAN `192.168.1.27` (đang lấy qua DHCP – nên đặt DHCP reservation trên router).
Image container phải dùng CUDA runtime ≤ 13.2 (các image của dự án dùng 12.8/12.9).

| Thành phần | Cấu hình | VRAM ước tính |
|---|---|---|
| Ollama `qwen3:30b-a3b-instruct-2507-q4_K_M` | ctx 8192, KV f16, 1 luồng | ≈ 20–21 GB |
| ASR faster-whisper large-v3 | float16, beam 5 | ≈ 4.5 GB (int8_float16 ≈ 2.9 GB) |
| VieNeu-TTS v3 Turbo | 4 luồng | ≈ 1.0–1.3 GB |
| **Tổng mặc định** | | **≈ 26–27 GB** (mục tiêu SV1: ≤ 28 GB) |
| + Kokoro (`english-tts`) | | + ≈ 3.1 GB |
| + Qwen3-ASR (`asr-qwen`) | `--gpu-memory-utilization 0.2` | + ≈ 6.4 GB (với `ASR_ENGINE=qwen3_vllm` thì faster-whisper không nạp model) |
| LLM thay thế | `gemma4:26b-a4b-it-qat` ≈ 17.5–18 GB; `qwen3.6:35b-a3b-q4_K_M` ≈ 25–26 GB (không vừa cùng ASR + TTS) | |

Đây là ước tính, phải đo trên máy thật: `bash scripts/vram_report.sh` (tổng VRAM, tiến trình nào thuộc
container nào, so với ngân sách) và `docker compose exec ollama ollama ps`.

> **GPU của batai đang dùng chung.** Ghi nhận ngày 2026-09-12: một vLLM ≈ 8.7 GiB, Ollama của host
> ≈ 5.8 GiB và một job huấn luyện ≈ 7.8 GiB đã chạy trên cùng card. Khi đó **stack Jarvis không đủ chỗ**:
> cần dừng/chuyển các tiến trình đó, hoặc giảm (`gemma4:26b-a4b-it-qat`, `ASR_COMPUTE_TYPE=int8_float16`,
> `OLLAMA_KV_CACHE_TYPE=q8_0`). Kiểm tra `nvidia-smi` trước khi `docker compose up`.

## 3. Cài đặt lần đầu trên batai

Yêu cầu sẵn có trên host: Docker Engine ≥ 28.2 (đang là 29) với compose plugin ≥ 2.24.4, driver NVIDIA
chạy được (`nvidia-smi`). Các container sản xuất khác đang chạy trên máy này: **không** khởi động lại
dockerd, **không** bật ufw, **không** đổi `/etc/docker/daemon.json`.

### 3.1 Đăng nhập SSH bằng khóa (trên PC)

```bash
ssh-copy-id <user>@192.168.1.27
# ~/.ssh/config
Host batai
    HostName 192.168.1.27
    User <user>
```

`deploy.sh` dùng `ssh -o BatchMode=yes` (không hỏi mật khẩu) và không chứa thông tin đăng nhập nào.

### 3.2 Thư mục triển khai và chép mã

```bash
ssh -t batai 'sudo install -d -m 0750 -o "$USER" -g "$USER" /opt/jarvis'
bash server/scripts/deploy.sh --copy-only              # dry-run: in lệnh
bash server/scripts/deploy.sh --apply --copy-only      # chép server/ + tools cần thiết
```

### 3.3 Chuẩn bị host: docker group, GPU qua CDI (trên batai)

```bash
bash /opt/jarvis/server/scripts/bootstrap_host.sh             # dry-run: đọc kỹ những gì sẽ làm
bash /opt/jarvis/server/scripts/bootstrap_host.sh --apply     # thực hiện (hỏi sudo)
```

Script làm (và chỉ làm) những việc sau:

1. Kiểm tra OS, Docker ≥ 28.2 (CDI bật mặc định), compose plugin, driver NVIDIA.
2. Thêm user triển khai vào nhóm `docker` (đăng xuất/đăng nhập lại để có hiệu lực).
3. NVIDIA Container Toolkit: nếu thiếu `nvidia-ctk` thì dừng lại; chỉ cài khi thêm `--install-toolkit`
   (bản ghim 1.20.1-1). Cài gói làm systemd reload: container GPU của dự án khác dùng hook cũ (`--gpus`
   không qua CDI) có thể mất GPU tới khi được tạo lại → chỉ làm trong khung giờ bảo trì.
4. Sinh **CDI spec** cho `nvidia.com/gpu=all` mà **không khởi động lại dockerd**:
   `systemctl restart nvidia-cdi-refresh.service` (toolkit ≥ 1.18, ghi `/var/run/cdi/nvidia.yaml` ở mỗi lần
   boot) hoặc `nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml`; bật `nvidia-cdi-refresh.path` với
   `--no-reload`.
5. Tạo thư mục `/opt/jarvis` thuộc user triển khai.
6. Chạy `scripts/gpu_check.sh` (container CUDA thử với `--device nvidia.com/gpu=all`).

Vì sao CDI: dockerd trên batai được khởi động trước khi có hook NVIDIA, nên đường `--gpus`/driver
`nvidia` không có, trong khi driver `cdi` của Docker tự đọc lại spec mới mà không cần restart. Vì vậy
`docker-compose.yml` xin GPU bằng `driver: cdi, device_ids: ["nvidia.com/gpu=all"]`.

Kiểm tra tay tương đương:

```bash
docker info --format '{{json .CDISpecDirs}}'     # ["/etc/cdi","/var/run/cdi"]
nvidia-ctk cdi list                              # có nvidia.com/gpu=all
docker run --rm --device nvidia.com/gpu=all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi -L
bash /opt/jarvis/server/scripts/gpu_check.sh
```

Tuyệt đối **không** chạy `nvidia-ctk runtime configure` (viết lại daemon.json, cần restart dockerd),
`systemctl restart docker`, `systemctl daemon-reload` ngoài giờ bảo trì, hay `ufw enable`.

### 3.4 File `.env`

```bash
cd /opt/jarvis/server
cp .env.example .env && chmod 600 .env
openssl rand -hex 32                 # -> XIAOZHI_AUTH_KEY
echo "sk-$(openssl rand -hex 32)"    # -> LITELLM_MASTER_KEY
nano .env                            # ANTHROPIC_API_KEY, LAN_IP, PUBLIC_WS_URL, PUBLIC_OTA_URL ...
```

Mỗi biến đều có chú thích trong `.env.example`. Mỗi container chỉ nhận các biến được liệt kê cho nó trong
`docker-compose.yml` (ví dụ khóa Anthropic chỉ vào `litellm`). Thiếu biến bắt buộc thì mọi lệnh
`docker compose` báo `required variable ... is missing a value`.

### 3.5 Khởi động, tải model, kiểm tra

```bash
cd /opt/jarvis/server
docker compose build                       # build image (xiaozhi, asr, tts-router, vieneu): lần đầu lâu
bash scripts/pull_models.sh --apply --asr  # tải + nạp sẵn OLLAMA_MODEL (~19 GB) và tải trước Whisper (~3 GB)
docker compose up -d
docker compose ps                          # chờ tất cả (healthy); VieNeu lần đầu tải model (tới 15 phút)
curl -s http://192.168.1.27:8003/xiaozhi/ota/   # 200: "OTA接口运行正常，...websocket地址是：ws://192.168.1.27:8100/xiaozhi/v1/"
curl -s http://192.168.1.27:8100/               # "Server is running"
bash scripts/vram_report.sh                # VRAM theo container
```

Firmware: `CONFIG_OTA_URL = http://192.168.1.27:8003/xiaozhi/ota/` (hoặc đổi trong trang cấu hình Wi-Fi
của mũ). OTA trả về `PUBLIC_WS_URL` cho mũ. Truy cập từ xa: `cloudflared/README.md`.

Kiểm tra LLM: `../tools/tests/llm/README.md` (bộ thử gọi tool, ≥ 95 % đúng). Thử dự phòng cloud
(SV3 tiêu chí 3): `docker compose stop ollama`, nói một câu, xem `docker compose logs litellm | grep
'"event": "fallback"'`, rồi `docker compose start ollama`.

### 3.6 Chạy trên máy dùng chung GPU (cách đang chạy trên batai từ 7/10/2026)

GPU của batai đang được người dùng khác chiếm gần hết (khoảng 31 GB trên 32 GB: một Ollama riêng của họ với `qwen3.6:35b`,
một job training, một dịch vụ dịch), và chưa cài NVIDIA Container Toolkit (cần sudo). Vì vậy stack chạy theo mẫu
`docker-compose.shared-host.example.yml`, chép trên host thành `docker-compose.override.yml` (file riêng của host,
`deploy.sh` không đụng tới):

| Thành phần | Thiết kế | Đang chạy |
|---|---|---|
| LLM | container `ollama` + `qwen3:30b-a3b-instruct-2507` | Ollama sẵn có của host (`OLLAMA_MODEL=qwen3.6:35b`), qua relay `ollama-relay`: host Ollama chỉ nhận kết nối từ 127.0.0.1, nên một socat chạy mạng host nghe ở gateway của mạng `llm` (`10.250.99.1:11436`, ghim dải `10.250.99.0/24`) và chuyển tiếp; từ LAN cổng này bị từ chối. LiteLLM không gửi `keep_alive`/`num_ctx`, nên không đổi chính sách giữ model hay ép nạp lại model của chủ Ollama |
| ASR | large-v3 float16 trên GPU | `ASR_MODEL=medium`, `ASR_DEVICE=cpu`, `ASR_COMPUTE_TYPE=int8`, `ASR_CPU_THREADS=8` |
| TTS | VieNeu trên GPU | VieNeu backend ONNX trên CPU (`VIENEU_DEVICE=cpu`, 8 luồng) |

Đo ngày 7/10 bằng `tools/tests/e2e/device_sim.py` từ một PC trong LAN: câu khớp nhanh (chèn chữ) 3 ms tới lệnh MCP;
nói bằng giọng (PTT) khoảng 1,5 s tới lệnh MCP (ASR CPU ~1,4 s, mục tiêu 0,8 s chưa đạt); câu qua LLM: lượt đầu sau khi
chủ Ollama vừa dùng mất ~4,4 s tới token đầu (Ollama chỉ có một slot, cache prompt bị đẩy ra), các lượt sau ~0,6 s; TTS
âm đầu ~0,13 s. Bộ thử gọi tool với `qwen3.6:35b` chỉ đạt khoảng 58 % (bỏ sót lệnh thứ hai, có câu trả lời "đã làm" mà
không gọi tool); bật `think: true` còn tệ hơn (hết `max_tokens` trong phần suy nghĩ). Lệnh thường dùng vẫn chắc chắn nhờ
khớp nhanh (`quick_phrases`, không qua LLM).

Quay lại thiết kế GPU khi có đủ VRAM (~25 GB): cài toolkit + CDI (mục 3.3), xóa `docker-compose.override.yml`, trả các
biến `OLLAMA_*`/`ASR_*` trong `.env` về giá trị của `.env.example`, `scripts/pull_models.sh --apply --asr`, rồi
`docker compose up -d`.

### 3.7 Dashboard: trang trạng thái trên trình duyệt (tùy chọn)

Service `dashboard` (profile `dashboard`, D32) mở một trang web trong LAN, tự làm mới mỗi 2 giây:

- **Mũ**: trực tuyến hay không, đang nghe / đang nói, mức mic và loa (dBFS), kết quả dò mic, âm lượng, Wi-Fi, thời gian
  chạy, bộ nhớ, mặt nạ, đèn mắt, chuỗi lệnh. Lấy từ MCP cục bộ của mũ (`ws://<IP-mũ>:8080/ws`, chỉ gọi
  `self.system.get_status` và `self.get_device_status`, mỗi 5 giây một kết nối ngắn).
- **Server**: `/health` của xiaozhi, ASR, tts-router, LiteLLM (model ASR, giọng TTS); model LLM local/cloud đang cấu hình.
- **Hội thoại gần đây**: server nghe ra câu gì, Jarvis trả lời gì, lệnh nhanh và tool đã gọi, độ trễ từng bước (nhận dạng,
  LLM, TTS, tổng từ lúc ngừng nói tới khi có tiếng). Đọc từ `tmp/server.log` của xiaozhi (volume `xiaozhi_tmp`, chỉ đọc).
- **Lỗi gần đây**: các dòng ERROR trong log, kèm traceback.

Bật trên batai: thêm vào `server/.env` rồi triển khai lại (`deploy.sh --apply`, hoặc `docker compose up -d`):

```bash
COMPOSE_PROFILES=dashboard
DASHBOARD_PASSWORD=<mật khẩu dài>      # bắt buộc: thiếu thì container không chạy
JARVIS_DEVICE_HOSTS=192.168.1.28       # tùy chọn: IP mũ; mũ đã kết nối server thì tự biết IP
```

Mở `http://<IP-batai>:8090` (`DASHBOARD_PORT`), đăng nhập user `jarvis` (`DASHBOARD_USER`) và mật khẩu trên.

- Trang hiện **lời nói thu được qua mic** (cả câu của người xung quanh khi đang trong phiên): chỉ mở trong LAN, đặt
  mật khẩu riêng, `DASHBOARD_BIND_IP=<IP LAN>` nếu không muốn mở trên mọi card mạng (kể cả Tailscale); chặn ngoài LAN
  cùng các cổng xiaozhi: `scripts/firewall_lan.sh --ports "8100 8003 8090"` (mục 8).
- Log không ghi phiên của từng thiết bị: hai thiết bị nói cùng lúc (ví dụ thêm bộ giả lập) thì hội thoại có thể bị gán
  nhầm, và phiên không thấy dòng ngắt kết nối quá 10 phút được báo "không còn hoạt động". Thiết bị có MAC kiểu
  "locally administered" (bộ giả lập) được hiện là thiết bị giả lập và không bị dò MCP.
- Mũ có token MCP cục bộ (`tools/provision_secrets.py --mcp-token`) thì đặt cùng giá trị vào `JARVIS_MCP_TOKEN`.

## 4. Triển khai và cập nhật

Từ PC (Git Bash trên Windows hoặc Linux/macOS), luôn chạy thử trước:

```bash
bash server/scripts/deploy.sh                          # dry-run: in mọi lệnh, không kết nối ssh
bash server/scripts/deploy.sh --apply                  # chép + docker compose pull/build/up trên batai
bash server/scripts/deploy.sh --apply --profiles tunnel           # COMPOSE_PROFILES trên host
bash server/scripts/deploy.sh --apply --method archive --ref main # chỉ những gì đã commit
bash server/scripts/deploy.sh --apply --pull-models    # + scripts/pull_models.sh --apply
```

- Chép: `server/` (trừ `.env`, thư mục test, cache) → `/opt/jarvis/server`; `tools/tasks_cli.py` và
  `tools/tests/llm/` → `/opt/jarvis/tools`. **`server/.env` và `server/docker-compose.override.yml`
  (override riêng của host, nếu có) trên host không bao giờ bị chép đè hay xóa.**
- Cách chép: `rsync` (xóa file thừa) nếu PC có rsync, nếu không thì `tar` qua ssh (Git Bash không có
  rsync), hoặc `git archive`. Host/thư mục: `--host`, `--dir` hoặc `JARVIS_DEPLOY_HOST`, `JARVIS_DEPLOY_DIR`.
- Script từ chối chạy nếu script/Dockerfile có xuống dòng CRLF (checkout Windows với `core.autocrlf=true`)
  và kiểm tra `docker-compose.yml` bằng `docker compose config` trước khi chép.
- Trên host nó chạy: `docker compose config -q && docker compose pull --ignore-buildable --quiet &&
  docker compose build --pull && docker compose up -d --remove-orphans && docker compose ps`.
- File cấu hình gắn vào container chỉ được đọc khi container khởi động, nên script so mã băm với lần
  triển khai trước (lưu ở `server/.deploy-*.sha` trên host): `litellm/config.yaml` hoặc
  `litellm/jarvis_callbacks.py` đổi → `docker compose restart litellm`; `ollama/entrypoint.sh` đổi →
  tạo lại container `ollama` (nạp lại model, khoảng 1 phút), nhưng chỉ khi service `ollama` đang bật: ở chế độ dùng chung
  GPU (mục 3.6) nó nằm trong profile `own-ollama` không bật nên không bị khởi động. Lần đầu chạy bản script này luôn làm cả hai.
- Lần đầu chưa có `.env`: file được chép xong, script dừng và hướng dẫn tạo `.env`.

Cập nhật bằng tay trên host: `cd /opt/jarvis/server && docker compose pull --ignore-buildable &&
docker compose build --pull && docker compose up -d --remove-orphans`.

- Đổi giá trị trong `.env` → `docker compose up -d <service>` (tạo lại container). **`docker compose
  restart` không đọc lại `.env`.** Đổi `litellm/config.yaml` → `docker compose restart litellm` là đủ.
- Nâng phiên bản một image bên thứ ba: đổi tag + digest trong `docker-compose.yml`, thử trên máy dev
  (`docker-compose.local.yml`), chạy `pytest server/scripts/tests` rồi mới triển khai.
- Quay lại bản trước: `deploy.sh --apply --method archive --ref <commit cũ>`.

## 5. Vận hành, log

```bash
cd /opt/jarvis/server
docker compose ps
docker compose logs -f --tail 100 xiaozhi            # mốc độ trễ: dòng "[LAT] sid=... stage=..."
docker compose exec xiaozhi tail -f tmp/server.log   # log file của xiaozhi (volume xiaozhi_tmp)
docker compose logs litellm | grep '"tag": "jarvis-llm"'
docker compose exec ollama ollama ps
bash scripts/vram_report.sh --watch 5
```

Dòng log JSON của LiteLLM (`litellm/jarvis_callbacks.py`, không chứa nội dung hội thoại):

| `event` | Khi nào |
|---|---|
| `cloud_call` | mỗi lượt gọi cloud thành công: model, token, `cost_usd`, `latency_ms`, `ttft_ms`, `fallback_from` |
| `fallback` | LiteLLM chuyển `jarvis-main` → `jarvis-cloud` (lý do: `Timeout`, `APIConnectionError`…) |
| `llm_error` | một lượt gọi model lỗi (local hoặc cloud) |
| `budget_exceeded` | hết ngân sách cloud tháng này (HTTP 429, không dự phòng) |
| `proxy_reject` | LiteLLM từ chối trước khi gọi model (thiếu/sai khóa) |
| `local_call` | chỉ khi `JARVIS_LOG_LOCAL_CALLS=1` |

Ví dụ: `{"tag": "jarvis-llm", "event": "cloud_call", "model_group": "jarvis-cloud", "model":
"anthropic/claude-opus-5-5", "prompt_tokens": 1000, "completion_tokens": 20, "cost_usd": 0.0044,
"fallback_from": "jarvis-main", ...}`.

Chi phí cloud tháng này (LiteLLM lưu trong Redis, không mất khi khởi động lại):

```bash
docker compose exec redis sh -c 'for k in $(redis-cli --scan --pattern "deployment_spend:*"); do echo "$k = $(redis-cli get $k) USD"; done'
```

Sau khi máy khởi động lại, Docker tự chạy lại mọi container (`unless-stopped`); `ollama` tự nạp sẵn model
(`docker compose logs ollama | grep jarvis-preload`). Thu thập dữ liệu ASR: `ASR_SAVE_DIR=/data/asr-dataset`
trong `.env` (volume `asr_dataset`).

## 6. Sao lưu

| Dữ liệu | Ở đâu | Sao lưu? |
|---|---|---|
| `server/.env` | file trên host | **có** (chứa bí mật: lưu mã hóa) |
| `jarvis_redis_data` | volume | **có** (chi phí cloud tháng này) |
| `jarvis_vieneu_voices` | volume | có, nếu đã clone giọng |
| `jarvis_asr_dataset` | volume | có, nếu đang thu dữ liệu ASR |
| `jarvis_xiaozhi_data` | volume | nên (cache câu đáp wake word, file firmware OTA). Volume này còn chứa `.config.yaml` đã render (có khóa LiteLLM và khóa ký token xiaozhi): lệnh dưới **loại file đó khỏi bản sao lưu**; entrypoint tạo lại khi khởi động |
| `jarvis_ollama_models`, `jarvis_hf_cache`, `jarvis_vieneu_hf_cache`, `jarvis_asr_cuda_cache`, `jarvis_asr_qwen_hf_cache` | volume | không (tải lại được, rất lớn) |
| `jarvis_xiaozhi_tmp` | volume | không (log, âm thanh tạm) |

```bash
cd /opt/jarvis && mkdir -p backup && chmod 700 backup
docker compose -f server/docker-compose.yml exec redis redis-cli BGSAVE
# File nén quyền 0600, thuộc user của bạn (không phải root), không chứa cấu hình đã render (có bí mật).
for v in redis_data vieneu_voices asr_dataset xiaozhi_data; do
  f="$v-$(date +%F).tgz"
  docker run --rm -v "jarvis_$v:/v:ro" -v "$PWD/backup:/b" alpine:3 sh -c \
    "umask 077 && tar czf /b/$f -C /v --exclude='.config.yaml*' . && chown $(id -u):$(id -g) /b/$f"
done
cp server/.env "backup/env-$(date +%F)" && chmod 600 backup/env-*
tar tzf "backup/xiaozhi_data-$(date +%F).tgz" | grep -c config.yaml   # phải in 0 (một file mỗi lần)
# Khôi phục một volume (dừng service dùng nó trước):
docker run --rm -v jarvis_redis_data:/v -v "$PWD/backup:/b" alpine:3 sh -c 'cd /v && tar xzf /b/redis_data-<ngày>.tgz'
```

## 7. Cổng mạng

| Cổng host | → container | Dùng cho | Phạm vi |
|---|---|---|---|
| **8100**/tcp (`XIAOZHI_WS_PORT`) | `xiaozhi:8000` | WebSocket `ws://<IP>:8100/xiaozhi/v1/` | chỉ LAN |
| **8003**/tcp (`XIAOZHI_OTA_PORT`) | `xiaozhi:8003` | OTA `http://<IP>:8003/xiaozhi/ota/` | chỉ LAN |
| **8090**/tcp (`DASHBOARD_PORT`) | `dashboard:8080` | trang trạng thái (chỉ khi bật profile `dashboard`, mục 3.7) | chỉ LAN, có mật khẩu |
| – | asr 9000, litellm 4000, redis 6379, tts-router 8880, vieneu 8000, kokoro 8880, ollama 11434, asr-qwen 8000 | nội bộ mạng Docker | không mở |

- Cổng **8000 của host thuộc dự án khác**: WebSocket của Jarvis mở ở 8100 (bên trong container vẫn là 8000).
- Host đã có Ollama riêng ở **11434**: container `ollama` không bao giờ mở cổng ra host.
- `XIAOZHI_BIND_IP=<IP LAN>` trong `.env` để chỉ mở hai cổng trên card mạng LAN (mặc định mọi card).
- `docker-compose.debug.yml` mở ASR/LiteLLM/TTS trên `127.0.0.1` (đổi cổng bằng `DEBUG_ASR_PORT`,
  `DEBUG_LITELLM_PORT`, `DEBUG_TTS_PORT` nếu trùng).
- `cloudflared` chỉ cần đi ra cổng 7844 TCP/UDP (và 443), không cần mở cổng vào.
- Đổi cổng host thì sửa cả `PUBLIC_WS_URL` / `PUBLIC_OTA_URL` và địa chỉ OTA trong firmware.
- Mạng Docker được tách để mỗi container chỉ thấy service nó gọi: `edge` (cloudflared, xiaozhi),
  `app` (xiaozhi, litellm, asr, tts-router, dashboard), `llm` (litellm, redis, ollama), `tts` (tts-router,
  vieneu, kokoro), `asrnet` (asr, asr-qwen). Redis (bộ đếm ngân sách cloud, không mật khẩu) và Ollama
  chỉ LiteLLM gọi được; VieNeu (mã bên thứ ba) chỉ thấy tts-router. Mạng nào cũng ra Internet được.
  Container phụ (test LLM, sinh dữ liệu wake word) nối vào đúng mạng: `--network jarvis_app` để gọi
  LiteLLM, `--network jarvis_tts` để gọi VieNeu.

## 8. Firewall

- **Không bật ufw một cách máy móc.** Máy đang phục vụ dự án khác: `ufw enable` với "deny incoming" có thể
  cắt SSH và các cổng khác đang dùng. Hơn nữa **cổng Docker publish đi vòng qua ufw** (Docker DNAT ở
  PREROUTING rồi chuyển qua chuỗi FORWARD, trong khi ufw lọc INPUT), nên ufw không bảo vệ được 8100/8003.
- Cách nên dùng, từ đơn giản tới chặt hơn:
  1. Router: **không** port-forward 8100/8003 ra Internet; truy cập từ xa chỉ qua Cloudflare Tunnel.
  2. `XIAOZHI_BIND_IP=192.168.1.27` trong `.env` rồi `docker compose up -d xiaozhi`.
  3. `scripts/firewall_lan.sh`: luật trong chuỗi `DOCKER-USER` (chuỗi Docker dành cho người dùng), khớp
     cổng gốc mà client gọi (`-m conntrack --ctorigdstport 8100`), chỉ cho mạng LAN; chỉ đụng tới luật gắn
     nhãn `jarvis-lan`:
     ```bash
     bash scripts/firewall_lan.sh                     # dry-run, mạng = LAN_IP/24 trong .env
     bash scripts/firewall_lan.sh --apply             # cài luật
     bash scripts/firewall_lan.sh --status
     bash scripts/firewall_lan.sh --apply --persist   # + dòng @reboot trong crontab của root
     bash scripts/firewall_lan.sh --remove --apply    # gỡ
     ```
     `--persist` dùng crontab thay cho unit systemd để khỏi phải `systemctl daemon-reload` trên máy GPU dùng chung.
- Kiểm tra từ ngoài LAN (điện thoại dùng 4G): `curl -m 3 http://<IP public>:8100/` phải thất bại; từ LAN
  thì 8100/8003 trả lời, các cổng 4000/6379/8880/9000 của Jarvis không mở.

## 9. Bảo mật

- **OTA cấp token WebSocket cho bất kỳ ai gọi được nó** (thiết kế của xiaozhi-server): giữ 8100/8003
  trong LAN; đường từ Internet bắt buộc qua **Cloudflare Access** với service token (`cloudflared/README.md`).
  `/mcp/vision/explain` (không cần token) không được trỏ qua tunnel.
- xiaozhi bật `server.auth` với `XIAOZHI_AUTH_KEY` cố định; đổi khóa này = thu hồi mọi token đã cấp.
- Bí mật chỉ ở `server/.env` (chmod 600, không commit). Mỗi container chỉ nhận biến mình cần: khóa
  Anthropic/OpenAI/Gemini chỉ vào `litellm`, `TUNNEL_TOKEN` chỉ vào `cloudflared`, `xiaozhi` (dịch vụ
  hướng ra mạng) chỉ có khóa xác thực của nó và khóa LiteLLM. `tests` kiểm tra điều này.
- `LITELLM_MASTER_KEY` phải mạnh (LiteLLM v1.104.0 từ chối khởi động với khóa rỗng hoặc `sk-1234`).
  Khóa sai → 400 `No connected db` (vẫn bị từ chối) và dòng log `proxy_reject`.
- Ngân sách cloud `CLOUD_MONTHLY_BUDGET_USD` được LiteLLM áp dụng theo tháng, lưu trong Redis.
- Redis, Ollama, ASR, TTS không mở cổng ra ngoài; Redis không mật khẩu (chỉ trong mạng Docker).
- VieNeu chạy mã từ xa của model (`trust_remote_code`): container không có bí mật nào; đặt
  `TTS_HF_OFFLINE=1` sau lần tải model đầu tiên.
- Log xiaozhi che `Authorization` / `CF-Access-*` (bản vá SV-C); LiteLLM tắt ghi nội dung hội thoại
  (`turn_off_message_logging`).
- Image ghim digest; `deploy.sh` không chứa thông tin đăng nhập (dùng khóa ssh).

## 10. Đổi nhà cung cấp / model cloud

`jarvis-cloud` (dự phòng của `jarvis-main` và đích của plugin `ask_expert`) mặc định là
**Claude Opus 5.5** (`anthropic/claude-opus-5-5`, $4 / $20 mỗi triệu token, ngữ cảnh 1M).

- Opus 5.5 **luôn "nghĩ"** (adaptive thinking, không tắt được): `reasoning_effort: low` trong
  `litellm/config.yaml` → LiteLLM gửi `thinking: {type: adaptive}` + `output_config: {effort: low}`.
- Opus 5.5 trả lỗi 400 nếu có `temperature`/`top_p`/`top_k`; xiaozhi gửi `temperature: 0.6` nên
  `additional_drop_params` bỏ các tham số này.
- Token "nghĩ" tính vào `max_tokens`, mà xiaozhi chỉ xin 300: callback nâng `max_tokens` của lượt gọi cloud
  lên `JARVIS_CLOUD_MIN_MAX_TOKENS` (mặc định 1024). Độ dài câu nói vẫn do prompt quyết định.
- Đã thử với LiteLLM v1.104.0 và một API Anthropic giả: request gửi đi không còn `temperature`, có
  `thinking adaptive` + `effort low`, `max_tokens` 1024, tool được chuyển đúng định dạng, chi phí tính đúng.

| Model | ID | Giá vào / ra (USD / 1M token) | Khi nào dùng | Sửa trong `litellm/config.yaml` |
|---|---|---|---|---|
| Claude Opus 5.5 (mặc định) | `claude-opus-5-5` | 4 / 20 | chất lượng cao nhất | – |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | 2 / 10 | nhanh và rẻ hơn | giữ `reasoning_effort: low` và `additional_drop_params`; `input_cost_per_token: 2.0e-06`, `output_cost_per_token: 1.0e-05` (**bắt buộc**: bảng giá kèm LiteLLM v1.104.0 chưa có model này – thiếu giá thì chi phí = 0 và ngân sách không bao giờ chặn) |
| Claude Haiku 4.5 | `claude-haiku-4-5` | 1 / 5 | nhanh nhất, không "nghĩ" trừ khi yêu cầu, chấp nhận `temperature` | **xóa** dòng `reasoning_effort` (Haiku 4.5 báo lỗi với effort); giá `1.0e-06` / `5.0e-06`; có thể đặt `JARVIS_CLOUD_MIN_MAX_TOKENS=0` |

Các bước:

1. Sửa `CLOUD_MODEL=anthropic/<id>` trong `server/.env`.
2. Sửa khối `jarvis-cloud` trong `litellm/config.yaml` theo bảng (giá trong `model_info`, `reasoning_effort`).
   Giữ nguyên `model_info.id` (`jarvis-cloud-anthropic`) để chi phí tháng này được tính tiếp; đổi id = đếm lại từ 0.
3. `docker compose up -d litellm`.
4. Thử: `python tools/tests/llm/run_tool_calls.py --model jarvis-cloud --only expert_vi,open_vi ...` và xem
   dòng `cloud_call` (`cost_usd` phải > 0).

Nhà cung cấp khác (OpenAI, Gemini): `OPENAI_API_KEY` / `GEMINI_API_KEY` trong `.env` đã được chuyển vào `litellm`
dưới tên `JARVIS_OPENAI_API_KEY` / `JARVIS_GEMINI_API_KEY`; thay khối `jarvis-cloud` bằng
`model: openai/<model-id>` + `api_key: os.environ/JARVIS_OPENAI_API_KEY` (hoặc `gemini/<model-id>` +
`JARVIS_GEMINI_API_KEY`), bỏ `reasoning_effort` nếu model không hỗ trợ, khai báo giá trong `model_info`. Tra model ID
và giá trên trang của nhà cung cấp lúc triển khai. Nhà cung cấp khác nữa: thêm
`JARVIS_TEN_KHOA_API_KEY: ${TEN_KHOA_API_KEY:-}` vào `environment` của `litellm` trong `docker-compose.yml` và
`TEN_KHOA_API_KEY=` vào `.env`.

> **Vì sao đổi tên khóa trong container litellm?** LiteLLM có các route chuyển tiếp thẳng (`/anthropic/*`, `/openai/*`,
> `/gemini/*`) tự lấy khóa từ biến mặc định (`ANTHROPIC_API_KEY`…) và **không đi qua giới hạn chi phí** của router.
> Ai có master key (xiaozhi có) sẽ gọi cloud không giới hạn. Trong container không có biến tên mặc định nên các route đó
> trả 401; chỉ `jarvis-cloud` (có `max_budget`) gọi được cloud. Nên đặt thêm hạn mức chi tiêu tháng ngay trên trang
> quản trị của Anthropic làm chốt chặn cuối.

Luôn kiểm tra lại danh sách model và lịch ngừng hỗ trợ trên tài liệu của Anthropic trước khi đổi.

## 11. Thử trên máy dev (Docker Desktop)

`docker-compose.local.yml` dùng GPU qua driver `nvidia` của Docker Desktop (Docker Desktop không có CDI spec
NVIDIA: `--device nvidia.com/gpu=all` báo `unresolvable CDI devices`), LiteLLM đọc `config.local.yaml`:
`jarvis-main` = Ollama của máy (model nhỏ `qwen3:4b-instruct`), `jarvis-cloud` = một bản "cloud giả" cũng
chạy trên Ollama nhưng tính giá như Opus 5.5 (để thử log chi phí và ngân sách mà không cần khóa Anthropic).

```bash
cd server
cp .env.example .env    # điền khóa giả đủ dài; ANTHROPIC_API_KEY để trống
docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.debug.yml up -d redis litellm
curl -s http://127.0.0.1:4000/health/liveliness
python ../tools/tests/llm/run_tool_calls.py --env-file .env
# thử dự phòng: model local "chết"
LOCAL_MAIN_API_BASE=http://127.0.0.1:9 docker compose -f docker-compose.yml -f docker-compose.local.yml \
  -f docker-compose.debug.yml up -d litellm
docker compose -f docker-compose.yml -f docker-compose.local.yml down
```

Không có GPU NVIDIA: thêm một override riêng với `deploy: !reset {}` cho `asr`, `vieneu`, `kokoro`,
`ollama`, `asr-qwen` và đặt `LOCAL_ASR_DEVICE=cpu LOCAL_ASR_COMPUTE_TYPE=int8 LOCAL_VIENEU_DEVICE=cpu`.

## 12. Kiểm thử và những gì đã xác minh

```bash
.venv/Scripts/python -m pytest server/scripts/tests server/litellm/tests tools/tests/llm -q
```

- `server/scripts/tests`: cú pháp + shellcheck mọi script, chạy thử (dry-run) từng script với lệnh giả
  (không gọi ssh/sudo/iptables/apt/curl thật), các biến môi trường của từng service khớp với mã nguồn,
  bí mật chỉ tới đúng container, cổng, GPU CDI, digest, `docker compose config` của mọi biến thể.
- `server/litellm/tests`: callback (sự kiện, chi phí, độ trễ, dự phòng, ngưỡng `max_tokens`) và cấu hình.
- `tools/tests/llm`: bộ thử gọi tool, đồng bộ `device_tools.json` với firmware.

Đã chạy thật ngày 2026-10-06 trên máy dev (Docker Desktop, GTX 1660 SUPER): LiteLLM v1.104.0 với
`config.local.yaml` (liveliness, xác thực, chat, stream, gọi tool, dự phòng khi model local chết và khi
treo, cooldown, ngân sách 429 + lưu trong Redis), `config.yaml` production với API Anthropic giả, luật
ingress bằng cloudflared 2026.10.0, quyền ghi volume của image ASR, `ollama/entrypoint.sh` trong image
`ollama/ollama:0.35.1` (nạp sẵn model nhỏ trên CPU, bỏ qua model chưa tải), `scripts/gpu_check.sh --mode gpus`.

Chưa xác minh (cần máy thật): mọi thứ trên batai (CDI, VRAM thật của RTX 5090, tường lửa), gọi Claude
thật, điểm gọi tool của model 30B, độ trễ dự phòng khi container `ollama` bị dừng trên Linux (mục 13).

## 13. Sự cố thường gặp

| Hiện tượng | Nguyên nhân → cách xử lý |
|---|---|
| `unresolvable CDI devices nvidia.com/gpu=all` | chưa có CDI spec → `bootstrap_host.sh --apply` (bước 4), `scripts/gpu_check.sh`. Trên Docker Desktop dùng `docker-compose.local.yml` |
| `could not select device driver "nvidia"` | dùng `docker-compose.local.yml` trên host Linux mà dockerd không có hook NVIDIA → trên batai chỉ dùng `docker-compose.yml` (CDI) |
| `required variable X is missing a value` | `.env` thiếu biến bắt buộc → điền theo `.env.example` |
| `xiaozhi` thoát ngay, log `missing or empty environment variable(s)` | biến của template cấu hình trống → sửa `.env`, `docker compose up -d xiaozhi` |
| `litellm` thoát khi khởi động, nói về master key | `LITELLM_MASTER_KEY` rỗng/yếu → `echo "sk-$(openssl rand -hex 32)"` |
| LiteLLM trả 400 `No connected db` | khóa LiteLLM sai (xiaozhi và `ask_expert` dùng `LITELLM_MASTER_KEY`) → sửa rồi `docker compose up -d xiaozhi` |
| ASR không healthy, log `Permission denied` khi tải model | volume cũ do root tạo (bản compose cũ gắn `/hf-cache`) → `docker compose rm -sf asr && docker volume rm jarvis_hf_cache` rồi `docker compose up -d asr` |
| Healthcheck `starting` rất lâu | lần đầu ASR/VieNeu tải model (cho phép tới 15 phút); xem `docker compose logs -f asr vieneu` |
| Lần `up` đầu báo `dependency failed to start ... is unhealthy`, xiaozhi không chạy | tải model quá 15 phút (đường truyền chậm). Chờ `docker compose ps` báo asr/vieneu `healthy` rồi chạy lại `docker compose up -d`. Lần sau: tải trước bằng `scripts/pull_models.sh --apply --asr` |
| Câu đầu tiên sau khi khởi động do cloud trả lời | model chưa nạp/chưa tải → `docker compose logs ollama \| grep jarvis-preload`, `scripts/pull_models.sh --apply` |
| Mọi câu đều qua cloud (`fallback` liên tục) | Ollama chết/treo, model chưa tải, thiếu VRAM (chạy một phần trên CPU → timeout) → `ollama ps`, `scripts/vram_report.sh` |
| Dự phòng mất nhiều giây khi container `ollama` bị dừng | xem mục 13.1 |
| 429 `crossed budget` | hết ngân sách cloud tháng này → tăng `CLOUD_MONTHLY_BUDGET_USD` (`docker compose up -d litellm`) hoặc chờ sang kỳ sau |
| Đổi `.env` mà không có tác dụng | đã dùng `docker compose restart` → dùng `docker compose up -d <service>` |
| Cổng 8100/8003 đã bị chiếm | đổi `XIAOZHI_WS_PORT` / `XIAOZHI_OTA_PORT` + `PUBLIC_*_URL` + địa chỉ OTA trong firmware |
| Script báo CRLF / `$'\r': command not found` | file bị checkout với CRLF trên Windows → chuyển về LF (`dos2unix`, hoặc `git config core.autocrlf input` rồi checkout lại) |
| Mũ ở ngoài không kết nối được | xem mục Sự cố trong `cloudflared/README.md` |

### 13.1 Dự phòng chậm khi container `ollama` bị dừng

Khi `ollama` dừng hẳn, tên `ollama` biến khỏi DNS nội bộ của Docker và mỗi lần LiteLLM tra cứu nó, câu hỏi
được chuyển ra DNS bên ngoài. Nếu DNS đó trả lời "không có tên này" chậm, mỗi câu hỏi phải chờ thêm trước
khi chuyển sang cloud (trên Docker Desktop đo được khoảng 4 s mỗi lần tra cứu, tổng ~16–20 s; trên Linux
với DNS bình thường thường chỉ vài chục ms – chưa đo được trên batai). Đo trên host:

```bash
docker compose exec litellm python3 -c '
import socket, time
t = time.time()
try:
    socket.getaddrinfo("ollama-absent-test", 11434)
except OSError as e:
    print("lookup failed as expected:", e)
print(round(time.time() - t, 2), "s")'
```

Kết quả trên ~0.5 s: thêm DNS nhanh cho `litellm` trong một file override (ví dụ
`docker-compose.override.yml` với `services: {litellm: {dns: ["192.168.1.1"]}}`, thay bằng DNS của router
hoặc `1.1.1.1`), rồi `docker compose up -d litellm`. Khi Ollama chỉ bị treo (container vẫn chạy), LiteLLM
chờ tối đa `stream_timeout` 10 s; hai lần timeout liên tiếp → bỏ qua model local trong 30 s.
