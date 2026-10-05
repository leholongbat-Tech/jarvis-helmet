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
- [ ] `sequence_runner`: đọc JSON, kiểm tra, task chạy, hủy, đọc/ghi NVS
- [ ] `helmet_tools`: đăng ký đủ tool như interfaces.md, mô tả song ngữ
- [ ] Tách phần kiểm tra JSON thành code C++ thuần, chỉ phụ thuộc cJSON, để test được trên máy tính (CMake + Catch2, hoặc target `linux` của ESP-IDF). Tối thiểu 15 trường hợp: hợp lệ, sai khóa, ngoài khoảng, quá 64 bước, quá 60 giây
- [ ] Thử thủ công bằng cách gửi MCP từ server (SV5) hoặc dùng `scripts/` của xiaozhi

## 4. Tiêu chí nghiệm thu
1. Chuỗi mẫu `open_mask` / `close_mask` trong interfaces.md chạy đúng thứ tự và đúng thời gian.
2. Chuỗi sai bị từ chối, lỗi chỉ đúng bước sai, phần cứng không động đậy.
3. Gửi chuỗi mới trong lúc chuỗi cũ đang chạy: chuỗi cũ dừng trong vòng 50ms, chuỗi mới bắt đầu.
4. Tool trả kết quả về server trong vòng 50ms (đo bằng log của server).
5. Chuỗi đã lưu vẫn còn sau khi khởi động lại và chạy được khi không có mạng.
6. Hội thoại vẫn bình thường (không vấp tiếng) trong lúc một chuỗi đang chạy.
