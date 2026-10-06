"""Enroll a reference clip as a VieNeu v3 Turbo voice and write a voices file to mount.

POST /v1/voices only enrolls a voice in process memory (lost on restart). This script uses the
SDK instead (add_voice + save_voices) and writes the complete voices file (built-in presets,
voices already installed from $VIENEU_VOICES_FILE, plus the new one). Mount that file's
directory at /voices and the container entrypoint installs it at start.

Only clone a voice you have the right to use (your own voice or a consenting speaker).

Example (stop the vieneu service first if the GPU is small):
  docker run --rm --gpus all \
    -v <hf_cache_volume>:/home/app/.cache/huggingface -v "$PWD/voices:/voices" \
    jarvis/vieneu:dev python /opt/jarvis/clone_voice.py \
      --name "Jarvis Bat" --ref /voices/ref_10s.wav --gender male --sample "Vâng, thưa ngài."
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="voice name used as `voice` in requests (1-64 chars)")
    ap.add_argument("--ref", required=True, help="reference clip (wav/mp3/flac/ogg/m4a), 3-30 s, clean speech")
    ap.add_argument("--out", default="/voices/voices_v3_turbo.json", help="voices file to write")
    ap.add_argument("--description", default="", help="free text shown by GET /v1/voices")
    ap.add_argument("--gender", default="", help="metadata only, e.g. male")
    ap.add_argument("--no-denoise", action="store_true", help="the clip is already clean")
    ap.add_argument("--default", action="store_true", help="make this the server's default voice")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    ap.add_argument("--sample", default="", help="also synthesize this text to <out dir>/<name>.sample.wav")
    args = ap.parse_args()

    ref = Path(args.ref)
    if not ref.is_file():
        print(f"reference clip not found: {ref}", file=sys.stderr)
        return 2

    from vieneu import Vieneu  # heavy import: after argument checks

    tts = Vieneu(mode="v3turbo", device=args.device)
    if args.name in tts._preset_voices:
        print(f"note: replacing existing voice '{args.name}'")
    tts.add_voice(args.name, str(ref), denoise=not args.no_denoise,
                  description=args.description, gender=args.gender)
    if args.default:
        tts._default_voice = args.name  # no public setter in the SDK
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tts.save_voices(out)
    print(f"saved {len(tts._preset_voices)} voices -> {out}")

    if args.sample:
        wav = tts.infer(args.sample, voice=args.name, apply_watermark=False)
        sample_path = out.parent / f"{args.name}.sample.wav"
        tts.save(wav, sample_path)
        print(f"sample -> {sample_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
