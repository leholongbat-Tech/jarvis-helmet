#!/usr/bin/env python3
"""Bố trí bản thử breadboard của Jarvis Helmet (docs/hardware/breadboard.md).

Nguồn dữ liệu duy nhất cho bản breadboard: vị trí linh kiện và dây nhảy khai báo một lần ở đây,
được kiểm tra (không lỗ nào dùng hai lần, không cắm vào lỗ bị devkit che, không đụng chân
strapping/PSRAM/UART/USB, mỗi mạng đúng thành phần, không chập GPIO với nhau hay với nguồn),
rồi xuất ra docs/hardware/breadboard.svg và ghi các bảng vào breadboard.md (giữa các dấu
<!-- BEGIN:x --> / <!-- END:x -->).

Breadboard như ảnh: 2 tấm 63 cột + 3 thanh ray; devkit ESP32-S3-DevKitC-1 (clone 2 cổng USB-C)
cắm hàng chân J1 ở hàng h tấm trên, J3 ở hàng b tấm dưới, cột 42 (đầu anten) tới 63 (đầu USB).

Cách dùng:
  python tools/hardware/breadboard_layout.py           # kiểm tra + ghi SVG + cập nhật bảng
  python tools/hardware/breadboard_layout.py --check   # chỉ kiểm tra
"""

from __future__ import annotations

import argparse
import html
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SVG_OUT = ROOT / "docs" / "hardware" / "breadboard.svg"
MD_DOC = ROOT / "docs" / "hardware" / "breadboard.md"

# ---------------------------------------------------------------------------
# Lưới breadboard. Cột 1 ở bên phải, 63 ở bên trái (như số in trên board trong ảnh).
# Tọa độ dọc tính bằng bước lỗ (u), 0 = hàng − của ray trên cùng.
# ---------------------------------------------------------------------------
P = 21.5          # px mỗi bước 2.54 mm
BX = 130.0        # x của cột 63
BY = 240.0        # y của ray RT−
WIDTH, HEIGHT = 1600, 1320

ROW_U = {"a": 3.5, "b": 4.5, "c": 5.5, "d": 6.5, "e": 7.5,
         "f": 10.5, "g": 11.5, "h": 12.5, "i": 13.5, "j": 14.5}
BOARD_DU = {"T": 0.0, "D": 17.0}
RAIL_U = {"RT-": 0.0, "RT+": 1.0, "RG-": 17.0, "RG+": 18.0, "RD-": 34.0, "RD+": 35.0}
# Lỗ ray theo nhóm 5, cách 1 lỗ (đối chiếu ảnh: nhóm 33–37 nằm ngay phải đầu anten).
RAIL_COLS = [c for start in range(3, 58, 6) for c in range(start, start + 5)]

HOLE_RE = re.compile(r"^([TD])\.([a-j])(\d{1,2})$")
RAIL_RE = re.compile(r"^(R[TGD])([+\-−])@(\d{1,2})$")


def X(col: float) -> float:
    return BX + (63 - col) * P


def Y(u: float) -> float:
    return BY + u * P


def norm(ref: str) -> str:
    return ref.replace("−", "-")


def parse(ref: str):
    ref = norm(ref)
    m = HOLE_RE.match(ref)
    if m:
        col = int(m.group(3))
        if not 1 <= col <= 63:
            raise ValueError(f"cột ngoài 1..63: {ref}")
        return ("hole", m.group(1), m.group(2), col)
    m = RAIL_RE.match(ref)
    if m:
        col = int(m.group(3))
        if col not in RAIL_COLS:
            raise ValueError(f"không có lỗ ray ở cột {col}: {ref}")
        return ("rail", m.group(1) + m.group(2), col)
    raise ValueError(f"tham chiếu lỗ sai: {ref}")


def uc(ref: str) -> tuple[float, float]:
    p = parse(ref)
    if p[0] == "hole":
        return float(p[3]), ROW_U[p[2]] + BOARD_DU[p[1]]
    return float(p[2]), RAIL_U[p[1]]


def xy(ref: str) -> tuple[float, float]:
    col, u = uc(ref)
    return X(col), Y(u)


def strip_of(ref: str):
    """Dải kim loại chứa lỗ: 5 lỗ a–e hoặc f–j của một cột, hoặc cả một hàng ray."""
    p = parse(ref)
    if p[0] == "hole":
        return (p[1], "ae" if p[2] in "abcde" else "fj", p[3])
    return (p[1],)


def show(ref: str) -> str:
    return norm(ref).replace("-@", "−@")


# ---------------------------------------------------------------------------
# Devkit: chân DK-Ln ở cột 41+n tấm trên hàng h; DK-Rn ở cột 41+n tấm dưới hàng b.
# ---------------------------------------------------------------------------
DK_L = ["3V3", "3V3", "RST", "4", "5", "6", "7", "15", "16", "17", "18", "8", "3", "46",
        "9", "10", "11", "12", "13", "14", "5V", "GND"]
DK_R = ["GND", "TX", "RX", "1", "2", "42", "41", "40", "39", "38", "37", "36", "35", "0",
        "45", "48", "47", "21", "20", "19", "GND", "GND"]

# Chân không được nối gì (lý do ghi vào bảng).
DK_NC = {
    ("L", 3): "EN/RST",
    ("L", 13): "strapping",
    ("L", 14): "strapping",
    ("R", 2): "UART0 TX, log",
    ("R", 3): "UART0 RX, log",
    ("R", 11): "PSRAM octal",
    ("R", 12): "PSRAM octal",
    ("R", 13): "PSRAM octal",
    ("R", 14): "nút BOOT có sẵn",
    ("R", 15): "strapping",
    ("R", 16): "LED RGB có sẵn",
    ("R", 19): "USB D+",
    ("R", 20): "USB D−",
}
DK_SPARE = {("L", 19): "trống", ("L", 20): "trống",
            ("R", 5): "trống", ("R", 10): "trống (DevKitC-1 v1.1: LED RGB)"}


def dk_pin_ref(side: str, n: int) -> str:
    return f"T.h{41 + n}" if side == "L" else f"D.b{41 + n}"


def dk_free_holes(side: str, n: int) -> list[str]:
    col = 41 + n
    return [f"T.f{col}", f"T.g{col}"] if side == "L" else [f"D.c{col}", f"D.d{col}", f"D.e{col}"]


def dk_label(name: str) -> str:
    return f"GPIO{name}" if name.isdigit() else name


def covered(ref: str) -> bool:
    """Lỗ nằm dưới thân devkit (kể cả phần anten) hoặc là chỗ chân devkit."""
    p = parse(ref)
    if p[0] == "rail":
        return p[1].startswith("RG") and p[2] >= 38
    _, board, row, col = p
    if board == "T":
        return (row in "hij" and col >= 42) or (row == "j" and 39 <= col <= 41)
    return row in "ab" and col >= 42


NC_HOLES = {h: why for (side, n), why in DK_NC.items() for h in dk_free_holes(side, n)}

# ---------------------------------------------------------------------------
# Linh kiện. pins = [(tên chân, lỗ)]. stage = bước lắp ở mục 5 của breadboard.md.
# ---------------------------------------------------------------------------
STAGES = {
    1: "B1 · Nguồn vào",
    2: "B2 · Nguồn devkit",
    3: "B3 · Ray 3V3 + LCD",
    4: "B4 · Âm thanh",
    5: "B5 · Nút bấm",
    6: "B6 · Đo 5V (VSENSE)",
    7: "B7 · MOSFET đèn mắt + output",
    8: "B8 · Servo",
}

PARTS: list[dict] = []


def part(pid, kind, pins, *, name, value="", stage, note="", **draw):
    PARTS.append(dict(id=pid, kind=kind, pins=list(pins), name=name, value=value,
                      stage=stage, note=note, draw=draw))


# B1 – nguồn vào
part("BUSB", "usbc", [("VBUS", "T.e1"), ("GND", "RT-@3")], stage=1,
     name="Mạch USB-C sink", value="có 5.1kΩ ở CC1 và CC2",
     note="Hàn 2 dây 22AWG lõi đặc: đỏ VBUS → `T.e1`, đen GND → `RT−@3`. Hoặc cáp USB-A cắt (wiring.md §2.7)")
part("F1", "pptc", [("1", "T.c1"), ("2", "T.c3")], stage=1,
     name="Cầu chì tự phục hồi", value="PPTC 2.5A hold",
     note="Mọi dòng 5V đi qua F1; chập trên breadboard thì F1 ngắt")
part("C1", "ecap", [("+", "RT+@4"), ("-", "RT-@4")], stage=1, lie="up",
     name="Tụ hóa", value="1000µF/10V",
     note="Sát chỗ cắm servo. Bẻ chân cho thân **nằm ngang chìa ra ngoài mép trên**, không che lỗ ray")
part("C2", "ccap", [("1", "RT+@7"), ("2", "RT-@7")], stage=1,
     name="Tụ gốm", value="100nF", note="Lọc nhiễu chổi than servo")

# B2 – nguồn devkit
part("D1", "diode", [("A", "RT+@61"), ("K", "T.a62")], stage=2,
     name="Diode Schottky", value="1N5819",
     note="Vạch (catot) về phía `T.a62`. Chặn 5V của cáp USB máy tính chảy sang ray (wiring.md §2.3)")
