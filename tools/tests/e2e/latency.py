#!/usr/bin/env python3
"""Latency report from xiaozhi-server ``[LAT]`` log lines (IT1 §1, contract §4.10).

Log format (one line per milestone, anywhere in a log line, ANSI colours allowed)::

    [LAT] sid=<session_id> stage=<stage> t_ms=<epoch ms> [key=value ...]

Stages: vad_end, asr_done (text_len=), llm_first_token, llm_done, tts_first_audio,
first_packet_sent, quick_phrase (task=), mcp_call (tool=, ms= round trip; t_ms = when the
call was sent, as the SV-C patch logs it). Optional ``speech_end=<epoch ms>`` on vad_end gives
the true end of speech (else --vad-offset-ms).

Lines are grouped by sid, then into turns (utterances): a turn starts at vad_end, or at
asr_done / quick_phrase / llm_first_token when the previous turn already finished that
part (text injected with listen/detect has no vad_end). Per-stage deltas are reported
with median and p90 (linear interpolation), optionally as CSV/JSON.

Usage: ``python latency.py tmp/server.log [more logs or globs] [--csv out.csv]``.
Exit codes: 0 ok, 1 a target failed with --check, 2 usage error, 3 no [LAT] lines.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as _dt
import glob
import json
import math
import re
import statistics
import sys
from typing import Iterable, Optional

LAT_RE = re.compile(r"\[LAT\]\s+(?P<body>.*)$")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
KV_RE = re.compile(r"(?P<k>[A-Za-z_][A-Za-z0-9_]*)=(?P<v>\S*)")

STAGES = ("vad_end", "asr_done", "quick_phrase", "llm_first_token", "tts_first_audio",
          "first_packet_sent", "llm_done", "mcp_call")
OPENERS = ("vad_end", "asr_done", "quick_phrase", "llm_first_token")


@dataclasses.dataclass
class LatEvent:
    sid: str
    stage: str
    t_ms: int
    fields: dict
    source: str = ""
    line: int = 0
    order: int = 0

    def num(self, key: str) -> Optional[float]:
        try:
            return float(self.fields[key])
        except (KeyError, ValueError):
            return None


@dataclasses.dataclass
class ParseStats:
    lines: int = 0
    lat_lines: int = 0
    malformed: list = dataclasses.field(default_factory=list)
    unknown_stages: dict = dataclasses.field(default_factory=dict)


def parse_line(line: str, source: str = "", line_no: int = 0) -> tuple:
    """Returns (event or None, error or None) for one log line."""
    line = ANSI_RE.sub("", line.rstrip("\r\n"))
    m = LAT_RE.search(line)
    if not m:
        return None, None
    fields = {kv.group("k"): kv.group("v") for kv in KV_RE.finditer(m.group("body"))}
    sid, stage, t_raw = fields.pop("sid", ""), fields.pop("stage", ""), fields.pop("t_ms", "")
    if not sid or not stage or not t_raw:
        return None, f"{source}:{line_no}: missing sid/stage/t_ms"
    try:
        t_ms = int(float(t_raw))
    except ValueError:
        return None, f"{source}:{line_no}: bad t_ms {t_raw!r}"
    return LatEvent(sid, stage, t_ms, fields, source, line_no), None


def expand_paths(patterns: Iterable[str]) -> list:
    paths: list = []
    for pattern in patterns:
        if pattern == "-":
            paths.append("-")
            continue
        matches = sorted(glob.glob(pattern))
        paths.extend(matches if matches else [pattern])
    return paths


def parse_files(patterns: Iterable[str]) -> tuple:
    events: list = []
    stats = ParseStats()
    for path in expand_paths(patterns):
        if path == "-":
            lines = sys.stdin
            name = "<stdin>"
        else:
            try:
                lines = open(path, encoding="utf-8", errors="replace")
            except OSError as e:
                raise SystemExit(f"latency.py: cannot read {path}: {e}")
            name = str(path)
        with (lines if path != "-" else _nullcontext(lines)) as fh:
            for no, line in enumerate(fh, start=1):
                stats.lines += 1
                ev, err = parse_line(line, name, no)
                if err:
                    stats.malformed.append(err)
                if ev is None:
                    continue
                stats.lat_lines += 1
                if ev.stage not in STAGES:
                    stats.unknown_stages[ev.stage] = stats.unknown_stages.get(ev.stage, 0) + 1
                ev.order = len(events)
                events.append(ev)
    return events, stats


class _nullcontext:
    def __init__(self, obj):
        self.obj = obj

    def __enter__(self):
        return self.obj

    def __exit__(self, *exc):
        return False


@dataclasses.dataclass
class Turn:
    sid: str
    index: int
    events: list = dataclasses.field(default_factory=list)

    @property
    def stages(self) -> set:
        return {e.stage for e in self.events}

    def first(self, stage: str) -> Optional[LatEvent]:
        return next((e for e in self.events if e.stage == stage), None)

    def all(self, stage: str) -> list:
        return [e for e in self.events if e.stage == stage]

    @property
    def is_user_turn(self) -> bool:
        return bool(self.stages & set(OPENERS))

    @property
    def start_ms(self) -> int:
        return min(e.t_ms for e in self.events)

    @property
    def kind(self) -> str:
        if "quick_phrase" in self.stages:
            return "quick"
        if "vad_end" in self.stages:
            return "voice"
        return "text"


def _opens_turn(turn: Optional[Turn], stage: str) -> bool:
    if stage not in OPENERS:
        return False
    if turn is None or not turn.is_user_turn:
        return True
    seen = turn.stages
    if stage == "vad_end":
        return bool(seen & {"asr_done", "quick_phrase", "llm_first_token", "first_packet_sent"})
    if stage == "asr_done":
        return bool(seen & {"asr_done", "first_packet_sent"})
    if stage == "quick_phrase":
        return bool(seen & {"quick_phrase", "llm_first_token", "first_packet_sent"})
    # llm_first_token: a second LLM round of the same turn (after tool calls) has not
    # produced audio yet; anything after audio or a quick phrase is a new utterance.
    return bool(seen & {"first_packet_sent", "quick_phrase"})


def group_turns(events: list) -> list:
    by_sid: dict = {}
    for ev in events:
        by_sid.setdefault(ev.sid, []).append(ev)
    turns: list = []
    for sid, evs in by_sid.items():
        evs.sort(key=lambda e: (e.t_ms, e.order))
        current: Optional[Turn] = None
        count = 0
        for ev in evs:
            if current is None or _opens_turn(current, ev.stage):
                if current is not None:
                    # a turn without any opener holds background events only (e.g. the
                    # sequence sync right after connect); it is reported separately
                    turns.append(current)
                if ev.stage in OPENERS:
                    count += 1
                current = Turn(sid, count if ev.stage in OPENERS else 0, [])
            current.events.append(ev)
        if current is not None:
            turns.append(current)
    turns.sort(key=lambda t: (t.start_ms, t.sid))
    return turns


def _delta(a: Optional[LatEvent], b: Optional[LatEvent]) -> Optional[float]:
    if a is None or b is None:
        return None
    return float(b.t_ms - a.t_ms)


SPEECH_END_KEYS = ("speech_end_ms", "speech_end")  # SV-C logs speech_end_ms=


def speech_end(ev: Optional[LatEvent]) -> Optional[float]:
    """Epoch ms of the last voiced audio, from the vad_end line (if logged and sane)."""
    if ev is None:
        return None
    for key in SPEECH_END_KEYS:
        value = ev.num(key)
        if value is not None and 0 < value <= ev.t_ms:
            return value
    return None


def eos_ms(turn: Turn, vad_offset_ms: float = 0.0) -> Optional[float]:
    vad = turn.first("vad_end")
    if vad is None:
        return None
    end = speech_end(vad)
    if end is not None:
        return end
    if vad.fields.get("src") == "listen_stop":
        return float(vad.t_ms)  # PTT release is the end of speech: no VAD silence to subtract
    return vad.t_ms - vad_offset_ms  # legacy VAD line without speech_end


PASSIVE_TOOLS = frozenset({"self_sequence_save", "self_mask_get_state", "self_system_get_status",
                           "self_get_device_status"})


def mcp_start_ms(ev: LatEvent, t_is_end: bool = False) -> float:
    """When the device call was sent. The SV-C patch logs t_ms = call issued (the line itself
    is written on completion, ms = round trip); --mcp-t-is-end handles the other convention."""
    rtt = ev.num("ms")
    if t_is_end and rtt is not None:
        return ev.t_ms - rtt
    return float(ev.t_ms)


def turn_metrics(turn: Turn, vad_offset_ms: float = 0.0, mcp_t_is_end: bool = False) -> dict:
    g = turn.first
    eos = eos_ms(turn, vad_offset_ms)
    vad = g("vad_end")
    first_packet = g("first_packet_sent")
    # The connect-time sync (self.sequence.save) and status reads move nothing.
    calls = sorted(mcp_start_ms(e, mcp_t_is_end) for e in turn.all("mcp_call")
                   if e.fields.get("tool", "") not in PASSIVE_TOOLS)
    after_eos = [c for c in calls if eos is not None and c >= eos]
    m = {
        "vad_wait": (vad.t_ms - speech_end(vad)) if speech_end(vad) is not None else None,
        "asr": _delta(vad, g("asr_done")),
        "llm_ttft": _delta(g("asr_done"), g("llm_first_token")),
        "llm_total": _delta(g("asr_done"), g("llm_done")),
        "tts_first": _delta(g("llm_first_token"), g("tts_first_audio")),
        "send_first": _delta(g("tts_first_audio"), first_packet),
        "server_total": _delta(vad, first_packet),
        "eos_to_audio": (first_packet.t_ms - eos) if first_packet is not None
        and eos is not None else None,
        "asr_to_quick": _delta(g("asr_done"), g("quick_phrase")),
        "eos_to_mcp": (after_eos[0] - eos) if after_eos else None,
    }
    m["eos_to_mcp_quick"] = m["eos_to_mcp"] if turn.kind == "quick" else None
    m["mcp_rtt"] = [e.num("ms") for e in turn.all("mcp_call") if e.num("ms") is not None]
    return m


@dataclasses.dataclass
class Metric:
    key: str
    label: str
    target_ms: Optional[float] = None
    note: str = ""


METRICS = [
    Metric("vad_wait", "VAD chờ im lặng (speech_end → vad_end)"),
    Metric("asr", "ASR (vad_end → asr_done)", 300, "SV2 §4.3 (câu 3 s, tham khảo)"),
    Metric("llm_ttft", "LLM token đầu (asr_done → llm_first_token)", 400, "SV3 §4.1"),
    Metric("llm_total", "LLM xong (asr_done → llm_done)"),
    Metric("tts_first", "TTS âm đầu (llm_first_token → tts_first_audio)"),
    Metric("send_first", "Gửi gói đầu (tts_first_audio → first_packet_sent)"),
    Metric("server_total", "Server (vad_end → first_packet_sent)"),
    Metric("eos_to_audio", "Nói xong → gói audio đầu", 1500, "IT1 §3 (LAN, trung vị)"),
    Metric("asr_to_quick", "Khớp nhanh (asr_done → quick_phrase)"),
    Metric("eos_to_mcp", "Nói xong → lệnh MCP đầu (mọi lượt)"),
    Metric("eos_to_mcp_quick", "Nói xong → lệnh MCP (khớp nhanh)", 800, "IT1 §3 / SV6 §5.2"),
    Metric("mcp_rtt", "MCP round-trip (mọi lệnh)", 50, "FW4 §4.4"),
]


def percentile(values: list, pct: float) -> Optional[float]:
    if not values:
        return None
    data = sorted(values)
    rank = (len(data) - 1) * pct / 100.0
    lo, hi = math.floor(rank), math.ceil(rank)
    return data[lo] + (data[hi] - data[lo]) * (rank - lo)


def summarize(turns: list, vad_offset_ms: float, targets: dict,
              mcp_t_is_end: bool = False) -> list:
    rows = []
    per_turn = [turn_metrics(t, vad_offset_ms, mcp_t_is_end) for t in turns]
    for metric in METRICS:
        values: list = []
        for m in per_turn:
            v = m.get(metric.key)
            if isinstance(v, list):
                values.extend(v)
            elif v is not None:
                values.append(v)
        target = targets.get(metric.key, metric.target_ms)
        row = {"metric": metric.key, "label": metric.label, "n": len(values),
               "median": round(statistics.median(values), 1) if values else None,
               "p90": round(percentile(values, 90), 1) if values else None,
               "min": round(min(values), 1) if values else None,
               "max": round(max(values), 1) if values else None,
               "target_ms": target, "note": metric.note, "pass": None}
        if target is not None and values:
            row["pass"] = row["median"] <= target
        rows.append(row)
    return rows


def _fmt(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:.0f}"


def format_table(rows: list) -> str:
    head = f"{'chặng':<17}{'mô tả':<48}{'n':>4}{'median':>8}{'p90':>8}{'min':>8}{'max':>8}" \
           f"{'mục tiêu':>10}  KQ"
    out = [head, "-" * len(head)]
    for r in rows:
        verdict = "" if r["pass"] is None else ("ĐẠT" if r["pass"] else "KHÔNG ĐẠT")
        target = "" if r["target_ms"] is None else f"≤{r['target_ms']:.0f}"
        out.append(f"{r['metric']:<17}{r['label'][:47]:<48}{r['n']:>4}{_fmt(r['median']):>8}"
                   f"{_fmt(r['p90']):>8}{_fmt(r['min']):>8}{_fmt(r['max']):>8}{target:>10}  "
                   f"{verdict}")
    return "\n".join(out)


def _local(ms: float) -> str:
    return _dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def turn_rows(turns: list, vad_offset_ms: float, mcp_t_is_end: bool = False) -> list:
    rows = []
    for t in turns:
        m = turn_metrics(t, vad_offset_ms, mcp_t_is_end)
        asr = t.first("asr_done")
        quick = t.first("quick_phrase")
        row = {
            "sid": t.sid, "turn": t.index, "kind": t.kind if t.is_user_turn else "background",
            "start": _local(t.start_ms),
            "text_len": asr.fields.get("text_len") if asr else "",
            "task": quick.fields.get("task") if quick else "",
            "tools": " ".join(e.fields.get("tool", "") for e in t.all("mcp_call")),
        }
        for stage in STAGES:
            ev = t.first(stage)
            row[f"t_{stage}"] = ev.t_ms if ev else ""
        for metric in METRICS:
            v = m.get(metric.key)
            if isinstance(v, list):
                v = " ".join(f"{x:.0f}" for x in v)
            row[metric.key] = "" if v is None else v
        rows.append(row)
    return rows


def format_turns(rows: list) -> str:
    head = f"{'sid':<10}{'#':>3} {'start':<24}{'kind':<11}{'task':<12}{'eos→audio':>10}" \
           f"{'eos→mcp':>9}{'asr':>7}{'ttft':>7}  tools"
    out = [head, "-" * len(head)]
    for r in rows:
        def cell(k):
            v = r.get(k, "")
            return "-" if v == "" else (f"{v:.0f}" if isinstance(v, float) else str(v))
        out.append(f"{r['sid'][:9]:<10}{r['turn']:>3} {r['start']:<24}{r['kind']:<11}"
                   f"{(r['task'] or '')[:11]:<12}{cell('eos_to_audio'):>10}{cell('eos_to_mcp'):>9}"
                   f"{cell('asr'):>7}{cell('llm_ttft'):>7}  {r['tools']}")
    return "\n".join(out)


def _parse_time(value: str) -> float:
    """Epoch ms, epoch s, or 'YYYY-MM-DD HH:MM[:SS]' (local time) -> epoch ms."""
    try:
        num = float(value)
        return num if num > 1e11 else num * 1000
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return _dt.datetime.strptime(value, fmt).timestamp() * 1000
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"cannot parse time {value!r}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="latency.py",
                                description="Per-stage latency (median/p90) from [LAT] lines.")
    p.add_argument("logs", nargs="+", help="log files or globs ('-' = stdin), e.g. "
                                           "server/xiaozhi/tmp/server.log*")
    p.add_argument("--sid", action="append", help="only these session ids (prefix match)")
    p.add_argument("--since", type=_parse_time, help="epoch ms/s or 'YYYY-MM-DD HH:MM[:SS]'")
    p.add_argument("--until", type=_parse_time)
    p.add_argument("--last", type=int, help="only the last N user turns")
    p.add_argument("--kind", choices=("voice", "text", "quick"), action="append",
                   help="only these turn kinds")
    p.add_argument("--vad-offset-ms", type=float, default=0.0,
                   help="only for old logs whose VAD vad_end lines lack speech_end=: end of speech "
                        "= vad_end - offset (never applied to PTT src=listen_stop turns)")
    p.add_argument("--mcp-t-is-end", action="store_true",
                   help="mcp_call t_ms marks the reply, not the request (default: request)")
    p.add_argument("--network", choices=("lan", "4g"), default="lan",
                   help="target for eos_to_audio: lan 1500 ms, 4g 2500 ms")
    p.add_argument("--csv", help="write one row per turn to this CSV (UTF-8 with BOM)")
    p.add_argument("--json", action="store_true", help="print JSON instead of tables")
    p.add_argument("--turns", action="store_true", help="also print the per-turn table")
    p.add_argument("--check", action="store_true",
                   help="exit 1 when a metric with a target misses it (median)")
    return p


def main(argv: Optional[list] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    events, stats = parse_files(args.logs)
    if args.sid:
        events = [e for e in events if any(e.sid.startswith(s) for s in args.sid)]
    if args.since is not None:
        events = [e for e in events if e.t_ms >= args.since]
    if args.until is not None:
        events = [e for e in events if e.t_ms <= args.until]
    if not events:
        print(f"latency.py: no [LAT] lines found ({stats.lines} lines read). Is the SV-C "
              "latency patch applied and the log level INFO?", file=sys.stderr)
        return 3
    turns = group_turns(events)
    user_turns = [t for t in turns if t.is_user_turn]
    if args.kind:
        user_turns = [t for t in user_turns if t.kind in args.kind]
    if args.last:
        user_turns = user_turns[-args.last:]
    selected = user_turns + ([t for t in turns if not t.is_user_turn] if not args.kind else [])
    targets = {"eos_to_audio": 2500.0 if args.network == "4g" else 1500.0}
    rows = summarize(selected, args.vad_offset_ms, targets, args.mcp_t_is_end)
    per_turn = turn_rows(sorted(selected, key=lambda t: t.start_ms), args.vad_offset_ms,
                         args.mcp_t_is_end)
    if args.csv:
        fields = list(per_turn[0].keys()) if per_turn else ["sid"]
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(per_turn)
    info = {"lines": stats.lines, "lat_lines": stats.lat_lines,
            "sessions": len({t.sid for t in selected}), "user_turns": len(user_turns),
            "malformed": stats.malformed[:20], "unknown_stages": stats.unknown_stages,
            "vad_offset_ms": args.vad_offset_ms, "network": args.network}
    if args.json:
        print(json.dumps({"info": info, "metrics": rows, "turns": per_turn},
                         ensure_ascii=False, indent=2))
    else:
        print(f"{stats.lat_lines} dòng [LAT] / {stats.lines} dòng log, {info['sessions']} phiên, "
              f"{len(user_turns)} lượt nói (mạng: {args.network}, vad offset "
              f"{args.vad_offset_ms:.0f} ms)")
        if stats.malformed:
            print(f"cảnh báo: {len(stats.malformed)} dòng [LAT] lỗi, ví dụ {stats.malformed[0]}")
        if stats.unknown_stages:
            print(f"cảnh báo: stage lạ {stats.unknown_stages}")
        print()
        print(format_table(rows))
        if args.turns:
            print()
            print(format_turns(per_turn))
        if args.csv:
            print(f"\nCSV: {args.csv}")
    failed = [r for r in rows if r["pass"] is False]
    return 1 if args.check and failed else 0


if __name__ == "__main__":
    sys.exit(main())
