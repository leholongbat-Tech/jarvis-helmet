# Bản thử trên breadboard – Jarvis Helmet

| | |
|---|---|
| **Phiên bản** | Rev A – 2026-10-06 (bản đầu, chưa cắm thử trên phần cứng thật) |
| **Mục đích** | Thử từng tính năng trước khi hàn PCB (HW4): P1 (mic, loa, LCD, nói chuyện với server) và P3 (servo, đèn mắt, output dự phòng, nút, đo 5V) |
| **Phần cứng** | 2 tấm breadboard 63 cột + 3 thanh ray ghép như ảnh, ESP32-S3-DevKitC-1 N16R8 (clone 2 cổng USB-C) đã cắm sẵn |
| **Bảng chân** | Giữ nguyên [wiring.md](wiring.md) §3.1 (HW2) → firmware `jarvis-helmet` chạy **không sửa `config.h`** |
| **Linh kiện** | Như [BOM.md](BOM.md); phần thêm riêng cho breadboard ở mục 4 |
| **Hình** | [breadboard.svg](breadboard.svg) (nhìn từ trên, cột 1 bên phải như số in trên board) |
| **Nguồn dữ liệu** | `tools/hardware/breadboard_layout.py`: khai báo vị trí linh kiện và dây, kiểm tra trùng lỗ, chân cấm và chập mạng, rồi sinh hình và 3 bảng có dấu `BEGIN/END` trong file này. Muốn đổi bố trí: sửa script, chạy `python tools/hardware/breadboard_layout.py`, **không sửa tay các bảng đó** |

## 0. Quy ước

| Ký hiệu | Ý nghĩa |
|---|---|
| `T`, `D` | Tấm breadboard **trên** (sát ray trên cùng) và tấm **dưới** |
| Hàng a–e, f–j | Nửa trên và nửa dưới của mỗi tấm, ngăn bởi rãnh giữa. 5 lỗ a–e của một cột nối với nhau, 5 lỗ f–j nối với nhau; hai nửa **không** nối |
| Cột 1…63 | Theo số in trên board: cột 1 ở bên phải, cột 63 ở bên trái (phía cổng USB của devkit) |
| `T.g51` | Tấm trên, hàng g, cột 51 |
| `RT`, `RG`, `RD` | Thanh ray trên cùng, ray giữa (giữa hai tấm), ray dưới cùng. Mỗi ray 2 hàng: **−** (vạch xanh) và **+** (vạch đỏ) |
| `RT+@3` | Lỗ hàng + của ray RT nằm thẳng cột 3. Lỗ ray theo nhóm 5 ở cột 3–7, 9–13, …, 57–61; cột 1, 2, 8, 14, …, 62, 63 không có lỗ ray (ước lượng từ ảnh: lỗ ray thật có thể lệch nửa bước so với cột, cắm vào lỗ ray gần nhất) |
| `DK-L4`, `DK-R17` | Như wiring.md: chân 4 hàng J1, chân 17 hàng J3 của devkit, đếm từ đầu anten |
| U1–U4, C1–C7, R1–R10, Q1/Q2, D1/D2, J3–J5, F1, SW2/SW3, LS1 | Trùng ký hiệu wiring.md |
| `BUSB`, `BL1`–`BL3`, `RL` | Chỉ có trên breadboard: mạch USB-C sink, LED thử, điện trở cho LED thử |
| `W1`–`W35`, `M1`–`M5` | Dây nhảy trên breadboard; dây Dupont của mic |

## 1. Devkit trên breadboard

Đối chiếu ảnh: hàng chân J1 cắm ở **hàng h tấm trên**, J3 ở **hàng b tấm dưới**, cả hai từ **cột 42** (đầu anten: 3V3 / GND) tới **cột 63** (đầu USB: GND / GND). Chân `DK-Ln` và `DK-Rn` ở cột 41+n. Thứ tự nhãn in trên clone trong ảnh khớp DevKitC-1 v1.0.

- Thân devkit che hàng i, j tấm trên và hàng a tấm dưới: mỗi chân J1 chỉ còn **2 lỗ trống (f, g)**, mỗi chân J3 còn **3 lỗ (c, d, e)**.
- Ray giữa `RG` nằm dưới thân devkit từ cột 38 trở đi nên không dùng.
- Anten (cột 39–41, chìa ra khỏi hàng chân) để thoáng: không cắm linh kiện, không vắt dây ngang qua.
- Khoảng cách hai hàng chân ước lượng từ ảnh là 9 bước (≈22.9mm). Vẫn phải đo bằng thước kẹp để chốt cột R/S cho PCB (wiring.md §5.1).
- Clone trong ảnh có cầu hàn ghi `IN-OUT` cạnh chân 5Vin: **để nguyên, không hàn nối** (xem bước B0.5).