part("C5", "ecap", [("+", "T.c62"), ("-", "T.c63")], stage=2, lie="right",
     name="Tụ hóa", value="470µF/10V",
     note="Thân nằm ngang về phía cột 59–61 (đang trống), không che `T.e62`, `T.e63`")
part("C6", "ccap", [("1", "T.b62"), ("2", "T.b63")], stage=2,
     name="Tụ gốm", value="100nF", note="")

# B3 – LCD
part("U4", "lcd", [("LED", "D.j47"), ("CS", "D.j48"), ("DC", "D.j49"), ("RESET", "D.j50"),
                   ("SDI", "D.j51"), ("SCK", "D.j52"), ("VCC", "D.j53"), ("GND", "D.j54")],
     stage=3, name="LCD 2.4\" ST7789 320×240 (MSP2401)", value="header 14 chân, dùng 1–8",
     note="Module 77×43mm nằm ngoài breadboard: 8 dây Dupont cái–đực ≤15cm từ header module tới hàng j, **theo tên chân** (chân 1 = VCC, pad vuông). Bỏ trống SDO(MISO), T_CLK…T_IRQ và khe thẻ SD")

# B4 – âm thanh
part("U2", "amp", [("VIN", "T.a36"), ("GND", "T.a37"), ("SD", "T.a38"), ("GAIN", "T.a39"),
                   ("DIN", "T.a40"), ("BCLK", "T.a41"), ("LRC", "T.a42")], stage=4,
     name="Amply I2S MAX98357A", value="",
     note="Chân ở hàng a, thân nằm đè lên ray RT, cọc loa quay ra mép trên. SD ← GPIO12 qua R13 (W36), GAIN nối VIN (W37, 6 dB)")
part("C3", "ecap", [("+", "T.c36"), ("-", "T.c37")], stage=4, lie="left",
     name="Tụ hóa", value="470µF/10V",
     note="Thân nằm ngang về phía cột 38–40 (che hàng b–d, chừa hàng e cho dây I2S)")
part("R13", "res", [("1", "T.e38"), ("2", "T.f38")], stage=4,
     name="Điện trở chân SD amply", value="2.2kΩ",
     note="Đứng, vắt qua rãnh giữa ở cột 38 (dải SD của U2; `T.b38`–`T.d38` nằm dưới C3). GPIO12 thấp = tắt amply, cao = bật")
part("C4", "ccap", [("1", "T.e36"), ("2", "T.e37")], stage=4,
     name="Tụ gốm", value="100nF", note="")
part("U3", "mic", [("VDD", "T.g43"), ("GND", "T.g63"), ("WS", "T.g45"), ("SCK", "T.g46"),
                   ("SD", "T.g47")], stage=4,
     name="Mic I2S INMP441", value="",
     note="Ngoài breadboard, 5 dây Dupont cái–đực ≤20cm bện xoắn (M1–M5). **L/R nối GND ngay trên module mic**")

# B5 – nút 6×6 cắm vắt qua rãnh giữa tấm dưới. Nối chéo (GPIO một góc, GND góc đối diện) nên
# đúng với cả hai kiểu nối trong của nút; hai chân còn lại không nối gì.
part("SW2", "tact", [("e21", "D.e21"), ("e23", "D.e23"), ("f21", "D.f21"), ("f23", "D.f23")],
     stage=5, name="Nút nhấn 6×6 – PTT", value="",
     note="GPIO10 vào nửa trên cột 23 (W19), GND vào nửa dưới cột 21 (W21)")
part("SW3", "tact", [("e15", "D.e15"), ("e17", "D.e17"), ("f15", "D.f15"), ("f17", "D.f17")],
     stage=5, name="Nút nhấn 6×6 – mặt nạ", value="",
     note="GPIO11 vào nửa trên cột 17 (W20), GND vào nửa dưới cột 15 (W22)")

# B6 – VSENSE
part("R1", "res", [("1", "D.h45"), ("2", "D.h43")], stage=6,
     name="Điện trở", value="100kΩ 1%", note="Cột 43 nhận VSENSE_TOP (W25)")
part("R2", "res", [("1", "D.g45"), ("2", "D.g42")], stage=6,
     name="Điện trở", value="100kΩ 1%", note="Cột 42 = GND của devkit (W24)")
part("C7", "ccap", [("1", "D.i45"), ("2", "D.i42")], stage=6,
     name="Tụ gốm", value="100nF", note="")

# B7 – MOSFET
part("Q1", "mosfet", [("G", "T.c14"), ("S", "T.c15"), ("D", "T.c16")], stage=7,
     name="AO3400A trên đế SOT-23 → 3 chân", value="",
     note="Đế cắm đứng. **Kiểm tra đế đưa G/S/D ra chân nào** (thang diode: S→D ≈0.5–0.7V)")
part("R3", "res", [("1", "T.d13"), ("2", "T.d14")], stage=7, name="Điện trở gate", value="100Ω", note="Lắp đứng")
part("R5", "res", [("1", "T.b14"), ("2", "T.b15")], stage=7, name="Điện trở kéo xuống", value="100kΩ",
     note="Gate → source: đèn tắt khi reset, nạp code")
part("R_EYE1", "res", [("1", "RT+@17"), ("2", "T.a17")], stage=7, name="Hạn dòng mắt trái",
     value="150Ω (với LED thử)", note="Khi cắm module mắt thật: thay bằng giá trị đo theo wiring.md §4.2")
part("R_EYE2", "res", [("1", "RT+@18"), ("2", "T.a18")], stage=7, name="Hạn dòng mắt phải",
     value="150Ω (với LED thử)", note="Như trên")
part("BL1", "led", [("A", "T.d17"), ("K", "T.d16")], stage=7, name="LED thử thay mắt trái",
     value="LED 3mm", note="Chân dài (A) ở `T.d17`. Module mắt thật E1: rút BL1, cắm dây + vào `T.d17`, − vào `T.d16`")
part("BL2", "led", [("A", "T.b18"), ("K", "T.b16")], stage=7, name="LED thử thay mắt phải",
     value="LED 3mm", note="Chân dài (A) ở `T.b18`. Module E2: rút BL2, dây + vào `T.b18`, − vào `T.b16`")
part("Q2", "mosfet", [("G", "T.c21"), ("S", "T.c22"), ("D", "T.c23")], stage=7,
     name="AO3400A trên đế SOT-23 → 3 chân", value="", note="Như Q1")
part("R4", "res", [("1", "T.d20"), ("2", "T.d21")], stage=7, name="Điện trở gate", value="100Ω", note="Lắp đứng")
part("R6", "res", [("1", "T.b21"), ("2", "T.b22")], stage=7, name="Điện trở kéo xuống", value="100kΩ", note="")
part("D2", "diode", [("A", "T.a23"), ("K", "RT+@24")], stage=7, name="Diode chống ngược", value="1N5819",
     note="Vạch (catot) về phía ray `RT+@24`. Lắp ngược = chập 5V khi Q2 dẫn")
part("RL", "res", [("1", "RT+@25"), ("2", "T.a24")], stage=7, name="Điện trở cho LED thử", value="220Ω", note="")
part("BL3", "led", [("A", "T.d24"), ("K", "T.d23")], stage=7, name="LED thử output dự phòng",
     value="LED 3mm", note="Chân dài (A) ở `T.d24`")
part("J5", "ext", [("+", "RT+@21"), ("-", "T.e23")], stage=7, name="Tải ngoài (tùy chọn)",
     value="≤1A", note="Ví dụ trở 10Ω/5W khi thử nhiệt Q2 (wiring.md §4.5), dây 22AWG ngắn")

# B8 – servo
part("J3", "header", [("GND", "T.c5"), ("+5V", "T.c6"), ("SIG", "T.c7")], stage=8,
     name="Header đực 3P → servo S0 (M1, trái)", value="",
     note="Dây nâu ở `T.c5`, đỏ `T.c6`, cam `T.c7`")
part("J4", "header", [("GND", "T.c9"), ("+5V", "T.c10"), ("SIG", "T.c11")], stage=8,
     name="Header đực 3P → servo S1 (M2, phải)", value="",
     note="Dây nâu ở `T.c9`, đỏ `T.c10`, cam `T.c11`")
part("R7", "res", [("1", "T.b7"), ("2", "T.b8")], stage=8, name="Điện trở tín hiệu servo", value="220Ω", note="Lắp đứng")
part("R8", "res", [("1", "T.b11"), ("2", "T.b12")], stage=8, name="Điện trở tín hiệu servo", value="220Ω", note="Lắp đứng")
part("R9", "res", [("1", "T.d7"), ("2", "T.d5")], stage=8, name="Kéo xuống (tùy chọn)", value="10kΩ",
     note="Chỉ lắp nếu servo giật lúc khởi động")
part("R10", "res", [("1", "T.d11"), ("2", "T.d9")], stage=8, name="Kéo xuống (tùy chọn)", value="10kΩ",
     note="Như trên")

# ---------------------------------------------------------------------------
# Dây. kind: power = 22AWG lõi đặc, càng ngắn càng tốt; jumper = dây cắm breadboard.
# route: điểm trung gian (cột, u) để vẽ, hoặc ("arc", độ cong px). lab: chỗ đặt nhãn (cột, u).
# ---------------------------------------------------------------------------
WIRES: list[dict] = []


