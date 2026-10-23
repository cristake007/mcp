#!/usr/bin/env bash
# Encrypted offsite copy of local MCP PostgreSQL backups using restic.
# Requires a separately initialized RESTIC_REPOSITORY and a root-only config.
set -Eeuo pipefail
umask 077

CONF="${MCP_OFFSITE_CONFIG:-/etc/mcp-offsite.env}"
if [[ ! -f "$CONF" ]]; then
  echo "Missing $CONF (configure restic repository and password file first)" >&2
  exit 2
fi
if [[ "$(stat -c '%u' "$CONF")" != "0" ]]; then
  echo "$CONF must be owned by root" >&2
  exit 2
fi
# Root-controlled file: may include RESTIC_REPOSITORY, RESTIC_PASSWORD_FILE,
# RESTIC_SFTP_COMMAND, MCP_BACKUP_DIR. Treat this file as executable configuration.
set -a
# shellcheck disable=SC1090
source "$CONF"
set +a

: "${RESTIC_REPOSITORY:?Set RESTIC_REPOSITORY in $CONF}"
: "${RESTIC_PASSWORD_FILE:?Set RESTIC_PASSWORD_FILE in $CONF}"
BACKUP_DIR="${MCP_BACKUP_DIR:-/var/backups/mcp}"
[[ -f "$RESTIC_PASSWORD_FILE" ]] || { echo "Missing restic password file" >&2; exit 2; }
[[ -d "$BACKUP_DIR" ]] || { echo "Missing local backup directory" >&2; exit 2; }
command -v restic >/dev/null || { echo "Install restic first" >&2; exit 2; }

shopt -s nullglob
dumps=("$BACKUP_DIR"/mcp-*.dump)
(( ${#dumps[@]} > 0 )) || { echo "No PostgreSQL backups found" >&2; exit 1; }
# Validate the newest local archive's checksum before uploading.
latest="$(printf '%s\n' "${dumps[@]}" | sort | tail -n 1)"
[[ -f "$latest.sha256" ]] || { echo "Missing checksum for $latest" >&2; exit 1; }
(cd "$BACKUP_DIR" && sha256sum --check --status "$(basename "$latest").sha256")

# Restic encrypts the backup before it leaves the VPS. No plaintext emails,
# password hashes or tokens should be sent to an unencrypted remote target.
restic backup "$BACKUP_DIR" --tag mcp-postgres
echo "Encrypted offsite snapshot completed"
