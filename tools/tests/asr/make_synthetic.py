#!/usr/bin/env python3
"""Generate a SMALL SYNTHETIC SMOKE DATASET for the ASR tools. NOT the real dataset.

Speech comes from the project TTS router (POST /v1/audio/speech, all groups) when it is up,
otherwise from Windows SAPI (English voices only; Vietnamese/mixed sentences are skipped unless a
vi-VN voice is installed, or synthesized with espeak-ng when --espeak is given: robotic, only
good enough to exercise the Vietnamese code path). Every utterance gets xiaozhi-like padding
(600 ms pre-roll, 700 ms tail) and a faint noise floor. Negative samples (30 s silence,
white/pink noise, mains hum) are added for the "silence/noise -> empty text" criterion.
Every manifest line has "synthetic": true.

    python tools/tests/asr/make_synthetic.py                      # auto: router, else SAPI
    python tools/tests/asr/make_synthetic.py --espeak             # + espeak-ng for vi/mixed
    python tools/tests/asr/make_synthetic.py --engine router --tts-url http://127.0.0.1:8880/v1/audio/speech
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import asrlib  # noqa: E402

HERE = Path(__file__).resolve().parent
SR = 16000
DEFAULT_OUT = HERE / "recordings" / "synthetic"
DEFAULT_TTS = "http://127.0.0.1:8880/v1/audio/speech"
CULTURE = {"en": "en", "vi": "vi", "mixed": "vi"}

SAPI_SCRIPT = r"""
param([string]$Jobs)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
# PS 5.1 emits a JSON array as ONE pipeline object: do not wrap it in @().
# (Variable names are case-insensitive: never reuse $jobs, it is the [string] parameter.)
$jobList = Get-Content -Raw -Encoding UTF8 $Jobs | ConvertFrom-Json
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voices = @($synth.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo })
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$out = @()
foreach ($j in $jobList) {
  $cands = @($voices | Where-Object { $_.Culture.Name -like ($j.culture + '*') })
  if ($j.voice) { $cands = @($voices | Where-Object { $_.Name -eq $j.voice }) }
  if ($cands.Count -eq 0) { $out += [pscustomobject]@{ id = $j.id; ok = $false; reason = "no SAPI voice for $($j.culture)" }; continue }
  $v = $cands[$j.index % $cands.Count]
  $synth.SelectVoice($v.Name)
  $synth.Rate = [int]$j.rate
  $synth.SetOutputToWaveFile($j.path, $fmt)
  $synth.Speak([string]$j.text)
  $synth.SetOutputToNull()
  $out += [pscustomobject]@{ id = $j.id; ok = $true; voice = $v.Name }
}
ConvertTo-Json -InputObject $out -Compress
"""


def router_base(url: str) -> str:
    return url.split("/v1/")[0].rstrip("/")


def router_available(url: str) -> bool:
    import httpx

    try:
        return httpx.get(router_base(url) + "/health", timeout=3.0).status_code == 200
    except httpx.HTTPError:
        return False


def synthesize_router(client, url: str, text: str, voice: Optional[str] = None) -> np.ndarray:
    """One sentence through the TTS router -> float32 mono 16 kHz."""
    payload = {"input": text, "response_format": "wav", "sample_rate": SR, "stream": False}
    if voice:
        payload["voice"] = voice
    resp = client.post(url, json=payload, timeout=120.0)
    resp.raise_for_status()
    data = resp.content
    if data[:4] == b"RIFF":
        audio, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
        audio = audio.mean(axis=1)
    else:  # raw s16le PCM (router default format)
        sr = int(resp.headers.get("x-sample-rate", SR))
        audio = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    return asrlib.resample(audio, sr, SR)


def synthesize_sapi(jobs: List[Dict], workdir: Path) -> Dict[str, Dict]:
    """Run all SAPI jobs in one PowerShell process; returns {id: {"ok", "voice"|"reason"}}."""
    if sys.platform != "win32" or not shutil.which("powershell"):
        return {j["id"]: {"ok": False, "reason": "SAPI needs Windows PowerShell"} for j in jobs}
    script, jobs_file = workdir / "sapi.ps1", workdir / "sapi_jobs.json"
    script.write_text(SAPI_SCRIPT, encoding="utf-8-sig")
    jobs_file.write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Jobs", str(jobs_file)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"SAPI failed: {proc.stderr.strip()[:500]}")
    parsed = json.loads(proc.stdout.strip() or "[]")
    parsed = parsed if isinstance(parsed, list) else [parsed]
    return {r["id"]: r for r in parsed}


def synthesize_espeak(jobs: List[Dict], workdir: Path, image: str) -> Dict[str, Dict]:
    """Vietnamese espeak-ng (local binary, else a throwaway Docker container) -> {id: result}."""
    lines = "".join(f"{j['id']}\t{' '.join(j['text'].split())}\n" for j in jobs)
    (workdir / "espeak_jobs.tsv").write_text(lines, encoding="utf-8", newline="\n")
    loop = ('while IFS="$(printf \'\\t\')" read -r id text; do '
            'espeak-ng -v vi -s 140 -w "$OUT/$id.wav" "$text" || echo "fail $id"; done < "$OUT/espeak_jobs.tsv"')
    if shutil.which("espeak-ng") and shutil.which("sh"):
        cmd = ["sh", "-c", loop.replace("$OUT", str(workdir).replace("\\", "/"))]
    elif shutil.which("docker"):
        cmd = ["docker", "run", "--rm", "-v", f"{workdir}:/out", image, "sh", "-c",
               "apk add --no-cache espeak-ng >/dev/null && " + loop.replace("$OUT", "/out")]
    else:
        return {j["id"]: {"ok": False, "reason": "espeak-ng and docker not found"} for j in jobs}
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    if proc.returncode != 0:
        raise RuntimeError(f"espeak-ng failed: {proc.stderr.strip()[:500]}")
    return {j["id"]: ({"ok": True, "voice": "espeak-ng:vi"} if Path(j["path"]).exists()
                      else {"ok": False, "reason": "espeak-ng produced no file"}) for j in jobs}


def voice_source(voice: Optional[str]) -> str:
    voice = voice or "?"
    return voice if voice.startswith("espeak") else f"sapi:{voice}"


def finish(audio: np.ndarray, pad_ms, noise_dbfs: Optional[float], rng) -> np.ndarray:
    pre, post = (np.zeros(int(SR * ms / 1000), dtype=np.float32) for ms in pad_ms)
    out = np.concatenate([pre, audio.astype(np.float32), post])
    if noise_dbfs is not None:
        out = out + rng.normal(0.0, 10 ** (noise_dbfs / 20.0), out.size).astype(np.float32)
    return np.clip(out, -1.0, 1.0)


def colored_noise(n: int, kind: str, rms_dbfs: float, rng) -> np.ndarray:
    white = rng.normal(0.0, 1.0, n)
    if kind == "pink":
        spec = np.fft.rfft(white)
        f = np.arange(spec.size, dtype=np.float64)
        f[0] = 1.0
        white = np.fft.irfft(spec / np.sqrt(f), n)
    x = white / np.sqrt(np.mean(white**2)) * 10 ** (rms_dbfs / 20.0)
    return x.astype(np.float32)


def negatives(rng, seconds: float = 30.0) -> List[Dict]:
    n = int(SR * seconds)
    t = np.arange(n) / SR
    hum = sum(np.sin(2 * np.pi * 50 * k * t) / k for k in (1, 2, 3, 5))
    hum = hum / np.sqrt(np.mean(hum**2)) * 10 ** (-35 / 20)
    return [
        {"id": "N01", "note": "digital silence", "audio": np.zeros(n, dtype=np.float32)},
        {"id": "N02", "note": "quiet room floor (-65 dBFS white)", "audio": colored_noise(n, "white", -65, rng)},
        {"id": "N03", "note": "white noise -30 dBFS", "audio": colored_noise(n, "white", -30, rng)},
        {"id": "N04", "note": "pink noise -30 dBFS", "audio": colored_noise(n, "pink", -30, rng)},
        {"id": "N05", "note": "50 Hz hum -35 dBFS + floor",
         "audio": (hum + colored_noise(n, "white", -60, rng)).astype(np.float32)},
    ]


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=str(DEFAULT_OUT), help="output dir (gitignored recordings/...)")
    p.add_argument("--engine", choices=("auto", "router", "sapi"), default="auto")
    p.add_argument("--tts-url", default=DEFAULT_TTS)
    p.add_argument("--voice", default=None, help="router voice or exact SAPI voice name")
    p.add_argument("--sentences", default=str(HERE / "sentences.md"))
    p.add_argument("--groups", default="mixed,vi,en")
    p.add_argument("--limit", type=int, default=0, help="max sentences per group (0 = all)")
    p.add_argument("--pad-ms", default="600,700", help="silence before,after each utterance")
    p.add_argument("--noise-dbfs", default="-70", help="noise floor added to speech, or 'off'")
    p.add_argument("--rate", type=int, default=0, help="SAPI speaking rate -10..10")
    p.add_argument("--espeak", action="store_true",
                   help="SAPI mode: synthesize vi/mixed sentences with espeak-ng (robotic, smoke only)")
    p.add_argument("--espeak-image", default="alpine:latest", help="image used when espeak-ng is not installed")
    p.add_argument("--no-negatives", action="store_true")
    p.add_argument("--seed", type=int, default=1234)
    args = p.parse_args(argv)

    out_dir = Path(args.out)
    (out_dir / "audio").mkdir(parents=True, exist_ok=True)
    pad_ms = [int(v) for v in args.pad_ms.split(",")]
    noise = None if args.noise_dbfs.lower() == "off" else float(args.noise_dbfs)
    rng = np.random.default_rng(args.seed)
    groups = [g.strip() for g in args.groups.split(",") if g.strip()]
    sentences = [s for s in asrlib.load_sentences(Path(args.sentences)) if s["group"] in groups]
    if args.limit:
        kept: List[Dict] = []
        per_group: Dict[str, int] = {}
        for s in sentences:
            if per_group.get(s["group"], 0) < args.limit:
                kept.append(s)
                per_group[s["group"]] = per_group.get(s["group"], 0) + 1
        sentences = kept

    engine = args.engine
    if engine == "auto":
        engine = "router" if router_available(args.tts_url) else "sapi"
    elif engine == "router" and not router_available(args.tts_url):
        print(f"TTS router not reachable at {args.tts_url}", file=sys.stderr)
        return 2
    print(f"engine: {engine}")

    items: List[Dict] = []
    if engine == "router":
        import httpx

        with httpx.Client() as client:
            for s in sentences:
                audio = synthesize_router(client, args.tts_url, s["text"], args.voice)
                rel = f"audio/{s['id']}.wav"
                sf.write(out_dir / rel, finish(audio, pad_ms, noise, rng), SR, subtype="PCM_16")
                items.append({"id": s["id"], "audio": rel, "reference": s["text"], "group": s["group"],
                              "synthetic": True, "source": f"router:{args.voice or 'default'}"})
                print(f"  {s['id']} ok")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            jobs = [{"id": s["id"], "text": s["text"], "culture": CULTURE[s["group"]], "voice": args.voice,
                     "rate": args.rate, "index": i, "path": str(tmpdir / f"{s['id']}.wav")}
                    for i, s in enumerate(sentences)]
            done = synthesize_sapi(jobs, tmpdir)
            if args.espeak:
                missing = [j for j in jobs if not done.get(j["id"], {}).get("ok") and j["culture"] == "vi"]
                if missing:
                    done.update(synthesize_espeak(missing, tmpdir, args.espeak_image))
            for s, job in zip(sentences, jobs):
                res = done.get(s["id"], {"ok": False, "reason": "no result"})
                if not res.get("ok"):
                    print(f"  {s['id']} skipped: {res.get('reason')}")
                    continue
                audio, sr = sf.read(job["path"], dtype="float32")
                rel = f"audio/{s['id']}.wav"
                sf.write(out_dir / rel, finish(asrlib.resample(audio, sr, SR), pad_ms, noise, rng), SR,
                         subtype="PCM_16")
                items.append({"id": s["id"], "audio": rel, "reference": s["text"], "group": s["group"],
                              "synthetic": True, "source": voice_source(res.get("voice"))})
                print(f"  {s['id']} ok ({res.get('voice')})")

    if not args.no_negatives:
        for neg in negatives(rng):
            rel = f"audio/{neg['id']}.wav"
            sf.write(out_dir / rel, neg["audio"], SR, subtype="PCM_16")
            items.append({"id": neg["id"], "audio": rel, "reference": "", "group": "noise",
                          "synthetic": True, "source": "generated", "note": neg["note"]})
    asrlib.write_manifest(out_dir / "manifest.jsonl", items)
    (out_dir / "README.txt").write_text(
        "DU LIEU SMOKE TONG HOP (TTS/SAPI + nhieu sinh ra) - KHONG phai bo thu am that bang mu.\n"
        "Chi dung de kiem tra cong cu eval.py va service ASR. Xem tools/tests/asr/README.md.\n",
        encoding="utf-8",
    )
    counts = {g: sum(1 for it in items if it["group"] == g) for g in asrlib.GROUP_ORDER}
    print(f"wrote {len(items)} items to {out_dir / 'manifest.jsonl'} {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
