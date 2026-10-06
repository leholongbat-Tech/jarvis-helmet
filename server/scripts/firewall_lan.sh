#!/usr/bin/env bash
# Restrict the xiaozhi host ports (WebSocket 8100, OTA 8003) to the LAN. Not run automatically.
#
# Docker-published ports bypass ufw and the INPUT chain (Docker DNATs them in PREROUTING and the
# traffic is FORWARDed), so the filter goes into iptables' DOCKER-USER chain, matching the port
# the client connected to (--ctorigdstport) before Docker's DNAT. Only connections Docker DNATed
# for a published port are matched (--ctstate DNAT): containers' own outbound traffic to some
# other host's port 8003/8100 and traffic between containers are never touched. The script only
# touches rules tagged "jarvis-lan": other projects' containers on the host keep working. It never
# enables or changes ufw and never restarts dockerd. Dry-run by default.
#
# Usage: scripts/firewall_lan.sh [--apply] [--subnet CIDR] [--ports "8100 8003"] [--status]
#                                [--remove] [--persist] [--quiet]
#
#   (default)        show the rules that would be installed
#   --apply          (re)install them (old jarvis-lan rules are removed first)
#   --subnet CIDR    allowed source network (default: LAN_IP from server/.env as a /24)
#   --ports "A B"    host ports (default: XIAOZHI_WS_PORT / XIAOZHI_OTA_PORT from server/.env, 8100 8003)
#   --status         list the installed jarvis-lan rules and exit
#   --remove         delete the jarvis-lan rules (with --apply)
#   --persist        also add a root crontab "@reboot" entry that re-applies the rules after a
#                    reboot (no systemd unit, so no `systemctl daemon-reload` on a shared GPU host)
#   --quiet          less output (for the @reboot entry)
#
# Test from a machine outside the subnet: curl -m 3 http://<server>:8100/ must time out.
set -euo pipefail
# The --persist @reboot entry runs under cron's PATH (/usr/bin:/bin), but iptables is in /usr/sbin.
# Appended, so a PATH that already finds the commands keeps priority.
export PATH="${PATH:+$PATH:}/usr/local/sbin:/usr/sbin:/sbin"
# shellcheck source=_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

TAG="jarvis-lan"
SUBNET=""
PORTS=""
MODE=install
PERSIST=0
QUIET=0
CHAIN="DOCKER-USER"

usage() { print_header "${BASH_SOURCE[0]}"; }

while (($#)); do
    case "$1" in
        --apply) DRY_RUN=0; shift ;;
        --subnet) SUBNET="${2:?--subnet needs a value}"; shift 2 ;;
        --ports) PORTS="${2:?--ports needs a value}"; shift 2 ;;
        --status) MODE=status; shift ;;
        --remove) MODE=remove; shift ;;
        --persist) PERSIST=1; shift ;;
        --quiet) QUIET=1; shift ;;
        -h | --help) usage; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
done

say() { if [[ "$QUIET" == "0" ]]; then info "$*"; fi; }

# ---- parameters -------------------------------------------------------------------------------
if [[ -z "$PORTS" ]]; then
    ws="$(env_get XIAOZHI_WS_PORT || true)"
    ota="$(env_get XIAOZHI_OTA_PORT || true)"
    PORTS="${ws:-8100} ${ota:-8003}"