def wire(wid, a, b, net, *, stage, kind="jumper", route=None, lab=None, note=""):
    WIRES.append(dict(id=wid, a=a, b=b, net=net, stage=stage, kind=kind, route=route,
                      lab=lab, note=note))


wire("W1", "T.a3", "RT+@3", "5V_MAIN", stage=1, kind="power", lab=(2.45, 2.1),
     note="Ra F1 → ray 5V. ★ = nhóm lỗ ray cột 3–7")

wire("W2", "T.e62", "T.f62", "5V_ESP", stage=2, kind="power", lab=(61.45, 9.0), note="Tới chân 5Vin (`DK-L21`)")
wire("W3", "T.e63", "T.f63", "GND_ESP", stage=2, kind="power", lab=(63.55, 9.0), note="Tới chân GND (`DK-L22`)")
wire("W4", "T.a63", "RT-@60", "GND_ESP", stage=2, kind="power", route=("arc", -12), lab=(62.9, 1.55),
     note="GND của devkit về ray GND")

wire("W5", "T.f42", "D.f53", "3V3", stage=3, route=[(41.5, 10.0), (38.5, 10.0), (38.5, 28.0), (53, 28.0)],
     lab=(38.5, 16.5), note="3V3 (`DK-L1`) tới VCC của LCD; đi vòng bên phải đầu anten")
wire("W6", "D.e63", "RD-@61", "GND_ESP", stage=5, route=[(62.5, 25.3), (62.5, 33.3)],
     lab=(62.5, 29.5), note="GND (`DK-R22`) xuống ray RD−")
wire("W7", "D.e47", "D.f47", "LCD_BLK", stage=3, lab=(47, 25.55))
wire("W8", "D.e48", "D.f48", "LCD_CS", stage=3, lab=(48, 26.45))
wire("W9", "D.e49", "D.f49", "LCD_DC", stage=3, lab=(49, 25.55))
wire("W10", "D.e50", "D.f50", "LCD_RES", stage=3, lab=(50, 26.45))
wire("W11", "D.e58", "D.f51", "LCD_MOSI", stage=3, route=[(58, 25.7), (51, 25.7)], lab=(54.5, 25.7))
wire("W12", "D.e59", "D.f52", "LCD_SCLK", stage=3, route=[(59, 26.3), (52, 26.3)], lab=(55.5, 26.3))
wire("W13", "D.e62", "D.f54", "GND_ESP", stage=3, route=[(62, 26.85), (54, 26.85)], lab=(58.0, 26.85),
     note="GND (`DK-R21`) tới GND của LCD")

wire("W14", "T.b36", "RT+@33", "5V_AUDIO", stage=4, kind="power", lab=(33.9, 2.95))
wire("W15", "T.b37", "RT-@34", "GND_AUDIO", stage=4, kind="power", lab=(35.2, 1.55))
wire("W16", "T.e40", "T.f48", "I2S_SPK_DIN", stage=4, lab=(42.0, 8.25))
wire("W17", "T.e41", "T.f49", "I2S_SPK_BCLK", stage=4, lab=(45.0, 9.0))
wire("W18", "T.e42", "T.f50", "I2S_SPK_LRC", stage=4, lab=(48.0, 9.75))
wire("W36", "T.g38", "T.g59", "AMP_SD", stage=4, route=[(38, 12.0), (59, 12.0)], lab=(50.0, 12.0),
     note="GPIO12 (`DK-L18`) → R13; đi sát mép trên devkit")
wire("W37", "T.e39", "T.d36", "AMP_GAIN", stage=4, lab=(37.5, 8.1),
     note="GAIN nối VIN = 6 dB. To hơn: cắm `T.d37` (GND) = 12 dB")

wire("W19", "T.g57", "D.c23", "BTN_PTT", stage=5,
     route=[(57, 9.7), (23.5, 9.7), (23.5, 21.9)], lab=(23.5, 16.0),
     note="Dài ~15cm, đi vòng bên phải devkit sang tấm dưới")
wire("W20", "T.g58", "D.c17", "BTN_MASK", stage=5,
     route=[(58, 9.4), (17.5, 9.4), (17.5, 21.9)], lab=(17.5, 16.0),
     note="Như W19")
wire("W21", "D.j21", "RD-@21", "GND_ESP", stage=5, lab=(21.6, 32.8))
wire("W22", "D.j15", "RD-@15", "GND_ESP", stage=5, lab=(15.6, 32.8))

wire("W23", "D.e45", "D.f45", "VSENSE", stage=6, lab=(45.0, 26.0))
wire("W24", "D.e42", "D.f42", "GND_ESP", stage=6, lab=(42.0, 26.0))
wire("W25", "RT+@31", "D.j43", "VSENSE_TOP", stage=6,
     route=[(31.5, 1.7), (31.5, 31.0), (43, 31.0)], lab=(31.5, 23.0),
     note="Dây đo 5V_MAIN, đi vòng bên phải devkit")

wire("W26", "T.g53", "T.e13", "EYES_PWM", stage=7, route=[(53, 8.45), (13, 8.45)], lab=(26.0, 8.45))
wire("W27", "T.a15", "RT-@15", "GND_PWR", stage=7, kind="power", route=("arc", 9), lab=(14.35, 2.1))
wire("W28", "T.g56", "T.e20", "OUT_PWM", stage=7, route=[(56, 8.15), (20, 8.15)], lab=(28.0, 8.15))
wire("W29", "T.a22", "RT-@22", "GND_PWR", stage=7, kind="power", route=("arc", 9), lab=(21.35, 2.1))

wire("W30", "T.a5", "RT-@5", "GND_SERVO", stage=8, kind="power", route=("arc", 9), lab=(4.35, 2.1))
wire("W31", "T.a6", "RT+@6", "5V_SERVO", stage=8, kind="power", lab=(6.0, 2.4))
wire("W32", "T.a9", "RT-@9", "GND_SERVO", stage=8, kind="power", route=("arc", 9), lab=(8.35, 2.1))
wire("W33", "T.a10", "RT+@10", "5V_SERVO", stage=8, kind="power", lab=(10.0, 2.4))
wire("W34", "T.g51", "T.e8", "SERVO0_PWM", stage=8, route=[(51, 9.05), (8, 9.05)], lab=(24.0, 9.05))
wire("W35", "T.g52", "T.e12", "SERVO1_PWM", stage=8, route=[(52, 8.75), (12, 8.75)], lab=(30.0, 8.75))

# Dây mic (Dupont) – đầu kia là chân của U3 nên chỉ khai báo để vẽ và lập bảng.
MIC_WIRES = [("M1", "VDD", "3V3"), ("M2", "GND", "GND_ESP"), ("M3", "WS", "I2S_MIC_WS"),
             ("M4", "SCK", "I2S_MIC_SCK"), ("M5", "SD", "I2S_MIC_SD")]

# Chức năng của từng chân devkit (để lập bảng và kiểm tra đủ/thừa).
DK_FUNC = {
    ("L", 1): "3V3 → LCD VCC (W5)", ("L", 2): "3V3 → mic VDD (M1)",
    ("L", 4): "Mic WS (M3)", ("L", 5): "Mic SCK (M4)", ("L", 6): "Mic SD (M5)",
    ("L", 7): "Amply DIN (W16)", ("L", 8): "Amply BCLK (W17)", ("L", 9): "Amply LRC (W18)",
    ("L", 10): "Servo S0 (W34 → R7)", ("L", 11): "Servo S1 (W35 → R8)",
    ("L", 12): "Gate đèn mắt (W26 → R3)", ("L", 15): "Gate output dự phòng (W28 → R4)",
    ("L", 16): "Nút PTT (W19)", ("L", 17): "Nút mặt nạ (W20)", ("L", 18): "SD amply (R13 → W36)",
    ("L", 21): "5Vin ← 5V_ESP (W2)", ("L", 22): "GND (W3); mic GND (M2)",
    ("R", 1): "GND → R2, C7 (W24)", ("R", 4): "VSENSE (W23)",
    ("R", 6): "LCD LED (W7)", ("R", 7): "LCD CS (W8)", ("R", 8): "LCD DC (W9)", ("R", 9): "LCD RESET (W10)",
    ("R", 17): "LCD SDI (W11)", ("R", 18): "LCD SCK (W12)",
    ("R", 21): "GND → LCD GND (W13)", ("R", 22): "GND → ray RD− (W6)",
}

