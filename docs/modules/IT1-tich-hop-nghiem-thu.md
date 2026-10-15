# IT1 – Tích hợp & nghiệm thu

| | |
|---|---|
| **Mục tiêu** | Kiểm chứng toàn bộ hệ thống chạy đúng yêu cầu ban đầu khi đội mũ và dùng thật, ở nhà lẫn ngoài trời |
| **Phụ thuộc** | Tất cả module |
| **Kết quả giao nộp** | `tools/tests/e2e/` (kịch bản thử, script đo độ trễ từ log), `docs/test-report.md` (kết quả từng đợt) |

## 1. Cách đo độ trễ
- Ghi lại các mốc thời gian trong log xiaozhi-server, lọc theo session id:
  1. VAD báo người dùng nói xong;
  2. ASR trả kết quả;
  3. LLM ra token đầu tiên;
  4. TTS có âm thanh đầu tiên;
  5. gói audio đầu tiên được gửi xuống thiết bị.
- Script `tools/tests/e2e/latency.py` đọc log, tính trung vị và p90 cho từng chặng.
- Đối chiếu bằng tai: quay video điện thoại khi thử 5 câu, đo từ lúc nói xong đến lúc có tiếng trả lời.

## 2. Kịch bản thử

| # | Kịch bản | Kỳ vọng |
|---|---|---|
| E1 | "Jarvis, mấy giờ rồi?" | Trả lời tiếng Việt, đúng giờ, giọng quản gia |
| E2 | "Jarvis, what's the weather like?" | Trả lời tiếng Anh, hoặc lịch sự báo là không có dữ liệu |
| E3 | "Jarvis, open mask rồi bật đèn mắt lên" | Gọi 2 tác vụ theo đúng thứ tự; mặt nạ mở, đèn sáng; HUD cập nhật |
| E4 | "Mở mặt nạ" (khớp nhanh) | Servo chạy ≤800ms sau khi nói xong (LAN) |
| E5 | Thêm `party_mode` vào YAML rồi ra lệnh | Chạy được, không khởi động lại gì |
| E6 | "Jarvis, hỏi chuyên gia: …" | Gọi `ask_expert`, trả lời ngắn gọn |
| E7 | Bấm PTT trong lúc Jarvis đang nói | Dừng nói ≤300ms, chuyển sang nghe |
| E8 | Gọi "Jarvis" trong lúc Jarvis đang nói | Ngắt lời được (nếu FW7/SV5 bật khử vọng); nếu không thì ghi là giới hạn |
| E9 | Tắt server, bấm nút mặt nạ / nhấn giữ | Mặt nạ và đèn vẫn chạy; HUD hiện OFFLINE |
| E10 | Tắt WiFi nhà (hotspot đang bật) | Tự chuyển sang hotspot ≤30 giây, dùng tiếp qua Cloudflare |
| E11 | Câu hiển thị có đủ dấu tiếng Việt | Không thiếu ký tự |
| E12 | "Hàu ơi, close the mask" | Wake word tiếng Việt hoạt động, mặt nạ đóng |
| E13 | 50 lệnh liên tiếp (script phát file âm thanh) | Không treo, heap của thiết bị không giảm dần (log `esp_get_free_heap_size`) |
| E14 | Đội mũ dùng liên tục 2 giờ bằng sạc dự phòng | Không reset, sạc không tự tắt, VSENSE ≥4.6V |

## 3. Chỉ tiêu tổng hợp

| Chỉ tiêu | Mục tiêu |
|---|---|
| Từ lúc nói xong đến tiếng trả lời đầu tiên (LAN, trung vị) | ≤1.5 giây |
| Như trên, qua 4G | ≤2.5 giây |
| Khớp nhanh: từ lúc nói xong đến khi servo chạy (LAN) | ≤0.8 giây |
| Từ khóa lệnh nhận đúng (ASR) | ≥95% |
| Gọi đúng tool (LLM) | ≥95% |
| Wake word nhận đúng (yên tĩnh) | ≥18/20 mỗi từ |
| Kích nhầm wake word | ≤1 lần/giờ |
| Pin | ≥2 giờ không sự cố |

