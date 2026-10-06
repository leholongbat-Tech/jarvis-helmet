#!/usr/bin/env bash
# Copy the server stack to the production host and (re)start it there. Run on your PC.
# Dry-run by default: every command is printed, nothing is copied and no ssh connection is made.
#
# Usage: server/scripts/deploy.sh [--apply] [--host HOST] [--dir DIR] [--method auto|rsync|tar|archive]
#                                 [--ref REF] [--profiles "tunnel ..."] [--copy-only] [--no-build]
#                                 [--pull-models]
#
#   --host HOST    ssh destination (default $JARVIS_DEPLOY_HOST or "batai"). User, port and key come
#                  from ~/.ssh/config / ssh-agent: this script holds no credentials. Extra ssh
#                  options: JARVIS_SSH_OPTS (default "-o BatchMode=yes", i.e. key-based login).
#   --dir DIR      stack directory on the host (default $JARVIS_DEPLOY_DIR or /opt/jarvis)
#   --method M     rsync   = working tree, removes stale files (rsync needed on both sides)
#                  tar     = working tree streamed over ssh, keeps stale files (Git Bash has no rsync)
#                  archive = `git archive REF`: committed files only
#                  auto    = rsync when installed locally, else tar (default)
#   --ref REF      commit or branch for --method archive (default HEAD)
#   --profiles P   COMPOSE_PROFILES on the host, e.g. "tunnel" or "tunnel english-tts"
#   --copy-only    copy the files, leave the containers alone
#   --no-build     skip `docker compose build` (images are still built when missing)
#   --pull-models  run scripts/pull_models.sh --apply on the host afterwards
#
# Copied: server/ (without .env, tests and caches) -> DIR/server; tools/tasks_cli.py and
# tools/tests/llm/ -> DIR/tools (the tool-call test reads ../server). DIR/server/.env and a host-local
# DIR/server/docker-compose.override.yml are never copied, overwritten or deleted: create .env once on
# the host (README.md "Cài đặt lần đầu").
set -euo pipefail
# shellcheck source=_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

HOST="${JARVIS_DEPLOY_HOST:-batai}"
DIR="${JARVIS_DEPLOY_DIR:-/opt/jarvis}"
METHOD=auto
REF=HEAD
PROFILES=""
COPY_ONLY=0
BUILD=1
PULL_MODELS=0

usage() { print_header "${BASH_SOURCE[0]}"; }

while (($#)); do
    case "$1" in
        --apply) DRY_RUN=0; shift ;;
        --host) HOST="${2:?--host needs a value}"; shift 2 ;;
        --dir) DIR="${2:?--dir needs a value}"; shift 2 ;;
        --method) METHOD="${2:?--method needs a value}"; shift 2 ;;
        --ref) REF="${2:?--ref needs a value}"; shift 2 ;;
        --profiles) PROFILES="${2?--profiles needs a value}"; shift 2 ;;
        --copy-only) COPY_ONLY=1; shift ;;
        --no-build) BUILD=0; shift ;;
        --pull-models) PULL_MODELS=1; shift ;;
        -h | --help) usage; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
done

re_host='^[A-Za-z0-9._@:-]+$'
re_dir='^/[A-Za-z0-9._/-]+$'
re_profiles='^[a-z0-9_ -]*$'
re_ref='^[A-Za-z0-9._/~^-]+$'
[[ "$HOST" =~ $re_host ]] || die "invalid --host: $HOST"
[[ "$DIR" =~ $re_dir && "$DIR" != *..* ]] || die "invalid --dir (absolute path, no spaces/..): $DIR"
[[ "$PROFILES" =~ $re_profiles ]] || die "invalid --profiles: $PROFILES"
[[ "$REF" =~ $re_ref ]] || die "invalid --ref: $REF"
case "$METHOD" in
    auto) if command -v rsync >/dev/null 2>&1; then METHOD=rsync; else METHOD=tar; fi ;;
    rsync | tar | archive) ;;
    *) die "--method must be auto, rsync, tar or archive" ;;
esac

# Paths under server/ that never go to the host (and are never deleted there by rsync --delete).
# Anchored ones are relative to server/; docker-compose.override.yml is a host-local override.
EXCLUDE_ANCHORED_DIRS=(tests scripts/tests litellm/tests models)
EXCLUDE_ANCHORED_FILES=(docker-compose.override.yml)
EXCLUDE_ANYWHERE=(.env __pycache__ '*.pyc' .pytest_cache '*.secret' cert.pem '*-*-*-*-*.json')
TOOLS_PATHS=(tools/tasks_cli.py tools/tests/llm)
# BatchMode: fail instead of hanging on a password prompt (use an ssh key / agent). Override with
# JARVIS_SSH_OPTS, e.g. JARVIS_SSH_OPTS="-o BatchMode=no -p 2222".
read -r -a ssh_opts <<<"${JARVIS_SSH_OPTS:--o BatchMode=yes}"
SSH=(ssh "${ssh_opts[@]}")
Q_DIR="$(printf '%q' "$DIR")"

# remote 'shell command': run it on the host (printed single-quoted, as you would type it).
remote() {
    if [[ "$DRY_RUN" == "1" ]]; then
        printf '  [dry-run] %s %s %s\n' "${SSH[*]}" "$HOST" "$(squote "$1")"
    else
        printf '  + %s %s %s\n' "${SSH[*]}" "$HOST" "$(squote "$1")"
        "${SSH[@]}" "$HOST" "$1"
    fi
}

dry_run_banner
step "Deploy $REPO_DIR -> $HOST:$DIR (method: $METHOD)"

