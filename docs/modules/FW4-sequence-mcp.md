# FW4 – Sequence runner & MCP tools

| | |
|---|---|
| **Mục tiêu** | Thiết bị nhận một chuỗi bước JSON từ server (hoặc chuỗi lưu sẵn) và chạy lần lượt. Server tạo được tác vụ mới mà không cần nạp lại firmware. Gom đầy đủ MCP tool theo [interfaces.md](../interfaces.md) |
| **Phụ thuộc** | FW2 (`MaskController`), FW3 (`OutputController`) |
| **Kết quả giao nộp** | `sequence_runner.{h,cc}`, `helmet_tools.{h,cc}` (đăng ký tool `self.sequence.*`, `self.display.show_info`, `self.system.get_status`) |

## 1. Thiết kế

**Kiểm tra trước khi chạy:**
- Dùng `cJSON` (có sẵn trong xiaozhi) để đọc mảng bước, kiểm tra theo bảng ràng buộc ở interfaces.md mục 3.
- Gặp lỗi ở bất kỳ bước nào thì trả lỗi kèm số thứ tự bước, **không chạy bước nào cả**.
- Tính trước tổng thời gian (delay + thời gian chuyển động ước tính). Vượt 60 giây thì từ chối.

**Chạy:**
- Có một FreeRTOS task `seq_runner` (stack 6KB, đặt trong PSRAM nếu được) nhận chuỗi qua queue độ dài 1. Chuỗi mới ghi đè chuỗi cũ.
- Mỗi bước gọi controller tương ứng, rồi chờ:
  - `mask` / `servo` → `MaskController::WaitIdle`
  - `eyes` (có `effect`) → `OutputController::WaitIdle`
  - `delay` → `vTaskDelay`, nhưng chờ từng đoạn 20ms để `stop` có tác dụng ngay
  - `display` → gọi qua `Application::Schedule` (LVGL phải chạy trên luồng của display, có lock)
  - `sound` → phát âm thanh trong assets qua `AudioService` (làm sau)
- `self.sequence.stop`, hoặc một chuỗi mới, sẽ đặt cờ hủy. Bước đang chạy dừng ở trạng thái an toàn: servo dừng tại chỗ, đèn giữ nguyên.

**Chuỗi lưu sẵn:**
- `self.sequence.save(name, steps)`: kiểm tra hợp lệ rồi ghi blob vào NVS khóa `seq_<name>` (name dài tối đa 11 ký tự).
- `self.sequence.run_saved(name)`: tìm trong NVS trước. Không có thì dùng chuỗi mặc định trong firmware (`open_mask`, `close_mask`).
- FW5 dùng các chuỗi này cho nút bấm khi mất mạng.

**Tool phụ:**
- `self.display.show_info(title, text, seconds)`: gọi API của HUD (FW6). Khi chưa có FW6 thì dùng `Display::ShowNotification`.
- `self.system.get_status`: trả JSON gồm trạng thái mask/eyes/output, `vbus_mv` (đọc ADC1_CH0 qua `esp_adc` oneshot + calibration, nhân 2), RSSI, uptime.

**Ràng buộc luồng:**
- Mọi tool callback chạy trên luồng chính. Chỉ được kiểm tra, đưa vào queue rồi trả về; không chờ.
- Truy cập controller phải an toàn đa luồng: dùng queue/lệnh, không chia sẻ biến trần giữa các task.

## 2. Ví dụ luồng xử lý
```
LLM ─► server plugin task_open_mask ─► MCP tools/call self.sequence.run {steps:"[...]"}
     ─► (luồng chính) kiểm tra hợp lệ ─► queue ─► trả "started"
     ─► (seq_runner) eyes fade_out → chờ → mask open → chờ → xong
```

## 3. Công việc
- [x] `sequence_runner`: đọc JSON, kiểm tra, task chạy, hủy, đọc/ghi NVS
- [x] `helmet_tools`: đăng ký đủ tool như interfaces.md, mô tả song ngữ
- [x] Tách phần kiểm tra JSON thành code C++ thuần (`sequence_validator.{h,cc}`, chỉ phụ thuộc cJSON), test trên máy tính bằng CMake + Catch2 (`firmware/tests/host/`): 28 test case của validator + toàn bộ fixture dùng chung với server (85 ca)
- [ ] Thử thủ công với mũ thật: qua server (SV5) hoặc WebSocket MCP cục bộ (`tools/servo_calibrate.py --call`)

## 4. Tiêu chí nghiệm thu
1. Chuỗi mẫu `open_mask` / `close_mask` trong interfaces.md chạy đúng thứ tự và đúng thời gian.
2. Chuỗi sai bị từ chối, lỗi chỉ đúng bước sai, phần cứng không động đậy.
3. Gửi chuỗi mới trong lúc chuỗi cũ đang chạy: chuỗi cũ dừng trong vòng 50ms, chuỗi mới bắt đầu.
4. Tool trả kết quả về server trong vòng 50ms (đo bằng log của server).
5. Chuỗi đã lưu vẫn còn sau khi khởi động lại và chạy được khi không có mạng.
6. Hội thoại vẫn bình thường (không vấp tiếng) trong lúc một chuỗi đang chạy.

## Ghi chú triển khai (10/2026)

**Trạng thái:** code xong; validator và luật ước tính có test chạy trên máy tính, dùng chung bộ fixture
`tools/tests/fixtures/sequences/` với server; đường gọi MCP đã chạy với server thật qua bộ giả lập mũ. **Chưa chạy trên mũ thật.**

| Kế hoạch | Thực tế | Lý do |
|---|---|---|
| Stack 6KB, đặt trong PSRAM nếu được | Stack 6KB trong RAM trong (`xTaskCreate` của ESP-IDF cấp stack ở `MALLOC_CAP_INTERNAL`) | – |
| Bước `display` qua `Application::Schedule` | Gọi HUD trực tiếp từ task runner (các hàm của display tự khóa) | `Schedule` có thể đứng khi kênh âm thanh đang mở |
| `sound` làm sau | Đã có đường chạy: phát âm thanh trong assets nếu có, không có thì ghi log và bỏ qua. Có 5 hiệu ứng ngắn của upstream và `xin_chao` ("Xin chào thế giới", giọng VieNeu) để thử loa | – |
| Lỗi `invalid step #i` | n đếm từ 1 (D23); lỗi đã qua `SanitizeMessage` vì `ReplyError` không escape | D23 |
| `duration_ms` của hiệu ứng mắt 100–10000, mặc định 1000 | 0 = mặc định theo hiệu ứng (breathe 2500, flicker 600, còn lại 1000); ước tính và thời gian chờ dùng giá trị đã thay mặc định | D26 |
| – | `self.display.show_info`: tiêu đề được rỗng (D30), giới hạn đếm theo ký tự Unicode; `self.system.get_status` có thêm `sequence.running/name`, `free_heap` và `audio.mic_dbfs/speaker_dbfs/mic_probe` (kiểm tra mic, loa từ xa; dò kênh mic lúc khởi động) | – |
| Gửi MCP để thử tay | WebSocket MCP cục bộ trên mũ (cổng 8080, D7) với danh sách tool được phép và token tùy chọn (D27) | Server không lấy tool user-only |

**Tiêu chí nghiệm thu:** 2 (từ chối chuỗi sai, chỉ đúng bước) đã kiểm bằng test máy tính; 4 (trả lời ≤50 ms) đo được trên bộ
giả lập (round-trip MCP trung vị 2 ms, chưa tính thời gian của ESP32); còn lại chờ mũ thật.
