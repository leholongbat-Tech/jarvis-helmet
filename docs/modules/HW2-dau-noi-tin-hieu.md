# HW2 – Bảng chân ESP32-S3 & đấu nối tín hiệu

| | |
|---|---|
| **Mục tiêu** | Chốt bảng chân GPIO và cách đấu dây tín hiệu cho mic, amply, LCD, nút bấm, servo và MOSFET |
| **Phụ thuộc** | HW1 (các nhánh nguồn) |
| **Kết quả giao nộp** | Bảng đấu dây từng module trong `docs/hardware/wiring.md` + hình `docs/hardware/wiring.svg` |

## 1. Bảng chân GPIO

Bảng dựa trên board `bread-compact-wifi-lcd` của xiaozhi để firmware gốc chạy được ngay ở P1. Chỉ đổi những chân cần thiết.

| GPIO | Chức năng | Hướng | Ghi chú |
|---|---|---|---|
| 4 | INMP441 WS | Ra | Giống bread board |
| 5 | INMP441 SCK | Ra | |
| 6 | INMP441 SD | Vào | |
| 7 | MAX98357 DIN | Ra | |
| 15 | MAX98357 BCLK | Ra | |
| 16 | MAX98357 LRC | Ra | |
| 47 | LCD SDI (MOSI) | Ra | |
| 21 | LCD SCK (SCLK) | Ra | |
| 40 | LCD DC | Ra | |
| 41 | LCD CS | Ra | SPI mode 0 |
| **39** | LCD RESET | Ra | **Đổi so với bread board (GPIO45).** GPIO45 là chân strapping VDD_SPI; nếu module LCD có điện trở kéo lên thì chip có thể không khởi động được |
| 42 | LCD LED (đèn nền) | Ra PWM | LEDC timer 0 / channel 0 (firmware xiaozhi dùng sẵn) |
| 17 | Servo trái (S0) | Ra PWM | LEDC timer 1, 50 Hz |
| 18 | Servo phải (S1) | Ra PWM | |
| 8 | Gate MOSFET đèn mắt | Ra PWM | LEDC timer 2, 25 kHz |
| 9 | Gate MOSFET output dự phòng | Ra PWM | |
| 10 | Nút PTT | Vào | Bấm thì nối GND; dùng pull-up nội |
| 11 | Nút mặt nạ | Vào | Bấm thì nối GND; dùng pull-up nội |
| 12 | SD MAX98357A (bật/tắt amply) | Ra | Qua R13 2.2kΩ; thấp = tắt, cao = bật. Thêm ở Rev B (`wiring.md` §3.4) |
| 1 | VSENSE (ADC1_CH0) | Vào analog | Cầu phân áp đo 5V_MAIN (HW1) |
| 0 | Nút BOOT (có sẵn trên devkit) | Vào | Giữ để vào chế độ cấu hình WiFi |
| 48 | LED RGB trên devkit | Ra | Firmware xiaozhi dùng làm đèn trạng thái |

**Chân không được dùng:**
- 26–32: flash.
- 33–37: PSRAM octal của N16R8.
- 19, 20: USB.
- 43, 44: UART0, dùng cho log.
- 3, 45, 46: strapping.

**Chân còn trống để mở rộng:** 2, 12, 13, 14, 38.

## 2. Đấu nối từng module

### INMP441 (mic)
| INMP441 | Nối tới |
|---|---|
| VDD | 3V3 |
| GND | GND |
| L/R | GND (kênh trái) |
| WS | GPIO4 |
| SCK | GPIO5 |
| SD | GPIO6 |

### MAX98357A (amply)
| MAX98357 | Nối tới |
|---|---|
| VIN | 5V_AUDIO (HW1) |
| GND | GND |
| LRC | GPIO16 |
| BCLK | GPIO15 |
| DIN | GPIO7 |
| SD | GPIO12 qua R13 2.2kΩ: firmware tắt amply khi không phát, bật (kênh trái) khi phát. Cách đơn giản: nối thẳng Vin (phương án A). **Không nối GND**: amply tắt |
| GAIN | Nối VIN = 6 dB (mặc định). Tiếng nhỏ: nối GND (12 dB). Không để trống (FAQ xiaozhi: GAIN hở gây tiếng khàn) |
| OUT+ / OUT− | Loa 3W 8Ω. **Không nối OUT− xuống GND** (ngõ ra cầu BTL) |