# ---- local checks (read-only) -----------------------------------------------------------------
[[ -f "$SERVER_DIR/docker-compose.yml" ]] || die "missing $SERVER_DIR/docker-compose.yml"
for p in "${TOOLS_PATHS[@]}"; do
    [[ -e "$REPO_DIR/$p" ]] || die "missing $REPO_DIR/$p"
done

if [[ "$METHOD" != "archive" ]]; then
    # CRLF line endings (Windows checkout with core.autocrlf=true) break shell scripts and
    # Dockerfiles on the Linux host.
    crlf="$(crlf_files "$SERVER_DIR")"
    if [[ -n "$crlf" ]]; then
        printf '%s\n' "$crlf" | sed 's/^/    /' >&2
        die "files above have CRLF line endings: convert them to LF (e.g. dos2unix, or git config core.autocrlf input + re-checkout)"
    fi
    ok "no CRLF line endings in scripts/Dockerfiles/templates"
else
    if command -v git >/dev/null 2>&1 && [[ -n "$(git -C "$REPO_DIR" status --porcelain -- server tools 2>/dev/null || true)" ]]; then
        warn "uncommitted changes under server/ or tools/ are NOT deployed with --method archive"
    fi
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    # The placeholders of .env.example satisfy every ${VAR:?} check: validates the compose file.
    if (cd "$SERVER_DIR" && docker compose --env-file .env.example config -q) 2>/dev/null; then
        ok "docker-compose.yml is valid (checked locally with .env.example)"
    else
        die "docker compose config failed locally: (cd server && docker compose --env-file .env.example config)"
    fi
else
    info "docker not available locally: compose file not pre-checked"
fi

# ---- copy -------------------------------------------------------------------------------------
step "Copy files"
remote "mkdir -p $Q_DIR/server $Q_DIR/tools"
case "$METHOD" in
    rsync)
        rsync_args=(-az --delete --human-readable)
        for e in "${EXCLUDE_ANCHORED_DIRS[@]}"; do rsync_args+=("--exclude=/$e/"); done
        for e in "${EXCLUDE_ANCHORED_FILES[@]}"; do rsync_args+=("--exclude=/$e"); done
        for e in "${EXCLUDE_ANYWHERE[@]}"; do rsync_args+=("--exclude=$e"); done
        run rsync "${rsync_args[@]}" -e "${SSH[*]}" "$SERVER_DIR/" "$HOST:$DIR/server/"
        run_sh "cd $(printf '%q' "$REPO_DIR") && $(quote_cmd rsync -azR --delete --exclude=__pycache__ --exclude='*.pyc' -e "${SSH[*]}" "${TOOLS_PATHS[@]}" "$HOST:$DIR/")"
        ;;
    tar)
        tar_args=()
        for e in "${EXCLUDE_ANCHORED_DIRS[@]}" "${EXCLUDE_ANCHORED_FILES[@]}"; do tar_args+=("--exclude=server/$e"); done
        for e in "${EXCLUDE_ANYWHERE[@]}"; do tar_args+=("--exclude=$e"); done
        run_sh "cd $(printf '%q' "$REPO_DIR") && $(quote_cmd tar -czf - "${tar_args[@]}" server "${TOOLS_PATHS[@]}") | $(quote_cmd "${SSH[@]}" "$HOST" "tar -xzf - -C $Q_DIR")"
        ;;
    archive)
        x_args=()
        for e in "${EXCLUDE_ANCHORED_DIRS[@]}" "${EXCLUDE_ANCHORED_FILES[@]}"; do x_args+=("--exclude=server/$e"); done
        run_sh "$(quote_cmd git -C "$REPO_DIR" archive --format=tar.gz "$REF" server "${TOOLS_PATHS[@]}") | $(quote_cmd "${SSH[@]}" "$HOST" "tar -xzf - -C $Q_DIR ${x_args[*]}")"
        ;;
esac
# NTFS checkouts lose the executable bit.
remote "chmod +x $Q_DIR/server/scripts/*.sh $Q_DIR/server/cloudflared/*.sh 2>/dev/null || true"

if [[ "$COPY_ONLY" == "1" ]]; then
    ok "files copied (--copy-only)"
    exit 0
fi

# ---- containers -------------------------------------------------------------------------------
step "Containers"
if [[ "$DRY_RUN" == "0" ]] && ! "${SSH[@]}" "$HOST" "test -f $Q_DIR/server/.env"; then
    warn "$HOST:$DIR/server/.env does not exist yet. Create it, then re-run deploy.sh --apply:"
    info "ssh $HOST 'cd $DIR/server && cp .env.example .env && chmod 600 .env'  # then edit the values"
    exit 1
fi
profiles_env=""
if [[ -n "$PROFILES" ]]; then
    profiles_env="export COMPOSE_PROFILES=$(printf '%q' "${PROFILES// /,}") && "
fi
compose_steps="docker compose config -q && docker compose pull --ignore-buildable --quiet"
if [[ "$BUILD" == "1" ]]; then
    compose_steps+=" && docker compose build --pull"
fi
compose_steps+=" && docker compose up -d --remove-orphans && docker compose ps"
remote "cd $Q_DIR/server && ${profiles_env}${compose_steps}"

if [[ "$PULL_MODELS" == "1" ]]; then
    step "Models"
    remote "cd $Q_DIR/server && ${profiles_env}bash scripts/pull_models.sh --apply"
fi

step "Done"
if [[ "$DRY_RUN" == "1" ]]; then
    info "dry-run only; re-run with --apply"
else
    ok "deployed; follow the start with: ssh $HOST 'cd $DIR/server && docker compose logs -f --tail 50'"
fi
