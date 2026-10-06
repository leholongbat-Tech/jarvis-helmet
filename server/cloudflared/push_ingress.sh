#!/usr/bin/env bash
# Push the public-hostname routes of the remote-managed tunnel (cloudflared/ingress.json) through
# the Cloudflare API, so the routes are kept in git and not only in the dashboard. Dry-run by
# default: renders and validates the file and prints the request (token masked).
#
# Usage: server/cloudflared/push_ingress.sh [--apply] [--show] [--dns] [--file FILE]
#
# Environment (export it in your shell for this command only; never write it into a repo file):
#   CF_API_TOKEN     API token: "Account > Cloudflare Tunnel > Edit" (+ "Zone > DNS > Edit" for --dns)
#   CF_ACCOUNT_ID    Cloudflare account id
#   TUNNEL_ID        tunnel UUID (Networking > Tunnels > your tunnel)
#   JARVIS_HOSTNAME  public hostname, e.g. jarvis.example.com (replaces ${JARVIS_HOSTNAME})
#   CF_ZONE_ID       zone id of the domain (only for --dns)
#
#   --apply    PUT the configuration to /accounts/$CF_ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/configurations
#              (cloudflared picks it up within seconds: log line "Updated to new configuration")
#   --show     GET the configuration the tunnel has now (read-only) and exit
#   --dns      also create the proxied CNAME JARVIS_HOSTNAME -> TUNNEL_ID.cfargotunnel.com
#              (the dashboard creates it by itself when you add the route there)
#   --file F   routes file (default: ingress.json next to this script)
set -euo pipefail
# shellcheck source=../scripts/_common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)/_common.sh"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILE="$HERE/ingress.json"
SHOW=0
DNS=0
API="${CF_API_BASE:-https://api.cloudflare.com/client/v4}"

usage() { print_header "${BASH_SOURCE[0]}"; }

while (($#)); do
    case "$1" in
        --apply) DRY_RUN=0; shift ;;
        --show) SHOW=1; shift ;;
        --dns) DNS=1; shift ;;
        --file) FILE="${2:?--file needs a value}"; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
done

need_var() {
    [[ -n "${!1:-}" ]] || die "environment variable $1 is not set (see --help)"
}

# ---- parameters -------------------------------------------------------------------------------
need_var JARVIS_HOSTNAME
need_var CF_ACCOUNT_ID
need_var TUNNEL_ID
re_host='^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$'
re_hex32='^[0-9a-f]{32}$'
re_uuid='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
[[ "$JARVIS_HOSTNAME" =~ $re_host ]] || die "JARVIS_HOSTNAME must be a lower-case host name like jarvis.example.com"
[[ "$CF_ACCOUNT_ID" =~ $re_hex32 ]] || die "CF_ACCOUNT_ID must be the 32-character hex account id"
[[ "$TUNNEL_ID" =~ $re_uuid ]] || die "TUNNEL_ID must be the tunnel UUID"
if [[ "$DNS" == "1" ]]; then
    need_var CF_ZONE_ID
    [[ "$CF_ZONE_ID" =~ $re_hex32 ]] || die "CF_ZONE_ID must be the 32-character hex zone id"
fi
URL="$API/accounts/$CF_ACCOUNT_ID/cfd_tunnel/$TUNNEL_ID/configurations"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
chmod 700 "$TMP"

# The token goes to curl through a 0600 header file, never on the command line (ps).
auth_header() {
    need_var CF_API_TOKEN
    printf 'Authorization: Bearer %s\n' "$CF_API_TOKEN" >"$TMP/auth"
    chmod 600 "$TMP/auth"
}

PY=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import json' >/dev/null 2>&1; then
        PY="$cand"
        break
    fi
done

# api_ok FILE: true when the Cloudflare API answer in FILE says "success": true.
api_ok() {
    if [[ -n "$PY" ]]; then
        "$PY" -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); sys.exit(0 if d.get("success") is True else 1)' "$1"
    else
        grep -Eq '"success"[[:space:]]*:[[:space:]]*true' "$1"
    fi
}