<!-- BEGIN:devkit -->
| Cột | J1 – tấm trên (chân ở `T.h`) | Dùng (lỗ `T.f`, `T.g`) | J3 – tấm dưới (chân ở `D.b`) | Dùng (lỗ `D.c`…`D.e`) |
|---|---|---|---|---|
| 42 | `DK-L1` 3V3 | 3V3 → LCD VCC (W5) | `DK-R1` GND | GND → R2, C7 (W24) |
| 43 | `DK-L2` 3V3 | 3V3 → mic VDD (M1) | `DK-R2` TX | **không nối** (UART0 TX, log) |
| 44 | `DK-L3` RST | **không nối** (EN/RST) | `DK-R3` RX | **không nối** (UART0 RX, log) |
| 45 | `DK-L4` GPIO4 | Mic WS (M3) | `DK-R4` GPIO1 | VSENSE (W23) |
| 46 | `DK-L5` GPIO5 | Mic SCK (M4) | `DK-R5` GPIO2 | trống |
| 47 | `DK-L6` GPIO6 | Mic SD (M5) | `DK-R6` GPIO42 | LCD BLK (W7) |
| 48 | `DK-L7` GPIO7 | Amply DIN (W16) | `DK-R7` GPIO41 | LCD CS (W8) |
| 49 | `DK-L8` GPIO15 | Amply BCLK (W17) | `DK-R8` GPIO40 | LCD DC (W9) |
| 50 | `DK-L9` GPIO16 | Amply LRC (W18) | `DK-R9` GPIO39 | LCD RES (W10) |
| 51 | `DK-L10` GPIO17 | Servo S0 (W34 → R7) | `DK-R10` GPIO38 | trống (DevKitC-1 v1.1: LED RGB) |
| 52 | `DK-L11` GPIO18 | Servo S1 (W35 → R8) | `DK-R11` GPIO37 | **không nối** (PSRAM octal) |
| 53 | `DK-L12` GPIO8 | Gate đèn mắt (W26 → R3) | `DK-R12` GPIO36 | **không nối** (PSRAM octal) |
| 54 | `DK-L13` GPIO3 | **không nối** (strapping) | `DK-R13` GPIO35 | **không nối** (PSRAM octal) |
| 55 | `DK-L14` GPIO46 | **không nối** (strapping) | `DK-R14` GPIO0 | **không nối** (nút BOOT có sẵn) |
| 56 | `DK-L15` GPIO9 | Gate output dự phòng (W28 → R4) | `DK-R15` GPIO45 | **không nối** (strapping) |
| 57 | `DK-L16` GPIO10 | Nút PTT (W19) | `DK-R16` GPIO48 | **không nối** (LED RGB có sẵn) |
| 58 | `DK-L17` GPIO11 | Nút mặt nạ (W20) | `DK-R17` GPIO47 | LCD SDA (W11) |
| 59 | `DK-L18` GPIO12 | trống | `DK-R18` GPIO21 | LCD SCL (W12) |
| 60 | `DK-L19` GPIO13 | trống | `DK-R19` GPIO20 | **không nối** (USB D+) |
| 61 | `DK-L20` GPIO14 | trống | `DK-R20` GPIO19 | **không nối** (USB D−) |
| 62 | `DK-L21` 5V | 5Vin ← 5V_ESP (W2) | `DK-R21` GND | GND → LCD GND (W13) |
| 63 | `DK-L22` GND | GND (W3); mic GND (M2) | `DK-R22` GND | GND → ray RD− (W6) |
<!-- END:devkit -->

## 2. Nguồn và phân vùng

### 2.1 Ray

| Ray | Hàng + | Hàng − | Dùng cho |
|---|---|---|---|
| `RT` | **5V_MAIN** (sau F1) | **GND** (nối về đầu vào) | Servo, MOSFET, amply, nhánh devkit qua D1. Đầu vào ở cột 3 = điểm sao ★ |
| `RG` | – | – | Chưa dùng (nằm dưới devkit) |
| `RD` | chưa dùng | **GND_ESP** (W6 từ `DK-R22`) | GND của hai nút |

Dán băng dính ghi "5V" lên ray RT+. Không cắm 5V vào ray RD và không cắm gì vào RD+.

### 2.2 Vùng

| Vùng | Vị trí | Gồm |
|---|---|---|
| Vào + điểm sao ★ | Tấm trên cột 1–3; ray RT cột 3–7 | BUSB, F1, W1, C1, C2 |
| Servo | Tấm trên a–e, cột 5–12 | J3, J4, R7, R8, (R9, R10) |
| Đèn mắt (Q1) | Tấm trên a–e, cột 13–18 | Q1, R3, R5, R_EYE1, R_EYE2, BL1, BL2 |
| Output dự phòng (Q2) | Tấm trên a–e, cột 20–25 | Q2, R4, R6, D2, RL, BL3, chỗ cắm J5 |
| Amply | Tấm trên hàng a, cột 36–42; thân đè lên ray RT | U2, C3, C4; loa LS1 ở ngoài |
| Nguồn devkit | Tấm trên a–e cột 62–63, ray RT cột 60–61 | D1, C5, C6, W2–W4 |
| Mic | Ngoài breadboard, 5 dây vào lỗ trống của J1 | U3, M1–M5 |
| Nút | Tấm dưới, vắt qua rãnh giữa, cột 15–23 | SW2, SW3 |
| VSENSE | Tấm dưới f–j, cột 42–45 | R1, R2, C7 |
| LCD | Tấm dưới hàng j, cột 47–54, cắm đứng | U4 |
| Còn trống | Tấm trên f–j cột 1–38; tấm dưới cột 1–14, 24–41 | Chỗ cho dây bay và mở rộng |

Lý do bố trí:

- **Dây nhanh thì ngắn, dây chậm được phép dài.** SPI của LCD chạy 40MHz: LCD cắm ngay dưới hàng chân J3, dây W7–W12 chỉ vắt qua rãnh (≤2cm). I2S của amply (W16–W18) ≤2.5cm. Dây PWM servo/MOSFET (50Hz, 5kHz) và dây nút được phép dài 10–15cm.
- **Không có 5V trên các dải a–e nằm ngay trên chân devkit** (cột 42–61 tấm trên). Cắm nhầm một dây qua rãnh ở đó không đưa 5V vào GPIO. Dải 5V duy nhất cạnh devkit là cột 62, nằm đúng trên chân 5Vin.
- **Dòng servo không đi qua dây của devkit** (mục 2.3).

### 2.3 Nguồn: giống và khác bản PCB

