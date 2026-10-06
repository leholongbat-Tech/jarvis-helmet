#!/usr/bin/env bash
# Read-only GPU checks for the Jarvis stack. Safe on the production host at any time: it changes
# nothing (the smoke test only runs a throw-away CUDA container, pulled once).
#
# Usage: scripts/gpu_check.sh [--mode cdi|gpus] [--no-smoke] [--image IMAGE]
#
#   --mode cdi    (default) smoke test with a CDI device request (--device nvidia.com/gpu=all),
#                 the path docker-compose.yml uses; works without restarting dockerd.
#   --mode gpus   smoke test with --gpus all (Docker Desktop, or a dockerd started after the
#                 NVIDIA hooks were installed); what docker-compose.local.yml uses.
#   --no-smoke    skip the container test.
#   --image IMG   CUDA image for the smoke test (default nvidia/cuda:12.8.0-base-ubuntu24.04).
# Exit code: 0 = all checks passed, 1 = a check failed.
set -euo pipefail
# shellcheck source=_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

MODE=cdi
SMOKE=1
IMAGE="${GPU_CHECK_IMAGE:-nvidia/cuda:12.8.0-base-ubuntu24.04}"

usage() { print_header "${BASH_SOURCE[0]}"; }

while (($#)); do
    case "$1" in
        --mode) MODE="${2:?--mode needs a value}"; shift 2 ;;
        --mode=*) MODE="${1#*=}"; shift ;;
        --no-smoke) SMOKE=0; shift ;;
        --image) IMAGE="${2:?--image needs a value}"; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
done
[[ "$MODE" == "cdi" || "$MODE" == "gpus" ]] || die "--mode must be cdi or gpus"

FAILS=0
fail() { warn "$*"; FAILS=$((FAILS + 1)); }

step "NVIDIA driver"
if command -v nvidia-smi >/dev/null 2>&1; then
    if out="$(nvidia-smi --query-gpu=index,name,driver_version,memory.used,memory.total --format=csv,noheader 2>&1)"; then
        while IFS= read -r line; do ok "GPU $line"; done <<<"$out"
    else
        fail "nvidia-smi failed: $out"
    fi
else
    fail "nvidia-smi not found (NVIDIA driver not installed?)"
fi

step "Docker"
require_cmd docker "install Docker Engine first (README.md)"
if server="$(docker version --format '{{.Server.Version}}' 2>/dev/null)"; then
    ok "Docker Engine $server"
    if version_ge "$server" "28.2"; then
        ok "CDI enabled by default in this Engine version (>= 28.2)"
    else
        warn "Docker Engine < 28.2: CDI must be enabled in daemon.json (features.cdi=true)"
    fi
    cdi_dirs="$(docker info --format '{{json .CDISpecDirs}}' 2>/dev/null || true)"
    if [[ -z "$cdi_dirs" || "$cdi_dirs" == "null" || "$cdi_dirs" == "[]" ]]; then
        if [[ "$MODE" == "cdi" ]]; then
            fail "dockerd reports no CDI spec dirs (CDI disabled?): CDI device requests will fail"
        else
            info "no CDI spec dirs reported (fine for --mode gpus / Docker Desktop)"
        fi
    else
        ok "CDI spec dirs: $cdi_dirs"
    fi
    runtime="$(docker info --format '{{.DefaultRuntime}}' 2>/dev/null || true)"
    info "default runtime: ${runtime:-unknown} (CDI works with plain runc; no 'nvidia' runtime needed)"
    devices="$(docker info --format '{{json .DiscoveredDevices}}' 2>/dev/null || true)"
    if [[ -n "$devices" && "$devices" != "null" && "$devices" != "[]" ]]; then
        info "devices discovered by dockerd: $devices"
    fi
else
    fail "cannot talk to dockerd (is it running? is this user in the docker group?)"
fi

step "NVIDIA Container Toolkit / CDI spec"
if command -v nvidia-ctk >/dev/null 2>&1; then
    ok "$(nvidia-ctk --version 2>/dev/null | head -n 1)"
    if cdi_list="$(nvidia-ctk cdi list 2>/dev/null)" && grep -q 'nvidia.com/gpu=all' <<<"$cdi_list"; then
        ok "CDI spec provides nvidia.com/gpu=all ($(grep -c 'nvidia.com/gpu=' <<<"$cdi_list") device names)"
    else
        fail "no CDI device nvidia.com/gpu=all: run scripts/bootstrap_host.sh (--apply) to generate the spec"
    fi
    for spec in /etc/cdi/nvidia.yaml /var/run/cdi/nvidia.yaml; do
        [[ -f "$spec" ]] && info "spec file: $spec ($(wc -c <"$spec") bytes)"
    done
    if command -v systemctl >/dev/null 2>&1 && systemctl cat nvidia-cdi-refresh.path >/dev/null 2>&1; then
        info "nvidia-cdi-refresh.path: $(systemctl is-enabled nvidia-cdi-refresh.path 2>/dev/null || true) / $(systemctl is-active nvidia-cdi-refresh.path 2>/dev/null || true)"
    fi
elif [[ "$MODE" == "cdi" ]] && is_linux; then
    fail "nvidia-ctk not found: install nvidia-container-toolkit (scripts/bootstrap_host.sh --install-toolkit)"
else
    info "nvidia-ctk not found (expected on Docker Desktop)"
fi

step "Smoke test ($MODE)"
if [[ "$SMOKE" == "1" ]]; then
    if [[ "$MODE" == "cdi" ]]; then
        gpu_args=(--device nvidia.com/gpu=all)
    else
        gpu_args=(--gpus all)
    fi
    info "$(quote_cmd docker run --rm "${gpu_args[@]}" "$IMAGE" nvidia-smi -L)"
    if out="$(docker run --rm "${gpu_args[@]}" "$IMAGE" nvidia-smi -L 2>&1)"; then
        # CUDA images print a license banner first: keep the "GPU n: ..." lines.
        gpus="$(grep -E '^GPU [0-9]+:' <<<"$out" || true)"
        if [[ -n "$gpus" ]]; then
            while IFS= read -r line; do ok "$line"; done <<<"$gpus"
        else
            fail "container ran but listed no GPU: $(tail -n 2 <<<"$out" | tr '\n' ' ')"
        fi
    else
        fail "GPU container failed: $(tail -n 3 <<<"$out" | tr '\n' ' ')"
    fi
else
    info "skipped (--no-smoke)"
fi

step "Result"
if ((FAILS == 0)); then
    ok "all GPU checks passed"
else
    die "$FAILS check(s) failed"
fi
