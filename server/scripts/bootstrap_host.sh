#!/usr/bin/env bash
# First-time preparation of the production host (Ubuntu 26.04 "batai", Docker Engine 29,
# NVIDIA driver 595) for the Jarvis stack. Run ON the host. Dry-run by default.
#
# Usage: scripts/bootstrap_host.sh [--apply] [--install-toolkit] [--user NAME] [--deploy-dir DIR]
#
#   --apply             execute (otherwise every command is only printed)
#   --install-toolkit   install nvidia-container-toolkit (pinned) if nvidia-ctk is missing.
#                       Package scripts reload systemd; GPU containers of other projects that got
#                       their GPU through the legacy hook can lose it -> maintenance window only.
#   --toolkit-version V toolkit package version (default 1.20.1-1)
#   --user NAME         deploy user added to the docker group (default: the user calling sudo/you)
#   --deploy-dir DIR    directory created for the stack, owned by that user (default /opt/jarvis)
#
# Steps: 1) checks (OS, Docker >= 28.2 with CDI, compose plugin, driver)  2) docker group
# 3) toolkit (only with --install-toolkit)  4) CDI spec for nvidia.com/gpu=all, generated
# WITHOUT restarting dockerd  5) deploy directory  6) GPU smoke test through CDI.
# Never restarts dockerd, never runs `nvidia-ctk runtime configure`, never touches ufw:
# other production containers run on this host.
set -euo pipefail
# shellcheck source=_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

INSTALL_TOOLKIT=0
TOOLKIT_VERSION="${NVIDIA_CONTAINER_TOOLKIT_VERSION:-1.20.1-1}"
DEPLOY_USER="${SUDO_USER:-${USER:-$(id -un)}}"
DEPLOY_DIR="/opt/jarvis"
MIN_DOCKER="28.2"
MIN_COMPOSE="2.24.4"

usage() { print_header "${BASH_SOURCE[0]}"; }

while (($#)); do
    case "$1" in
        --apply) DRY_RUN=0; shift ;;
        --install-toolkit) INSTALL_TOOLKIT=1; shift ;;
        --toolkit-version) TOOLKIT_VERSION="${2:?--toolkit-version needs a value}"; shift 2 ;;
        --user) DEPLOY_USER="${2:?--user needs a value}"; shift 2 ;;
        --deploy-dir) DEPLOY_DIR="${2:?--deploy-dir needs a value}"; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
