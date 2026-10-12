# Jarvis Helmet

Mũ giáp kiểu Iron Man có trợ lý ảo **Jarvis**:
- nghe hiểu **tiếng Việt và tiếng Anh, kể cả trộn trong cùng một câu**;
- đánh thức bằng giọng nói ("Jarvis"; "Hàu ơi" ở biến thể firmware B2, "Quản gia" ở B1) hoặc nút bấm (PTT);
- trả lời bằng giọng nói, giọng nam tiếng Việt (VieNeu-TTS), tiếng Anh tùy chọn bằng Kokoro;
- điều khiển 2 servo nâng mặt nạ, đèn mắt và một kênh output dự phòng;
- chạy các tác vụ tùy biến khai báo trong file YAML, kể cả khi mất mạng (nút bấm);
- hiển thị HUD trên màn hình 2.4" 320×240.

> Phạm vi repo: **phần điện điều khiển + firmware + server AI**. Phần cơ khí làm riêng.

## Trạng thái

Mã nguồn của mọi module đã xong và đã kiểm thử trên máy dev: test firmware chạy trên máy tính, build đủ 4 biến thể firmware
(ESP-IDF 6.1), toàn bộ test Python (cũng chạy trong container Linux như CI), và smoke test cả stack server bằng Docker Desktop
với bộ giả lập mũ (`tools/tests/e2e/device_sim.py`).

