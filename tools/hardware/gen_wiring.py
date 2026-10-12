# -*- coding: utf-8 -*-
"""Generate docs/hardware/wiring.svg (system wiring diagram) for Jarvis Helmet."""
import sys
from xml.sax.saxutils import escape

OUT = sys.argv[1]
W, H = 1600, 1060

C5V = "#d62728"; C3V3 = "#ef7d00"; CGND = "#333333"; CI2S = "#1f6fd1"
CSPI = "#8e44ad"; CPWM = "#2a9d3a"; CANA = "#c79a00"; CDIG = "#0f8f9a"
TXT = "#1d1d1f"; MUTED = "#5f6368"; BOX = "#f6f8fb"; BOXS = "#4a4f57"

el = []
def add(s): el.append(s)

def t(x, y, s, size=13, anchor="start", weight="normal", fill=TXT, italic=False):
    st = ' font-style="italic"' if italic else ''
    add(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" fill="{fill}"{st}>{escape(s)}</text>')

def line(x1, y1, x2, y2, c, w=2.2, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ''
    add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{c}" stroke-width="{w}" stroke-linecap="round"{d}/>')

def pl(pts, c, w=2.2, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ''
    p = " ".join(f"{x},{y}" for x, y in pts)
    add(f'<polyline points="{p}" fill="none" stroke="{c}" stroke-width="{w}" stroke-linejoin="round" stroke-linecap="round"{d}/>')

def rect(x, y, w, h, fill=BOX, stroke=BOXS, sw=1.5, rx=6, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ''
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')

def dot(x, y, c, r=3.6):
    add(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{c}"/>')

def pin(x, y, c):
    add(f'<circle cx="{x}" cy="{y}" r="3.2" fill="#ffffff" stroke="{c}" stroke-width="1.8"/>')

def res_h(x1, x2, y, c, label=None, ly=None, lanchor="middle"):
    """horizontal resistor body between x1..x2 on wire y"""
    add(f'<rect x="{x1}" y="{y-6}" width="{x2-x1}" height="12" fill="#ffffff" stroke="{c}" stroke-width="2"/>')
    if label:
        t((x1+x2)/2 if lanchor == "middle" else x1, ly if ly else y-11, label, 12, lanchor, fill=TXT)

def res_v(x, y1, y2, c, label=None, lx=None, lanchor="end"):
    add(f'<rect x="{x-6}" y="{y1}" width="12" height="{y2-y1}" fill="#ffffff" stroke="{c}" stroke-width="2"/>')
    if label:
        t(lx if lx else x-11, (y1+y2)/2+4, label, 12, lanchor)

def cap_v(x, ytop, ybot, c, polar=False, label=None, lx=None, lanchor="start", plus_dy=-6):
    """vertical capacitor from ytop to ybot (wire ends), plates in the middle.
    plus_dy: baseline offset of the '+' mark from the middle (move it off a nearby wire)."""
    m = (ytop+ybot)/2
    line(x, ytop, x, m-4, c, 2)
    line(x, m+4, x, ybot, CGND, 2)
    line(x-9, m-4, x+9, m-4, c, 2.6)
    if polar:
        add(f'<path d="M {x-9} {m+6} Q {x} {m+2} {x+9} {m+6}" fill="none" stroke="{CGND}" stroke-width="2.6"/>')
        t(x-14, m+plus_dy, "+", 12, "middle", fill=c)
    else:
        line(x-9, m+4, x+9, m+4, CGND, 2.6)
    if label:
        t(lx if lx is not None else x+13, m+4, label, 12, lanchor)

def gnd(x, y, label=None, lx=None, anchor="start"):
    line(x, y, x, y+6, CGND, 2)
    line(x-9, y+6, x+9, y+6, CGND, 2)
    line(x-6, y+10, x+6, y+10, CGND, 2)
    line(x-3, y+14, x+3, y+14, CGND, 2)
    if label:
        t(lx if lx is not None else x+12, y+15, label, 12, anchor, fill=MUTED)

def flag(x, y, name, c, direction="left", w=None):
    """net tag; (x,y) = attachment point. direction: where the tag body extends."""
    w = w or (len(name)*7.4 + 16)
    h = 18
    if direction == "left":
        pts = [(x, y), (x-8, y-h/2), (x-w, y-h/2), (x-w, y+h/2), (x-8, y+h/2)]
        tx = x - 8 - (w-8)/2
    else:
        pts = [(x, y), (x+8, y-h/2), (x+w, y-h/2), (x+w, y+h/2), (x+8, y+h/2)]
        tx = x + 8 + (w-8)/2
    p = " ".join(f"{a},{b}" for a, b in pts)
    add(f'<polygon points="{p}" fill="#ffffff" stroke="{c}" stroke-width="1.8"/>')
    t(tx, y+4.5, name, 12, "middle", "bold", c)

def diode_v(x, ytop, ybot, c, up=False, label=None, lx=None, lanchor="end"):
    """diode between ytop..ybot; default conducts downward (anode top)."""
    m = (ytop+ybot)/2
    line(x, ytop, x, m-8, c, 2); line(x, m+8, x, ybot, c, 2)
    if not up:
        add(f'<polygon points="{x-9},{m-8} {x+9},{m-8} {x},{m+7}" fill="#ffffff" stroke="{c}" stroke-width="2"/>')
        line(x-9, m+8, x+9, m+8, c, 2.6)
    else:
        add(f'<polygon points="{x-9},{m+8} {x+9},{m+8} {x},{m-7}" fill="#ffffff" stroke="{c}" stroke-width="2"/>')
        line(x-9, m-8, x+9, m-8, c, 2.6)
    if label:
        t(lx if lx is not None else x-14, m+4, label, 12, lanchor)

def nmos(x, y, label=None):
    """N-MOSFET; gate input at (x-30, y); drain at (x+12, y-30); source at (x+12, y+30)."""
    line(x-30, y, x-10, y, CPWM, 2.2)
    line(x-10, y-14, x-10, y+14, TXT, 2.4)
    for a, b in ((-17, -9), (-4, 4), (9, 17)):
        line(x-4, y+a, x-4, y+b, TXT, 2.6)
    pl([(x-4, y-13), (x+12, y-13), (x+12, y-30)], TXT, 2)
    pl([(x-4, y+13), (x+12, y+13), (x+12, y+30)], TXT, 2)
    line(x-4, y, x+12, y, TXT, 2)
    line(x+12, y, x+12, y+13, TXT, 2)
    add(f'<polygon points="{x-3},{y} {x+5},{y-4} {x+5},{y+4}" fill="{TXT}"/>')
    if label:
        t(x+22, y-2, label[0], 12, "start", "bold")
        t(x+22, y+13, label[1], 12, "start", fill=MUTED)

def button_v(x, ytop, ybot, c, label=None):
    m = (ytop+ybot)/2
    line(x, ytop, x, m-9, c, 2); line(x, m+9, x, ybot, CGND, 2)
    dot(x, m-9, c, 2.6); dot(x, m+9, CGND, 2.6)
    line(x-12, m-12, x-12, m+12, TXT, 2)            # actuator plate
    line(x-12, m, x-22, m, TXT, 2)
    line(x-22, m-6, x-22, m+6, TXT, 2)
    if label:
        t(x-28, m+4, label, 12, "end")

# ---------------------------------------------------------------- canvas
add(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>')
t(30, 42, "Jarvis Helmet – Sơ đồ đấu dây hệ thống", 26, weight="bold")
t(30, 66, "ESP32-S3-DevKitC-1 N16R8 · bảng chân theo HW2 · nguồn sạc dự phòng 5V ≥2.4A nối hình sao (HW1) · công suất theo HW3", 14, fill=MUTED)
t(1570, 38, "Rev C · 2026-10-12", 14, "end", "bold")
t(1570, 56, "Chưa đo trên phần cứng thật · thay đổi Rev C: wiring.md đầu file", 12, "end", fill=MUTED)
t(1570, 72, "Chi tiết, tọa độ PCB: docs/hardware/wiring.md", 12, "end", fill=MUTED)

# legend
lx = 30; ly = 100
items = [("5V", C5V), ("3V3", C3V3), ("GND", CGND), ("I2S", CI2S), ("SPI (LCD)", CSPI),
         ("PWM", CPWM), ("Analog (VSENSE)", CANA), ("Vào số (nút)", CDIG)]
for name, c in items:
    line(lx, ly, lx+28, ly, c, 4)
    t(lx+34, ly+4.5, name, 13)
    lx += 34 + len(name)*7.6 + 26
flag(lx+70, ly, "5V_AUDIO", C5V, "left")
t(lx+78, ly+4.5, "cờ = nối theo tên lưới", 13)
lx += 78 + 170
gnd(lx, ly-9)
t(lx+14, ly+4.5, "GND về điểm sao, mỗi nhánh dây riêng", 13)

# ---------------------------------------------------------------- devkit
DX1, DX2, DY1, DY2 = 650, 950, 150, 770
rect(DX1, DY1, DX2-DX1, DY2-DY1, fill="#eef4fc", stroke="#2f4f7f", sw=2, rx=10)
t(800, 174, "U1 · ESP32-S3-DevKitC-1", 16, "middle", "bold")
t(800, 192, "N16R8 (WROOM-1-N16R8)", 13, "middle", fill=MUTED)
t(800, 224, "L4 / R17 = chân số 4 hàng trái / số 17 hàng phải", 12, "middle", fill=MUTED)

left_pins = [
    ("3V3", "L1", 205, C3V3), ("GPIO4 · WS", "L4", 258, CI2S), ("GPIO5 · SCK", "L5", 279, CI2S),
    ("GPIO6 · SD", "L6", 300, CI2S), ("GPIO7 · DIN", "L7", 438, CI2S), ("GPIO15 · BCLK", "L8", 460, CI2S),
    ("GPIO16 · LRC", "L9", 482, CI2S), ("GPIO12 · SD", "L18", 510, CI2S), ("GPIO10 · PTT", "L16", 580, CDIG), ("GPIO11 · MẶT NẠ", "L17", 640, CDIG),
    ("GPIO1 · VSENSE", "R4", 690, CANA), ("5V (5V_ESP)", "L21", 725, C5V), ("GND", "L22", 750, CGND),
]
right_pins = [
    ("GPIO21 · SCLK", "R18", 240, CSPI), ("GPIO47 · MOSI", "R17", 265, CSPI), ("GPIO39 · RESET", "R9", 290, CSPI),
    ("GPIO40 · DC", "R8", 315, CSPI), ("GPIO41 · CS", "R7", 340, CSPI), ("GPIO42 · LED", "R6", 365, CPWM),
    ("GPIO17 · SERVO S0", "L10", 432, CPWM), ("GPIO18 · SERVO S1", "L11", 505, CPWM),
    ("GPIO8 · GATE Q1", "L12", 700, CPWM), ("GPIO9 · GATE Q2", "L15", 735, CPWM),
]
for name, tag, y, c in left_pins:
    line(DX1-20, y, DX1, y, c, 2.2); pin(DX1-20, y, c)
    add(f'<text x="{DX1+10}" y="{y+4.5}" font-size="13" fill="{TXT}">{escape(name)}<tspan fill="{MUTED}" font-size="12"> {tag}</tspan></text>')
for name, tag, y, c in right_pins:
    line(DX2, y, DX2+20, y, c, 2.2); pin(DX2+20, y, c)
    add(f'<text x="{DX2-10}" y="{y+4.5}" font-size="13" text-anchor="end" fill="{TXT}"><tspan fill="{MUTED}" font-size="12">{tag} </tspan>{escape(name)}</text>')
t(800, 384, "Có sẵn trên devkit, không đi dây:", 12, "middle", "bold", MUTED)
t(800, 400, "GPIO0 = nút BOOT · GPIO48 = LED RGB", 12, "middle", fill=MUTED)
t(800, 416, "(DevKitC-1 v1.1: LED ở GPIO38)", 12, "middle", fill=MUTED)
t(800, 540, "Cấm dùng: 3, 19, 20, 26–37, 43–46", 12, "middle", fill=MUTED)
t(800, 556, "Còn trống: 2, 13, 14, 38", 12, "middle", fill=MUTED)

# ---------------------------------------------------------------- INMP441
rect(380, 150, 180, 165)
t(390, 170, "U3 · INMP441", 14, weight="bold")
t(390, 186, "mic I2S (qua J8)", 12, fill=MUTED)
mic = [("VDD", 195, C3V3), ("GND", 216, CGND), ("L/R", 237, CGND), ("WS", 258, CI2S), ("SCK", 279, CI2S), ("SD", 300, CI2S)]
for n, y, c in mic:
    pin(560, y, c); t(550, y+4.5, n, 12, "end")
for y, c in ((258, CI2S), (279, CI2S), (300, CI2S)):
    line(563, y, DX1-23, y, c)
pl([(DX1-23, 205), (595, 205), (595, 195), (563, 195)], C3V3)
dot(595, 195, C3V3)
pl([(595, 195), (595, 138), (1100, 138), (1100, 175), (1127, 175)], C3V3)
t(860, 133, "3V3 → U4 VCC (J7.1)", 12, "middle", fill=C3V3)
pl([(563, 216), (574, 216), (574, 237)], CGND, 2)
line(563, 237, 574, 237, CGND, 2)
gnd(574, 237)
t(390, 305, "L/R nối GND = kênh trái", 12, "start", fill=MUTED)
t(605, 318, "J8 (5 chân)", 12, "middle", fill=MUTED)

# ---------------------------------------------------------------- MAX98357A
rect(380, 395, 180, 165)
t(390, 415, "U2 · MAX98357A", 14, weight="bold")
t(390, 431, "amply I2S trên PCB", 12, fill=MUTED)
for n, y, c in (("DIN", 438, CI2S), ("BCLK", 460, CI2S), ("LRC", 482, CI2S)):
    pin(560, y, c); t(550, y+4.5, n, 12, "end")
    line(563, y, DX1-23, y, c)
# SD_MODE from GPIO12 through R13: low = off, high = on (left channel)
pin(560, 510, CI2S); t(550, 514.5, "SD · bật/tắt", 12, "end")
line(563, 510, 571, 510, CI2S); res_h(571, 617, 510, CI2S, "R13 2.2k", 500); line(617, 510, DX1-23, 510, CI2S)
# GAIN tied to VIN: 6 dB, never left floating
pin(560, 535, C5V); t(550, 539.5, "GAIN = VIN (6 dB)", 12, "end")
line(563, 535, 578, 535, C5V, 2); t(582, 539.5, "5V", 11, fill=C5V)
for n, y, c in (("VIN", 450, C5V), ("GND", 472, CGND), ("OUT+", 505, TXT), ("OUT−", 530, TXT)):
    pin(380, y, c); t(390, y+4.5, n, 12)
# VIN + caps
line(377, 450, 214, 450, C5V)
flag(214, 450, "5V_AUDIO", C5V, "left")
line(377, 472, 268, 472, CGND)
cap_v(296, 450, 472, C5V, polar=True, plus_dy=1)   # short leads: keep '+' off the 5V_AUDIO wire
cap_v(330, 450, 472, C5V)
dot(296, 450, C5V); dot(330, 450, C5V); dot(296, 472, CGND); dot(330, 472, CGND)
gnd(268, 472)
t(258, 480, "GND_AUDIO", 12, "end", fill=MUTED)
t(313, 441, "C3 470µ  C4 100n", 12, "middle")
# speaker
pl([(377, 505), (262, 505)], TXT); pl([(377, 530), (262, 530)], TXT)
add('<rect x="246" y="503" width="16" height="29" fill="#ffffff" stroke="#1d1d1f" stroke-width="2"/>')
add('<polygon points="246,503 222,489 222,546 246,532" fill="#ffffff" stroke="#1d1d1f" stroke-width="2"/>')
t(214, 512, "LS1", 13, "end", "bold")
t(214, 528, "loa 3W 8Ω", 12, "end")
t(320, 498, "J10", 12, "middle", fill=MUTED)
t(214, 556, "BTL: không nối OUT− xuống GND", 12, "end", "bold", C5V)

# ---------------------------------------------------------------- buttons
for y, sw, cap, name in ((580, "SW2 PTT", "C8", "BTN_PTT"), (640, "SW3 mặt nạ", "C9", "BTN_MASK")):
    line(DX1-23, y, 440, y, CDIG)
    dot(520, y, CDIG); dot(440, y, CDIG)
    cap_v(520, y, y+26, CDIG, label=cap+" 100n")
    gnd(520, y+26)
    button_v(440, y, y+30, CDIG, label=sw)
    gnd(440, y+30)
t(600, 572, "J9", 12, "middle", fill=MUTED)
t(388, 712, "pull-up nội; bấm = nối GND", 12, "start", fill=MUTED)

# VSENSE / 5V / GND pins on devkit left
line(DX1-23, 690, 600, 690, CANA)
flag(600, 690, "VSENSE", CANA, "left")
line(DX1-23, 725, 600, 725, C5V)
flag(600, 725, "5V_ESP", C5V, "left")
pl([(DX1-23, 750), (612, 750)], CGND)
gnd(612, 750, "GND_ESP", 600, "end")

# ---------------------------------------------------------------- LCD
rect(1130, 150, 200, 240)
t(1320, 172, "U4 · LCD 2.4\"", 14, "end", "bold")
t(1320, 190, "ST7789 · MSP2401", 12, "end", fill=MUTED)
lcd = [("1 VCC", 175, C3V3), ("2 GND", 200, CGND), ("7 SCK", 240, CSPI), ("6 SDI", 265, CSPI),
       ("4 RESET", 290, CSPI), ("5 DC", 315, CSPI), ("3 CS", 340, CSPI), ("8 LED", 365, CPWM)]
for n, y, c in lcd:
    pin(1130, y, c); t(1140, y+4.5, n, 12)
for y, c in ((240, CSPI), (265, CSPI), (290, CSPI), (315, CSPI), (340, CSPI), (365, CPWM)):
    line(DX2+23, y, 1127, y, c)
line(1127, 200, 1112, 200, CGND, 2); gnd(1112, 200)
t(1050, 231, "J7", 12, "middle", fill=MUTED)
notes = ["J7.n = chân n của module (1:1):", "dùng 1–8; bỏ trống 9 SDO (MISO),", "10–14 cảm ứng, khe thẻ SD",
         "Tấm IPS: LCD_ST7789_240X320", "Tấm TN: _NO_IPS (build -noips)", "Ngang 320×240, SPI mode 0",
         "Ảnh nhiễu: R11 33Ω nối tiếp SCK", "RESET = GPIO39 (thay GPIO45)"]
for i, s in enumerate(notes):
    t(1345, 214 + i*18, s, 12, fill=TXT if i in (0, 3) else MUTED, weight="bold" if i in (0, 3) else "normal")

# ---------------------------------------------------------------- servos
for (y, rn, jn, mn, side) in ((432, "R7 220Ω", "J3", "M1 · servo S0", "trái"), (505, "R8 220Ω", "J4", "M2 · servo S1", "phải")):
    line(DX2+23, y, 1000, y, CPWM)
    res_h(1000, 1046, y, CPWM, rn, y-10)
    line(1046, y, 1150, y, CPWM)
    rect(1150, y-10, 22, 48, fill="#ffffff", stroke=BOXS, sw=1.6, rx=3)
    t(1161, y-14, jn, 12, "middle", "bold")
    for k, (yy, c) in enumerate(((y, CPWM), (y+14, C5V), (y+28, CGND))):
        dot(1150, yy, c, 2.6); dot(1172, yy, c, 2.6)
        line(1172, yy, 1240, yy, c, 2.2)
    flag(1150, y+14, "5V_SERVO", C5V, "left")
    line(1150, y+28, 1132, y+28, CGND, 2)
    gnd(1132, y+28)
    rect(1240, y-12, 170, 52, fill="#fbfbf3")
    t(1250, y+4, mn, 13, weight="bold")
    t(1250, y+20, f"MG90S ({side})", 12, fill=MUTED)
    t(1250, y+34, "cam · đỏ · nâu", 12, fill=MUTED)
t(1102, 562, "đất ở J3/J4 = GND_SERVO", 12, "middle", fill=MUTED)
for i, s in enumerate(["S1 lắp đối xứng: đảo", "chiều bằng hiệu chỉnh", "(closed > open, FW2).", "Giật khi khởi động:", "thêm R9/R10 10kΩ."]):
    t(1420, 440 + i*17, s, 12, fill=MUTED)

# ---------------------------------------------------------------- eyes driver (Q1)
YG = 700
line(DX2+23, YG, 1005, YG, CPWM)
res_h(1005, 1050, YG, CPWM, "R3 100Ω", YG-10)
line(1050, YG, 1115, YG, CPWM)
dot(1090, YG, CPWM)
nmos(1145, YG, ("Q1 AO3400A", "đèn mắt"))
res_v(1090, YG+8, YG+32, TXT, "R5 100k", 1080)
line(1090, YG, 1090, YG+8, CPWM, 2)
pl([(1090, YG+32), (1090, YG+40), (1157, YG+40)], CGND, 2)
line(1157, YG+30, 1157, YG+40, CGND, 2)
gnd(1157, YG+40, "GND_PWR")
# drain -> EYES_DRV line. One chain: 5V_EYES -> R_EYE -> eye module E1 (both LEDs in
# parallel inside, as on the module's CR2032 holder) -> EYES_DRV.
XE = 1440
pl([(1157, YG-30), (1157, 670), (XE, 670), (XE, 654)], TXT, 2.2)
flag(1250, 580, "5V_EYES", C5V, "left")
pl([(1250, 580), (XE, 580), (XE, 600)], C5V)
res_h(1335, 1385, 580, C5V, "R_EYE")
rect(XE-45, 600, 90, 54, fill="#fff8e6", stroke="#b8860b", sw=1.6, rx=4)
line(XE, 600, XE, 607, C5V, 2)
line(XE-22, 607, XE+22, 607, C5V, 2)          # + rail (battery holder +)
line(XE-22, 647, XE+22, 647, TXT, 2)          # − rail (battery holder −)
line(XE, 647, XE, 654, TXT, 2)
for xl in (XE-22, XE+22):                     # the two LEDs, in parallel
    line(xl, 607, xl, 621, C5V, 2)
    add(f'<polygon points="{xl-7},{621} {xl+7},{621} {xl},{633}" fill="#ffffff" stroke="#b8860b" stroke-width="1.8"/>')
    line(xl-7, 633, xl+7, 633, "#b8860b", 2.2)
    line(xl, 633, xl, 647, TXT, 2)
    for dy in (0, 6):                         # light arrows
        line(xl+9, 624+dy, xl+15, 618+dy, "#b8860b", 1.4)
t(XE+8, 597, "+", 12, fill=MUTED)
t(XE+8, 667, "−", 12, fill=MUTED)
t(XE+52, 616, "E1", 12, weight="bold")
t(XE+52, 631, "2 LED", 12, fill=MUTED)
t(XE+52, 646, "song song", 12, fill=MUTED)
for x, lab, yy in ((1285, "J6.1", 580), (1285, "J6.2", 670)):
    add(f'<rect x="{x-5}" y="{yy-5}" width="10" height="10" fill="#ffffff" stroke="{BOXS}" stroke-width="1.6"/>')
    t(x, yy-9, lab, 12, "middle", fill=MUTED)
t(1300, 690, "EYES_DRV (drain Q1)", 12, "start", fill=MUTED)
t(1240, 756, "Module mắt: tháo pin CR2032 trước khi nối 5V", 12, fill=MUTED)
t(1240, 772, "Một R_EYE cho cả module, chọn theo đo (mục 4.2)", 12, fill=MUTED)
t(1240, 788, "R5 kéo xuống: đèn tắt khi khởi động / reset", 12, fill=MUTED)

# ---------------------------------------------------------------- power band
rect(20, 798, 1035, 254, fill="#fff7f6", stroke="#e3b5b0", sw=1.4, rx=10)
t(32, 818, "NGUỒN & PHÂN PHỐI (HW1) – nối đất hình sao", 15, weight="bold")
# J1
rect(40, 870, 56, 84, fill="#ffffff", stroke=BOXS, sw=1.6, rx=4)
t(68, 864, "J1", 13, "middle", "bold")
t(52, 894, "+", 14, "middle", "bold", C5V); t(52, 940, "−", 14, "middle", "bold", CGND)
dot(96, 890, C5V); dot(96, 936, CGND)
t(32, 978, "← sạc dự phòng 5V ≥2.4A", 12)
t(32, 994, "USB-A cắt / USB-C sink", 12, fill=MUTED)
t(32, 1010, "(5.1kΩ ở CC1 và CC2)", 12, fill=MUTED)
t(32, 1040, "D1*: nối tắt nếu devkit có diode ở chân 5V", 11, fill=MUTED)
# F1, SW1
line(96, 890, 106, 890, C5V)
add(f'<rect x="106" y="882" width="40" height="16" fill="#ffffff" stroke="{C5V}" stroke-width="2"/>')
line(111, 896, 141, 884, C5V, 1.4)
t(126, 875, "F1 2.5A", 12, "middle")
# Q3: high-side P-MOSFET, drain from F1, source to SW1, gate to GND = reverse-polarity guard
line(146, 890, 160, 890, C5V)
add(f'<rect x="160" y="881" width="30" height="18" fill="#ffffff" stroke="{BOXS}" stroke-width="1.6" rx="2"/>')
t(175, 894, "Q3", 11, "middle", "bold")
line(175, 899, 175, 936, CGND, 1.6); dot(175, 936, CGND)
t(170, 912, "AO3401A", 11, "end", fill=MUTED)
t(170, 926, "chống ngược", 11, "end", fill=MUTED)
line(190, 890, 206, 890, C5V)
dot(206, 890, C5V, 3); dot(244, 890, C5V, 3)
line(209, 889, 240, 876, C5V, 2.4)
t(226, 868, "SW1 ≥3A", 12, "middle")
t(226, 912, "(qua J2)", 12, "middle", fill=MUTED)
line(244, 890, 276, 890, C5V)
# star bars
add(f'<rect x="276" y="836" width="10" height="74" fill="{C5V}"/>')
t(270, 852, "★ 5V_MAIN", 12, "end", "bold", C5V)
line(96, 936, 276, 936, CGND)
add(f'<rect x="276" y="928" width="10" height="114" fill="{CGND}"/>')
t(270, 958, "★ GND_STAR", 12, "end", "bold", CGND)

cols = {  # name: (x, y5V, yGND)
    "VS": (330, 902, None), "ESP": (470, 890, 1000), "AUD": (595, 878, 1012),
    "SRV": (720, 866, 1024), "EYE": (845, 854, None), "J5": (965, 842, None),
}
def fan5v(key, thin=False):
    x, y5, _ = cols[key]
    pl([(286, y5), (x, y5), (x, 930)], C5V, 1.6 if thin else 2.2)
def fangnd(key, xr):
    x, _, yg = cols[key]
    pl([(xr, 985), (xr, yg), (286, yg)], CGND, 2.2)

# col VSENSE
fan5v("VS", thin=True)
res_v(330, 930, 954, C5V, "R1", 318)
line(330, 954, 330, 962, CANA, 2)
dot(330, 962, CANA)
line(330, 962, 385, 962, CANA, 2)
res_v(330, 968, 992, CGND, "R2", 318)
line(330, 962, 330, 968, CANA, 2)
cap_v(360, 962, 1000, CANA, label="C7", lx=373)
dot(360, 962, CANA)
line(330, 992, 330, 1000, CGND, 2)
dot(330, 1000, CGND); dot(360, 1000, CGND)
flag(385, 962, "VSENSE", CANA, "right")
t(338, 917, "VSENSE_TOP", 12, "start", "bold", C5V)
t(338, 932, "100kΩ 1%", 12, "start", fill=MUTED)
t(395, 990, "→ GPIO1", 12, fill=MUTED)

# col 5V_ESP
fan5v("ESP")
diode_v(470, 930, 958, C5V)
dot(470, 958, C5V)
line(470, 958, 500, 958, C5V, 2)
cap_v(470, 958, 985, C5V, polar=True)
cap_v(500, 958, 985, C5V)
line(470, 985, 500, 985, CGND, 2)
fangnd("ESP", 470)
t(482, 905, "5V_ESP", 12, "start", "bold", C5V)
t(482, 920, "→ U1 5V (L21)", 12, fill=MUTED)
t(482, 935, "D1* 1N5819/SS34", 12, fill=MUTED)
t(510, 966, "C5 470µ", 12); t(510, 982, "C6 100n", 12)
t(478, 996, "GND_ESP", 12, fill=MUTED)

# col 5V_AUDIO
fan5v("AUD")
dot(595, 930, C5V)
line(595, 930, 625, 930, C5V, 2)
cap_v(595, 930, 985, C5V, polar=True)
cap_v(625, 930, 985, C5V)
line(595, 985, 625, 985, CGND, 2)
fangnd("AUD", 595)
t(603, 893, "5V_AUDIO", 12, "start", "bold", C5V)
t(603, 908, "→ U2 VIN", 12, fill=MUTED)
t(635, 950, "C3 470µF", 12); t(635, 966, "C4 100nF", 12)
t(603, 1008, "GND_AUDIO", 12, fill=MUTED)

# col 5V_SERVO
fan5v("SRV")
dot(720, 930, C5V)
line(720, 930, 750, 930, C5V, 2)
cap_v(720, 930, 985, C5V, polar=True)
cap_v(750, 930, 985, C5V)
line(720, 985, 750, 985, CGND, 2)
fangnd("SRV", 720)
t(728, 881, "5V_SERVO", 12, "start", "bold", C5V)
t(728, 896, "→ J3.2, J4.2", 12, fill=MUTED)
t(728, 1020, "GND_SERVO", 12, fill=MUTED)
t(760, 950, "C1 1000µF", 12); t(760, 966, "C2 100nF", 12)

# col 5V_EYES
fan5v("EYE")
t(853, 869, "5V_EYES", 12, "start", "bold", C5V)
t(853, 884, "→ J6.1", 12, fill=MUTED)
t(853, 899, "→ R_EYE → E1", 12, fill=MUTED)
line(845, 930, 845, 940, C5V, 2)
add(f'<polygon points="839,940 851,940 845,950" fill="{C5V}"/>')

# col 5V_MAIN -> J5
fan5v("J5")
t(973, 857, "5V_MAIN", 12, "start", "bold", C5V)
t(973, 872, "→ J5.1, D2", 12, fill=MUTED)
line(965, 930, 965, 940, C5V, 2)
add(f'<polygon points="959,940 971,940 965,950" fill="{C5V}"/>')


# ---------------------------------------------------------------- spare output (Q2)
rect(1070, 798, 510, 254, fill="#f6fbf6", stroke="#b5d6b8", sw=1.4, rx=10)
t(1082, 818, "OUTPUT DỰ PHÒNG (HW3) – J5, ≤1A", 15, weight="bold")
YQ = 950
pl([(DX2+23, 735), (1062, 735), (1062, YQ), (1100, YQ)], CPWM)
res_h(1100, 1145, YQ, CPWM, "R4 100Ω", YQ-10)
line(1145, YQ, 1215, YQ, CPWM)
dot(1185, YQ, CPWM)
nmos(1245, YQ, ("Q2 AO3400A", "output dự phòng"))
res_v(1185, YQ+8, YQ+32, TXT, "R6 100k", 1175)
line(1185, YQ, 1185, YQ+8, CPWM, 2)
pl([(1185, YQ+32), (1185, YQ+42), (1257, YQ+42)], CGND, 2)
line(1257, YQ+30, 1257, YQ+42, CGND, 2)
dot(1257, YQ+42, CGND)
# GND_PWR return to star (below everything)
pl([(1257, YQ+42), (1257, 1036), (286, 1036)], CGND, 2.2)
t(1265, 1032, "GND_PWR → điểm sao", 12, fill=MUTED)
# 5V_MAIN in, D2, J5, load
flag(1200, 862, "5V_MAIN", C5V, "left")
line(1200, 862, 1430, 862, C5V)
pl([(1257, YQ-30), (1257, 905), (1430, 905)], TXT, 2.2)
dot(1350, 862, C5V); dot(1350, 905, TXT)
diode_v(1350, 862, 905, C5V, up=True, label="D2 1N5819", lx=1340)
rect(1430, 850, 24, 68, fill="#ffffff", stroke=BOXS, sw=1.6, rx=3)
t(1442, 845, "J5", 12, "middle", "bold")
t(1442, 866, "+", 13, "middle", "bold", C5V); t(1442, 911, "−", 13, "middle", "bold", TXT)
pl([(1454, 862), (1480, 862)], C5V); pl([(1454, 905), (1480, 905)], TXT)
rect(1480, 846, 88, 76, fill="#ffffff", stroke=BOXS, sw=1.4, rx=4, dash="5 4")
t(1524, 874, "TẢI ≤1A", 12, "middle", "bold")
t(1524, 890, "relay, quạt,", 12, "middle", fill=MUTED)
t(1524, 906, "motor…", 12, "middle", fill=MUTED)
t(1300, 920, "OUT_DRV", 12, "start", fill=MUTED)
t(1350, 1000, "D2: vạch catot về +5V", 12, fill=MUTED)
t(1350, 1016, "Chưa có trong ngân sách dòng HW1", 12, fill=MUTED)

svg = (f'<?xml version="1.0" encoding="UTF-8"?>\n'
       f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
       f'font-family="sans-serif" role="img" aria-labelledby="ttl desc">\n'
       f'<title id="ttl">Jarvis Helmet – sơ đồ đấu dây hệ thống (Rev C)</title>\n'
       f'<desc id="desc">ESP32-S3-DevKitC-1 ở giữa; INMP441, MAX98357A + loa, nút bấm, LCD 2.4" ST7789, 2 servo, '
       f'mạch đèn mắt Q1, output dự phòng Q2 và khối nguồn nối hình sao. Bảng chân theo HW2.</desc>\n'
       + "\n".join(e for e in el if e) + "\n</svg>\n")
with open(OUT, "w", encoding="utf-8", newline="\n") as f:
    f.write(svg)
print("wrote", OUT, len(el), "elements")
