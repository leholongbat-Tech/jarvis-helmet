# -*- coding: utf-8 -*-
"""Generate docs/hardware/perfboard-7x9.svg – top view of the 27x35 hole layout."""
import sys
from xml.sax.saxutils import escape

OUT = sys.argv[1]
P = 24                      # px per 2.54 mm
X0, Y0 = 100, 160           # centre of hole A:1
NC, NR = 27, 35
W, H = 1290, 1070
MARGIN = 0.78               # board edge distance from outer hole centre, in pitches (~2 mm)

C5V = "#d62728"; CGND = "#333333"; CANA = "#c79a00"; CPWM = "#2a9d3a"; TXT = "#1d1d1f"; MUTED = "#5f6368"
ORANGE = "#c45a00"; CDIG = "#0f8f9a"; CSPI = "#8e44ad"   # same net colours as wiring.svg

COLS = [chr(ord('A')+i) for i in range(26)] + ["AA"]
def ci(c):
    return COLS.index(c) + 1
def X(c):                   # c: letter or float column index
    return X0 + ((ci(c) if isinstance(c, str) else c) - 1) * P
def Y(r):
    return Y0 + (r - 1) * P

el = []
def add(s): el.append(s)
HALO = 'paint-order="stroke" stroke="#ffffff" stroke-width="3.5" stroke-linejoin="round"'
def t(x, y, s, size=12, anchor="start", weight="normal", fill=TXT, halo=True, rot=None):
    h = HALO if halo else ""
    r = f' transform="rotate({rot} {x} {y})"' if rot is not None else ""
    add(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" fill="{fill}" {h}{r}>{escape(s)}</text>')
def rect(x, y, w, h, fill="none", stroke=TXT, sw=1.5, rx=2, dash=None, op=None):
    d = f' stroke-dasharray="{dash}"' if dash else ''
    o = f' fill-opacity="{op}"' if op is not None else ''
    add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" fill="{fill}"{o} stroke="{stroke}" stroke-width="{sw}"{d}/>')
def circ(x, y, r, fill="none", stroke=TXT, sw=1.5, op=None):
    o = f' fill-opacity="{op}"' if op is not None else ''
    add(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{fill}"{o} stroke="{stroke}" stroke-width="{sw}"/>')
def poly(pts, c, w, dash=None, halo=False, op=None):
    p = " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in pts)
    d = f' stroke-dasharray="{dash}"' if dash else ''
    o = f' stroke-opacity="{op}"' if op else ''
    if halo:
        add(f'<polyline points="{p}" fill="none" stroke="#ffffff" stroke-width="{w+4}" stroke-linejoin="round" stroke-linecap="round"/>')
    add(f'<polyline points="{p}" fill="none" stroke="{c}" stroke-width="{w}" stroke-linejoin="round" stroke-linecap="round"{d}{o}/>')

def zone(c1, r1, c2, r2, fill, label=None):
    x1, y1 = X(c1) - P/2, Y(r1) - P/2
    x2, y2 = X(c2) + P/2, Y(r2) + P/2
    rect(x1, y1, x2-x1, y2-y1, fill=fill, stroke="none", sw=0, rx=4)

def pinsq(c, r, fill=TXT):
    add(f'<rect x="{X(c)-4.2:.1f}" y="{Y(r)-4.2:.1f}" width="8.4" height="8.4" fill="{fill}"/>')

def header_m(c1, c2, r, label=None, lx=None, ly=None, anchor="middle", vertical=False, r2=None, pin1=None):
    """male header: horizontal c1..c2 on row r, or vertical on column c1 rows r..r2.
    pin1: column of pin 1 for a horizontal header (default c1)."""
    if not vertical:
        x1, x2 = X(c1) - P*0.45, X(c2) + P*0.45
        rect(x1, Y(r)-P*0.45, x2-x1, P*0.9, fill="#e9e9e9", stroke=TXT, sw=1.4, rx=2)
        for k in range(ci(c1), ci(c2)+1):
            pinsq(k, r)
        p1 = pin1 if pin1 else c1
        add(f'<rect x="{X(p1)-6:.1f}" y="{Y(r)-6:.1f}" width="12" height="12" fill="none" stroke="{C5V}" stroke-width="1.6"/>')
    else:
        y1, y2 = Y(r) - P*0.45, Y(r2) + P*0.45
        rect(X(c1)-P*0.45, y1, P*0.9, y2-y1, fill="#e9e9e9", stroke=TXT, sw=1.4, rx=2)
        for k in range(r, r2+1):
            pinsq(c1, k)
        add(f'<rect x="{X(c1)-6:.1f}" y="{Y(r)-6:.1f}" width="12" height="12" fill="none" stroke="{C5V}" stroke-width="1.6"/>')
    if label:
        t(lx, ly, label, 12, anchor, "bold")

def header_f(c, r1, r2, fill="#3a3a3a"):
    rect(X(c)-P*0.46, Y(r1)-P*0.46, P*0.92, Y(r2)-Y(r1)+P*0.92, fill=fill, stroke="#111", sw=1.2, rx=2)
    for k in range(r1, r2+1):
        add(f'<rect x="{X(c)-3:.1f}" y="{Y(k)-3:.1f}" width="6" height="6" fill="#d9d9d9"/>')

def res_stand(c1, r1, c2, r2, label=None, lx=None, ly=None, anchor="middle", optional=False):
    xa, ya, xb, yb = X(c1), Y(r1), X(c2), Y(r2)
    dash = "3 2" if optional else None
    add(f'<line x1="{xa:.1f}" y1="{ya:.1f}" x2="{xb:.1f}" y2="{yb:.1f}" stroke="#7a5c2e" stroke-width="2.4"/>')
    # body sits on first hole
    circ(xa, ya, 6.2, fill="#f2d7a8", stroke="#7a5c2e", sw=1.6)
    if optional:
        add(f'<circle cx="{xa:.1f}" cy="{ya:.1f}" r="9" fill="none" stroke="{MUTED}" stroke-width="1.1" stroke-dasharray="3 2"/>')
    dot(xb, yb, "#7a5c2e", 3)
    if label:
        t(lx, ly, label, 12, anchor, "bold")

def cap_cer(c1, r1, c2, r2, label=None, lx=None, ly=None, anchor="middle"):
    xa, ya, xb, yb = X(c1), Y(r1), X(c2), Y(r2)
    mx, my = (xa+xb)/2, (ya+yb)/2
    add(f'<line x1="{xa:.1f}" y1="{ya:.1f}" x2="{xb:.1f}" y2="{yb:.1f}" stroke="#1f6fd1" stroke-width="2.2"/>')
    if xa == xb:
        rect(mx-6, my-5, 12, 10, fill="#9cc3f0", stroke="#1f5fb0", sw=1.4, rx=4)
    else:
        rect(mx-5, my-6, 10, 12, fill="#9cc3f0", stroke="#1f5fb0", sw=1.4, rx=4)
    dot(xa, ya, "#1f5fb0", 3); dot(xb, yb, "#1f5fb0", 3)
    if label:
        t(lx, ly, label, 12, anchor, "bold")

def cap_el(cp, rp, cn, rn, dia_mm, label=None, value=None, lx=None, ly=None, anchor="middle"):
    xa, ya, xb, yb = X(cp), Y(rp), X(cn), Y(rn)
    mx, my = (xa+xb)/2, (ya+yb)/2
    r = dia_mm / 2 / 2.54 * P
    circ(mx, my, r, fill="#e7ddf5", stroke="#5b3d8a", sw=1.8, op=0.85)
    dot(xa, ya, C5V, 3.6); dot(xb, yb, CGND, 3.6)
    t(xa + (7 if xa == xb else 0), ya - (0 if xa == xb else 7) + (4 if xa == xb else 0), "+", 13, "start" if xa == xb else "middle", "bold", C5V)
    if label:
        t(lx, ly, label, 12, anchor, "bold")
    if value:
        t(lx, ly+14, value, 12, anchor)

def diode_lay(ck, rk, ca, ra, label=None, lx=None, ly=None, anchor="middle"):
    xk, yk, xa, ya = X(ck), Y(rk), X(ca), Y(ra)
    add(f'<line x1="{xk:.1f}" y1="{yk:.1f}" x2="{xa:.1f}" y2="{ya:.1f}" stroke="#777" stroke-width="2"/>')
    mx, my = (xk+xa)/2, (yk+ya)/2
    L = 5.2/2.54*P; Wd = 2.7/2.54*P
    if xk == xa:
        rect(mx-Wd/2, my-L/2, Wd, L, fill="#2b2b2b", stroke="#111", sw=1, rx=3)
        s = -1 if yk < ya else 1
        add(f'<rect x="{mx-Wd/2:.1f}" y="{(my + s*L/2 - (5 if s > 0 else 0)) if s > 0 else (my - L/2):.1f}" width="{Wd:.1f}" height="5" fill="#d9d9d9"/>')
    dot(xk, yk, "#555", 3); dot(xa, ya, "#555", 3)
    if label:
        t(lx, ly, label, 12, anchor, "bold")

def mosfet(cg, r, label, lx, ly, anchor="middle"):
    """adapter with G,S,D on cg, cg+1, cg+2 of row r; board extends upward."""
    g = ci(cg)
    x1, x2 = X(g) - P*0.55, X(g+2) + P*0.55
    rect(x1, Y(r)-P*0.95, x2-x1, P*1.4, fill="#1f7a3a", stroke="#0e4d22", sw=1.4, rx=3, op=0.9)
    rect(X(g+1)-7, Y(r)-P*0.75, 14, 9, fill="#222", stroke="#000", sw=1, rx=1)
    for k, n in enumerate("GSD"):
        dot(X(g+k), Y(r), "#f0f0f0", 3.4)
        # pin letter sits lower-left of its pin so the G/S pull-down bridges below stay visible
        t(X(g+k) - 5, Y(r)+16, n, 12, "end", "bold", "#0e4d22")
    t(X(g) - 1, Y(r) - P*0.42, label, 12, "middle", "bold", "#ffffff", halo=False)

def domino(c1, r1, c2, r2, x1, y1, x2, y2, label, lines):
    rect(X(x1), Y(y1), X(x2)-X(x1), Y(y2)-Y(y1), fill="#5aa7d6", stroke="#1e5f86", sw=1.6, rx=3, op=0.9)
    for (c, r) in ((c1, r1), (c2, r2)):
        circ(X(c), Y(r), 5.5, fill="#c9d6de", stroke="#1e5f86", sw=1.4)
    return

def dot(x, y, c, r=3):
    add(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{c}"/>')

def star(x, y, r, c):
    import math
    pts = []
    for i in range(10):
        a = -math.pi/2 + i*math.pi/5
        rr = r if i % 2 == 0 else r*0.45
        pts.append(f"{x+rr*math.cos(a):.1f},{y+rr*math.sin(a):.1f}")
    add(f'<polygon points="{" ".join(pts)}" fill="{c}" stroke="#ffffff" stroke-width="1.2"/>')

# ------------------------------------------------------------------ canvas
add(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>')
add('<defs><pattern id="hatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
    '<line x1="0" y1="0" x2="0" y2="8" stroke="#d62728" stroke-width="2" stroke-opacity="0.35"/></pattern>'
    '<pattern id="hatchS" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)">'
    '<line x1="0" y1="0" x2="0" y2="7" stroke="#c45a00" stroke-width="1.6" stroke-opacity="0.35"/></pattern></defs>')
t(30, 38, "Jarvis Helmet – Bố trí PCB đục lỗ 7×9cm (lưới 27×35, nhìn từ mặt linh kiện)", 22, weight="bold", halo=False)
t(30, 62, "Rev C · 2026-10-12 · tọa độ cột:hàng khớp docs/hardware/wiring.md mục 5 · bản dự kiến, chưa hàn thử", 13, fill=MUTED, halo=False)
t(30, 80, "Cạnh 9cm theo chiều dọc. Hàng 1 = cạnh trên (header cáp), hàng 35 = cạnh dưới (khối nguồn). Bước lỗ 2.54mm.", 13, fill=MUTED, halo=False)

# board
bx1, by1 = X(1) - MARGIN*P, Y(1) - MARGIN*P
bx2, by2 = X(NC) + MARGIN*P, Y(NR) + MARGIN*P
rect(bx1, by1, bx2-bx1, by2-by1, fill="#f6eedb", stroke="#b59b62", sw=2, rx=6)

# zones
zone("A", 2, "H", 27, "#dbe9fb")          # audio
zone("D", 22, "H", 27, "#fde3df")         # 5V_ESP block
zone("T", 2, "AA", 35, "#e2f2e3")         # power stage / servo
zone("A", 28, "S", 35, "#fde3df")         # power block
zone("I", 3, "S", 27, "#e9eef6")          # devkit
# keep-out under antenna + reserved column S
x1, y1 = X("I") - P/2, Y(1) - P/2
add(f'<rect x="{x1:.1f}" y="{y1:.1f}" width="{11*P:.1f}" height="{2*P:.1f}" fill="url(#hatch)" stroke="{C5V}" stroke-width="1.2" stroke-dasharray="4 3"/>')
xs = X("S") - P/2
add(f'<rect x="{xs:.1f}" y="{Y(3)-P/2:.1f}" width="{P:.1f}" height="{22*P:.1f}" fill="url(#hatchS)" stroke="none"/>')

# holes
for c in range(1, NC+1):
    for r in range(1, NR+1):
        add(f'<circle cx="{X(c):.1f}" cy="{Y(r):.1f}" r="3.3" fill="#ffffff" stroke="#b8a273" stroke-width="1.3"/>')

# column / row labels
for c in range(1, NC+1):
    t(X(c), by1 - 12, COLS[c-1], 12, "middle", "bold", MUTED, halo=False)
    t(X(c), by2 + 20, COLS[c-1], 12, "middle", "bold", MUTED, halo=False)
for r in range(1, NR+1):
    t(bx1 - 10, Y(r) + 4, str(r), 12, "end", "bold", MUTED, halo=False)
    t(bx2 + 10, Y(r) + 4, str(r), 12, "start", "bold", MUTED, halo=False)

# ------------------------------------------------------------------ bottom-side wires (drawn under parts)
R5V = dict(c=C5V); RG = dict(c=CGND)
DASH_P = "9 5"; DASH_B = "4 3"
def pw(pts, c, w=4.0, dash=DASH_P, halo=True):
    poly(pts, c, w, dash, halo)
# input chain + buses (bare copper)
pw([("B", 34), ("B", 30)], C5V)
pw([("D", 30), ("F", 30), ("F", 32)], C5V)                 # F1 out -> Q3 drain
pw([("E", 32), ("E", 33), ("F", 33), ("F", 34)], C5V)      # Q3 source -> J2.1 (SW1)
pw([("H", 34), ("H", 31), ("K", 31)], C5V)
pw([("D", 34), ("D", 35), ("K", 35), ("K", 33)], CGND)
pw([("K", 31), ("Q", 31)], C5V, 6.5, "12 4")
pw([("K", 33), ("Q", 33)], CGND, 6.5, "12 4")
# servo pair (short path)
pw([("Q", 31), ("X", 31), ("X", 29), ("Y", 29), ("Y", 23)], C5V, 5.5)
pw([("Q", 33), ("Z", 33), ("Z", 23)], CGND, 5.5)
# other branches (insulated 22AWG)
pw([("M", 31), ("M", 27), ("D", 27)], C5V, 3)
pw([("L", 31), ("L", 28), ("B", 28), ("B", 4), ("C", 4), ("E", 4), ("E", 7), ("G", 7)], C5V, 3)
pw([("O", 31), ("O", 28), ("T", 28), ("T", 9), ("AA", 9)], C5V, 3)
pw([("P", 31), ("P", 30), ("U", 30), ("U", 13), ("X", 13)], C5V, 3)
pw([("O", 33), ("O", 34), ("AA", 34), ("AA", 22), ("X", 22), ("X", 20)], CGND, 3)
pw([("N", 33), ("N", 25), ("I", 25), ("I", 24)], CGND, 3)
pw([("L", 33), ("L", 32), ("J", 32), ("J", 29), ("C", 29), ("C", 5)], CGND, 3)
pw([("C", 5), ("D", 5), ("D", 8), ("G", 8)], CGND, 3)
# short bridges / bare links (thin)
def br(pts, c):
    poly(pts, c, 2.2, DASH_B, False)
br([("D", 23), ("I", 23)], C5V)
br([("F", 24), ("I", 24)], CGND)
br([("D", 32), ("D", 34)], CGND)      # Q3 gate -> J1- (reverse-polarity P-FET)
br([("G", 10), ("E", 10), ("E", 7)], C5V)   # U2 GAIN -> VIN (6 dB)
br([("F", 3), ("H", 3), ("H", 1)], CGND)
br([("U", 1), ("U", 4), ("T", 4), ("T", 3)], CGND)   # J7.2 GND; T:1 is J7.1 = 3V3
br([("T", 3), ("R", 3)], CGND)
br([("R", 6), ("V", 6)], CANA)
br([("X", 13), ("AA", 13), ("AA", 15)], C5V)
br([("X", 16), ("Y", 16), ("Y", 20)], TXT)
br([("Y", 20), ("Z", 20), ("AA", 20), ("AA", 17)], TXT)
br([("Z", 10), ("AA", 10)], TXT)
br([("W", 10), ("X", 10)], CPWM); br([("V", 20), ("W", 20)], CPWM)
br([("W", 23), ("X", 23)], CPWM); br([("W", 26), ("X", 26)], CPWM)
# button caps C8/C9 to J9.1/J9.2 (GPIO10/11 wires land on F:2/G:2)
br([("F", 1), ("F", 2)], CDIG); br([("G", 1), ("G", 2)], CDIG)
# optional parts: R9/R10 pull-downs on the servo signal side, R11 in series with the LCD SCK
br([("W", 23), ("W", 24)], CPWM); br([("W", 26), ("W", 27)], CPWM)
br([("Z", 1), ("Z", 2)], CSPI)

# ------------------------------------------------------------------ parts
# top headers
header_m("A", "E", 1, "J8 MIC 5P", X("C"), by1 - 30)
header_m("F", "H", 1, "J9 NÚT 3P", X("G"), by1 - 30)
header_m("T", "AA", 1, "J7 LCD 8P (= chân 1–8 module)", X("W") + P/2, by1 - 30)
cap_cer("F", 2, "F", 3); cap_cer("G", 2, "G", 3)
t(X("F") + P/2, Y(4) + 6, "C8 C9", 12, "middle", "bold")
res_stand("Z", 2, "Z", 3, "R11*", X("Y") + 4, Y(2.6), "end", optional=True)

# devkit
dx1, dx2 = X("H") + P/2, X("R") + P/2
dy1, dy2 = Y(0.6), Y(26.4)
rect(dx1, dy1, dx2-dx1, dy2-dy1, fill="#3b6ec8", stroke="#2f4f7f", sw=2, rx=8, dash="8 5", op=0.10)
header_f("I", 3, 24); header_f("R", 3, 24, fill="#5a4a3a")
for cx in ("K", "O"):
    rect(X(cx) - 4, Y(25.7), 1.6*P, 0.9*P, fill="#c0c0c0", stroke="#555", sw=1.2, rx=2)
t(X("L") + 8, Y(26.95), "USB", 12, "middle", "bold", MUTED)
t(X("P") + 8, Y(26.95), "USB", 12, "middle", "bold", MUTED)
t(X("M") + P/2, Y(9), "U1", 16, "middle", "bold")
t(X("M") + P/2, Y(9.75), "ESP32-S3-DevKitC-1", 12, "middle", "bold")
t(X("M") + P/2, Y(10.5), "cắm trên 2 hàng rào cái 1×22", 12, "middle")
t(X("M") + P/2, Y(11.3), "thân devkit: kích thước dự kiến", 12, "middle", fill=ORANGE)
t(X("M") + P/2, Y(14), "Dưới thân devkit: chỉ đi dây", 12, "middle", fill=MUTED)
t(X("M") + P/2, Y(14.75), "mặt dưới, không đặt linh kiện", 12, "middle", fill=MUTED)
t(X("M") + P/2, Y(1.3), "VÙNG CẤM – ANTEN (hàng 1–2)", 12, "middle", "bold", C5V)
t(X("J") + 2, Y(3) + 4, "L1 3V3", 12, "start", "bold")
t(X("J") + 2, Y(23) + 4, "L21 5V", 12, "start", "bold", C5V)
t(X("J") + 2, Y(24) + 4, "L22 GND", 12, "start", "bold")
t(X("Q") + 6, Y(3) + 4, "R1 GND", 12, "end", "bold")
t(X("Q") + 6, Y(6) + 4, "R4 GPIO1", 12, "end", "bold", CANA)
t(X("Q") + 6, Y(24) + 4, "R22", 12, "end", "bold")
t(X("R"), Y(25.15) + 2, "R: dự kiến", 12, "middle", "bold", ORANGE)
t(X("S") + 5, Y(13), "cột S để trống (header phải nếu 10 bước)", 12, "middle", "bold", ORANGE, rot=90)

# left zone – audio
cap_el("C", 4, "C", 5, 8.0, "C3", "470µF", X("A") - 4, Y(2.55), "start")
mx1, mx2 = X("A") - 0.62*P, X("G") + 0.42*P
rect(mx1, Y(6.5), mx2-mx1, 7*P, fill="#1f6fd1", stroke="#1f5fb0", sw=1.8, rx=5, dash="7 4", op=0.10)
header_f("G", 7, 13)
for k, n in enumerate(["VIN", "GND", "SD", "GAIN", "DIN", "BCLK", "LRC"]):
    t(X("F") - 9, Y(7+k) + 4, n, 12, "end", "bold", "#174a8c")
cap_cer("F", 7, "F", 8)
t(X(2.3), Y(8.3), "U2 MAX98357A", 12, "middle", "bold")
t(X(2.3), Y(9.0), "thân module", 12, "middle", fill=ORANGE)
t(X(2.3), Y(9.7), "(dự kiến)", 12, "middle", fill=ORANGE)
header_m("A", "A", 15, vertical=True, r2=16)
res_stand("F", 16, "F", 17, "R13", X("F") + 10, Y(16.6), "start")   # GPIO12 -> SD_MODE
t(X("B") - 2, Y(15.5) + 4, "J10 LOA", 12, "start", "bold")
t(X("D"), Y(18.6), "VÙNG ÂM THANH", 13, "middle", "bold", "#174a8c")
t(X("D"), Y(19.35), "(I2S, mic, loa)", 12, "middle", fill="#174a8c")
# 5V_ESP block
diode_lay("D", 23, "D", 27, "D1", X("B") + 6, Y(25) + 4, "middle")
t(X("B") + 6, Y(25.7) + 4, "1N5819*", 12, "middle")
cap_el("F", 23, "F", 24, 6.3, "C5", None, X("F"), Y(21.75), "middle")
t(X("F"), Y(21.75) + 14, "470µF", 12, "middle")
cap_cer("H", 23, "H", 24, "C6", X("H"), Y(25.45), "middle")

# right zone – VSENSE
res_stand("T", 4, "T", 6, "R2", X("S") - 2, Y(4.9), "end")
cap_cer("U", 4, "U", 6, "C7", X("U") + 2, Y(7.6), "middle")
res_stand("V", 6, "V", 8, "R1", X("W"), Y(7.35), "start")
t(X("V") + 9, Y(8.65) + 2, "← N:31", 12, "start", "bold", C5V)
t(X("Y"), Y(4.6), "VSENSE", 12, "middle", "bold", CANA)
t(X("Y"), Y(5.3), "R1/R2 100k 1%", 12, "middle")
# eyes
res_stand("V", 10, "W", 10, "R3", X("V"), Y(11.45), "middle")
mosfet("X", 10, "Q1", X("Y"), Y(8.3))
# gate/source links to the pull-down, drawn over the adapter so they stay visible
br([("X", 10), ("X", 11)], CPWM); br([("Y", 10), ("Y", 11)], CGND)
res_stand("X", 11, "Y", 11, "R5", X("Z") + 4, Y(11.45), "start")
header_m("AA", "AA", 9, vertical=True, r2=10)
t(X("AA") + 8, Y(8.3), "J6 MẮT", 12, "end", "bold")
# spare
diode_lay("X", 13, "X", 16, "D2", X("W") - 2, Y(14.7) + 4, "end")
domino("AA", 15, "AA", 17, 25.62, 13.03, 28.42, 18.97, "J5", None)
t(X(27.05), Y(13.85) + 4, "J5", 13, "middle", "bold", "#0b3b57")
t(X(26.25), Y(16), "dự phòng", 12, "middle", "bold", "#0b3b57", rot=-90)
t(X("AA") + 9, Y(15) + 5, "+", 14, "start", "bold", C5V)
t(X("AA") + 9, Y(17) + 5, "−", 14, "start", "bold", CGND)
res_stand("V", 19, "V", 20, "R4", X("U") - 4, Y(19.4), "end")
mosfet("W", 20, "Q2", X("X"), Y(18.3))
br([("W", 20), ("W", 21)], CPWM)   # gate link to R6 (source link X:20-X:21 is the GND_PWR wire)
res_stand("W", 21, "X", 21, "R6", X("Y") + 6, Y(21.45), "start")
# servo
res_stand("V", 23, "W", 23, "R7", X("V"), Y(22.45), "middle")
header_m("X", "Z", 23, pin1="Z")   # pin 1 = GND (brown), pin 3 = signal (wiring.md 4.1)
t(X("Y"), Y(22.45), "J3 S0", 12, "middle", "bold")
cap_cer("Y", 24, "Z", 24, "C2", X("AA") + 2, Y(24) + 4, "middle")
res_stand("W", 24, "W", 25, "R9*", X("W") + 10, Y(24.5) + 4, "start", optional=True)
res_stand("V", 26, "W", 26, "R8", X("V"), Y(25.45), "middle")
header_m("X", "Z", 26, pin1="Z")
t(X("Y"), Y(25.45), "J4 S1", 12, "middle", "bold")
res_stand("W", 27, "W", 28, "R10*", X("W") + 10, Y(27.5) + 4, "start", optional=True)
t(X("Z") + 2, Y(23) + 17, "nâu", 12, "middle", fill=MUTED)
cap_el("X", 30, "Z", 30, 10.0, "C1", "1000µF", X("Y"), Y(30) + 22, "middle")

# power block
domino("B", 34, "D", 34, 1.03, 32.62, 4.97, 35.57, "J1", None)
t(X("C"), Y(33.35), "J1 vào", 12, "middle", "bold", "#0b3b57")
t(X("B") - 12, Y(34) + 5, "+", 13, "middle", "bold", C5V); t(X("D") + 12, Y(34) + 5, "−", 13, "middle", "bold", CGND)
rect(X(0.6), Y(29.6), X(5.4) - X(0.6), 0.8*P, fill="#f2c94c", stroke="#9a7b10", sw=1.6, rx=4, op=0.9)
dot(X("B"), Y(30), "#5a4a10", 3.4); dot(X("D"), Y(30), "#5a4a10", 3.4)
t(X("A") - 4, Y(29.25) + 4, "F1 PPTC 2.5A", 12, "start", "bold")   # above F1; Q3 sits below it
domino("F", 34, "H", 34, 5.03, 32.62, 8.97, 35.57, "J2", None)
t(X("G"), Y(33.35), "J2 → SW1", 12, "middle", "bold", "#0b3b57")
mosfet("D", 32, "Q3", X("E"), Y(30.3))   # AO3401A: G D:32, S E:32, D F:32
star(X("N"), Y(32), 9, CGND)
t(X("N") + 14, Y(32) + 4, "điểm sao", 12, "start", "bold")
t(X("J") + 4, Y(31) - 9, "5V_MAIN", 12, "end", "bold", C5V)
t(X("M"), Y(33) + 17, "GND_STAR", 12, "middle", "bold", CGND)
t(X("P") + 2, Y(28.6), "KHỐI NGUỒN", 12, "start", "bold", "#9b2c22")

# route labels
t(X("B") - 6, Y(22.5), "5V_AUDIO", 12, "middle", "bold", C5V, rot=-90)
t(X("C") + 7, Y(22.5), "GND_AUDIO", 12, "middle", "bold", CGND, rot=-90)
t(X("T") - 7, Y(20.5), "5V_EYES", 12, "middle", "bold", C5V, rot=-90)
t(X("U") + 7, Y(26.0), "5V → J5", 12, "middle", "bold", C5V, rot=-90)
t(X("AA") - 7, Y(32.4), "GND_PWR", 12, "middle", "bold", CGND, rot=-90)
t(X("N") + 7, Y(28.6), "GND_ESP", 12, "middle", "bold", CGND, rot=-90)
t(X("M") - 7, Y(28.9), "5V → D1", 12, "middle", "bold", C5V, rot=-90)   # 5V_ESP starts after D1
t(X("U") + 4, Y(31) - 8, "5V_SERVO", 12, "start", "bold", C5V)
t(X("U") + 4, Y(33) + 16, "GND_SERVO", 12, "start", "bold", CGND)

# ------------------------------------------------------------------ legend
LX = 800; ly = 140
t(LX, ly, "Chú giải", 16, weight="bold", halo=False)
ly += 26
def leg_line(c, w, dash, text, y):
    add(f'<line x1="{LX}" y1="{y}" x2="{LX+44}" y2="{y}" stroke="{c}" stroke-width="{w}" stroke-dasharray="{dash}"/>')
    t(LX+54, y+4, text, 12, halo=False)
leg_line(C5V, 5.5, DASH_P, "Dây nguồn 5V mặt dưới (thanh sao, cặp servo: dây trần 0.8mm/22AWG)", ly); ly += 20
leg_line(CGND, 5.5, DASH_P, "Dây GND mặt dưới, mỗi nhánh về điểm sao", ly); ly += 20
leg_line(C5V, 3, DASH_P, "Nhánh 22AWG có vỏ (được bắt chéo, có viền trắng)", ly); ly += 20
leg_line(TXT, 2.2, DASH_B, "Cầu hàn / dây trần ngắn (màu theo mạng)", ly); ly += 24
def leg_sym(draw, text, y):
    draw(y)
    t(LX+54, y+4, text, 12, halo=False)
leg_sym(lambda y: (rect(LX+4, y-8, 36, 16, fill="#e9e9e9", stroke=TXT, sw=1.4), add(f'<rect x="{LX+9}" y="{y-4}" width="8" height="8" fill="{TXT}"/>'), add(f'<rect x="{LX+27}" y="{y-4}" width="8" height="8" fill="{TXT}"/>'), add(f'<rect x="{LX+7}" y="{y-6}" width="12" height="12" fill="none" stroke="{C5V}" stroke-width="1.6"/>')), "Header đực (ô đỏ = chân 1)", ly); ly += 22
leg_sym(lambda y: (rect(LX+4, y-8, 36, 16, fill="#3a3a3a", stroke="#111", sw=1.2), add(f'<rect x="{LX+10}" y="{y-3}" width="6" height="6" fill="#d9d9d9"/>'), add(f'<rect x="{LX+28}" y="{y-3}" width="6" height="6" fill="#d9d9d9"/>')), "Hàng rào cái (đế cắm devkit, MAX98357)", ly); ly += 22
leg_sym(lambda y: rect(LX+2, y-9, 40, 18, fill="#3b6ec8", stroke="#2f4f7f", sw=1.6, dash="6 4", op=0.12), "Thân module cắm trên đế (kích thước dự kiến)", ly); ly += 22
leg_sym(lambda y: (add(f'<line x1="{LX+12}" y1="{y}" x2="{LX+36}" y2="{y}" stroke="#7a5c2e" stroke-width="2.4"/>'), circ(LX+12, y, 6.2, fill="#f2d7a8", stroke="#7a5c2e", sw=1.6), dot(LX+36, y, "#7a5c2e", 3)), "Điện trở lắp đứng (thân ở lỗ tròn); * = tùy chọn", ly); ly += 22
leg_sym(lambda y: (add(f'<line x1="{LX+12}" y1="{y}" x2="{LX+36}" y2="{y}" stroke="#1f6fd1" stroke-width="2.2"/>'), rect(LX+19, y-6, 10, 12, fill="#9cc3f0", stroke="#1f5fb0", sw=1.4, rx=4)), "Tụ gốm 100nF", ly); ly += 22
leg_sym(lambda y: (circ(LX+22, y, 9, fill="#e7ddf5", stroke="#5b3d8a", sw=1.8), dot(LX+17, y, C5V, 3), dot(LX+27, y, CGND, 3)), "Tụ hóa (vòng = đường kính thân; chấm đỏ = cực +)", ly); ly += 22
leg_sym(lambda y: (rect(LX+8, y-6, 28, 12, fill="#2b2b2b", stroke="#111", sw=1, rx=3), add(f'<rect x="{LX+8}" y="{y-6}" width="5" height="12" fill="#d9d9d9"/>')), "Diode nằm (vạch sáng = catot)", ly); ly += 22
leg_sym(lambda y: (rect(LX+2, y-10, 40, 18, fill="#1f7a3a", stroke="#0e4d22", sw=1.4, op=0.9)), "AO3400A (Q1, Q2) / AO3401A (Q3) trên đế SOT-23 (G S D)", ly); ly += 22
leg_sym(lambda y: rect(LX+4, y-9, 36, 18, fill="#5aa7d6", stroke="#1e5f86", sw=1.6, op=0.9), "Domino 5.08mm (J1, J2, J5)", ly); ly += 22
leg_sym(lambda y: star(LX+22, y, 9, CGND), "Điểm sao: thanh 5V_MAIN K:31–Q:31, GND_STAR K:33–Q:33", ly); ly += 22
leg_sym(lambda y: add(f'<rect x="{LX+2}" y="{y-9}" width="40" height="18" fill="url(#hatch)" stroke="{C5V}" stroke-width="1.2" stroke-dasharray="4 3"/>'), "Vùng cấm dưới anten: không linh kiện, không dây", ly); ly += 22
leg_sym(lambda y: add(f'<rect x="{LX+2}" y="{y-9}" width="40" height="18" fill="url(#hatchS)" stroke="{ORANGE}" stroke-width="1"/>'), "Cột S để trống (dự phòng devkit 10 bước)", ly); ly += 26
for fill, text in (("#dbe9fb", "Vùng âm thanh (trái)"), ("#e2f2e3", "Vùng VSENSE, MOSFET, servo (phải)"), ("#fde3df", "Khối nguồn và nhánh 5V_ESP"), ("#e9eef6", "Vùng devkit")):
    rect(LX+4, ly-8, 36, 16, fill=fill, stroke="#999", sw=1, rx=3)
    t(LX+54, ly+4, text, 12, halo=False)
    ly += 20
ly += 10
t(LX, ly, "Lối ra trên thanh sao", 13, weight="bold", halo=False); ly += 18
for s in ("5V K:31 ← J2 (từ SW1) · L 5V_AUDIO · M 5V_ESP · N VSENSE_TOP",
          "   O 5V_EYES · P → J5 · Q 5V_SERVO",
          "GND K:33 ← J1− · L GND_AUDIO · N GND_ESP · O GND_PWR",
          "   Q GND_SERVO · M, P dự phòng"):
    t(LX, ly, s, 12, halo=False, fill=TXT); ly += 17
ly += 10
t(LX, ly, "Phải đo trước khi hàn", 13, weight="bold", fill=ORANGE, halo=False); ly += 18
for s in ("• Khoảng cách 2 hàng chân devkit: 9 bước (22.86mm) → header",
          "  phải ở cột R như hình; 10 bước (25.4mm) → dời sang cột S.",
          "• Đếm lỗ tấm thật: hình dùng 27×35; tấm 24×30 phải vẽ lại.",
          "• Kích thước MAX98357, đế SOT-23, vị trí cổng USB devkit.",
          "• Thứ tự chân module (LCD, MAX98357) so với nhãn in."):
    t(LX, ly, s, 12, halo=False, fill=TXT); ly += 17
ly += 10
t(LX, ly, "Ghi chú", 13, weight="bold", halo=False); ly += 18
for s in ("• Dây tín hiệu GPIO không vẽ: dây có vỏ AWG26–30 mặt dưới,",
          "  đi theo bảng wiring.md mục 5.3.",
          "• Âm thanh (trái) và servo/MOSFET (phải) ở hai phía devkit.",
          "• Cổng USB devkit ở hàng ~25–26, cắm cáp từ phía dưới: không",
          "  đặt vật cao ở cột J–Q, hàng 26–35.",
          "• Q1 S (Y:10) về GND_PWR bằng dây riêng tới O:33.",
          "• Q3 (P-FET): D ← F1, S → J2/SW1, G → J1−: chống cắm ngược.",
          "• D1*: thay bằng dây nếu devkit đã có diode ở chân 5V.",
          "• R13 (F:16–F:17): GPIO12 → SD amply; cầu G:10–E:7 = GAIN→VIN.",
          "• R_EYE (một con cho module mắt) nằm trên dây, không trên PCB.",
          "• GND_ESP (N:31) và GND_AUDIO (J:31) bắt chéo dây trần 5V:",
          "  dùng dây silicone, lót co nhiệt, không hàn ở chỗ giao."):
    t(LX, ly, s, 12, halo=False, fill=TXT); ly += 17

svg = (f'<?xml version="1.0" encoding="UTF-8"?>\n'
       f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
       f'font-family="sans-serif" role="img" aria-labelledby="ttl desc">\n'
       f'<title id="ttl">Jarvis Helmet – bố trí PCB đục lỗ 7×9cm (Rev C)</title>\n'
       f'<desc id="desc">Lưới 27 cột (A–AA) × 35 hàng, nhìn từ mặt linh kiện. Devkit ở giữa, âm thanh bên trái, '
       f'MOSFET và servo bên phải, khối nguồn và điểm sao ở cạnh dưới. Tọa độ dự kiến theo wiring.md mục 5.</desc>\n'
       + "\n".join(el) + "\n</svg>\n")
with open(OUT, "w", encoding="utf-8", newline="\n") as f:
    f.write(svg)
print("wrote", OUT, len(el))
