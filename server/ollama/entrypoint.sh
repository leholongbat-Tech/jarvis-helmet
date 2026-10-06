#!/bin/sh
# Entrypoint of the ollama container (mounted read-only by docker-compose.yml, run by dash).
#
# Runs `ollama serve` as the main process and, in the background, preloads the model named by
# JARVIS_PRELOAD_MODEL (= OLLAMA_MODEL in server/.env) as soon as the server answers. With
# OLLAMA_KEEP_ALIVE=-1 the model then stays on the GPU, so the first voice command after a
# reboot does not wait for a cold 19 GB load (which would trip LiteLLM's stream timeout and
# send the request to the paid cloud model). A model that is not pulled yet is skipped:
# scripts/pull_models.sh pulls it (and preloads it as well).
#
# Knobs: JARVIS_PRELOAD=0 disables the preload; JARVIS_PRELOAD_WAIT_S (default 120) = how long
# to wait for the server.
set -u

log() { printf 'jarvis-preload: %s\n' "$*"; }

preload() {
    model="${JARVIS_PRELOAD_MODEL:-}"
    if [ -z "$model" ]; then
        log "JARVIS_PRELOAD_MODEL is empty, nothing to preload"
        return 0
    fi
    waited=0
    until ollama list >/dev/null 2>&1; do
        waited=$((waited + 1))
        if [ "$waited" -ge "${JARVIS_PRELOAD_WAIT_S:-120}" ]; then
            log "server not answering after ${waited}s, giving up"
            return 0
        fi
        sleep 1
    done
    if ! ollama show "$model" >/dev/null 2>&1; then
        log "model '$model' is not pulled yet (run scripts/pull_models.sh), skipping"
        return 0
    fi
    log "loading '$model' ..."
    start=$(date +%s)
    # An empty prompt only loads the model (Ollama FAQ "How can I preload a model").
    if ollama run "$model" "" >/dev/null 2>&1; then
        log "'$model' loaded in $(($(date +%s) - start))s"
    else
        log "preloading '$model' failed (see the server log)"
    fi
}

if [ "${JARVIS_PRELOAD:-1}" != "0" ]; then
    # Double fork: the helper is re-parented to the container init (`init: true`) and reaped there.
    (preload &)
fi

if [ "$#" -eq 0 ]; then
    set -- serve
fi
exec ollama "$@"
