#!/usr/bin/env python3
"""Count microWakeWord detections on recordings, with the firmware's streaming algorithm.

Mirrors firmware/main/boards/jarvis-helmet/micro_wake_word.cc:
  * features: TFLM microfrontend via pymicro-features (40 ch, 30 ms window, 10 ms step)
  * int8 input: q = ((u16 * round(0.0390625/scale * 2^16) + 2^15) >> 16) + zero_point
  * one inference per `stride` new feature rows, uint8 probability
  * detection when sum(last W probabilities) > cutoff_u8 * W, cutoff_u8 = pct * 255 // 100
  * refractory: after a detection (and at start) 100 slices (1 s) whose latest probability is
    below the cutoff must pass before the next detection
The probability stream is computed once per file; every (cutoff, window) pair is then
simulated on it, so a whole grid costs one model pass.

Positives: folder(s) of short clips, one wake word each -> recall (clips with >= 1 detection).
Negatives: long recordings (podcast, TV, music, chatter) -> false accepts per hour.

Examples:
    python eval_wakeword.py --model quan_gia.tflite --positives rec/pos --negatives podcast.mp3
    python eval_wakeword.py --model quan_gia.tflite --positives rec/pos \
        --cutoffs 90:99:1 --windows 3:8:1 --csv out/grid.csv --json out/grid.json

Needs: pip install -r requirements-eval.txt (numpy, pymicro-features, ai-edge-litert); runs
on Linux/macOS (or in Docker on Windows, see README.md).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import wwcommon as ww

FEATURE_SCALE = 0.0390625        # pymicro-features float = uint16 feature * 1/25.6
FEATURES = 40
STEP_SAMPLES = 160               # 10 ms at 16 kHz
REFRACTORY_SLICES = 100          # 1 s, kRefractoryMs / kStepMs in the firmware
AUDIO_SUFFIXES = {".wav", ".flac", ".mp3", ".ogg", ".m4a", ".opus", ".aac", ".webm"}


# --------------------------------------------------------------------------- model / features

def interpreter_class():
    for module, attr in (("ai_edge_litert.interpreter", "Interpreter"),
                         ("tflite_runtime.interpreter", "Interpreter"),
                         ("tensorflow.lite", "Interpreter")):
        try:
            return getattr(__import__(module, fromlist=[attr]), attr)
        except Exception:  # noqa: BLE001
            continue
    raise SystemExit("error: no TFLite runtime; pip install ai-edge-litert (see requirements-eval.txt)")


def compute_features(audio: np.ndarray) -> np.ndarray:
    """uint16 features [n, 40], one row per 10 ms, from a fresh frontend (like Start())."""
    try:
        from pymicro_features import MicroFrontend  # type: ignore
    except ImportError:
        raise SystemExit("error: pip install pymicro-features (see requirements-eval.txt)")
    frontend = MicroFrontend()
    data = ww.to_int16(audio).tobytes()
    rows = []
    position = 0
    chunk = STEP_SAMPLES * 2
    while position + chunk <= len(data):
        result = frontend.process_samples(data[position:position + chunk])
        position += max(1, result.samples_read) * 2
        if result.features:
            rows.append(result.features)
    if not rows:
        return np.zeros((0, FEATURES), np.uint16)
    return np.round(np.asarray(rows, np.float64) / FEATURE_SCALE).astype(np.uint16)


class StreamingModel:
    def __init__(self, path: Path):
        self.path = path
        self.cls = interpreter_class()
        self._load()
        details = self.interpreter.get_input_details()[0]
        out = self.interpreter.get_output_details()[0]
        shape = list(details["shape"])
        if details["dtype"] != np.int8 or len(shape) != 3 or shape[2] != FEATURES:
            raise SystemExit(f"error: model input must be int8 [1, n, 40], got {details['dtype']} {shape}")
        if out["dtype"] != np.uint8:
            raise SystemExit(f"error: model output must be uint8, got {out['dtype']}")
        self.stride = int(shape[1])
        scale, zero_point = details["quantization"]
        self.multiplier = int(round(FEATURE_SCALE / scale * 65536.0))
        self.zero_point = int(zero_point)

    def _load(self):
        self.interpreter = self.cls(model_path=str(self.path))
        self.interpreter.allocate_tensors()
        self.input_index = self.interpreter.get_input_details()[0]["index"]
        self.output_index = self.interpreter.get_output_details()[0]["index"]

    def quantize(self, features: np.ndarray) -> np.ndarray:
        values = ((features.astype(np.int64) * self.multiplier + 32768) >> 16) + self.zero_point
        return np.clip(values, -128, 127).astype(np.int8)

    def probabilities(self, features: np.ndarray) -> np.ndarray:
        """uint8 probability per slice index (-1 where no inference ended on that slice)."""
        self._load()  # fresh streaming state per recording
        quantized = self.quantize(features)
        result = np.full(len(features), -1, np.int16)
        for end in range(self.stride, len(features) + 1, self.stride):
            block = quantized[end - self.stride:end].reshape(1, self.stride, FEATURES)
            self.interpreter.set_tensor(self.input_index, block)
            self.interpreter.invoke()
            result[end - 1] = int(self.interpreter.get_tensor(self.output_index).reshape(-1)[0])
        return result


# --------------------------------------------------------------------------- decision

def detect(probs: np.ndarray, cutoff_pct: int, window: int, warmup: bool = True) -> list[int]:
    """Firmware decision logic; returns the slice indices of the detections."""
    cutoff = cutoff_pct * 255 // 100
    ring = [0] * window
    index = 0
    newest = 0
    cooldown = REFRACTORY_SLICES if warmup else 0
    detections = []
    for slice_index, value in enumerate(probs.tolist()):
        fresh = value >= 0
        if fresh:
            newest = value
            ring[index] = value
            index = (index + 1) % window
        if cooldown > 0 and newest < cutoff:
            cooldown -= 1
        if fresh and cooldown == 0 and sum(ring) > cutoff * window:
            detections.append(slice_index)
            ring = [0] * window
            newest = 0
            cooldown = REFRACTORY_SLICES
    return detections


# --------------------------------------------------------------------------- evaluation

def parse_range(text: str, as_pct: bool) -> list[int]:
    """'95,96,97' or '90:99:1'; values <= 1 are fractions (0.97 -> 97) for cutoffs."""
    values: list[float] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            start, stop, step = (float(x) for x in part.split(":"))
            count = int(round((stop - start) / step)) + 1
            values += [start + i * step for i in range(count)]
        else:
            values.append(float(part))
    out = []
    for value in values:
        if as_pct and value <= 1.0:
            value *= 100.0
        out.append(int(round(value)))
    return sorted(set(out))


def audio_files(paths: list[Path]) -> list[Path]:
    files = []
    for path in paths:
        if path.is_dir():
            files += sorted(p for p in path.rglob("*") if p.suffix.lower() in AUDIO_SUFFIXES)
        elif path.exists():
            files.append(path)
        else:
            raise SystemExit(f"error: {path} not found")
    return files


@dataclass
class Stream:
    path: Path
    seconds: float
    probs: np.ndarray
    peak: int = 0


@dataclass
class Result:
    cutoff_pct: int
    window: int
    recall: float | None = None
    hits: int = 0
    positives: int = 0
    false_accepts: int = 0
    negative_hours: float = 0.0
    misses: list = field(default_factory=list)
    fa_times: list = field(default_factory=list)

    @property
    def fa_per_hour(self) -> float | None:
        return self.false_accepts / self.negative_hours if self.negative_hours > 0 else None


def quiet(rng: np.random.Generator, ms: int) -> np.ndarray:
    """-70 dBFS noise: what the device hears between utterances in a quiet room."""
    return (rng.standard_normal(int(ms * ww.TARGET_RATE / 1000)) * 10 ** (-70 / 20)).astype(np.float32)


def load_streams(model: StreamingModel, files: list[Path], lead_ms: int,
                 trail_ms: int = 0) -> list[Stream]:
    streams = []
    rng = np.random.default_rng(0)
    for path in files:
        audio = ww.read_audio(path)
        seconds = len(audio) / ww.TARGET_RATE
        # The device runs continuously: quiet audio before the clip warms up the frontend and
        # the refractory, and audio keeps coming after it - the trainer places every wake word
        # 0.2-0.3 s before the end of its window, so models fire a little after the last
        # syllable and a clip cut right after it would never be detected.
        if lead_ms > 0:
            audio = np.concatenate([quiet(rng, lead_ms), audio])
        if trail_ms > 0:
            audio = np.concatenate([audio, quiet(rng, trail_ms)])
        probs = model.probabilities(compute_features(audio))
        valid = probs[probs >= 0]
        streams.append(Stream(path, seconds, probs, int(valid.max()) if len(valid) else 0))
    return streams


def evaluate(positives: list[Stream], negatives: list[Stream], cutoffs: list[int],
             windows: list[int], warmup: bool) -> list[Result]:
    results = []
    hours = sum(s.seconds for s in negatives) / 3600.0
    for cutoff in cutoffs:
        for window in windows:
            result = Result(cutoff, window, positives=len(positives), negative_hours=hours)
            for stream in positives:
                if detect(stream.probs, cutoff, window, warmup):
                    result.hits += 1
                else:
                    result.misses.append(stream.path.name)
            if positives:
                result.recall = result.hits / len(positives)
            for stream in negatives:
                found = detect(stream.probs, cutoff, window, warmup)
                result.false_accepts += len(found)
                result.fa_times += [f"{stream.path.name}@{s * STEP_SAMPLES / ww.TARGET_RATE:.1f}s"
                                    for s in found]
            results.append(result)
    return results


def meets_criteria(result: Result, min_recall: float, max_fa_per_hour: float) -> bool:
    recall_ok = result.recall is None or result.recall >= min_recall
    fa = result.fa_per_hour
    return recall_ok and (fa is None or fa <= max_fa_per_hour)


def main(argv: list[str] | None = None) -> int:
    ww.utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", type=Path, required=True, help="streaming int8 .tflite")
    parser.add_argument("--manifest", type=Path, help="trainer .json (default cutoff/window)")
    parser.add_argument("--positives", type=Path, nargs="*", default=[], help="clip folders/files")
    parser.add_argument("--negatives", type=Path, nargs="*", default=[],
                        help="long recordings or folders (podcast, TV, music)")
    parser.add_argument("--cutoffs", default=None, help="e.g. '90:99:1' or '95,97' (pct or 0..1)")
    parser.add_argument("--windows", default=None, help="e.g. '3:8:1' or '5,6'")
    parser.add_argument("--lead-ms", type=int, default=1500,
                        help="quiet lead-in before each positive clip (0 to disable)")
    parser.add_argument("--trail-ms", type=int, default=1000,
                        help="quiet audio after each positive clip (0 to disable)")
    parser.add_argument("--no-warmup", action="store_true",
                        help="disable the 1 s refractory at the start of each file")
    parser.add_argument("--min-recall", type=float, default=0.9, help="FW7: 18/20 quiet")
    parser.add_argument("--max-fa-per-hour", type=float, default=1.0, help="FW7: <= 1 per hour")
    parser.add_argument("--show-misses", action="store_true")
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    if not args.positives and not args.negatives:
        parser.error("give --positives and/or --negatives")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest else {}
    micro = manifest.get("micro") or {}
    default_cutoff = int(round(float(micro.get("probability_cutoff", 0.97)) * 100))
    default_window = int(micro.get("sliding_window_size", 5))
    cutoffs = parse_range(args.cutoffs, True) if args.cutoffs else sorted({default_cutoff, 95, 97, 99})
    windows = parse_range(args.windows, False) if args.windows else sorted({default_window, 3, 5, 7})
    if any(not 1 <= w <= 20 for w in windows) or any(not 0 <= c <= 100 for c in cutoffs):
        parser.error("windows must be 1..20 and cutoffs 0..100")

    model = StreamingModel(args.model)
    print(f"model {args.model.name}: stride {model.stride}, input zero point {model.zero_point}")
    positives = load_streams(model, audio_files(args.positives), args.lead_ms, args.trail_ms)
    negatives = load_streams(model, audio_files(args.negatives), 0)
    neg_hours = sum(s.seconds for s in negatives) / 3600.0
    print(f"{len(positives)} positive clips, {len(negatives)} negative files ({neg_hours:.2f} h)")
    results = evaluate(positives, negatives, cutoffs, windows, warmup=not args.no_warmup)

    header = f"{'cutoff':>6} {'win':>3} {'recall':>14} {'FA':>5} {'FA/h':>7}  ok"
    print(header)
    print("-" * len(header))
    for result in results:
        recall = (f"{result.hits}/{result.positives} {result.recall:5.1%}" if result.recall is not None
                  else "-")
        fa_h = f"{result.fa_per_hour:7.2f}" if result.fa_per_hour is not None else "      -"
        ok = "*" if meets_criteria(result, args.min_recall, args.max_fa_per_hour) else ""
        print(f"{result.cutoff_pct:>5}% {result.window:>3} {recall:>14} {result.false_accepts:>5} {fa_h}  {ok}")
    print("* = meets --min-recall and --max-fa-per-hour; set the chosen pair as "
          "CONFIG_JARVIS_MWW_CUTOFF_PCT / CONFIG_JARVIS_MWW_SLIDING_WINDOW")
    if args.show_misses:
        for result in results:
            if result.misses or result.fa_times:
                print(f"\n{result.cutoff_pct}% / {result.window}: misses {result.misses[:20]} "
                      f"FA {result.fa_times[:20]}")
    peaks = sorted((s.peak for s in positives))
    if peaks:
        print(f"positive peak probability: min {peaks[0]}, median {peaks[len(peaks) // 2]}, max {peaks[-1]} (/255)")

    rows = [{"cutoff_pct": r.cutoff_pct, "window": r.window, "recall": r.recall, "hits": r.hits,
             "positives": r.positives, "false_accepts": r.false_accepts,
             "negative_hours": round(r.negative_hours, 4), "fa_per_hour": r.fa_per_hour,
             "meets_criteria": meets_criteria(r, args.min_recall, args.max_fa_per_hour)}
            for r in results]
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        details = [dict(row, misses=r.misses, fa_times=r.fa_times) for row, r in zip(rows, results)]
        args.json.write_text(json.dumps({"model": args.model.name, "results": details},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
