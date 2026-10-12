# Đấu dây phần cứng – Jarvis Helmet

| | |
|---|---|
| **Phiên bản** | Rev C – 2026-10-12 (chưa đo trên phần cứng thật). Thay đổi so với các bản trước: bảng "Lịch sử thay đổi" ngay dưới |
| **Nguồn gốc** | [HW1](../modules/HW1-nguon-dien.md), [HW2](../modules/HW2-dau-noi-tin-hieu.md), [HW3](../modules/HW3-mach-cong-suat.md), [HW4](../modules/HW4-lap-rap-pcb.md). Bảng chân lấy nguyên từ HW2; nếu hai nơi lệch nhau thì **HW2 đúng** và sửa file này |
| **Hình kèm** | [wiring.svg](wiring.svg) (sơ đồ hệ thống), [perfboard-7x9.svg](perfboard-7x9.svg) (bố trí PCB), [breadboard.svg](breadboard.svg) (bản thử breadboard, chi tiết ở [breadboard.md](breadboard.md)), [index.html](index.html) (xem offline) |
| **BOM** | [BOM.md](BOM.md) |

| Rev | Ngày | Thay đổi |
|---|---|---|
| C | 2026-10-12 | Đèn mắt: module mua về là một hộp pin CR2032 nuôi 2 LED song song, nên còn **một** module E1 và **một** R_EYE (bỏ E2, R_EYE1/R_EYE2 và chỗ chia nhánh chữ Y, mục 4.2). PCB, J6 và firmware không đổi |
| B | 2026-10-09 | (1) U4 đổi sang LCD 2.4" MSP2401; J7 xếp đúng chân 1–8 của module (cáp 1:1), cụm GND sang cột U, R11 sang `Z:2`–`Z:3`. (2) Thêm Q3 AO3401A chống cắm ngược sau F1 (mục 2.9). (3) Chân SD của MAX98357A ← GPIO12 qua R13 2.2kΩ, firmware tắt amply khi không phát; GAIN nối VIN (6 dB) thay vì để trống (mục 3.4). (4) PWM đèn mắt / output dự phòng 5 kHz → 25 kHz. (5) D1 giữ hay nối tắt tùy devkit, C5 220 → 470µF, số liệu LDO theo sơ đồ thật (mục 2.3). Đối chiếu với dự án xiaozhi và datasheet ở mục 7 |
| A | 2026-10-06 | Bản đầu |

Các ô ghi **"Đo thực tế: ___"** phải điền sau khi đo trên linh kiện thật. Tọa độ ghi **"dự kiến"** phụ thuộc kích thước devkit/module, phải kiểm lại trước khi hàn.

## 0. Quy ước

| Ký hiệu | Ý nghĩa |
|---|---|
| `DK-L4`, `DK-R17` | Chân devkit ESP32-S3-DevKitC-1: hàng chân **trái** (Espressif gọi là J1) / **phải** (J3), đếm từ đầu có anten (chân 1) xuống đầu có cổng USB (chân 22). Đổi tên để khỏi nhầm với domino J1 và jack servo J3 |
| `I:23` | Lỗ trên PCB đục lỗ: **cột I, hàng 23** (xem mục 5.1). Dấu `:` để khỏi nhầm với tên linh kiện (`J6` là jack đèn mắt, `J:6` là lỗ cột J hàng 6) |
| `J7.3` | Chân số 3 của đầu nối J7 |
| `5V_SERVO`… | Tên mạng điện (net), xem bảng mục 1.3 |
| U1…U4, LS1, M1/M2, E1, SW1–SW3 | Module/thiết bị ngoài: U1 devkit, U2 MAX98357A, U3 INMP441, U4 LCD 2.4" ST7789 (module MSP2401), LS1 loa, M1/M2 servo S0/S1, E1 module đèn mắt (2 LED song song cho 2 mắt), SW1 công tắc nguồn, SW2 nút PTT, SW3 nút mặt nạ |
| C8, C9, R9–R11, R13, Q3, R_EYE | Ký hiệu đặt thêm trong tài liệu này: tụ chống dội nút, điện trở kéo xuống servo (tùy chọn), điện trở 33Ω cho SCK của LCD (tùy chọn), điện trở 2.2kΩ ở chân SD amply, MOSFET chống cắm ngược, điện trở hạn dòng đèn mắt |

## 1. Tổng quan

### 1.1 Thành phần

| Khối | Linh kiện chính | Đặt ở |
|---|---|---|
| Điều khiển | U1 ESP32-S3-DevKitC-1 N16R8 | Giữa PCB, cắm qua hàng rào cái |
| Âm thanh | U3 INMP441 (mic I2S), U2 MAX98357A (amply I2S) + LS1 loa 3W 8Ω | U2 trên PCB (vùng trái); mic và loa trong mũ, nối qua J8/J10 |
| Hiển thị | U4 LCD 2.4" ST7789 320×240 (SPI, module MSP2401, 77×43mm) | Trong mũ, nối qua J7 |
| Chấp hành | M1/M2 servo MG90S, E1 module LED mắt (CR2032 → 5V, 2 LED song song), output dự phòng J5 | Mạch công suất ở vùng phải PCB |
| Nhập liệu | SW2 nút PTT, SW3 nút mặt nạ, nút BOOT có sẵn trên devkit | Nút trong mũ, nối qua J9 |
| Nguồn | Sạc dự phòng 5V ≥2.4A → J1 → F1 → SW1 → điểm sao 5V_MAIN | Khối nguồn ở cạnh dưới PCB |

### 1.2 Sơ đồ khối

```
Sạc dự phòng 5V ≥2.4A ─(USB-A cắt / USB-C sink 5.1k)─► J1 ─► F1 2.5A ─► Q3 (chống ngược) ─► J2 ═ SW1 ═ J2 ─► ★ ĐIỂM SAO (5V_MAIN + GND_STAR)

★ ─┬─ 5V_SERVO ─── C1 + C2 ── J3, J4 ── servo M1 (S0), M2 (S1) ◄── PWM GPIO17, GPIO18 (qua R7, R8 220Ω)
   ├─ 5V_AUDIO ─── C3 + C4 ── U2 MAX98357A ── LS1 loa 3W 8Ω ◄── I2S GPIO7 (DIN), GPIO15 (BCLK), GPIO16 (LRC); SD ◄── GPIO12 (R13)
   ├─ 5V_ESP ───── D1* ─ C5 + C6 ── U1 chân 5V ── LDO devkit ── 3V3 ─┬─► U3 INMP441 ── I2S GPIO4 (WS), GPIO5 (SCK), GPIO6 (SD)
   │                                                                └─► U4 LCD ST7789 ◄── SPI GPIO47, 21, 40, 41, 39, 42
   ├─ VSENSE_TOP ─ R1/R2 (chia 2) + C7 ──► GPIO1 (ADC1_CH0)
   ├─ 5V_EYES ──── R_EYE ── E1 (2 LED song song) ── Q1 AO3400A ◄── PWM GPIO8 (R3 100Ω, R5 100k kéo xuống)
   ├─ 5V_MAIN ──── J5 (+ D2) ── tải ≤1A ── Q2 AO3400A ◄── PWM GPIO9 (R4 100Ω, R6 100k kéo xuống)
   └─ GND: mỗi nhánh một dây riêng về GND_STAR (GND_SERVO, GND_AUDIO, GND_ESP, GND_PWR)

Nút: SW2 PTT → GPIO10, SW3 mặt nạ → GPIO11 (bấm = nối GND, C8/C9 100nF). Có sẵn trên devkit: BOOT GPIO0, LED RGB GPIO48.
```

### 1.3 Bảng mạng điện (net list)

Tọa độ lỗ theo bố trí mục 5 (cột R của devkit là **dự kiến**, đổi sang S nếu đo được 10 bước).

| Mạng | Loại | Các điểm nối |
|---|---|---|
| `5V_IN` | Nguồn vào | J1.1 (+, `B:34`) ↔ F1 chân vào (`B:30`) |
| `5V_FUSED` | Nguồn sau cầu chì | F1 chân ra (`D:30`) ↔ Q3 D (`F:32`) |
| `5V_PROT` | Sau Q3 (chống ngược) | Q3 S (`E:32`) ↔ J2.1 (`F:34`) ↔ SW1 chân vào |
| `5V_MAIN` | Nguồn chính (sau SW1) | SW1 chân ra ↔ J2.2 (`H:34`) ↔ thanh 5V điểm sao `K:31`–`Q:31` ↔ D1 anot (`D:27`, dây từ `M:31`) ↔ J5.1 (+, `AA:15`) ↔ D2 catot (`X:13`) |
| `5V_SERVO` | Nhánh từ điểm sao | `Q:31` ↔ C1+ (`X:30`) ↔ C2 (`Y:24`) ↔ J3.2 (`Y:23`) ↔ J4.2 (`Y:26`) ↔ dây đỏ servo M1, M2 |
| `5V_AUDIO` | Nhánh từ điểm sao | `L:31` ↔ C3+ (`C:4`) ↔ C4 (`F:7`) ↔ U2 VIN (`G:7`) ↔ U2 GAIN (`G:10`, cầu `G:10`–`E:10`–`E:7`) |
| `5V_ESP` | Sau D1 (≈4.65V) | D1 catot (`D:23`) ↔ C5+ (`F:23`) ↔ C6 (`H:23`) ↔ U1 chân 5V `DK-L21` (`I:23`) |
| `5V_EYES` | Nhánh từ điểm sao | `O:31` ↔ J6.1 (`AA:9`) ↔ R_EYE (trên dây, sát hộp pin module) ↔ lá + của E1 |
| `VSENSE_TOP` | Dây mảnh từ điểm sao | `N:31` ↔ R1 đầu trên (`V:8`) |
| `3V3` | Ra từ LDO devkit | U1 `DK-L1`/`DK-L2` (`I:3`/`I:4`) ↔ J8.1 (`A:1`) → U3 VDD ↔ J7.1 (`T:1`) → U4 VCC |
| `GND_STAR` | Điểm sao (thanh GND) | J1.2 (−, `D:34`) ↔ Q3 G (`D:32`) ↔ thanh GND `K:33`–`Q:33`. Mọi nhánh GND bên dưới chỉ gặp nhau ở đây |
| `GND_SERVO` | Nhánh GND | `Q:33` ↔ C1− (`Z:30`) ↔ C2 (`Z:24`) ↔ J3.1 (`Z:23`) ↔ J4.1 (`Z:26`) ↔ dây nâu servo |
| `GND_PWR` | Nhánh GND | `O:33` ↔ Q2 S (`X:20`) ↔ R6 (`X:21`); Q1 S (`Y:10`) ↔ R5 (`Y:11`) |
| `GND_AUDIO` | Nhánh GND | `L:33` ↔ C3− (`C:5`) ↔ C4 (`F:8`) ↔ U2 GND (`G:8`) |
| `GND_ESP` | Nhánh GND (= GND tín hiệu) | `N:33` ↔ U1 `DK-L22` (`I:24`); các chân GND khác của devkit `DK-R1` (`R:3`), `DK-R21/22` nối sẵn bên trong devkit. GND tín hiệu lấy từ đây: C5−, C6 (qua `I:24`); R2, C7, J7.2, J8.2, J9.3, C8, C9 (qua cụm `R:3` = `DK-R1`). Trên PCB cụm `R:3` **không** có dây riêng về điểm sao: nó về `DK-L22` qua GND bên trong devkit, nên khi rút devkit thì cụm này hở |
| `I2S_MIC_WS` | I2S mic | GPIO4 `DK-L4` (`I:6`) ↔ J8.3 (`C:1`) ↔ U3 WS |
| `I2S_MIC_SCK` | I2S mic | GPIO5 `DK-L5` (`I:7`) ↔ J8.4 (`D:1`) ↔ U3 SCK |
| `I2S_MIC_SD` | I2S mic | GPIO6 `DK-L6` (`I:8`) ↔ J8.5 (`E:1`) ↔ U3 SD |
| `MIC_LR` | Cấu hình mic | U3 L/R ↔ U3 GND (nối tắt ngay trên module mic → kênh trái) |
| `I2S_SPK_DIN` | I2S loa | GPIO7 `DK-L7` (`I:9`) ↔ U2 DIN (`G:11`) |
| `I2S_SPK_BCLK` | I2S loa | GPIO15 `DK-L8` (`I:10`) ↔ U2 BCLK (`G:12`) |
| `I2S_SPK_LRC` | I2S loa | GPIO16 `DK-L9` (`I:11`) ↔ U2 LRC (`G:13`) |
| `AMP_SD` | Bật/tắt amply | GPIO12 `DK-L18` (`I:20`) ↔ R13 2.2kΩ (`F:17`–`F:16`) ↔ U2 SD (`G:9`) |
| `SPK_P`, `SPK_N` | Ngõ ra BTL | U2 OUT+ ↔ J10.1 (`A:15`) ↔ LS1 (+); U2 OUT− ↔ J10.2 (`A:16`) ↔ LS1 (−). **Không nối xuống GND** |
| `LCD_MOSI` | SPI | GPIO47 `DK-R17` (`R:19`) ↔ J7.6 (`Y:1`) ↔ U4 SDI(MOSI) |
| `LCD_SCLK` | SPI | GPIO21 `DK-R18` (`R:20`) ↔ (R11 33Ω tùy chọn) ↔ J7.7 (`Z:1`) ↔ U4 SCK |
| `LCD_DC` | SPI | GPIO40 `DK-R8` (`R:10`) ↔ J7.5 (`X:1`) ↔ U4 DC |
| `LCD_CS` | SPI | GPIO41 `DK-R7` (`R:9`) ↔ J7.3 (`V:1`) ↔ U4 CS |
| `LCD_RES` | Điều khiển | GPIO39 `DK-R9` (`R:11`) ↔ J7.4 (`W:1`) ↔ U4 RESET |
| `LCD_BLK` | PWM đèn nền | GPIO42 `DK-R6` (`R:8`) ↔ J7.8 (`AA:1`) ↔ U4 LED |
| `SERVO0_PWM` | PWM | GPIO17 `DK-L10` (`I:12`) ↔ R7 (`V:23`→`W:23`) ↔ J3.3 (`X:23`) ↔ dây cam M1 |
| `SERVO1_PWM` | PWM | GPIO18 `DK-L11` (`I:13`) ↔ R8 (`V:26`→`W:26`) ↔ J4.3 (`X:26`) ↔ dây cam M2 |
| `EYES_PWM` | PWM | GPIO8 `DK-L12` (`I:14`) ↔ R3 (`V:10`) |
| `EYES_GATE` | Gate Q1 | R3 (`W:10`) ↔ Q1 G (`X:10`) ↔ R5 (`X:11`) |
| `EYES_DRV` | Drain Q1 | Q1 D (`Z:10`) ↔ J6.2 (`AA:10`) ↔ lá (−) hộp pin E1 |
| `OUT_PWM` | PWM | GPIO9 `DK-L15` (`I:17`) ↔ R4 (`V:19`) |
| `OUT_GATE` | Gate Q2 | R4 (`V:20`) ↔ Q2 G (`W:20`) ↔ R6 (`W:21`) |
| `OUT_DRV` | Drain Q2 | Q2 D (`Y:20`) ↔ J5.2 (−, `AA:17`) ↔ D2 anot (`X:16`) |
| `BTN_PTT` | Vào | GPIO10 `DK-L16` (`I:18`) ↔ J9.1 (`F:1`) ↔ C8 ↔ SW2 (chân kia về GND) |
| `BTN_MASK` | Vào | GPIO11 `DK-L17` (`I:19`) ↔ J9.2 (`G:1`) ↔ C9 ↔ SW3 (chân kia về GND) |
| `VSENSE` | Analog | GPIO1 `DK-R4` (`R:6`) ↔ `S:6`–`V:6` ↔ R1, R2, C7 |
| `BOOT`, `LED_RGB` | Có sẵn trên devkit | GPIO0 (nút BOOT), GPIO48 (LED RGB). Không đi dây ra PCB |

