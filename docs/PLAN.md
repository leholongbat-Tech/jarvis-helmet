# Kế hoạch tổng thể – Jarvis Helmet

## 1. Bối cảnh và mục tiêu

Mục tiêu là làm một mũ giáp kiểu Iron Man có trợ lý ảo chạy chủ yếu trên server AI tại nhà. Trợ lý cần:

1. Nhận dạng tiếng Việt và tiếng Anh **trộn trong cùng một câu** (ví dụ: "Jarvis, open mask rồi bật đèn mắt lên").
2. Thức dậy khi nghe "Jarvis" hoặc "Quản gia".
3. Trả lời bằng giọng nói, theo phong cách một quản gia lịch lãm.
4. Điều khiển 2 servo nâng mặt nạ, bật/tắt đèn mắt và một output dự phòng.
5. Chạy các tác vụ tùy biến (mở mặt nạ, đóng mặt nạ, chế độ chiến đấu…). Thêm tác vụ mới **không cần nạp lại firmware**.
6. Hiển thị trạng thái, câu hội thoại và HUD trên màn 2.4" 320×240 (ST7789, nằm ngang).

Không thuộc phạm vi: thiết kế cơ khí (khung, bản lề, vỏ mũ).

## 2. Các quyết định đã chốt

| Hạng mục | Quyết định |
|---|---|
| Server | Ubuntu, RTX 5090 32GB, RAM 128GB, Docker |
| Mạng | Ưu tiên WiFi nhà. Khi ra ngoài, bắt hotspot điện thoại và kết nối qua **Cloudflare Tunnel (WSS)** |
| LLM | Chạy local mặc định, chuyển sang cloud khi cần. Nhà cung cấp cloud đổi được bằng file cấu hình (LiteLLM) |
| Ngôn ngữ | Trộn Việt–Anh trong cùng một câu |
| Wake word | Chạy trên ESP32. "Jarvis" dùng model ESP-SR có sẵn; "Quản gia" phải làm thêm (xem FW7) |
| Firmware | Dựa trên **xiaozhi-esp32 v2.5.0** (ESP-IDF ≥6.0.1, khuyên dùng 6.1) |
| Server phần mềm | **xiaozhi-esp32-server v0.9.7**, bản Python chế độ tối giản; thay các module ASR, LLM, TTS |
| Servo | Cả 2 servo cùng nâng mặt nạ, chạy đồng bộ, servo phải đảo chiều |
| Đèn mắt | Module LED cosplay chạy pin CR2032 (một hộp pin, 2 LED song song). 2 mắt chung 1 kênh và 1 điện trở hạn dòng, thêm 1 kênh dự phòng |
| Nút bấm | Nút nói (PTT) và nút mở/đóng mặt nạ. Nút mặt nạ vẫn hoạt động khi mất mạng |
| Nguồn | Sạc dự phòng 5V, dòng ra ≥2.4A |
| Màn hình | Trạng thái, câu hội thoại, HUD thông tin, giao diện kiểu Iron Man |
| Tác vụ | Ra lệnh bằng ngôn ngữ tự nhiên. Tác vụ khai báo trong `tasks.yaml`, có thêm các câu lệnh khớp nhanh |
| Ngắt lời | Bằng nút PTT (tin cậy) và bằng wake word (thử nghiệm) |
| Tính cách | Quản gia lịch lãm, gọi người dùng là "thưa ngài" / "Sir", giọng nam trầm |
| Bản vẽ điện | Bảng đấu dây + hình minh họa SVG/HTML |

## 3. Cấu trúc repo

```
jarvis-helmet/
├─ README.md
├─ requirements-dev.txt       ← thư viện để chạy toàn bộ test Python
├─ docs/
│  ├─ PLAN.md                 ← file này
│  ├─ interfaces.md           ← giao tiếp giữa firmware và server
│  ├─ modules/                ← kế hoạch chi tiết từng module
│  ├─ hardware/               ← bảng đấu dây, BOM, hình SVG (HW1–HW4)
│  ├─ hud.md  wakeword-report.md  test-report.md
├─ firmware/                  ← xiaozhi-esp32 v2.5.0 đưa thẳng vào repo + board jarvis-helmet (FW1–FW7)
│  ├─ main/boards/jarvis-helmet/
│  └─ tests/host/             ← test C++ chạy trên máy tính (Catch2)
├─ server/                    ← docker compose + các service (SV1–SV7)
│  ├─ docker-compose.yml  docker-compose.local.yml  docker-compose.debug.yml  .env.example
│  ├─ xiaozhi/                ← Dockerfile mỏng dựa trên image chính thức + bản vá + config + plugin
│  ├─ asr/  litellm/  ollama/  tts/  cloudflared/
│  ├─ scripts/                ← chuẩn bị host, triển khai, firewall, tải model
│  ├─ tasks/tasks.yaml
│  └─ tests/
└─ tools/
   ├─ fw_build_docker.sh  provision_secrets.py  servo_calibrate.py  tasks_cli.py
   ├─ fonts/  hardware/
   ├─ wakeword/               ← train microWakeWord "Quản gia" (FW7)
   └─ tests/                  ← giả lập mũ, đo độ trễ, bộ câu ASR/TTS/LLM, fixture chuỗi bước (IT1)
```

