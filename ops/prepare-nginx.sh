#!/usr/bin/env bash
# Install Nginx security snippets WITHOUT editing Certbot-managed TLS vhosts.
# Safe to repeat; tests Nginx syntax and rolls back snippet changes on failure.
set -Eeuo pipefail
if (( EUID != 0 )); then
  echo "Run as root: sudo bash ops/prepare-nginx.sh" >&2
  exit 1
fi
command -v nginx >/dev/null || { echo "Nginx must be installed" >&2; exit 1; }
nginx -t >/dev/null 2>&1 || { echo "Existing Nginx config is invalid; not modifying" >&2; exit 1; }
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../webserver" && pwd)"
STAGE="$(mktemp -d /tmp/mcp-nginx.XXXXXXXX)"
trap 'rm -rf -- "$STAGE"' EXIT

FILES=(
  "/etc/nginx/conf.d/mcp-security.conf|nginx-limits.conf.example"
  "/etc/nginx/snippets/mcp-proxy.inc|nginx-proxy.inc.example"
  "/etc/nginx/snippets/mcp-hardening.inc|nginx-hardening.inc.example"
)
install -d -m 0755 /etc/nginx/conf.d /etc/nginx/snippets
declare -a destinations=()
declare -a had_original=()
for item in "${FILES[@]}"; do
  dest="${item%%|*}"
  source="$SOURCE_DIR/${item#*|}"
  [[ -f "$source" ]] || { echo "Missing source $source" >&2; exit 1; }
  n="${#destinations[@]}"
  destinations+=("$dest")
  if [[ -e "$dest" ]]; then
    cp -a -- "$dest" "$STAGE/$n"
    had_original+=(1)
  else
    had_original+=(0)
  fi
  install -m 0644 "$source" "$dest"
done

if ! nginx -t; then
  echo "Nginx validation failed; restoring earlier snippets" >&2
  for ((i=0; i<${#destinations[@]}; i++)); do
    if (( had_original[i] )); then
      cp -a -- "$STAGE/$i" "${destinations[i]}"
    else
      rm -f -- "${destinations[i]}"
    fi
  done
  nginx -t || true
  exit 1
fi

echo "Nginx snippets installed and syntax verified."
echo "NOT YET ACTIVE on an existing HTTPS vhost: add this single line"
echo "inside the HTTPS server {} for mcp.vreaudigitalizare.ro:"
echo "    include /etc/nginx/snippets/mcp-hardening.inc;"
echo "Then run: sudo nginx -t && sudo systemctl reload nginx"