```
Sạc dự phòng ─ BUSB ─► T.e1 ─ F1 ─► T.c3 ─ W1 ─► ★ RT+@3 ═══════ ray RT+ (5V_MAIN) ═══════════════════════► …
                       (GND) ──────────────────► ★ RT−@3 ═══════ ray RT− (GND) ═══════════════════════════► …
  ★ cột 3–7 │ servo J3/J4 + C1, C2 (cột 4–10) │ Q1, Q2 (cột 15–25) │ amply U2 (cột 33–34) │ D1 → devkit (cột 60–61)
             ◄── gần đầu vào nhất                                                    xa nhất ──►
```

| Hạng mục | PCB (HW1, HW4) | Breadboard |
|---|---|---|
| Công tắc SW1 | Có | Không. Rút cáp USB-C ở BUSB = tắt |
| Cầu chì F1 2.5A | Có | Có (`T.c1`–`T.c3`): chập trên breadboard là chuyện thường gặp |
| Điểm sao | Thanh 5V/GND 7 lỗ, mỗi nhánh một dây | Đầu vào ở cột 3 của ray RT. Tải xếp theo dòng: servo sát ★, rồi MOSFET, amply, devkit xa nhất. Dòng khởi động 1.5–2A của servo chỉ chạy trên đoạn ray cột 3–10, không chạy qua đoạn ray và dây mà devkit dùng |
| GND tín hiệu | LCD, mic, nút, VSENSE về chân GND devkit | Giống: LCD (W13), mic (M2), VSENSE (W24) cắm vào chân GND devkit; nút qua ray RD− (W6 từ `DK-R22`). GND tín hiệu chỉ gặp GND công suất ở một chỗ: W3 + W4 (cột 63 → `RT−@60`) |
| D1, C5, C6 | Sát chân 5V devkit | Giống (`T.a62`, `T.c62`, `T.b62`) |
| C1 / C3 | Sát servo / sát VIN amply | Giống (`RT@4` / `T.c36`) |
| Tụ chống dội C8, C9 | Có, gần J9 | **Không cắm**: nút nằm ngay trên board, dây ngắn; firmware đã chống dội. C8/C9 lọc nhiễu bắt vào dây nút dài trong mũ nên chỉ cần trên PCB |
| R_EYE1, R_EYE2 | Trên dây, gần mắt | Trên board (`RT+@17`, `RT+@18`), 150Ω khi thử bằng LED |

Dòng qua mỗi tiếp điểm breadboard nên giữ dưới ~1A. Dây nguồn (đánh dấu "22AWG lõi đặc" trong bảng) cắt ngắn, cắm sâu. Nếu servo làm devkit reset hoặc tiếp điểm nóng, xem mục 6.

## 3. Bảng lắp

### 3.1 Linh kiện

