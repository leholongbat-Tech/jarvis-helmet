#!/usr/bin/env python3
"""A/B evaluation of ASR configurations (SV2): WER with Vietnamese-aware normalization,
command keyword accuracy, empty results on silence/noise, and latency p50/p90.

Each configuration is an ASR endpoint plus optional per-request overrides (``language``,
``prompt``) understood by server/asr; other engines are compared by pointing at differently
configured services (see configs.example.yaml).

    python tools/tests/asr/eval.py --manifest tools/tests/asr/recordings/real/manifest.jsonl \
        --configs tools/tests/asr/configs.example.yaml --out tools/tests/asr/RESULTS.md

Without --configs, three configs are run against --url: <model>+vi, <model>+auto and
<model>+vi+prompt. Per-utterance details go to <manifest dir>/eval_details/<config>.jsonl.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import asrlib  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_URL = "http://127.0.0.1:9000/v1/audio/transcriptions"
DEFAULT_PROMPT = (
    "Jarvis, quản gia, open mask, close mask, mặt nạ, đèn mắt, combat mode, stealth mode, thưa ngài."
)
# SV2 acceptance thresholds.
KW_TARGET, WER_MIXED_TARGET, WER_VI_TARGET, LATENCY_TARGET_MS = 0.95, 0.15, 0.10, 300.0


@dataclass
class Config:
    name: str
    url: str = DEFAULT_URL
    language: Optional[str] = None  # None = service default (env)
    prompt: Optional[str] = None  # None = service default; "none" disables
    model: str = "whisper-large-v3"
    verbose: bool = True  # server/asr supports response_format=verbose_json
    note: str = ""
    health: Dict[str, Any] = field(default_factory=dict)

    @property
    def health_url(self) -> str:
        return re.sub(r"/v1/audio/transcriptions/?$", "", self.url.rstrip("/")) + "/health"


def load_configs(path: Optional[str], url: str, prompt: str) -> List[Config]:
    if not path:
        return [
            Config("{model}+vi", url, language="vi", prompt="none"),
            Config("{model}+auto", url, language="auto", prompt="none"),
            Config("{model}+vi+prompt", url, language="vi", prompt=prompt),
        ]
    text = Path(path).read_text(encoding="utf-8")
    if path.endswith((".yaml", ".yml")):
        import yaml

        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    entries = data.get("configs", data) if isinstance(data, dict) else data
    configs = []
    for e in entries:
        known = {k: e[k] for k in ("name", "url", "language", "prompt", "model", "verbose", "note") if k in e}
        configs.append(Config(**known))
    return configs


def fetch_health(client: httpx.Client, cfg: Config) -> Dict[str, Any]:
    try:
        resp = client.get(cfg.health_url, timeout=5.0)
        return resp.json() if resp.status_code == 200 else {"status": f"HTTP {resp.status_code}"}
    except (httpx.HTTPError, ValueError) as exc:
        return {"status": f"unreachable ({exc.__class__.__name__})"}


def transcribe(client: httpx.Client, cfg: Config, audio_path: str, timeout: float) -> Dict[str, Any]:
    data = {"model": cfg.model, "response_format": "verbose_json" if cfg.verbose else "json"}
    if cfg.language is not None:
        data["language"] = cfg.language
    if cfg.prompt is not None:
        data["prompt"] = cfg.prompt
    raw = Path(audio_path).read_bytes()
    started = time.perf_counter()
    try:
        resp = client.post(cfg.url, data=data, files={"file": (Path(audio_path).name, raw, "audio/wav")},
                           timeout=timeout)
    except httpx.HTTPError as exc:
        return {"error": f"{exc.__class__.__name__}: {exc}", "latency_ms": None}
    latency = (time.perf_counter() - started) * 1000.0
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}", "latency_ms": latency}
    body = resp.json()
    jarvis = body.get("jarvis") or {}
    timing = jarvis.get("timing_ms") or {}
    return {
        "text": body.get("text", ""),
        "latency_ms": latency,
        "server_total_ms": timing.get("total"),
        "server_infer_ms": timing.get("infer"),
        "dropped": jarvis.get("dropped"),
        "raw_text": jarvis.get("raw_text"),
        "duration_s": body.get("duration"),
        "language": body.get("language"),
    }


def score_item(item: Dict[str, Any], out: Dict[str, Any], keywords) -> Dict[str, Any]:
    ref_tokens, hyp_tokens = asrlib.tokenize(item["reference"]), asrlib.tokenize(out.get("text") or "")
    s, d, i = asrlib.edit_ops(ref_tokens, hyp_tokens)
    # Text before the hallucination filter (verbose_json only): measures what the filter removed.
    raw = out.get("raw_text")
    raw_tokens = asrlib.tokenize(raw) if raw else hyp_tokens
    rs, rd, ri = asrlib.edit_ops(ref_tokens, raw_tokens)
    row = {
        "id": item["id"],
        "group": item["group"],
        "audio": item["audio"],
        "reference": item["reference"],
        "hypothesis": out.get("text", ""),
        "ref_norm": " ".join(ref_tokens),
        "hyp_norm": " ".join(hyp_tokens),
        "sub": s,
        "del": d,
        "ins": i,
        "n_ref": len(ref_tokens),
        "wer": (s + d + i) / len(ref_tokens) if ref_tokens else (1.0 if hyp_tokens else 0.0),
        "raw_edits": rs + rd + ri,
        "keywords": asrlib.keyword_stats(ref_tokens, hyp_tokens, keywords),
    }
    row.update({k: v for k, v in out.items() if k != "text"})
    return row


def _ratio(num: float, den: float) -> Optional[float]:
    return num / den if den else None


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok = [r for r in rows if not r.get("error")]
    speech = [r for r in ok if r["group"] in asrlib.SPEECH_GROUPS and r["n_ref"] > 0]
    negatives = [r for r in ok if r["group"] == "noise" or r["n_ref"] == 0]
    per_group = {}
    for g in asrlib.SPEECH_GROUPS:
        rs = [r for r in speech if r["group"] == g]
        edits = sum(r["sub"] + r["del"] + r["ins"] for r in rs)
        per_group[g] = {"n": len(rs), "wer": _ratio(edits, sum(r["n_ref"] for r in rs))}
    kw_ref = sum(v["ref"] for r in speech for v in r["keywords"].values())
    kw_hit = sum(v["hit"] for r in speech for v in r["keywords"].values())
    kw_fa = sum(v["false_alarm"] for r in speech for v in r["keywords"].values())
    misses: Dict[str, int] = {}
    for r in speech:
        for name, v in r["keywords"].items():
            if v["ref"] > v["hit"]:
                misses[name] = misses.get(name, 0) + v["ref"] - v["hit"]
    lat = [r["latency_ms"] for r in ok if r.get("latency_ms") is not None]
    srv = [r["server_total_ms"] for r in ok if r.get("server_total_ms") is not None]
    mid = [r.get("server_total_ms") or r.get("latency_ms") for r in speech
           if r.get("duration_s") and 2.0 <= r["duration_s"] <= 4.0]
    rtf = [r["server_total_ms"] / (r["duration_s"] * 1000.0) for r in ok
           if r.get("server_total_ms") is not None and r.get("duration_s")]
    edits_all = sum(r["sub"] + r["del"] + r["ins"] for r in speech)
    n_ref_all = sum(r["n_ref"] for r in speech)
    return {
        "n": len(rows),
        "errors": len(rows) - len(ok),
        "wer": _ratio(edits_all, n_ref_all),
        "wer_raw": _ratio(sum(r["raw_edits"] for r in speech), n_ref_all),
        "dropped_on_speech": sum(1 for r in speech if r.get("dropped")),
        "groups": per_group,
        "kw_acc": _ratio(kw_hit, kw_ref),
        "kw_ref": kw_ref,
        "kw_false_alarms": kw_fa,
        "kw_misses": dict(sorted(misses.items(), key=lambda kv: -kv[1])),
        "empty_on_speech": sum(1 for r in speech if not r["hyp_norm"]),
        "n_speech": len(speech),
        "negatives": len(negatives),
        "negatives_nonempty": sum(1 for r in negatives if r["hyp_norm"]),
        "lat_p50": asrlib.percentile(lat, 50),
        "lat_p90": asrlib.percentile(lat, 90),
        "srv_p50": asrlib.percentile(srv, 50),
        "srv_p90": asrlib.percentile(srv, 90),
        "mid_p50": asrlib.percentile(mid, 50),
        "n_mid": len(mid),
        "rtf_p50": asrlib.percentile(rtf, 50),
    }


def _pct(v: Optional[float]) -> str:
    return "–" if v is None else f"{v * 100:.1f}%"


def _ms(v: Optional[float]) -> str:
    return "–" if v is None else f"{v:.0f}"


def _rtf(v: Optional[float]) -> str:
    return "–" if v is None else f"{v:.3f}"


def _verdict(ok: Optional[bool]) -> str:
    return "–" if ok is None else ("ĐẠT" if ok else "CHƯA ĐẠT")


def _cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def render_markdown(results, configs, items, args, keywords) -> str:
    groups = {g: sum(1 for it in items if it["group"] == g) for g in asrlib.GROUP_ORDER}
    synthetic = any(it.get("synthetic") for it in items)
    lines = ["# Kết quả đánh giá ASR (SV2 – so sánh A/B)", ""]
    if synthetic or args.label:
        lines += [f"> **{args.label or 'DỮ LIỆU SMOKE'}**", ">"]
    if synthetic:
        lines += [
            "> Manifest có mẫu **tổng hợp bằng TTS** (`synthetic: true`): đây chỉ là smoke test của",
            "> công cụ, **không phải** kết quả A/B trên bộ thu âm thật bằng mũ (xem README.md).",
        ]
    lines += [
        "",
        f"- Thời điểm chạy: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- Manifest: `{args.manifest}` – {len(items)} mẫu "
        f"(mixed {groups['mixed']}, vi {groups['vi']}, en {groups['en']}, noise {groups['noise']})",
        f"- Từ khóa lệnh: `{Path(args.keywords).name}` ({len(keywords)} mục)",
        "- WER chấm trên văn bản đã chuẩn hóa: Unicode NFC, chữ thường, bỏ dấu câu, thống nhất vị trí dấu "
        "thanh (hòa = hoà). Tiếng Việt tính theo âm tiết.",
        "",
        "## Tổng hợp",
        "",
        "| Cấu hình | WER chung | WER thô (trước lọc) | WER mixed | WER vi | WER en | Từ khóa | Câu nói bị lọc "
        "| Câu nói ra rỗng | Âm tính có chữ | p50 ms | p90 ms | p50 câu 2–4 s | RTF p50 | Lỗi HTTP |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for cfg in configs:
        s = results[cfg.name]
        g = s["groups"]
        lines.append(
            f"| {cfg.name} | {_pct(s['wer'])} | {_pct(s['wer_raw'])} | {_pct(g['mixed']['wer'])} | "
            f"{_pct(g['vi']['wer'])} | {_pct(g['en']['wer'])} | {_pct(s['kw_acc'])} | "
            f"{s['dropped_on_speech']}/{s['n_speech']} | {s['empty_on_speech']}/{s['n_speech']} | "
            f"{s['negatives_nonempty']}/{s['negatives']} | {_ms(s['lat_p50'])} | {_ms(s['lat_p90'])} | "
            f"{_ms(s['mid_p50'])} | {_rtf(s['rtf_p50'])} | {s['errors']} |"
        )
    lines += [
        "",
        "- WER thô = WER của văn bản trước bộ lọc ảo giác (cần `response_format=verbose_json`); nếu thấp hơn "
        "nhiều so với WER chung thì bộ lọc đang xóa nhầm lời nói thật → nới ngưỡng `ASR_FILTER_*`.",
        "- \"Câu nói bị lọc\" = câu có lời nói nhưng bị cổng VAD hoặc bộ lọc loại bỏ (`dropped`).",
        "- p50/p90 = độ trễ phía client (gồm HTTP); \"p50 câu 2–4 s\" = thời gian xử lý phía server cho các "
        "câu dài 2–4 giây (tiêu chí #3 đo trên RTX 5090 với model đã nạp sẵn).",
        "",
        "## Đối chiếu tiêu chí nghiệm thu SV2",
        "",
        "| Cấu hình | #1 Từ khóa ≥95% | #2 WER mixed ≤15% | #2 WER vi ≤10% | #3 câu ~3 s ≤300 ms | #4 im lặng/ồn → rỗng |",
        "|---|---|---|---|---|---|",
    ]
    for cfg in configs:
        s = results[cfg.name]
        g = s["groups"]
        c1 = None if s["kw_acc"] is None else s["kw_acc"] >= KW_TARGET
        c2a = None if g["mixed"]["wer"] is None else g["mixed"]["wer"] <= WER_MIXED_TARGET
        c2b = None if g["vi"]["wer"] is None else g["vi"]["wer"] <= WER_VI_TARGET
        c3 = None if s["mid_p50"] is None else s["mid_p50"] <= LATENCY_TARGET_MS
        c4 = None if not s["negatives"] else s["negatives_nonempty"] == 0
        lines.append(f"| {cfg.name} | {_verdict(c1)} | {_verdict(c2a)} | {_verdict(c2b)} | {_verdict(c3)} | {_verdict(c4)} |")
    lines += ["", "## Cấu hình đã chạy", "", "| Tên | URL | language | prompt | Service (/health) |", "|---|---|---|---|---|"]
    for cfg in configs:
        h = cfg.health
        svc = ", ".join(f"{k}={h[k]}" for k in ("engine", "model", "device", "compute_type", "language") if h.get(k))
        svc = svc or str(h.get("status", "?"))
        prompt = "(mặc định service)" if cfg.prompt is None else cfg.prompt
        lang = "(mặc định service)" if cfg.language is None else cfg.language
        lines.append(f"| {cfg.name} | `{cfg.url}` | {lang} | {_cell(prompt)} | {_cell(svc)} |")
    lines += ["", "## Từ khóa bị nhận sai nhiều nhất", ""]
    for cfg in configs:
        misses = results[cfg.name]["kw_misses"]
        top = ", ".join(f"{k} ({v})" for k, v in list(misses.items())[:10]) or "không có"
        lines.append(f"- **{cfg.name}**: {top}")
    lines += ["", "## Câu sai nhiều nhất (tối đa 8 câu mỗi cấu hình)", ""]
    for cfg in configs:
        rows = sorted(results[cfg.name]["rows"], key=lambda r: (-(r["wer"] or 0), r["id"]))
        rows = [r for r in rows if r["wer"] > 0 or r.get("error")][:8]
        lines += [f"### {cfg.name}", ""]
        if not rows:
            lines += ["Không có câu sai.", ""]
            continue
        lines += ["| Mã | Nhóm | Tham chiếu | Kết quả | WER | Ghi chú |", "|---|---|---|---|---|---|"]
        for r in rows:
            note = r.get("error") or (f"dropped={r['dropped']}" if r.get("dropped") else "")
            lines.append(
                f"| {r['id']} | {r['group']} | {_cell(r['reference'])} | {_cell(r['hypothesis'])} | "
                f"{r['wer'] * 100:.0f}% | {_cell(note)} |"
            )
        lines.append("")
    lines += [
        "## Cách chạy lại",
        "",
        "```bash",
        f"python tools/tests/asr/eval.py --manifest {args.manifest}"
        + (f" --configs {args.configs}" if args.configs else f" --url {args.url}")
        + f" --out {args.out}",
        "```",
        "",
    ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--manifest", required=True, help="JSONL manifest (audio, reference, group)")
    p.add_argument("--configs", help="YAML/JSON list of configs (see configs.example.yaml)")
    p.add_argument("--url", default=DEFAULT_URL, help="endpoint for the default configs")
    p.add_argument("--prompt", default=DEFAULT_PROMPT, help="prompt for the default '+prompt' config")
    p.add_argument("--keywords", default=str(HERE / "keywords.txt"))
    p.add_argument("--out", default=str(HERE / "RESULTS.md"), help="Markdown report to write")
    p.add_argument("--details-dir", help="per-utterance JSONL (default: <manifest dir>/eval_details)")
    p.add_argument("--label", default="", help="banner shown at the top of the report")
    p.add_argument("--groups", default="", help="comma list to keep, e.g. mixed,vi")
    p.add_argument("--limit", type=int, default=0, help="only the first N items (quick check)")
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--no-warmup", action="store_true", help="do not send one untimed request first")
    args = p.parse_args(argv)

    items = asrlib.load_manifest(Path(args.manifest))
    if args.groups:
        keep = {g.strip() for g in args.groups.split(",")}
        items = [it for it in items if it["group"] in keep]
    if args.limit:
        items = items[: args.limit]
    if not items:
        print("manifest is empty", file=sys.stderr)
        return 2
    keywords = asrlib.load_keywords(Path(args.keywords))
    configs = load_configs(args.configs, args.url, args.prompt)
    details_dir = Path(args.details_dir) if args.details_dir else Path(args.manifest).parent / "eval_details"
    details_dir.mkdir(parents=True, exist_ok=True)

    results: Dict[str, Dict[str, Any]] = {}
    with httpx.Client() as client:
        for cfg in configs:
            cfg.health = fetch_health(client, cfg)
            cfg.name = cfg.name.replace("{model}", str(cfg.health.get("model", "asr")))
            print(f"== {cfg.name}  ({cfg.url}, language={cfg.language}, prompt={cfg.prompt!r})")
            print(f"   health: {json.dumps(cfg.health, ensure_ascii=False)[:300]}")
            if not args.no_warmup:
                transcribe(client, cfg, items[0]["audio_path"], args.timeout)
            rows = []
            for it in items:
                out = transcribe(client, cfg, it["audio_path"], args.timeout)
                row = score_item(it, out, keywords)
                rows.append(row)
                mark = "ERR" if row.get("error") else f"{row['wer'] * 100:5.1f}%"
                lat = row.get("latency_ms")
                print(f"   {it['id']:>6} {it['group']:<5} {mark} {'' if lat is None else f'{lat:6.0f} ms'}  "
                      f"{row.get('error') or row['hypothesis']}")
            summary = summarize(rows)
            summary["rows"] = rows
            results[cfg.name] = summary
            safe = re.sub(r"[^A-Za-z0-9_.+-]", "_", cfg.name)
            with (details_dir / f"{safe}.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
                for row in rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"   WER {_pct(summary['wer'])} (raw {_pct(summary['wer_raw'])})  keywords {_pct(summary['kw_acc'])}  "
                  f"negatives non-empty {summary['negatives_nonempty']}/{summary['negatives']}  "
                  f"p50 {_ms(summary['lat_p50'])} ms  p90 {_ms(summary['lat_p90'])} ms")

    report = render_markdown(results, configs, items, args, keywords)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report, encoding="utf-8", newline="\n")
    print(f"report written to {args.out}; details in {details_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