**Hiện trạng ngày 10/10/2026.** Mũ chưa lắp; mọi phép thử chạy trên board thử cắm breadboard (ESP32-S3 N16R8, mic INMP441,
amply MAX98357A + loa, LCD 2.4" 320×240).

| Phần | Hiện trạng |
|---|---|
| Server `batai` | Chạy từ 7/10 ở chế độ dùng chung GPU (`server/README.md` mục 3.6), đủ 8 container: LLM `qwen3.6:35b` qua Ollama sẵn có của máy, ASR Whisper medium và TTS VieNeu trên CPU, giọng đọc nhanh 1,5 lần (từ 9/10). Trang trạng thái ở cổng 8090 (D32) |
| Board thử | Firmware `jarvis-helmet-mww`, chạy ổn định (không tự khởi động lại), HUD hiện trên LCD 2.4". Cả chuỗi nói chuyện chạy từ 8/10: đánh thức hoặc nút BOOT → ASR → LLM → TTS → loa, LLM gọi được tác vụ trên board (đóng mặt nạ). Từ lúc nói xong đến lúc mũ bắt đầu trả lời: 2,4–3,5 s (đo ở server ngày 9/10), phần lớn là ASR trên CPU (1,3–2,3 s). Loa hết rè từ 9/10 (ngõ ra 48 kHz, lọc cắt trầm, nối cố định chân SD và GAIN của amply) |
| Wake word | "Hàu ơi" (B2) model v4, ngưỡng 97/5: offline nhận 95% clip sạch, 0,63 lần kích nhầm mỗi giờ trên giọng người thật; đã đánh thức được trên board thử (server nhận 7 lần ngày 9/10). Qua mic board thử chỉ nhận 35% vì mic ồn (SNR ~7,5 dB, 60% bản thu bị cắt đỉnh); ASR cũng sai nhiều hơn: WER 11–17% với âm thanh sạch, 41–52% qua kênh mic này (mô phỏng). Chi tiết: [`tools/wakeword/README.md`](tools/wakeword/README.md) mục 9 |
| Lỗi đang có | Ollama dùng chung đôi lúc không trả lời trong 10 s (`stream_timeout`; 19/221 yêu cầu ngày 9/10). Chưa có khóa Anthropic để chuyển sang Claude, nên khi đó Jarvis chỉ nói "hệ thống đang gặp sự cố". Số đo 5V trên HUD sai (2,6–3,6 V) vì cầu chia áp VSENSE trên breadboard đấu chưa đúng (lỗi phần cứng) |

Cách kiểm tra mic và loa: [board README mục 8.1](firmware/main/boards/jarvis-helmet/README.md).

**Chưa làm:** lắp mũ hoàn chỉnh và đo đạc (HW1–HW4, servo/đèn, hiệu chỉnh, độ trễ thật), GPU riêng cho Jarvis (cần VRAM
trống và NVIDIA Container Toolkit), Cloudflare Tunnel + Access và khóa Anthropic, đo tỉ lệ nhận "Hàu ơi" bằng giọng thật với
mic đặt gần miệng, thu dữ liệu giọng thật cho ASR/wake word. Kết quả nghiệm thu ghi vào [docs/test-report.md](docs/test-report.md).

## Kiến trúc

```
┌──────────── Mũ (ESP32-S3 N16R8) ────────────┐        ┌────── Server Ubuntu "batai" (RTX 5090) ──────┐
│ INMP441 ─► AFE ─► wake word "Jarvis"        │  WSS   │ cloudflared ─► xiaozhi-server (+ bản vá)     │
│ Opus 16 kHz ───────────────────────────────────────► │   ├─ ASR  faster-whisper large-v3            │
│ ◄──────────────────────────── Opus 24 kHz (TTS)      │   ├─ LLM  LiteLLM → Ollama Qwen3 30B (local) │
│ MAX98357 ─► loa 3W                          │        │   │         └► Claude (cloud, có ngân sách)  │
│ MCP tools: mask · eyes · output · sequence  │ ◄───── │   ├─ TTS  tts-router → VieNeu-TTS / Kokoro   │
│ ST7789 HUD · nút PTT · nút mặt nạ (offline) │        │   └─ plugin helmet_tasks (tasks.yaml)        │
└─────────────────────────────────────────────┘        └──────────────────────────────────────────────┘
```

## Cấu trúc repo

| Thư mục | Nội dung |
|---|---|
| [`firmware/`](firmware/) | xiaozhi-esp32 v2.5.0 + board [`jarvis-helmet`](firmware/main/boards/jarvis-helmet/README.md) (servo, đèn mắt, sequence runner, MCP tools, HUD, nút bấm, wake word); test chạy trên máy tính ở [`firmware/tests/host/`](firmware/tests/host/) |
| [`server/`](server/README.md) | Docker Compose cho máy chủ: xiaozhi-server đã vá, ASR, LiteLLM + Redis, Ollama, TTS router + VieNeu (+ Kokoro), cloudflared; script triển khai, firewall, sao lưu |
| [`server/tasks/tasks.yaml`](server/tasks/tasks.yaml) | Các tác vụ của mũ (mở mặt nạ, bật đèn…); sửa là có hiệu lực, không cần khởi động lại |
| [`docs/`](docs/) | Kế hoạch, giao tiếp giữa các module, tài liệu từng module, phần cứng, HUD, mẫu báo cáo nghiệm thu |
| [`tools/`](tools/) | Build firmware bằng Docker, nạp bí mật, hiệu chỉnh servo, kiểm tra `tasks.yaml`, font, wake word, bộ test e2e/ASR/TTS/LLM |

## Bắt đầu nhanh

```bash
# Firmware (Docker, không cần cài ESP-IDF): build biến thể mặc định, lấy file .bin về out/fw
FW_OUT=out/fw tools/fw_build_docker.sh jarvis-helmet --name jarvis-helmet --language vi-VN --wake-word wn9_jarvis_tts
# Nạp, cấu hình Wi-Fi/OTA, hiệu chỉnh servo: firmware/main/boards/jarvis-helmet/README.md

# Server: xem trước rồi triển khai lên batai (chi tiết, kể cả các bước cần sudo: server/README.md)
bash server/scripts/deploy.sh                 # dry-run
bash server/scripts/deploy.sh --apply

# Kiểm thử trên máy dev
python -m pip install -r requirements-dev.txt
python -m pytest -q server/tests server/scripts/tests server/litellm/tests tools/tests tools/wakeword/tests
python tools/tasks_cli.py validate
```

## Tài liệu

- [docs/PLAN.md](docs/PLAN.md): tổng quan, các quyết định đã chốt, lộ trình, rủi ro
- [docs/interfaces.md](docs/interfaces.md): giao tiếp firmware ↔ server (MCP tools, định dạng chuỗi bước, NVS, `tasks.yaml`)
- [docs/hardware/](docs/hardware/): sơ đồ đấu dây, bố trí PCB đục lỗ, BOM
- [docs/hud.md](docs/hud.md): thiết kế HUD; [docs/wakeword-report.md](docs/wakeword-report.md): chọn phương án wake word
- [docs/test-report.md](docs/test-report.md): mẫu báo cáo nghiệm thu; [tools/tests/e2e/scenarios.md](tools/tests/e2e/scenarios.md): kịch bản E1–E14

### Danh sách module

| ID | Module | Nhóm | Phụ thuộc | Trạng thái |
|---|---|---|---|---|
| [HW1](docs/modules/HW1-nguon-dien.md) | Nguồn & phân phối điện | Phần cứng | – | Tài liệu, sơ đồ xong; chờ lắp và đo |
| [HW2](docs/modules/HW2-dau-noi-tin-hieu.md) | Bảng chân ESP32-S3 & đấu nối tín hiệu | Phần cứng | HW1 | Tài liệu, sơ đồ xong; chờ lắp và đo |
| [HW3](docs/modules/HW3-mach-cong-suat.md) | Mạch công suất: servo, đèn mắt, output dự phòng | Phần cứng | HW1, HW2 | Tài liệu, sơ đồ xong; chờ lắp và đo |
| [HW4](docs/modules/HW4-lap-rap-pcb.md) | Bố trí & hàn PCB 7×9, đi dây trong mũ | Phần cứng | HW1–HW3, FW1 | Bố trí xong; chờ hàn |
| [FW1](docs/modules/FW1-board-bringup.md) | Board `jarvis-helmet`: khởi động phần cứng | Firmware | HW2 | Code xong; chạy trên board thử; chờ mũ thật |
| [FW2](docs/modules/FW2-mask-controller.md) | Điều khiển mặt nạ (2 servo) | Firmware | FW1, HW3 | Code xong, test máy tính OK; chờ hiệu chỉnh |
| [FW3](docs/modules/FW3-output-controller.md) | Đèn mắt & output dự phòng | Firmware | FW1, HW3 | Code xong; chờ mũ thật |
| [FW4](docs/modules/FW4-sequence-mcp.md) | Sequence runner & MCP tools | Firmware | FW2, FW3 | Code xong, test máy tính OK; server đồng bộ và chạy được sequence trên board thử |
| [FW5](docs/modules/FW5-nut-bam-offline.md) | Nút bấm & chế độ offline | Firmware | FW4 | Code xong; chờ mũ thật |
| [FW6](docs/modules/FW6-hud-display.md) | HUD Iron Man & font tiếng Việt | Firmware | FW1 | Code xong, font đủ glyph; HUD chạy trên LCD 2.4" của board thử |
| [FW7](docs/modules/FW7-wake-word.md) | Wake word "Jarvis" + tên tiếng Việt | Firmware | FW1, SV4 | 3 phương án đã build; "Hàu ơi" (B2) v4 chạy trên board thử: offline 95% clip sạch, 0,63 kích nhầm mỗi giờ; qua mic board thử chỉ 35% (mic ồn, SNR ~7,5 dB); chờ đo bằng giọng thật |
| [SV1](docs/modules/SV1-ha-tang.md) | Hạ tầng server (GPU, Docker) | Server | – | Chạy trên batai ở chế độ dùng chung GPU; GPU riêng chờ sudo và VRAM trống |
| [SV2](docs/modules/SV2-asr.md) | Nhận dạng giọng nói Việt–Anh | Server | SV1 | Chạy trên batai (Whisper medium, CPU): WER 11% giọng đọc sạch (FLEURS), 41–52% qua kênh mic board thử (mô phỏng); chờ dữ liệu giọng thật qua mic mũ |
| [SV3](docs/modules/SV3-llm.md) | LLM local + chuyển sang cloud | Server | SV1 | Chạy trên batai qua Ollama dùng chung, đôi lúc quá 10 s; chờ khóa Anthropic để có Claude dự phòng |
| [SV4](docs/modules/SV4-tts.md) | Tổng hợp giọng nói tiếng Việt | Server | SV1 | Chạy trên batai (VieNeu trên CPU, đọc nhanh 1,5 lần) |
| [SV5](docs/modules/SV5-xiaozhi-server.md) | xiaozhi-server: cấu hình & tính cách | Server | SV2–SV4 | Chạy trên batai, phục vụ board thử từ 7/10 |
| [SV6](docs/modules/SV6-helmet-tasks.md) | Plugin tác vụ `tasks.yaml` | Server | SV5, FW4 | Chạy trên batai; board thử nhận và chạy tác vụ qua giọng nói |
| [SV7](docs/modules/SV7-remote-bao-mat.md) | Truy cập từ xa & bảo mật | Server | SV5 | Code xong; chờ cấu hình Cloudflare |
| [IT1](docs/modules/IT1-tich-hop-nghiem-thu.md) | Tích hợp & nghiệm thu | Tích hợp | tất cả | Bộ giả lập + kịch bản xong; chuỗi nói chuyện chạy trên board thử; chờ mũ thật |

## Phần cứng chính

ESP32-S3 N16R8, INMP441 (mic I2S), MAX98357A (amply I2S) + loa 3W 8Ω, LCD 2.4" SPI ST7789 320×240 (module MSP2401), 2× servo MG90S, module LED mắt cosplay (CR2032), PCB đục lỗ 7×9cm, sạc dự phòng 5V ≥2.4A. Module PCM5102 không dùng trong thiết kế hiện tại.

## Mã nguồn mở được dùng

- Firmware: [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) v2.5.0, MIT; ESP-IDF 6.1, LVGL; microWakeWord (B2) dùng `esp-tflite-micro`, `esp-nn`, `esp-micro-speech-features`
- Server: [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) v0.9.7, MIT (chạy image chính thức + bản vá `server/xiaozhi/patches/`)
- ASR: [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper), MIT, model Whisper large-v3
- LLM: [BerriAI/litellm](https://github.com/BerriAI/litellm), MIT; [Ollama](https://github.com/ollama/ollama), MIT; model Qwen3 30B-A3B Instruct, Apache-2.0
- TTS: [pnnbao97/VieNeu-TTS](https://github.com/pnnbao97/VieNeu-TTS), Apache-2.0; [remsky/Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI), Apache-2.0 (tùy chọn)
- Truy cập từ xa: [cloudflared](https://github.com/cloudflare/cloudflared), Apache-2.0
- Font HUD: font giấy phép SIL OFL 1.1 (`firmware/main/boards/jarvis-helmet/FONTS-LICENSE.md`)
