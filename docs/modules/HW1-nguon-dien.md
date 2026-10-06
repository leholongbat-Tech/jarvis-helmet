# HW1 – Nguồn & phân phối điện

| | |
|---|---|
| **Mục tiêu** | Lấy điện từ sạc dự phòng 5V và chia cho ESP32, âm thanh, servo, đèn mắt. Không được sụt áp làm ESP32 reset |
| **Phụ thuộc** | – |
| **Kết quả giao nộp** | Sơ đồ nguồn trong `docs/hardware/wiring.md` + `docs/hardware/wiring.svg`, bảng ngân sách dòng, BOM phần nguồn |

## 1. Ngân sách dòng (ước tính)

| Tải | Trung bình | Đỉnh | Ghi chú |
|---|---|---|---|
| ESP32-S3 + WiFi | 120 mA | 500 mA | Đỉnh khi phát WiFi |
| LCD + đèn nền | 50 mA | 60 mA | Lấy từ 3.3V của devkit |
| INMP441 | 1.4 mA | – | |
| MAX98357 + loa 8Ω | 100 mA | 450 mA | Khoảng 1.8W ở 5V |
| 2× MG90S | 0 (đứng yên) / 300 mA (đang chạy) | 1.5–2 A (kẹt) | Đỉnh khi khởi động và khi kẹt cơ khí |
| Đèn mắt | 20–60 mA | – | Đo ở HW3 |
| **Tổng** | **~0.4 A** (lúc nói chuyện) | **~3 A** (trường hợp xấu nhất) | Sạc ≥2.4A dùng được, nhưng firmware phải tránh mọi đỉnh dòng xảy ra cùng lúc (FW2) |

## 2. Sơ đồ nguồn

```
Sạc dự phòng ─ cáp USB → Domino J1 ─► F1 cầu chì tự phục hồi 2.5A ─► SW1 công tắc ─► 5V_MAIN ●(điểm GND hình sao)
                                                                                 │
   ┌──────────────────────────────┬───────────────────────┬──────────────────────┼────────────────────┐
   ▼                              ▼                       ▼                      ▼                    ▼
5V_SERVO                      5V_AUDIO                 5V_ESP                 5V_EYES             VSENSE
C1 1000µF/10V + C2 100nF      C3 470µF/10V + C4 100nF  D1 SS34 (chống ngược)   → HW3                R1 100k ─┬─ GPIO1
→ jack J3, J4 (servo)         → VIN MAX98357           C5 220µF/10V + C6 100nF                      R2 100k ─┴─ GND
dây 22AWG, về thẳng GND sao                            → chân 5V của devkit                          C7 100nF // R2
                                                       (LDO trên devkit → 3V3: ESP32, LCD, mic)
```

Nguyên tắc:
- **Tách nhánh:** dây nguồn servo đi riêng từ điểm 5V_MAIN; dây GND của servo về thẳng điểm sao, không đi chung với GND của tín hiệu và âm thanh.
- **D1 (SS34)** đặt trước chân 5V của devkit. Nó chặn dòng chảy ngược giữa cổng USB máy tính và nhánh servo/sạc khi nạp code lúc đang cắm sạc. Sau D1 còn khoảng 4.65V, đủ cho LDO của devkit (AMS1117 cần tối thiểu ~4.4V).
- **Tụ đặt sát tải:** C1 ở chỗ cắm servo, C3 ở MAX98357, C5 ở devkit.
- **Đo điện áp (VSENSE):** cầu phân áp 1:2 vào GPIO1 (ADC1, dùng được cùng lúc với WiFi). Firmware hiển thị giá trị lên HUD và cảnh báo khi 5V_MAIN < 4.6V.

## 3. Đầu vào từ sạc dự phòng

- **Cổng USB-A:** cắt một cáp USB-A, lấy dây đỏ (+) và đen (−) bắt vào domino J1. Cáp ≤50cm, dây nguồn tiết diện ≥22AWG.
- **Cổng USB-C:** bắt buộc dùng mạch USB-C có điện trở **5.1kΩ ở chân CC** (loại "USB-C sink/breakout"). Không có điện trở này, cổng C sẽ không cấp điện.
- Sạc dự phòng có thể **tự tắt khi tải nhỏ**. Hệ thống luôn kéo khoảng 150mA vì WiFi luôn bật, thường đủ để sạc không tắt. Phải kiểm chứng bằng bài thử 2 giờ ở IT1.
- HUD **không đo được % pin** của sạc. Chỉ hiện điện áp 5V_MAIN.

## 4. BOM phần nguồn

| Ký hiệu | Linh kiện | SL |
|---|---|---|
| J1 | Domino 2P 5.08mm | 1 |
| F1 | Cầu chì tự phục hồi 2.5A (hold) | 1 |
| SW1 | Công tắc gạt ≥3A | 1 |
| D1 | SS34 hoặc 1N5819 | 1 |
| C1 | Tụ hóa 1000µF/10V | 1 |
| C3 | Tụ hóa 470µF/10V | 1 |
| C5 | Tụ hóa 220µF/10V | 1 |
| C2, C4, C6, C7 | Tụ gốm 100nF | 4 |
| R1, R2 | 100kΩ 1% | 2 |
| – | Cáp USB-A (hoặc mạch USB-C sink 5.1k) | 1 |
| – | Dây silicone 22AWG đỏ/đen | 1m |

## 5. Công việc

- [x] Vẽ sơ đồ nguồn (SVG) và viết mục nguồn trong `wiring.md` (mục 2: cây nguồn, nối đất hình sao, D1, tụ lọc, VSENSE, ngân sách dòng, quy trình đo)
- [ ] Đo điện áp không tải của sạc ở cả cổng A và cổng C (nếu có)
- [ ] Lắp thử nhánh nguồn trên breadboard, đo từng nhánh **trước khi** cắm ESP32 và các module
- [ ] Thử tải bằng servo: 2 servo chạy qua lại liên tục, đo 5V_MAIN bằng đồng hồ hoặc máy hiện sóng

## 6. Tiêu chí nghiệm thu

1. Không tải: 5V_MAIN trong khoảng 4.9–5.25V, 5V_ESP (sau D1) ≥4.5V.
2. Thử nặng nhất (2 servo chạy + loa âm lượng tối đa + WiFi phát liên tục) trong 10 phút: **không có lần reset nào**. Kiểm tra bằng log `esp_reset_reason`; giá trị ADC VSENSE ≥4.6V.
3. Cắm cáp USB máy tính vào devkit trong lúc sạc dự phòng đang bật: không có dòng chảy ngược. Đo điện áp tại cổng USB khi sạc tắt.

## Ghi chú triển khai (10/2026)

**Trạng thái:** tài liệu và sơ đồ xong: [`docs/hardware/wiring.md`](../hardware/wiring.md) mục 2, `wiring.svg`, BOM phần nguồn
trong [`docs/hardware/BOM.md`](../hardware/BOM.md), bản thử breadboard [`docs/hardware/breadboard.md`](../hardware/breadboard.md).
Firmware đọc VSENSE (`VbusMonitor`, ADC1_CH0 ×2) và báo `vbus_mv` trong `self.system.get_status` và trên HUD.
**Chưa lắp và đo** (các mục còn trống ở trên cần phần cứng thật).
