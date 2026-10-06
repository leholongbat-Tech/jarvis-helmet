#!/usr/bin/env bash
# Build the firmware inside the official ESP-IDF Docker image.
#
# Sources are copied into a named Docker volume before building: bind-mounting a
# Windows path makes the build several times slower, and keeping build/ and
# managed_components/ in the volume makes the next build incremental.
#
# Usage:
#   tools/fw_build_docker.sh [build.py args...]
#   tools/fw_build_docker.sh jarvis-helmet --name jarvis-helmet --language vi-VN --wake-word wn9_jarvis_tts
#   FW_CMD="idf.py size" tools/fw_build_docker.sh          # run any command in the synced tree
#   FW_CMD="python -m pip install -q pytest && python -m pytest scripts/tests -q" tools/fw_build_docker.sh
#                                                           # upstream host tests (image has no pytest)
#   FW_CLEAN=1 tools/fw_build_docker.sh ...                 # wipe build/ first
#   FW_OUT=out/fw tools/fw_build_docker.sh ...              # copy build artifacts back to the host
#
# Env:
#   IDF_IMAGE   default espressif/idf:v6.1
#   FW_VOLUME   default jarvis-fw-work (one volume per parallel build, e.g. jarvis-fw-work-7pin)
#   FW_SRC      firmware tree to build, default <repo>/firmware (e.g. a scratch copy)
set -euo pipefail

export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FW_SRC="${FW_SRC:-${REPO_ROOT}/firmware}"
IDF_IMAGE="${IDF_IMAGE:-espressif/idf:v6.1}"
FW_VOLUME="${FW_VOLUME:-jarvis-fw-work}"

# Docker Desktop on Windows needs a Windows-style path for bind mounts.
if command -v cygpath >/dev/null 2>&1; then
  FW_SRC_MOUNT="$(cygpath -m "${FW_SRC}")"
else
  FW_SRC_MOUNT="${FW_SRC}"
fi

if [[ $# -gt 0 ]]; then
  BUILD_CMD="python scripts/build.py $(printf '%q ' "$@")"
else
  BUILD_CMD="python scripts/build.py jarvis-helmet --name jarvis-helmet --language vi-VN --wake-word wn9_jarvis_tts"
fi
BUILD_CMD="${FW_CMD:-$BUILD_CMD}"

docker volume create "${FW_VOLUME}" >/dev/null

OUT_MOUNT=()
if [[ -n "${FW_OUT:-}" ]]; then
  mkdir -p "${REPO_ROOT}/${FW_OUT}"
  OUT_DIR="${REPO_ROOT}/${FW_OUT}"
  command -v cygpath >/dev/null 2>&1 && OUT_DIR="$(cygpath -m "${OUT_DIR}")"
  OUT_MOUNT=(-v "${OUT_DIR}:/out")
fi

docker run --rm \
  -v "${FW_VOLUME}:/work" \
  -v "${FW_SRC_MOUNT}:/src:ro" \
  "${OUT_MOUNT[@]}" \
  -e FW_CLEAN="${FW_CLEAN:-0}" \
  -e IDF_GIT_SAFE_DIR=/work/fw \
  "${IDF_IMAGE}" \
  bash -c '
    set -e
    mkdir -p /work/fw
    # Mirror /src into /work/fw, keeping build products and downloaded components.
    # Only files whose content changed are copied, and they get a fresh mtime, so ninja
    # rebuilds exactly what changed (copying with the original mtimes can hide edits).
    rm -rf /work/stage && mkdir -p /work/stage
    tar -C /src --exclude=./build --exclude=./managed_components --exclude=./sdkconfig \
        --exclude=./sdkconfig.old --exclude=./dependencies.lock -cf - . | tar -C /work/stage -xf -
    cd /work/stage
    find . -type f | while IFS= read -r f; do
      if ! cmp -s "$f" "/work/fw/$f"; then
        mkdir -p "/work/fw/$(dirname "$f")"
        cp "$f" "/work/fw/$f"
        touch "/work/fw/$f"
      fi
    done
    cd /work/fw
    find . \( -path ./build -o -path ./managed_components \) -prune -o -type f \
        ! -name sdkconfig ! -name sdkconfig.old ! -name dependencies.lock -print |
      while IFS= read -r f; do [ -e "/work/stage/$f" ] || rm -f "$f"; done
    rm -rf /work/stage
    if [ "$FW_CLEAN" = "1" ]; then rm -rf build; fi
    echo ">>> $0"
    eval "$0"
    if [ -d /out ]; then
      mkdir -p /out
      for f in build/*.bin build/*.elf build/*.map build/flasher_args.json build/partition_table/*.bin build/bootloader/*.bin releases/*.zip; do
        [ -e "$f" ] && cp -a --parents "$f" /out/ || true
      done
    fi
  ' "${BUILD_CMD}"
