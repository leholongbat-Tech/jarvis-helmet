# Báo cáo kiểm thử – Jarvis Helmet

Mẫu báo cáo cho IT1 (`docs/modules/IT1-tich-hop-nghiem-thu.md`). Mỗi giai đoạn P1–P8 kết thúc bằng một **đợt thử con** (điền bảng của
giai đoạn đó, mục 3); đợt nghiệm thu cuối (sau HW4) điền mục 4, 5 và kết luận. Quy trình từng kịch bản và lệnh đo:
`tools/tests/e2e/scenarios.md`; công cụ: `tools/tests/e2e/README.md`, `tools/tests/asr/README.md`, `tools/tests/tts/README.md`,
`docs/wakeword-report.md`.

**Ký hiệu cột KQ**: `ĐẠT` · `KHÔNG ĐẠT` · `GIỚI HẠN` (không đạt nhưng đã chấp nhận, ghi lý do – ví dụ E8) · `CHƯA THỬ` · `N/A`.

**Quy ước điền**:
- "Kết quả đo" ghi số liệu thật (ms, %, số lần/20…), không ghi "ok".
- "Bằng chứng": tên file/đường dẫn (video, ảnh, `summary.json` của `device_sim.py`, CSV của `latency.py`, log serial…). Bằng chứng lưu
  ngoài repo; không đưa giọng nói, log có khóa hay ảnh có thông tin cá nhân vào repo.
- Không ghi khóa, token, mật khẩu vào báo cáo (chỉ ghi "có/không", tên biến).
- Lỗi tìm được: thêm một dòng vào mục 6 và tạo issue gắn nhãn module (`HW2`, `SV3`, `FW5`…).

---

## 0. Lịch sử các đợt thử

| Đợt | Ngày | Giai đoạn | Người thử | Firmware (commit / biến thể) | Server (commit) | Tóm tắt kết quả |
|---|---|---|---|---|---|---|
| 1 | … | P… | … | … / `jarvis-helmet` | … | … |

---

## 1. Thông tin đợt thử

| Mục | Giá trị |
|---|---|
| Đợt / giai đoạn | … |
| Ngày, giờ bắt đầu – kết thúc | … |
| Người thử | … |
| Địa điểm, môi trường âm thanh | (phòng yên tĩnh / có nhạc nền / ngoài đường) |
| Mạng | (Wi-Fi nhà LAN / hotspot 4G qua Cloudflare) – tên nhà mạng nếu 4G |
| Người đội mũ / người nói | … |

## 2. Môi trường và phiên bản

Lấy thông tin: `git rev-parse --short HEAD`; `cd server && docker compose images` (tag + image id);
`docker compose exec ollama ollama list`; `/health` của ASR và tts-router (chạy kèm `docker-compose.debug.yml`:
`curl -s http://127.0.0.1:9000/health`, `curl -s http://127.0.0.1:8880/health`); `nvidia-smi`; log khởi động của mũ
(`Board: UUID=... SKU=jarvis-helmet` và dòng phiên bản trên màn).

### 2.1 Firmware và phần cứng

| Mục | Giá trị |
|---|---|
| Commit repo (firmware) | … |
| Biến thể build (`--name`) | `jarvis-helmet` / `jarvis-helmet-7pin` / `jarvis-helmet-multinet` / `jarvis-helmet-mww` |
| Wake word (FW7) | A `wn9_jarvis_tts` / B1 MultiNet / B2 microWakeWord (model: …) |
| `--language` | `vi-VN` |
| ESP-IDF | v6.1 (Docker `espressif/idf:v6.1` / EIM) |
| Option Kconfig khác mặc định | (ví dụ `CONFIG_JARVIS_WAKE_WORD_BARGE_IN=y`, `CONFIG_OTA_URL=...`) |
| Cách nạp | `idf.py flash` / `esptool write-flash` theo board README / OTA |
| Module ESP32-S3 | N16R8 (…) |
| Màn hình | ST7789 240×240 8 chân / 7 chân |
| Mic / amply / loa | INMP441 / MAX98357A / … |
| Servo ×2 | (model) |
| Đèn mắt, output dự phòng | … |
| Mạch | breadboard / PCB 7×9 (bản …) |
| Nguồn | sạc dự phòng (model, dòng ra) / USB máy tính |
| Hiệu chỉnh servo | S0 đóng/mở: … / … ; S1 đóng/mở: … / … |

