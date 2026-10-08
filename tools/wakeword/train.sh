#!/usr/bin/env bash
# Train a microWakeWord model ("quản gia" by default, --wake-word for another phrase) on the
# GPU server (RTX 5090 / Blackwell), or on its CPU with --gpu none --cpus N.
#
# Runs the TaterTotterson microWakeWord trainer headless (no web UI, no published ports):
#   1. replaces the clips in the trainer data dir with ours:
#        positives (VieNeu/Piper clips + personal recordings) -> <data>/personal_samples/
#        hard negatives (gen_negatives.py)                   -> <data>/negative_samples/
#        --own-tts clips (instead of the trainer's own TTS)  -> <data>/own_tts/
#   2. inside the container: set up the training venv + datasets (first run downloads many GB),
#      fetch the Piper vi_VN voice, run train_wake_word --language=vi
#   3. copies <slug>.tflite + <slug>.json to --export-dir and prints the firmware export command.
#
# Run it ON the GPU host (needs Docker + NVIDIA). On batai GPUs are handed to containers via
# CDI (--gpu cdi, default); on Docker Desktop / legacy runtimes use --gpu all.
#
# License note: the Blackwell image trains with an unofficial TensorFlow 2.22 wheel
# (chivitiH/tensorflow-blackwell-python313) licensed CC BY-NC 4.0 -> non-commercial use only.
# Several negative datasets the trainer downloads are also for non-commercial use.
#
# Usage:
#   tools/wakeword/train.sh --data-dir /mnt/data1/mww \
#       --positives out/positives,out/positives_aug,out/personal --negatives out/negatives
#   tools/wakeword/train.sh --wake-word "hàu ơi" --title "Hàu ơi" --slug hau_oi \
#       --gpu none --cpus 12 --own-tts out/hau_oi/tts --image ghcr.io/tatertotterson/microwakeword:v32@sha256:... ...
#   tools/wakeword/train.sh --dry-run ...        # print the plan, change nothing
set -euo pipefail

IMAGE_DEFAULT="ghcr.io/tatertotterson/microwakeword:v32-blackwell@sha256:0917b746a2ff198569ff95730ebffaa40e4d109fdbd0afff5b000650786b636b"
IMAGE="${MWW_IMAGE:-$IMAGE_DEFAULT}"
DATA_DIR="${MWW_DATA_DIR:-/mnt/data1/mww}"
POSITIVES=""
NEGATIVES=""
WAKE_WORD="quản gia"
TITLE="Quản gia"
SLUG="quan_gia"
SAMPLES=30000
STEPS=40000
TTS_MODE="hybrid"
GPU="cdi"
CPUS=""
EXPORT_DIR="out/model"
FRESH=1
PULL=1
SETUP_ONLY=0
OWN_TTS=""
DRY_RUN=0
CONTAINER="jarvis-mww-train"
# Only vais1000 keeps the tones: vi_VN-vivos-x_low and vi_VN-25hours_single-low (piper 0.2.0)
# have no tone symbols in their phoneme maps, so "hàu", "hầu" and "hậu" all come out alike.
PIPER_VOICES=(
  "vi/vi_VN/vais1000/medium/vi_VN-vais1000-medium"
)

usage() {
  sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'
  cat <<EOF

Options:
  --data-dir DIR       trainer data dir (persistent, many GB)       [${DATA_DIR}]
  --positives LIST     comma separated folders of positive WAVs
  --negatives LIST     comma separated folders of hard-negative WAVs
  --samples N          trainer TTS samples to generate              [${SAMPLES}]
  --steps N            training steps                               [${STEPS}]
  --tts-mode MODE      modern | hybrid | piper                      [${TTS_MODE}]
  --own-tts LIST       comma separated folders of positive WAVs used as the trainer's TTS
                       corpus instead of synthesizing one (e.g. VieNeu clips); --samples is
                       then the number of clips
  --wake-word TEXT     phrase to train                               [${WAKE_WORD}]
  --title TEXT         display name in the manifest                  [${TITLE}]
  --slug NAME          file name of the model                        [${SLUG}]
  --gpu MODE           cdi | all | none                             [${GPU}]
  --cpus N             limit the container to N CPUs (also the TF/OpenMP thread pools)
  --image REF          trainer image (non-Blackwell GPU: ...:v32)    [${IMAGE}]
  --export-dir DIR     where to copy the trained model              [${EXPORT_DIR}]
  --keep-features      reuse cached augmented features (default: rebuild)
  --no-pull            do not docker pull the image
  --setup-only         only prepare the venv, datasets and voices (first run: many GB)
  --dry-run            print what would happen
EOF
}

