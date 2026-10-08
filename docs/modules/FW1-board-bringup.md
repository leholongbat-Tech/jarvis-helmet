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
| `firmware/main/boards/jarvis-helmet/config.h` | Bảng chân lấy từ HW2. `AUDIO_I2S_METHOD_SIMPLEX`. Audio vào 16 kHz, ra 48 kHz (MAX98357A không hỗ trợ LRCLK 24 kHz; firmware tự chuyển mẫu luồng TTS 24 kHz lên 48 kHz). Thông số LCD lấy từ khối `CONFIG_LCD_ST7789_240X240` (hoặc `_7PIN`) của `bread-compact-wifi-lcd/config.h`. Thêm định nghĩa chân servo, mắt, output, nút, VSENSE |
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
- [x] Đưa mã upstream v2.5.0 vào repo (commit `vendor: xiaozhi-esp32 v2.5.0`)
- [x] Tạo thư mục board, đăng ký ở Kconfig và CMake
- [x] Build với LCD `_240X240` và với `_240X240_7PIN`; [ ] chọn bản khớp màn thật
- [ ] Nạp, cấu hình WiFi, thử với server mặc định (P1), rồi với server nhà (P2)
- [x] Đọc mã `esp-wifi-connect` về nhiều SSID (kết quả ở mục ghi chú); [ ] thử chuyển mạng trên mũ thật

## 5. Tiêu chí nghiệm thu
1. Build không lỗi, không có cảnh báo mới trong thư mục board.
2. Khởi động: LCD hiện giao diện mặc định đúng màu và hướng. LED GPIO48 báo trạng thái.
3. Nói "Jarvis" → thiết bị chuyển sang trạng thái nghe. Hội thoại được với server.
4. Log khởi động ghi đúng `board type: jarvis-helmet`.
5. Tắt WiFi nhà → thiết bị tự kết nối hotspot trong vòng 30 giây.

## Ghi chú triển khai (10/2026)

**Trạng thái:** code xong; build 4 biến thể bằng ESP-IDF 6.1 trong Docker (`tools/fw_build_docker.sh`) không cảnh báo, còn
trống ~31% phân vùng app; test chạy trên máy tính (`firmware/tests/host/`) đạt. **Chưa nạp vào mũ thật.** Hướng dẫn build,
nạp, Wi-Fi, OTA: `firmware/main/boards/jarvis-helmet/README.md`; thay đổi trên file upstream: `firmware/PATCHES.md`.

**Board thử (breadboard), 7–8/10/2026:** khởi động đúng board, vào Wi-Fi, kích hoạt với server batai, MCP cục bộ trả lời.
Ngày 7/10 mic không gửi dữ liệu (`mic_probe` = `stuck_low`); sửa dây xong, ngày 8/10 `mic_probe` = `left` và cả chuỗi chạy
(BOOT → ASR → LLM → TTS → loa). Loa rè khi Jarvis nói, dù luồng TTS sạch (đỉnh -1,5 dBFS, không cắt sau Opus); đã sửa hai
chỗ: ngõ ra I2S 48 kHz (MAX98357A không hỗ trợ LRCLK 24 kHz) và lọc cắt trầm 250 Hz (giọng Jarvis có 94% năng lượng dưới
500 Hz, loa nhỏ không phát được dải đó).

**Sai khác so với kế hoạch:**

| Kế hoạch | Thực tế | Lý do |
|---|---|---|
| Bảng phân vùng 16MB có sẵn | `partitions_jarvis_16m.csv`: thêm `helmet` (NVS 0xC000) và `jsecrets` (NVS 0x4000) ở vùng trống 0x10000–0x1FFFF | D6: cài đặt, hiệu chỉnh, chuỗi đã lưu và bí mật tách khỏi NVS của upstream. Chỉ đổi được bằng cách nạp qua cáp |
| 1–2 biến thể (LCD 8/7 chân) | 4 biến thể cùng `type` `jarvis-helmet`: `jarvis-helmet`, `-7pin`, `-multinet` (wake word B1), `-mww` (microWakeWord B2) | FW7 |
| `SingleLed(GPIO48)` | LED WS2812 ở GPIO48; DevKitC-1 v1.1 đặt LED ở GPIO38 (ghi chú trong `config.h`) | D24 |
| Cài đặt rải rác | Menu Kconfig "Jarvis Helmet" (`Kconfig.jarvis`): ngưỡng giữ PTT, WebSocket MCP cục bộ + cổng, ngắt bằng wake word, quét Wi-Fi nhanh, microWakeWord, header Cloudflare dự phòng | Gom một chỗ |
| Kiểm tra nhiều SSID | `esp-wifi-connect` lưu tối đa 10 mạng, không có thứ tự ưu tiên (chọn mạng mạnh nhất khi quét), không tự chuyển khi đang kết nối. Board rút chu kỳ quét lại còn 3 s, tăng dần tới 30 s (`CONFIG_JARVIS_WIFI_FAST_RESCAN`) | Mục tiêu đổi mạng ≤30 s (tiêu chí 5) |
| Mic INMP441 kênh trái (L/R nối GND) | Lúc khởi động `MeteredAudioCodec::ProbeMicrophone` đọc 64 ms cả hai kênh (kéo lên SD), ghi log `MicProbe:`, dùng kênh phải nếu chỉ kênh đó có dữ liệu; kết quả trong `self.system.get_status` (`audio.mic_probe`, `mic_dbfs`, `speaker_dbfs`) | Kiểm tra dây mic, loa từ xa khi chưa có màn hình; L/R để hở không làm mất mic |
| Loa MAX98357A, ra 24 kHz như `bread-compact-wifi` | Ra 48 kHz (datasheet: LRCLK 24 kHz "NOT supported"); lọc cắt trầm Butterworth bậc 4 trong `MeteredAudioCodec::Write` (`CONFIG_JARVIS_SPEAKER_HPF_HZ`, mặc định 250 Hz, 0 = tắt) | Loa board thử rè: giọng nam của Jarvis có âm cơ bản ~120 Hz, 37% năng lượng dưới 200 Hz |
| Header xác thực khi đi qua Internet (SV7) | Sổ đăng ký header theo host trong `main/protocols/extra_headers.cc`, áp cho OTA, tải firmware, WebSocket; chỉ gửi qua TLS (D13, D31). Bí mật đọc từ NVS `jsecrets` | Tải `assets` của upstream dùng client HTTP riêng nên chưa có header (hạn chế đã biết) |

**Tiêu chí nghiệm thu:** 1 đạt (build không cảnh báo); 2–5 chờ mũ thật.