## 2. Nguồn & phân phối điện (HW1)

### 2.1 Cây nguồn

```
Sạc dự phòng ─ cáp USB ─► J1 (domino 2P) ─► F1 2.5A ─► Q3 P-FET ─► J2.1 ═► SW1 (≥3A, gắn trên vỏ mũ) ═► J2.2
                                                                                                    │
                                                ★ ĐIỂM SAO: thanh 5V_MAIN K:31–Q:31 / thanh GND_STAR K:33–Q:33
   ┌───────────────────┬───────────────────┬──────────────────────┬─────────────────┬───────────────┼──────────────┐
   ▼ Q:31 / Q:33       ▼ L:31 / L:33       ▼ M:31 / N:33          ▼ O:31            ▼ P:31          ▼ N:31
 5V_SERVO/GND_SERVO  5V_AUDIO/GND_AUDIO  5V_ESP/GND_ESP         5V_EYES           5V_MAIN → J5    VSENSE_TOP
 C1 1000µF + C2      C3 470µF + C4       D1 SS34/1N5819         R_EYE (1 con)     D2 1N5819       R1 100k
 sát J3/J4           sát VIN MAX98357    → C5 470µF + C6        → E1 → Q1         tải ≤1A → Q2    R2 100k // C7
 → M1, M2            → U2 → loa LS1      sát chân 5V devkit     (GND_PWR: O:33)   (GND_PWR)       → GPIO1
                                         → LDO devkit → 3V3: ESP32-S3, LCD, mic
```

### 2.2 Nối đất hình sao – quy tắc

1. **Một điểm duy nhất:** GND của sạc (J1.2) chỉ nối vào thanh GND_STAR. Mỗi nhánh có dây GND riêng chạy về thanh này; các nhánh **không** nối GND với nhau ở chỗ khác (trừ bên trong devkit, nơi các chân GND vốn đã nối chung).
2. **Servo đi riêng:** 5V_SERVO và GND_SERVO đi cặp sát nhau, ngắn nhất từ thanh sao sang J3/J4 (mặt dưới, dây đồng trần 0.8mm hoặc 22AWG), không vòng qua vùng âm thanh. Dòng khởi động/kẹt 1.5–2A của servo không được chạy qua dây GND của ESP32 hay của mic.
3. **Âm thanh đi riêng:** GND_AUDIO từ thanh sao tới C3−/U2 GND. Dây mic (I2S) lấy GND từ GND_ESP, không lấy từ GND_AUDIO hay GND_SERVO.
4. **GND công suất (GND_PWR):** source của Q1, Q2 về thanh sao bằng dây riêng (dòng tải dự phòng tới 1A).
5. **GND tín hiệu (GND_ESP):** LCD, mic, nút, cầu phân áp VSENSE lấy GND từ chân GND của devkit (`DK-R1` hoặc `DK-L22`).
6. Thanh sao làm bằng dây đồng trần 0.8mm hàn dọc 7 lỗ; mỗi nhánh hàn vào **một lỗ riêng** của thanh. Dây nhánh là dây silicone có vỏ nên được phép bắt chéo nhau ở mặt dưới. Có hai chỗ dây GND có vỏ bắt chéo **dây trần 5V** (GND_ESP ở `N:31`, GND_AUDIO ở `J:31`, xem mục 5.3): lót ống co nhiệt tại chỗ giao, không hàn ở đó; chạm chập tại đây là chập thẳng 5V–GND của sạc.

### 2.3 D1 – chống dòng ngược, tùy loại devkit

Có cần D1 hay không phụ thuộc chân 5V của devkit nối vào đâu (sơ đồ nguyên lý ở mục 7). Xác định loại board bằng chữ in trên chip LDO cạnh cổng USB và phép đo ở breadboard B0 #5:

| Devkit | Bên trong | D1 |
|---|---|---|
| ESP32-S3-DevKitC-1 chính hãng (v1.0, v1.1) | Mỗi cổng USB vào chân 5V qua một diode 1N5819HW; chân 5V nối thẳng vào LDO **SGM2212-3.3** (800mA, sụt áp ~0.25V ở 0.5A, ~0.4V ở 0.8A) | **Giữ D1.** Thiếu nó, USB máy tính đi qua diode của devkit, ra chân 5V và nuôi cả servo, amply |
| Clone YD-ESP32-S3 (2 cổng USB-C, VCC-GND) và các bản cùng thiết kế | Diode ở 2 cổng USB, thêm diode D3 giữa chân 5V và mạng 5V bên trong (pad "IN-OUT" để nối tắt); LDO **CJ6107A33** (1A, sụt 0.5V ở 1A) | **Nối tắt D1 bằng dây**: D3 đã chặn dòng đi ra chân 5V, giữ D1 là hai lần sụt áp |
| Clone khác | Chưa rõ; có bản dùng AMS1117 (sụt ~1.1V) | Đo như breadboard B0 #5: chỉ cắm USB máy tính, tải 1kΩ ở chân 5V. ≈0V → có diode, nối tắt D1. ≈4.6–5V → giữ D1 |

