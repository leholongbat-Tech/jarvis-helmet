"""Clip generation driver shared by gen_positives.py and gen_negatives.py.

One TTS call per job; each call yields `variants` clips (different speed / tempo / pitch /
level), which keeps the GPU time low. Output: 16 kHz mono PCM16 WAV + manifest.jsonl.
Re-running with the same arguments and seed skips clips that already exist.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import wwcommon as ww


@dataclass
class Job:
    index: int
    engine: str                 # "tts", "piper" or "dry"
    voice: str
    text: str
    extra: dict = field(default_factory=dict)
    clips: list = field(default_factory=list)   # [(clip_index, Variation, path)]


def add_common_arguments(parser: argparse.ArgumentParser, default_out: str, default_count: int) -> None:
    group = parser.add_argument_group("TTS (VieNeu via tts-router or directly)")
    group.add_argument("--api-url", default="http://localhost:8880/v1/audio/speech",
                       help="OpenAI-style speech endpoint (tts-router :8880 or vieneu :8000); "
                            "'none' = Piper only")
    group.add_argument("--api-key", default=None, help="Bearer key (VIENEU_API_KEY), if set")
    group.add_argument("--model", default="vieneu-v3-turbo")
    group.add_argument("--tts-rate", type=int, default=24000,
                       help="sample rate requested from the server (resampled to 16 kHz)")
    group.add_argument("--voices", default="all",
                       help="comma separated voice names, or 'all' = GET /v1/voices")
    group.add_argument("--exclude-voices", default="", help="comma separated voices to skip")
    group.add_argument("--sampling-params", action="store_true",
                       help="vary VieNeu temperature/top_p per call (direct VieNeu only)")
    group = parser.add_argument_group("Piper (optional, pip install piper-tts)")
    group.add_argument("--piper-voice", action="append", default=[], type=Path,
                       help="vi_VN .onnx voice (repeatable), e.g. vi_VN-vivos-x_low.onnx")
    group.add_argument("--piper-share", type=float, default=0.25,
                       help="fraction of calls done with Piper when --piper-voice is given")
    group = parser.add_argument_group("output and variation")
    group.add_argument("--out", type=Path, default=Path(default_out))
    group.add_argument("--count", type=int, default=default_count, help="number of clips")
    group.add_argument("--variants", type=int, default=3, help="clips per TTS call")
    group.add_argument("--speed", type=float, nargs=2, default=(0.85, 1.20), metavar=("MIN", "MAX"),
                       help="tape speed range (tempo+pitch)")
    group.add_argument("--tempo", type=float, nargs=2, default=(0.90, 1.12), metavar=("MIN", "MAX"),
                       help="WSOLA tempo range, applied to half of the clips")
    group.add_argument("--semitones", type=float, default=2.0,
                       help="max pitch shift (+/-), applied to half of the clips")
    group.add_argument("--peak", type=float, nargs=2, default=(-12.0, -1.0), metavar=("MIN", "MAX"),
                       help="peak level range in dBFS")
    group.add_argument("--workers", type=int, default=4, help="parallel TTS requests")
    group.add_argument("--seed", type=int, default=1)
    group.add_argument("--dry-run", action="store_true",
                       help="no TTS: write synthetic speech-like clips (pipeline test)")
    group.add_argument("--plan-only", action="store_true", help="print the plan and exit")


def resolve_voices(args, client: ww.TtsClient | None) -> list[str]:
    if args.voices.strip().lower() == "all":
        if client is None:
            return ["dry-voice-a", "dry-voice-b", "dry-voice-c"]
        voices = client.list_voices()
        if not voices:
            raise SystemExit("error: the server returned no voices; pass --voices")
    else:
        voices = [ww.nfc(v.strip()) for v in args.voices.split(",") if v.strip()]
    excluded = {ww.nfc(v.strip()) for v in args.exclude_voices.split(",") if v.strip()}
    voices = [v for v in voices if v not in excluded]
    if not voices:
        raise SystemExit("error: no voices left")
    return voices


def build_plan(args, texts: list[str], voices: list[str], piper_voices: list, prefix: str,
               rng: random.Random, cycle_texts: bool = False) -> list[Job]:
    """cycle_texts=False: voices round robin, random texts (positives).
    cycle_texts=True: texts in turn so each gets the same share, random voices (negatives)."""
    variants = max(1, args.variants)
    calls = math.ceil(args.count / variants)
    piper_calls = int(round(calls * args.piper_share)) if piper_voices else 0
    order = list(texts)
    if cycle_texts:
        rng.shuffle(order)
    jobs: list[Job] = []
    clip_index = 0
    for call in range(calls):
        if args.dry_run:
            engine = "dry"
            voice = rng.choice(voices) if cycle_texts else voices[call % len(voices)]
        elif call < piper_calls:
            engine = "piper"
            pv = piper_voices[call % len(piper_voices)]
            speaker = rng.randrange(pv.num_speakers)
            voice = f"{pv.name}#{speaker}"
        else:
            engine = "tts"
            voice = rng.choice(voices) if cycle_texts else voices[(call - piper_calls) % len(voices)]
        text = order[call % len(order)] if cycle_texts else rng.choice(order)
        job = Job(index=call, engine=engine, voice=voice, text=text)
        if engine == "tts" and args.sampling_params:
            job.extra = {"temperature": round(rng.uniform(0.6, 1.0), 2),
                         "top_p": round(rng.uniform(0.85, 0.98), 2)}
        if engine == "piper":
            job.extra = {"length_scale": round(rng.uniform(0.85, 1.25), 2),
                         "noise_scale": round(rng.uniform(0.4, 0.9), 2),
                         "noise_w": round(rng.uniform(0.5, 1.0), 2)}
        for _ in range(variants):
            if clip_index >= args.count:
                break
            variation = ww.random_variation(rng, tuple(args.speed), tuple(args.tempo),
                                            args.semitones, tuple(args.peak))
            name = f"{prefix}_{clip_index:05d}_{engine}_{ww.slugify(voice, 24)}.wav"
            job.clips.append((clip_index, variation, args.out / name))
            clip_index += 1
        jobs.append(job)
    return jobs


def summarize(jobs: list[Job]) -> str:
    by_engine: dict[str, int] = {}
    by_voice: dict[str, int] = {}
    for job in jobs:
        by_engine[job.engine] = by_engine.get(job.engine, 0) + len(job.clips)
        by_voice[job.voice] = by_voice.get(job.voice, 0) + len(job.clips)
    clips = sum(len(job.clips) for job in jobs)
    lines = [f"{len(jobs)} synthesis calls -> {clips} clips",
             "per engine: " + ", ".join(f"{k}={v}" for k, v in sorted(by_engine.items())),
             f"voices: {len(by_voice)}"]
    return "\n".join(lines)


def run(args, texts: list[str], prefix: str, kind: str,
        cycle_texts: bool = False) -> tuple[int, int]:
    """Generate the clips described by args; returns (clips written, failed calls)."""
    rng = random.Random(args.seed)
    if not 0.0 <= args.piper_share <= 1.0:
        raise SystemExit("error: --piper-share must be between 0 and 1")
    piper_only = args.api_url.strip().lower() == "none"
    if piper_only and not args.dry_run:
        if not args.piper_voice:
            raise SystemExit("error: --api-url none needs at least one --piper-voice")
        args.piper_share = 1.0
    client = None if args.dry_run or piper_only else ww.TtsClient(
        url=args.api_url, api_key=args.api_key, model=args.model, rate=args.tts_rate)
    piper_voices = []
    if not args.dry_run:
        for path in args.piper_voice:
            piper_voices.append(ww.PiperVoice(path))
    voices = ["piper"] if piper_only and not args.dry_run else resolve_voices(args, client)
    jobs = build_plan(args, texts, voices, piper_voices, prefix, rng, cycle_texts)
    print(summarize(jobs))
    if args.plan_only:
        return 0, 0

    args.out.mkdir(parents=True, exist_ok=True)
    manifest = args.out / "manifest.jsonl"
    lock = threading.Lock()
    piper_by_name = {pv.name: pv for pv in piper_voices}
    written = 0
    failures = 0

    def synthesize(job: Job) -> np.ndarray:
        if job.engine == "dry":
            return ww.synthetic_speech(random.Random(args.seed * 100003 + job.index))
        if job.engine == "piper":
            name, speaker = job.voice.rsplit("#", 1)
            return piper_by_name[name].synthesize(job.text, int(speaker), **job.extra)
        return client.synthesize(job.text, job.voice, job.extra)

    def work(job: Job) -> int:
        pending = [clip for clip in job.clips if not clip[2].exists()]
        if not pending:
            return 0
        audio = synthesize(job)
        count = 0
        for clip_index, variation, path in pending:
            clip = variation.apply(audio)
            ww.write_wav(path, clip)
            record = {"file": path.name, "kind": kind, "text": job.text, "engine": job.engine,
                      "voice": job.voice, "call": job.index, "clip": clip_index,
                      "duration_s": round(len(clip) / ww.TARGET_RATE, 3), **variation.as_dict(),
                      **({"tts": job.extra} if job.extra else {})}
            with lock:
                ww.append_jsonl(manifest, record)
            count += 1
        return count

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(work, job): job for job in jobs}
        for done, future in enumerate(as_completed(futures), 1):
            job = futures[future]
            try:
                written += future.result()
            except Exception as error:  # noqa: BLE001 - keep going, report at the end
                failures += 1
                print(f"warning: call {job.index} ({job.engine} {job.voice!r}, {job.text!r}) "
                      f"failed: {error}", file=sys.stderr)
            if done % 50 == 0 or done == len(jobs):
                print(f"  {done}/{len(jobs)} calls, {written} clips written")
    print(f"done: {written} new clips in {args.out} ({failures} failed calls)")
    return written, failures


def exit_code(written: int, failures: int) -> int:
    """0 = fine, 1 = nothing could be generated, 2 = partial failure (re-run to resume)."""
    if failures == 0:
        return 0
    return 1 if written == 0 else 2
