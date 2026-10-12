# HW3 – Mạch công suất: servo, đèn mắt, output dự phòng

| | |
|---|---|
| **Mục tiêu** | Nối 2 servo, đèn mắt (module CR2032) và một output dự phòng để ESP32 điều khiển an toàn bằng PWM |
| **Phụ thuộc** | HW1 (5V_SERVO, 5V_EYES), HW2 (GPIO17, 18, 8, 9) |
| **Kết quả giao nộp** | Mục "Công suất" trong `wiring.md`, quy trình đo đèn mắt, BOM |

## 1. Servo MG90S ×2

```
5V_SERVO ──────────────── dây đỏ   (servo S0, S1)
GND (điểm sao) ─────────── dây nâu
GPIO17 ──[220Ω]─────────── dây cam S0 (trái)
GPIO18 ──[220Ω]─────────── dây cam S1 (phải, lắp đối xứng → firmware đảo chiều)
```

- Đầu nối: header đực 3 chân 2.54mm (khớp với giắc servo) hoặc JST-XH 3P.
- MG90S nhận được tín hiệu 3.3V, không cần mạch chuyển mức.
- Dải xung khoảng 500–2400µs. Góc đóng/mở thực tế lấy từ bước hiệu chỉnh ở FW2.
- Tụ C1 1000µF (HW1) đặt ngay chỗ cắm servo.
- Nếu servo bị giật trong lúc ESP32 khởi động (khi đó chân GPIO đang thả nổi), thêm điện trở 10kΩ kéo dây tín hiệu xuống GND.

## 2. Đèn mắt – module LED cosplay chạy CR2032

Module mua về là một hộp pin CR2032 3V nuôi cả 2 LED (2 mắt) nối song song. Pin cúc áo có điện trở trong lớn nên tự hạn dòng cho LED. Khi chuyển sang nguồn 5V thì **bắt buộc thêm một điện trở hạn dòng** nối tiếp với cả module.

### Mạch
```
5V_EYES ──[R_EYE]──► (+) module đèn mắt: 2 LED song song (−) ──► Drain Q1 AO3400A
                                                                 Source Q1 ──► GND
GPIO8 ──[100Ω]──► Gate Q1 ──[100kΩ]──► GND       (kéo xuống: đèn tắt trong lúc khởi động/reset)
```
- 2 mắt dùng chung một kênh và **một** điện trở R_EYE: tháo pin, hàn dây vào lá + (qua R_EYE) và lá − của hộp pin. Không lắp lại pin khi đã nối 5V.
- Nếu module có công tắc riêng thì để công tắc luôn ở vị trí ON, hoặc nối tắt công tắc. Module có nút đổi chế độ (có IC) thì chọn chế độ sáng đứng; IC chạy sai khi PWM thì nối thẳng vào cụm LED hoặc chỉ bật/tắt 0/100%.
- Q1 là AO3400A, gắn trên đế chuyển SOT-23 sang DIP: logic-level, Rds(on) <50mΩ ở Vgs 2.5V. Nếu dòng mỗi kênh <150mA có thể thay bằng 2N7000 (TO-92).

### Quy trình đo để chọn R_EYE
1. Tháo pin CR2032, xác định cực + và − của hộp pin. Kiểm tra trên module đã có sẵn điện trở hay chưa.
2. Cấp **3.0V từ nguồn có giới hạn dòng 100mA** vào hai lá hộp pin, đồng hồ đo dòng mắc nối tiếp. Không có nguồn chỉnh được thì dùng pin CR2032 mới và đo dòng.
3. Ghi lại dòng tổng I của cả 2 LED khi độ sáng vừa ý (I_target, thường ≤ 40mA) và điện áp V_f trên module.
4. Tính:
   - R_EYE = (5.0 − V_f) / I_target.
   - Công suất P = I_target² × R_EYE; chọn điện trở có công suất ≥ 2P.
   - Ví dụ: V_f = 2.9V, I = 30mA (cả 2 LED) → R = 70Ω → chọn **75Ω 1/4W** (dòng thật ~28mA).
5. Ghi kết quả đo vào bảng trong `wiring.md`.

## 3. Output dự phòng

```
5V_MAIN ──┬──► (+) domino J5 ─── tải ─── (−) domino J5 ──► Drain Q2 AO3400A ── Source ──► GND
          └──|◄ D2 1N5819 (cathode về 5V, anode về Drain): diode chống ngược cho tải cảm (relay, quạt, motor)
GPIO9 ──[100Ω]──► Gate Q2 ──[100kΩ]──► GND
```
- Dòng tải tối đa **1A**, bị giới hạn bởi ngân sách dòng (HW1) chứ không phải bởi MOSFET.
- Firmware hỗ trợ cả bật/tắt và PWM.

## 4. BOM phần công suất

| Ký hiệu | Linh kiện | SL |
|---|---|---|
| Q1, Q2 | AO3400A + đế chuyển SOT-23 | 2 |
| R3, R4 | 100Ω (gate) | 2 |
| R5, R6 | 100kΩ (kéo xuống) | 2 |
| R7, R8 | 220Ω (tín hiệu servo) | 2 |
| R_EYE | Chọn theo kết quả đo | 1 |
| D2 | 1N5819 | 1 |
| J3, J4 | Header 3P cho servo | 2 |
| J5 | Domino 2P (output dự phòng) | 1 |
| J6 | Header 2P hoặc JST cho đèn mắt | 1 |

## 5. Công việc
- [ ] Mở module đèn mắt, đo theo quy trình ở mục 2, chọn R_EYE
- [ ] Lắp thử Q1 trên breadboard, dùng PWM điều chỉnh độ sáng
- [ ] Thử Q2 với một tải giả 500mA (điện trở 10Ω/5W, hoặc quạt 5V)
- [ ] Ghi kết quả vào `wiring.md`

## 6. Tiêu chí nghiệm thu
1. Đèn mắt tắt hoàn toàn trong lúc ESP32 khởi động, reset và nạp code (nhờ điện trở kéo xuống).
2. PWM 0–100% thay đổi độ sáng mượt, không nháy thấy được bằng mắt, không nghe tiếng rít (25 kHz, ngoài dải nghe; Rev A dùng 5 kHz).
3. Q2 đóng tải 500mA liên tục 10 phút, MOSFET nguội hơn 50°C.
4. Servo không rung khi đứng yên, không giật khi ESP32 khởi động.

## Ghi chú triển khai (10/2026)

**Trạng thái:** sơ đồ và quy trình đo chọn R_EYE xong ([`docs/hardware/wiring.md`](../hardware/wiring.md) mục 4); firmware điều
khiển servo (FW2) và MOSFET đèn mắt/output (FW3) theo đúng chân và tần số PWM ở đây. **Chưa đo module đèn mắt và chưa thử tải.**
