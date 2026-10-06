#!/usr/bin/env bash
# Pull the local LLM into the ollama container (volume ollama_models), preload it on the GPU and
# show what is loaded. Optionally download the Whisper model into the ASR cache as well.
# Dry-run by default: prints the commands; --apply runs them.
#
# Usage: scripts/pull_models.sh [--apply] [--model TAG]... [--asr] [--no-preload]
#
#   --model TAG    model(s) to pull (repeatable). Default: OLLAMA_MODEL from server/.env
#                  (qwen3:30b-a3b-instruct-2507-q4_K_M, about 19 GB). Only OLLAMA_MODEL is preloaded:
#                  OLLAMA_MAX_LOADED_MODELS=1, loading another model would evict it.
#   --asr          also run the ASR prefetch (jarvis_asr.prefetch: ASR_MODEL into the hf_cache volume)
#   --no-preload   pull only, do not load the model on the GPU
#
# Runs in server/ with `docker compose` (honours COMPOSE_FILE / COMPOSE_PROFILES). Starts the
# ollama service first if it is not running.
set -euo pipefail
# shellcheck source=_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

DEFAULT_MODEL="qwen3:30b-a3b-instruct-2507-q4_K_M"
MODELS=()
ASR=0
PRELOAD=1

usage() { print_header "${BASH_SOURCE[0]}"; }

while (($#)); do
    case "$1" in
        --apply) DRY_RUN=0; shift ;;
        --model) MODELS+=("${2:?--model needs a value}"); shift 2 ;;
        --model=*) MODELS+=("${1#*=}"); shift ;;
        --asr) ASR=1; shift ;;
        --no-preload) PRELOAD=0; shift ;;
        -h | --help) usage; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
done

MAIN_MODEL="$(env_get OLLAMA_MODEL || true)"
MAIN_MODEL="${MAIN_MODEL:-$DEFAULT_MODEL}"
if ((${#MODELS[@]} == 0)); then
    MODELS=("$MAIN_MODEL")
fi
for m in "${MODELS[@]}"; do
    [[ "$m" =~ ^[A-Za-z0-9._:/-]+$ ]] || die "invalid model tag: $m"
done

dry_run_banner
step "Models"
info "OLLAMA_MODEL (preloaded): $MAIN_MODEL"
info "to pull: ${MODELS[*]}"
[[ -f "$SERVER_DIR/.env" ]] || warn "server/.env not found: using the default model and compose defaults"

step "Ollama service"
running=""
if command -v docker >/dev/null 2>&1; then
    running="$(compose ps --status running --services 2>/dev/null || true)"
fi
if grep -qx ollama <<<"$running"; then
    ok "ollama is running"
else
    info "ollama is not running: starting it"
    run compose up -d ollama
    if [[ "$DRY_RUN" == "0" ]]; then
        for _ in $(seq 1 60); do
            compose exec -T ollama ollama list </dev/null >/dev/null 2>&1 && break
            sleep 2
        done
    fi
fi

step "Pull"
for m in "${MODELS[@]}"; do
    run compose exec -T ollama ollama pull "$m" </dev/null
done

if [[ "$PRELOAD" == "1" ]]; then
    step "Preload $MAIN_MODEL"
    if [[ "$DRY_RUN" == "0" ]] && ! compose exec -T ollama ollama show "$MAIN_MODEL" </dev/null >/dev/null 2>&1; then
        # `ollama run` would silently start the download of a model that is not pulled.
        warn "$MAIN_MODEL is not pulled: not preloading (add --model $MAIN_MODEL)"
    else
        # </dev/null: `ollama run` reads a prompt from stdin and would wait forever on a terminal.
        run compose exec -T ollama ollama run "$MAIN_MODEL" "" </dev/null
        run compose exec -T ollama ollama ps </dev/null
        info "check: SIZE ~20 GB for the default model, PROCESSOR '100% GPU', CONTEXT 8192"
    fi
fi

if [[ "$ASR" == "1" ]]; then
    step "ASR model (hf_cache volume)"
    run compose run --rm --no-deps asr python -m jarvis_asr.prefetch
fi

step "Done"
if [[ "$DRY_RUN" == "1" ]]; then
    info "dry-run only; re-run with --apply"
else
    ok "models ready; LiteLLM uses OLLAMA_MODEL after: docker compose up -d litellm (if you changed it)"
fi
