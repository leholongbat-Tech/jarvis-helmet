# Jarvis Helmet

Mũ giáp kiểu Iron Man có trợ lý ảo **Jarvis**:
- nghe hiểu **tiếng Việt và tiếng Anh, kể cả trộn trong cùng một câu**;
- đánh thức bằng giọng nói ("Jarvis", "Quản gia");
- trả lời bằng giọng nói;
- điều khiển 2 servo nâng mặt nạ và đèn mắt;
- chạy các tác vụ tùy biến khai báo trong file YAML;
- hiển thị HUD trên màn hình 240×240.

> Phạm vi repo: **phần điện điều khiển + firmware + server AI**. Phần cơ khí làm riêng.
>
> Trạng thái: **đang lập kế hoạch**, chưa có code.

## Kiến trúc

```
┌──────────── Mũ (ESP32-S3 N16R8) ────────────┐        ┌──────── Server Ubuntu (RTX 5090) ────────┐
│ INMP441 ─► AFE ─► wake word "Jarvis"        │  WSS   │ cloudflared ─► xiaozhi-server            │
│ Opus 16 kHz ───────────────────────────────────────► │   ├─ ASR  faster-whisper large-v3        │
│ ◄──────────────────────────── Opus 24 kHz (TTS)      │   ├─ LLM  LiteLLM → Ollama (local)/cloud │
│ MAX98357 ─► loa 3W                          │        │   ├─ TTS  VieNeu-TTS                     │
│ MCP tools: mask · eyes · output · sequence  │ ◄───── │   └─ plugin helmet_tasks (tasks.yaml)    │
│ ST7789 HUD · nút PTT · nút mặt nạ (offline) │        └──────────────────────────────────────────┘
└─────────────────────────────────────────────┘
```

## Tài liệu kế hoạch

- [docs/PLAN.md](docs/PLAN.md): tổng quan, các quyết định đã chốt, lộ trình, rủi ro
- [docs/interfaces.md](docs/interfaces.md): giao tiếp giữa các module (MCP tools, định dạng chuỗi bước, `tasks.yaml`)

### Danh sách module

| ID | Module | Nhóm | Phụ thuộc |
|---|---|---|---|
| [HW1](docs/modules/HW1-nguon-dien.md) | Nguồn & phân phối điện | Phần cứng | – |
| [HW2](docs/modules/HW2-dau-noi-tin-hieu.md) | Bảng chân ESP32-S3 & đấu nối tín hiệu | Phần cứng | HW1 |
| [HW3](docs/modules/HW3-mach-cong-suat.md) | Mạch công suất: servo, đèn mắt, output dự phòng | Phần cứng | HW1, HW2 |
| [HW4](docs/modules/HW4-lap-rap-pcb.md) | Bố trí & hàn PCB 7×9, đi dây trong mũ | Phần cứng | HW1–HW3, FW1 |
| [FW1](docs/modules/FW1-board-bringup.md) | Board `jarvis-helmet`: khởi động phần cứng | Firmware | HW2 |
| [FW2](docs/modules/FW2-mask-controller.md) | Điều khiển mặt nạ (2 servo) | Firmware | FW1, HW3 |
| [FW3](docs/modules/FW3-output-controller.md) | Đèn mắt & output dự phòng | Firmware | FW1, HW3 |
| [FW4](docs/modules/FW4-sequence-mcp.md) | Sequence runner & MCP tools | Firmware | FW2, FW3 |
| [FW5](docs/modules/FW5-nut-bam-offline.md) | Nút bấm & chế độ offline | Firmware | FW4 |
| [FW6](docs/modules/FW6-hud-display.md) | HUD Iron Man & font tiếng Việt | Firmware | FW1 |
| [FW7](docs/modules/FW7-wake-word.md) | Wake word "Jarvis" + "Quản gia" | Firmware | FW1, SV4 |
| [SV1](docs/modules/SV1-ha-tang.md) | Hạ tầng server (GPU, Docker) | Server | – |
| [SV2](docs/modules/SV2-asr.md) | Nhận dạng giọng nói Việt–Anh | Server | SV1 |
| [SV3](docs/modules/SV3-llm.md) | LLM local + chuyển sang cloud | Server | SV1 |
| [SV4](docs/modules/SV4-tts.md) | Tổng hợp giọng nói tiếng Việt | Server | SV1 |
| [SV5](docs/modules/SV5-xiaozhi-server.md) | xiaozhi-server: cấu hình & tính cách | Server | SV2–SV4 |
| [SV6](docs/modules/SV6-helmet-tasks.md) | Plugin tác vụ `tasks.yaml` | Server | SV5, FW4 |
| [SV7](docs/modules/SV7-remote-bao-mat.md) | Truy cập từ xa & bảo mật | Server | SV5 |
| [IT1](docs/modules/IT1-tich-hop-nghiem-thu.md) | Tích hợp & nghiệm thu | Tích hợp | tất cả |

## Phần cứng chính

ESP32-S3 N16R8, INMP441 (mic I2S), MAX98357A (amply I2S) + loa 3W 8Ω, LCD ST7789 1.54" 240×240, 2× servo MG90S, module LED mắt cosplay (CR2032), PCB đục lỗ 7×9cm, sạc dự phòng 5V ≥2.4A. Module PCM5102 không dùng trong thiết kế hiện tại.

## Mã nguồn mở được dùng

- Firmware: [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) v2.5.0, MIT
- Server: [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) v0.9.7, MIT
- TTS: [pnnbao97/VieNeu-TTS](https://github.com/pnnbao97/VieNeu-TTS), Apache-2.0
- ASR: [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper), MIT