### LCD 2.4" SPI module MSP2401 (ST7789, 320×240)
Module không cảm ứng, header 14 chân 2.54mm. Tấm 240×320 dùng nằm ngang 320×240.

| Chân module | Nối tới |
|---|---|
| 1 · VCC | 3V3 (module nhận 3.3–5V, có LDO riêng) |
| 2 · GND | GND |
| 3 · CS | GPIO41 |
| 4 · RESET | GPIO39 |
| 5 · DC/RS | GPIO40 |
| 6 · SDI(MOSI) | GPIO47 |
| 7 · SCK | GPIO21 |
| 8 · LED | GPIO42 (PWM đèn nền, mức cao = sáng) |
| 9 · SDO(MISO), 10–14 · T_CLK, T_CS, T_DIN, T_DO, T_IRQ | Bỏ trống |

J7 trên PCB xếp đúng thứ tự chân 1–8 của module nên cáp cắm thẳng 1:1 ([wiring.md](../hardware/wiring.md) §3.5). Khe thẻ SD (header 4 chân ở cạnh kia) không dùng. Cùng mã MSP2401 có bản driver ILI9341: nhãn phải ghi ST7789.

### Nút bấm
Mỗi nút: một chân nối GPIO, chân kia nối GND, mắc thêm tụ 100nF song song để chống dội.
- PTT → GPIO10
- Mặt nạ → GPIO11

### Servo và MOSFET
Xem HW3. Dây tín hiệu servo: GPIO17 và GPIO18, mỗi dây qua điện trở 220Ω để bảo vệ chân GPIO.

## 3. Đi dây tín hiệu
- Mic đặt gần miệng và xa loa, bọc mút. Dây I2S tới mic ≤20cm, bện xoắn từng sợi tín hiệu với một sợi GND.
- Dây SPI tới LCD ≤20cm. Nếu ảnh bị nhiễu hoặc sai màu, thêm điện trở 33Ω nối tiếp trên SCK và giảm tốc độ SPI trong firmware.
- Dùng đầu cắm JST-PH hoặc header 2.54 để tháo được mic, LCD và nút bấm khi bảo trì.

## 4. Công việc
- [ ] Xác định đúng loại devkit đang có (DevKitC-1 hay bản clone) và kiểm tra các chân 39–42, 47, 48 có đưa ra header không
- [ ] Kiểm tra LCD 2.4" MSP2401: nhãn ghi driver ST7789; tấm IPS hay TN (`jarvis-helmet` hay `jarvis-helmet-noips` hiện đúng màu)
- [x] Viết bảng đấu dây và vẽ hình `wiring.svg` (`docs/hardware/wiring.md` mục 3; cùng bảng chân với `config.h` của firmware)
- [ ] Lắp trên breadboard (P1) và nạp firmware gốc `bread-compact-wifi-lcd`, chỉnh `DISPLAY_RST_PIN` = 39 và chọn LCD `ST7789 240*320` (firmware gốc hiện dọc)

## 5. Tiêu chí nghiệm thu
1. Firmware gốc khởi động bình thường, LCD hiển thị đúng màu và đúng hướng, không bị lệch ảnh.
2. Mic thu được giọng nói: đánh thức "Jarvis" được trong phòng yên tĩnh ở khoảng cách 30cm.
3. Loa phát rõ, không rè ở âm lượng 70%.
4. Không chân nào trong bảng trên trùng với chân strapping, flash hoặc PSRAM.

## Ghi chú triển khai (10/2026)

**Trạng thái:** bảng chân và sơ đồ xong, đã đưa vào `firmware/main/boards/jarvis-helmet/config.h` (LCD RST = GPIO39, LED trạng
thái GPIO48 – DevKitC-1 v1.1 dùng GPIO38, D24). LCD đổi từ ST7789 1.54" 240×240 sang module 2.4" MSP2401 (ST7789, ngang 320×240); firmware có 2 biến thể cho tấm IPS (mặc định) và TN (`-noips`). Rev B (10/2026): chân SD amply ← GPIO12, GAIN nối VIN, J7 xếp theo chân LCD, Q3 chống cắm ngược (`wiring.md` đầu file). **Chưa xác định loại
devkit/LCD thật và chưa lắp breadboard.**