### 2.2 Server

| Mục | Giá trị |
|---|---|
| Máy chủ | Ubuntu …, GPU …, driver …, RAM … |
| Docker Engine / Compose | … / … |
| Commit repo (server) | … |
| `xiaozhi` | `jarvis/xiaozhi:<tag>` (FROM `xiaozhi-esp32-server:server_0.9.7`), image id … |
| `asr` | `jarvis/asr:<tag>`; engine …, model …, revision …, language …, prompt có/không, compute_type … |
| `tts-router` / `vieneu` | `jarvis/tts-router:<tag>` / `jarvis/vieneu:<tag>` (VieNeu-TTS `app-v0.18.9`); `TTS_VOICE` = … |
| `kokoro` (nếu dùng) | `kokoro-fastapi-gpu:v0.9.0-cu128`; `TTS_ENGLISH_ENGINE` = …, giọng … |
| `ollama` / model local | `ollama/ollama:0.35.1` / `OLLAMA_MODEL` = … |
| `litellm` / model cloud | `litellm:v1.104.0` / `CLOUD_MODEL` = …, ngân sách tháng … USD |
| `asr-qwen` (nếu dùng) | `vllm/vllm-openai:v0.31.0-cu129` |
| `cloudflared` (nếu dùng) | `cloudflare/cloudflared:2026.10.0`; tunnel … |
| OTA URL của mũ / `PUBLIC_WS_URL` | `http://<LAN>:8003/xiaozhi/ota/` hoặc `https://jarvis.<domain>/...` / `ws://…` hoặc `wss://…` |
| VRAM khi đủ model đã nạp | … GB (`nvidia-smi`) |

### 2.3 Công cụ thử

| Mục | Giá trị |
|---|---|
| Python (máy chạy công cụ) | 3.12.… |
| `device_sim.py` | phiên bản `1.0.0` (trường `version` trong `summary.json`) |
| Gói (`tools/tests/e2e/requirements.txt`) | … |

---

## 3. Đợt thử con theo giai đoạn

Mỗi bảng gồm tiêu chí nghiệm thu của các module trong giai đoạn và các kịch bản E liên quan (gợi ý ở cuối
`tools/tests/e2e/scenarios.md`). Thêm dòng nếu cần.

### P1 – Thử phần cứng trên breadboard (HW2, FW1)

| Mã | Bài thử / tiêu chí | Cách thử | Kết quả đo | KQ | Bằng chứng | Ghi chú |
|---|---|---|---|---|---|---|
| HW2 #1 | Firmware gốc khởi động, LCD đúng màu/hướng, không lệch ảnh | Quan sát | | | | |
| HW2 #2 | Đánh thức "Jarvis" ở 30 cm, phòng yên tĩnh | Thử 20 lần | …/20 | | | |
| HW2 #3 | Loa rõ, không rè ở âm lượng 70 % | Nghe | | | | |
| HW2 #4 | Không chân nào trùng strapping/flash/PSRAM | Đối chiếu bảng chân | | | | |
| FW1 #1 | Build không lỗi, không cảnh báo mới trong thư mục board | `tools/fw_build_docker.sh ...` | | | | |
| FW1 #2 | LCD hiện giao diện đúng màu/hướng; LED trạng thái báo trạng thái | Quan sát | | | | |
| FW1 #3 | "Jarvis" → trạng thái nghe, hội thoại được với server | Thử trên mũ | | | | |
| FW1 #4 | Log khởi động đúng board | Log serial (`SKU=jarvis-helmet`) | | | | |
| FW1 #5 | Tắt Wi-Fi nhà → nối hotspot ≤ 30 s | Đồng hồ + log serial | … s | | | |