die() { echo "error: $*" >&2; exit 1; }
run() {
  if [[ ${DRY_RUN} -eq 1 ]]; then
    printf '[dry-run]'; printf ' %q' "$@"; printf '\n'
  else
    "$@"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --data-dir) DATA_DIR="$2"; shift 2 ;;
    --positives) POSITIVES="$2"; shift 2 ;;
    --negatives) NEGATIVES="$2"; shift 2 ;;
    --samples) SAMPLES="$2"; shift 2 ;;
    --steps) STEPS="$2"; shift 2 ;;
    --tts-mode) TTS_MODE="$2"; shift 2 ;;
    --wake-word) WAKE_WORD="$2"; shift 2 ;;
    --title) TITLE="$2"; shift 2 ;;
    --slug) SLUG="$2"; shift 2 ;;
    --gpu) GPU="$2"; shift 2 ;;
    --cpus) CPUS="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --export-dir) EXPORT_DIR="$2"; shift 2 ;;
    --keep-features) FRESH=0; shift ;;
    --no-pull) PULL=0; shift ;;
    --setup-only) SETUP_ONLY=1; shift ;;
    --own-tts) OWN_TTS="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done

[[ "${SAMPLES}" =~ ^[0-9]+$ && "${STEPS}" =~ ^[0-9]+$ ]] || die "--samples/--steps must be integers"
[[ "${SLUG}" =~ ^[a-z0-9_]+$ ]] || die "--slug must match [a-z0-9_]+"
[[ -n "${WAKE_WORD}" && -n "${TITLE}" ]] || die "--wake-word and --title must not be empty"
CPU_ARGS=()
if [[ -n "${CPUS}" ]]; then
  [[ "${CPUS}" =~ ^[0-9]+$ && "${CPUS}" -ge 1 ]] || die "--cpus must be a positive integer"
  # A quota alone leaves TensorFlow/OpenMP sizing their pools for every host CPU.
  CPU_ARGS=(--cpus "${CPUS}" -e OMP_NUM_THREADS="${CPUS}" -e TF_NUM_INTRAOP_THREADS="${CPUS}"
            -e TF_NUM_INTEROP_THREADS=2)
fi
case "${TTS_MODE}" in modern|hybrid|piper) ;; *) die "--tts-mode must be modern, hybrid or piper" ;; esac
case "${GPU}" in
  cdi) GPU_ARGS=(--device nvidia.com/gpu=all) ;;
  all) GPU_ARGS=(--gpus all) ;;
  none) GPU_ARGS=() ;;
  *) die "--gpu must be cdi, all or none" ;;
esac
[[ -n "${POSITIVES}${OWN_TTS}" ]] || echo "warning: no --positives; the trainer will use only its own TTS samples" >&2
command -v docker >/dev/null 2>&1 || [[ ${DRY_RUN} -eq 1 ]] || die "docker not found"

# --------------------------------------------------------------------------- 1. stage clips
stage_dir() {  # stage_dir <list> <target dir> <prefix>; the target ends up with exactly these clips
  local list="$1" target="$2" prefix="$3" count=0 dir file base
  run rm -rf "${target}"
  run mkdir -p "${target}"
  IFS=',' read -r -a dirs <<< "${list}"
  for dir in "${dirs[@]}"; do
    [[ -z "${dir}" ]] && continue
    [[ -d "${dir}" ]] || die "not a folder: ${dir}"
    base="$(basename "${dir}")"
    while IFS= read -r -d '' file; do
      run cp -f "${file}" "${target}/${prefix}_${base}_$(basename "${file}")"
      count=$((count + 1))
    done < <(find "${dir}" -maxdepth 1 -type f -iname '*.wav' -print0)
  done
  echo "staged ${count} WAV files into ${target}"
}

