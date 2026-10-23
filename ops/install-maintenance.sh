#!/usr/bin/env bash
# Install idempotent daily systemd timers for MCP backups and OAuth cleanup.
# This does not restart MCP, Nginx or PostgreSQL.
set -Eeuo pipefail

if (( EUID != 0 )); then
  echo "Run as root: sudo bash ops/install-maintenance.sh" >&2
  exit 1
fi
command -v systemctl >/dev/null || { echo "systemd is required" >&2; exit 1; }
command -v docker >/dev/null || { echo "Docker CLI is required" >&2; exit 1; }

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! "$PROJECT_DIR" =~ ^/[a-zA-Z0-9_./-]+$ ]]; then
  echo "Project path contains unsupported characters: $PROJECT_DIR" >&2
  exit 1
fi
[[ -f "$PROJECT_DIR/compose.yaml" && -f "$PROJECT_DIR/.env" ]] || {
  echo "Missing compose.yaml or .env in $PROJECT_DIR" >&2
  exit 1
}
DOCKER="$(command -v docker)"
mkdir -p /etc/systemd/system
for unit in mcp-db-backup.service mcp-db-backup.timer mcp-oauth-cleanup.service mcp-oauth-cleanup.timer; do
  dest="/etc/systemd/system/$unit"
  if [[ -f "$dest" ]] && ! grep -Fxq '# Managed by mcp/ops/install-maintenance.sh' "$dest"; then
    echo "Refusing to overwrite unmanaged unit: $dest" >&2
    exit 1
  fi
done

cat > /etc/systemd/system/mcp-db-backup.service <<EOF
# Managed by mcp/ops/install-maintenance.sh
[Unit]
Description=Private MCP PostgreSQL backup
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=$PROJECT_DIR
ExecStart=/bin/bash $PROJECT_DIR/ops/backup.sh
TimeoutStartSec=15min
UMask=0077
EOF

cat > /etc/systemd/system/mcp-db-backup.timer <<'EOF'
# Managed by mcp/ops/install-maintenance.sh
[Unit]
Description=Private MCP daily PostgreSQL backup schedule

[Timer]
OnCalendar=*-*-* 03:00:00
RandomizedDelaySec=10min
Persistent=true
Unit=mcp-db-backup.service

[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/mcp-oauth-cleanup.service <<EOF
# Managed by mcp/ops/install-maintenance.sh
[Unit]
Description=Private MCP purge expired OAuth state
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=$PROJECT_DIR
ExecStart=$DOCKER compose --project-directory $PROJECT_DIR -f $PROJECT_DIR/compose.yaml exec -T mcp python -m app.manage cleanup
TimeoutStartSec=5min
UMask=0077
EOF

cat > /etc/systemd/system/mcp-oauth-cleanup.timer <<'EOF'
# Managed by mcp/ops/install-maintenance.sh
[Unit]
Description=Private MCP daily expired OAuth state cleanup

[Timer]
OnCalendar=*-*-* 04:00:00
RandomizedDelaySec=10min
Persistent=true
Unit=mcp-oauth-cleanup.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now mcp-db-backup.timer mcp-oauth-cleanup.timer
systemctl list-timers --all mcp-db-backup.timer mcp-oauth-cleanup.timer
echo "Installed daily backup + OAuth cleanup timers."
echo "Manual check: systemctl start mcp-db-backup.service"
echo "Logs: journalctl -u mcp-db-backup.service -u mcp-oauth-cleanup.service -n 50"
echo "Do not enable duplicate /etc/cron.d/mcp-backup jobs."
