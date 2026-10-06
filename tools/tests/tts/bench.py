#!/usr/bin/env python3
"""TTS bench for the Jarvis tts-router (SV4 §2, acceptance 1-2).

For every sentence of sentences.txt: POST /v1/audio/speech (streaming PCM), measure the time
to the first audio byte (TTFA) and the total time, save a WAV for listening, check that the
audio is not silent and that its duration is plausible for the (normalized) text, then print
a table with medians and the SV4 criterion "TTFA <= 300 ms for sentences <= 15 words".

--voices-scan synthesizes one sentence with every (male) VieNeu voice from /v1/voices and
ranks them by median pitch (F0): a lower F0 means a deeper voice. Listen to the WAVs to choose.

Examples:
  python tools/tests/tts/bench.py --url http://127.0.0.1:8880
  python tools/tests/tts/bench.py --only 1,5,14,20 --repeat 3 --json out.json
  python tools/tests/tts/bench.py --voices-scan
Needs: httpx, numpy (both in the repo .venv). Python >= 3.9.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.parse
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

import httpx
import numpy as np

HERE = Path(__file__).resolve().parent
DEFAULT_SCAN_TEXT = "Vâng, thưa ngài. Hệ thống đã sẵn sàng, tôi luôn ở đây để phục vụ ngài."


@dataclass
class Sentence:
    index: int
    group: str
    text: str


@dataclass
class Result:
    index: int
    group: str
    text: str
    words: int
    status: int = 0
    ttfa_ms: Optional[float] = None
    total_ms: Optional[float] = None
    audio_s: float = 0.0
    rtf: Optional[float] = None
    rms_dbfs: float = -120.0
    peak_dbfs: float = -120.0
    voiced_ratio: float = 0.0
    route: str = ""
    normalized: str = ""
    spoken_words: int = 0
    wav: str = ""
    error: str = ""
    checks: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == 200 and not self.checks


def load_sentences(path: Path) -> List[Sentence]:
    out, group = [], ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            m = re.match(r"#\s*@(\S+)", line)
            if m:
                group = m.group(1)
            continue
        out.append(Sentence(len(out) + 1, group, line))
    return out


def count_words(text: str) -> int:
    return len(re.findall(r"[^\W_]+(?:['.][^\W_]+)*", text))


def slug(text: str, n: int = 32) -> str:
    s = re.sub(r"[^\w]+", "_", text, flags=re.UNICODE).strip("_")
    return s[:n] or "x"


def write_wav(path: Path, pcm: bytes, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm[: len(pcm) - len(pcm) % 2])


def audio_stats(pcm: bytes, rate: int):
    """(rms dBFS, peak dBFS, voiced ratio of 20 ms frames above -40 dBFS)."""
    x = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype="<i2").astype(np.float64) / 32768.0
    if x.size == 0:
        return -120.0, -120.0, 0.0
    rms = float(np.sqrt(np.mean(x * x)))
    peak = float(np.max(np.abs(x)))
    hop = max(1, rate // 50)
    frames = x[: x.size // hop * hop].reshape(-1, hop)
    frame_db = 20 * np.log10(np.sqrt(np.mean(frames * frames, axis=1)) + 1e-9) if len(frames) else np.array([])
    voiced = float(np.mean(frame_db > -40)) if frame_db.size else 0.0
    return 20 * np.log10(rms + 1e-9), 20 * np.log10(peak + 1e-9), voiced


def median_f0(pcm: bytes, rate: int, fmin: float = 60.0, fmax: float = 300.0) -> Optional[float]:
    """Median pitch of voiced frames (autocorrelation, 40 ms windows, 10 ms hop)."""
    x = np.frombuffer(pcm[: len(pcm) - len(pcm) % 2], dtype="<i2").astype(np.float64) / 32768.0
    win, hop = int(rate * 0.04), int(rate * 0.01)
    lag_min, lag_max = int(rate / fmax), int(rate / fmin)
    f0s = []
    for start in range(0, x.size - win, hop):
        frame = x[start:start + win]
        frame = frame - frame.mean()
        energy = float(np.dot(frame, frame))
        if energy / win < 10 ** (-35 / 10):  # quieter than -35 dBFS
            continue
        spec = np.fft.rfft(frame * np.hanning(win), n=2 * win)
        ac = np.fft.irfft(spec * np.conj(spec))[:win]
        if ac[0] <= 0:
            continue
        seg = ac[lag_min:lag_max]
        if seg.size == 0:
            continue
        lag = int(np.argmax(seg)) + lag_min
        if ac[lag] / ac[0] > 0.45:
            f0s.append(rate / lag)
    return float(np.median(f0s)) if len(f0s) >= 5 else None


def synthesize(client: httpx.Client, url: str, text: str, voice: Optional[str], rate: int):
    body = {"input": text, "response_format": "pcm", "sample_rate": rate, "stream": True}
    if voice:
        body["voice"] = voice
    t0 = time.perf_counter()
    ttfa = None
    chunks = []
    with client.stream("POST", url.rstrip("/") + "/v1/audio/speech", json=body) as resp:
        if resp.status_code != 200:
            return resp.status_code, None, (time.perf_counter() - t0) * 1000, b"", resp.headers, resp.read().decode("utf-8", "replace")[:200]
        for chunk in resp.iter_raw():
            if chunk:
                if ttfa is None:
                    ttfa = (time.perf_counter() - t0) * 1000
                chunks.append(chunk)
        headers = resp.headers
    return 200, ttfa, (time.perf_counter() - t0) * 1000, b"".join(chunks), headers, ""


def run_one(client, args, s: Sentence, out_dir: Path, tag: str = "") -> Result:
    res = Result(s.index, s.group, s.text, count_words(s.text))
    try:
        status, ttfa, total, pcm, headers, err = synthesize(client, args.url, s.text, args.voice, args.rate)
    except httpx.HTTPError as exc:
        res.error = f"{type(exc).__name__}: {exc}"
        res.checks.append("request failed")
        return res
    res.status, res.ttfa_ms, res.total_ms = status, ttfa, total
    if status != 200:
        res.error = err
        res.checks.append(f"HTTP {status}")
        return res
    res.route = headers.get("x-tts-route", "")
    res.normalized = urllib.parse.unquote(headers.get("x-tts-normalized", ""))
    res.spoken_words = count_words(res.normalized) or res.words
    res.audio_s = len(pcm) / 2 / args.rate
    res.rtf = (total / 1000) / res.audio_s if res.audio_s else None
    res.rms_dbfs, res.peak_dbfs, res.voiced_ratio = audio_stats(pcm, args.rate)
    name = f"{s.index:02d}_{slug(s.text)}{tag}.wav"
    write_wav(out_dir / name, pcm, args.rate)
    res.wav = str(out_dir / name)
    # plausibility: speech present, roughly 0.12-1.0 s per spoken word (normalized text)
    if res.audio_s == 0:
        res.checks.append("no audio")
    elif res.rms_dbfs < -45 or res.voiced_ratio < 0.2:
        res.checks.append("silent")
    per_word = res.audio_s / max(1, res.spoken_words)
    if res.audio_s and not 0.12 <= per_word <= 1.0:
        res.checks.append(f"duration {res.audio_s:.1f}s for {res.spoken_words} words")
    return res


def fmt(v, spec="{:.0f}") -> str:
    return "-" if v is None else spec.format(v)


def print_table(results: List[Result]) -> None:
    print(f"{'#':>2} {'grp':<8} {'words':>5} {'TTFA ms':>8} {'total ms':>9} {'audio s':>8} {'RTF':>5} "
          f"{'dBFS':>6} {'route':<14} {'ok':<4} text")
    for r in results:
        text = r.text if len(r.text) <= 46 else r.text[:43] + "..."
        flag = "ok" if r.ok else "FAIL"
        print(f"{r.index:>2} {r.group:<8} {r.words:>5} {fmt(r.ttfa_ms):>8} {fmt(r.total_ms):>9} "
              f"{r.audio_s:>8.2f} {fmt(r.rtf, '{:.2f}'):>5} {r.rms_dbfs:>6.1f} {r.route:<14} {flag:<4} {text}")
        if r.checks or r.error:
            print(f"   -> {'; '.join(r.checks)} {r.error}")


def summarize(results: List[Result], ttfa_target: float, short_words: int) -> dict:
    ok = [r for r in results if r.status == 200 and r.ttfa_ms is not None]

    def med(vals):
        vals = [v for v in vals if v is not None]
        return statistics.median(vals) if vals else None

    short = [r for r in ok if r.words <= short_words]
    passed = [r for r in short if r.ttfa_ms <= ttfa_target]
    summary = {
        "requests": len(results),
        "ok": sum(1 for r in results if r.ok),
        "median_ttfa_ms": med([r.ttfa_ms for r in ok]),
        "median_total_ms": med([r.total_ms for r in ok]),
        "median_rtf": med([r.rtf for r in ok]),
        "p90_ttfa_ms": float(np.percentile([r.ttfa_ms for r in ok], 90)) if ok else None,
        "short_sentences": len(short),
        "short_ttfa_pass": len(passed),
    }
    print()
    print(f"requests={summary['requests']} ok={summary['ok']}  median TTFA={fmt(summary['median_ttfa_ms'])} ms "
          f"(p90 {fmt(summary['p90_ttfa_ms'])} ms)  median total={fmt(summary['median_total_ms'])} ms  "
          f"median RTF={fmt(summary['median_rtf'], '{:.2f}')}")
    print(f"SV4 criterion 1: TTFA <= {ttfa_target:.0f} ms for sentences <= {short_words} words: "
          f"{len(passed)}/{len(short)}")
    return summary


def voices_scan(client: httpx.Client, args, out_dir: Path) -> int:
    resp = client.get(args.url.rstrip("/") + "/v1/voices")
    resp.raise_for_status()
    voices = [v for v in resp.json().get("data", []) if v.get("engine") == "vieneu"]
    if args.gender != "all":
        voices = [v for v in voices if v.get("gender") == args.gender]
    if not voices:
        print("no voices found")
        return 1
    rows = []
    for v in voices:
        vid = v["id"]
        status, ttfa, total, pcm, _, err = synthesize(client, args.url, args.scan_text, vid, args.rate)
        if status != 200:
            print(f"{vid}: HTTP {status} {err}")
            continue
        path = out_dir / "voices" / f"{slug(vid, 40)}.wav"
        write_wav(path, pcm, args.rate)
        f0 = median_f0(pcm, args.rate)
        rows.append((f0 if f0 is not None else 1e9, vid, v.get("description", ""), ttfa, len(pcm) / 2 / args.rate, path))
    rows.sort()
    print(f"{'F0 Hz':>6}  {'voice':<16} {'TTFA ms':>8} {'audio s':>8}  description / wav")
    for f0, vid, desc, ttfa, dur, path in rows:
        print(f"{fmt(None if f0 >= 1e9 else f0):>6}  {vid:<16} {fmt(ttfa):>8} {dur:>8.2f}  {desc}  {path}")
    print("\nLower F0 = deeper voice. Listen to the WAVs, then set TTS_VOICE in server/.env.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8880", help="tts-router base URL")
    ap.add_argument("--sentences", default=str(HERE / "sentences.txt"))
    ap.add_argument("--voice", default=None, help="voice to request (default: router TTS_VOICE)")
    ap.add_argument("--rate", type=int, default=24000)
    ap.add_argument("--only", default="", help="comma-separated sentence numbers, e.g. 1,5,20")
    ap.add_argument("--limit", type=int, default=0, help="only the first N sentences")
    ap.add_argument("--repeat", type=int, default=1, help="runs per sentence (medians over all)")
    ap.add_argument("--warmup", type=int, default=1, help="untimed requests before measuring")
    ap.add_argument("--out", default=str(HERE / "out"), help="directory for WAV files")
    ap.add_argument("--json", default="", help="write all results to this JSON file")
    ap.add_argument("--show-text", action="store_true", help="print the normalized text per sentence")
    ap.add_argument("--ttfa-target", type=float, default=300.0)
    ap.add_argument("--short-words", type=int, default=15)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--voices-scan", action="store_true", help="rank VieNeu voices by pitch")
    ap.add_argument("--gender", default="male", choices=["male", "female", "all"])
    ap.add_argument("--scan-text", default=DEFAULT_SCAN_TEXT)
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    out_dir = Path(args.out)
    client = httpx.Client(timeout=httpx.Timeout(args.timeout, connect=5.0))
    try:
        health = client.get(args.url.rstrip("/") + "/health")
        print(f"router {args.url}: /health {health.status_code} {health.text[:300]}")
    except httpx.HTTPError as exc:
        print(f"router {args.url} unreachable: {exc}")
        return 2
    if args.voices_scan:
        return voices_scan(client, args, out_dir)

    sentences = load_sentences(Path(args.sentences))
    if args.only:
        wanted = {int(x) for x in args.only.split(",") if x.strip()}
        sentences = [s for s in sentences if s.index in wanted]
    if args.limit:
        sentences = sentences[: args.limit]
    for _ in range(max(0, args.warmup)):
        synthesize(client, args.url, "Xin chào.", args.voice, args.rate)

    results: List[Result] = []
    for rep in range(max(1, args.repeat)):
        for s in sentences:
            tag = f"_r{rep + 1}" if args.repeat > 1 else ""
            results.append(run_one(client, args, s, out_dir, tag))
    print_table(results)
    if args.show_text:
        print()
        for r in results:
            print(f"{r.index:>2}: {r.normalized}")
    summary = summarize(results, args.ttfa_target, args.short_words)
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": summary, "results": [asdict(r) for r in results]},
                                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"WAV files: {out_dir}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
