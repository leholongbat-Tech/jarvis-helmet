#!/usr/bin/env bash
# Read-only VRAM report: GPU totals, GPU processes mapped to their containers (Jarvis services vs
# other tenants of the host), the model Ollama keeps loaded, and a check against a budget.
#
# Usage: scripts/vram_report.sh [--budget-gb N] [--project NAME] [--watch SECONDS] [--strict]
#
#   --budget-gb N    VRAM budget of the Jarvis containers in GB (default 28, SV1 acceptance #3)
#   --project NAME   compose project whose containers count as Jarvis (default jarvis)
#   --watch S        repeat every S seconds (Ctrl-C to stop)
#   --strict         exit 1 when the Jarvis containers use more than the budget
#
# Needs nvidia-smi on the host. Process -> container mapping reads /proc/<pid>/cgroup (Linux);
# PIDs that belong to no container are shown as "host".
set -euo pipefail
# shellcheck source=_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

BUDGET_GB=28
PROJECT="${COMPOSE_PROJECT_NAME:-jarvis}"
WATCH=0
STRICT=0
PROC_ROOT="${PROC_ROOT:-/proc}"

usage() { print_header "${BASH_SOURCE[0]}"; }

while (($#)); do
    case "$1" in
        --budget-gb) BUDGET_GB="${2:?--budget-gb needs a value}"; shift 2 ;;
        --project) PROJECT="${2:?--project needs a value}"; shift 2 ;;
        --watch) WATCH="${2:?--watch needs a value}"; shift 2 ;;
        --strict) STRICT=1; shift ;;
        -h | --help) usage; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
done
[[ "$BUDGET_GB" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "--budget-gb must be a number"
[[ "$WATCH" =~ ^[0-9]+$ ]] || die "--watch must be a whole number of seconds"
require_cmd nvidia-smi "this report needs the NVIDIA driver tools"

# container id -> name of every running container ("" when docker is not reachable).
container_names() {
    if command -v docker >/dev/null 2>&1; then
        docker ps --no-trunc --format '{{.ID}} {{.Names}}' 2>/dev/null || true
    fi
}

# container_of PID: name of the container the process runs in, or "host".
container_of() {
    local pid="$1" cid name
    cid="$(grep -oE '[0-9a-f]{64}' "$PROC_ROOT/$pid/cgroup" 2>/dev/null | head -n 1 || true)"
    if [[ -n "$cid" ]]; then
        name="$(awk -v id="$cid" '$1 == id { print $2; exit }' <<<"$NAMES")"
        printf '%s' "${name:-container:${cid:0:12}}"
    else
        printf 'host'
    fi
}

report() {
    NAMES="$(container_names)"
    step "GPUs ($(date '+%Y-%m-%d %H:%M:%S'))"
    nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu \
        --format=csv,noheader,nounits |
        while IFS=, read -r idx name used total util; do
            printf '  GPU%s %-28s %6s / %6s MiB used, %s%% busy\n' \
                "${idx// /}" "$(echo "$name" | xargs)" "${used// /}" "${total// /}" "${util// /}"
        done

    step "GPU processes"
    local jarvis_mib=0 other_mib=0 unknown=0 pid pname mem owner
    printf '  %-8s %-34s %-10s %s\n' PID CONTAINER MiB PROCESS
    while IFS=, read -r pid pname mem; do
        pid="${pid// /}"
        mem="${mem// /}"
        pname="$(echo "$pname" | xargs)"
        [[ -n "$pid" ]] || continue
        owner="$(container_of "$pid")"
        printf '  %-8s %-34s %-10s %s\n' "$pid" "$owner" "$mem" "$pname"
        if [[ "$mem" =~ ^[0-9]+$ ]]; then
            if [[ "$owner" == "$PROJECT"-* ]]; then
                jarvis_mib=$((jarvis_mib + mem))
            else
                other_mib=$((other_mib + mem))
            fi
        else
            unknown=$((unknown + 1))
        fi
    done < <(nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits 2>/dev/null || true)

    if command -v docker >/dev/null 2>&1 && awk '{print $2}' <<<"$NAMES" | grep -qx "${PROJECT}-ollama-1"; then
        step "Ollama (loaded models)"
        docker exec "${PROJECT}-ollama-1" ollama ps 2>/dev/null | sed 's/^/  /' || warn "ollama ps failed"
    fi

    step "Budget"
    local budget_mib
    budget_mib="$(awk -v gb="$BUDGET_GB" 'BEGIN { printf "%d", gb * 1024 }')"
    info "Jarvis containers (${PROJECT}-*): ${jarvis_mib} MiB of a ${budget_mib} MiB budget (${BUDGET_GB} GB)"
    info "other processes on the GPU: ${other_mib} MiB"
    if ((unknown > 0)); then
        warn "$unknown process(es) without a memory figure (no permission or WSL); totals are incomplete"
    fi
    if ((jarvis_mib > budget_mib)); then
        warn "Jarvis containers exceed the budget"
        return 1
    fi
    ok "within budget"
}

while true; do
    status=0
    report || status=$?
    if ((WATCH == 0)); then
        if ((status != 0 && STRICT == 1)); then
            exit 1
        fi
        exit 0
    fi
    sleep "$WATCH"
done
