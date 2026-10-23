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
0 3 * * * root /opt/mcp/ops/backup.sh >> /var/log/mcp-backup.log 2>&1
```

Then:

```bash
sudo chmod 0644 /etc/cron.d/mcp-backup
sudo /opt/mcp/ops/backup.sh
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