- D1 (1N5819, hoặc SS34 sụt thấp hơn một chút) nối **anot ← 5V_MAIN, catot → chân 5V của devkit**. Ở 0.7A (Wi-Fi phát + LCD 2.4") sụt khoảng 0.45V.
- 5V_MAIN thấp nhất mà 3V3 còn ổn định lúc Wi-Fi phát: DevKitC-1 chính hãng + D1 ≈ 0.45 + 0.3 + 3.3 = **~4.1V** (~4.5V khi LDO nóng, diode sụt tối đa); clone YD, D1 nối tắt ≈ 0.45 (D3) + 0.4 + 3.3 = **~4.2V**; board AMS1117 + D1 ≈ **~4.85V**, sát mức sạc dự phòng khi chịu tải: nên đổi board. Ngưỡng cảnh báo HUD 4.6V (mục 2.5) nằm trên hai mức đầu.
- Giữ hay bỏ D1, **C5 470µF + C6 100nF** vẫn đặt sát chân 5V devkit: khi servo kéo 5V_MAIN sụt nhanh, diode ngắt và C5 nuôi LDO trong vài ms.
- Thay thế tốt hơn (tùy chọn): IC "ideal diode" TI LM66100 (1.5A, sụt ~55mV ở 0.7A, chặn dòng ngược), cần đế chuyển SC-70/SOT-23-6.
- Nạp code bằng USB máy tính lúc mũ đang có nguồn: D1 (hoặc D3 của clone) chặn dòng sang servo/amply. Máy tính tắt mà cáp vẫn cắm: diode ở cổng USB của hai loại devkit trên chặn sạc dự phòng cấp ngược ra máy tính; với clone khác thì tắt SW1 hoặc rút cáp. Đo thực tế (loại devkit, LDO, diode ở chân 5V): ___

### 2.4 Tụ lọc – vị trí

| Tụ | Giá trị | Nhánh | Đặt ở (dự kiến) | Lý do |
|---|---|---|---|---|
| C1 | 1000µF/10V hóa | 5V_SERVO | `X:30`(+) / `Z:30`(−), sát J3/J4 | Cấp dòng đỉnh khi servo khởi động, giữ 5V_MAIN không sụt |
| C2 | 100nF gốm | 5V_SERVO | `Y:24`–`Z:24`, giữa J3 và J4 | Lọc nhiễu cao tần chổi than servo |
| C3 | 470µF/10V hóa | 5V_AUDIO | `C:4`(+) / `C:5`(−), cạnh U2 | Đỉnh dòng 450mA của amply class-D |
| C4 | 100nF gốm | 5V_AUDIO | `F:7`–`F:8`, sát chân VIN/GND của U2 (nằm dưới module, linh kiện thấp) | Lọc tần số chuyển mạch ~300kHz |
| C5 | 470µF/10V hóa, Ø6.3mm | 5V_ESP | `F:23`(+) / `F:24`(−), sát chân 5V devkit | Đỉnh 500mA khi WiFi phát; nuôi LDO khi servo kéo 5V_MAIN sụt (mục 2.3) |
| C6 | 100nF gốm | 5V_ESP | `H:23`–`H:24`, cầu hàn sang `I:23`/`I:24` | Lọc sát chân |
| C7 | 100nF gốm | VSENSE | `U:4`–`U:6`, song song R2 | Lọc ADC, xem 2.5 |

Tụ hóa 10V là mức tối thiểu theo HW1; dùng 16V nếu mua được cùng kích thước (dư áp tốt hơn, tuổi thọ cao hơn). Lắp đúng cực: vạch trên vỏ tụ là cực âm.

### 2.5 Đo điện áp 5V_MAIN (VSENSE)

```
5V_MAIN (N:31) ── dây mảnh ── R1 100k 1% ──┬── VSENSE ──► GPIO1 (ADC1_CH0, DK-R4)
                                           ├── R2 100k 1% ── GND_ESP
                                           └── C7 100nF ──── GND_ESP
```

| Đại lượng | Giá trị |
|---|---|
| Tỉ lệ chia | V_gpio = V × R2 / (R1 + R2) = **V / 2** |
| Suy giảm ADC | 12 dB (`ADC_ATTEN_DB_12`, tên cũ `ADC_ATTEN_DB_11`): dải đo khoảng 0–3.1V theo tài liệu ESP-IDF cho ESP32-S3 (kiểm lại với bản IDF đang dùng) → đo được 5V_MAIN tới ~6.2V. GPIO1 là ADC1 nên dùng được cùng lúc với WiFi |
| Firmware | `vbus_mv = mV đọc được × 2` (FW4: `esp_adc` oneshot + calibration). Hiện lên HUD, trả trong `self.system.get_status` |
| Ngưỡng cảnh báo | 5V_MAIN < **4.6V** ⇔ V_gpio < **2.30V** |
| Dòng qua cầu chia | 5V / 200kΩ = 25µA, không đáng kể |
| Trở kháng nguồn | R1 ∥ R2 = 50kΩ; với C7: τ = 50kΩ × 100nF = 5ms (f_c ≈ 32Hz). Lọc gợn servo và cấp điện tích cho tụ lấy mẫu ADC; sau khi cấp nguồn chờ ≥25ms (5τ) mới đọc |
| Sai số | R 1% → tỉ lệ sai ≤ ±1% (±50mV ở 5V) + sai số ADC sau calibration (vài chục mV, nhân 2). Hiệu chỉnh một lần bằng đồng hồ, ghi offset vào mục 6 |

| 5V_MAIN | V_gpio | Ghi chú |
|---|---|---|
| 4.40V | 2.200V | HUD báo đỏ. DevKitC-1 chính hãng / clone YD còn giữ được 3V3; board AMS1117 + D1 đã thiếu áp (mục 2.3) |
| 4.60V | 2.300V | **Ngưỡng cảnh báo HUD** |
| 5.00V | 2.500V | Danh định |
| 5.25V | 2.625V | Trần không tải theo HW1; còn dư ~15% dải ADC |

### 2.6 Ngân sách dòng

| Tải | Nhánh | Trung bình | Đỉnh | Ghi chú |
|---|---|---|---|---|
| ESP32-S3 + WiFi | 5V_ESP | 120mA | 500mA | Đỉnh khi phát WiFi |
| LCD 2.4" + đèn nền | 5V_ESP → 3V3 | 100mA | 170mA | Lấy từ LDO 3.3V của devkit. Ước tính từ 0.57W của nhà sản xuất; đo thực tế: ___ mA |
| INMP441 | 5V_ESP → 3V3 | 1.4mA | – | |
| MAX98357 + loa 8Ω | 5V_AUDIO | 100mA | 450mA | ~1.8W ở 5V |
| 2× MG90S | 5V_SERVO | 0 (đứng yên) / 300mA (đang chạy) | 1.5–2A (khởi động, kẹt) | Firmware tránh đỉnh trùng nhau (FW2: S1 khởi động trễ ~30ms) |
| Đèn mắt (1 module, 2 LED) | 5V_EYES | 20–40mA (ước tính HW1) | – | Ví dụ ở mục 4.2: 28mA cho cả module. Đo thực tế: ___ mA |
| Output dự phòng J5 | 5V_MAIN | 0 | ≤1A | **Chưa có trong ngân sách HW1**: chỉ bật khi servo đứng yên, hoặc tính lại ngân sách |
| **Tổng** | | **~0.45A** khi hội thoại | **~3A** xấu nhất (chưa kể J5) | Sạc ≥2.4A dùng được nhờ firmware tách đỉnh dòng (FW2) |

Tải 3V3 lúc đỉnh ≈ 0.65A (ESP 355–500mA + LCD ~170mA): trong khả năng LDO của DevKitC-1 (SGM2212, 800mA) và clone YD (CJ6107, 1A).

F1 (2.5A hold) có thể ngắt khi servo kẹt kéo dài cộng loa to; đỉnh ngắn vài trăm ms thì không ngắt. Nếu F1 nóng hoặc ngắt khi thử tải nặng, kiểm tra cơ khí servo trước khi nghĩ tới tăng cỡ cầu chì.

### 2.7 Đầu vào từ sạc dự phòng

| Cổng | Cách nối | Lưu ý |
|---|---|---|
| USB-A | Cắt cáp USB-A ≤50cm: dây đỏ (+) → J1.1, dây đen (−) → J1.2. Bỏ dây xanh/trắng (D+/D−), bọc co nhiệt riêng từng sợi | Dây nguồn ≥22AWG. Cáp sạc rẻ tiền có dây nguồn rất mảnh → đo sụt áp ở bước 4 mục 2.8 |
| USB-C | Bắt buộc dùng mạch "USB-C sink/breakout" có **5.1kΩ từ CC1 xuống GND và 5.1kΩ từ CC2 xuống GND** (mạch tốt có sẵn cả hai, cắm xoay chiều nào cũng được). VBUS → J1.1, GND → J1.2 | Không có điện trở CC thì cổng C **không cấp điện** |

- **Chống cắm ngược: Q3** (mục 2.9). Vẫn đo cực tính tại J1 (que đỏ ở J1.1 phải đọc +5V) trước khi cắm module (mục 2.8 bước 2).
- **Dòng ra của sạc:** tối thiểu 2.4A, **nên dùng cổng 3A**. Nhóm robot Otto (xiaozhi) yêu cầu nguồn ≥3A khi chạy servo; FAQ xiaozhi: loa 3W cần nguồn ≥5V/2A (mục 7).
- Sạc dự phòng có thể tự tắt khi tải nhỏ. Mũ luôn kéo khoảng 250mA (Wi-Fi luôn bật + LCD 2.4"), cao hơn nhiều ngưỡng tự tắt thường gặp (ví dụ chip IP5306 tắt khi tải dưới 45mA trong 32 giây); phải kiểm chứng bằng bài thử 2 giờ (IT1 E14). Không dùng deep sleep.
- HUD không đo được % pin của sạc, chỉ hiện điện áp 5V_MAIN.

### 2.8 Đo kiểm khối nguồn (nghiệm thu HW1 §6)

Thử theo thứ tự; ở bước 1–3 **chưa cắm devkit và module**. Ghi số đo vào mục 6.

| # | Bước | Cách đo | Đạt khi |
|---|---|---|---|
| 1 | Trước khi cấp điện | Đồng hồ thang Ω: 5V_MAIN ↔ GND_STAR, 5V_ESP ↔ GND | >1kΩ (số đo tăng dần do tụ nạp là bình thường) |
| 2 | Không tải | Cấp sạc, bật SW1; đo J1, thanh 5V_MAIN, `I:23` (5V_ESP) | 5V_MAIN **4.9–5.25V**; 5V_ESP **≥4.5V** |
| 3 | Đủ các nhánh | Đo tại J3.2, J4.2, U2 VIN, J6.1, J5.1, đầu trên R1 | Bằng 5V_MAIN (chênh <50mV) |
| 4 | Tải nặng nhất | Cắm devkit + module, nạp firmware; 2 servo chạy qua lại liên tục + loa âm lượng tối đa + WiFi phát liên tục, **10 phút** | **Không có lần reset nào** (đọc `esp_reset_reason` trong log sau mỗi lần khởi động, đếm số lần); VSENSE luôn **≥4.6V**; 3V3 tại `I:3` thấp nhất **≥3.2V** (đồng hồ chế độ MIN hoặc máy hiện sóng); áp rơi Q3 (`F:32` → `E:32`) <0.15V |
| 5 | Dòng ngược | (a) Tắt SW1, rút servo khỏi J3/J4 (xung GPIO17/18 có thể nuôi ngược nhánh servo qua mạch vào của servo, làm sai số đo), cắm USB máy tính vào devkit: đo 5V_MAIN tại thanh sao. (b) Bật SW1 và cắm USB máy tính: ESP chạy bình thường, D1 không nóng. (c) Tắt sạc, vẫn cắm USB: đo điện áp tại cổng USB và tại thanh sao | (a) 5V_MAIN <0.3V (D1 chặn được); (b) không có dòng chảy ngược vào sạc; (c) ghi số đo vào mục 6 |
| 6 | Hiệu chỉnh VSENSE | So giá trị HUD với đồng hồ ở 3 mức (không tải, servo chạy, loa to) | Sai lệch ≤ ±50mV sau khi bù offset |

### 2.9 Q3 – chống cắm ngược nguồn

```
J1.1 (+) ── F1 ── 5V_FUSED ── Q3 D (F:32)    Q3 S (E:32) ── 5V_PROT ── J2.1 → SW1 → 5V_MAIN
                              Q3 G (D:32) ── J1.2 (−, D:34)
```

- Q3 = **AO3401A** (MOSFET kênh P, −30V, −4A, Rds(on) <60mΩ ở Vgs −4.5V, Vgs tối đa ±12V) trên đế SOT-23 → 3 chân: 1 = G, 2 = S, 3 = D như AO3400A. Đặt ngay sau F1, trước SW1.
- Nối đúng cực: diode thân (D → S) dẫn trước, sau đó Vgs ≈ −5V làm Q3 dẫn hoàn toàn. Sụt ~30mV ở 0.5A, ~0.15V ở 2.5A (đỉnh ngắn khi servo kẹt).
- Nối ngược cực (đấu nhầm dây cáp USB cắt; nhiều cáp rẻ không theo màu chuẩn): gate ở +5V so với source, diode thân phân cực ngược → Q3 ngắt, không có dòng vào mạch.
- Không cần zener ở gate (5V < 12V). Không dùng diode nối tiếp (SS34/1N5819 sụt 0.5–0.9V ở 3A, mất áp cho servo và amply), cũng không dùng diode song song sau F1 (lúc sự cố đường nguồn xuống −0.5…−1V, quá mức −0.3V của MAX98357A). Theo TI SLVA139 (mục 7).
- Kiểm tra trước khi cấp điện (mục 5.5 #17): thang diode `F:32` (que đỏ) → `E:32` (que đen) ≈0.5–0.7V, chiều ngược hở; `D:32` thông `D:34`.

## 3. Bảng chân GPIO & đấu nối tín hiệu (HW2)

### 3.1 Bảng chân

Dựa trên board `bread-compact-wifi-lcd` của xiaozhi (firmware gốc chạy được ngay ở P1), chỉ đổi LCD RES sang GPIO39. Cột "Chân devkit" theo ESP32-S3-DevKitC-1 của Espressif; **bản clone có thể khác**, phải đối chiếu nhãn in trên board trước khi hàn. Cột "Lỗ PCB" theo bố trí mục 5 (header phải ở cột R là dự kiến).

| GPIO | Chức năng | Hướng | Chân devkit | Lỗ PCB | Nối tới | Ghi chú |
|---|---|---|---|---|---|---|
| 4 | INMP441 WS | Ra | `DK-L4` | `I:6` | J8.3 → U3 WS | Giống bread board |
| 5 | INMP441 SCK | Ra | `DK-L5` | `I:7` | J8.4 → U3 SCK | |
| 6 | INMP441 SD | Vào | `DK-L6` | `I:8` | J8.5 ← U3 SD | |
| 7 | MAX98357 DIN | Ra | `DK-L7` | `I:9` | U2 DIN (`G:11`) | |
| 15 | MAX98357 BCLK | Ra | `DK-L8` | `I:10` | U2 BCLK (`G:12`) | |
| 16 | MAX98357 LRC | Ra | `DK-L9` | `I:11` | U2 LRC (`G:13`) | |
| 47 | LCD SDI (MOSI) | Ra | `DK-R17` | `R:19` | J7.6 → U4 SDI | |
| 21 | LCD SCK (SCLK) | Ra | `DK-R18` | `R:20` | J7.7 → U4 SCK | R11 33Ω nối tiếp nếu ảnh nhiễu |
| 40 | LCD DC | Ra | `DK-R8` | `R:10` | J7.5 → U4 DC | |
| 41 | LCD CS | Ra | `DK-R7` | `R:9` | J7.3 → U4 CS | SPI mode 0 |
| **39** | LCD RESET | Ra | `DK-R9` | `R:11` | J7.4 → U4 RESET | **Đổi so với bread board (GPIO45)**: GPIO45 là chân strapping VDD_SPI, LCD có điện trở kéo lên có thể làm chip không khởi động |
| 42 | LCD LED (đèn nền) | Ra PWM | `DK-R6` | `R:8` | J7.8 → U4 LED | LEDC timer 0 / channel 0 (`PwmBacklight` của xiaozhi) |
| 17 | Servo trái (S0) | Ra PWM | `DK-L10` | `I:12` | R7 220Ω → J3.3 | LEDC timer 1, 50 Hz, 14-bit, channel 1 |
| 18 | Servo phải (S1) | Ra PWM | `DK-L11` | `I:13` | R8 220Ω → J4.3 | Timer 1, channel 2 |
| 8 | Gate MOSFET đèn mắt | Ra PWM | `DK-L12` | `I:14` | R3 100Ω → Q1 G | LEDC timer 2, 25 kHz, 10-bit, channel 3 |
| 9 | Gate MOSFET output dự phòng | Ra PWM | `DK-L15` | `I:17` | R4 100Ω → Q2 G | Timer 2, channel 4 |
| 10 | Nút PTT | Vào | `DK-L16` | `I:18` | J9.1 → SW2 | Bấm = nối GND; pull-up nội; C8 100nF |
| 11 | Nút mặt nạ | Vào | `DK-L17` | `I:19` | J9.2 → SW3 | Bấm = nối GND; pull-up nội; C9 100nF |
| 12 | SD MAX98357A (bật/tắt amply) | Ra | `DK-L18` | `I:20` | R13 2.2kΩ → U2 SD (`G:9`) | Thấp = tắt, cao = bật (kênh trái); firmware theo `EnableOutput` (mục 3.4) |
| 1 | VSENSE (ADC1_CH0) | Vào analog | `DK-R4` | `R:6` | Nút chia R1/R2/C7 (`T:6`) | Đo 5V_MAIN, mục 2.5 |
| 0 | Nút BOOT (có sẵn trên devkit) | Vào | `DK-R14` | (`R:16`) | – | Giữ khi khởi động để vào cấu hình WiFi. Không đi dây |
| 48 | LED RGB trên devkit | Ra | `DK-R16` | (`R:18`) | – | Đèn trạng thái xiaozhi (`SingleLed`). Xem lưu ý v1.1 bên dưới |

Chân nguồn của devkit: 3V3 = `DK-L1`, `DK-L2` (`I:3`, `I:4`); 5V = `DK-L21` (`I:23`, nhận 5V_ESP); GND = `DK-L22` (`I:24`), `DK-R1` (`R:3`), `DK-R21`, `DK-R22` (`R:23`, `R:24`). `DK-L3` (`I:5`) là RST/EN: không nối gì, cẩn thận cầu hàn với lỗ bên cạnh.

**Chân không được dùng:**

| Chân | Lý do |
|---|---|
| 26–32 | Flash SPI |
| 33–37 | PSRAM octal của N16R8 (35, 36, 37 vẫn có mặt trên header `DK-R11`…`DK-R13`, **không** nối) |
| 19, 20 | USB (D−/D+) |
| 43, 44 | UART0, dùng cho log |
| 3, 45, 46 | Strapping |

**Chân còn trống để mở rộng:** 2 (`DK-R5`), 13 (`DK-L19`), 14 (`DK-L20`), 38 (`DK-R10`).

> **Lưu ý phiên bản devkit:** ESP32-S3-DevKitC-1 **v1.0** nối LED RGB vào GPIO48 (khớp bảng trên); bản **v1.1** chuyển LED RGB sang **GPIO38**. Nếu devkit là v1.1 thì GPIO38 **không còn trống** và firmware phải đổi `BUILTIN_LED_GPIO`. Bản clone thường theo v1.0. Đo thực tế (phiên bản / chân LED): ___
>
> N16R8 dùng flash/PSRAM 3.3V nên GPIO47/48 có mức logic 3.3V bình thường (bản R16V 1.8V thì không).

### 3.2 Kiểm tra trước khi hàn (HW2 §4)

| Hạng mục | Cách kiểm | Kết quả |
|---|---|---|
| Loại devkit | Đọc nhãn: DevKitC-1 chính hãng (v1.0/v1.1) hay clone | Đo thực tế: ___ |
| Chân 39, 40, 41, 42, 47, 48 có ra header | Đối chiếu nhãn in trên board | Đo thực tế: ___ |
| Thứ tự chân header giống bảng `DK-Lx`/`DK-Rx` | Đối chiếu từng chân | Đo thực tế: ___ |
| LCD đúng driver ST7789 | Đọc nhãn túi: "Driver: ST7789" (cùng mã MSP2401 có bản ILI9341, firmware không chạy được bản đó) | Đo thực tế: ___ |
| Thứ tự chân module MAX98357 | Đọc nhãn (Adafruit: LRC, BCLK, DIN, GAIN, SD, GND, Vin) | Đo thực tế: ___ |

### 3.3 INMP441 (U3, mic) – qua J8

| INMP441 | Mạng | J8 (lỗ PCB) | Nối tới trên PCB |
|---|---|---|---|
| VDD | `3V3` | J8.1 (`A:1`) | `DK-L1` (`I:3`) |
| GND | `GND_ESP` | J8.2 (`B:1`) | Cụm GND `H:3` → `DK-R1` |
| L/R | `MIC_LR` | – | Nối tắt với GND **ngay trên module mic** (chọn kênh trái) |
| WS | `I2S_MIC_WS` | J8.3 (`C:1`) | GPIO4 (`I:6`) |
| SCK | `I2S_MIC_SCK` | J8.4 (`D:1`) | GPIO5 (`I:7`) |
| SD | `I2S_MIC_SD` | J8.5 (`E:1`) | GPIO6 (`I:8`) |

J8 cần **5 chân** (HW4 ghi "J8 MIC 4P" là thiếu một chân, xem cuối mục 5.2).

- SD là ngõ ra ba trạng thái; datasheet khuyên 100kΩ kéo xuống GND. Phần lớn module tròn có sẵn điện trở này, và bước dò mic của firmware lúc khởi động (log `MicProbe`) hiểu cả hai trường hợp: chỉ thêm 100kΩ ở đầu mic khi module không có.
- Tụ 100nF sát VDD (có sẵn trên module). Lỗ thu âm ở mặt dưới module: không để thiếc lem chạm vòng GND quanh lỗ (lỗi hay gặp trong cộng đồng xiaozhi).
- Không cấp clock I2S khi mic chưa có nguồn. Sau khi có clock, mic xuất số 0 trong 2^18 chu kỳ SCK đầu (~256ms); firmware đã chờ.

### 3.4 MAX98357A (U2, amply) – cắm trên PCB

| MAX98357 | Mạng | Lỗ PCB (dự kiến) | Ghi chú |
|---|---|---|---|
| VIN | `5V_AUDIO` | `G:7` | C3 470µF + C4 100nF sát chân |
| GND | `GND_AUDIO` | `G:8` | Về thanh sao bằng dây riêng |
| SD | `AMP_SD` | `G:9` | GPIO12 qua R13 2.2kΩ: thấp (<0.16V) = tắt, cao (>1.4V) = bật, phát kênh trái. R13 hạn dòng khi amply chưa có nguồn mà GPIO đã lên mức cao |
| GAIN | `5V_AUDIO` | `G:10` | **Nối VIN** (cầu `G:10`–`E:10`–`E:7`) = 6 dB, xem bảng dưới |
| DIN | `I2S_SPK_DIN` | `G:11` | GPIO7 |
| BCLK | `I2S_SPK_BCLK` | `G:12` | GPIO15 |
| LRC | `I2S_SPK_LRC` | `G:13` | GPIO16 |
| OUT+ / OUT− | `SPK_P` / `SPK_N` | J10.1 / J10.2 (`A:15` / `A:16`) | 2 dây ngắn từ cọc loa của module xuống J10; loa LS1 cắm vào J10 |

| Cách nối GAIN | Độ lợi | Khi nào dùng |
|---|---|---|
| Nối VIN | 6 dB | **Mặc định Rev B** |
| Nối GND | 12 dB | Tiếng nhỏ (hướng dẫn breadboard chính thức của xiaozhi nối kiểu này) |
| 100kΩ lên VIN | 3 dB | Vẫn rè hoặc quá to ở 6 dB |
| Để trống | 9 dB | Không dùng: GAIN chỉ được đọc lúc bật nguồn, để hở dễ bắt nhiễu; FAQ xiaozhi ghi GAIN hở là nguyên nhân hay gặp của tiếng khàn |

**Vì sao SD nối GPIO12, GAIN = 6 dB.** Ngõ ra I2S simplex của xiaozhi là mono, chỉ có dữ liệu ở kênh trái, kênh phải bằng 0. Rev A để SD trống: trở kéo lên 1MΩ trên module đặt amply ở chế độ (L+R)/2, mất 6 dB → tổng 9 − 6 = 3 dB (suy từ cấu hình I2S, đo lại khi thử). Rev B: SD ở mức cao chỉ phát kênh trái (0 dB) + GAIN 6 dB → to hơn Rev A khoảng 3 dB ở cùng âm lượng phần mềm. Firmware kéo SD xuống thấp khi audio service dừng ngõ ra (sau 15 giây không phát), nên lúc chờ và lúc cấu hình Wi-Fi amply tắt hẳn, không xì. Chưa nối dây GPIO12 thì module tự bật như Rev A (nhỏ hơn 6 dB).

**Phương án A – SD nối thẳng VIN** (cách của hướng dẫn chính thức xiaozhi; breadboard thử dùng từ 2026-10-09): một dây từ chân SD sang chân VIN của module, không lắp R13, không nối GPIO12. Amply luôn bật ở chế độ kênh trái, to bằng cách dùng GPIO12 nhưng không tắt được lúc chờ (có thể nghe xì nhẹ). Firmware vẫn điều khiển GPIO12; chân đó để trống thì không ảnh hưởng. Trên PCB: bỏ R13 và dây GPIO12, nối `G:9`–`H:9`–`H:7`–`G:7` ở mặt dưới (đi vòng qua cột `H`, không chạm `G:8` là GND).

| Chân SD nối | Kết quả |
|---|---|
| VIN (phương án A) hoặc GPIO12 mức cao qua R13 (phương án B) | Bật, chỉ phát kênh trái: đủ mức |
| Để hở | Bật, chế độ (L+R)/2: nhỏ đi 6 dB |
| GND | **Tắt hẳn amply, không có tiếng.** Không nối SD xuống GND (khác với chân GAIN) |

Không nối SD chung với GAIN: nếu sau này chuyển GAIN xuống GND thì amply tắt luôn.

- Nguồn amply: C3 470µF + C4 100nF **sát chân VIN** (datasheet: tối thiểu 10µF + 0.1µF). Dây GND của amply về điểm sao phải chắc: issue #392 của xiaozhi là tiếng rè do thiếu dây GND amply.
- Dây loa xoắn đôi, ngắn, đi xa cáp mic. Datasheet không yêu cầu ferrite; chỉ thêm khi thấy nhiễu.

> **Ngõ ra cầu (BTL):** OUT+ và OUT− đều là tín hiệu PWM đối xứng. **Không nối OUT− xuống GND**, không dùng chung dây âm loa với GND, không đo hai cọc loa bằng máy hiện sóng có kẹp mass nối đất. Loa 3W 8Ω; ở 5V amply ra khoảng 1.8W.

### 3.5 LCD 2.4" ST7789 320×240 (U4, module MSP2401) – qua J7

U4 là module LCD 2.4" SPI mã MSP2401: driver ST7789, tấm 240×320 dùng **ngang 320×240**, không có cảm ứng. Cạnh dưới có header 14 chân 2.54mm (chân 1 = VCC, pad vuông, số 14 in ở đầu kia), cạnh trên có header 4 chân của khe thẻ SD (không dùng). Chỉ nối chân 1–8.

J7 xếp **đúng thứ tự chân 1–8 của module** (J7.n = chân n) nên cáp 8 sợi cắm thẳng 1:1: chân 1 (VCC) ở `T:1`, có ô đỏ trên hình. Trước khi cấp điện vẫn đối chiếu tên chân in trên module với bảng dưới (cắm ngược cáp là cấp ngược nguồn cho LCD).

| J7 (lỗ) | Mạng | Nối tới | Chân U4 (số · tên in trên module) |
|---|---|---|---|
| J7.1 (`T:1`) | `3V3` | `DK-L1`/`DK-L2` | 1 · VCC |
| J7.2 (`U:1`) | `GND_ESP` | `DK-R1` (`R:3`) | 2 · GND |
| J7.3 (`V:1`) | `LCD_CS` | GPIO41 | 3 · CS |
| J7.4 (`W:1`) | `LCD_RES` | GPIO39 | 4 · RESET |
| J7.5 (`X:1`) | `LCD_DC` | GPIO40 | 5 · DC/RS |
| J7.6 (`Y:1`) | `LCD_MOSI` | GPIO47 | 6 · SDI(MOSI) |
| J7.7 (`Z:1`) | `LCD_SCLK` | GPIO21 | 7 · SCK |
| J7.8 (`AA:1`) | `LCD_BLK` | GPIO42 | 8 · LED |
| – | – | – | 9 · SDO(MISO) và 10–14 · T_CLK, T_CS, T_DIN, T_DO, T_IRQ: **bỏ trống** |

| Loại tấm màn | Cấu hình firmware (`config.json` → `sdkconfig_append`) | Build |
|---|---|---|
| IPS (mặc định) | `CONFIG_LCD_ST7789_240X320=y` (bật đảo màu) | `jarvis-helmet` |
| TN (non-IPS): nền HUD hiện gần trắng, màu bị đảo | `CONFIG_LCD_ST7789_240X320_NO_IPS=y` | `jarvis-helmet-noips` |

- SPI mode 0, 40 MHz, có CS. Hướng ngang do `DISPLAY_SWAP_XY` trong `config.h`. Ảnh ngược 180° thì đảo cả `DISPLAY_MIRROR_X` và `DISPLAY_MIRROR_Y`, ảnh bị lật gương thì đảo một trong hai.
- VCC nhận 3.3–5V (module có LDO riêng), logic 3.3V. Thiết kế cấp **3V3** từ LDO devkit để J7 giữ nguyên: ước tính ~100mA, đỉnh ~170mA (mục 2.6), đo lại ở P1. Chân LED đi vào transistor đèn nền trên module (mức cao = sáng) nên nhận PWM thẳng từ GPIO42.
- Cùng mã MSP2401 còn có bản driver ILI9341. Nhãn phải ghi **ST7789** (mục 3.2).
- PCB 77.2×42.7mm, vùng hiển thị 36.7×49.0mm, lớn hơn nhiều so với màn 1.54" cũ: chừa chỗ trong mũ, bắt giá đỡ qua 4 lỗ ở góc module.

### 3.6 Nút bấm (SW2 PTT, SW3 mặt nạ) – qua J9

| J9 (lỗ) | Mạng | Nối tới | Tụ chống dội |
|---|---|---|---|
| J9.1 (`F:1`) | `BTN_PTT` | GPIO10 (`I:18`) | C8 100nF: `F:2` (nối `F:1`) – `F:3` (GND) |
| J9.2 (`G:1`) | `BTN_MASK` | GPIO11 (`I:19`) | C9 100nF: `G:2` (nối `G:1`) – `G:3` (GND) |
| J9.3 (`H:1`) | `GND_ESP` | Cụm GND `F:3`–`G:3`–`H:3`–`H:2`–`H:1` → dây có vỏ tới `DK-R1` (`R:3`) | – |

- Mỗi nút: một chân về GPIO, chân kia về GND (chung J9.3). Không cần điện trở kéo lên ngoài: firmware bật pull-up nội (~45kΩ).
- Pull-up nội × 100nF ≈ 4.5ms: đủ lọc dội cơ khí, không ảnh hưởng ngưỡng bấm/giữ 400ms của FW5.
- Tụ đặt **trên PCB** gần J9 để lọc cả nhiễu bắt vào dây nút trong mũ.

### 3.7 Tín hiệu servo và gate MOSFET

| Tín hiệu | Linh kiện nối tiếp | Lý do |
|---|---|---|
| GPIO17 → J3.3, GPIO18 → J4.3 | R7, R8 220Ω | Giới hạn dòng vào chân GPIO nếu dây servo chạm 5V hoặc servo hỏng: (5 − 3.3)V / 220Ω ≈ 7.7mA |
| GPIO8 → Q1 G, GPIO9 → Q2 G | R3, R4 100Ω | Xem mục 4.4 |
| Tùy chọn: J3.3, J4.3 → GND | R9, R10 10kΩ | Chỉ lắp nếu servo giật lúc ESP32 khởi động (GPIO thả nổi) |

MG90S nhận được mức 3.3V, không cần mạch chuyển mức. Chi tiết phần công suất ở mục 4.

### 3.8 Quy tắc đi dây tín hiệu

- **Mic:** đặt gần miệng, xa loa, bọc mút. Cáp I2S tới mic ≤20cm, bện xoắn; nếu được thì xoắn từng sợi tín hiệu (WS, SCK, SD) với một sợi GND.
- **LCD:** cáp SPI ≤20cm. Ảnh nhiễu hoặc sai màu: thêm R11 33Ω nối tiếp trên SCK (sát J7, vị trí `Z:2`–`Z:3`) và giảm tốc độ SPI trong firmware.
- **Tách nhóm:** dây I2S/SPI không đi song song sát dây servo và dây đèn mắt; nếu phải cắt ngang thì cắt vuông góc.
- **Đầu cắm tháo được:** JST-PH (2.0mm) hoặc header 2.54 cho mic, LCD, nút; servo dùng header 3P đực khớp giắc servo.
- **Trên PCB:** dây tín hiệu dùng dây có vỏ mảnh (AWG26–30, ví dụ dây wire-wrap) đi mặt dưới; không dùng dây trần cho tín hiệu.

### 3.9 Nghiệm thu HW2 §5

| # | Tiêu chí | Cách thử | Đạt khi |
|---|---|---|---|
| 1 | Khởi động, LCD | Breadboard (P1, cách cắm ở [breadboard.md](breadboard.md)): nạp firmware `jarvis-helmet` (hoặc firmware gốc `bread-compact-wifi-lcd` đã đổi `DISPLAY_RST_PIN` = 39 và chọn `ST7789 240*320`, màn sẽ hiện dọc); quan sát màn hình | Khởi động bình thường; LCD đúng màu, đúng hướng, không lệch ảnh |
| 2 | Mic | Phòng yên tĩnh, nói "Jarvis" cách mic 30cm | Đánh thức được |
| 3 | Loa | Phát câu trả lời ở âm lượng 70% | Rõ, không rè (rè thì đổi cách nối GAIN, mục 3.4) |
| 4 | Bảng chân | Đối chiếu bảng 3.1 với danh sách chân không được dùng | Không chân nào trùng strapping (3, 45, 46), flash (26–32) hoặc PSRAM (33–37). GPIO0 là nút BOOT có sẵn của devkit, dùng đúng chức năng |

## 4. Mạch công suất: servo, đèn mắt, output dự phòng (HW3)

### 4.1 Servo MG90S ×2 (M1 = S0 trái, M2 = S1 phải)

```
5V_SERVO (Q:31 → C1+ X:30) ──────────────── J3.2 / J4.2 ── dây đỏ   (M1, M2)
GND_SERVO (Q:33 → C1− Z:30) ─────────────── J3.1 / J4.1 ── dây nâu
GPIO17 (I:12) ──[R7 220Ω  V:23→W:23]─────── J3.3 ───────── dây cam M1 (S0, trái)
GPIO18 (I:13) ──[R8 220Ω  V:26→W:26]─────── J4.3 ───────── dây cam M2 (S1, phải, lắp đối xứng → đảo chiều bằng hiệu chỉnh)
                    (tùy chọn R9/R10 10kΩ từ J3.3/J4.3 xuống GND_SERVO)
```

| Đầu nối | Chân 1 | Chân 2 | Chân 3 | Lỗ PCB (dự kiến) |
|---|---|---|---|---|
| J3 (S0) | GND – dây nâu | +5V – dây đỏ | Tín hiệu – dây cam | `Z:23` / `Y:23` / `X:23` |
| J4 (S1) | GND – dây nâu | +5V – dây đỏ | Tín hiệu – dây cam | `Z:26` / `Y:26` / `X:26` |

- Đầu nối: header đực 3 chân 2.54mm (khớp giắc servo) hoặc JST-XH 3P. **Đánh dấu phía dây nâu (GND) bằng bút trên PCB** (chân 1 = `Z:23`/`Z:26`, ô đỏ trong `perfboard-7x9.svg`). Chân +5V nằm giữa nên cắm ngược không đảo cực nguồn, nhưng khi đó dây GND của servo rơi vào chân tín hiệu: dòng servo chạy qua R7/R8 vào GPIO (220Ω giới hạn còn ~20mA), servo không chạy. Thấy vậy thì tắt nguồn, cắm lại đúng chiều.
- MG90S nhận được tín hiệu 3.3V, không cần chuyển mức. Dải xung khoảng 500–2400µs; góc đóng/mở thật lấy từ bước hiệu chỉnh FW2 (lưu NVS `s0_closed`, `s0_open`, `s1_closed`, `s1_open`). Servo phải có `closed > open` nên tự đảo chiều.
- C1 1000µF đặt ngay chỗ cắm servo (`X:30`/`Z:30`), C2 100nF (`Y:24`–`Z:24`) giữa J3 và J4.
- Servo giật lúc ESP32 khởi động (GPIO đang thả nổi): lắp R9 (`W:24`–`W:25`) và R10 (`W:27`–`W:28`) 10kΩ, đầu còn lại nối GND_SERVO bằng dây có vỏ.
- Dây servo đi xa bản lề và cơ cấu chuyển động; chừa dây dư ở chỗ mặt nạ chuyển động.

### 4.2 Đèn mắt – module LED cosplay chạy CR2032 (E1)

Module mua về là **một** hộp pin CR2032 3V nuôi cả 2 LED (mắt trái, mắt phải) nối song song. Pin cúc áo có điện trở trong lớn nên tự hạn dòng cho LED; chuyển sang nguồn 5V thì **bắt buộc thêm một điện trở hạn dòng R_EYE**, nối tiếp với cả module, thay cho điện trở trong của pin.

```
5V_EYES (O:31) ── J6.1 (AA:9) ──[R_EYE]──► lá (+) hộp pin E1 ─┬─ LED mắt trái ──┬─ lá (−) hộp pin E1 ── J6.2 (AA:10) ── Drain Q1 (Z:10)
                                                              └─ LED mắt phải ──┘
                                                                                                                        Source Q1 (Y:10) ── GND_PWR (O:33)
GPIO8 (I:14) ──[R3 100Ω  V:10→W:10]──► Gate Q1 (X:10) ──[R5 100kΩ  X:11→Y:11]──► Source Q1
                                       (kéo xuống: đèn tắt trong lúc khởi động/reset/nạp code)
```

- Hai mắt chung một kênh PWM, một R_EYE, một cặp dây: J6 vẫn 2 chân (+5V_EYES, EYES_DRV), PCB không đổi so với Rev B. Hai LED vẫn song song như lúc chạy pin nên độ sáng hai mắt giống hệt khi dùng pin.
- **Tháo hẳn pin CR2032**, hàn 2 dây vào hai lá tiếp điện của hộp pin: dây qua R_EYE vào lá **+**, dây về J6.2 vào lá **−** (đo bằng đồng hồ khi còn pin nếu không rõ lá nào). **Không lắp lại pin** khi đã nối 5V: pin lithium bị nạp, có thể phồng hoặc cháy.
- R_EYE hàn **trên dây**, sát hộp pin của module, bọc co nhiệt.
- Module có công tắc: để luôn ON hoặc nối tắt. Module có nút đổi chế độ (nháy, thở…) là có IC bên trong: chọn chế độ sáng đứng. PWM của Q1 bật/tắt nguồn IC 25.000 lần/giây nên IC có thể chạy sai; khi đó hàn dây thẳng vào hai đầu cụm LED (bỏ qua IC), hoặc chỉ dùng độ sáng 0/100%.
- Q1 = AO3400A trên đế chuyển SOT-23 → 3 chân 2.54mm. AO3400A: chân 1 = G, 2 = S, 3 = D; **kiểm tra đế chuyển đưa chân nào ra lỗ nào** trước khi hàn. Logic-level, Rds(on) <50mΩ ở Vgs 2.5V. Dòng mỗi kênh <150mA có thể thay bằng 2N7000 (TO-92, thứ tự chân khác: xem datasheet).

#### Quy trình đo để chọn R_EYE

1. Tháo pin CR2032, xác định lá + và − của hộp pin. Kiểm tra trên module đã có sẵn điện trở hay chưa (ghi vào bảng).
2. Cấp **3.0V từ nguồn có giới hạn dòng 100mA** vào hai lá của hộp pin (cả 2 LED cùng sáng), đồng hồ đo dòng mắc nối tiếp. Bắt đầu từ ~2.5V rồi tăng dần tới 3.0V. Không có nguồn chỉnh được thì dùng pin CR2032 mới và đo dòng.
3. Ghi dòng **tổng của cả 2 LED** khi độ sáng vừa ý (**I_target**) và điện áp **V_f** đo ngay hai lá hộp pin tại dòng đó. LED 3–5mm thường không quá ~20mA mỗi con, tức I_target ≤ 40mA.
4. Tính:
   - R_EYE = (5.0 − V_f) / I_target, chọn giá trị chuẩn E24 gần nhất (lớn hơn một chút thì an toàn hơn cho LED);
   - công suất P = I_target² × R_EYE; chọn điện trở có công suất **≥ 2P**;
   - kiểm tra lại dòng thật: I = (5V_EYES đo được − V_f) / R chọn.
5. Ghi kết quả vào bảng dưới và vào mục 6.4.

| Module | Điện trở có sẵn trên module? | V_f (V) | I_target cả 2 LED (mA) | R tính (Ω) | R chọn (E24) | P = I²R (mW) | Công suất R chọn | I thật ở 5V (mA) |
|---|---|---|---|---|---|---|---|---|
| Ví dụ (HW3) | không | 2.9 | 30 | 70 | **75Ω** | 68 | 1/4W (≥136mW) | 28 |
| E1 (2 LED song song) | Đo thực tế: ___ | ___ | ___ | ___ | ___ | ___ | ___ | ___ |

PWM 25 kHz chỉ thay đổi dòng **trung bình**; dòng đỉnh luôn bằng I thật ở bảng trên, nên R_EYE phải tính cho 100% độ sáng.

### 4.3 Output dự phòng (J5)

```
5V_MAIN (P:31) ──┬──► J5.1 (+, AA:15) ─── tải ─── J5.2 (−, AA:17) ──┬──► Drain Q2 (Y:20)     Source Q2 (X:20) ──► GND_PWR (O:33)
                 │                                                  │
                 └──── catot D2 1N5819 (X:13) ◄──|── anot (X:16) ────┘   (diode chống ngược cho tải cảm: relay, quạt, motor)
GPIO9 (I:17) ──[R4 100Ω  V:19→V:20]──► Gate Q2 (W:20) ──[R6 100kΩ  W:21→X:21]──► Source Q2
```

- Dòng tải tối đa **1A**, bị giới hạn bởi ngân sách dòng (mục 2.6) chứ không phải bởi MOSFET.
- D2: vạch catot về phía +5V (J5.1). Lắp ngược D2 = ngắn mạch 5V qua Q2 khi Q2 dẫn.
- Firmware hỗ trợ cả bật/tắt (`self.output.set`) và PWM (LEDC timer 2, channel 4).
- Domino J5 đặt sát mép phải, lỗ bắt dây quay ra ngoài; thân domino che các lỗ cột `Z` từ hàng 14 tới 18, chỉ đi dây mặt dưới ở đó.

### 4.4 Vì sao có điện trở gate 100Ω và điện trở kéo xuống 100kΩ

| Linh kiện | Tác dụng | Kiểm tra nhanh |
|---|---|---|
| R3, R4 100Ω nối tiếp gate | Hạn xung dòng nạp tụ gate từ chân GPIO, dập dao động trên dây gate | Thời gian chuyển mạch ≈ 100Ω × C_gate (cỡ vài trăm pF–1nF) ≈ 0.1µs, rất nhỏ so với chu kỳ PWM 40µs (25kHz) → tổn hao chuyển mạch <1%, không đáng kể |
| R5, R6 100kΩ gate → source | Giữ gate ở 0V khi GPIO thả nổi (lúc reset, khởi động, nạp code, hoặc khi rút devkit) → MOSFET tắt, đèn/tải tắt | Đặt ngay tại MOSFET (giữa G và S), không đặt ở phía devkit. Chia áp 100Ω/100kΩ làm gate mất 0.1% điện áp, bỏ qua được; dòng rò 33µA khi bật |
| AO3400A logic-level | Dẫn bão hòa ở Vgs = 3.3V | Ở 1A: P ≈ 1² × 0.05Ω ≈ 50mW → MOSFET gần như không nóng |

### 4.5 Nghiệm thu HW3 §6

| # | Tiêu chí | Cách thử | Đạt khi |
|---|---|---|---|
| 1 | Đèn mắt tắt khi khởi động/reset/nạp code | Bật nguồn, nhấn RST, nạp firmware trong lúc quan sát đèn | Đèn **tắt hoàn toàn** suốt các quá trình |
| 2 | PWM mượt | `self.eyes.set` 0 → 100% từng nấc 10%; quay điện thoại chế độ chậm | Độ sáng đổi đều, không nháy thấy được, không nghe tiếng rít (25 kHz) |
| 3 | Q2 chịu tải | Tải giả 10Ω/5W (0.5A, trở nóng ~2.5W: đặt trên bề mặt chịu nhiệt) hoặc quạt 5V, bật liên tục **10 phút**; đo nhiệt Q2 ở phút 0/5/10 | Q2 **nguội hơn 50°C** |
| 4 | Servo ổn định | Để mặt nạ đứng yên 5 phút; khởi động lại ESP32 nhiều lần | Không rung khi đứng yên, không giật khi khởi động |

## 5. Lắp ráp PCB đục lỗ 7×9cm & đi dây trong mũ (HW4)

Hình bố trí: [perfboard-7x9.svg](perfboard-7x9.svg) (nhìn từ mặt linh kiện).

### 5.1 Quy ước lưới

| Hạng mục | Quy ước |
|---|---|
| Hướng tấm | Cạnh 9cm theo chiều **dọc**; nhìn từ **mặt linh kiện** (top view) |
| Cột | `A`…`Z`, `AA` = **27 cột**, trái → phải |
| Hàng | `1`…`35` = **35 hàng**, trên → dưới. Hàng 1 = cạnh trên (header cáp tín hiệu), hàng 35 = cạnh dưới (khối nguồn) |
| Bước lỗ | 2.54mm; tâm lỗ đầu → tâm lỗ cuối: 26 × 2.54 = 66.04mm (ngang), 34 × 2.54 = 86.36mm (dọc) |
| Vùng | Trái `A`–`H`: âm thanh. Giữa `I`–`S`: devkit (cột `S` để trống dự phòng). Phải `T`–`AA`: VSENSE, MOSFET, servo. Dưới hàng 27–35: khối nguồn |
| Ký hiệu | `I:23` = cột I, hàng 23. Linh kiện 2 chân ghi `F:23`–`F:24`; "(+)", "(K)" = cực dương, catot |

> **Đếm lỗ tấm thật trước khi làm theo bảng.** 27×35 là số lỗ tối đa vừa khổ 70×90mm (HW4 ghi "khoảng 27×35"). Nhiều tấm 7×9cm bán sẵn có lề rộng và chỉ có **24×30 lỗ**; khi đó bố trí dưới đây không vừa, phải co lại (bỏ cột dự phòng `S`, dồn khối nguồn) và vẽ lại hình. Đo thực tế (số cột × số hàng): ___

#### Đo devkit để chốt cột (dự kiến → chính thức)

1. Đặt devkit lên tấm PCB (chưa hàn), cho hàng chân trái lọt vào cột `I`, chân 1 (3V3, phía anten) ở hàng 3.
2. Đếm số bước từ cột `I` tới hàng chân phải: **9 bước (22.86mm) → cột `R`** (như bảng); **10 bước (25.4mm) → cột `S`** (mọi tọa độ `R:x` của devkit dời sang `S:x`, cầu hàn `R:3–S:3`, `R:6–S:6` rút ngắn còn 1 bước; vùng phải giữ nguyên nhờ cột `S` đã để trống).
3. Hoặc đo bằng thước kẹp: (khoảng cách mép ngoài hai hàng chân) − 0.64mm = khoảng cách tâm.
4. Đo thêm khoảng nhô của devkit: từ chân 1 tới mép đầu anten, từ chân 22 tới mép cổng USB. Phần anten nằm trên hàng 1–2 (vùng cấm), cổng USB nằm khoảng hàng 25–26 và cắm cáp từ phía dưới.

| Kích thước | Đo thực tế |
|---|---|
| Khoảng cách tâm 2 hàng chân devkit | ___ mm (→ cột R hay S: ___) |
| Chân 1 → mép đầu anten | ___ mm |
| Chân 22 → mép đầu USB | ___ mm |
| Module MAX98357: rộng × dài, khoảng từ hàng chân tới mép xa | ___ |

### 5.2 Bảng bố trí

Cột "Dự kiến" = tọa độ còn phụ thuộc kích thước thật (devkit, module, đế SOT-23). Điện trở và tụ gốm cạnh MOSFET/VSENSE lắp **đứng** (bước 1–2 lỗ) cho gọn; tụ hóa đứng; diode nằm.

**Cạnh trên (hàng 1–3) – header cáp tín hiệu**

| Ký hiệu | Linh kiện | Tọa độ | Dự kiến | Ghi chú |
|---|---|---|---|---|
| J8 | Header đực 5P 2.54 (hoặc JST-PH 5P) – mic | `A:1` 3V3 · `B:1` GND · `C:1` WS · `D:1` SCK · `E:1` SD | | |
| J9 | Header 3P – nút | `F:1` PTT · `G:1` MASK · `H:1` GND | | `H:1` sát mép devkit: kiểm tra vỏ giắc không chạm devkit |
| C8 | 100nF (PTT) | `F:2`–`F:3` | | `F:2` nối `F:1` |
| C9 | 100nF (mặt nạ) | `G:2`–`G:3` | | `G:2` nối `G:1` |
| – | Cụm GND nút | `F:3`–`G:3`–`H:3`–`H:2`–`H:1` | | Dây có vỏ `H:3` → `R:3` (`DK-R1`) |
| J7 | Header 8P – LCD | `T:1` VCC (3V3, chân 1) · `U:1` GND · `V:1` CS · `W:1` RESET · `X:1` DC · `Y:1` SDI · `Z:1` SCK · `AA:1` LED | | Trùng chân 1–8 của module, cáp 1:1 (mục 3.5) |
| R11 | 33Ω (tùy chọn) | `Z:2`–`Z:3` | | Mặc định không lắp: dây GPIO21 vào thẳng `Z:1`. Khi lắp: dây GPIO21 vào `Z:3`, cầu `Z:1`–`Z:2` |
| – | **Vùng cấm dưới anten** | `I:1`–`S:2` | ✔ | Không linh kiện, không dây |

**Giữa – devkit**

| Ký hiệu | Linh kiện | Tọa độ | Dự kiến | Ghi chú |
|---|---|---|---|---|
| U1 (trái) | Hàng rào cái 1×22 | `I:3`–`I:24` = `DK-L1`…`DK-L22` | | Chân 1 (3V3) ở hàng 3 |
| U1 (phải) | Hàng rào cái 1×22 | `R:3`–`R:24` = `DK-R1`…`DK-R22` | ✔ | 10 bước → `S:3`–`S:24` |
| – | Cột dự phòng | `S:3`–`S:24` để trống | ✔ | Chỉ dùng `S:3`, `S:6` làm cầu nối |
| – | Thân devkit | khoảng giữa cột H/I tới giữa cột R/S, hàng ~1–26 | ✔ | Anten hướng lên hàng 1, cổng USB hướng xuống khối nguồn. Dưới thân devkit chỉ đi dây mặt dưới |

**Vùng trái – âm thanh**

| Ký hiệu | Linh kiện | Tọa độ | Dự kiến | Ghi chú |
|---|---|---|---|---|
| C3 | 470µF/10V | `C:4` (+) · `C:5` (−) | | Ngoài thân U2 (tụ cao ~11mm) |
| U2 | MAX98357A trên hàng rào cái 1×7 | `G:7` VIN · `G:8` GND · `G:9` SD · `G:10` GAIN · `G:11` DIN · `G:12` BCLK · `G:13` LRC | ✔ | Thứ tự chân kiểu Adafruit; thân module chìa sang trái tới khoảng cột A, hàng ~6–14. Cột `H` để trống (khe giữa U2 và devkit) |
| C4 | 100nF | `F:7`–`F:8` | | Nằm dưới thân U2 (linh kiện thấp), cầu sang `G:7`/`G:8` |
| J10 | Header 2P / JST-PH 2P – loa | `A:15` OUT+ · `A:16` OUT− | | 2 dây ngắn từ cọc loa của U2 |
| R13 | 2.2kΩ – chân SD amply | `F:16`–`F:17` | | Đứng, ngoài thân U2. `F:17` nhận dây GPIO12 (`I:20`); `F:16` → dây có vỏ `F:16`→`F:9`→`G:9` (SD) |
| – | Cầu GAIN → VIN | `G:10`–`E:10`–`E:7` | | Dây trần ngắn mặt dưới, dưới thân U2; bắt chéo dây SD (có vỏ) ở `F:10` |
| D1 | 1N5819 (hoặc SS34) | `D:23` (K) – `D:27` (A), nằm dọc | | Vạch catot ở hàng 23. Devkit đã có diode ở chân 5V: thay bằng dây (mục 2.3) |
| C5 | 470µF/10V, Ø6.3mm | `F:23` (+) · `F:24` (−) | | Loại Ø8mm chạm D1 |
| C6 | 100nF | `H:23`–`H:24` | | Cầu sang `I:23`/`I:24` |
| – | Cầu 5V_ESP | `D:23`–`E:23`–`F:23`–`G:23`–`H:23`–`I:23` | | Hàng 23, mặt dưới |
| – | Cầu GND_ESP cục bộ | `F:24`–`G:24`–`H:24`–`I:24` | | Hàng 24, mặt dưới |

**Vùng phải – VSENSE, MOSFET, servo**

| Ký hiệu | Linh kiện | Tọa độ | Dự kiến | Ghi chú |
|---|---|---|---|---|
| – | Cầu GND (LCD + VSENSE) | `U:1`–`U:2`–`U:3`–`U:4`–`T:4`–`T:3`, `T:3`–`S:3`–`R:3` | ✔ (`R:3`) | `R:3` = `DK-R1` GND. `T:1` là 3V3 (J7.1), `T:2` để trống: không nối vào cụm này |
| R2 | 100kΩ 1% | `T:4` (GND) – `T:6` (VSENSE) | | Đứng, bước 2 lỗ |
| C7 | 100nF | `U:4` (GND) – `U:6` (VSENSE) | | Bước 5.08 |
| – | Nút VSENSE | `R:6`–`S:6`–`T:6`–`U:6`–`V:6` | ✔ (`R:6`) | `R:6` = GPIO1 |
| R1 | 100kΩ 1% | `V:6` (VSENSE) – `V:8` (VSENSE_TOP) | | Đứng; `V:8` nhận dây mảnh từ `N:31` |
| R3 | 100Ω | `V:10` (← GPIO8) – `W:10` | | Cầu `W:10`–`X:10` |
| Q1 | AO3400A + đế SOT-23 | `X:10` G · `Y:10` S · `Z:10` D | ✔ | Kiểm tra thứ tự chân của đế; thân đế quay lên trên |
| R5 | 100kΩ | `X:11` (G) – `Y:11` (S) | | Cầu `X:10`–`X:11` (gate) và `Y:10`–`Y:11` (source). Thiếu hai cầu này thì R5 không kéo gate xuống |
| J6 | Header 2P / JST – đèn mắt | `AA:9` (1: 5V_EYES) · `AA:10` (2: EYES_DRV) | | Cầu `Z:10`–`AA:10` |
| D2 | 1N5819 | `X:13` (K) – `X:16` (A), nằm dọc | | Cầu K: `X:13`→`AA:13`→`AA:15`; A: `X:16`→`Y:16`→`Y:20` |
| J5 | Domino 2P 5.08 – output dự phòng | `AA:15` (+) · `AA:17` (−) | ✔ | Lỗ bắt dây quay ra mép phải; thân che `Z:14`–`Z:18` |
| R4 | 100Ω | `V:19` (← GPIO9) – `V:20` | | Đứng dọc; cầu `V:20`–`W:20` |
| Q2 | AO3400A + đế SOT-23 | `W:20` G · `X:20` S · `Y:20` D | ✔ | Cầu drain `Y:20`→`Z:20`→`AA:20`→`AA:17` |
| R6 | 100kΩ | `W:21` (G) – `X:21` (S) | | Cầu `W:20`–`W:21` (gate); `X:20`–`X:21` (source) nằm trên dây GND_PWR |
| R7 | 220Ω | `V:23` (← GPIO17) – `W:23` | | Cầu `W:23`–`X:23` |
| J3 | Header 3P – servo S0 | `X:23` tín hiệu · `Y:23` +5V · `Z:23` GND | | Chân 1 = `Z:23` (GND, dây nâu): đánh dấu bằng bút |
| C2 | 100nF | `Y:24`–`Z:24` | | |
| R9 | 10kΩ (tùy chọn) | `W:24`–`W:25` | | `W:24` nối `W:23`; `W:25` → GND_SERVO |
| R8 | 220Ω | `V:26` (← GPIO18) – `W:26` | | Cầu `W:26`–`X:26` |
| J4 | Header 3P – servo S1 | `X:26` tín hiệu · `Y:26` +5V · `Z:26` GND | | Chân 1 = `Z:26` (GND, dây nâu) |
| R10 | 10kΩ (tùy chọn) | `W:27`–`W:28` | | `W:27` nối `W:26`; `W:28` → GND_SERVO |
| C1 | 1000µF/10V (Ø10mm) | `X:30` (+) · `Z:30` (−) | | Thân chiếm khoảng `W`–`Z`, hàng 28–32 |

**Cạnh dưới (hàng 27–35) – khối nguồn**

| Ký hiệu | Linh kiện | Tọa độ | Dự kiến | Ghi chú |
|---|---|---|---|---|
| J1 | Domino 2P 5.08 – nguồn vào | `B:34` (+) · `D:34` (−) | | Lỗ bắt dây quay ra mép dưới |
| F1 | PPTC 2.5A (hold) | `B:30` (vào) · `D:30` (ra) | | Bước chân 5.08 |
| Q3 | AO3401A + đế SOT-23 (chống cắm ngược) | `D:32` G · `E:32` S · `F:32` D | ✔ | Thân đế quay lên hàng 31, dưới F1. Cầu gate `D:32`→`D:34` (J1−) mặt dưới (mục 2.9) |
| J2 | Domino 2P 5.08 (hoặc JST-XH 2P ≥3A) – tới SW1 | `F:34` (1: tới SW1) · `H:34` (2: từ SW1) | | |
| – | Thanh 5V_MAIN | `K:31`–`Q:31` | | Dây đồng trần 0.8mm. Lỗ ra nhánh: `K` vào từ J2 · `L` 5V_AUDIO · `M` 5V_ESP · `N` VSENSE_TOP · `O` 5V_EYES · `P` → J5 · `Q` 5V_SERVO |
| – | Thanh GND_STAR | `K:33`–`Q:33` | | `K` từ J1− · `L` GND_AUDIO · `N` GND_ESP · `O` GND_PWR · `Q` GND_SERVO · `M`, `P` dự phòng |
| ★ | Đánh dấu điểm sao | `N:32` | | Viết "★" bằng bút |

Khác hình phác ở HW4 (giữ nguyên nguyên tắc vùng: âm thanh trái, servo/MOSFET phải, nguồn dưới, header trên):
- J7 (LCD) lên góc trên **phải**, VSENSE sang **phải**: các chân LCD và GPIO1 nằm ở hàng chân phải của DevKitC-1, dây ngắn hơn.
- J2 đặt cạnh F1 ở bên trái: dòng 5V đi một chiều J1 → F1 → SW1 → điểm sao, không vòng qua lại.
- J3/J4 + C1 nằm thấp nhất, sát khối nguồn: đường 5V_SERVO/GND_SERVO ngắn nhất. J6, J5 nằm cạnh MOSFET của chính nó.
- J8 là 5 chân (HW4 ghi 4P): mic cần VDD, GND, WS, SCK, SD; L/R nối GND ngay trên module mic.

### 5.3 Dây mặt dưới

**Dây nguồn** (vẽ nét đứt trong `perfboard-7x9.svg`). Thanh sao, dây vào và cặp servo dùng dây đồng trần 0.8mm hoặc 22AWG; các nhánh còn lại dùng dây silicone 22AWG có vỏ (được bắt chéo nhau).

| Dây | Đường đi |
|---|---|
| 5V_IN | `B:34` → `B:30` |
| 5V_FUSED | `D:30` → `F:30` → `F:32` (Q3 D) |
| 5V_PROT | `E:32` (Q3 S) → `E:33` → `F:33` → `F:34` (J2.1) |
| Gate Q3 | `D:32` → `D:33` → `D:34` (J1−), dây trần ngắn |
| 5V_MAIN (từ SW1) | `H:34` → `H:31` → `K:31` |
| GND vào | `D:34` → `D:35` → `K:35` → `K:33` |
| 5V_SERVO | `Q:31` → `X:31` → `X:29` → `Y:29` → `Y:23` (qua C1+ `X:30`, J4.2 `Y:26`, C2 `Y:24`, J3.2 `Y:23`) |
| GND_SERVO | `Q:33` → `Z:33` → `Z:23` (qua C1− `Z:30`, J4.1 `Z:26`, C2 `Z:24`, J3.1 `Z:23`) |
| 5V → D1 (nhánh ESP; mạng 5V_ESP bắt đầu sau D1) | `M:31` → `M:27` → `D:27` (D1 anot) |
| 5V_AUDIO | `L:31` → `L:28` → `B:28` → `B:4` → `C:4` (C3+) → `E:4` → `E:7` → `G:7` (qua C4 `F:7`) |
| 5V_EYES | `O:31` → `O:28` → `T:28` → `T:9` → `AA:9` |
| 5V_MAIN → J5 | `P:31` → `P:30` → `U:30` → `U:13` → `X:13` (D2 K) → `AA:13` → `AA:15` |
| GND_PWR | `O:33` → `O:34` → `AA:34` → `AA:22` → `X:22` → `X:21` → `X:20` (Q2 S). Q1 S (`Y:10`): dây riêng về `O:33` |
| GND_AUDIO | `L:33` → `L:32` → `J:32` → `J:29` → `C:29` → `C:5` (C3−) → `D:5` → `D:8` → `G:8` (U2 GND, qua C4 `F:8`) |
| GND_ESP | `N:33` → `N:25` → `I:25` → `I:24` (`DK-L22`) |
| VSENSE_TOP | `N:31` → `V:8`, dây mảnh (hình chỉ ghi nhãn "← N:31" ở `V:8`, không vẽ đường) |

> **Chỗ dây GND có vỏ bắt chéo dây trần 5V (mặt dưới):** GND_ESP (`N:33` → `N:25`) đi qua thanh 5V_MAIN tại `N:31`, lỗ này còn có mối hàn dây VSENSE_TOP; GND_AUDIO (`J:32` → `J:29`) đi qua dây 5V_MAIN từ SW1 tại `J:31`. Dùng dây silicone, luồn thêm một đoạn ống co nhiệt ở chỗ giao, ép dây sát mặt PCB, không hàn ở chỗ giao. Hàn xong đo lại 5V_MAIN–GND >1kΩ (mục 5.5 #15).

**Dây tín hiệu** (dây có vỏ AWG26–30, không vẽ trong hình, đi theo bảng):

| Từ (devkit) | Tới | | Từ (devkit) | Tới |
|---|---|---|---|---|
| `I:3` 3V3 | `A:1` (J8.1), `T:1` (J7.1) | | `I:13` GPIO18 | `V:26` (R8) |
| `I:6` GPIO4 | `C:1` (J8.3) | | `I:14` GPIO8 | `V:10` (R3) |
| `I:7` GPIO5 | `D:1` (J8.4) | | `I:17` GPIO9 | `V:19` (R4) |
| `I:8` GPIO6 | `E:1` (J8.5) | | `I:18` GPIO10 | `F:2` (J9.1/C8) |
| `I:9` GPIO7 | `G:11` (U2 DIN) | | `I:19` GPIO11 | `G:2` (J9.2/C9) |
| `I:10` GPIO15 | `G:12` (U2 BCLK) | | `R:8` GPIO42 | `AA:1` (J7.8) |
| `I:11` GPIO16 | `G:13` (U2 LRC) | | `R:9` GPIO41 | `V:1` (J7.3) |
| `I:12` GPIO17 | `V:23` (R7) | | `R:10` GPIO40 | `X:1` (J7.5) |
| `B:1` (J8.2 GND) | `H:3` (cụm GND) | | `R:11` GPIO39 | `W:1` (J7.4) |
| `H:3` (cụm GND) | `R:3` (`DK-R1`) | | `R:19` GPIO47 | `Y:1` (J7.6) |
| `R:6` GPIO1 | `T:6` (cầu qua `S:6`) | | `R:20` GPIO21 | `Z:1` (J7.7) hoặc `Z:3` nếu có R11 |
| `I:20` GPIO12 | `F:17` (R13); `F:16` → `F:9` → `G:9` (SD) | | | |

Không đi dây qua vùng cấm dưới anten (hàng 1–2, cột `I`–`S`): dây 3V3 `I:3` → `T:1` và các dây LCD từ cột `R` đi dưới thân devkit (hàng ≥3), sang vùng cột `T`–`AA` rồi mới lên hàng 1.

### 5.4 Thứ tự hàn và thử

Sau mỗi bước phải đạt phép thử rồi mới làm bước sau. Ghi kết quả vào mục 6.

| Bước | Hàn | Thử ngay sau bước |
|---|---|---|
| 0 | Chuẩn bị: đếm lỗ tấm, đo devkit và U2 (mục 5.1), sửa bảng nếu khác; đánh dấu cột/hàng bằng bút kim mỗi 5 lỗ; kiểm tra MOSFET đã hàn lên đế (thang diode: S→D thấy diode thân ≈0.5–0.7V) | Bảng tọa độ đã chốt |
| 1 | **Khối nguồn:** thanh 5V_MAIN và GND_STAR, J1, F1, Q3 + cầu gate, J2 (nối tắt tạm J2.1–J2.2 nếu chưa có SW1), D1 (hoặc dây, mục 2.3), C5, C6 + cầu hàng 23/24, các nhánh nguồn kèm tụ đầu nhánh: 5V_SERVO/GND_SERVO + C1 + C2, 5V_AUDIO/GND_AUDIO + C3 + C4, 5V_EYES, 5V_MAIN → J5, VSENSE_TOP | Chưa cắm gì: Ω giữa 5V_MAIN–GND và 5V_ESP–GND >1kΩ. Cấp sạc: 5V_MAIN 4.9–5.25V, 5V_ESP (`H:23`) ≥4.5V, đầu mỗi nhánh bằng 5V_MAIN (mục 2.8 bước 1–3) |
| 2 | **Hàng rào cái** cho devkit (`I:3`–`I:24`, `R:3`–`R:24`) và U2 (`G:7`–`G:13`); cầu GND `R:3`–`T:3`, cụm GND `U:1`–`U:4`–`T:4`–`T:3`; nút VSENSE + R1, R2, C7; dây I2S loa, R13 + dây GPIO12/SD, cầu GAIN | Thông mạch từng chân theo bảng 3.1 (đầu kim vào lỗ hàng rào). `I:23` = 5V_ESP, `I:24` = GND, `I:3` không thông với 5V và GND. Đo R giữa `R:6` và `R:3` ≈ 100kΩ (R2; số đo trôi nhẹ lúc đầu do C7 nạp). Chưa cắm devkit thì cụm GND `R:3` chưa nối về thanh sao (mục 5.5 #2b) |
| 3 | **Khối công suất:** Q1, Q2, R3–R8, (R9, R10), D2, J3–J6, cầu drain/source, GND_PWR, dây gate từ `I:14`, `I:17`, dây servo từ `I:12`, `I:13` | Gate–source Q1, Q2 ≈ 100kΩ; D2 thuận từ `X:16` sang `X:13`, ngược thì hở; J3/J4: chân 2 = 5V_SERVO, chân 1 = GND_SERVO; GPIO17/18 → chân 3 qua 220Ω; không chập 5V–GND |
| 4 | **Header cáp:** J7, J8, J9 + C8, C9 + cụm GND nút, J10, (R11); dây tín hiệu LCD, mic, nút; dây loa từ U2 xuống J10 | Thông mạch từng chân J7/J8/J9/J10 tới GPIO/mạng theo bảng 3.3–3.6; các chân kề nhau trên J7 không thông nhau |
| 5 | (Không hàn) | Chưa cắm module: Ω giữa 5V_MAIN–GND, 5V_ESP–GND, 3V3–GND đều **>1kΩ** |
| 6 | Cắm devkit, nạp firmware (mục 2.3: tắt SW1 khi cắm USB máy tính nếu devkit không có diode VBUS); sau đó cắm U2, LCD, mic, nút, servo, đèn mắt | Vừa cắm devkit, chưa cấp điện: `R:3` ↔ `K:33` thông (cụm GND tín hiệu về sao qua devkit, mục 5.5 #2b). Sau đó chạy lại thử của HW1 (mục 2.8 bước 4–6), HW2 (mục 3.9: LCD đúng màu/hướng, "Jarvis" ở 30cm, loa 70% không rè), HW3 (mục 4.5) |

### 5.5 Danh sách đo thông mạch (nghiệm thu HW4: đúng 100%, không chập)

Đo khi **chưa cắm module**. ☐ = đánh dấu khi đạt.

| # | Mạng / cặp điểm | Kỳ vọng | ☐ |
|---|---|---|---|
| 1 | 5V_MAIN: `H:34` ↔ `K:31`…`Q:31` ↔ `X:30` ↔ `Y:23` ↔ `Y:26` ↔ `G:7` ↔ `C:4` ↔ `AA:9` ↔ `AA:15` ↔ `X:13` ↔ `V:8` ↔ `D:27` | Thông (≈0Ω) | |
| 2 | GND: `D:34` ↔ `K:33`…`Q:33` ↔ `Z:30` ↔ `Z:23` ↔ `Z:26` ↔ `G:8` ↔ `C:5` ↔ `X:20` ↔ `Y:10` ↔ `I:24` | Thông | |
| 2b | Cụm GND tín hiệu: `R:3` ↔ `T:3` ↔ `T:4` ↔ `U:4` ↔ `U:1` ↔ `H:3` ↔ `H:1` ↔ `B:1` ↔ `F:3` ↔ `G:3` | Thông với nhau. Chưa cắm devkit thì cụm này **hở** với các điểm ở #2 (nó về điểm sao qua GND bên trong devkit, `DK-R1` ↔ `DK-L22`); cắm devkit rồi thì `R:3` ↔ `K:33` thông | |
| 3 | 5V_ESP: `D:23` ↔ `F:23` ↔ `H:23` ↔ `I:23` | Thông | |
| 4 | D1: 5V_MAIN (`D:27`) → 5V_ESP (`D:23`) | Thang diode: thuận 0.2–0.45V, ngược hở | |
| 5 | 3V3: `I:3` ↔ `A:1` ↔ `T:1` | Thông; `T:1` **không** thông cụm GND (`U:1`, `T:3`). `I:4` (`DK-L2`) chỉ nối với `I:3` bên trong devkit nên chưa cắm devkit thì hở; không cần cầu `I:3`–`I:4` (dễ chạm sang `I:5` RST) | |
| 6 | I2S mic: `I:6`↔`C:1`, `I:7`↔`D:1`, `I:8`↔`E:1` | Thông | |
| 7 | I2S loa: `I:9`↔`G:11`, `I:10`↔`G:12`, `I:11`↔`G:13` | Thông | |
| 8 | LCD: `R:9`↔`V:1`, `R:11`↔`W:1`, `R:10`↔`X:1`, `R:19`↔`Y:1`, `R:20`↔`Z:1`, `R:8`↔`AA:1` | Thông (SCK ≈33Ω nếu có R11) | |
| 9 | Servo: `I:12`↔`X:23`, `I:13`↔`X:26` | ≈220Ω | |
| 10 | Gate: `I:14`↔`X:10`, `I:17`↔`W:20` | ≈100Ω | |
| 11 | Gate–source: `X:10`↔`Y:10`, `W:20`↔`X:20` | ≈100kΩ | |
| 12 | Nút: `I:18`↔`F:1`, `I:19`↔`G:1` | Thông | |
| 13 | VSENSE: `R:6`↔`T:6` thông; `R:6`↔`R:3` ≈100kΩ (R2); `V:8`↔`R:6` ≈100kΩ (R1). Số đo trôi nhẹ do tụ nạp | Như ghi | |
| 14 | Drain: `Z:10`↔`AA:10`; `Y:20`↔`AA:17`↔`X:16` | Thông | |
| 15 | **Không được thông:** 5V_MAIN–GND, 5V_ESP–GND, 3V3–GND, 3V3–5V, 5V_IN–5V_MAIN (khi SW1 tắt) | >1kΩ / hở | |
| 17 | Q3, SD, GAIN: `D:32` ↔ `D:34` thông; thang diode `F:32` → `E:32` ≈0.5–0.7V, ngược hở; `I:20` ↔ `G:9` ≈2.2kΩ; `G:10` ↔ `G:7` thông | Như ghi | |
| 16 | **Không được thông (lỗ kề nhau):** `I:23`–`I:24`, `I:4`–`I:5` (3V3–RST), `G:7`–`G:8`, `Y:23`–`Z:23`, `Y:26`–`Z:26`, `AA:15`–`AA:17`, các chân kề nhau của J7, J8, J9 | Hở (cặp có tụ lớn thì số đo tăng dần). Riêng `AA:15`–`AA:17` có D2 ở giữa: thang diode chiều `AA:17` → `AA:15` thấy ≈0.2–0.45V là đúng, chiều ngược phải hở | |

### 5.6 Đi dây trong mũ

- Cáp tới mic, LCD và nút: bện xoắn, **≤20cm**, đầu cắm có khóa (JST-PH/XH). Bọc co nhiệt ở mọi mối hàn.
- Dây servo và dây đèn mắt đi xa bản lề và cơ cấu chuyển động. Chừa đoạn dây dư ở chỗ mặt nạ chuyển động để không bị kéo căng. R_EYE nằm trên dây, sát hộp pin module mắt, bọc co nhiệt.
- Mic gần miệng, bọc mút, xa loa. Loa đặt phía tai, quay ra xa mic.
- Cố định PCB bằng ốc M3 hoặc đế nhựa, có đệm chống rung. Không dán keo nóng lên linh kiện bị nóng (F1, Q2 khi tải lớn, trở tải giả).
- Công tắc SW1 và chỗ cắm nguồn đặt ở nơi dễ với tay khi đang đội mũ. Dây SW1 dùng 22AWG.
- Cổng USB của devkit (cạnh dưới) phải tiếp cận được để nạp code; không đặt vật cao trên đường cắm cáp (cột `J`–`Q`, hàng 26–35 chỉ có thanh sao nằm sát mặt PCB).

### 5.7 Nghiệm thu HW4 §5

1. Đo thông mạch đúng 100% theo bảng HW2/HW3 (mục 5.5), không có chập.
2. Các bài thử HW1 (tải nặng nhất, mục 2.8 bước 4) và HW3 (mục 4.5) vẫn đạt sau khi lên PCB.
3. Lắc mạnh mũ (mô phỏng lúc đội) trong khi đang chạy: không reset, không mất tiếng, không mất hình.

## 6. Nhật ký đo

Ghi ngày, người đo, dụng cụ. Khi thay linh kiện hoặc sửa mạch thì thêm dòng mới, không xóa dòng cũ.

### 6.1 Thông số linh kiện thật

| Hạng mục | Giá trị | Ngày |
|---|---|---|
| Devkit: chính hãng/clone, phiên bản (v1.0/v1.1), chân LED RGB (48/38) | Đo thực tế: ___ | |
| Devkit có diode trên VBUS USB? | Đo thực tế: ___ | |
| Devkit: chữ in trên chip LDO, có diode ở chân 5V (B0 #5) → giữ hay nối tắt D1 (mục 2.3) | Đo thực tế: ___ | |
| 3V3 thấp nhất khi tải nặng (mục 2.8 bước 4) | ___ V | |
| Khoảng cách tâm 2 hàng chân devkit → cột header phải (R/S) | Đo thực tế: ___ mm → ___ | |
| LCD: driver in trên nhãn (ST7789), loại tấm (build `jarvis-helmet` hay `-noips` hiện đúng màu), hướng ảnh (`MIRROR_X/Y`), dòng 3V3 | Đo thực tế: ___ | |
| MAX98357: thứ tự chân, kích thước | Đo thực tế: ___ | |
| Tấm PCB: số cột × số hàng thật | Đo thực tế: ___ | |
| Sạc dự phòng: hãng/model, dòng ra danh định | ___ | |

### 6.2 Điện áp không tải (mục 2.8 bước 2–3)

| Điểm đo | Cổng USB-A | Cổng USB-C (nếu có) | Đạt (4.9–5.25V / ≥4.5V) |
|---|---|---|---|
| J1 (vào) | ___ V | ___ V | |
| 5V_MAIN (thanh sao) | ___ V | ___ V | |
| 5V_ESP (`H:23`, sau D1) | ___ V | ___ V | |
| 3V3 (`I:3`, khi đã cắm devkit) | ___ V | ___ V | |
| Đầu nhánh: J3.2 / J4.2 / U2 VIN / J6.1 / J5.1 | ___ / ___ / ___ / ___ / ___ V | | |

### 6.3 VSENSE và 5V_MAIN khi có tải (mục 2.8 bước 4, 6)

| Tình huống | 5V_MAIN đo bằng đồng hồ (V) | HUD / `vbus_mv` (mV) | Lệch (mV) | Thấp nhất thấy được (V) | ≥4.6V? |
|---|---|---|---|---|---|
| Chờ (WiFi kết nối, không nói) | | | | | |
| Đang hội thoại, loa 70% | | | | | |
| 2 servo chạy qua lại | | | | | |
| **Nặng nhất:** 2 servo + loa tối đa + WiFi phát, 10 phút | | | | | |
| Offset hiệu chỉnh đưa vào firmware | ___ mV | | | | |

### 6.4 Kết quả chọn R_EYE (mục 4.2)

| Module | Có sẵn điện trở? | V_f (V) | I_target (mA) | R tính (Ω) | R chọn | Công suất R | I thật ở 5V (mA) | Ngày |
|---|---|---|---|---|---|---|---|---|
| E1 (2 LED song song) | | | | | | | | |

### 6.5 Nhiệt độ MOSFET (mục 4.5)

| Thử | Tải | Phút 0 (°C) | Phút 5 (°C) | Phút 10 (°C) | Nhiệt độ phòng (°C) | Đạt (<50°C) |
|---|---|---|---|---|---|---|
| Q2, bật liên tục | 10Ω/5W hoặc quạt 5V (~500mA) | | | | | |
| Q1, đèn mắt 100% | E1 | | | | | |
| Q1, PWM 50% | E1 | | | | | |

### 6.6 Đếm số lần reset

| Bài thử | Thời gian | Số lần reset | `esp_reset_reason` ghi được | VSENSE thấp nhất | Ghi chú |
|---|---|---|---|---|---|
| HW1 thử nặng nhất trên breadboard | 10 phút | | | | |
| HW1 thử nặng nhất trên PCB (HW4) | 10 phút | | | | |
| HW4 lắc mũ khi đang chạy | ___ phút | | | | |
| IT1 E14: đội mũ 2 giờ bằng sạc dự phòng | 2 giờ | | | | Sạc có tự tắt không: ___ |

### 6.7 Dòng ngược (mục 2.8 bước 5)

| Trường hợp | Điểm đo | Giá trị | Đạt |
|---|---|---|---|
| (a) SW1 tắt, rút servo, cắm USB máy tính | 5V_MAIN tại thanh sao | ___ V (<0.3V) | |
| (b) SW1 bật + USB máy tính | ESP chạy, D1 nóng không? | ___ | |
| (c) Sạc tắt, vẫn cắm USB | Cổng USB / thanh sao | ___ V / ___ V | |

### 6.8 Hàn và thông mạch (mục 5.4, 5.5)

| Bước | Ngày | Thử đạt? | Lỗi tìm thấy / cách sửa |
|---|---|---|---|
| 1 Khối nguồn | | | |
| 2 Hàng rào cái + VSENSE | | | |
| 3 Khối công suất | | | |
| 4 Header cáp | | | |
| 5 Ω 5V/3V3–GND >1kΩ | | | |
| 6 Cắm module, chạy lại thử HW1–HW3 | | | |

## 7. Tham khảo (Rev B)

| Nguồn | Dùng cho |
|---|---|
| [Sơ đồ ESP32-S3-DevKitC-1 v1.1](https://dl.espressif.com/dl/schematics/SCH_ESP32-S3-DevKitC-1_V1.1_20221130.pdf), [v1.0](https://dl.espressif.com/dl/SCH_ESP32-S3-DEVKITC-1_V1_20210312C.pdf) | LDO SGM2212, diode 1N5819HW ở cổng USB (mục 2.3) |
| [SGM2212](https://www.sg-micro.com/product/SGM2212) | Dòng 800mA, sụt áp (mục 2.3) |
| [YD-ESP32-S3 (VCC-GND)](https://github.com/vcc-gnd/YD-ESP32-S3) | Clone 2 cổng USB-C: LDO CJ6107, diode D3 ở chân 5V (mục 2.3) |
| [ESP32-S3-WROOM-1 datasheet](https://www.espressif.com/sites/default/files/documentation/esp32-s3-wroom-1_wroom-1u_datasheet_en.pdf) | Dòng phát Wi-Fi 355mA, nguồn ≥0.5A |
| [TI SLVA139](https://www.ti.com/lit/an/slva139/slva139.pdf), [AO3401A](https://aosmd.com/sites/default/files/res/data_sheets/AO3401A.pdf) | Chống cắm ngược bằng P-MOSFET (mục 2.9) |
| [MAX98357A datasheet](https://www.analog.com/media/en/technical-documentation/data-sheets/MAX98357A-MAX98357B.pdf), [Adafruit pinouts](https://learn.adafruit.com/adafruit-max98357-i2s-class-d-mono-amp/pinouts) | Ngưỡng SD_MODE, GAIN, tần số LRCLK, tụ nguồn (mục 3.4) |
| [Hướng dẫn đấu dây breadboard xiaozhi](https://xiaozhi.me/xz-docs/docs/tutorial-basics/esp32-s3-bread-board-diy-wiring/), [FAQ xiaozhi](https://xiaozhi.me/xz-docs/docs/help-doc/xiaozhi-ai-chatbot-q&a/) | Bảng chân gốc; SD, GAIN nối cứng; GAIN hở gây khàn; loa 3W cần ≥5V/2A |
| xiaozhi-esp32 issues [#392](https://github.com/78/xiaozhi-esp32/issues/392), [#1552](https://github.com/78/xiaozhi-esp32/issues/1552), [#2116](https://github.com/78/xiaozhi-esp32/issues/2116) | Rè do thiếu dây GND amply; servo cần nguồn ≥3A; xì loa lúc cấu hình Wi-Fi |
| [INMP441 datasheet](https://microcontroller-course.web.cern.ch/Project_Material/resources/INMP441.pdf) | 100kΩ kéo xuống SD, L/R, tụ VDD (mục 3.3) |
| [Crash Works Iron_Man_Servo](https://github.com/crashworks3d/Iron_Man_Servo), [MG90S](https://protosupplies.com/product/servo-motor-micro-mg90s/) | Mũ Iron Man: sạc dự phòng nuôi chung servo và MCU; dòng MG90S 10 / 120–250 / 700mA (nghỉ / chạy / kẹt) |
| [IP5306](https://done.land/components/power/powersupplies/battery/chargers/charge-discharge/ip5306/) | Sạc dự phòng tự tắt khi tải <45mA trong 32 giây |
