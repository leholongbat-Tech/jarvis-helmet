# BOM – Jarvis Helmet (phần điện)

| | |
|---|---|
| **Phiên bản** | Rev A – 2026-10-06 |
| **Nguồn gốc** | README (phần cứng chính), BOM ở [HW1](../modules/HW1-nguon-dien.md) §4 và [HW3](../modules/HW3-mach-cong-suat.md) §4, linh kiện mô tả trong [HW2](../modules/HW2-dau-noi-tin-hieu.md) và [HW4](../modules/HW4-lap-rap-pcb.md) |
| **Đấu dây** | [wiring.md](wiring.md) – ký hiệu trong bảng này trùng với ký hiệu ở đó |
| **Phạm vi** | Phần điện điều khiển. Khung, bản lề, vỏ mũ làm riêng (không có ở đây) |

Ký hiệu có dấu * là ký hiệu không có trong BOM của HW1/HW3: J2, J7–J10 lấy từ hình bố trí ở HW4; C8, C9, R9–R11, U1–U4, LS1, M1/M2, E1/E2, SW2/SW3 do `wiring.md` đặt thêm cho linh kiện đã mô tả trong HW2/HW3; R_EYE1/2 là "R_EYE ×2" của HW3, đánh số riêng cho từng mắt.

## 1. Module chính

| Ký hiệu | Linh kiện | Thông số | SL | Ghi chú/thay thế |
|---|---|---|---|---|
| U1* | ESP32-S3-DevKitC-1 N16R8 | Module ESP32-S3-WROOM-1-N16R8: flash 16MB, PSRAM 8MB octal (3.3V), 2 hàng × 22 chân | 1 | Bản clone dùng được nếu đưa ra đủ chân 39–42, 47, 48. DevKitC-1 **v1.1** để LED RGB ở GPIO38 (v1.0: GPIO48) – ghi lại phiên bản |
| U3* | INMP441 | Mic MEMS I2S, 3.3V, module tròn 6 chân (VDD, GND, L/R, WS, SCK, SD) | 1 | |
| U2* | MAX98357A | Amply class-D I2S, ~1.8W vào 8Ω ở 5V, module 7 chân (LRC, BCLK, DIN, GAIN, SD, GND, Vin) | 1 | Gắn trên hàng rào cái để thay được |
| LS1* | Loa | 3W 8Ω, đường kính 28–40mm | 1 | Kèm dây và giắc JST-PH 2P |
| U4* | LCD ST7789 | 1.54" IPS 240×240, SPI, 3.3V, loại 7 hoặc 8 chân | 1 | 8 chân (có CS) → `LCD_ST7789_240X240`; 7 chân → `LCD_ST7789_240X240_7PIN` (SPI mode 3) |
| M1, M2* | Servo MG90S | Bánh răng kim loại, 4.8–6V, xung 500–2400µs | 2 | M1 = S0 trái, M2 = S1 phải (lắp đối xứng) |
| E1, E2* | Module LED mắt cosplay | Loại chạy pin CR2032 3V | 2 (1 cặp) | Tháo pin; mỗi module một điện trở R_EYE khi chạy 5V |
| – | PCB đục lỗ | 7×9cm, bước 2.54mm, nên dùng loại 2 mặt mạ thiếc | 1 | Mua thêm 1 dự phòng. Bố trí trong `wiring.md` dùng lưới 27×35; **đếm lỗ trước khi mua** (nhiều tấm 7×9 chỉ có 24×30 lỗ) |
| – | Sạc dự phòng | 5V, dòng ra ≥2.4A, cổng USB-A hoặc USB-C | 1 | Không tự tắt khi tải ~150mA (thử IT1 E14) |
| SW2, SW3* | Nút nhấn nhả | Thường hở (momentary), gắn vỏ (ví dụ Ø12mm) hoặc nút 6×6mm | 2 | SW2 = PTT, SW3 = mặt nạ |

## 2. Nguồn (HW1)

| Ký hiệu | Linh kiện | Thông số | SL | Ghi chú/thay thế |
|---|---|---|---|---|
| J1 | Domino 2P | Bước 5.08mm, hàn PCB (kiểu KF301-2P) | 1 | Nguồn vào |
| F1 | Cầu chì tự phục hồi (PPTC) | 2.5A hold, ≥6V, chân cắm bước 5.08mm | 1 | Ví dụ họ MF-R250 / RXEF250 |
| SW1 | Công tắc gạt | ≥3A, gắn vỏ mũ | 1 | Nối vào PCB qua J2 |
| D1 | Diode Schottky | SS34 (3A, SMA) hoặc 1N5819 (1A, DO-41) | 1 | 1N5819 chân cắm dễ hàn trên PCB đục lỗ; dòng qua D1 ≤~0.6A |
| C1 | Tụ hóa | 1000µF/10V, Ø10mm, bước chân 5mm | 1 | 16V nếu mua được cùng cỡ; loại 105°C, low-ESR |
| C3 | Tụ hóa | 470µF/10V, Ø6.3–8mm | 1 | |
| C5 | Tụ hóa | 220µF/10V, Ø6.3mm | 1 | |
| C2, C4, C6, C7 | Tụ gốm | 100nF (104), 50V, bước 2.54 hoặc 5.08mm | 4 | |
| R1, R2 | Điện trở | 100kΩ **1%**, 1/4W | 2 | Cầu phân áp VSENSE |
| – | Cáp USB-A | ≤50cm, dây nguồn ≥22AWG | 1 | **Hoặc** mạch USB-C sink có 5.1kΩ ở CC1 và CC2 |
| – | Dây silicone | 22AWG đỏ + đen | 1m mỗi màu | Theo HW1; xem thêm mục 6 |