<!-- BEGIN:parts -->
| Bước | Ký hiệu | Linh kiện | Giá trị | Chân → lỗ | Ghi chú |
|---|---|---|---|---|---|
| B1 | BUSB | Mạch USB-C sink | có 5.1kΩ ở CC1 và CC2 | VBUS `T.e1` · GND `RT−@3` | Hàn 2 dây 22AWG lõi đặc: đỏ VBUS → `T.e1`, đen GND → `RT−@3`. Hoặc cáp USB-A cắt (wiring.md §2.7) |
| B1 | F1 | Cầu chì tự phục hồi | PPTC 2.5A hold | `T.c1` – `T.c3` | Mọi dòng 5V đi qua F1; chập trên breadboard thì F1 ngắt |
| B1 | C1 | Tụ hóa | 1000µF/10V | + `RT+@4` · − `RT−@4` | Sát chỗ cắm servo. Bẻ chân cho thân **nằm ngang chìa ra ngoài mép trên**, không che lỗ ray |
| B1 | C2 | Tụ gốm | 100nF | `RT+@7` – `RT−@7` | Lọc nhiễu chổi than servo |
| B2 | D1 | Diode Schottky | 1N5819 | A `RT+@61` · K `T.a62` | Vạch (catot) về phía `T.a62`. Chặn 5V của cáp USB máy tính chảy sang ray (wiring.md §2.3) |
| B2 | C5 | Tụ hóa | 220µF/10V | + `T.c62` · − `T.c63` | Thân nằm ngang về phía cột 59–61 (đang trống), không che `T.e62`, `T.e63` |
| B2 | C6 | Tụ gốm | 100nF | `T.b62` – `T.b63` |  |
| B3 | U4 | LCD ST7789 1.54" 240×240 | 8 chân | BLK `D.j47` · CS `D.j48` · DC `D.j49` · RES `D.j50` · SDA `D.j51` · SCL `D.j52` · VCC `D.j53` · GND `D.j54` | Cắm **đứng** ở hàng j, mặt màn quay ra ngoài, chân GND ở cột 54. Màn 7 chân: xem mục 3.3 |
| B4 | U2 | Amply I2S MAX98357A |  | VIN `T.a36` · GND `T.a37` · SD `T.a38` · GAIN `T.a39` · DIN `T.a40` · BCLK `T.a41` · LRC `T.a42` | Chân ở hàng a, thân nằm đè lên ray RT, cọc loa quay ra mép trên. SD, GAIN để trống (mono, 9 dB) |
| B4 | C3 | Tụ hóa | 470µF/10V | + `T.c36` · − `T.c37` | Thân nằm ngang về phía cột 38–40 (che hàng b–d, chừa hàng e cho dây I2S) |
| B4 | C4 | Tụ gốm | 100nF | `T.e36` – `T.e37` |  |
| B4 | U3 | Mic I2S INMP441 |  | VDD `T.g43` · GND `T.g63` · WS `T.g45` · SCK `T.g46` · SD `T.g47` | Ngoài breadboard, 5 dây Dupont cái–đực ≤20cm bện xoắn (M1–M5). **L/R nối GND ngay trên module mic** |
| B5 | SW2 | Nút nhấn 6×6 – PTT |  | e21 `D.e21` · e23 `D.e23` · f21 `D.f21` · f23 `D.f23` | GPIO10 vào nửa trên cột 23 (W19), GND vào nửa dưới cột 21 (W21) |
| B5 | SW3 | Nút nhấn 6×6 – mặt nạ |  | e15 `D.e15` · e17 `D.e17` · f15 `D.f15` · f17 `D.f17` | GPIO11 vào nửa trên cột 17 (W20), GND vào nửa dưới cột 15 (W22) |
| B6 | R1 | Điện trở | 100kΩ 1% | `D.h45` – `D.h43` | Cột 43 nhận VSENSE_TOP (W25) |
| B6 | R2 | Điện trở | 100kΩ 1% | `D.g45` – `D.g42` | Cột 42 = GND của devkit (W24) |
| B6 | C7 | Tụ gốm | 100nF | `D.i45` – `D.i42` |  |
| B7 | Q1 | AO3400A trên đế SOT-23 → 3 chân |  | G `T.c14` · S `T.c15` · D `T.c16` | Đế cắm đứng. **Kiểm tra đế đưa G/S/D ra chân nào** (thang diode: S→D ≈0.5–0.7V) |
| B7 | R3 | Điện trở gate | 100Ω | `T.d13` – `T.d14` | Lắp đứng |
| B7 | R5 | Điện trở kéo xuống | 100kΩ | `T.b14` – `T.b15` | Gate → source: đèn tắt khi reset, nạp code |
| B7 | R_EYE1 | Hạn dòng mắt trái | 150Ω (với LED thử) | `RT+@17` – `T.a17` | Khi cắm module mắt thật: thay bằng giá trị đo theo wiring.md §4.2 |
| B7 | R_EYE2 | Hạn dòng mắt phải | 150Ω (với LED thử) | `RT+@18` – `T.a18` | Như trên |
| B7 | BL1 | LED thử thay mắt trái | LED 3mm | A `T.d17` · K `T.d16` | Chân dài (A) ở `T.d17`. Module mắt thật E1: rút BL1, cắm dây + vào `T.d17`, − vào `T.d16` |
| B7 | BL2 | LED thử thay mắt phải | LED 3mm | A `T.b18` · K `T.b16` | Chân dài (A) ở `T.b18`. Module E2: rút BL2, dây + vào `T.b18`, − vào `T.b16` |
| B7 | Q2 | AO3400A trên đế SOT-23 → 3 chân |  | G `T.c21` · S `T.c22` · D `T.c23` | Như Q1 |
| B7 | R4 | Điện trở gate | 100Ω | `T.d20` – `T.d21` | Lắp đứng |
| B7 | R6 | Điện trở kéo xuống | 100kΩ | `T.b21` – `T.b22` |  |
| B7 | D2 | Diode chống ngược | 1N5819 | A `T.a23` · K `RT+@24` | Vạch (catot) về phía ray `RT+@24`. Lắp ngược = chập 5V khi Q2 dẫn |
| B7 | RL | Điện trở cho LED thử | 220Ω | `RT+@25` – `T.a24` |  |
| B7 | BL3 | LED thử output dự phòng | LED 3mm | A `T.d24` · K `T.d23` | Chân dài (A) ở `T.d24` |
| B7 | J5 | Tải ngoài (tùy chọn) | ≤1A | + `RT+@21` · − `T.e23` | Ví dụ trở 10Ω/5W khi thử nhiệt Q2 (wiring.md §4.5), dây 22AWG ngắn |
| B8 | J3 | Header đực 3P → servo S0 (M1, trái) |  | GND `T.c5` · +5V `T.c6` · SIG `T.c7` | Dây nâu ở `T.c5`, đỏ `T.c6`, cam `T.c7` |
| B8 | J4 | Header đực 3P → servo S1 (M2, phải) |  | GND `T.c9` · +5V `T.c10` · SIG `T.c11` | Dây nâu ở `T.c9`, đỏ `T.c10`, cam `T.c11` |
| B8 | R7 | Điện trở tín hiệu servo | 220Ω | `T.b7` – `T.b8` | Lắp đứng |
| B8 | R8 | Điện trở tín hiệu servo | 220Ω | `T.b11` – `T.b12` | Lắp đứng |
| B8 | R9 | Kéo xuống (tùy chọn) | 10kΩ | `T.d7` – `T.d5` | Chỉ lắp nếu servo giật lúc khởi động |
| B8 | R10 | Kéo xuống (tùy chọn) | 10kΩ | `T.d11` – `T.d9` | Như trên |
<!-- END:parts -->

### 3.2 Dây