## 4. Quy trình
1. Mỗi giai đoạn P1–P8 kết thúc bằng một đợt thử con: chỉ chạy các kịch bản liên quan, ghi vào `docs/test-report.md`.
2. Đợt nghiệm thu cuối chạy toàn bộ E1–E14 trên mũ đã lắp hoàn chỉnh (sau HW4).
3. Lỗi tìm được ghi thành GitHub issue, gắn nhãn module (`HW2`, `SV3`…).

## 5. Công việc
- [x] Thêm log mốc thời gian (bản vá `[LAT]` trong xiaozhi-server) và viết `latency.py`
- [x] Bộ giả lập mũ `device_sim.py` (OTA, WebSocket, MCP, Opus) tự chạy và chấm E1–E8, E11–E13; `make_wavs.py` tạo WAV bằng TTS; [ ] ghi giọng thật cho E13
- [x] Viết mẫu `docs/test-report.md` và hướng dẫn từng kịch bản `tools/tests/e2e/scenarios.md`
- [ ] Chạy các đợt thử theo từng giai đoạn và đợt nghiệm thu cuối (cần mũ thật, batai)

## 6. Tiêu chí nghiệm thu
Đạt toàn bộ chỉ tiêu ở mục 3. Kịch bản E1–E14 đều pass, hoặc có ghi rõ lý do giới hạn đã được chấp nhận (ví dụ E8).

## Ghi chú triển khai (10/2026)

**Công cụ** (`tools/tests/e2e/`, hướng dẫn trong README và `scenarios.md`):

| Công cụ | Dùng để |
|---|---|
| `device_sim.py` | Giả lập mũ ở mức giao thức: OTA như firmware, WebSocket v1, MCP server với đúng danh sách tool của firmware (lấy từ `tools/tests/llm/device_tools.json`), Opus 16 kHz lên / giải mã 24 kHz xuống; chạy kịch bản và tự chấm (`--scenario E4`…) |
| `latency.py` | Đọc dòng `[LAT]` trong log server, tính trung vị/p90 từng chặng, so với chỉ tiêu mục 3 (`--check`) |
| `heap_trend.py` | Đọc log serial của mũ (dòng heap in mỗi 10 s), kiểm tra heap không giảm dần trong E13 |
| `make_wavs.py` | Tạo WAV đầu vào bằng TTS hoặc chuyển đổi bản ghi |

Kiểm tra bổ sung so với bảng mục 2: E4 bỏ qua lệnh `self.sequence.save` lúc kết nối khi đo "lệnh đầu tiên"; E12 yêu cầu
server **không trả lời riêng** wake word (`wake_word_silent`), nếu không là "Quản gia" chưa có trong `wakeup_words`.

**Kết quả trên máy dev** (Docker Desktop, GTX 1660 6 GB, Whisper small thay large-v3, model 4B thay 30B; chỉ để kiểm tra
chức năng, chưa phải nghiệm thu):

| Kịch bản | Kết quả |
|---|---|
| E1, E3, E4 (chữ và PTT), E12, E13 (8 lượt) | Đạt (10/2026, sau các bản sửa review) |
| E4 PTT | Nói xong → lệnh MCP 788 ms (≤800) |
| Khớp nhanh (trung vị nhiều lượt) | 644 ms |
| E3 | Hai tác vụ gộp thành một `self.sequence.run` (D4); bước LLM rất chậm vì model 4B chạy một phần trên CPU |

Độ trễ ASR/LLM/tổng không đạt trên máy dev (dự kiến: GPU yếu). Phải đo lại trên batai (RTX 5090) và với mũ thật.

**Giới hạn đã biết:** E8 (ngắt lời bằng wake word) – khử vọng phía server của v0.9.7 chỉ chạy với MQTT, mũ dùng WebSocket nên
mặc định tắt nhận wake word khi đang nói (D12); dùng nút PTT (E7). E9, E10, E14 chỉ làm được trên mũ thật.