## 3. Công suất (HW3)

| Ký hiệu | Linh kiện | Thông số | SL | Ghi chú/thay thế |
|---|---|---|---|---|
| Q1, Q2 | MOSFET kênh N logic-level | AO3400A: 30V, 5.7A, Rds(on) <50mΩ ở Vgs 2.5V, SOT-23 (1 = G, 2 = S, 3 = D) | 2 | Dòng mỗi kênh <150mA có thể thay 2N7000 (TO-92, thứ tự chân khác) |
| – | Đế chuyển SOT-23 | SOT-23 → 3 chân bước 2.54mm | 2 | Kiểm tra đế đưa chân nào ra lỗ nào |
| R3, R4 | Điện trở gate | 100Ω, 1/4W | 2 | |
| R5, R6 | Điện trở kéo xuống | 100kΩ, 1/4W | 2 | Đặt giữa gate và source |
| R7, R8 | Điện trở tín hiệu servo | 220Ω, 1/4W | 2 | |
| R_EYE1, R_EYE2* | Điện trở hạn dòng đèn mắt | Chọn theo đo (ví dụ 51Ω 1/4W) | 2 | Mua sẵn vài giá trị E24 33–100Ω; công suất ≥ 2 × I²R |
| D2 | Diode Schottky | 1N5819 | 1 | Chống ngược cho tải cảm ở J5 |
| J3, J4 | Header đực 3P | 2.54mm, khớp giắc servo | 2 | Hoặc JST-XH 3P |
| J5 | Domino 2P | Bước 5.08mm | 1 | Output dự phòng, ≤1A |
| J6 | Header 2P hoặc JST-PH 2P | 2.54mm / 2.0mm | 1 | Đổi sang 3P nếu muốn đặt R_EYE trên PCB |

## 4. Tín hiệu (HW2)

| Ký hiệu | Linh kiện | Thông số | SL | Ghi chú/thay thế |
|---|---|---|---|---|
| C8, C9* | Tụ gốm chống dội nút | 100nF (104) | 2 | C8 = PTT, C9 = mặt nạ |
| R9, R10* | Điện trở kéo xuống tín hiệu servo | 10kΩ, 1/4W | 2 | **Tùy chọn**: chỉ lắp nếu servo giật khi ESP32 khởi động |
| R11* | Điện trở nối tiếp SCL LCD | 33Ω, 1/4W | 1 | **Tùy chọn**: chỉ lắp nếu ảnh LCD nhiễu/sai màu |

## 5. Đầu nối & đế cắm (HW4)

| Ký hiệu | Linh kiện | Thông số | SL | Ghi chú/thay thế |
|---|---|---|---|---|
| J2* | Domino 2P | Bước 5.08mm | 1 | Nối tới SW1; hoặc JST-XH 2P (≥3A) |
| J7* | Header đực 8P | 2.54mm | 1 | Cáp LCD; hoặc JST-PH 8P |
| J8* | Header đực 5P | 2.54mm | 1 | Cáp mic; hoặc JST-PH 5P. HW4 ghi 4P là thiếu 1 chân |
| J9* | Header đực 3P | 2.54mm | 1 | Cáp nút (PTT, mặt nạ, GND chung) |
| J10* | Header đực 2P | 2.54mm | 1 | Loa; hoặc JST-PH 2P |
| – | Hàng rào cái 1×22 | 2.54mm | 2 | Đế cắm devkit. Nếu cắt từ thanh 1×40 thì cần 2 thanh (cắt hàng rào cái mất 1 chân ở chỗ cắt); phần thừa đủ làm đế 1×7 cho MAX98357A |
| – | Hàng rào cái 1×7 | 2.54mm | 1 | Đế cắm MAX98357A |
| – | Header đực thanh 1×40 | 2.54mm | 1–2 | Cắt làm J3, J4, J6–J10 |
| – | Vỏ giắc + chân bấm | JST-PH 2.0mm (2P, 3P, 5P, 8P) hoặc Dupont 2.54mm | 1 bộ | Cho cáp mic, LCD, nút, loa, đèn mắt; cần kìm bấm phù hợp |

