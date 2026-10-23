# PostgreSQL backup and restore (VPS)

This procedure covers the two-container deployment in `compose.yaml` on
Ubuntu/Debian. It uses `pg_dump -Fc` to make a consistent snapshot without
stopping the running MCP server. It is **not** a full disk / point-in-time
recovery solution.

## One-time setup

From the cloned repository directory:

```bash
sudo install -d -m 0700 /var/backups/mcp
sudo chmod +x ops/backup.sh
sudo ./ops/backup.sh
sudo ls -lh /var/backups/mcp/
```

The script requires Docker Compose v2, access to the Docker daemon (run as
root or a trusted Docker administrator), and the `postgres` container running.

It produces private (`0600`) `.dump` snapshots and `.sha256` checksum
files under `/var/backups/mcp`. It validates the archive header/TOC using
`pg_restore --list`, but **that is not a full restore test**.

Do **not** store database backups under a public web directory or inside Git.

## Daily scheduling on the VPS

Get your actual repository path:

```bash
pwd
```

Create a root cron file; replace `/opt/mcp` with that absolute repository
path, and do not use `~` in cron:

```bash
sudo nano /etc/cron.d/mcp-backup
```

Example contents (runs each day at 03:00 in the host's timezone):

```cron
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 3 * * * root /bin/bash /opt/mcp/ops/backup.sh >> /var/log/mcp-backup.log 2>&1
0 4 * * * root /usr/bin/docker compose --project-directory /opt/mcp -f /opt/mcp/compose.yaml exec -T mcp python -m app.manage cleanup >> /var/log/mcp-maintenance.log 2>&1
```

Then:

```bash
sudo chmod 0644 /etc/cron.d/mcp-backup
sudo bash /opt/mcp/ops/backup.sh
sudo tail -n 20 /var/log/mcp-backup.log
```

You can change the destination with `MCP_BACKUP_DIR` and local retention
with `MCP_BACKUP_RETENTION_DAYS` (default 14 days), set in the cron entry
or execution environment. The script only removes matching older backup files.

**A backup on the same VPS is not sufficient for disaster recovery.**
Copy backups to a different host/storage with encryption at rest and
restricted access; configure failure monitoring. If the VPS is destroyed,
same-host backups are lost. Keep the `.env` secrets in a **separate, encrypted**
off-server recovery store; never commit them to Git or include passwords in
support tickets.

## Optional encrypted offsite backup (strongly recommended)

The local snapshot is **not** protected against VPS loss, disk failure or host
compromise. For production, use a second storage location (another Linux
host, an SFTP backup service, or an existing restic-compatible encrypted
repository). No external **authentication provider** is required by MCP:
offsite storage is a separate operations concern.

Install restic and prepare a root-only encryption password:

```bash
sudo apt install -y restic
sudo install -d -m 0700 /root/.config/mcp
sudo sh -c 'umask 077; openssl rand -base64 48 > /root/.config/mcp/restic-password'
sudo install -m 0600 /dev/null /etc/mcp-offsite.env
sudo nano /etc/mcp-offsite.env
```

Example `/etc/mcp-offsite.env` (replace destination with **your** backup
server and configured SSH account):

```bash
RESTIC_REPOSITORY=sftp:backupuser@backup.example.com:/backups/mcp
RESTIC_PASSWORD_FILE=/root/.config/mcp/restic-password
```

The remote SFTP account must have write access to the target directory;
configure root's SSH key authentication and **verify the remote SSH host key**
before running restic. Do not disable SSH host-key checking. Ensure the
configuration file is owned by root and mode `0600`.

Initialize that destination once:

```bash
sudo bash -c 'set -a; source /etc/mcp-offsite.env; set +a; restic init'
```

Run the encrypted upload after a successful local backup:

```bash
sudo bash ops/offsite-restic.sh
```

The script verifies the newest local archive checksum and uses restic's
built-in encryption. It does **not** delete remote snapshots; add a separately
reviewed restic retention policy when remote storage is selected.

After setup, you may add to `/etc/cron.d/mcp-backup` (backup at 03:00,
offsite upload at 03:30; edit `/opt/mcp` to the actual repository directory):

```cron
30 3 * * * root /bin/bash /opt/mcp/ops/offsite-restic.sh >> /var/log/mcp-offsite.log 2>&1
```

Check remote snapshots and repository integrity:

```bash
sudo bash -c 'set -a; source /etc/mcp-offsite.env; set +a; restic snapshots'
sudo bash -c 'set -a; source /etc/mcp-offsite.env; set +a; restic check'
```

**Test a restic restore to an isolated directory**, then separately test a
PostgreSQL restore to an isolated database. Keep the restic password and SSH
recovery key in a separate secure location; losing them can make remote
snapshots irrecoverable.

If backup or upload fails, the cron log must be checked and the failure
addressed. Set up monitoring/alerts appropriate to your VPS before claiming
unattended backup protection.

## Existing backups from pre-hardening releases

Earlier MCP releases stored usable OAuth tokens in PostgreSQL JSONB. A
backup created **before** the credential-scrubbing database migration may
therefore contain those raw credentials. Protect or securely retire old
backups. After updating and validating login, consider using the explicit
admin `revoke-all-sessions --yes-revoke-everyone` command to invalidate
previous sessions; this forces colleagues to reconnect ChatGPT. Do not
delete your only recovery copy before creating and testing a replacement.

## Verify backup integrity

```bash
cd /var/backups/mcp
sudo sha256sum -c mcp-YYYYMMDDTHHMMSSZ-NNN.dump.sha256
```

Replace the sample filename with a real file listed in the backup directory.

## Restore drill (on an isolated test instance)

**Never test restoration by overwriting the live production database.**
On an isolated installation of this project using PostgreSQL 18, with a
separately configured test environment and the backup copied locally:

```bash
# From the isolated project's repository directory:
docker compose up -d postgres
docker compose exec -T postgres createdb -U mcp mcp_restore_test
docker compose exec -T postgres pg_restore -U mcp -d mcp_restore_test \
  --exit-on-error --no-owner --no-acl < /path/to/mcp-BACKUP.dump
docker compose exec -T postgres psql -U mcp -d mcp_restore_test \
  -c 'SELECT count(*) FROM users;'
```

If `mcp_restore_test` already exists, use a fresh isolated database name
rather than replaying the dump on existing data. The presence of rows
does not alone establish full recovery; check the expected tables and
perform an application-level test in an isolated environment.

The dump includes credentials hashes, OAuth client and session data; protect
it accordingly. Use a distinct hostname and unique environment secrets on
the isolated restore host, and **do not expose the restored OAuth database
publicly**. After a real disaster, ensure the original hostname and secrets
are restored consistently, or reconnect clients as appropriate.

See the PostgreSQL 18 documentation on
[`pg_dump`](https://www.postgresql.org/docs/18/app-pgdump.html) and
[`pg_restore`](https://www.postgresql.org/docs/18/app-pgrestore.html).