# Thành phần của từng mạng (chân linh kiện "Ký hiệu.chân", chân devkit "DK.<tên>", ray "RT+"…).
# Chân linh kiện nằm trong mạng mà không được liệt kê ở đây → lỗi.
EXPECT = {
    "5V_IN": ["BUSB.VBUS", "F1.1"],
    "5V_MAIN": ["RT+", "F1.2", "C1.+", "C2.1", "J3.+5V", "J4.+5V", "R_EYE1.1", "R_EYE2.1",
                "D2.K", "RL.1", "J5.+", "U2.VIN", "U2.GAIN", "C3.+", "C4.1", "R1.2", "D1.A"],
    "5V_ESP": ["DK.5V", "D1.K", "C5.+", "C6.1"],
    "GND": ["RT-", "RD-", "DK.GND", "BUSB.GND", "C1.-", "C2.2", "J3.GND", "J4.GND", "R9.2", "R10.2",
            "Q1.S", "R5.2", "Q2.S", "R6.2", "U2.GND", "C3.-", "C4.2", "C5.-", "C6.2", "U3.GND",
            "U4.GND", "R2.2", "C7.2", "SW2.f21", "SW3.f15"],
    "3V3": ["DK.3V3", "U4.VCC", "U3.VDD"],
    "GPIO4": ["DK.4", "U3.WS"], "GPIO5": ["DK.5", "U3.SCK"], "GPIO6": ["DK.6", "U3.SD"],
    "GPIO7": ["DK.7", "U2.DIN"], "GPIO15": ["DK.15", "U2.BCLK"], "GPIO16": ["DK.16", "U2.LRC"],
    "GPIO17": ["DK.17", "R7.2"], "SERVO0": ["R7.1", "J3.SIG", "R9.1"],
    "GPIO18": ["DK.18", "R8.2"], "SERVO1": ["R8.1", "J4.SIG", "R10.1"],
    "GPIO8": ["DK.8", "R3.1"], "EYES_GATE": ["R3.2", "Q1.G", "R5.1"],
    "EYES_DRV": ["Q1.D", "BL1.K", "BL2.K"],
    "EYE1+": ["R_EYE1.2", "BL1.A"], "EYE2+": ["R_EYE2.2", "BL2.A"],
    "GPIO9": ["DK.9", "R4.1"], "OUT_GATE": ["R4.2", "Q2.G", "R6.1"],
    "OUT_DRV": ["Q2.D", "D2.A", "BL3.K", "J5.-"], "LOAD+": ["RL.2", "BL3.A"],
    "GPIO10": ["DK.10", "SW2.e23"], "GPIO11": ["DK.11", "SW3.e17"],
    "GPIO1": ["DK.1", "R1.1", "R2.1", "C7.1"],
    "GPIO42": ["DK.42", "U4.LED"], "GPIO41": ["DK.41", "U4.CS"], "GPIO40": ["DK.40", "U4.DC"],
    "GPIO39": ["DK.39", "U4.RESET"], "GPIO47": ["DK.47", "U4.SDI"], "GPIO21": ["DK.21", "U4.SCK"],
    "GPIO12": ["DK.12", "R13.2"], "AMP_SD": ["R13.1", "U2.SD"],
    # Hai chân còn lại của mỗi nút nối với GPIO hoặc GND tùy kiểu nối trong của nút,
    # nên dải của chúng phải trống hoàn toàn.
    "SW2.e21": ["SW2.e21"], "SW2.f23": ["SW2.f23"], "SW3.e15": ["SW3.e15"], "SW3.f17": ["SW3.f17"],
}


# ---------------------------------------------------------------------------
# Kiểm tra
# ---------------------------------------------------------------------------
class UnionFind:
    def __init__(self):
        self.parent: dict = {}

    def find(self, a):
        self.parent.setdefault(a, a)
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def check() -> list[str]:
    errors: list[str] = []
    used: dict[str, str] = {}

    def use(ref: str, who: str):
        try:
            parse(ref)
        except ValueError as exc:
            errors.append(f"{who}: {exc}")
            return
        key = norm(ref)
        if key in used:
            errors.append(f"lỗ {show(ref)} dùng hai lần: {used[key]} và {who}")
        used[key] = who
        if covered(ref):
            errors.append(f"{who}: lỗ {show(ref)} nằm dưới devkit")
        if key in NC_HOLES:
            errors.append(f"{who}: lỗ {show(ref)} thuộc chân không được nối ({NC_HOLES[key]})")

    for p in PARTS:
        for pin, ref in p["pins"]:
            use(ref, f"{p['id']}.{pin}")
    for w in WIRES:
        use(w["a"], w["id"])
        use(w["b"], w["id"])
    if errors:
        return errors

    # Netlist: dây nối hai dải; chân devkit cùng tên (3V3, GND) nối nhau bên trong devkit.
    uf = UnionFind()
    member_strip = {f"{p['id']}.{pin}": strip_of(ref) for p in PARTS for pin, ref in p["pins"]}
    for w in WIRES:
        uf.union(strip_of(w["a"]), strip_of(w["b"]))
    dk_strips: dict[str, list] = defaultdict(list)
    for side, names in (("L", DK_L), ("R", DK_R)):
        for n, name in enumerate(names, 1):
            dk_strips[name].append(strip_of(dk_pin_ref(side, n)))
    for strips in dk_strips.values():
        for s in strips[1:]:
            uf.union(strips[0], s)

    def node(member: str):
        if member.startswith("DK."):
            return uf.find(dk_strips[member[3:]][0])
        if member in ("RT+", "RT-", "RD+", "RD-"):
            return uf.find((member,))
        return uf.find(member_strip[member])

    root_of = {}
    for group, members in EXPECT.items():
        roots = {node(m) for m in members}
        if len(roots) != 1:
            errors.append(f"mạng {group} bị tách: {', '.join(members)}")
        root_of[group] = node(members[0])
    by_root = defaultdict(list)
    for group, root in root_of.items():
        by_root[root].append(group)
    errors += [f"chập mạng: {', '.join(g)}" for g in by_root.values() if len(g) > 1]
    listed = {m: g for g, ms in EXPECT.items() for m in ms}
    for member in member_strip:
        if member not in listed:
            errors.append(f"chân {member} chưa khai báo mạng trong EXPECT")
            continue
        owners = by_root.get(node(member), [])
        if owners and listed[member] not in owners:
            errors.append(f"chân {member} nằm nhầm trong mạng {owners[0]}")
    for name, strips in dk_strips.items():
        if f"DK.{name}" not in listed and uf.find(strips[0]) in by_root:
            errors.append(f"chân devkit {name} bị nối vào mạng {by_root[uf.find(strips[0])]}")

    # Chân devkit có chức năng phải có dây/linh kiện ở lỗ trống, và ngược lại.
    for side in ("L", "R"):
        for n in range(1, 23):
            has = any(norm(h) in used for h in dk_free_holes(side, n))
            func = DK_FUNC.get((side, n))
            if has and func is None:
                errors.append(f"DK-{side}{n} có dây nhưng chưa ghi chức năng")
            if func and not has and "dự phòng" not in func:
                errors.append(f"DK-{side}{n} ghi '{func}' nhưng không có dây")
    return errors


# ---------------------------------------------------------------------------
# Vẽ SVG
# ---------------------------------------------------------------------------
TEXT = "#1d1d1f"
MUTED = "#5f6368"
NET_COLOR = {"5V": "#d62728", "3V3": "#ef7d00", "GND": "#333333", "I2S": "#1f6fd1",
             "SPI": "#8e44ad", "PWM": "#2a9d3a", "ANALOG": "#c79a00", "BTN": "#0f8f9a"}


def net_cat(net: str) -> str:
    if net.startswith("5V") or net == "VSENSE_TOP":
        return "5V"
    if net == "3V3":
        return "3V3"
    if net.startswith("GND"):
        return "GND"
    if net.startswith("I2S") or net == "AMP_SD":
        return "I2S"
    if net == "AMP_GAIN":
        return "5V"
    if net.startswith("LCD"):
        return "SPI"
    if net.endswith("PWM"):
        return "PWM"
    if net == "VSENSE":
        return "ANALOG"
    if net.startswith("BTN"):
        return "BTN"
    raise ValueError(net)


class Svg:
    def __init__(self):
        self.out: list[str] = []

    def add(self, s: str):
        self.out.append(s)

    def text(self, x, y, s, size=12.0, anchor="start", weight="normal", fill=TEXT, halo=False,
             rotate=None):
        attrs = [f'x="{x:.1f}"', f'y="{y:.1f}"', f'font-size="{size}"', f'text-anchor="{anchor}"',
                 f'fill="{fill}"']
        if weight != "normal":
            attrs.append(f'font-weight="{weight}"')
        if halo:
            attrs.append('stroke="#ffffff" stroke-width="3" stroke-linejoin="round" paint-order="stroke"')
        if rotate is not None:
            attrs.append(f'transform="rotate({rotate} {x:.1f} {y:.1f})"')
        self.add(f'<text {" ".join(attrs)}>{html.escape(s)}</text>')

    def rect(self, x, y, w, h, fill="none", stroke="none", sw=1.0, rx=0.0, extra=""):
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
                 f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}" {extra}/>')

    def line(self, x1, y1, x2, y2, stroke=TEXT, sw=1.0, extra=""):
        self.add(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" '
                 f'stroke-width="{sw}" stroke-linecap="round" {extra}/>')

    def circle(self, x, y, r, fill="none", stroke="none", sw=1.0, extra=""):
        self.add(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{fill}" stroke="{stroke}" '
                 f'stroke-width="{sw}" {extra}/>')

    def path(self, d, stroke=TEXT, sw=1.0, fill="none", extra=""):
        self.add(f'<path d="{d}" stroke="{stroke}" stroke-width="{sw}" fill="{fill}" '
                 f'stroke-linecap="round" stroke-linejoin="round" {extra}/>')