## 6. Dây & vật tư hàn

| Ký hiệu | Linh kiện | Thông số | SL | Ghi chú/thay thế |
|---|---|---|---|---|
| – | Dây silicone | 22AWG đỏ/đen | thêm ~1m | Ngoài 1m của HW1: dây SW1, nối dài servo, dây đèn mắt |
| – | Dây đồng trần | Ø0.8mm | 0.5m | Thanh điểm sao, cặp 5V_SERVO/GND_SERVO, cầu nối dài |
| – | Dây có vỏ mảnh | AWG26–30 (dây wire-wrap), nhiều màu | 5–10m | Dây tín hiệu mặt dưới PCB |
| – | Cáp tín hiệu mềm | 26–28AWG silicone hoặc cáp dẹt | 2m | Cáp mic, LCD, nút ≤20cm, bện xoắn |
| – | Ống co nhiệt | Bộ nhiều cỡ 1.5–6mm | 1 bộ | Mọi mối hàn dây, R_EYE trên dây |
| – | Thiếc hàn, flux | Thiếc 0.6–0.8mm lõi nhựa thông | – | |

## 7. Cơ khí & cố định

| Ký hiệu | Linh kiện | Thông số | SL | Ghi chú/thay thế |
|---|---|---|---|---|
| – | Trụ M3 + ốc + đai ốc | Trụ nhựa/đồng 5–10mm | 4 bộ | Bắt PCB vào mũ |
| – | Đệm chống rung | Cao su hoặc xốp EVA | 4 | Lót dưới trụ M3 |
| – | Mút tiêu âm | Xốp mềm | 1 miếng | Bọc mic, chắn gió |
| – | Dây rút nhựa | 2.5mm | 20 | Gom dây, giữ dây servo xa bản lề |
| – | Băng dính 2 mặt xốp | – | 1 cuộn | Gắn loa, LCD tạm thời |
| – | Keo epoxy / keo nhiệt | – | – | Cố định dây; **không** dán lên linh kiện nóng (F1, Q2 khi tải lớn) |

## 8. Dụng cụ đo & thử (không lắp lên mũ)

| Ký hiệu | Linh kiện | Thông số | SL | Ghi chú/thay thế |
|---|---|---|---|---|
| – | Đồng hồ vạn năng | V, Ω, thông mạch, thang diode, mA | 1 | Mọi bước thử |
| – | Nguồn bàn giới hạn dòng | 0–5V, giới hạn 100mA | 1 | Đo chọn R_EYE ở 3.0V; không có thì dùng pin CR2032 mới |
| – | Tải giả | Điện trở 10Ω/5W (≈0.5A ở 5V) | 1 | Thử Q2 10 phút; hoặc quạt 5V |
| – | Nhiệt kế | Hồng ngoại hoặc đầu dò | 1 | Kiểm Q2 <50°C |
| – | Máy hiện sóng | – | (tùy chọn) | Xem sụt áp 5V_MAIN khi servo khởi động |
| – | Breadboard + dây cắm | 2 tấm 63 cột + 3 ray (như bản thử) | 1 bộ | Thử P1/P3 trước khi hàn. Vật tư thêm (mạch USB-C sink, nút 6×6, LED 3mm, dây 22AWG…): [breadboard.md](breadboard.md) mục 4 |

## 9. Không dùng

| Ký hiệu | Linh kiện | Thông số | SL | Ghi chú/thay thế |
|---|---|---|---|---|
| – | PCM5102 | Module DAC I2S | 0 | Có trong danh sách linh kiện ban đầu nhưng **không dùng** trong thiết kế hiện tại; âm thanh ra qua MAX98357A |

## 10. Tổng hợp linh kiện thụ động

| Giá trị | Ký hiệu | SL cần | Gợi ý mua |
|---|---|---|---|
| Tụ gốm 100nF | C2, C4, C6, C7, C8, C9 | 6 | 20 |
| 100kΩ 1% | R1, R2 | 2 | 10 |
| 100kΩ | R5, R6 | 2 | (chung với trên) |
| 100Ω | R3, R4 | 2 | 10 |
| 220Ω | R7, R8 | 2 | 10 |
| 10kΩ (tùy chọn) | R9, R10 | 2 | 10 |
| 33Ω (tùy chọn) | R11 | 1 | 10 |
| R_EYE | R_EYE1, R_EYE2 | 2 | Bộ E24 33–100Ω, 1/4W và 1/2W |
| 1N5819 | D1 (nếu không dùng SS34), D2 | 2 | 5 |
| Tụ hóa 1000µF / 470µF / 220µF | C1 / C3 / C5 | 1 / 1 / 1 | 2 mỗi loại |
| AO3400A + đế SOT-23 | Q1, Q2 | 2 | 4 |

Nên mua dư: 1 INMP441, 1 MAX98357A, 1 servo MG90S, 1 cầu chì PPTC 2.5A.