### P2 – Dựng server AI (SV1, SV2, SV3, SV4, SV5)

| Mã | Bài thử / tiêu chí | Cách thử | Kết quả đo | KQ | Bằng chứng | Ghi chú |
|---|---|---|---|---|---|---|
| SV1 #1 | `docker compose up -d`: mọi service healthy ≤ 3 phút (sau lần tải model đầu) | `docker compose ps` | … phút | | | |
| SV1 #2 | Khởi động lại máy: service tự chạy lại | Reboot | | | | |
| SV1 #3 | Tổng VRAM ≤ 28 GB khi đủ 3 model đã nạp | `nvidia-smi` | … GB | | | |
| SV1 #4 | Từ máy khác trong LAN chỉ thấy cổng WS và OTA của xiaozhi (`XIAOZHI_WS_PORT` 8100, `XIAOZHI_OTA_PORT` 8003 theo D21; module SV1 ghi 8000/8003 là bản cũ) | Quét cổng | | | | |
| SV2 #1 | Từ khóa lệnh ≥ 95 % | `tools/tests/asr/eval.py` (bộ thật) → `RESULTS.md` | … % | | | |
| SV2 #2 | WER ≤ 15 % (trộn) / ≤ 10 % (tiếng Việt) | như trên | … % / … % | | | |
| SV2 #3 | Câu 3 s xử lý ≤ 300 ms (RTX 5090) | như trên (p50 câu 2–4 s) | … ms | | | |
| SV2 #4 | 30 s im lặng/ồn → chuỗi rỗng | như trên (mẫu `noise`) | …/… có chữ | | | |
| SV3 #1 | Token đầu (local) ≤ 400 ms; câu ≤ 40 từ xong ≤ 1,5 s | `latency.py` (`llm_ttft`, `llm_total`) | … / … ms | | | |
| SV3 #2 | ≥ 95 % đúng tool + tham số; 10/10 câu không gọi tool | `tools/tests/llm/run_tool_calls.py` | … % ; …/10 | | | |
| SV3 #3 | Dừng Ollama: câu tiếp theo vẫn được trả lời qua cloud (chậm thêm ≤ ~3 s), log ghi chuyển sang cloud | `docker compose stop ollama` + hỏi | … s | | | |
| SV3 #4 | "Hỏi chuyên gia" → `ask_expert`, trả lời cloud ngắn, đúng ngôn ngữ | E6 | | | | |
| SV4 #1 | TTFA ≤ 300 ms với câu ≤ 15 từ | `tools/tests/tts/bench.py` | …/… câu; trung vị … ms | | | |
| SV4 #2 | Đọc đúng 20/20 câu, không sai số/đơn vị | Nghe WAV của bench | …/20 | | | |
| SV4 #3 | Loa trong mũ rõ, không rè ở 70 % | Nghe trên mũ | | | | |
| SV4 #4 | 1 giờ hội thoại: VRAM ổn định | `nvidia-smi` theo thời gian | … → … GB | | | |
| SV5 #1 | Mũ kết nối, hội thoại Việt–Anh trọn vẹn qua LAN; log không lỗi lệch giao thức | Mũ thật + log | | | | |
| SV5 #2 | Không câu tiếng Trung nào nói ra/hiện lên trong 30 phút | Dùng thử 30 phút | | | | |
| SV5 #3 | 20 câu mẫu: ≥ 18 đúng phong cách | Đánh giá bằng tai | …/20 | | | |
| SV5 #4 | Nói xong → tiếng trả lời ≤ 1,5 s (LAN) | `latency.py` + video (mục 4) | … ms | | | |
| E1 | "Jarvis, mấy giờ rồi?" | scenarios.md E1 | | | | |
| E2 | "Jarvis, what's the weather like?" | scenarios.md E2 | | | | |
| E6 | "Jarvis, hỏi chuyên gia: …" | scenarios.md E6 | | | | |

### P3 – Cơ cấu chấp hành (HW1, HW3, FW2, FW3, FW4)