echo "== trainer data dir: ${DATA_DIR}"
run mkdir -p "${DATA_DIR}"
# Clips staged by an earlier run (maybe for another wake word) must not leak into this one.
stage_dir "${POSITIVES}" "${DATA_DIR}/personal_samples" "jarvis"
stage_dir "${NEGATIVES}" "${DATA_DIR}/negative_samples" "jarvis"
stage_dir "${OWN_TTS}" "${DATA_DIR}/own_tts" "tts"

if [[ ${FRESH} -eq 1 ]]; then
  # The trainer only re-featurizes personal/negative samples when its own TTS set changes.
  # The caches are created by the container as root, so a non-root docker user cannot
  # delete them here: the container removes them itself (see INNER).
  echo "== cached features will be removed inside the container so the staged clips are used"
fi

# --------------------------------------------------------------------------- 2. train
voice_urls=""
for voice in "${PIPER_VOICES[@]}"; do
  voice_urls+=" ${voice}"
done
INNER=$(cat <<'EOS'
set -euo pipefail
# The container runs as root: hand the trained models back to the calling user.
trap 'chown -R "${HOST_UID}:${HOST_GID}" /data/output 2>/dev/null || true' EXIT
cd /data
if [ "${FRESH}" = 1 ]; then
  rm -rf /data/work/wake_word_samples_augmented /data/work/personal_augmented_features \
         /data/work/reviewed_negative_features
fi
/root/mww-scripts/cli/setup_python_venv --data-dir=/data
/root/mww-scripts/cli/setup_training_datasets --data-dir=/data
# The trainer screens its TTS clips with Silero VAD from the web UI's recorder venv, which a
# headless run never creates: install the VAD into the training venv and point it there.
if [ -z "${REC_VENV_DIR:-}" ] && [ ! -x /data/.recorder-venv/bin/python ]; then
  /data/.venv/bin/python -c "import silero_vad" 2>/dev/null \
    || /data/.venv/bin/pip install -q "silero-vad>=5.1,<7"
  export REC_VENV_DIR=/data/.venv
fi
if [ "${TTS_MODE}" != "modern" ]; then
  voices_dir=/data/tools/piper-sample-generator/voices
  mkdir -p "${voices_dir}"
  for voice in ${PIPER_VOICES}; do
    for ext in onnx onnx.json; do
      file="${voices_dir}/$(basename "${voice}").${ext}"
      [ -s "${file}" ] || curl -fsSL -o "${file}" \
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/${voice}.${ext}?download=true"
    done
  done
fi
if [ "${SETUP_ONLY}" = 1 ]; then
  echo "== setup done (venv, datasets, Piper voices); train later without --setup-only"
  exit 0
fi
if [ "${OWN_TTS}" = 1 ]; then
  # Our clips become the trainer's TTS corpus: a manifest carrying the signature the trainer
  # computes for this very run (its --dry-run) makes it reuse the folder instead of synthesizing.
  out=/data/work/wake_word_samples
  rm -rf "${out}" /data/work/.wake_word_samples.build
  mkdir -p "${out}"
  SAMPLES=0
  for f in /data/own_tts/*.wav; do
    [ -e "${f}" ] || continue
    cp "${f}" "${out}/${SAMPLES}.wav"
    SAMPLES=$((SAMPLES + 1))
  done
  [ "${SAMPLES}" -gt 0 ] || { echo "error: no --own-tts clips in /data/own_tts" >&2; exit 1; }
  # The signature lists the TTS engines of the mode, so the mode needs one (piper: the voice
  # fetched above), although none of them runs.
  gen_args=("${WAKE_WORD}" --samples="${SAMPLES}" --language=vi --tts-mode="${TTS_MODE}"
            --data-dir=/data --output-dir="${out}")
  /data/.venv/bin/python /root/mww-scripts/cli/tts_generate_samples.py "${gen_args[@]}" \
    --dry-run > /tmp/own_tts_plan.json
  /data/.venv/bin/python -c 'import json, sys
plan = json.load(open(sys.argv[1], encoding="utf-8"))
manifest = {"signature": plan["signature"], "source": "jarvis-helmet train.sh --own-tts"}
open(sys.argv[2], "w", encoding="utf-8").write(json.dumps(manifest, ensure_ascii=False, indent=2))' \
    /tmp/own_tts_plan.json "${out}/.generation_manifest.json"
  # Same normalization as the generator main(): fail now rather than let it synthesize anyway.
  /data/.venv/bin/python -c 'import sys
sys.path.insert(0, "/root/mww-scripts/cli")
import tts_generate_samples as t
args = t.parser().parse_args(sys.argv[1:])
args.language = args.language.strip().lower().replace("-", "_")
args.english_accent = t.normalize_english_accent(args.english_accent, args.language)
args.tts_mode = t.normalize_tts_mode(args.tts_mode)
sys.exit(0 if t.Generator(args).cache_hit() else "error: the trainer would not reuse the --own-tts clips")' \
    "${gen_args[@]}"
  echo "== ${SAMPLES} --own-tts clips replace the trainer's TTS corpus"
fi
source /data/.venv/bin/activate
train_wake_word --data-dir=/data --language=vi --tts-mode="${TTS_MODE}" \
  --samples="${SAMPLES}" --training-steps="${STEPS}" --artifact-slug="${SLUG}" \
  "${WAKE_WORD}" "${TITLE}"
EOS
)

echo "== training '${WAKE_WORD}' with ${IMAGE}"
[[ ${PULL} -eq 1 ]] && run docker pull "${IMAGE}"
run docker run --rm --name "${CONTAINER}" "${GPU_ARGS[@]}" "${CPU_ARGS[@]}" \
  -e TF_FORCE_GPU_ALLOW_GROWTH=true -e FRESH="${FRESH}" -e SETUP_ONLY="${SETUP_ONLY}" \
  -e OWN_TTS="$([[ -n "${OWN_TTS}" ]] && echo 1 || echo 0)" \
  -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  -e TTS_MODE="${TTS_MODE}" -e SAMPLES="${SAMPLES}" -e STEPS="${STEPS}" -e SLUG="${SLUG}" \
  -e WAKE_WORD="${WAKE_WORD}" -e TITLE="${TITLE}" -e PIPER_VOICES="${voice_urls# }" \
  -v "${DATA_DIR}:/data" --entrypoint bash "${IMAGE}" -c "${INNER}"

# --------------------------------------------------------------------------- 3. collect
[[ ${SETUP_ONLY} -eq 1 ]] && exit 0
if [[ ${DRY_RUN} -eq 1 ]]; then
  echo "[dry-run] would copy the newest ${DATA_DIR}/output/*-${SLUG}-*/${SLUG}.{tflite,json} to ${EXPORT_DIR}"
  exit 0
fi
latest="$(find "${DATA_DIR}/output" -maxdepth 2 -name "${SLUG}.tflite" -printf '%T@ %p\n' 2>/dev/null \
          | sort -n | tail -1 | cut -d' ' -f2-)"
[[ -n "${latest}" ]] || die "no ${SLUG}.tflite under ${DATA_DIR}/output (see the training log)"
mkdir -p "${EXPORT_DIR}"
cp -f "${latest}" "${EXPORT_DIR}/"
[[ -f "${latest%.tflite}.json" ]] && cp -f "${latest%.tflite}.json" "${EXPORT_DIR}/"
echo "== model: ${EXPORT_DIR}/${SLUG}.tflite"
echo "next:"
echo "  python tools/wakeword/eval_wakeword.py --model ${EXPORT_DIR}/${SLUG}.tflite --manifest ${EXPORT_DIR}/${SLUG}.json --positives <recordings> --negatives <podcast>"
echo "  python tools/wakeword/export/tflite_to_c.py --model ${EXPORT_DIR}/${SLUG}.tflite --manifest ${EXPORT_DIR}/${SLUG}.json"