<!-- BEGIN:wires -->
| Bước | Dây | Từ | Tới | Mạng | Loại | Ghi chú |
|---|---|---|---|---|---|---|
| B1 | W1 | `T.a3` | `RT+@3` | `5V_MAIN` | 22AWG lõi đặc | Ra F1 → ray 5V. ★ = nhóm lỗ ray cột 3–7 |
| B2 | W2 | `T.e62` | `T.f62` | `5V_ESP` | 22AWG lõi đặc | Tới chân 5Vin (`DK-L21`) |
| B2 | W3 | `T.e63` | `T.f63` | `GND_ESP` | 22AWG lõi đặc | Tới chân GND (`DK-L22`) |
| B2 | W4 | `T.a63` | `RT−@60` | `GND_ESP` | 22AWG lõi đặc | GND của devkit về ray GND |
| B3 | W5 | `T.f42` | `D.f53` | `3V3` | dây cắm | 3V3 (`DK-L1`) tới VCC của LCD; đi vòng bên phải đầu anten |
| B3 | W7 | `D.e47` | `D.f47` | `LCD_BLK` | dây cắm |  |
| B3 | W8 | `D.e48` | `D.f48` | `LCD_CS` | dây cắm | Màn 7 chân: bỏ dây này |
| B3 | W9 | `D.e49` | `D.f49` | `LCD_DC` | dây cắm |  |
| B3 | W10 | `D.e50` | `D.f50` | `LCD_RES` | dây cắm |  |
| B3 | W11 | `D.e58` | `D.f51` | `LCD_MOSI` | dây cắm |  |
| B3 | W12 | `D.e59` | `D.f52` | `LCD_SCLK` | dây cắm |  |
| B3 | W13 | `D.e62` | `D.f54` | `GND_ESP` | dây cắm | GND (`DK-R21`) tới GND của LCD |
| B4 | W14 | `T.b36` | `RT+@33` | `5V_AUDIO` | 22AWG lõi đặc |  |
| B4 | W15 | `T.b37` | `RT−@34` | `GND_AUDIO` | 22AWG lõi đặc |  |
| B4 | W16 | `T.e40` | `T.f48` | `I2S_SPK_DIN` | dây cắm |  |
| B4 | W17 | `T.e41` | `T.f49` | `I2S_SPK_BCLK` | dây cắm |  |
| B4 | W18 | `T.e42` | `T.f50` | `I2S_SPK_LRC` | dây cắm |  |
| B4 | M1 | U3 VDD | `T.g43` | `3V3` | Dupont cái–đực |  |
| B4 | M2 | U3 GND | `T.g63` | `GND_ESP` | Dupont cái–đực |  |
| B4 | M3 | U3 WS | `T.g45` | `I2S_MIC_WS` | Dupont cái–đực |  |
| B4 | M4 | U3 SCK | `T.g46` | `I2S_MIC_SCK` | Dupont cái–đực |  |
| B4 | M5 | U3 SD | `T.g47` | `I2S_MIC_SD` | Dupont cái–đực |  |
| B5 | W6 | `D.e63` | `RD−@61` | `GND_ESP` | dây cắm | GND (`DK-R22`) xuống ray RD− |
| B5 | W19 | `T.g57` | `D.c23` | `BTN_PTT` | dây cắm | Dài ~15cm, đi vòng bên phải devkit sang tấm dưới |
| B5 | W20 | `T.g58` | `D.c17` | `BTN_MASK` | dây cắm | Như W19 |
| B5 | W21 | `D.j21` | `RD−@21` | `GND_ESP` | dây cắm |  |
| B5 | W22 | `D.j15` | `RD−@15` | `GND_ESP` | dây cắm |  |
| B6 | W23 | `D.e45` | `D.f45` | `VSENSE` | dây cắm |  |
| B6 | W24 | `D.e42` | `D.f42` | `GND_ESP` | dây cắm |  |
| B6 | W25 | `RT+@31` | `D.j43` | `VSENSE_TOP` | dây cắm | Dây đo 5V_MAIN, đi vòng bên phải devkit |
| B7 | W26 | `T.g53` | `T.e13` | `EYES_PWM` | dây cắm |  |
| B7 | W27 | `T.a15` | `RT−@15` | `GND_PWR` | 22AWG lõi đặc |  |
| B7 | W28 | `T.g56` | `T.e20` | `OUT_PWM` | dây cắm |  |
| B7 | W29 | `T.a22` | `RT−@22` | `GND_PWR` | 22AWG lõi đặc |  |
| B8 | W30 | `T.a5` | `RT−@5` | `GND_SERVO` | 22AWG lõi đặc |  |
| B8 | W31 | `T.a6` | `RT+@6` | `5V_SERVO` | 22AWG lõi đặc |  |
| B8 | W32 | `T.a9` | `RT−@9` | `GND_SERVO` | 22AWG lõi đặc |  |
| B8 | W33 | `T.a10` | `RT+@10` | `5V_SERVO` | 22AWG lõi đặc |  |
| B8 | W34 | `T.g51` | `T.e8` | `SERVO0_PWM` | dây cắm |  |
| B8 | W35 | `T.g52` | `T.e12` | `SERVO1_PWM` | dây cắm |  |
<!-- END:wires -->

### 3.3 Biến thể và thay thế

| Trường hợp | Làm thế nào |
|---|---|
| LCD 7 chân (GND, VCC, SCL, SDA, RES, DC, BLK) | Cắm từ `D.j54` (GND) tới `D.j48` (BLK). **Bỏ W8**; W7 đổi thành `D.e47` → `D.f48`. Build `jarvis-helmet-7pin` |
| Ảnh LCD nhiễu, sai màu | Thêm R11 33Ω: bỏ W12, cắm dây `D.e59` → `D.f56` và R11 giữa `D.g56` – `D.g52`. Vẫn nhiễu: rút ngắn W5, W13 |
| Module mắt thật E1, E2 | Đo và chọn R_EYE theo wiring.md §4.2, thay R_EYE1/R_EYE2. Rút BL1 (BL2), cắm dây + của module vào lỗ chân A, dây − vào lỗ chân K của LED vừa rút |
| Tải thật / tải giả ở output dự phòng (J5) | Dây + vào `RT+@21`, dây − vào `T.e23`, 22AWG ngắn. BL3 để nguyên cũng được |
| Nút thật gắn vỏ (có dây) | PTT: một dây vào `D.a23`, dây kia vào `D.g21`. Mặt nạ: `D.a17` và `D.g15`. Cắm song song với nút 6×6 được |
| Đổi tiếng to/nhỏ (GAIN) | Mặc định để trống = 9 dB. 6 dB (rè, quá to): dây `T.e39` → `T.d36` (VIN). 12 dB (nhỏ): `T.e39` → `T.d37` (GND) |

## 4. Vật tư thêm cho breadboard

Ngoài BOM chính (đã có devkit, module, R, C, diode, MOSFET, header):

