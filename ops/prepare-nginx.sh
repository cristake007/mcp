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
rollback_on_failure() {
  status=$?
  if (( status != 0 )); then
    echo "Installation failed; reverting Nginx snippets" >&2
    for ((i=0; i<${#destinations[@]}; i++)); do
      if (( had_original[i] )); then
        cp -a -- "$STAGE/$i" "${destinations[i]}" || true
      else
        rm -f -- "${destinations[i]}" || true
      fi
    done
  fi
  rm -rf -- "$STAGE"
}
declare -a destinations=()
declare -a had_original=()
trap rollback_on_failure EXIT

FILES=(
  "/etc/nginx/conf.d/mcp-security.conf|nginx-limits.conf.example"
  "/etc/nginx/snippets/mcp-proxy.inc|nginx-proxy.inc.example"
  "/etc/nginx/snippets/mcp-hardening.inc|nginx-hardening.inc.example"
)
install -d -m 0755 /etc/nginx/conf.d /etc/nginx/snippets
for item in "${FILES[@]}"; do
  dest="${item%%|*}"
  source="$SOURCE_DIR/${item#*|}"
  [[ -f "$source" ]] || { echo "Missing source $source" >&2; exit 1; }
  n="${#destinations[@]}"
  if [[ -e "$dest" ]]; then
    cp -a -- "$dest" "$STAGE/$n"
    had_original+=(1)
  else
    had_original+=(0)
  fi
  destinations+=("$dest")
  install -m 0644 "$source" "$dest"
done

if ! nginx -t; then
  echo "Nginx syntax validation failed" >&2
  exit 1
fi

echo "Nginx snippets installed and syntax verified."
echo "NOT YET ACTIVE on an existing HTTPS vhost: add this single line"
echo "inside your MCP hostname's existing HTTPS server {}:"
echo "    include /etc/nginx/snippets/mcp-hardening.inc;"
echo "Then run: sudo nginx -t && sudo systemctl reload nginx"