fi
read -r -a PORT_LIST <<<"$PORTS"
((${#PORT_LIST[@]} > 0)) || die "no ports given"
for p in "${PORT_LIST[@]}"; do
    if ! [[ "$p" =~ ^[0-9]+$ ]] || ((p < 1 || p > 65535)); then
        die "invalid port: $p"
    fi
done

if [[ "$MODE" == "install" && -z "$SUBNET" ]]; then
    lan_ip="$(env_get LAN_IP || true)"
    if [[ -n "$lan_ip" ]] && valid_ipv4 "$lan_ip"; then
        IFS=. read -r o1 o2 o3 _ <<<"$lan_ip"
        SUBNET="$o1.$o2.$o3.0/24"
    else
        die "no --subnet given and no valid LAN_IP in server/.env"
    fi
fi
if [[ -n "$SUBNET" ]]; then
    [[ "$SUBNET" == */* ]] || SUBNET="$SUBNET/32"
    prefix="${SUBNET#*/}"
    if ! valid_ipv4 "${SUBNET%/*}" || ! [[ "$prefix" =~ ^[0-9]+$ ]] || ((prefix > 32)); then
        die "invalid --subnet: $SUBNET (expected e.g. 192.168.1.0/24)"
    fi
fi

# ---- iptables helpers -------------------------------------------------------------------------
ipt() { run "${SUDO[@]}" iptables "$@"; }

# Read-only queries never prompt for a password (sudo -n): a dry-run must not need root.
if ((${#SUDO[@]} > 0)); then SUDO_N=(sudo -n); else SUDO_N=(); fi

# Current jarvis-lan rules as `iptables -S` lines (empty when not readable, e.g. dry-run as a user).
current_rules() {
    "${SUDO_N[@]}" iptables -S "$CHAIN" 2>/dev/null | grep -- "--comment $TAG" || true
}

remove_rules() {
    local line
    local -a args
    while IFS= read -r line; do
        [[ -n "$line" ]] || continue
        read -r -a args <<<"${line/#-A /-D }"
        ipt "${args[@]}"
    done < <(current_rules)
}

install_rules() {
    local p
    # -I inserts at the top: insert the DROP first, then the RETURN above it, per port.
    for p in "${PORT_LIST[@]}"; do
        ipt -I "$CHAIN" 1 -p tcp -m conntrack --ctstate DNAT --ctorigdstport "$p" --ctdir ORIGINAL \
            -m comment --comment "$TAG" -j DROP
        ipt -I "$CHAIN" 1 -p tcp -m conntrack --ctstate DNAT --ctorigdstport "$p" --ctdir ORIGINAL \
            -s "$SUBNET" -m comment --comment "$TAG" -j RETURN
    done
}

persist() {
    local self line quoted_line
    self="$SCRIPTS_DIR/firewall_lan.sh"
    line="@reboot sleep 60 && $(quote_cmd "$self" --apply --quiet --subnet "$SUBNET" --ports "${PORT_LIST[*]}") >>/var/log/jarvis-firewall.log 2>&1 # jarvis firewall_lan"
    quoted_line="$(printf '%q' "$line")"
    step "Persist (root crontab)"
    say "entry: $line"
    # Replace an older entry, keep every other line of root's crontab.
    run_sh "( ${SUDO[*]} crontab -l 2>/dev/null | grep -v 'jarvis firewall_lan'; printf '%s\n' $quoted_line ) | ${SUDO[*]} crontab -"
}

# ---- main -------------------------------------------------------------------------------------
if [[ "$MODE" == "status" ]]; then
    is_linux || die "iptables status is only available on the Linux host"
    rules="$("${SUDO[@]}" iptables -S "$CHAIN" 2>/dev/null | grep -- "--comment $TAG" || true)"
    if [[ -n "$rules" ]]; then
        printf '%s\n' "$rules"
    else
        info "no $TAG rules installed"
    fi
    exit 0
fi

[[ "$QUIET" == "1" ]] || dry_run_banner
if [[ "$DRY_RUN" == "0" ]]; then
    is_linux || die "--apply only works on the Linux host"
    require_cmd iptables
    # At boot (@reboot) dockerd may still be starting: wait for its chain.
    for _ in $(seq 1 60); do
        "${SUDO[@]}" iptables -S "$CHAIN" >/dev/null 2>&1 && break
        sleep 2
    done
    "${SUDO[@]}" iptables -S "$CHAIN" >/dev/null 2>&1 || die "chain $CHAIN not found (is dockerd running?)"
fi

case "$MODE" in
    remove)
        step "Remove $TAG rules from $CHAIN"
        if [[ "$DRY_RUN" == "1" ]]; then
            say "would delete every rule tagged '$TAG' (see --status)"
        fi
        remove_rules
        # Also drop the --persist @reboot entry, or the rules come back after the next reboot.
        if [[ "$DRY_RUN" == "1" ]] ||
            { "${SUDO[@]}" crontab -l 2>/dev/null || true; } | grep -q 'jarvis firewall_lan'; then
            run_sh "( ${SUDO[*]} crontab -l 2>/dev/null | grep -v 'jarvis firewall_lan' ) | ${SUDO[*]} crontab -"
        fi
        ;;
    install)
        step "Allow ${PORT_LIST[*]} from $SUBNET only ($CHAIN)"
        remove_rules
        install_rules
        if [[ "$PERSIST" == "1" ]]; then
            persist
        fi
        ;;
esac

if [[ "$DRY_RUN" == "0" && "$QUIET" == "0" ]]; then
    step "Installed rules"
    current_rules | sed 's/^/  /'
fi