| Mã | Bài thử / tiêu chí | Cách thử | Kết quả đo | KQ | Bằng chứng | Ghi chú |
|---|---|---|---|---|---|---|
| HW1 #1 | Không tải: 5V_MAIN 4,9–5,25 V; 5V_ESP ≥ 4,5 V | Đồng hồ đo | … V / … V | | | |
| HW1 #2 | Tải nặng nhất 10 phút: không reset, VSENSE ≥ 4,6 V | Log `esp_reset_reason` + VSENSE | | | | |
| HW1 #3 | Cắm USB máy tính khi sạc dự phòng đang bật: không dòng ngược | Đo tại cổng USB | | | | |
| HW3 #1 | Đèn mắt tắt hẳn khi khởi động/reset/nạp code | Quan sát | | | | |
| HW3 #2 | PWM 0–100 % mượt, không nháy (~5 kHz) | Quan sát / máy hiện sóng | | | | |
| HW3 #3 | Q2 tải 500 mA 10 phút, MOSFET < 50 °C | Nhiệt kế | … °C | | | |
| HW3 #4 | Servo không rung khi đứng yên, không giật khi khởi động | Quan sát | | | | |
| FW2 #1 | Hai servo tới đích cùng lúc (lệch < 50 ms), không vượt góc hiệu chỉnh | Video | … ms | | | |
| FW2 #2 | Speed 70: mở hết 1,1 ± 0,2 s, mượt | Video | … s | | | |
| FW2 #3 | Khởi động lại khi mặt nạ mở: đứng yên | Reset khi đang mở | | | | |
| FW2 #4 | Giữ vị trí 5 phút không rung (`detach_after_ms`) | Quan sát | | | | |
| FW2 #5 | Hiệu chỉnh còn sau khi rút điện | `tools/servo_calibrate.py` | | | | |
| FW2 #6 | 10 lệnh mở/đóng liên tiếp nhanh: không treo, trạng thái cuối đúng | `servo_calibrate.py` / giọng nói | | | | |
| FW3 #1 | Đèn tắt suốt lúc khởi động (trừ cấu hình sáng khi khởi động) | Quan sát | | | | |
| FW3 #2 | 5 hiệu ứng đúng mô tả; hiệu ứng mới hủy hiệu ứng cũ | `self.eyes.effect` | | | | |
| FW3 #3 | Độ sáng 10/50/100 % khác nhau rõ, không nháy | Quan sát | | | | |
| FW3 #4 | Output dự phòng bật/tắt đúng, giữ trạng thái khi mất Wi-Fi | `self.output.set` | | | | |
| FW4 #1 | Chuỗi mẫu `open_mask`/`close_mask` đúng thứ tự, đúng thời gian | Video | | | | |
| FW4 #2 | Chuỗi sai bị từ chối, lỗi chỉ đúng bước sai, phần cứng không động | `self.sequence.run` với chuỗi sai | | | | |
| FW4 #3 | Chuỗi mới trong lúc chuỗi cũ chạy: chuỗi cũ dừng ≤ 50 ms | Video / log | … ms | | | |
| FW4 #4 | Tool trả kết quả ≤ 50 ms (log server) | `latency.py` (`mcp_rtt`) | … ms | | | |
| FW4 #5 | Chuỗi đã lưu còn sau khởi động lại, chạy được khi offline | Reset + nút mặt nạ | | | | |
| FW4 #6 | Hội thoại không vấp tiếng khi chuỗi đang chạy | Nghe | | | | |
| E3 | "Jarvis, open mask rồi bật đèn mắt lên" | scenarios.md E3 | | | | |

### P4 – Tác vụ tùy biến và nút bấm (SV6, FW5)