Firmware được **đưa thẳng mã nguồn upstream vào repo** chứ không dùng submodule:
- Commit đầu tiên là `vendor: xiaozhi-esp32 v2.5.0`, chép nguyên bản upstream.
- Các commit sau chỉ chứa thay đổi của dự án.
- Nhờ vậy `git diff` thấy rõ phần tự viết, và khi nâng cấp upstream chỉ cần áp lại các commit đó.

## 4. Lộ trình

| Giai đoạn | Module | Kết quả nghiệm thu |
|---|---|---|
| **P0** Chuẩn bị | SV1, mua linh kiện (HW1–HW3 BOM) | Môi trường ESP-IDF 6.1 trên Windows. Server chạy được container có GPU |
| **P1** Thử phần cứng trên breadboard | HW2, FW1 | Mic, loa, LCD chạy với firmware gốc; nói chuyện được với server mặc định |
| **P2** Dựng server AI | SV2, SV3, SV4, SV5 | Hội thoại Việt–Anh qua LAN với server nhà |
| **P3** Cơ cấu chấp hành | HW1, HW3, FW2, FW3, FW4 | Ra lệnh bằng giọng nói để mở/đóng mặt nạ, bật đèn |
| **P4** Tác vụ tùy biến | SV6, FW5 | Thêm tác vụ YAML không cần nạp lại firmware; nút bấm chạy offline |
| **P5** Giao diện | FW6 | HUD Iron Man, hiện chữ tiếng Việt có dấu |
| **P6** Lắp ráp | HW4 | Mạch trên PCB 7×9, đi dây trong mũ |
| **P7** Wake word | FW7 | Gọi được bằng cả "Jarvis" và "Quản gia" |
| **P8** Dùng ngoài trời & nghiệm thu | SV7, IT1 | Dùng qua 4G, đạt các chỉ tiêu ở IT1 |

Thứ tự phụ thuộc:

```
SV1 ─► SV2 ─┐
     ├► SV3 ─┼► SV5 ─► SV6 ─► SV7 ──────────┐
     └► SV4 ─┘                               │
HW1 ─► HW2 ─► FW1 ─┬► FW2 ─┐                 ├► IT1
         └─► HW3 ──┤       ├► FW4 ─► FW5 ────┤
                   ├► FW3 ─┘                 │
                   ├► FW6 ───────────────────┤
                   └► FW7 (cần SV4 tạo dữ liệu) ┘
HW1..HW3 + FW1 ─► HW4
```

## 5. Rủi ro chính

| Rủi ro | Ảnh hưởng | Cách xử lý (trạng thái) |
|---|---|---|
| Server v0.9.7 không tương thích firmware v2.5.0 | Không kết nối được | Ghim phiên bản cả hai. Đã chạy thử giao thức đầy đủ bằng bộ giả lập mũ (OTA, WebSocket, MCP, Opus); còn phải thử với mũ thật |
| ASR nhận sai câu trộn Việt–Anh | Hiểu sai lệnh | Câu mồi từ vựng, hotword, câu khớp nhanh, bộ lọc ảo giác, công cụ so sánh A/B (SV2). Cần thu giọng thật để đo WER |
| Mic và loa cùng nằm trong mũ, gây tiếng vọng | Ngắt lời bằng giọng nói kém | Nút PTT luôn ngắt được. Khử vọng phía server của v0.9.7 chỉ có với MQTT, nên ngắt bằng giọng nói mặc định tắt (D12) |
| Servo kéo dòng làm sụt áp, ESP32 tự reset | Mũ khởi động lại giữa chừng | Tách nhánh nguồn, tụ lớn, servo chạy mượt có gia tốc, không giật khi khởi động (HW1, FW2) |
| Wake word "Quản gia" kích nhầm hoặc không nhận | Khó gọi trợ lý | 3 phương án A/B1/B2, chỉnh ngưỡng; còn nút PTT dự phòng (FW7, `docs/wakeword-report.md`) |
| Sạc dự phòng tự tắt khi tải nhỏ | Mất nguồn | Không dùng deep sleep; thử chạy liên tục 2 giờ (IT1) |
| Máy chủ dùng chung với dự án khác (cổng 8000, GPU, dockerd) | Làm hỏng dịch vụ khác | Chỉ mở cổng 8100/8003, GPU qua CDI không cần khởi động lại dockerd, không bật ufw, script mặc định chạy thử (SV1) |
| GPU lộ ra internet qua tunnel | Người lạ dùng ké, tốn tiền cloud | Cloudflare Access (service token trong NVS của mũ) là cổng chặn từ Internet; ngân sách cloud theo tháng trong LiteLLM; mạng Docker tách riêng (SV7). OTA cấp token cho bất kỳ ai trong LAN: LAN được coi là tin cậy (D14) |

