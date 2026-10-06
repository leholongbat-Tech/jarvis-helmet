#!/usr/bin/env python3
"""Audio helpers for the e2e scenarios.

    tts      synthesize the scenario sentences with an OpenAI-compatible TTS endpoint
             (the Jarvis tts-router: POST /v1/audio/speech) into 16 kHz mono WAVs
    convert  convert recordings (wav/m4a/mp3/ogg/flac... via PyAV) to 16 kHz mono WAV
    play     play WAVs through the PC speaker in a loop (E13 with the real helmet)

Files are named like device_sim expects in tools/tests/e2e/audio/: <ID>.wav for the
first sentence of a scenario, <ID>_<n>.wav for the n-th one (E13 has four).
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import device_sim  # noqa: E402

LEAD_MS, TAIL_MS = 300, 300


def decode_bytes_16k(data: bytes):
    av = device_sim._av()
    np = device_sim._np()
    rs = av.AudioResampler(format="s16", layout="mono", rate=device_sim.UPLINK_RATE)
    chunks = []
    with av.open(io.BytesIO(data)) as container:
        for frame in container.decode(audio=0):
            chunks += [f.to_ndarray().reshape(-1) for f in rs.resample(frame)]
    chunks += [f.to_ndarray().reshape(-1) for f in rs.resample(None)]
    if not chunks:
        raise SystemExit("no audio in the TTS response")
    return np.concatenate(chunks).astype(np.int16)


def pad(pcm):
    np = device_sim._np()
    rate = device_sim.UPLINK_RATE
    return np.concatenate([np.zeros(rate * LEAD_MS // 1000, np.int16), pcm,
                           np.zeros(rate * TAIL_MS // 1000, np.int16)])


def synthesize(url: str, text: str, voice: Optional[str], model: str, api_key: str,
               timeout: float) -> bytes:
    body = {"model": model, "input": text, "response_format": "wav"}
    if voice:
        body["voice"] = voice
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, OSError) as e:
        raise SystemExit(f"TTS request failed: {e}")


def cmd_tts(args) -> int:
    out = Path(args.out)
    jobs = []
    if args.text:
        jobs.append((args.name or "custom", args.text))
    else:
        ids = args.scenario or sorted(device_sim.SCENARIOS, key=lambda s: int(s[1:]))
        for sid in ids:
            sc = device_sim.SCENARIOS[sid.upper()]
            for i, text in enumerate(sc.texts):
                name = sid.upper() if len(sc.texts) == 1 else f"{sid.upper()}_{i + 1}"
                jobs.append((name, text))
    for name, text in jobs:
        data = synthesize(args.tts_url, text, args.voice, args.model, args.api_key, args.timeout)
        pcm = pad(decode_bytes_16k(data))
        path = out / f"{name}.wav"
        device_sim.write_wav(path, pcm, device_sim.UPLINK_RATE)
        print(f"{path}  {len(pcm) / device_sim.UPLINK_RATE:.1f}s  {text}")
    return 0


def cmd_convert(args) -> int:
    out = Path(args.out)
    for src in args.files:
        pcm = device_sim.load_audio_16k(Path(src))
        path = out / (Path(src).stem + ".wav")
        device_sim.write_wav(path, pcm, device_sim.UPLINK_RATE)
        start, end = device_sim.speech_bounds_ms(pcm)
        print(f"{path}  {len(pcm) / device_sim.UPLINK_RATE:.1f}s  speech {start}-{end} ms")
    return 0


def play_file(path: Path) -> None:
    if sys.platform == "win32":
        import winsound
        winsound.PlaySound(str(path), winsound.SND_FILENAME)
        return
    for player in (["aplay", "-q"], ["paplay"], ["afplay"]):
        if shutil.which(player[0]):
            subprocess.run(player + [str(path)], check=False)
            return
    raise SystemExit("no audio player found (aplay, paplay or afplay)")


def cmd_play(args) -> int:
    files = [Path(f) for f in args.files]
    for i in range(args.repeat):
        path = files[i % len(files)]
        print(f"[{time.strftime('%H:%M:%S')}] {i + 1}/{args.repeat} {path.name}", flush=True)
        play_file(path)
        time.sleep(args.interval)
    return 0


def main(argv: Optional[list] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    p = argparse.ArgumentParser(prog="make_wavs.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tts", help="synthesize scenario sentences via an OpenAI-style TTS")
    t.add_argument("--tts-url", required=True,
                   help="e.g. http://127.0.0.1:8880/v1/audio/speech (docker-compose.debug.yml)")
    t.add_argument("--voice", help="voice id (default: the server's TTS_VOICE)")
    t.add_argument("--model", default="tts-1")
    t.add_argument("--api-key", default="")
    t.add_argument("--scenario", action="append", help="only these scenario ids")
    t.add_argument("--text", help="one custom sentence instead of the scenarios")
    t.add_argument("--name", help="file name (without .wav) for --text")
    t.add_argument("--timeout", type=float, default=60.0)
    t.add_argument("--out", default=str(device_sim.DEFAULT_AUDIO_DIR))
    c = sub.add_parser("convert", help="convert recordings to 16 kHz mono WAV")
    c.add_argument("files", nargs="+")
    c.add_argument("--out", default=str(device_sim.DEFAULT_AUDIO_DIR))
    pl = sub.add_parser("play", help="play WAVs through the speaker in a loop")
    pl.add_argument("files", nargs="+")
    pl.add_argument("--repeat", type=int, default=50)
    pl.add_argument("--interval", type=float, default=8.0, help="seconds between commands")
    args = p.parse_args(argv)
    return {"tts": cmd_tts, "convert": cmd_convert, "play": cmd_play}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