| Mã | Bài thử / tiêu chí | Cách thử | Kết quả đo | KQ | Bằng chứng | Ghi chú |
|---|---|---|---|---|---|---|
| SV6 #1 | Thêm tác vụ YAML (`party_mode`) không khởi động lại gì; lượt sau chạy được | E5 | | | | |
| SV6 #2 | Khớp nhanh: nói xong → servo chạy ≤ 800 ms (LAN) | E4 | … ms | | | |
| SV6 #3 | "Mở mặt nạ rồi bật đèn mắt lên" → 2 tác vụ đúng thứ tự | E3 | | | | |
| SV6 #4 | 20 câu gây nhiễu không bị khớp nhanh nhầm | `tools/tasks_cli.py match "<câu>"` + thử trên mũ | …/20 | | | |
| SV6 #5 | YAML lỗi: plugin giữ bản cũ, log chỉ rõ dòng lỗi | E5 (bước thêm) | | | | |
| FW5 #1 | Mất server/Wi-Fi: nút mặt nạ và nhấn giữ đèn vẫn chạy | E9 | | | | |
| FW5 #2 | Bấm PTT khi đang nói: dừng ≤ 300 ms, chuyển sang nghe | E7 | … ms | | | |
| FW5 #3 | Nhấn giữ PTT, nói, thả: server nhận đúng đoạn đã nói | Mũ thật + log `stt` | | | | |
| FW5 #4 | Bấm đúp khi mặt nạ đang chạy: dừng tại chỗ | Mũ thật | | | | |
| FW5 #5 | 20 lần mỗi loại: không nhầm bấm nhanh/nhấn giữ | Mũ thật | …/20, …/20 | | | |
| E4 | "Mở mặt nạ" (khớp nhanh) | scenarios.md E4 | | | | Ghi chế độ: PTT / wake word |
| E5 | Thêm `party_mode` rồi ra lệnh | scenarios.md E5 | | | | |
| E7 | Bấm PTT khi đang nói | scenarios.md E7 | | | | |
| E9 | Tắt server, dùng nút mặt nạ | scenarios.md E9 | | | | |

### P5 – Giao diện (FW6)

| Mã | Bài thử / tiêu chí | Cách thử | Kết quả đo | KQ | Bằng chứng | Ghi chú |
|---|---|---|---|---|---|---|
| FW6 #1 | Câu "Ừ, đã bật chế độ chiến đấu – tôi sẵn sàng, thưa ngài!" đủ dấu, không ô vuông | E11 (`self.display.show_info`) | | | | |
| FW6 #2 | Mỗi trạng thái (chờ, nghe, nghĩ, nói, lỗi) hình ảnh khác rõ; chuyển < 100 ms | Video | | | | |
| FW6 #3 | Animation ≥ 20 FPS; audio không vấp, nhận dạng bình thường | Video / log | … FPS | | | |
| FW6 #4 | Câu dài tự cuộn, không bị cắt | Câu trả lời dài | | | | |
| FW6 #5 | Thanh trạng thái cập nhật giờ, Wi-Fi, điện áp ≤ 5 s | Quan sát | | | | |
| E11 | Câu hiển thị đủ dấu tiếng Việt | scenarios.md E11 | | | | |

### P6 – Lắp ráp (HW4)

| Mã | Bài thử / tiêu chí | Cách thử | Kết quả đo | KQ | Bằng chứng | Ghi chú |
|---|---|---|---|---|---|---|
| HW4 #1 | Thông mạch đúng 100 % theo bảng HW2/HW3, không chập | Đồng hồ đo | | | | |
| HW4 #2 | Bài thử HW1 (tải nặng nhất) và HW3 vẫn đạt trên PCB | Lặp lại HW1 #2, HW3 #1–#4 | | | | |
| HW4 #3 | Lắc mạnh mũ khi đang chạy: không reset, không mất tiếng/hình | Thử lắc + log | | | | |
| E13 | 50 lệnh liên tiếp (mũ thật, heap) | scenarios.md E13 | | | | |
| E14 | 2 giờ liên tục bằng sạc dự phòng | scenarios.md E14 | | | | |

### P7 – Wake word (FW7)

Chi tiết phương pháp và bảng số liệu: `docs/wakeword-report.md`. Ở đây chỉ ghi kết luận.