## 6. Quy ước

- Tên tool MCP dùng tiền tố `self.` (quy ước của xiaozhi), ví dụ `self.mask.open`.
- Mỗi module có mục **Tiêu chí nghiệm thu**. Module chỉ coi là xong khi đạt hết các tiêu chí đó.
- Phiên bản phần mềm bên ngoài ghi trong module tương ứng. Tên model AI (LLM, ASR) cần kiểm tra lại bản mới nhất lúc triển khai.

## 7. Quyết định khi triển khai (D1–D33)

Các quyết định chốt khi viết code, bổ sung hoặc điều chỉnh kế hoạch ban đầu. Mục nào ảnh hưởng tới giao tiếp firmware ↔ server
cũng có trong `docs/interfaces.md`.

| | Hạng mục | Quyết định |
|---|---|---|
| D1 | LLM cloud | `jarvis-cloud` = `anthropic/claude-opus-5-5`; không gửi `temperature`/`top_p` (model này không nhận); giá $4/$20 mỗi triệu token để tính ngân sách. Đổi sang `claude-sonnet-5-5`, `claude-haiku-4-5`, OpenAI hay Gemini bằng `.env` (`server/README.md` mục 10) |
| D2 | LLM local | Ollama `qwen3:30b-a3b-instruct-2507-q4_K_M` (19 GB, không "thinking", gọi được tool), context 8192, giữ model trong VRAM, 1 luồng; đổi bằng `OLLAMA_MODEL` |
| D3 | Âm thanh xuống | Bản vá để `hello` của server báo đúng 24 kHz |
| D4 | Nhiều tool một lượt | Các `task_*` của một câu trả lời được gộp thành một `self.sequence.run`, mỗi lệnh vẫn nhận kết quả riêng |
| D5 | Câu đáp cố định | Ngoài lượt hội thoại (khớp nhanh) dùng cách `speak_txt` của upstream; trong lượt thì xếp hàng câu nói |
| D6 | NVS | Partition riêng `helmet` (hiệu chỉnh, cài đặt, chuỗi đã lưu, `mask_pos`) và `jsecrets` (bí mật) |
| D7 | Hiệu chỉnh servo | Server không lấy tool user-only, nên hiệu chỉnh qua WebSocket MCP cục bộ của mũ + `tools/servo_calibrate.py` |
| D8 | Nút PTT | Nhấn nhanh khi mũ đang nói: dừng và xóa âm thanh đang phát rồi nghe; giữ = nói (ngưỡng 400 ms) |
| D9 | Offline | Nút bấm điều khiển trực tiếp controller/runner (không qua hàng đợi của luồng chính); huy hiệu OFFLINE theo Wi-Fi và lỗi kết nối |
| D10 | HUD & font | HUD vẽ đè lên giao diện gốc; font tiếng Việt sinh bằng `lv_font_conv` 1.5.3; mức âm thanh lấy từ codec; trạng thái "đang nghĩ" suy ra từ câu STT |
| D11 | Wake word | A (mặc định): `wn9_jarvis_tts`; B1: MultiNet ("jia wei si", "guan jia"); B2: microWakeWord tự huấn luyện (`CONFIG_JARVIS_MICRO_WAKE_WORD`) |
| D12 | Ngắt bằng wake word | Mặc định tắt khi mũ đang nói (`CONFIG_JARVIS_WAKE_WORD_BARGE_IN=n`): không có khử vọng, giọng của chính mũ có thể kích hoạt |
| D13 | Cloudflare Access | Header theo host, áp cho OTA, tải firmware và WebSocket; bí mật trong NVS `jsecrets`, nạp bằng `tools/provision_secrets.py` |
| D14 | Cấu hình server | `.config.yaml` sinh từ template khi container khởi động, bí mật lấy từ biến môi trường; bật xác thực token; múi giờ +7; che header bí mật trong log |
| D15 | Wake/exit word | So khớp sau khi bỏ khoảng trắng, liệt kê đủ biến thể; `enable_greeting: false` và server không trả lời riêng wake word (bản vá `wake_no_echo`) |
| D16 | Prompt | Tính cách quản gia Jarvis, có `{{current_time}}`, không emoji |
| D17 | Chuỗi tiếng Trung | Mọi câu nói/hiển thị cứng bằng tiếng Trung của upstream được bản vá đổi sang tiếng Việt |
| D18 | TTS | `jarvis_stream` (stream PCM 24 kHz) → `tts-router` (chuẩn hóa số/đơn vị/viết tắt, chọn engine từng câu) → VieNeu-TTS, hoặc Kokoro cho câu tiếng Anh (tùy chọn) |
| D19 | ASR | faster-whisper large-v3 float16 trong service riêng, thu dữ liệu tùy chọn (`ASR_SAVE_DIR`), engine B Qwen3-ASR qua vLLM (tùy chọn) |
| D20 | GPU | Compose xin GPU qua CDI (`nvidia.com/gpu=all`), không cần khởi động lại dockerd |
| D21 | Cổng | Cổng 8000 của host thuộc dự án khác: chỉ mở WebSocket 8100 và OTA 8003; mọi service khác nội bộ |
| D22 | Cloudflare | Tunnel quản lý trên dashboard (`TUNNEL_TOKEN`), định tuyến theo đường dẫn; không có token → 302 |
| D23 | Lỗi chuỗi bước | `invalid step #n`, n đếm từ 1 ở cả hai phía |
| D24 | LED trạng thái | GPIO48 theo HW2 (DevKitC-1 v1.1 dùng GPIO38) |
| D25 | LiteLLM | v1.104.0, Redis lưu chi tiêu, dự phòng `jarvis-main → jarvis-cloud`, timeout 15 s, 1 lần thử lại, ngân sách tháng `CLOUD_MONTHLY_BUDGET_USD` |
| D26 | Hiệu ứng mắt | `duration_ms` 0 = thời lượng mặc định của hiệu ứng (breathe 2500, flicker 600, còn lại 1000) |
| D27 | MCP cục bộ | Token tùy chọn (`mcp_token` trong NVS), danh sách tool được phép, giới hạn độ sâu JSON |
| D28 | VAD | `VAD_MIN_SILENCE_MS` (mặc định 700); mục tiêu 800 ms của khớp nhanh tính cho chế độ PTT |
| D29 | Regex server | Id tác vụ và tên âm thanh kiểm tra bằng `fullmatch` như firmware |
| D30 | Thẻ HUD | Tiêu đề được rỗng |
| D31 | Header Cloudflare | Chỉ gửi qua https/wss tới đúng host đã đăng ký |
| D32 | Dashboard | Trang trạng thái tùy chọn (profile `dashboard`): ngoại lệ duy nhất của D21, cổng 8090 trong LAN, mật khẩu bắt buộc (HTTP Basic); chỉ đọc log xiaozhi, `/health` của các service và tool trạng thái của mũ (`server/README.md` mục 3.7) |
| D33 | Wake word tiếng Việt | "Hàu ơi" (người dùng chọn, 10/2026) thay "Quản gia" ở bản `jarvis-helmet-mww`; train chỉ bằng giọng TTS VieNeu (25 giọng, đưa vào trainer bằng `--own-tts`), trên CPU batai 12 nhân vì GPU bận và chưa có NVIDIA Container Toolkit. Không dùng giọng Piper vivos/25hours (mất dấu thanh). "hầu/hậu/hào ơi" không làm mẫu âm (gần đồng âm, ASR cũng nhầm), nên có thể đánh thức mũ. Model không commit (giấy phép dataset), server nhận "Hàuơi" và các cách ASR viết nhầm là wake word (`tools/wakeword/README.md` mục 9) |

Bổ sung sau các vòng review: mạng Docker tách theo nhóm service (Redis, Ollama chỉ LiteLLM gọi được), khóa nhà cung cấp cloud
chỉ đưa vào container LiteLLM dưới tên riêng (chặn đường pass-through vượt ngân sách), entrypoint từ chối khóa yếu,
`deploy.sh` tự nạp lại cấu hình LiteLLM/Ollama khi đổi, bản sao lưu không chứa cấu hình có bí mật.