def rounded_path(pts: list[tuple[float, float]], r: float = 6.0) -> str:
    d = [f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"]
    for i in range(1, len(pts) - 1):
        (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[i + 1]
        l1 = math.hypot(x1 - x0, y1 - y0) or 1
        l2 = math.hypot(x2 - x1, y2 - y1) or 1
        rr = min(r, l1 / 2, l2 / 2)
        ax, ay = x1 - (x1 - x0) / l1 * rr, y1 - (y1 - y0) / l1 * rr
        bx, by = x1 + (x2 - x1) / l2 * rr, y1 + (y2 - y1) / l2 * rr
        d.append(f"L{ax:.1f},{ay:.1f} Q{x1:.1f},{y1:.1f} {bx:.1f},{by:.1f}")
    d.append(f"L{pts[-1][0]:.1f},{pts[-1][1]:.1f}")
    return " ".join(d)


def arc_path(a, b, bend: float) -> str:
    (x1, y1), (x2, y2) = a, b
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy) or 1
    cx, cy = (x1 + x2) / 2 - dy / length * bend, (y1 + y2) / 2 + dx / length * bend
    return f"M{x1:.1f},{y1:.1f} Q{cx:.1f},{cy:.1f} {x2:.1f},{y2:.1f}"


def get_part(pid: str) -> dict:
    return next(p for p in PARTS if p["id"] == pid)


def draw_board(s: Svg):
    left, right = X(63) - 1.5 * P, X(1) + 1.5 * P
    pieces = [(-1.0, 2.15), (2.3, 15.7), (15.85, 19.15), (19.3, 32.7), (32.85, 36.0)]
    for u0, u1 in pieces:
        s.rect(left, Y(u0), right - left, (u1 - u0) * P, fill="#f5f4ef", stroke="#c9c5ba", sw=1.2, rx=4)
    for base in (0.0, 17.0):
        s.rect(left + 4, Y(base + 8.55), right - left - 8, 0.9 * P, fill="#e6e3da", rx=2)
    for neg, pos in ((-0.6, 1.6), (16.4, 18.6), (33.4, 35.6)):
        s.line(X(61.4), Y(neg), X(2.6), Y(neg), stroke="#1f6fd1", sw=1.6)
        s.line(X(61.4), Y(pos), X(2.6), Y(pos), stroke="#d62728", sw=1.6)
    hole, hs = "#8b877c", 0.29 * P
    for board in "TD":
        for row in ROW_U:
            for col in range(1, 64):
                ref = f"{board}.{row}{col}"
                if not covered(ref):
                    x, y = xy(ref)
                    s.rect(x - hs / 2, y - hs / 2, hs, hs, fill=hole, rx=1)
    for rail, u in RAIL_U.items():
        for col in RAIL_COLS:
            if not (rail.startswith("RG") and col >= 38):
                s.rect(X(col) - hs / 2, Y(u) - hs / 2, hs, hs, fill=hole, rx=1)
    marks = [1] + list(range(5, 61, 5)) + [63]
    for u in (2.8, 15.25, 19.8, 32.25):
        for col in marks:
            s.text(X(col), Y(u) + 3.8, str(col), size=10, anchor="middle", fill=MUTED)
    for board in "TD":
        for row, u in ROW_U.items():
            uu = u + BOARD_DU[board]
            s.text(X(63) - 0.95 * P, Y(uu) + 3.5, row, size=10, anchor="middle", fill=MUTED)
            s.text(X(1) + 0.95 * P, Y(uu) + 3.5, row, size=10, anchor="middle", fill=MUTED)
    for rail, u, label, color in (("RT−", 0.0, "GND", "#333333"), ("RT+", 1.0, "5V_MAIN", "#d62728"),
                                  ("RD−", 34.0, "GND_ESP", "#333333"), ("RD+", 35.0, "chưa dùng", MUTED)):
        s.text(left - 6, Y(u) + 4, f"{rail} {label}", size=11, anchor="end", weight="bold", fill=color)
    s.text(right + 8, Y(17.0) + 4, "RG", size=11, anchor="start", weight="bold", fill=MUTED)
    s.text(right + 8, Y(18.0) + 4, "chưa dùng", size=11, anchor="start", weight="bold", fill=MUTED)
    s.text(left - 6, Y(9.0) + 4, "TẤM TRÊN T", size=11, anchor="end", weight="bold", fill=MUTED)
    s.text(left - 6, Y(26.0) + 4, "TẤM DƯỚI D", size=11, anchor="end", weight="bold", fill=MUTED)


def draw_devkit(s: Svg):
    top, bot = Y(11.65), Y(22.35)
    left, right = X(63) - 1.0 * P, X(38.6)
    for u in (14.1, 17.9):
        s.rect(X(63) - 2.45 * P, Y(u), 2.4 * P, 2.0 * P, fill="#c9ccd1", stroke="#7d828a", sw=1, rx=4)
    s.rect(left, top, right - left, bot - top, fill="#1e1f22", stroke="#000000", sw=1.2, rx=3)
    sx0, sx1 = X(48.6), X(42.3)
    s.rect(sx0, Y(13.9), sx1 - sx0, Y(20.1) - Y(13.9), fill="#c9ccd1", stroke="#8a8f96", sw=1, rx=2)
    s.text((sx0 + sx1) / 2, Y(16.4), "ESP32-S3", size=11.5, anchor="middle", weight="bold", fill="#333")
    s.text((sx0 + sx1) / 2, Y(17.4), "WROOM-1", size=10, anchor="middle", fill="#333")
    s.text((sx0 + sx1) / 2, Y(18.4), "N16R8", size=10, anchor="middle", fill="#333")
    s.rect(X(41.9), Y(13.9), X(38.75) - X(41.9), Y(20.1) - Y(13.9), fill="#111", stroke="#444", sw=1)
    zig = []
    for k in range(7):
        uu = 14.3 + k * 0.8
        zig += [(X(41.4), Y(uu)), (X(39.2), Y(uu)), (X(39.2), Y(uu + 0.4)), (X(41.4), Y(uu + 0.4))]
    s.path("M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in zig), stroke="#b08d2c", sw=0.9)
    s.rect(X(57.6), Y(14.6), 1.2 * P, 1.2 * P, fill="#f2f2f2", stroke="#999", sw=0.8, rx=2)
    s.text(X(57.0), Y(16.6), "RGB", size=9.5, anchor="middle", fill="#ddd")
    for col, name in ((55.0, "BOOT"), (52.6, "RST")):
        s.rect(X(col) - 0.55 * P, Y(18.3), 1.1 * P, 1.1 * P, fill="#d9d9d9", stroke="#888", sw=0.8, rx=2)
        s.circle(X(col), Y(18.85), 0.22 * P, fill="#9a9a9a")
        s.text(X(col), Y(20.3), name, size=9.5, anchor="middle", fill="#ddd")
    s.text(X(60.6), Y(17.0), "U1", size=15, anchor="middle", weight="bold", fill="#ffffff")
    s.text(X(60.6), Y(18.0), "ESP32-S3-DevKitC-1", size=9.5, anchor="middle", fill="#cfcfcf")
    s.text(X(60.6), Y(18.8), "clone, 2 cổng USB-C", size=9.5, anchor="middle", fill="#cfcfcf")
    for side, names in (("L", DK_L), ("R", DK_R)):
        for n, name in enumerate(names, 1):
            x, y = xy(dk_pin_ref(side, n))
            s.circle(x, y, 0.17 * P, fill="#d4b14a", stroke="#7a6320", sw=0.8)
            ly = Y(13.35) if side == "L" else Y(20.95)
            s.text(x, ly, name, size=9.5, anchor="middle", fill="#ff8a80" if (side, n) in DK_NC else "#f1f1f1")
    s.rect(X(41.6), Y(13.3), X(38.4) - X(41.6), Y(20.6) - Y(13.3), stroke="#ef7d00", sw=1.2,
           extra='stroke-dasharray="4 3"')
    s.text(X(40.0), Y(21.35), "anten: không", size=9.5, anchor="middle", fill="#c25e00", halo=True)
    s.text(X(40.0), Y(21.95), "đi dây sát", size=9.5, anchor="middle", fill="#c25e00", halo=True)
    for ref in NC_HOLES:
        x, y = xy(ref)
        k = 0.17 * P
        s.line(x - k, y - k, x + k, y + k, stroke="#d62728", sw=1.7)
        s.line(x - k, y + k, x + k, y - k, stroke="#d62728", sw=1.7)


def body(s: Svg, a, b, fill, stroke, length, width, rx, band=False):
    (x1, y1), (x2, y2) = a, b
    s.line(x1, y1, x2, y2, stroke="#8d8d8d", sw=1.7)
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
    extra = (f'<rect x="{length / 2 - 0.3 * length:.1f}" y="{-width / 2:.1f}" width="{0.14 * length:.1f}" '
             f'height="{width:.1f}" fill="#d0d0d0"/>') if band else ""
    s.add(f'<g transform="translate({mx:.1f} {my:.1f}) rotate({ang:.1f})">'
          f'<rect x="{-length / 2:.1f}" y="{-width / 2:.1f}" width="{length:.1f}" height="{width:.1f}" '
          f'rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="1"/>{extra}</g>')
    return mx, my


ECAP_SIZE = {"1000µF/10V": (3.0, 2.2), "470µF/10V": (2.5, 1.75), "220µF/10V": (2.4, 1.45)}  # (dài, Ø) theo P


def draw_ecap(s: Svg, p: dict):
    (xp, yp), (xn, yn) = (xy(ref) for _, ref in p["pins"])
    length, dia = (v * P for v in ECAP_SIZE[p["value"]])
    lie = p["draw"]["lie"]
    if lie == "up":           # thân nằm ra ngoài mép trên
        cx0, base = (xp + xn) / 2, min(yp, yn) - 0.9 * P
        s.line(xp, yp, cx0 + 4, base, stroke="#8d8d8d", sw=1.6)
        s.line(xn, yn, cx0 - 4, base, stroke="#8d8d8d", sw=1.6)
        bx, by, bw, bh = cx0 - dia / 2, base - length, dia, length
        stripe = (bx, by, 0.28 * dia, bh)
        s.rect(bx, by, bw, bh, fill="#26467e", stroke="#14284a", sw=1.1, rx=5)
        s.rect(*stripe, fill="#9fb3d6", rx=3)
        s.text(cx0 + 3, by + bh / 2, f"{p['id']} {p['value'].split('/')[0]}", size=10, anchor="middle",
               weight="bold", fill="#ffffff", rotate=-90)
        return
    ym = (yp + yn) / 2
    if lie == "right":        # về phía cột nhỏ hơn (x lớn hơn)
        base = max(xp, xn) + 0.5 * P
        bx = base
    else:                     # về phía cột lớn hơn (x nhỏ hơn)
        base = min(xp, xn) - 0.5 * P
        bx = base - length
    s.line(xp, yp, base, ym - 4, stroke="#8d8d8d", sw=1.6)
    s.line(xn, yn, base, ym + 4, stroke="#8d8d8d", sw=1.6)
    by = ym - dia / 2
    s.rect(bx, by, length, dia, fill="#26467e", stroke="#14284a", sw=1.1, rx=5)
    s.rect(bx, by + dia - 0.26 * dia, length, 0.26 * dia, fill="#9fb3d6", rx=3)
    s.text(bx + length / 2, ym + 2, f"{p['id']} {p['value'].split('/')[0]}", size=10, anchor="middle",
           weight="bold", fill="#ffffff")


def draw_parts(s: Svg):
    for p in PARTS:
        kind = p["kind"]
        pts = [xy(ref) for _, ref in p["pins"]]
        if kind == "res":
            body(s, pts[0], pts[1], "#e7cf9d", "#8a6d3b", 0.78 * P, 0.33 * P, 3)
        elif kind == "ccap":
            body(s, pts[0], pts[1], "#f0b545", "#9a6a12", 0.45 * P, 0.36 * P, 4)
        elif kind == "diode":
            body(s, pts[0], pts[1], "#202020", "#000", 0.95 * P, 0.36 * P, 2, band=True)
        elif kind == "pptc":
            body(s, pts[0], pts[1], "#f2c12e", "#9a7a10", 1.15 * P, 0.55 * P, 6)
        elif kind == "ecap":
            draw_ecap(s, p)
        elif kind == "led":
            (x1, y1), (x2, y2) = pts
            s.line(x1, y1, x2, y2, stroke="#8d8d8d", sw=1.6)
            s.circle((x1 + x2) / 2, (y1 + y2) / 2, 0.5 * P, fill="#ef5350", stroke="#a11d1d", sw=1.2,
                     extra='fill-opacity="0.92"')
            s.circle(x1, y1, 0.11 * P, fill="#a11d1d")  # chân A
        elif kind == "mosfet":  # đế SOT-23 cắm đứng: nhìn từ trên chỉ thấy cạnh mạch
            (x0, y0), (x2, _) = pts[0], pts[2]
            s.rect(x2 - 0.5 * P, y0 - 0.27 * P, (x0 - x2) + 1.0 * P, 0.54 * P, fill="#5b3f8c",
                   stroke="#352158", sw=1, rx=2)
            for (name, _), (x, y) in zip(p["pins"], pts):
                s.text(x, y + 3.6, name, size=9.5, anchor="middle", weight="bold", fill="#ffffff")
        elif kind == "header":
            (x0, y0), (x2, _) = pts[0], pts[2]
            s.rect(x2 - 0.5 * P, y0 - 0.5 * P, (x0 - x2) + 1.0 * P, 1.0 * P, fill="#1b1b1b", rx=1.5)
            for (x, y), color in zip(pts, ("#6d4c41", "#d62728", "#ef8f00")):
                s.rect(x - 0.22 * P, y - 0.22 * P, 0.44 * P, 0.44 * P, fill=color, rx=1)
        elif kind == "tact":
            xs, ys = [x for x, _ in pts], [y for _, y in pts]
            cx0, cy0, half = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, 1.2 * P
            s.rect(cx0 - half, cy0 - half, 2 * half, 2 * half, fill="#3a3a3a", stroke="#111", sw=1, rx=3)
            s.circle(cx0, cy0, 0.7 * P, fill="#1a1a1a", stroke="#666", sw=1)
            for x, y in pts:
                s.circle(x, y, 0.13 * P, fill="#bdbdbd", stroke="#555", sw=0.8)
        elif kind == "ext":
            for (name, _), (x, y) in zip(p["pins"], pts):
                s.circle(x, y, 0.22 * P, stroke="#d62728" if name == "+" else "#333", sw=1.4,
                         extra='stroke-dasharray="2 1.5"')

    draw_amp(s)
    draw_mic(s)
    draw_lcd(s)
    draw_usbc(s)

    labels = {  # (cột, u, căn lề)
        "F1": (2.0, 6.75, "middle"), "C2": (7.0, -1.05, "middle"),
        "R7": (7.5, 3.9, "middle"), "R8": (11.5, 3.9, "middle"),
        "R9": (6.0, 7.1, "middle"), "R10": (10.0, 7.1, "middle"),
        "R3": (13.5, 7.15, "middle"), "R5": (14.5, 3.9, "middle"),
        "R_EYE1": (16.85, -1.45, "start"), "R_EYE2": (18.15, -1.45, "end"),
        "BL1": (16.5, 7.75, "middle"), "BL2": (18.15, 4.3, "end"),
        "R4": (20.5, 7.15, "middle"), "R6": (21.5, 3.9, "middle"),
        "D2": (23.15, 2.2, "end"), "RL": (24.85, 2.2, "start"), "BL3": (23.5, 7.75, "middle"),
        "C4": (35.65, 7.5, "start"),
        "D1": (60.3, 2.2, "middle"), "C6": (62.5, 4.05, "middle"),
        "SW2": (22.0, 23.15, "middle"), "SW3": (16.0, 23.15, "middle"),
        "R1": (42.65, 29.5, "start"), "R2": (45.35, 28.5, "end"), "C7": (41.65, 30.5, "start"),
    }
    for pid, (col, u, anchor) in labels.items():
        s.text(X(col), Y(u) + 3.6, pid, size=10.5, anchor=anchor, weight="bold", halo=True)
    s.text(X(2.2), Y(-1.7), "★ điểm sao", size=11, anchor="start", weight="bold", fill="#d62728", halo=True)
    s.text(X(21.4), Y(-1.15), "J5 tải ngoài", size=9.5, anchor="middle", fill=MUTED, halo=True)


def draw_amp(s: Svg):
    amp = get_part("U2")
    xl, xr, top = X(42.5), X(35.5), Y(-4.7)
    s.rect(xl, top, xr - xl, Y(4.0) - top, fill="#3f2a73", stroke="#24164a", sw=1.2, rx=4,
           extra='fill-opacity="0.95"')
    s.rect(xl + 26, top - 7, xr - xl - 52, 24, fill="#2e8b57", stroke="#1b5e3a", sw=1, rx=2)
    cx0 = (xl + xr) / 2
    s.text(cx0, top + 38, "U2", size=15, anchor="middle", weight="bold", fill="#ffffff")
    s.text(cx0, top + 55, "MAX98357A", size=11.5, anchor="middle", fill="#e6dcff")
    s.text(cx0, top + 71, "nằm đè lên ray RT", size=10, anchor="middle", fill="#cbbdf5")
    for name, ref in amp["pins"]:
        x, y = xy(ref)
        s.circle(x, y, 0.15 * P, fill="#d4b14a", stroke="#7a6320", sw=0.8)
        s.text(x + 3.5, Y(2.75), name, size=9.5, anchor="start", fill="#ffffff", rotate=-90)
    spx, spy = X(30.0), Y(-3.4)
    s.circle(spx, spy, 24, fill="#e9e9e9", stroke="#555", sw=1.4)
    s.circle(spx, spy, 11, fill="#bdbdbd", stroke="#777", sw=1)
    s.text(spx + 30, spy - 2, "LS1 loa", size=11, anchor="start", weight="bold", halo=True)
    s.text(spx + 30, spy + 12, "3W 8Ω", size=10, anchor="start", fill=MUTED, halo=True)
    s.path(f"M{xr - 30:.1f},{top + 2:.1f} C{xr:.1f},{top - 22:.1f} {spx + 5:.1f},{spy - 40:.1f} {spx:.1f},{spy - 24:.1f}",
           stroke="#d62728", sw=1.8)
    s.path(f"M{xr - 18:.1f},{top + 6:.1f} C{xr + 8:.1f},{top - 4:.1f} {spx - 30:.1f},{spy:.1f} {spx - 24:.1f},{spy:.1f}",
           stroke="#333", sw=1.8)
    s.text(cx0, top - 14, "OUT+ / OUT− ra loa, không nối GND", size=10, anchor="middle", fill=MUTED, halo=True)


def draw_mic(s: Svg):
    mic = dict(get_part("U3")["pins"])
    mcx, mcy = X(52.5), Y(-4.6)
    s.circle(mcx, mcy, 30, fill="#1f4fa3", stroke="#12306a", sw=1.2)
    s.circle(mcx, mcy, 6, fill="#d7dbe2", stroke="#777", sw=0.8)
    s.text(mcx, mcy - 12, "U3", size=12, anchor="middle", weight="bold", fill="#ffffff")
    s.text(mcx, mcy + 20, "INMP441", size=9.5, anchor="middle", fill="#dfe8ff")
    for k, line in enumerate(("đặt xa loa ≥30cm", "Dupont ≤20cm bện xoắn", "L/R nối GND trên module")):
        s.text(mcx - 38, mcy - 12 + k * 13, line, size=10, anchor="end", fill=MUTED, halo=True)
    start_x = {"SD": X(50.9), "SCK": X(51.7), "WS": X(52.5), "VDD": X(53.3), "GND": X(54.1)}
    lane = {"SD": -1.85, "SCK": -1.55, "WS": -1.25}
    for mid, pin_name, net in MIC_WIRES:
        hx, hy = xy(mic[pin_name])
        color = NET_COLOR[net_cat(net)]
        sx, sy = start_x[pin_name], mcy + 28
        if pin_name == "GND":
            pts = [(sx, sy), (sx, Y(-2.2)), (X(63.75), Y(-2.2)), (X(63.75), Y(10.9)), (hx, hy)]
            lab = (X(63.75) - 4, Y(5.5), "end")
        else:
            down = hx + 0.35 * P
            uu = lane.get(pin_name, -1.0) if pin_name != "VDD" else -2.2
            pts = [(sx, sy), (sx, Y(uu)), (down, Y(uu)), (down, hy - 0.45 * P), (hx, hy)]
            lab = (down, Y(5.0) if pin_name != "VDD" else Y(6.0), "middle")
        d = rounded_path(pts, 5)
        s.path(d, stroke="#ffffff", sw=4.8)
        s.path(d, stroke=color, sw=2.4, extra='stroke-dasharray="7 3"')
        s.circle(hx, hy, 0.15 * P, fill=color, stroke="#ffffff", sw=1)
        s.text(lab[0], lab[1], mid, size=10, anchor=lab[2], weight="bold", fill=color, halo=True)


def draw_lcd(s: Svg):
    lcd = get_part("U4")
    xl, yl = X(54.6), Y(31.5)
    s.rect(xl, yl - 0.25 * P, X(46.4) - xl, 0.5 * P, fill="#1b1b1b", stroke="#000", sw=1, rx=1.5)
    for name, ref in lcd["pins"]:
        x, _ = xy(ref)
        label = {"RESET": "RST"}.get(name, name)  # 5 chữ đè lên số cột
        s.text(x - 4, Y(31.05), label, size=9.5, anchor="end", fill="#4a2a6b", rotate=-90, halo=True)
    s.text(X(55.7), yl + 4, "U4: 8 dây Dupont", size=11, anchor="end", weight="bold", halo=True)


def draw_usbc(s: Svg):
    busb = dict(get_part("BUSB")["pins"])
    bx0, by0, bw, bh = X(1) + 2.0 * P, Y(2.4), 2.9 * P, 5.8 * P
    s.rect(bx0, by0, bw, bh, fill="#24314f", stroke="#121a2b", sw=1.2, rx=3)
    s.rect(bx0 + bw - 7, by0 + 1.7 * P, 14, 2.4 * P, fill="#c9ccd1", stroke="#7d828a", sw=1, rx=3)
    s.text(bx0 + 1.3 * P, by0 + 2.9 * P, "USB-C", size=10, anchor="middle", fill="#ffffff", rotate=-90)
    s.text(bx0 + bw / 2, by0 + bh + 16, "BUSB", size=11, anchor="middle", weight="bold", halo=True)
    for k, line in enumerate(("sink 5.1kΩ×2", "← sạc dự phòng", "5V ≥2.4A")):
        s.text(bx0 + bw / 2, by0 + bh + 31 + 13 * k, line, size=10, anchor="middle", fill=MUTED, halo=True)
    vx, vy = xy(busb["VBUS"])
    gx, gy = xy(busb["GND"])
    gnd = [(bx0, Y(3.4)), (X(0.25), Y(3.4)), (X(0.25), Y(-0.55)), (X(2.55), Y(-0.55)), (gx, gy)]
    for d, color in ((rounded_path([(bx0, vy), (vx, vy)]), "#d62728"), (rounded_path(gnd, 5), "#333333")):
        s.path(d, stroke="#ffffff", sw=5.8)
        s.path(d, stroke=color, sw=3.4)
    s.circle(vx, vy, 0.15 * P, fill="#d62728", stroke="#fff", sw=1)
    s.circle(gx, gy, 0.15 * P, fill="#333", stroke="#fff", sw=1)


def draw_wires(s: Svg):
    for w in WIRES:
        a, b = xy(w["a"]), xy(w["b"])
        color = NET_COLOR[net_cat(w["net"])]
        route = w["route"]
        if route is None:
            d = f"M{a[0]:.1f},{a[1]:.1f} L{b[0]:.1f},{b[1]:.1f}"
        elif isinstance(route, tuple):
            d = arc_path(a, b, route[1])
        else:
            d = rounded_path([a] + [(X(c), Y(u)) for c, u in route] + [b], 7)
        width = 3.6 if w["kind"] == "power" else 2.7
        s.path(d, stroke="#ffffff", sw=width + 2.6)
        s.path(d, stroke=color, sw=width)
        for x, y in (a, b):
            s.circle(x, y, 0.15 * P, fill=color, stroke="#ffffff", sw=1)
    for w in WIRES:  # nhãn vẽ sau cùng để không bị dây khác đè
        if w["lab"] is None:
            (x1, y1), (x2, y2) = xy(w["a"]), xy(w["b"])
            x, y = (x1 + x2) / 2, (y1 + y2) / 2
        else:
            x, y = X(w["lab"][0]), Y(w["lab"][1])
        s.text(x, y + 3.5, w["id"], size=10, anchor="middle", weight="bold",
               fill=NET_COLOR[net_cat(w["net"])], halo=True)


def draw_zones(s: Svg):
    for name, sub, c0, c1, cap in (("VÀO", "F1 2.5A", 0.55, 3.45, 2.0),
                                   ("SERVO", "J3 → S0 · J4 → S1", 4.55, 12.45, 8.5),
                                   ("Q1 · ĐÈN MẮT", "BL1, BL2 = LED thử", 12.55, 18.45, 15.0),
                                   ("Q2 · OUTPUT", "BL3 = LED thử", 19.55, 25.45, 20.8),
                                   ("NGUỒN ESP", "", 60.6, 63.45, None)):
        s.rect(X(c1), Y(3.0), X(c0) - X(c1), Y(8.05) - Y(3.0), fill="#fff7e0", stroke="#d9b45a", sw=1,
               rx=5, extra='stroke-dasharray="5 3" fill-opacity="0.55"')
        if cap is not None:
            s.text(X(cap), Y(12.95), name, size=11, anchor="middle", weight="bold", fill="#9a6a12", halo=True)
            s.text(X(cap), Y(13.85), sub, size=10, anchor="middle", fill="#9a6a12", halo=True)
    for name, c0, c1, u0, u1, cap in (("NÚT PTT · MẶT NẠ", 13.6, 24.4, 23.6, 28.4, (20.5, 20.95)),
                                      ("VSENSE", 41.55, 45.45, 27.0, 31.0, (43.5, 32.2)),
                                      ("LCD", 46.55, 54.45, 27.0, 32.0, None)):
        s.rect(X(c1), Y(u0), X(c0) - X(c1), Y(u1) - Y(u0), fill="#eef7ff", stroke="#7fa7d9", sw=1,
               rx=5, extra='stroke-dasharray="5 3" fill-opacity="0.55"')
        if cap:
            s.text(X(cap[0]), Y(cap[1]) + 3.8, name, size=10.5, anchor="middle", weight="bold",
                   fill="#2f5f9a", halo=True)


def draw_legend(s: Svg):
    x = 30
    for cat, text in (("5V", "5V"), ("3V3", "3V3"), ("GND", "GND"), ("I2S", "I2S"), ("SPI", "SPI (LCD)"),
                      ("PWM", "PWM"), ("ANALOG", "Analog"), ("BTN", "Nút")):
        s.line(x, 95, x + 26, 95, stroke=NET_COLOR[cat], sw=3.4)
        s.text(x + 32, 99.5, text, size=12.5)
        x += 32 + len(text) * 7.4 + 26
    s.line(x, 95, x + 26, 95, stroke="#555", sw=4.4)
    s.text(x + 32, 99.5, "đậm = 22AWG lõi đặc (nguồn)", size=12.5)
    x += 232
    s.line(x, 95, x + 26, 95, stroke="#555", sw=2.4, extra='stroke-dasharray="7 3"')
    s.text(x + 32, 99.5, "đứt = Dupont ra module ngoài", size=12.5)
    x += 238
    k = 0.17 * P
    s.line(x + 6 - k, 95 - k, x + 6 + k, 95 + k, stroke="#d62728", sw=1.7)
    s.line(x + 6 - k, 95 + k, x + 6 + k, 95 - k, stroke="#d62728", sw=1.7)
    s.text(x + 18, 99.5, "lỗ của chân KHÔNG được nối", size=12.5)


def draw_footer(s: Svg):
    y0, h = 1048, 250
    boxes = [
        (30, 370, "Ray & devkit", [
            "RT+ 5V_MAIN · RT− GND (công suất)",
            "RD− GND_ESP (GND của nút) · RD+ chưa dùng",
            "RG nằm dưới devkit: chưa dùng",
            "J1 ở hàng h tấm trên: còn lỗ f, g",
            "J3 ở hàng b tấm dưới: còn lỗ c, d, e",
            "Chân thứ n ở cột 41+n; cột 42 = đầu anten",
            "Lỗ a–e trên chân devkit (cột 42–61 tấm",
            "trên): không nối 5V xuống hàng f",
        ]),
        (415, 370, "Tách dòng servo khỏi devkit", [
            "Nguồn vào ở cột 3 (★), qua F1 lên RT.",
            "Servo cắm sát ★, rồi tới MOSFET, amply;",
            "devkit lấy nguồn xa nhất (D1 ở cột 61):",
            "dòng khởi động 1.5–2A của servo chỉ chạy",
            "qua đoạn ray cột 3–10, không qua dây",
            "GND/5V của devkit, mic, LCD.",
            "LCD, nút, mic lấy GND tín hiệu từ chân",
            "GND devkit (D.e62, RD−, T.g63), không từ RT−.",
        ]),
        (800, 375, "Trước khi cấp điện (mỗi bước)", [
            "Ray RT, RD: thông mạch cột 3 ↔ cột 61.",
            "Ω RT+↔RT−, 3V3↔GND tăng dần, không ≈0Ω.",
            "Cực tụ C1, C3, C5 (vạch sáng = −).",
            "Vạch D1 về T.a62, vạch D2 về RT+@24.",
            "VCC của LCD là 3V3 (W5), không phải 5V.",
            "Không có dây nào ở lỗ ×.",
            "Lắp theo B1 → B8 (breadboard.md mục 5),",
            "đạt bước trước mới sang bước sau.",
        ]),
        (1190, 380, "Điểm đo", [
            "5V_MAIN: RT+@3 (★) · 5V_ESP: T.d62",
            "3V3: T.g42 · GND tín hiệu: D.c42",
            "VSENSE: D.j45 = 5V_MAIN ÷ 2",
            "Firmware: jarvis-helmet (LCD tấm TN:",
            "jarvis-helmet-noips). Bảng chân giữ",
            "nguyên HW2: không sửa config.h.",
            "Lệnh thử MCP: tools/servo_calibrate.py",
            "(breadboard.md mục 5).",
        ]),
    ]
    for x, w, title, lines in boxes:
        s.rect(x, y0, w, h, fill="#f7f7f8", stroke="#d9dce1", sw=1, rx=8)
        s.text(x + 14, y0 + 27, title, size=14.5, weight="bold")
        for k, line in enumerate(lines):
            s.text(x + 14, y0 + 53 + k * 24, line, size=12.5)


def build_svg() -> str:
    s = Svg()
    s.add('<?xml version="1.0" encoding="UTF-8"?>')
    s.add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" '
          f'height="{HEIGHT}" font-family="sans-serif" role="img" aria-labelledby="ttl desc">')
    s.add('<title id="ttl">Jarvis Helmet – bản thử breadboard (Rev B)</title>')
    s.add('<desc id="desc">Hai breadboard 63 cột và ba thanh ray như ảnh; ESP32-S3-DevKitC-1 cắm hàng h tấm '
          'trên và hàng b tấm dưới, cột 42–63. Ray trên là 5V_MAIN/GND công suất cho servo, MOSFET, amply; '
          'ray dưới là GND_ESP cho nút; LCD lấy 3V3/GND thẳng từ chân devkit. '
          'Sinh bởi tools/hardware/breadboard_layout.py.</desc>')
    s.rect(0, 0, WIDTH, HEIGHT, fill="#ffffff")
    s.text(30, 42, "Jarvis Helmet – Bản thử trên breadboard", size=26, weight="bold")
    s.text(30, 66, "Bảng chân giữ nguyên HW2: firmware jarvis-helmet chạy không sửa · nguồn, tụ, diode, MOSFET "
           "như HW1/HW3 · nhìn từ trên, cột 1 bên phải như số in trên board", size=14, fill=MUTED)
    s.text(1570, 38, "Rev B · 2026-10-09", size=14, anchor="end", weight="bold")
    s.text(1570, 56, "Chưa cắm thử trên phần cứng thật", size=12, anchor="end", fill=MUTED)
    s.text(1570, 72, "Bảng lỗ đầy đủ: docs/hardware/breadboard.md", size=12, anchor="end", fill=MUTED)
    draw_legend(s)
    draw_board(s)
    draw_zones(s)
    draw_devkit(s)
    draw_parts(s)
    draw_wires(s)
    draw_footer(s)
    s.add("</svg>")
    return "\n".join(s.out) + "\n"


# ---------------------------------------------------------------------------
# Bảng markdown
# ---------------------------------------------------------------------------
def md_devkit() -> str:
    rows = ["| Cột | J1 – tấm trên (chân ở `T.h`) | Dùng (lỗ `T.f`, `T.g`) "
            "| J3 – tấm dưới (chân ở `D.b`) | Dùng (lỗ `D.c`…`D.e`) |",
            "|---|---|---|---|---|"]

    def use(side, n):
        if (side, n) in DK_NC:
            return f"**không nối** ({DK_NC[(side, n)]})"
        return DK_FUNC.get((side, n)) or DK_SPARE.get((side, n), "–")

    for n in range(1, 23):
        rows.append(f"| {41 + n} | `DK-L{n}` {dk_label(DK_L[n - 1])} | {use('L', n)} | "
                    f"`DK-R{n}` {dk_label(DK_R[n - 1])} | {use('R', n)} |")
    return "\n".join(rows)


def md_parts() -> str:
    rows = ["| Bước | Ký hiệu | Linh kiện | Giá trị | Chân → lỗ | Ghi chú |", "|---|---|---|---|---|---|"]
    for p in sorted(PARTS, key=lambda q: q["stage"]):
        if p["kind"] in ("res", "ccap", "pptc"):
            pins = f"`{show(p['pins'][0][1])}` – `{show(p['pins'][1][1])}`"
        else:
            pins = " · ".join(f"{name.replace('-', '−')} `{show(ref)}`" for name, ref in p["pins"])
        rows.append(f"| {STAGES[p['stage']].split(' ·')[0]} | {p['id']} | {p['name']} | {p['value']} | "
                    f"{pins} | {p['note']} |")
    return "\n".join(rows)


KIND_TEXT = {"power": "22AWG lõi đặc", "jumper": "dây cắm", "dupont": "Dupont cái–đực"}


def md_wires() -> str:
    mic = get_part("U3")
    pins = dict(mic["pins"])
    # (bước, dây trước mic, số thứ tự, mã, từ, tới, mạng, loại, ghi chú)
    entries = [(w["stage"], 0, int(w["id"][1:]), w["id"], f"`{show(w['a'])}`", w["b"], w["net"], w["kind"],
                w["note"]) for w in WIRES]
    entries += [(mic["stage"], 1, k, mid, f"U3 {pin_name}", pins[pin_name], net, "dupont", "")
                for k, (mid, pin_name, net) in enumerate(MIC_WIRES)]
    rows = ["| Bước | Dây | Từ | Tới | Mạng | Loại | Ghi chú |", "|---|---|---|---|---|---|---|"]
    for stage, _, _, wid, src, dst, net, kind, note in sorted(entries):
        rows.append(f"| {STAGES[stage].split(' ·')[0]} | {wid} | {src} | `{show(dst)}` | `{net}` | "
                    f"{KIND_TEXT[kind]} | {note} |")
    return "\n".join(rows)


def update_md(text: str) -> str:
    for key, table in (("devkit", md_devkit()), ("parts", md_parts()), ("wires", md_wires())):
        pattern = re.compile(rf"(<!-- BEGIN:{key} -->\n).*?(\n<!-- END:{key} -->)", re.S)
        if not pattern.search(text):
            raise SystemExit(f"breadboard.md thiếu dấu <!-- BEGIN:{key} --> / <!-- END:{key} -->")
        text = pattern.sub(lambda m: m.group(1) + table + m.group(2), text)
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description="Kiểm tra và vẽ bố trí breadboard của Jarvis Helmet")
    ap.add_argument("--check", action="store_true", help="chỉ kiểm tra, không ghi file")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # console Windows mặc định cp1252
    errors = check()
    if errors:
        print("LỖI bố trí:", file=sys.stderr)
        for e in errors:
            print("  -", e, file=sys.stderr)
        return 1
    print(f"OK: {len(PARTS)} linh kiện, {len(WIRES)} dây nhảy + {len(MIC_WIRES)} dây mic; "
          "không trùng lỗ, không chập mạng")
    if args.check:
        return 0
    SVG_OUT.write_text(build_svg(), encoding="utf-8")
    print(f"ghi {SVG_OUT.relative_to(ROOT)}")
    if MD_DOC.exists():
        MD_DOC.write_text(update_md(MD_DOC.read_text(encoding="utf-8")), encoding="utf-8")
        print(f"cập nhật bảng trong {MD_DOC.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
