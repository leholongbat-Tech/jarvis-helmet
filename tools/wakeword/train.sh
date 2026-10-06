#!/usr/bin/env bash
# Train the "quản gia" microWakeWord model on the GPU server (RTX 5090 / Blackwell).
#
# Runs the TaterTotterson microWakeWord trainer headless (no web UI, no published ports):
#   1. copies our clips into the trainer data dir:
#        positives (VieNeu/Piper clips + personal recordings) -> <data>/personal_samples/
#        hard negatives (gen_negatives.py)                   -> <data>/negative_samples/
#   2. inside the container: set up the training venv + datasets (first run downloads many GB),
#      fetch the Piper vi_VN voices, run train_wake_word --language=vi
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
EXPORT_DIR="out/model"
FRESH=1
PULL=1
DRY_RUN=0
CONTAINER="jarvis-mww-train"
PIPER_VOICES=(
  "vi/vi_VN/vivos/x_low/vi_VN-vivos-x_low"
  "vi/vi_VN/vais1000/medium/vi_VN-vais1000-medium"
  "vi/vi_VN/25hours_single/low/vi_VN-25hours_single-low"
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
  --gpu MODE           cdi | all | none                             [${GPU}]
  --image REF          trainer image (non-Blackwell GPU: ...:v32)    [${IMAGE}]
  --export-dir DIR     where to copy the trained model              [${EXPORT_DIR}]
  --keep-features      reuse cached augmented features (default: rebuild)
  --no-pull            do not docker pull the image
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
    --gpu) GPU="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --export-dir) EXPORT_DIR="$2"; shift 2 ;;
    --keep-features) FRESH=0; shift ;;
    --no-pull) PULL=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done

[[ "${SAMPLES}" =~ ^[0-9]+$ && "${STEPS}" =~ ^[0-9]+$ ]] || die "--samples/--steps must be integers"
case "${TTS_MODE}" in modern|hybrid|piper) ;; *) die "--tts-mode must be modern, hybrid or piper" ;; esac
case "${GPU}" in
  cdi) GPU_ARGS=(--device nvidia.com/gpu=all) ;;
  all) GPU_ARGS=(--gpus all) ;;
  none) GPU_ARGS=() ;;
  *) die "--gpu must be cdi, all or none" ;;
esac
[[ -n "${POSITIVES}" ]] || echo "warning: no --positives; the trainer will use only its own TTS samples" >&2
command -v docker >/dev/null 2>&1 || [[ ${DRY_RUN} -eq 1 ]] || die "docker not found"

# --------------------------------------------------------------------------- 1. stage clips
stage_dir() {  # stage_dir <list> <target dir> <prefix>
  local list="$1" target="$2" prefix="$3" count=0 dir file base
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
[[ -n "${POSITIVES}" ]] && stage_dir "${POSITIVES}" "${DATA_DIR}/personal_samples" "jarvis"
[[ -n "${NEGATIVES}" ]] && stage_dir "${NEGATIVES}" "${DATA_DIR}/negative_samples" "jarvis"

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
source /data/.venv/bin/activate
train_wake_word --data-dir=/data --language=vi --tts-mode="${TTS_MODE}" \
  --samples="${SAMPLES}" --training-steps="${STEPS}" --artifact-slug="${SLUG}" \
  "${WAKE_WORD}" "${TITLE}"
EOS
)

echo "== training '${WAKE_WORD}' with ${IMAGE}"
[[ ${PULL} -eq 1 ]] && run docker pull "${IMAGE}"
run docker run --rm --name "${CONTAINER}" "${GPU_ARGS[@]}" \
  -e TF_FORCE_GPU_ALLOW_GROWTH=true -e FRESH="${FRESH}" \
  -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  -e TTS_MODE="${TTS_MODE}" -e SAMPLES="${SAMPLES}" -e STEPS="${STEPS}" -e SLUG="${SLUG}" \
  -e WAKE_WORD="${WAKE_WORD}" -e TITLE="${TITLE}" -e PIPER_VOICES="${voice_urls# }" \
  -v "${DATA_DIR}:/data" --entrypoint bash "${IMAGE}" -c "${INNER}"

# --------------------------------------------------------------------------- 3. collect
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