done
re_user='^[A-Za-z_][A-Za-z0-9._-]*$'
[[ "$DEPLOY_USER" =~ $re_user ]] || die "invalid user name: $DEPLOY_USER"
[[ "$DEPLOY_DIR" == /* ]] || die "--deploy-dir must be an absolute path"
[[ "$TOOLKIT_VERSION" =~ ^[0-9][0-9A-Za-z.~+-]*$ ]] || die "invalid --toolkit-version"

# Problems that block --apply (in a dry-run they are only reported).
BLOCKERS=0
blocker() {
    warn "$*"
    BLOCKERS=$((BLOCKERS + 1))
}

dry_run_banner

# ---- 1. checks (read-only) --------------------------------------------------------------------
step "1. Checks"
if is_linux; then
    if [[ -r /etc/os-release ]]; then
        # shellcheck disable=SC1091
        os="$(. /etc/os-release && printf '%s %s' "${NAME:-?}" "${VERSION_ID:-?}")"
        info "OS: $os"
        [[ "$os" == "Ubuntu 26.04"* ]] || warn "written for Ubuntu 26.04; check package names on $os"
    fi
else
    blocker "not a Linux host ($(uname -s 2>/dev/null)): run this script on the server; nothing can be applied here"
fi

docker_ok=0  # dockerd reachable as this user (step 6 needs it)
DOCKER=(docker)
if [[ "$DRY_RUN" == "0" && ${#SUDO[@]} -gt 0 ]] && is_linux && command -v docker >/dev/null 2>&1 &&
    ! docker version >/dev/null 2>&1; then
    # First run: not in the docker group yet (step 2 adds it). Read the versions as root so the
    # Docker/CDI/compose blockers still stop --apply before anything is installed.
    DOCKER=("${SUDO[@]}" docker)
fi
if command -v docker >/dev/null 2>&1 && server_version="$("${DOCKER[@]}" version --format '{{.Server.Version}}' 2>/dev/null)"; then
    if [[ "${DOCKER[0]}" == docker ]]; then
        docker_ok=1
    fi
    if version_ge "$server_version" "$MIN_DOCKER"; then
        ok "Docker Engine $server_version (CDI enabled by default since $MIN_DOCKER)"
    else
        blocker "Docker Engine $server_version < $MIN_DOCKER: upgrade Docker first (CDI is required)"
    fi
    cdi_dirs="$("${DOCKER[@]}" info --format '{{json .CDISpecDirs}}' 2>/dev/null || true)"
    if [[ -z "$cdi_dirs" || "$cdi_dirs" == "null" || "$cdi_dirs" == "[]" ]]; then
        blocker "dockerd has CDI disabled (features.cdi=false in /etc/docker/daemon.json?). Enabling it" \
            "needs a dockerd restart: plan a maintenance window; this script will not restart dockerd"
    else
        ok "CDI spec dirs: $cdi_dirs"
    fi
    if compose_version="$("${DOCKER[@]}" compose version --short 2>/dev/null)"; then
        if version_ge "${compose_version#v}" "$MIN_COMPOSE"; then
            ok "Docker Compose $compose_version"
        else
            blocker "Docker Compose $compose_version < $MIN_COMPOSE (needed for !override/!reset in the overrides)"
        fi
    else
        blocker "docker compose plugin missing (apt install docker-compose-plugin)"
    fi
elif command -v docker >/dev/null 2>&1; then
    warn "docker is installed but dockerd is not reachable as $(id -un) (not in the docker group yet?)"
else
    blocker "Docker Engine is not installed: install it from Docker's apt repository first (README.md)"
fi

if command -v nvidia-smi >/dev/null 2>&1 && driver="$(nvidia-smi --query-gpu=driver_version,name --format=csv,noheader 2>/dev/null | head -n 1)"; then
    ok "NVIDIA driver: $driver"
else
    blocker "NVIDIA driver not working (nvidia-smi): install the driver first"
fi

if ((BLOCKERS > 0)) && [[ "$DRY_RUN" == "0" ]]; then
    die "$BLOCKERS blocking problem(s): fix them, then re-run"
fi

# ---- 2. docker group --------------------------------------------------------------------------
step "2. docker group for '$DEPLOY_USER'"
if id -nG "$DEPLOY_USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
    ok "$DEPLOY_USER is already in the docker group"
else
    run "${SUDO[@]}" usermod -aG docker "$DEPLOY_USER"
    info "log out and back in (or: newgrp docker) for the group to apply"
fi

# ---- 3. NVIDIA Container Toolkit --------------------------------------------------------------
step "3. NVIDIA Container Toolkit"
if command -v nvidia-ctk >/dev/null 2>&1; then
    ok "$(nvidia-ctk --version 2>/dev/null | head -n 1)"
elif [[ "$INSTALL_TOOLKIT" == "1" ]]; then
    warn "installing packages reloads systemd: GPU containers started with the legacy hook (--gpus" \
        "without CDI) may lose their GPU until recreated. Do this in a maintenance window."
    keyring=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    list=/etc/apt/sources.list.d/nvidia-container-toolkit.list
    run "${SUDO[@]}" apt-get update
    run "${SUDO[@]}" apt-get install -y --no-install-recommends ca-certificates curl gnupg2
    run_sh "curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | ${SUDO[*]} gpg --dearmor --yes -o $keyring"
    run_sh "curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=$keyring] https://#g' | ${SUDO[*]} tee $list >/dev/null"
    run "${SUDO[@]}" apt-get update
    run "${SUDO[@]}" apt-get install -y \
        "nvidia-container-toolkit=$TOOLKIT_VERSION" "nvidia-container-toolkit-base=$TOOLKIT_VERSION" \
        "libnvidia-container-tools=$TOOLKIT_VERSION" "libnvidia-container1=$TOOLKIT_VERSION"
    info "do NOT run 'nvidia-ctk runtime configure': it rewrites daemon.json and needs a dockerd restart (CDI does not)"
else
    if [[ "$DRY_RUN" == "0" ]]; then
        die "nvidia-ctk is missing: re-run with --install-toolkit (maintenance window, see --help)"
    fi
    warn "nvidia-ctk is missing: --apply needs --install-toolkit (maintenance window, see --help)"
fi

# ---- 4. CDI spec ------------------------------------------------------------------------------
step "4. CDI spec (no dockerd restart: dockerd's CDI cache picks up new specs by itself)"
if command -v systemctl >/dev/null 2>&1 && systemctl cat nvidia-cdi-refresh.service >/dev/null 2>&1; then
    # Toolkit >= 1.18: the unit writes /var/run/cdi/nvidia.yaml (at boot and on driver changes).
    run "${SUDO[@]}" systemctl restart nvidia-cdi-refresh.service
    if [[ "$(systemctl is-enabled nvidia-cdi-refresh.path 2>/dev/null || true)" != "enabled" ]]; then
        # --no-reload: a plain `enable` reloads systemd (same risk as above for legacy-hook containers).
        run "${SUDO[@]}" systemctl enable --no-reload nvidia-cdi-refresh.path nvidia-cdi-refresh.service
    fi
else
    # Older toolkit (or none yet): static spec in /etc/cdi; re-run this step after a driver upgrade.
    run "${SUDO[@]}" mkdir -p /etc/cdi
    run "${SUDO[@]}" nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
fi
if [[ "$DRY_RUN" == "0" ]]; then
    if nvidia-ctk cdi list 2>/dev/null | grep -q 'nvidia.com/gpu=all'; then
        ok "CDI device nvidia.com/gpu=all available"
    else
        die "CDI spec has no nvidia.com/gpu=all (see: nvidia-ctk --debug cdi list; journalctl -u nvidia-cdi-refresh)"
    fi
else
    run nvidia-ctk cdi list
fi

# ---- 5. deploy directory ----------------------------------------------------------------------
step "5. Deploy directory $DEPLOY_DIR"
if [[ -d "$DEPLOY_DIR" ]]; then
    ok "$DEPLOY_DIR exists (owner: $(stat -c %U "$DEPLOY_DIR" 2>/dev/null || echo '?'))"
else
    run "${SUDO[@]}" install -d -m 0750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$DEPLOY_DIR"
fi

# ---- 6. verification --------------------------------------------------------------------------
step "6. GPU smoke test through CDI"
if [[ "$DRY_RUN" == "1" ]]; then
    run "$SCRIPTS_DIR/gpu_check.sh" --mode cdi
elif [[ "$docker_ok" == "1" ]]; then
    "$SCRIPTS_DIR/gpu_check.sh" --mode cdi || die "GPU check failed"
else
    info "dockerd not reachable as $(id -un) yet: log in again (docker group), then run $SCRIPTS_DIR/gpu_check.sh --mode cdi"
fi

step "Next"
info "from your PC: server/scripts/deploy.sh --host <this host> --dir $DEPLOY_DIR (dry-run, then --apply)"
info "on this host: cd $DEPLOY_DIR/server && cp .env.example .env && chmod 600 .env && \$EDITOR .env"
info "ports: XIAOZHI_WS_PORT=8100 / XIAOZHI_OTA_PORT=8003; host port 8000 belongs to another project"
info "firewall (optional, read README first): scripts/firewall_lan.sh (dry-run), then --apply"
