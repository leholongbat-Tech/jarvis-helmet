# FW1 – Board `jarvis-helmet`: khởi động phần cứng

| | |
|---|---|
| **Mục tiêu** | Tạo board riêng `jarvis-helmet` trên nền xiaozhi-esp32 v2.5.0. Mic, loa, LCD 240×240, wake word "Jarvis", WiFi nhiều mạng và địa chỉ server riêng phải chạy được |
| **Phụ thuộc** | HW2 (bảng chân) |
| **Kết quả giao nộp** | Thư mục `firmware/` (mã upstream đưa vào repo) và `firmware/main/boards/jarvis-helmet/` |

## 1. Đưa mã upstream vào repo
1. Clone `https://github.com/78/xiaozhi-esp32` ở tag `v2.5.0`, xóa `.git`, chép vào `firmware/`.
2. Commit riêng: `vendor: xiaozhi-esp32 v2.5.0`.
3. Các thay đổi của dự án là các commit sau. Khi nâng cấp upstream: đưa bản mới vào, rồi áp lại các commit đó.

## 2. File tạo mới / sửa

| File | Nội dung |
|---|---|
| `firmware/main/boards/jarvis-helmet/config.h` | Bảng chân lấy từ HW2. `AUDIO_I2S_METHOD_SIMPLEX`. Audio vào 16 kHz, ra 24 kHz. Thông số LCD lấy từ khối `CONFIG_LCD_ST7789_240X240` (hoặc `_7PIN`) của `bread-compact-wifi-lcd/config.h`. Thêm định nghĩa chân servo, mắt, output, nút, VSENSE |
| `firmware/main/boards/jarvis-helmet/config.json` | `"type": "jarvis-helmet"`, `"target": "esp32s3"`, `sdkconfig_append`: `CONFIG_LCD_ST7789_240X240=y` (hoặc `_7PIN`) |
| `firmware/main/boards/jarvis-helmet/jarvis_helmet_board.cc` | Class `JarvisHelmetBoard : public WifiBoard`, chép từ `bread-compact-wifi-lcd/compact_wifi_board_lcd.cc`: `InitializeSpi`, `InitializeLcdDisplay`, `NoAudioCodecSimplex`, `PwmBacklight`, `SingleLed(GPIO48)`. Có các điểm gắn sẵn để FW2–FW6 thêm controller sau |
| `firmware/main/boards/jarvis-helmet/README.md` | Hướng dẫn build và nạp |
| `firmware/main/Kconfig.projbuild` | Thêm `config BOARD_TYPE_JARVIS_HELMET` vào `choice BOARD_TYPE` (`depends on IDF_TARGET_ESP32S3`). Thêm vào điều kiện `depends on` của khối chọn "LCD Type" (dòng ~738) để menuconfig cho chọn kích thước màn |
| `firmware/main/CMakeLists.txt` | Nhánh `elseif(CONFIG_BOARD_TYPE_JARVIS_HELMET)`: `BOARD_DIR "jarvis-helmet"`, font chữ, icon, emoji (FW6 sẽ thay font tiếng Việt) |

> Đặt `type` riêng (`jarvis-helmet`), không dùng lại tên board có sẵn. Nếu không, OTA của server chính thức có thể ghi đè firmware.

## 3. Cấu hình build
- ESP-IDF **6.1** (tối thiểu 6.0.1). Trên Windows dùng VS Code + extension ESP-IDF.
- Lệnh: `python scripts/build.py jarvis-helmet --language vi-VN --wake-word wn9_jarvis_tts`
- `CONFIG_OTA_URL`:
  - P1: để mặc định (server xiaozhi.me) để thử phần cứng.
  - P2: đổi sang `http://<IP-LAN>:8003/xiaozhi/ota/`.
  - P8: đổi sang `https://jarvis.<domain>/xiaozhi/ota/`.
- WiFi: cấu hình qua điểm phát `Xiaozhi-xxxx` → 192.168.4.1, lưu cả WiFi nhà và hotspot điện thoại. **Cần kiểm tra** thư viện `esp-wifi-connect` lưu được nhiều SSID và thứ tự ưu tiên của nó.
- Partition: dùng bảng 16MB có sẵn của upstream (có phân vùng assets, model).

## 4. Công việc
- [ ] Đưa mã upstream v2.5.0 vào repo
- [ ] Tạo thư mục board, đăng ký ở Kconfig và CMake
- [ ] Build với LCD `_240X240` và với `_240X240_7PIN`, chọn bản khớp màn thật
- [ ] Nạp, cấu hình WiFi, thử với server mặc định (P1), rồi với server nhà (P2)
- [ ] Kiểm tra lưu nhiều SSID và việc tự chuyển mạng

## 5. Tiêu chí nghiệm thu
1. Build không lỗi, không có cảnh báo mới trong thư mục board.
2. Khởi động: LCD hiện giao diện mặc định đúng màu và hướng. LED GPIO48 báo trạng thái.
3. Nói "Jarvis" → thiết bị chuyển sang trạng thái nghe. Hội thoại được với server.
4. Log khởi động ghi đúng `board type: jarvis-helmet`.
5. Tắt WiFi nhà → thiết bị tự kết nối hotspot trong vòng 30 giây.