| Mã | Bài thử / tiêu chí | Cách thử | Kết quả đo | KQ | Bằng chứng | Ghi chú |
|---|---|---|---|---|---|---|
| FW7 #1 | Mỗi từ ≥ 18/20 (yên tĩnh), ≥ 15/20 (nhạc nền), khoảng cách đội mũ | wakeword-report §3 | Jarvis …/20, …/20; Quản gia …/20, …/20 | | | Phương án: … |
| FW7 #2 | Kích nhầm ≤ 1 lần/giờ khi phát podcast/TV | wakeword-report §3 | … lần/giờ | | | |
| FW7 #3 | Nói xong wake word → phản hồi < 500 ms | Video | … ms | | | |
| FW7 #4 | Không vấp audio; RAM internal trống ≥ 30 KB | `heap_trend.py` (`minimal_sram`) | … KB | | | |
| E8 | Gọi "Jarvis" khi đang nói | scenarios.md E8 | | | | Mặc định: GIỚI HẠN (D12) |
| E12 | "Quản gia, close the mask" | scenarios.md E12 | | | | |

### P8 – Dùng ngoài trời và nghiệm thu (SV7, IT1)

| Mã | Bài thử / tiêu chí | Cách thử | Kết quả đo | KQ | Bằng chứng | Ghi chú |
|---|---|---|---|---|---|---|
| SV7 #1 | `curl https://jarvis.<domain>/xiaozhi/ota/` không header → bị chặn (302 hoặc 401/403 tùy cấu hình Access); có service token → 200 | `curl` | mã … / … | | | |
| SV7 #2 | Qua 4G: hội thoại trọn vẹn, nói xong → tiếng trả lời ≤ 2,5 s | `latency.py --network 4g` + video | … ms | | | |
| SV7 #3 | Đổi mạng (tắt Wi-Fi nhà): dùng được trong ≤ 30 s | E10 | … s | | | |
| SV7 #4 | Repo không chứa bí mật (`git grep`), có `.env.example` và hướng dẫn nạp bí mật | `git grep` | | | | |
| E10 | Tắt Wi-Fi nhà khi hotspot bật | scenarios.md E10 | | | | |
| E1–E14 | Toàn bộ kịch bản trên mũ hoàn chỉnh | mục 5 | | | | |
| IT1 §3 | Mọi chỉ tiêu tổng hợp | mục 4 | | | | |

---

## 4. Chỉ tiêu tổng hợp (IT1 §3)

| Chỉ tiêu | Mục tiêu | Điều kiện đo | n | Trung vị | p90 | KQ | Nguồn / lệnh | Bằng chứng |
|---|---|---|---|---|---|---|---|---|
| Nói xong → tiếng trả lời đầu tiên (LAN) | ≤ 1,5 s (trung vị) | Mũ thật, Wi-Fi nhà, giọng thật | | | | | `latency.py <log> --kind voice` + video 5 câu | |
| Như trên, qua 4G | ≤ 2,5 s | Hotspot 4G qua Cloudflare | | | | | `latency.py <log> --kind voice --network 4g` + video | |
| Khớp nhanh: nói xong → servo chạy (LAN) | ≤ 0,8 s | E4, chế độ: … | | | | | video + `eos_to_mcp_quick` | |
| Từ khóa lệnh nhận đúng (ASR) | ≥ 95 % | Bộ thu âm thật, cấu hình … | | – | – | | `tools/tests/asr/RESULTS.md` | |
| Gọi đúng tool (LLM) | ≥ 95 % | Model …, bộ `tool_calls.yaml` | | – | – | | `tools/tests/llm/run_tool_calls.py` | |
| Wake word nhận đúng (yên tĩnh) | ≥ 18/20 mỗi từ | Phương án … | | – | – | | `docs/wakeword-report.md` | |
| Kích nhầm wake word | ≤ 1 lần/giờ | Podcast/TV … giờ | | – | – | | `docs/wakeword-report.md` | |
| Pin | ≥ 2 giờ không sự cố | E14 | | – | – | | `e14_status.log` | |

