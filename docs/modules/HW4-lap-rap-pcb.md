# HW4 – Bố trí & hàn PCB đục lỗ 7×9cm, đi dây trong mũ

| | |
|---|---|
| **Mục tiêu** | Chuyển mạch từ breadboard lên PCB đục lỗ 7×9cm (khoảng 27×35 lỗ): gọn, tháo lắp được, chịu rung khi đội |
| **Phụ thuộc** | HW1, HW2, HW3 đã thử xong trên breadboard; FW1 chạy được |
| **Kết quả giao nộp** | `docs/hardware/perfboard-7x9.svg` (sơ đồ bố trí linh kiện và dây trên lưới lỗ), hướng dẫn hàn theo từng bước, ảnh mạch thật |

## 1. Bố trí (đặt cạnh 9cm theo chiều dọc)

```
        cột A ............ H  I ......... S  T ............ AA
hàng 1  ┌──────────────────────────────────────────────────────┐
        │ [J7 LCD 8P]   [J8 MIC 4P]    [J9 NÚT 3P]              │  ← cạnh trên: header cáp tín hiệu
        │                ┌───────────┐                          │
        │  MAX98357      │           │        Q1 (mắt) R3 R5    │
        │  + C3 C4       │  ESP32-S3 │        Q2 (dự phòng) R4 R6│
        │  [J10 LOA]     │  DevKit   │        D2                │
        │                │ (cắm qua  │        R7 R8             │
        │  R1 R2 C7      │ hàng rào  │        [J3 S0] [J4 S1]   │
        │  (VSENSE)      │  cái)     │        C1 1000µF         │
hàng 24 │                └───────────┘        [J6 MẮT] [J5 DP]  │
        │  D1  C5 C6          F1        C2                      │
        │  [J1 NGUỒN VÀO]  ●GND sao   [J2 → SW1 công tắc]       │  ← cạnh dưới: khối nguồn
hàng 35 └──────────────────────────────────────────────────────┘
```

- Khoảng cách giữa 2 hàng chân devkit phải **đo trên board thật**: DevKitC-1 là 2×22 chân; bản clone có thể khác. Từ đó chốt vị trí cột.
- Đường 5V_SERVO và GND servo: dùng dây đồng trần 0.8mm hoặc dây 22AWG chạy mặt dưới, đi ngắn nhất từ khối nguồn sang J3/J4. Không đi vòng qua vùng âm thanh.
- Vùng âm thanh (MAX98357, dây mic) nằm khác phía với servo và MOSFET để giảm nhiễu.
- Mọi module (devkit, MAX98357) cắm qua hàng rào cái để thay được.

## 2. Thứ tự hàn và thử

1. **Khối nguồn:** J1, F1, J2, D1, các tụ, điểm GND sao. Cấp điện và đo 5V_MAIN, 5V_ESP. Chưa cắm module nào.
2. **Hàng rào cái** cho devkit và MAX98357. Đo thông mạch từng chân theo bảng HW2.
3. **Khối công suất:** Q1, Q2, các điện trở, J3–J6. Đo thông mạch.
4. **Header cáp:** J7 (LCD), J8 (mic), J9 (nút), J10 (loa).
5. Khi chưa cắm module, đo điện trở giữa 5V và GND: phải lớn hơn 1kΩ.
6. Cắm devkit, nạp firmware và chạy lại các bài thử của HW1–HW3.

## 3. Đi dây trong mũ

- Cáp tới mic, LCD và nút bấm: bện xoắn, ≤20cm, có đầu cắm có khóa (JST-PH/XH). Bọc co nhiệt ở các mối hàn.
- Dây servo và dây đèn mắt đi xa bản lề và cơ cấu chuyển động. Chừa đoạn dây dư ở chỗ mặt nạ chuyển động để không bị kéo căng.
- Đặt mic gần miệng, bọc mút, xa loa. Loa đặt ở phía tai, quay ra xa mic.
- Cố định PCB bằng ốc M3 hoặc đế nhựa, có đệm chống rung. Không dán keo nóng lên linh kiện bị nóng.
- Công tắc SW1 và cổng cắm nguồn đặt ở chỗ dễ với tay khi đang đội mũ.

## 4. Công việc
- [ ] Đo kích thước thật của devkit và MAX98357, vẽ `perfboard-7x9.svg` (lưới 27×35, đánh số hàng/cột)
- [ ] Hàn theo thứ tự ở mục 2, thử từng bước
- [ ] Lắp vào mũ, chụp ảnh, cập nhật sơ đồ nếu có thay đổi

## 5. Tiêu chí nghiệm thu
1. Đo thông mạch đúng 100% theo bảng HW2/HW3, không có chập.
2. Các bài thử ở HW1 (thử tải nặng nhất) và HW3 vẫn đạt sau khi lên PCB.
3. Lắc mạnh mũ (mô phỏng lúc đội) trong khi đang chạy: không reset, không mất tiếng, không mất hình.
