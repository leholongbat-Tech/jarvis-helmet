#!/usr/bin/env python3
"""Turn a trained microWakeWord model into firmware/main/boards/jarvis-helmet/mww_model_data.c.

    python tools/wakeword/export/tflite_to_c.py --model quan_gia.tflite --manifest quan_gia.json
    python tools/wakeword/export/tflite_to_c.py --placeholder          # size-0 placeholder

The generated file defines (only with CONFIG_JARVIS_MICRO_WAKE_WORD):
    const unsigned int  g_jarvis_mww_model_size;          // 0 = placeholder, detector disabled
    const unsigned int  g_jarvis_mww_tensor_arena_size;   // manifest hint, firmware probes upward
    const unsigned char g_jarvis_mww_model[];             // 16-byte aligned flatbuffer

The manifest (written by the trainer next to the .tflite) supplies the suggested
probability cutoff / sliding window; they go into comments together with the matching
Kconfig lines (CONFIG_JARVIS_MWW_CUTOFF_PCT / CONFIG_JARVIS_MWW_SLIDING_WINDOW).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

BOARD_SUBDIR = Path("firmware") / "main" / "boards" / "jarvis-helmet"
GENERATOR = "tools/wakeword/export/tflite_to_c.py"
FIRMWARE_STEP_MS = 10  # micro_wake_word.cc computes features every 10 ms
BYTES_PER_LINE = 12


class ExportError(Exception):
    pass


def default_output() -> Path | None:
    """firmware/main/boards/jarvis-helmet/mww_model_data.c of the enclosing repo, if any."""
    for parent in Path(__file__).resolve().parents:
        if (parent / BOARD_SUBDIR).is_dir():
            return parent / BOARD_SUBDIR / "mww_model_data.c"
    return None


def load_model(path: Path) -> bytes:
    data = path.read_bytes()
    if len(data) < 16:
        raise ExportError(f"{path}: too small for a TFLite model ({len(data)} bytes)")
    if data[4:8] != b"TFL3":
        raise ExportError(f"{path}: missing the TFLite flatbuffer identifier 'TFL3'")
    return data


def load_manifest(path: Path | None) -> dict:
    if path is None:
        return {}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ExportError(f"{path}: manifest must be a JSON object")
    return manifest


def manifest_settings(manifest: dict, force: bool) -> dict:
    """Extract and check the values the firmware cares about."""
    micro = manifest.get("micro") or {}
    settings = {
        "wake_word": manifest.get("wake_word"),
        "cutoff": micro.get("probability_cutoff"),
        "window": micro.get("sliding_window_size"),
        "step_ms": micro.get("feature_step_size"),
        "arena": micro.get("tensor_arena_size"),
    }
    step = settings["step_ms"]
    if step is not None and int(step) != FIRMWARE_STEP_MS and not force:
        raise ExportError(
            f"manifest feature_step_size={step} ms but the firmware frontend uses "
            f"{FIRMWARE_STEP_MS} ms (use --force to export anyway)")
    if settings["cutoff"] is not None and not 0.0 < float(settings["cutoff"]) <= 1.0:
        raise ExportError(f"probability_cutoff out of range: {settings['cutoff']}")
    if settings["window"] is not None and not 1 <= int(settings["window"]) <= 20:
        message = f"sliding_window_size={settings['window']} is outside the Kconfig range 1..20"
        if not force:
            raise ExportError(message + " (use --force to export anyway)")
        print(f"warning: {message}", file=sys.stderr)
    return settings


def inspect_model(path: Path) -> str | None:
    """Describe input/output tensors when a TFLite runtime is installed (optional)."""
    interpreter_cls = None
    for module, attr in (("ai_edge_litert.interpreter", "Interpreter"),
                         ("tflite_runtime.interpreter", "Interpreter"),
                         ("tensorflow.lite", "Interpreter")):
        try:
            mod = __import__(module, fromlist=[attr])
            interpreter_cls = getattr(mod, attr)
            break
        except Exception:  # noqa: BLE001 - any import problem just skips the check
            continue
    if interpreter_cls is None:
        return None
    interpreter = interpreter_cls(model_path=str(path))
    interpreter.allocate_tensors()
    inp = interpreter.get_input_details()[0]
    out = interpreter.get_output_details()[0]
    problems = []
    shape = [int(x) for x in inp["shape"]]
    if inp["dtype"].__name__ != "int8" or len(shape) != 3 or shape[0] != 1 or shape[2] != 40:
        problems.append(f"input must be int8 [1, n, 40], got {inp['dtype'].__name__} {shape}")
    out_shape = [int(x) for x in out["shape"]]
    if out["dtype"].__name__ != "uint8" or out_shape != [1, 1]:
        problems.append(f"output must be uint8 [1, 1], got {out['dtype'].__name__} {out_shape}")
    if problems:
        raise ExportError("; ".join(problems))
    scale, zero_point = inp["quantization"]
    return (f"input int8 {shape} (scale {scale:.7g}, zero point {int(zero_point)}), "
            f"output uint8 {out_shape}, stride {shape[1]}")


def kconfig_pct(cutoff: float) -> int:
    # Firmware threshold is pct * 255 / 100 (integer division); round to the nearest pct.
    return max(50, min(100, int(math.floor(cutoff * 100 + 0.5))))


def render(model: bytes | None, settings: dict, source: str, tensors: str | None) -> str:
    lines = [
        "// microWakeWord model for the Jarvis Helmet (plan B2, CONFIG_JARVIS_MICRO_WAKE_WORD).",
        f"// Generated by {GENERATOR}; do not edit by hand.",
        "//",
    ]
    if model is None:
        lines += [
            "// PLACEHOLDER: no model is committed. Train one (tools/wakeword/README.md), then:",
            f"//   python {GENERATOR} --model <name>.tflite --manifest <name>.json",
            "// With g_jarvis_mww_model_size == 0 the detector logs a warning and disables itself.",
        ]
    else:
        lines += [
            f"// Source:     {source}",
            f"// SHA-256:    {hashlib.sha256(model).hexdigest()}",
            f"// Size:       {len(model)} bytes",
        ]
        if settings.get("wake_word"):
            lines.append(f"// Wake word:  {settings['wake_word']}")
        if tensors:
            lines.append(f"// Tensors:    {tensors}")
        if settings.get("step_ms") is not None:
            lines.append(f"// Features:   40 x 30 ms windows, step {settings['step_ms']} ms")
        if settings.get("cutoff") is not None:
            cutoff = float(settings["cutoff"])
            pct = kconfig_pct(cutoff)
            lines.append(f"// Manifest probability_cutoff {cutoff:g} "
                         f"(= {int(cutoff * 255)}/255) -> CONFIG_JARVIS_MWW_CUTOFF_PCT={pct} "
                         f"(= {pct * 255 // 100}/255)")
        if settings.get("window") is not None:
            window = int(settings["window"])
            lines.append(f"// Manifest sliding_window_size {window} -> "
                         f"CONFIG_JARVIS_MWW_SLIDING_WINDOW={window}")
        lines.append("// Tune both against recordings with tools/wakeword/eval_wakeword.py.")
    arena = int(settings.get("arena") or 0) if model is not None else 0
    size = len(model) if model is not None else 0
    lines += [
        "",
        "#include <sdkconfig.h>",
        "",
        "// Keeps the translation unit non-empty when the option is off.",
        "typedef int jarvis_mww_model_data_unused_t;",
        "",
        "#ifdef CONFIG_JARVIS_MICRO_WAKE_WORD",
        "",
        f"const unsigned int g_jarvis_mww_model_size = {size};",
        f"const unsigned int g_jarvis_mww_tensor_arena_size = {arena};",
    ]
    if model is None:
        lines.append("const unsigned char g_jarvis_mww_model[16] __attribute__((aligned(16))) = {0};")
    else:
        lines.append("const unsigned char g_jarvis_mww_model[] __attribute__((aligned(16))) = {")
        for offset in range(0, len(model), BYTES_PER_LINE):
            chunk = model[offset:offset + BYTES_PER_LINE]
            lines.append("    " + ", ".join(f"0x{byte:02x}" for byte in chunk) + ",")
        lines.append("};")
    lines += ["", "#endif  // CONFIG_JARVIS_MICRO_WAKE_WORD", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--model", type=Path, help="trained model (.tflite, int8 streaming)")
    source.add_argument("--placeholder", action="store_true",
                        help="write the size-0 placeholder (no model)")
    parser.add_argument("--manifest", type=Path,
                        help="trainer manifest (.json next to the model); default: <model>.json")
    parser.add_argument("--output", type=Path, default=None,
                        help=f"C file to write (default: {BOARD_SUBDIR.as_posix()}/mww_model_data.c "
                             "in this repo)")
    parser.add_argument("--force", action="store_true",
                        help="export even if the manifest does not match the firmware frontend")
    parser.add_argument("--no-inspect", action="store_true",
                        help="skip the optional tensor check with a TFLite runtime")
    args = parser.parse_args(argv)
    if args.output is None:
        args.output = default_output()
        if args.output is None:
            parser.error("cannot find firmware/main/boards/jarvis-helmet; pass --output")

    try:
        if args.placeholder:
            text = render(None, {}, "", None)
        else:
            model = load_model(args.model)
            manifest_path = args.manifest
            if manifest_path is None and args.model.with_suffix(".json").exists():
                manifest_path = args.model.with_suffix(".json")
            settings = manifest_settings(load_manifest(manifest_path), args.force)
            tensors = None if args.no_inspect else inspect_model(args.model)
            if tensors is None and not args.no_inspect:
                print("note: no TFLite runtime installed, tensor shapes not checked", file=sys.stderr)
            source_desc = args.model.name + (f" + {manifest_path.name}" if manifest_path else "")
            text = render(model, settings, source_desc, tensors)
    except (OSError, ValueError, ExportError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {args.output}")
    if not args.placeholder and settings.get("cutoff") is not None:
        print("suggested Kconfig (config.json sdkconfig_append or menuconfig):")
        print(f"  CONFIG_JARVIS_MWW_CUTOFF_PCT={kconfig_pct(float(settings['cutoff']))}")
        if settings.get("window") is not None:
            print(f"  CONFIG_JARVIS_MWW_SLIDING_WINDOW={int(settings['window'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