Độ trễ theo chặng (dán từ `latency.py`; mỗi điều kiện một cột):

| Chỉ số `latency.py` | Chặng | Mục tiêu | LAN trung vị / p90 (ms) | 4G trung vị / p90 (ms) |
|---|---|---|---|---|
| `vad_wait` | lúc hết tiếng → `vad_end` | – | | |
| `asr` | `vad_end` → `asr_done` | 300 (tham khảo) | | |
| `llm_ttft` | `asr_done` → token đầu | 400 | | |
| `llm_total` | `asr_done` → LLM xong | – | | |
| `tts_first` | token đầu → âm TTS đầu | – | | |
| `send_first` | âm TTS đầu → gói đầu gửi đi | – | | |
| `server_total` | `vad_end` → gói đầu gửi đi | – | | |
| `eos_to_audio` | nói xong → gói audio đầu | 1500 LAN / 2500 4G | | |
| `eos_to_mcp_quick` | nói xong → lệnh MCP (khớp nhanh) | 800 | | |
| `mcp_rtt` | round-trip lệnh MCP | 50 | | |
| Video (nghe thấy tiếng) | nói xong → nghe tiếng trả lời | – | | |

---

## 5. Kết quả kịch bản E1–E14

| # | Kịch bản | Tự động (`device_sim.py`) | Mũ thật | Kết quả đo chính | KQ | Bằng chứng | Ghi chú / giới hạn |
|---|---|---|---|---|---|---|---|
| E1 | "Jarvis, mấy giờ rồi?" | | | | | | |
| E2 | "Jarvis, what's the weather like?" | | | | | | |
| E3 | "Jarvis, open mask rồi bật đèn mắt lên" | | | thứ tự hành động: … | | | |
| E4 | "Mở mặt nạ" (khớp nhanh) | | | … ms (chế độ …) | | | |
| E5 | Thêm `party_mode` vào YAML rồi ra lệnh | | | khởi động lại: không/có | | | |
| E6 | "Jarvis, hỏi chuyên gia: …" | | | log `ask_expert`: … ms | | | |
| E7 | Bấm PTT khi Jarvis đang nói | | | … ms | | | |
| E8 | Gọi "Jarvis" khi Jarvis đang nói | | | | | | Mặc định `CONFIG_JARVIS_WAKE_WORD_BARGE_IN=n` → GIỚI HẠN (D12) |
| E9 | Tắt server, dùng nút mặt nạ / nhấn giữ | N/A | | OFFLINE hiển thị: có/không | | | |
| E10 | Tắt Wi-Fi nhà (hotspot đang bật) | (đường Cloudflare) | | … s | | | |
| E11 | Câu hiển thị đủ dấu tiếng Việt | | | | | | |
| E12 | "Quản gia, close the mask" | | | | | | Phương án wake word: … |
| E13 | 50 lệnh liên tiếp | | | …/50; heap … B/giờ | | | |
| E14 | 2 giờ liên tục bằng sạc dự phòng | N/A | | uptime …, VSENSE min … mV | | | |

---

## 6. Nhật ký lỗi

| # | Ngày | Giai đoạn / kịch bản | Module (nhãn) | Mức độ | Mô tả | Cách tái hiện | Issue | Trạng thái | Xử lý (commit / ghi chú) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | … | P… / E… | … | Nghiêm trọng / Trung bình / Nhẹ | … | … | #… | Mở / Đang sửa / Đã sửa / Chấp nhận | … |

---

## 7. Kết luận

| Mục | Nội dung |
|---|---|
| Chỉ tiêu IT1 §3 đạt | …/8 |
| Kịch bản E1–E14 đạt | …/14 (GIỚI HẠN: …) |
| Giới hạn đã chấp nhận | (ví dụ E8 – ngắt lời bằng wake word, D12) |
| Lỗi còn mở | (số issue) |
| Quyết định | Nghiệm thu / Chưa nghiệm thu – lý do |
| Người xác nhận, ngày | … |