| Vật tư | SL | Dùng cho |
|---|---|---|
| Mạch USB-C sink có sẵn 5.1kΩ ở CC1 và CC2 (hoặc cáp USB-A cắt) | 1 | BUSB |
| Dây đồng 22AWG lõi đặc đỏ + đen | ~1m | Dây ghi "22AWG lõi đặc" ở bảng 3.2, 2 dây ra của BUSB |
| Bộ dây cắm breadboard đực–đực, có loại ≥15cm | 1 bộ | Các dây còn lại; W5, W19, W20, W25 dài 10–15cm |
| Dây Dupont cái–đực 20cm | 5 | Mic M1–M5 |
| Nút nhấn 6×6mm 4 chân | 2 | SW2, SW3 |
| LED đỏ 3mm | 3 | BL1–BL3. Để chân dài cho thân LED cao hơn đế MOSFET |
| Điện trở 150Ω | 2 | R_EYE1, R_EYE2 khi thử bằng LED |
| Điện trở 220Ω | 1 | RL (ngoài R7, R8) |
| Điện trở 1kΩ | 1 | Tải thử ở bước B0.5 |

## 5. Thứ tự lắp và thử

Mỗi bước: lắp phần của bước đó (cột "Bước" ở bảng 3.1, 3.2), làm phép thử, **đạt mới sang bước sau**. Ghi số đo vào wiring.md §6 và bảng P1/P3 của [test-report.md](../test-report.md). Trước mỗi lần cấp điện: không có dây nào ở lỗ ×; đo Ω giữa RT+ và RT−, giữa 3V3 (`T.g42`) và GND (`T.g63`) theo cách ở mục 5.0.

Lệnh MCP ở các bước B6–B9 chạy từ laptop cùng mạng, IP của mũ xem trên router hoặc trong log (`LocalMcp: MCP WebSocket server ...`):

```bash
pip install websockets
python tools/servo_calibrate.py --host 192.168.1.50 --call self.system.get_status
```

### 5.0 Đo chập nguồn – GND cho đúng

Mạch có tụ lọc (C1 1000µF trên RT, tụ của devkit, LCD và mic trên 3V3), nên lúc vừa chạm que đo thì tụ đang nạp và nhìn như chập:

- **Rút hết nguồn** (sạc và cáp USB) trước khi đo. Đo Ω trên mạch đang có điện vừa sai vừa có thể hỏng đồng hồ.
- Dùng **thang Ω**, que đỏ vào nguồn (RT+ hoặc 3V3), que đen vào GND, giữ yên vài giây.
  - Số đo **tăng dần** rồi dừng ở mức cao (hàng trăm Ω trở lên, tùy mạch): **bình thường**, là tụ đang nạp.
  - Số đo **đứng yên dưới ~10Ω**: **chập**, không được cấp điện. Tách dần theo mục 6.
- Thang thông mạch (còi) dễ gây nhầm: **kêu ngắn rồi im là bình thường**; kêu liên tục mới là chập.
- So với số tham chiếu đo ở B0 #6: lần nào thấp hơn hẳn số đó là có chỗ cắm sai.

### B0 – Chuẩn bị (chưa có dây nào, chưa có nguồn ngoài)

| # | Làm | Đạt khi |
|---|---|---|
| 1 | Đối chiếu nhãn in trên devkit với bảng mục 1 | Khớp. Ghi phiên bản devkit, chân LED RGB (wiring.md §6.1) |
| 2 | Thông mạch từng ray đầu–cuối: `RT−@3` ↔ `RT−@61`, `RT+@3` ↔ `RT+@61`, `RD−@3` ↔ `RD−@61` | ≈0Ω. Breadboard dài đôi khi chia đôi ray ở giữa (vạch màu đứt quãng): khi đó nối hai nửa bằng dây ngắn |
| 3 | Build và nạp `jarvis-helmet` (LCD 7 chân: `jarvis-helmet-7pin`) như README board §2–3, qua cổng USB-C hiện ra là cổng COM (chip USB–UART) của clone | Log có `SKU=jarvis-helmet`. LED RGB nháy xanh dương (chưa có Wi-Fi) |
| 4 | Dán nhãn "5V" lên ray RT+ | |
| 5 | **Chân 5Vin có diode không?** Chỉ cắm USB máy tính. Cắm tạm điện trở 1kΩ giữa `T.f62` (5Vin) và `T.f63` (GND), đo áp `T.f62` so với `T.f63`, rồi rút điện trở | ≈0V: 5Vin chỉ là đầu vào (có diode trên board). ≈4.6–5V: 5Vin nối thẳng VBUS → D1 bắt buộc, và khi máy tính tắt mà còn cắm cáp thì nguồn ngoài cấp ngược ra cổng USB: rút cáp khi không nạp code. Ghi vào wiring.md §6.1 ("Devkit có diode trên VBUS USB?") |
| 6 | Số tham chiếu: chỉ có devkit, rút mọi nguồn, đo Ω `T.g42` (3V3) ↔ `T.g63` (GND) theo mục 5.0, đợi số đứng yên | Không đứng yên ở ≈0Ω. Ghi lại số đo để so ở các bước sau |

### B1 – Nguồn vào

Lắp BUSB, F1, W1, C1, C2. **Chưa cắm W2, W3** (devkit chưa nhận nguồn ngoài).

| # | Làm | Đạt khi |
|---|---|---|
| 1 | Chưa cấp: Ω `RT+@3` ↔ `RT−@3` | >1kΩ (tăng dần do C1 nạp) |
| 2 | Cắm sạc dự phòng: đo `T.e1`, `RT+@3`, `RT+@61` (que đen ở `RT−@3`) | Dương (đúng cực), **4.9–5.25V**, hai đầu ray chênh <50mV (wiring.md §2.8 bước 2–3, ghi §6.2) |

### B2 – Nguồn devkit

Lắp D1, C5, C6, W2, W3, W4.

