# FW3 – Đèn mắt & output dự phòng

| | |
|---|---|
| **Mục tiêu** | Bật/tắt và chỉnh độ sáng đèn mắt bằng PWM, có các hiệu ứng (thở, nháy, khởi động, tắt dần). Bật/tắt output dự phòng |
| **Phụ thuộc** | FW1, HW3 |
| **Kết quả giao nộp** | `firmware/main/boards/jarvis-helmet/output_controller.{h,cc}` + các tool `self.eyes.*`, `self.output.set` |

## 1. Thiết kế

**PWM:**
- LEDC timer 2, 5 kHz, độ phân giải 10-bit.
- Channel 3 → GPIO8 (mắt), channel 4 → GPIO9 (output dự phòng).
- Dùng hàm fade phần cứng của LEDC (`ledc_set_fade_time_and_start`) để chuyển độ sáng mượt mà không tốn CPU.
- Hiệu chỉnh gamma (≈2.2) khi đổi % độ sáng ra duty, để mắt người thấy độ sáng tăng đều.

**Hiệu ứng** (chạy trong một FreeRTOS task, mỗi lần chỉ chạy một hiệu ứng; lệnh mới hủy hiệu ứng cũ):

| Tên | Mô tả |
|---|---|
| `breathe` | Sáng lên rồi tối dần theo chu kỳ 2.5 giây, lặp mãi đến khi có lệnh khác |
| `blink` | Nháy 2 lần nhanh như chớp mắt, sau đó trở lại độ sáng trước |
| `flicker` | Chập chờn ngẫu nhiên khoảng 600ms rồi sáng ổn định (như đèn sắp hỏng) |
| `power_up` | Chập chờn ngắn, sau đó sáng dần từ 0 lên mức mặc định trong `duration_ms` |
| `fade_out` | Tắt dần trong `duration_ms` |

**API C++:**
```cpp
class OutputController {
 public:
  void SetEyes(bool on, int brightness = -1);   // -1 = dùng độ sáng mặc định trong NVS (eyes_bright)
  void EyesEffect(const std::string& name, int duration_ms);
  void SetOutput(bool on);
  bool WaitIdle(int timeout_ms);                 // FW4 dùng; breathe được coi là xong ngay
  OutputState GetState() const;
  void RegisterMcpTools();                       // self.eyes.set, self.eyes.effect, self.output.set
};
```

**Liên kết với trạng thái trợ lý** (tùy chọn, bật/tắt bằng cấu hình):
- Khi đang nghe: mắt sáng 100%.
- Khi đang nói: mắt nháy nhẹ theo âm lượng.
- Khi chờ: mắt sáng mức mặc định.

## 2. Công việc
- [ ] Viết `output_controller.{h,cc}`: LEDC, gamma, task hiệu ứng
- [ ] Đăng ký MCP tool kèm mô tả song ngữ
- [ ] Gắn vào board, mặc định tắt khi khởi động
- [ ] (Tùy chọn) Mắt phản ứng theo trạng thái trợ lý

## 3. Tiêu chí nghiệm thu
1. Đèn tắt suốt quá trình khởi động. Chỉ sáng khi có lệnh, hoặc theo cấu hình "sáng khi khởi động".
2. Cả 5 hiệu ứng chạy đúng mô tả. Hiệu ứng mới hủy ngay hiệu ứng cũ, không bị chồng.
3. Độ sáng 10%, 50%, 100% nhìn thấy khác nhau rõ rệt và đều; không nháy thấy được.
4. Output dự phòng bật/tắt đúng lệnh, giữ nguyên trạng thái khi WiFi mất kết nối.
