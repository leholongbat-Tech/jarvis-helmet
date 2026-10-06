#!/usr/bin/env bash
# Shared helpers for server/scripts/*.sh. Sourced, never executed directly.
# shellcheck shell=bash

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd "$SCRIPTS_DIR/.." && pwd)"
REPO_DIR="$(cd "$SERVER_DIR/.." && pwd)"
export SCRIPTS_DIR SERVER_DIR REPO_DIR

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    C_RED=$'\e[31m' C_YEL=$'\e[33m' C_GRN=$'\e[32m' C_BLD=$'\e[1m' C_RST=$'\e[0m'
else
    C_RED='' C_YEL='' C_GRN='' C_BLD='' C_RST=''
fi

# print_header FILE: the leading comment block of a script (its usage text), without "# ".
print_header() {
    awk 'NR == 1 && /^#!/ { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$1"
}

step() { printf '\n%s== %s ==%s\n' "$C_BLD" "$*" "$C_RST"; }
info() { printf '  %s\n' "$*"; }
ok()   { printf '  %s[ok]%s %s\n' "$C_GRN" "$C_RST" "$*"; }
warn() { printf '  %s[!]%s %s\n' "$C_YEL" "$C_RST" "$*" >&2; }
die()  { printf '%s[x] %s%s\n' "$C_RED" "$*" "$C_RST" >&2; exit 1; }

# Shell-quoted rendering of a command line (for logs and dry-run output).
quote_cmd() {
    local quoted=() arg
    for arg in "$@"; do
        quoted+=("$(printf '%q' "$arg")")
    done
    printf '%s' "${quoted[*]}"
}

# squote STRING: STRING in single quotes, safe to paste into a POSIX shell.
squote() {
    local q="'\\''"
    printf "'%s'" "${1//\'/$q}"
}

# run CMD [ARGS...]: print it in dry-run mode (DRY_RUN=1, default), execute it otherwise.
# Scripts that change a host set DRY_RUN=0 only when called with --apply.
DRY_RUN="${DRY_RUN:-1}"

# Prefix for commands that need root: "${SUDO[@]}" is empty when already root.
# shellcheck disable=SC2034  # used by the scripts that source this file
if [[ "$(id -u 2>/dev/null || echo 1000)" == "0" ]]; then
    SUDO=()
else
    SUDO=(sudo)
fi

dry_run_banner() {
    if [[ "$DRY_RUN" == "1" ]]; then
        printf '%sDRY-RUN%s: nothing is changed; commands are only printed. Re-run with --apply to execute.\n' "$C_YEL" "$C_RST"
    fi
}

run() {
    if [[ "$DRY_RUN" == "1" ]]; then
        printf '  [dry-run] %s\n' "$(quote_cmd "$@")"
    else
        printf '  + %s\n' "$(quote_cmd "$@")"
        "$@"
    fi
}

# run_sh 'shell pipeline': same as run, for commands that need a pipe or redirection.
run_sh() {
    if [[ "$DRY_RUN" == "1" ]]; then
        printf '  [dry-run] %s\n' "$1"
    else
        printf '  + %s\n' "$1"
        bash -c "$1"
    fi
}

# env_get KEY [FILE]: value of KEY in a .env file (default server/.env) without sourcing it
# (values may contain spaces). Strips matching quotes and a trailing CR. Returns 1 if absent.
env_get() {
    local key="$1" file="${2:-$SERVER_DIR/.env}" line value
    [[ -f "$file" ]] || return 1
    # (no reliance on the caller's pipefail: an empty match means "absent")
    line="$(grep -E "^[[:space:]]*${key}=" "$file" | tail -n 1)" || true
    [[ -n "$line" ]] || return 1
    value="${line#*=}"
    value="${value%$'\r'}"
    if [[ ${#value} -ge 2 ]]; then
        if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]] || [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
            value="${value:1:${#value}-2}"
        fi
    fi
    printf '%s' "$value"
}

# compose ARGS...: docker compose in server/ (honours COMPOSE_FILE / COMPOSE_PROFILES / COMPOSE_PROJECT_NAME).
compose() {
    (cd "$SERVER_DIR" && docker compose "$@")
}

# require_cmd CMD [HINT]
require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "missing command '$1'${2:+: $2}"
}

# version_ge A B: true if version A >= B (dotted numbers).
version_ge() {
    [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" == "$2" ]]
}

# crlf_files DIR: files under DIR that must run on Linux (shell scripts, Dockerfiles, templates)
# but contain CR characters. Uses tr, not grep: Git Bash's grep reports false CR matches.
crlf_files() {
    local f
    while IFS= read -r -d '' f; do
        if [[ -n "$(tr -dc '\r' <"$f" | head -c 1)" ]]; then
            printf '%s\n' "$f"
        fi
    done < <(find "$1" -type d \( -name __pycache__ -o -name .pytest_cache \) -prune -o -type f \
        \( -name '*.sh' -o -name 'Dockerfile*' -o -name '*.dockerignore' -o -name '.env.example' \
        -o -name '*.tmpl' \) -print0)
}

# is_linux: true on a Linux host (the scripts that change a host only act there).
is_linux() {
    [[ "$(uname -s 2>/dev/null)" == "Linux" ]]
}

# valid_ipv4 ADDR: dotted quad with every octet in 0..255.
valid_ipv4() {
    local ip="$1" octet
    local -a octets
    [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || return 1
    IFS=. read -r -a octets <<<"$ip"
    for octet in "${octets[@]}"; do
        ((10#$octet <= 255)) || return 1
    done
}