| # | Làm | Đạt khi |
|---|---|---|
| 1 | Thử D1 rời bằng thang diode trước khi cắm; cắm vạch về `T.a62` | Thuận 0.2–0.45V, ngược hở |
| 2 | Cấp sạc, **không** cắm USB máy tính | Devkit khởi động. `T.d62` (5V_ESP) **≥4.5V**; `T.g42` (3V3) 3.25–3.35V |
| 3 | Rút sạc, cắm USB máy tính (dòng ngược, wiring.md §2.8 bước 5a) | `RT+@3` < 0.3V (D1 chặn) |
| 4 | Cắm cả sạc và USB máy tính | ESP chạy bình thường, D1 không nóng |

### B3 – LCD (nghiệm thu HW2 #1)

Lắp W5, U4, W7–W13. Bước này chạy được chỉ với USB máy tính.

| # | Làm | Đạt khi |
|---|---|---|
| 1 | Trước khi cấp, theo mục 5.0: Ω `D.f53` (VCC) ↔ `D.f54` (GND); thông mạch `D.f53` ↔ `RT+@3` | 3V3–GND không thấp hơn hẳn số tham chiếu B0 #6; VCC không thông RT+ (LCD không bị cấp 5V) |
| 2 | Cấp nguồn, quan sát | HUD hiện đúng màu, đúng hướng, không lệch ảnh; dòng `jarvis-helmet/<phiên bản>` trên màn. Sai: xem mục 3.3 và mục 6 |

### B4 – Âm thanh (P1: nói chuyện với server)

Lắp U2 (SD, GAIN để trống), C3, C4, W14–W18, loa LS1; mic U3 với M1–M5 (L/R nối GND trên module). **Cần nguồn ngoài**: amply lấy 5V từ ray RT.

