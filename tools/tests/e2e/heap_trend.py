#!/usr/bin/env python3
"""Free-heap trend and reboots from the helmet's serial log (IT1 E13, E14; FW7 §5.4).

The firmware prints ``I (<ms since boot>) SystemInfo: free sram: <n> minimal sram: <m>``
every 10 s in every device state (jarvis-helmet board status timer; upstream's own print stops
while the device is busy). ``"free_heap":<n>`` from ``self.system.get_status`` JSON is accepted
too. A boot banner (``rst:0x..``) or a boot timestamp going backwards starts a new boot segment,
which counts as a reset.

The last boot segment is analysed: least-squares slope (bytes/hour), the drop between the
mean of the first and the last time window (10 % of the duration, at least 1 min), and the
lowest "minimal sram". Samples further apart than --max-gap fail the run: the heap was not
observed in between (old firmware, lost serial data), so a leak there would go unnoticed.

Capture the log with ``idf.py -p COMx monitor | tee helmet.log`` (or any serial terminal).
Usage: ``python heap_trend.py helmet.log [--max-drop 4096] [--min-free 30720]``.
Exit codes: 0 pass, 1 fail, 2 usage error, 3 no heap samples.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from typing import Optional

SRAM_RE = re.compile(r"\((?P<ms>\d+)\)\s+[\w.-]+:\s+free sram:\s*(?P<free>\d+)\s+minimal sram:\s*"
                     r"(?P<min>\d+)")
JSON_RE = re.compile(r"\((?P<ms>\d+)\).*\"free_heap\"\s*:\s*(?P<free>\d+)")
BOOT_RE = re.compile(r"rst:0x[0-9a-fA-F]+\s*\((?P<reason>[A-Z0-9_]+)\)")


def parse(lines) -> tuple:
    """Returns (segments, reasons): segments = list of [(t_s, free, minimal|None)]."""
    segments: list = [[]]
    reasons: list = []
    last_ms = -1
    for line in lines:
        boot = BOOT_RE.search(line)
        if boot:
            reasons.append(boot.group("reason"))
            if segments[-1]:
                segments.append([])
            last_ms = -1
            continue
        m = SRAM_RE.search(line)
        minimal: Optional[int] = None
        if m:
            minimal = int(m.group("min"))
        else:
            m = JSON_RE.search(line)
            if not m:
                continue
        ms = int(m.group("ms"))
        if ms < last_ms and segments[-1]:
            segments.append([])  # timestamp went backwards: the device rebooted
            reasons.append("UNKNOWN")
        last_ms = ms
        segments[-1].append((ms / 1000.0, int(m.group("free")), minimal))
    return [s for s in segments if s], reasons


def slope_per_hour(samples: list) -> float:
    xs = [s[0] for s in samples]
    ys = [s[1] for s in samples]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den * 3600.0


def window_means(seg: list) -> tuple:
    """Mean free heap of the first and the last time window (10 % of the span, >= 60 s)."""
    t0, t1 = seg[0][0], seg[-1][0]
    window = max(60.0, 0.1 * (t1 - t0))
    head = [s[1] for s in seg if s[0] <= t0 + window]
    tail = [s[1] for s in seg if s[0] >= t1 - window]
    return statistics.fmean(head), statistics.fmean(tail)


def largest_gap(seg: list) -> float:
    return max((b[0] - a[0] for a, b in zip(seg, seg[1:])), default=0.0)


def analyse(segments: list, reasons: list, max_drop: int, max_slope: float, min_free: int,
            allow_reboot: bool, min_samples: int, max_gap: float = 30.0) -> dict:
    seg = segments[-1]
    head, tail = window_means(seg)
    minimal = [s[2] for s in seg if s[2] is not None]
    res = {
        "boots": len(segments),
        "reset_reasons": reasons,
        "samples": len(seg),
        "duration_min": round((seg[-1][0] - seg[0][0]) / 60.0, 1),
        "free_first": seg[0][1],
        "free_last": seg[-1][1],
        "free_min": min(s[1] for s in seg),
        "minimal_sram": min(minimal) if minimal else None,
        "drop_bytes": round(head - tail),
        "slope_bytes_per_hour": round(slope_per_hour(seg)),
        "checks": [],
    }
    checks = res["checks"]
    checks.append({"name": "enough_samples", "ok": len(seg) >= min_samples,
                   "detail": f"{len(seg)} samples (need {min_samples})"})
    gap = largest_gap(seg)
    res["largest_gap_s"] = round(gap, 1)
    checks.append({"name": "no_sample_gaps", "ok": gap <= max_gap,
                   "detail": f"largest gap between samples {gap:.0f} s (max {max_gap:.0f} s)"})
    leaking = res["drop_bytes"] > max_drop and res["slope_bytes_per_hour"] < -max_slope
    checks.append({"name": "no_steady_decrease", "ok": not leaking,
                   "detail": f"drop {res['drop_bytes']} B (max {max_drop}), slope "
                             f"{res['slope_bytes_per_hour']} B/h (min -{max_slope:.0f})"})
    # A banner before the first sample (power-on, flashing) is the capture's own boot.
    checks.append({"name": "no_reset", "ok": allow_reboot or len(segments) == 1,
                   "detail": f"{len(segments)} boot segment(s), boot banners {reasons or '-'}"})
    if res["minimal_sram"] is not None:
        checks.append({"name": "minimal_sram", "ok": res["minimal_sram"] >= min_free,
                       "detail": f"lowest minimal sram {res['minimal_sram']} B "
                                 f"(need >= {min_free})"})
    res["pass"] = all(c["ok"] for c in checks)
    return res


def main(argv: Optional[list] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    p = argparse.ArgumentParser(prog="heap_trend.py", description=__doc__.splitlines()[0])
    p.add_argument("log", help="serial log file ('-' = stdin)")
    p.add_argument("--max-drop", type=int, default=4096,
                   help="allowed drop between first and last 10%% of samples (bytes)")
    p.add_argument("--max-slope", type=float, default=2048.0,
                   help="allowed decrease rate (bytes/hour) before calling it a leak")
    p.add_argument("--min-free", type=int, default=30 * 1024,
                   help="lowest acceptable 'minimal sram' (FW7 §5.4: 30 KB)")
    p.add_argument("--min-samples", type=int, default=6, help="need at least N samples (10 s each)")
    p.add_argument("--max-gap", type=float, default=30.0,
                   help="fail when two consecutive samples are more than S seconds apart")
    p.add_argument("--allow-reboot", action="store_true", help="do not fail on resets")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    fh = sys.stdin if args.log == "-" else open(args.log, encoding="utf-8", errors="replace")
    with fh:
        segments, reasons = parse(fh)
    if not segments:
        print("heap_trend.py: no 'free sram' samples found", file=sys.stderr)
        return 3
    res = analyse(segments, reasons, args.max_drop, args.max_slope, args.min_free,
                  args.allow_reboot, args.min_samples, args.max_gap)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"{res['samples']} mẫu trong {res['duration_min']} phút, {res['boots']} lần khởi động")
        print(f"free sram: đầu {res['free_first']}, cuối {res['free_last']}, thấp nhất "
              f"{res['free_min']}; minimal sram thấp nhất {res['minimal_sram']}")
        print(f"giảm {res['drop_bytes']} B, độ dốc {res['slope_bytes_per_hour']} B/giờ")
        for c in res["checks"]:
            print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}")
        print("KẾT QUẢ: " + ("ĐẠT" if res["pass"] else "KHÔNG ĐẠT"))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
