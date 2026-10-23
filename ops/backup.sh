#!/usr/bin/env bash
# Consistent PostgreSQL snapshot for MCP (without stopping the application).
set -Eeuo pipefail
umask 077

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${MCP_BACKUP_DIR:-/var/backups/mcp}"
RETENTION_DAYS="${MCP_BACKUP_RETENTION_DAYS:-14}"

if [[ ! "$RETENTION_DAYS" =~ ^[0-9]+$ ]]; then
  echo "MCP_BACKUP_RETENTION_DAYS must be a nonnegative integer" >&2
  exit 2
fi
if [[ ! -f "$PROJECT_DIR/compose.yaml" ]]; then
  echo "Missing compose.yaml in $PROJECT_DIR" >&2
  exit 2
fi

install -d -m 0700 "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$BACKUP_DIR/mcp-$STAMP-$$.dump"
TMP="$(mktemp "$BACKUP_DIR/.mcp-$STAMP.XXXXXXXX.tmp")"
trap 'rm -f -- "$TMP"' EXIT

# pg_dump -Fc creates a consistent custom-format snapshot of the live database.
# docker compose exec -T avoids allocating a TTY, keeping the binary stream clean.
docker compose --project-directory "$PROJECT_DIR" -f "$PROJECT_DIR/compose.yaml" \
  exec -T postgres pg_dump -U mcp -d mcp -Fc > "$TMP"

if [[ ! -s "$TMP" ]]; then
  echo "Backup failed: empty PostgreSQL dump" >&2
  exit 1
fi

# Verify that pg_restore recognizes the archive before promoting it.
docker compose --project-directory "$PROJECT_DIR" -f "$PROJECT_DIR/compose.yaml" \
  exec -T postgres pg_restore --list < "$TMP" > /dev/null

chmod 0600 "$TMP"
mv -- "$TMP" "$DEST"
trap - EXIT
sha256sum "$DEST" > "$DEST.sha256"
chmod 0600 "$DEST.sha256"
echo "Backup created: $DEST"

# Retention is evaluated only after a successful dump; excludes other files.
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'mcp-*.dump' \
  -mtime +"$RETENTION_DAYS" -print -delete
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'mcp-*.dump.sha256' \
  -mtime +"$RETENTION_DAYS" -print -delete