| # | Làm | Đạt khi |
|---|---|---|
| 1 | Bấm BOOT lúc khởi động → kết nối Wi-Fi `Xiaozhi-XXXX` → http://192.168.4.1 thêm Wi-Fi (README board §5). P1 dùng server mặc định, P2 trở đi đặt OTA URL server nhà (README §4) | Log báo đã kết nối Wi-Fi, HUD không báo OFFLINE |
| 2 | Mic cách loa ≥30cm, phòng yên tĩnh, nói "Jarvis" cách mic 30cm, 20 lần | Đánh thức được (HW2 #2) |
| 3 | Hỏi một câu, nghe trả lời ở âm lượng 70% | Rõ, không rè (HW2 #3). Rè: đổi GAIN (mục 3.3) |
| 4 | `T.g42` (3V3) khi đang trả lời (Wi-Fi phát liên tục) | ≥3.20V. Thấp hơn: mục 6 |

### B5 – Nút (FW5)

Lắp W6 (ray RD− = GND_ESP), SW2, SW3, W19–W22.

| # | Làm | Đạt khi |
|---|---|---|
| 1 | Trước khi cấp, thang thông mạch `D.c23` ↔ `D.j21` và `D.c17` ↔ `D.j15` | Thả: hở. Bấm: thông |
| 2 | PTT: bấm nhanh, nhấn giữ ≥400ms | Bắt đầu/kết thúc hội thoại; giữ để nói, thả thì gửi câu (README board §6) |
| 3 | Mặt nạ: bấm 1 lần, giữ ≥800ms, bấm đúp (chưa có servo thì xem log/HUD) | Chạy `open_mask`/`close_mask`; bật/tắt đèn mắt; dừng khẩn cấp |

### B6 – Đo 5V (VSENSE)

Lắp R1, R2, C7, W23–W25.

| # | Làm | Đạt khi |
|---|---|---|
| 1 | Chưa cắm W25, chưa cấp: Ω `D.f45` ↔ `D.f42` | ≈100kΩ (R2; số đo trôi lúc đầu do C7 nạp) |
| 2 | Cấp nguồn: `D.j45` so với GND; `--call self.system.get_status` | `D.j45` ≈ 5V_MAIN ÷ 2; `vbus_mv` lệch ≤ ±50mV so với đồng hồ tại `RT+@3` sau khi bù (wiring.md §2.8 bước 6, ghi §6.3) |

### B7 – MOSFET: đèn mắt và output dự phòng (HW3 §6 #1–3)

Lắp Q1, R3, R5, R_EYE1, R_EYE2, BL1, BL2, W26, W27; Q2, R4, R6, D2, RL, BL3, W28, W29.

| # | Làm | Đạt khi |
|---|---|---|
| 1 | Trước khi cắm Q1/Q2: thang diode trên đế, S → D | 0.5–0.7V; chiều ngược hở. Xác định đúng chân G/S/D của đế |
| 2 | Chưa cắm W26, W28, chưa cấp: Ω `T.c14` ↔ `T.c15` và `T.c21` ↔ `T.c22`. Thử D2 rời bằng thang diode trước khi cắm | ≈100kΩ (R5, R6); D2 thuận 0.2–0.45V, ngược hở |
| 3 | Bật nguồn, nhấn RST, nạp lại firmware, vừa làm vừa nhìn BL1–BL3 | Tắt hoàn toàn suốt quá trình (HW3 #1) |
| 4 | `--call self.eyes.set '{"on": true, "brightness": 10}'`, rồi 20, 30 … 100 | BL1, BL2 sáng dần đều, không nháy khi quay điện thoại chế độ chậm (HW3 #2) |
| 5 | `--call self.output.set '{"on": true}'`, rồi `'{"on": false}'` | BL3 sáng / tắt |
| 6 | (Tùy chọn) tải giả 10Ω/5W ở J5 (mục 3.3), bật 10 phút | Q2 <50°C ở phút 0/5/10 (HW3 #3, ghi §6.5); tiếp điểm breadboard chỉ ấm |

### B8 – Servo (HW3 §6 #4)

Lắp J3, J4, R7, R8, W30–W35; R9, R10 chỉ khi cần. Servo chưa gắn vào mặt nạ.

| # | Làm | Đạt khi |
|---|---|---|
| 1 | Trước khi cấp: `T.c5` thông RT−, `T.c6` thông RT+; Ω `T.c7` ↔ `T.e8` và `T.c11` ↔ `T.e12` | Thông; thông; ≈220Ω |
| 2 | Cắm servo (nâu ở cột 5 / 9), khởi động lại devkit 5 lần | Servo không giật lúc khởi động; giật thì lắp R9, R10 |
| 3 | `python tools/servo_calibrate.py --host <IP>` → `cal 0 90`, `+5`, `-5`; `cal 1 90`; `open`, `close`, `state` | Cả hai servo chạy êm, S1 bắt đầu sau S0 ~30ms; HUD không cảnh báo 5V thấp |

### B9 – Thử nặng nhất và dòng ngược (HW1 §6)

| # | Làm | Đạt khi |
|---|---|---|
| 1 | 2 servo mở/đóng liên tục (vòng lặp bên dưới) + loa âm lượng tối đa (`self.audio_speaker.set_volume` 100 hoặc nói "tăng âm lượng tối đa") + đang trả lời (Wi-Fi phát), **10 phút** | Không lần reset nào (`esp_reset_reason` trong log); `vbus_mv` thấp nhất **≥4600** (wiring.md §2.8 bước 4, ghi §6.6 dòng "trên breadboard") |
| 2 | Sau 10 phút sờ F1, W1, W30–W33, BUSB | Ấm được; nóng tay thì xem mục 6 |
| 3 | Rút servo, tắt sạc, cắm USB máy tính; rồi cắm cả hai; rồi tắt sạc mà vẫn cắm USB (wiring.md §2.8 bước 5) | Như B2 #3–4; ghi số đo trường hợp (c) vào §6.7 |

Servo chưa mang mặt nạ nên dòng nhỏ hơn thực tế. Muốn mô phỏng tải: gắn cần servo và lấy tay cản nhẹ, mỗi lần ≤2 giây. Bài này phải làm lại trên PCB khi đã lắp cơ khí.

```bash
# Git Bash; đổi IP cho đúng mũ
for i in $(seq 1 150); do
  python tools/servo_calibrate.py --host 192.168.1.50 --call self.mask.open;  sleep 2
  python tools/servo_calibrate.py --host 192.168.1.50 --call self.mask.close; sleep 2
done
```

## 6. Sự cố thường gặp

| Hiện tượng | Nguyên nhân hay gặp | Xử lý |
|---|---|---|
| Devkit reset khi servo khởi động | Sụt áp ở tiếp điểm breadboard hoặc cáp sạc mảnh | Dây nguồn 22AWG ngắn, cắm sâu; C1 đúng chỗ; thử sạc khác ≥2.4A. Vẫn reset: hàn riêng một cặp dây 22AWG từ BUSB (sau F1) tới J3/J4, bỏ qua ray (đúng cách đi dây của PCB) |
| 3V3 < 3.2V khi Wi-Fi phát | B0.5 ra ≈0V: devkit có diode sau chân 5Vin, cộng D1 thành hai lần sụt áp, LDO thiếu áp | Thay D1 bằng một dây (diode trên devkit đã chặn dòng từ USB máy tính ra ray). Ghi lại để quyết định D1 cho PCB (wiring.md §2.3) |
| LCD trắng/đen | Sai loại 7/8 chân, RES/DC đảo, VCC chưa nối | Kiểm tra W5, W7–W13 theo tên chân in trên LCD |
| LCD sai màu, sọc nhiễu | SPI 40MHz qua dây cắm | R11 (mục 3.3), dây W11, W12 ngắn nhất có thể |
| Không đánh thức được | L/R của mic chưa nối GND; WS và SCK đảo; mic sát loa | Kiểm tra M1–M5, đặt mic xa loa |
| Loa rè | GAIN quá cao; 5V sụt khi to | Đổi GAIN (mục 3.3), đo RT+ khi phát |
| Đèn mắt lóe khi khởi động | Thiếu R5/R6 hoặc đế MOSFET sai chân | B7 #1–2 |
| F1 ngắt (nóng, mất 5V) | Chập 5V–GND, D2 cắm ngược, servo kẹt | Tắt nguồn, đo Ω RT+ ↔ RT−, kiểm tra D2 |
| 3V3 hoặc 5V đo "thông" GND | Thường là tụ đang nạp (mục 5.0). Chập thật: dây cắm lệch một cột, LCD/mic cắm ngược VCC–GND, chân đế MOSFET sai, D2 ngược | Rút nguồn, đo bằng thang Ω. Vẫn ≈0Ω: rút dần theo thứ tự ngược B8 → B3 (với 3V3: W5, rồi M1) và đo lại sau mỗi lần rút; chỗ vừa rút làm số đo vọt lên là chỗ chập. Rút hết mà vẫn chập: rút devkit ra khỏi breadboard, đo trên devkit trần |
| Nút luôn "đang bấm" | Có dây ở dải `D.a–e21`, `D.f–j23` (hoặc 15/17) | Hai dải chân còn lại của mỗi nút phải để trống |

## 7. Lên PCB (HW4) – khác gì

- Bố trí PCB vẫn theo wiring.md §5; breadboard chỉ để kiểm chứng mạch.
- Thêm SW1, C8, C9; điểm sao thành thanh 7 lỗ; R_EYE chuyển lên dây gần mắt, J6 2 chân.
- Bỏ BUSB (thay bằng J1 domino), LED thử BL1–BL3, RL.
- Số đo ở B0.5 (diode chân 5Vin), B2 (5V_ESP), B4 #4 (3V3 khi Wi-Fi phát), B6 (offset VSENSE) quyết định giữ hay bỏ D1 và offset firmware: ghi vào wiring.md §6 trước khi hàn.
