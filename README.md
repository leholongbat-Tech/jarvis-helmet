# Jarvis Helmet

Mũ giáp kiểu Iron Man có trợ lý ảo **Jarvis**:
- nghe hiểu **tiếng Việt và tiếng Anh, kể cả trộn trong cùng một câu**;
- đánh thức bằng giọng nói ("Jarvis"; "Quản gia" ở biến thể firmware B1/B2) hoặc nút bấm (PTT);
- trả lời bằng giọng nói, giọng nam tiếng Việt (VieNeu-TTS), tiếng Anh tùy chọn bằng Kokoro;
- điều khiển 2 servo nâng mặt nạ, đèn mắt và một kênh output dự phòng;
- chạy các tác vụ tùy biến khai báo trong file YAML, kể cả khi mất mạng (nút bấm);
- hiển thị HUD trên màn hình 240×240.

> Phạm vi repo: **phần điện điều khiển + firmware + server AI**. Phần cơ khí làm riêng.

## Trạng thái

Mã nguồn của mọi module đã xong và đã kiểm thử trên máy dev: test firmware chạy trên máy tính, build đủ 4 biến thể firmware
(ESP-IDF 6.1), toàn bộ test Python (cũng chạy trong container Linux như CI), và smoke test cả stack server bằng Docker Desktop
với bộ giả lập mũ (`tools/tests/e2e/device_sim.py`).

**Ngày 7/10/2026:** server đã chạy trên `batai` ở chế độ dùng chung GPU (LLM qua Ollama sẵn có của máy, ASR/TTS trên
CPU – `server/README.md` mục 3.6); firmware đã nạp vào board thử (ESP32-S3 N16R8 + mic + loa), board vào Wi-Fi và nhận
server qua LAN. Mic của board thử chưa gửi dữ liệu (firmware dò thấy đường SD luôn ở mức thấp), nên chưa đánh thức được: cần
kiểm tra nguồn và dây mic theo [board README mục 8.1](firmware/main/boards/jarvis-helmet/README.md).

**Chưa làm:** lắp mũ hoàn chỉnh và đo đạc (HW1–HW4, servo/đèn, hiệu chỉnh, độ trễ thật), GPU riêng cho Jarvis (cần VRAM
trống và NVIDIA Container Toolkit), Cloudflare Tunnel + Access và khóa Anthropic, huấn luyện wake word "Quản gia", thu
dữ liệu giọng thật cho ASR/wake word. Kết quả nghiệm thu ghi vào [docs/test-report.md](docs/test-report.md).

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
| [FW1](docs/modules/FW1-board-bringup.md) | Board `jarvis-helmet`: khởi động phần cứng | Firmware | HW2 | Code xong, build OK; chờ mũ thật |
| [FW2](docs/modules/FW2-mask-controller.md) | Điều khiển mặt nạ (2 servo) | Firmware | FW1, HW3 | Code xong, test máy tính OK; chờ hiệu chỉnh |
| [FW3](docs/modules/FW3-output-controller.md) | Đèn mắt & output dự phòng | Firmware | FW1, HW3 | Code xong; chờ mũ thật |
| [FW4](docs/modules/FW4-sequence-mcp.md) | Sequence runner & MCP tools | Firmware | FW2, FW3 | Code xong, test máy tính OK |
| [FW5](docs/modules/FW5-nut-bam-offline.md) | Nút bấm & chế độ offline | Firmware | FW4 | Code xong; chờ mũ thật |
| [FW6](docs/modules/FW6-hud-display.md) | HUD Iron Man & font tiếng Việt | Firmware | FW1 | Code xong, font đủ glyph; chờ màn hình thật |
| [FW7](docs/modules/FW7-wake-word.md) | Wake word "Jarvis" + "Quản gia" | Firmware | FW1, SV4 | 3 phương án đã build; "Quản gia" (B2) chờ huấn luyện |
| [SV1](docs/modules/SV1-ha-tang.md) | Hạ tầng server (GPU, Docker) | Server | – | Script xong; chờ quyền sudo trên batai |
| [SV2](docs/modules/SV2-asr.md) | Nhận dạng giọng nói Việt–Anh | Server | SV1 | Code xong; chờ dữ liệu giọng thật để đo WER |
| [SV3](docs/modules/SV3-llm.md) | LLM local + chuyển sang cloud | Server | SV1 | Code xong; chờ khóa Anthropic |
| [SV4](docs/modules/SV4-tts.md) | Tổng hợp giọng nói tiếng Việt | Server | SV1 | Code xong, đã chạy thử |
| [SV5](docs/modules/SV5-xiaozhi-server.md) | xiaozhi-server: cấu hình & tính cách | Server | SV2–SV4 | Code xong, smoke test OK |
| [SV6](docs/modules/SV6-helmet-tasks.md) | Plugin tác vụ `tasks.yaml` | Server | SV5, FW4 | Code xong, smoke test OK |
| [SV7](docs/modules/SV7-remote-bao-mat.md) | Truy cập từ xa & bảo mật | Server | SV5 | Code xong; chờ cấu hình Cloudflare |
| [IT1](docs/modules/IT1-tich-hop-nghiem-thu.md) | Tích hợp & nghiệm thu | Tích hợp | tất cả | Bộ giả lập + kịch bản xong; chờ mũ thật |

## Phần cứng chính

ESP32-S3 N16R8, INMP441 (mic I2S), MAX98357A (amply I2S) + loa 3W 8Ω, LCD ST7789 1.54" 240×240, 2× servo MG90S, module LED mắt cosplay (CR2032), PCB đục lỗ 7×9cm, sạc dự phòng 5V ≥2.4A. Module PCM5102 không dùng trong thiết kế hiện tại.

## Mã nguồn mở được dùng

- Firmware: [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) v2.5.0, MIT; ESP-IDF 6.1, LVGL; microWakeWord (B2) dùng `esp-tflite-micro`, `esp-nn`, `esp-micro-speech-features`
- Server: [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) v0.9.7, MIT (chạy image chính thức + bản vá `server/xiaozhi/patches/`)
- ASR: [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper), MIT, model Whisper large-v3
- LLM: [BerriAI/litellm](https://github.com/BerriAI/litellm), MIT; [Ollama](https://github.com/ollama/ollama), MIT; model Qwen3 30B-A3B Instruct, Apache-2.0
- TTS: [pnnbao97/VieNeu-TTS](https://github.com/pnnbao97/VieNeu-TTS), Apache-2.0; [remsky/Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI), Apache-2.0 (tùy chọn)
- Truy cập từ xa: [cloudflared](https://github.com/cloudflare/cloudflared), Apache-2.0
- Font HUD: font giấy phép SIL OFL 1.1 (`firmware/main/boards/jarvis-helmet/FONTS-LICENSE.md`)
