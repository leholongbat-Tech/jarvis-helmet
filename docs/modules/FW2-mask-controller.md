# FW2 – Điều khiển mặt nạ (2 servo)

| | |
|---|---|
| **Mục tiêu** | Mở/đóng mặt nạ bằng 2 servo chạy đồng bộ, chuyển động mượt, có hiệu chỉnh góc, không giật khi khởi động |
| **Phụ thuộc** | FW1, HW3 |
| **Kết quả giao nộp** | `firmware/main/boards/jarvis-helmet/mask_controller.{h,cc}` + các MCP tool `self.mask.*`, `self.servo.*` (xem [interfaces.md](../interfaces.md)) |

## 1. Thiết kế

**PWM:**
- LEDC timer 1, 50 Hz, độ phân giải 14-bit; channel 1 (S0, GPIO17) và channel 2 (S1, GPIO18).
- Timer 0 / channel 0 đã dùng cho đèn nền LCD (`PwmBacklight`), không được dùng.
- Đổi góc ra độ rộng xung: `pulse_us = 500 + angle/180 × (2400 − 500)`. Hai giá trị 500 và 2400 là hằng số trong `config.h`.
- Tham khảo cách tổ chức LEDC trong `main/boards/otto-robot/oscillator.cc`.

**Vị trí và hiệu chỉnh:**
- Vị trí mặt nạ tính theo phần trăm: 0% = đóng, 100% = mở.
- Mỗi servo có góc đóng `closed` và góc mở `open` lưu trong NVS (`s0_closed`, `s0_open`, …). Servo bên phải lắp đối xứng nên có `closed > open`: cách đổi tuyến tính tự xử lý chiều đảo, không cần cờ invert riêng.
- Góc ra lệnh luôn bị kẹp trong khoảng [closed, open] để không ép cơ khí.

**Chuyển động:**
- Có một FreeRTOS task riêng nhận lệnh qua queue và cập nhật PWM mỗi 20ms.
- Đường cong ease-in-out (cosin). Thời gian chạy = `T_base × 100 / speed`, với `T_base` = 800ms; speed 10–100.
- Hai servo dùng chung một trục thời gian nên chạy đồng bộ. Servo S1 có thể khởi động trễ khoảng 30ms để giảm dòng khởi động (cấu hình được).
- Lệnh mới ghi đè lệnh đang chạy, xuất phát từ vị trí hiện tại.

**Giữ vị trí:**
- `detach_after_ms` (cấu hình): 0 = luôn cấp xung giữ lực (dùng khi mặt nạ tự rơi do trọng lực). Giá trị > 0 = ngắt xung sau ngần ấy ms để servo không rung và đỡ tốn điện.
- Mỗi lần chạy xong, ghi vị trí cuối vào NVS. Lần khởi động sau, xung đầu tiên đúng bằng vị trí đã lưu nên servo **không bị giật**.

**API C++:**
```cpp
class MaskController {
 public:
  MaskController(gpio_num_t s0, gpio_num_t s1);
  void Open(int speed = 70);
  void Close(int speed = 70);
  void SetPosition(int percent, int speed = 70);
  void MoveServo(int id, int angle, int duration_ms);   // dùng cho bước "servo" và khi hiệu chỉnh
  bool WaitIdle(int timeout_ms);                         // sequence runner (FW4) gọi hàm này
  MaskState GetState() const;                            // {OPEN|CLOSED|MOVING, position}
  void RegisterMcpTools();                               // self.mask.*, self.servo.* (user-only)
};
```

## 2. Quy trình hiệu chỉnh (làm một lần sau khi lắp cơ khí)
1. Gọi `self.servo.calibrate(servo=0, angle=…)` cho đến khi mặt nạ đóng khít. Ghi lại góc. Thực tế: server v0.9.7 không lấy tool user-only, nên gọi qua WebSocket MCP cục bộ của mũ bằng `tools/servo_calibrate.py` (D7; hướng dẫn ở README của board, mục 8).
2. Làm lại để tìm góc mở hết.
3. Làm tương tự cho servo 1.
4. Lưu bằng `self.servo.save_calibration`.

Giá trị mặc định trước khi hiệu chỉnh là 90° ± 10°, để lần chạy đầu không ép cơ khí.

## 3. Công việc
- [x] Viết `mask_controller.{h,cc}`: LEDC, task chuyển động, NVS
- [x] Đăng ký MCP tool kèm mô tả song ngữ
- [x] Gắn vào `JarvisHelmetBoard` (tạo đối tượng và gọi `RegisterMcpTools` trong `InitializeTools`)
- [ ] Thử trên servo chưa lắp cơ khí, sau đó thử trên mũ

## 4. Tiêu chí nghiệm thu
1. Hai servo đến đích cùng lúc, lệch nhau dưới 50ms; không vượt quá khoảng góc đã hiệu chỉnh.
2. Ở speed 70, mở hết trong khoảng 1.1 ± 0.2 giây, chuyển động mượt, không giật.
3. Khởi động lại khi mặt nạ đang mở: mặt nạ đứng yên, không bị kéo về vị trí khác.
4. Giữ vị trí 5 phút không rung (khi đã bật `detach_after_ms`).
5. Hiệu chỉnh vẫn còn sau khi rút điện.
6. Gửi 10 lệnh mở/đóng liên tiếp thật nhanh: không treo, trạng thái cuối cùng đúng với lệnh cuối cùng.

## Ghi chú triển khai (10/2026)

**Trạng thái:** code xong theo đúng thiết kế trên; phần tính toán thuần (góc → xung, đường cong ease-in-out, thời gian chạy,
góc khi khởi động) nằm trong `actuator_math.h` và có test chạy trên máy tính (`firmware/tests/host/test_actuator_math.cpp`).
**Chưa thử với servo thật.**

- Thông số trong `config.h`: xung 500–2400 µs, LEDC timer 1 / channel 1–2, 50 Hz, 14 bit, chu kỳ cập nhật 20 ms, servo S1 khởi
  động trễ 30 ms (`MASK_S1_START_DELAY_MS`), `MASK_DETACH_AFTER_MS` = 0 (luôn giữ lực), góc mặc định trước khi hiệu chỉnh S0
  80/100°, S1 100/80° (đóng/mở).
- Thời gian chạy hết hành trình = `800 × 100 / speed` ms (speed 70 → 1142 ms); quãng ngắn tỉ lệ theo khoảng cách, tối thiểu 150 ms.
  Sequence runner ước tính mỗi bước `mask` bằng cả hành trình (`docs/interfaces.md` mục 3).
- Không giật khi khởi động: ngoài `mask_pos`, mỗi servo lưu góc nghỉ riêng (`s0_pos`, `s1_pos`), vì một bước `servo` có thể chỉ
  xoay một bên; xung đầu tiên sau khi bật nguồn đúng bằng góc đã lưu.
- API thực tế khác bản nháp ở chỗ: `speed = -1` nghĩa là tốc độ mặc định lưu trong NVS (`mask_speed`, mặc định 70); có thêm `Stop()`,
  `Calibrate()`, `SaveCalibration()` (góc đóng và mở phải khác nhau).
- Nút mặt nạ (FW5) gọi thẳng controller, không qua luồng chính (D9).

**Tiêu chí nghiệm thu:** tất cả chờ mũ thật (servo + cơ khí).