if [[ "$SHOW" == "1" ]]; then
    auth_header
    curl -sS --fail-with-body "$URL" -H @"$TMP/auth" -o "$TMP/show.json" || die "GET failed: $(cat "$TMP/show.json" 2>/dev/null)"
    if [[ -n "$PY" ]]; then
        "$PY" -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); print(json.dumps((d.get("result") or {}).get("config"), indent=2))' "$TMP/show.json"
    else
        cat "$TMP/show.json"
    fi
    exit 0
fi

# ---- render + validate ------------------------------------------------------------------------
[[ -f "$FILE" ]] || die "routes file not found: $FILE"
# The literal placeholder ${JARVIS_HOSTNAME} in the file is replaced (single quotes on purpose).
# shellcheck disable=SC2016
sed 's/\${JARVIS_HOSTNAME}/'"$JARVIS_HOSTNAME"'/g' "$FILE" >"$TMP/ingress.json"
# shellcheck disable=SC2016
if grep -q '\${' "$TMP/ingress.json"; then
    die "unreplaced placeholder left in $FILE"
fi
if [[ -n "$PY" ]]; then
    "$PY" - "$TMP/ingress.json" "$JARVIS_HOSTNAME" <<'PYEOF' || die "invalid routes file: $FILE"
import json, re, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
host = sys.argv[2]
rules = data["config"]["ingress"]
assert isinstance(rules, list) and rules, "config.ingress must be a non-empty list"
last = rules[-1]
assert "hostname" not in last and "path" not in last, "the last rule must be a catch-all (no hostname/path)"
for i, rule in enumerate(rules):
    assert isinstance(rule.get("service"), str) and rule["service"], f"rule {i + 1}: service missing"
    if "hostname" in rule:
        assert rule["hostname"] == host, f"rule {i + 1}: hostname {rule['hostname']!r} != {host!r}"
    if "path" in rule:
        re.compile(rule["path"])
        assert rule["path"].startswith("^"), f"rule {i + 1}: anchor the path regex with ^"
print(f"  [ok] {len(rules)} rules for {host}")
PYEOF
else
    warn "python3 not found: routes file not validated"
fi

dry_run_banner
step "Tunnel configuration for $JARVIS_HOSTNAME"
sed 's/^/    /' "$TMP/ingress.json"
masked=(curl -sS --fail-with-body -X PUT "$URL" -H "Authorization: Bearer ****" -H "Content-Type: application/json" --data @ingress.json)
if [[ "$DRY_RUN" == "1" ]]; then
    printf '  [dry-run] %s\n' "$(quote_cmd "${masked[@]}")"
else
    auth_header
    printf '  + %s\n' "$(quote_cmd "${masked[@]}")"
    if ! curl -sS --fail-with-body -X PUT "$URL" -H @"$TMP/auth" -H "Content-Type: application/json" \
        --data @"$TMP/ingress.json" -o "$TMP/put.json" || ! api_ok "$TMP/put.json"; then
        die "Cloudflare API refused the configuration: $(cat "$TMP/put.json" 2>/dev/null)"
    fi
    ok "tunnel configuration updated (check: docker compose logs cloudflared | grep 'Updated to new configuration')"
fi

if [[ "$DNS" == "1" ]]; then
    step "DNS record $JARVIS_HOSTNAME -> $TUNNEL_ID.cfargotunnel.com (proxied)"
    printf '{"type":"CNAME","proxied":true,"name":"%s","content":"%s.cfargotunnel.com","comment":"Jarvis Helmet tunnel"}\n' \
        "$JARVIS_HOSTNAME" "$TUNNEL_ID" >"$TMP/dns.json"
    dns_url="$API/zones/$CF_ZONE_ID/dns_records"
    masked=(curl -sS --fail-with-body -X POST "$dns_url" -H "Authorization: Bearer ****" -H "Content-Type: application/json" --data "$(cat "$TMP/dns.json")")
    if [[ "$DRY_RUN" == "1" ]]; then
        printf '  [dry-run] %s\n' "$(quote_cmd "${masked[@]}")"
    else
        auth_header
        if ! curl -sS --fail-with-body -X POST "$dns_url" -H @"$TMP/auth" -H "Content-Type: application/json" \
            --data @"$TMP/dns.json" -o "$TMP/dns_out.json" || ! api_ok "$TMP/dns_out.json"; then
            die "DNS record not created (it may exist already): $(cat "$TMP/dns_out.json" 2>/dev/null)"
        fi
        ok "DNS record created"
    fi
fi
