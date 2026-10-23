#!/usr/bin/env bash
# Full pg_restore drill in a disposable, network-isolated PostgreSQL 18 container.
# Never modifies the live database, existing volumes, or running services.
set -Eeuo pipefail
umask 077

command -v docker >/dev/null || { echo "Docker required" >&2; exit 2; }
BACKUP_DIR="${MCP_BACKUP_DIR:-/var/backups/mcp}"
if (( $# == 0 )); then
  shopt -s nullglob
  archives=("$BACKUP_DIR"/mcp-*.dump)
  (( ${#archives[@]} > 0 )) || { echo "No backup found in $BACKUP_DIR" >&2; exit 1; }
  archive="$(printf '%s\n' "${archives[@]}" | sort | tail -n 1)"
elif (( $# == 1 )); then
  archive="$1"
else
  echo "Usage: sudo bash ops/verify-restore.sh [backup.dump]" >&2
  exit 2
fi
[[ -r "$archive" && -s "$archive" ]] || { echo "Missing/unreadable backup: $archive" >&2; exit 1; }
archive="$(realpath "$archive")"

if [[ -f "$archive.sha256" ]]; then
  (cd "$(dirname "$archive")" && sha256sum --check --status "$(basename "$archive").sha256") || {
    echo "Checksum verification failed: $archive" >&2
    exit 1
  }
else
  echo "Warning: checksum file not found, testing archive directly" >&2
fi

name="mcp-restore-verify-$$"
cleanup() { docker rm --force "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT
echo "Starting isolated PostgreSQL 18 restore test (network disabled)"
docker run -d --rm --network none --name "$name" \
  -e POSTGRES_HOST_AUTH_METHOD=trust postgres:18 >/dev/null

ready=0
for _ in $(seq 1 45); do
  if docker exec "$name" pg_isready -q -U postgres -d postgres >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
(( ready )) || { echo "Disposable PostgreSQL did not become ready" >&2; exit 1; }

docker cp "$archive" "$name:/tmp/mcp-restore.dump"
docker exec "$name" pg_restore --list /tmp/mcp-restore.dump >/dev/null
docker exec "$name" createdb -U postgres mcp_restore_check
docker exec "$name" pg_restore --exit-on-error --single-transaction \
  --no-owner --no-acl -U postgres -d mcp_restore_check \
  /tmp/mcp-restore.dump
docker exec "$name" psql -U postgres -d mcp_restore_check \
  -v ON_ERROR_STOP=1 -Atc \
  "SELECT count(*) FROM users;" >/dev/null
docker exec "$name" psql -U postgres -d mcp_restore_check \
  -v ON_ERROR_STOP=1 -Atc \
  "SELECT count(*) = 5 FROM information_schema.tables
   WHERE table_schema='public'
     AND table_name IN ('users','oauth_clients','oauth_pending','oauth_codes','oauth_tokens');" \
  | grep -Fxq t

echo "PASS: full archive restored and expected MCP tables verified in an isolated container."
echo "No production database, volumes or current MCP sessions were modified."
