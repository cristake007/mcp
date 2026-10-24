# Private MCP Server — FastMCP 4.1

Small self-hosted MCP server for **administrator-managed user accounts** (no public signup).
No public registration, no GitHub/Google login, no ContextForge.

- FastMCP 4.1.0 / Streamable HTTP at `/mcp`
- First-party email/password login at `/login`
- Native FastMCP OAuth 2.1 flow (DCR, PKCE, metadata)
- PostgreSQL 18 storing Argon2id password hashes, OAuth clients, login transactions,
  metadata for one-use authorization codes and SHA-256 hashes of opaque tokens
  (raw OAuth credentials are not persisted in token/code JSONB)
- Single MCP container + one PostgreSQL container
- Nginx HTTPS reverse proxy on the existing host, **not** a third container
- Two example tools: `ping` and `whoami`

## 1. Configure

Use a Linux host with Docker Compose and an existing HTTPS Nginx virtual host.
The public origin in this example is `https://mcp.cursurituv.ro`.

```bash
git clone https://github.com/cristake007/mcp.git
cd mcp
cp .env.example .env
chmod 600 .env
```

Generate two different secrets using OpenSSL (run these commands separately):

```bash
openssl rand -hex 32
openssl rand -hex 32
```

Open `.env` in a text editor (for example, `nano .env`) and set:

```dotenv
PUBLIC_BASE_URL=https://mcp.cursurituv.ro
POSTGRES_PASSWORD=<paste the first generated secret here>
LOGIN_COOKIE_KEY=<paste the second generated secret here>
```

Replace the placeholders (including angle brackets) with the generated values.
The two secrets must be different; never commit the real `.env` to Git.
`PUBLIC_BASE_URL` must exactly match your public HTTPS origin, without `/mcp`
or a trailing slash. Save the file before starting Docker Compose.

Do **not** put actual user passwords or email addresses in `.env` or Git.

## 2. Start

```bash
docker compose config -q
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:4444/healthz
```

Expected response: `{"status":"ok"}`.

Nginx on the host should proxy **all** paths of the MCP hostname to
`http://127.0.0.1:4444`, not just `/mcp`. The OAuth discovery, `/register`,
`/authorize`, `/token`, and `/login` paths are also required.
For step-by-step VPS deployment (Docker, DNS, Nginx, Certbot, OAuth), see [`webserver/README.md`](webserver/README.md) and the [Nginx vhost example](webserver/nginx.vhost.example). Shared cPanel hosting is not supported.

## 3. Add authorized accounts

Run each command interactively for every person you want to authorize, supplying an email and a strong password
(at least 12 characters). The CLI prompts for the password without echoing it.

```bash
docker compose exec mcp python -m app.manage add-user coleg1@firma.ro
docker compose exec mcp python -m app.manage add-user coleg2@firma.ro
docker compose exec mcp python -m app.manage list-users
```

Use **your colleagues' real email addresses** instead of the examples. Only an administrator with server access can create or reactivate accounts; knowledge of the MCP URL is not enough to connect.

To change a password (revokes that user's existing tokens), run `add-user`
again using the same email address. To deactivate immediately:

```bash
docker compose exec mcp python -m app.manage disable-user coleg2@firma.ro
```

Disabled users can be re-enabled with `add-user`, using the same email.
There is no hardcoded two-account limit.

### Restrict OAuth clients before handling sensitive data

The server intentionally supports OAuth Dynamic Client Registration (used by MCP
clients). User accounts are still **local**: merely knowing the MCP URL never
grants access. However, an attacker could attempt to register a malicious
client and ask an employee to approve it. For a private deployment, restrict
callback hosts after confirming which clients are genuinely in use.

After your first successful ChatGPT connection, list the *registered callback
URLs* (admin-only; do not publish these details):

```bash
docker compose exec mcp python -m app.manage list-clients
```

In `.env`, set `OAUTH_ALLOWED_REDIRECT_HOSTS` to a comma-separated list of
**exact hostnames** from trusted clients; do not enter full URLs, paths,
wildcards, ports, or example values. Include loopback `127.0.0.1` only if
your desktop client genuinely uses it. Example only:

```dotenv
OAUTH_ALLOWED_REDIRECT_HOSTS=chatgpt.com,127.0.0.1
```

`chatgpt.com` is **not** guaranteed to be the actual callback host:
use `list-clients`, not guesses. Blank/unset means compatibility mode with
any valid HTTPS (or loopback HTTP) callback and is **not production-hardened**.
Once configured, run `docker compose up -d` to apply the setting and test
a **new** ChatGPT authorization. Existing OAuth tokens are not automatically
revoked just because the allowed hosts change.

For a deliberate, one-time credential cutover that logs out **everybody**:

```bash
docker compose exec mcp python -m app.manage revoke-all-sessions --yes-revoke-everyone
```

Run that command only after warning colleagues they must reconnect. The login
form also displays the actual callback **hostname** as an anti-impersonation
measure; displayed client names alone are not proof of client identity.

## 4. Connect ChatGPT

After HTTPS is working:

1. In ChatGPT, create or configure a custom remote MCP app/connector.
2. URL: `https://mcp.cursurituv.ro/mcp`.
3. Use the **OAuth** connection option. ChatGPT should open `/login`.
4. Authenticate with an administrator-managed local account.
5. Approve the connection by submitting the login form.
6. Try `ping` and `whoami`.

Each ChatGPT account authenticates separately. Your MCP login email does **not**
need to match the ChatGPT account email.

Test OAuth discovery (a healthy 200 JSON response):

```bash
curl -i https://mcp.cursurituv.ro/.well-known/oauth-authorization-server
curl -i https://mcp.cursurituv.ro/.well-known/oauth-protected-resource/mcp
```

If login isn't shown, check Nginx routing, valid TLS, Cloudflare rules
(no browser-only challenge on protocol endpoints), and the OAuth metadata.
The ChatGPT account must independently support custom MCP connectors/apps.

## 5. Operations

For an existing Certbot HTTPS site, install the rate-limit and safe access-log
snippets **without overwriting its vhost**:

```bash
sudo bash ops/prepare-nginx.sh
```

Then add the single HTTPS server-block include described in
[`webserver/README.md`](webserver/README.md), run `sudo nginx -t`, and
reload Nginx. Snippet preparation alone is not activation.

After confirming an initial manual database backup, install persistent daily
systemd timers for database backups and expired OAuth cleanup:

```bash
sudo bash ops/install-maintenance.sh
sudo systemctl start mcp-db-backup.service
sudo systemctl list-timers 'mcp-*' --all
```

Check the newest backup by fully restoring it in a disposable PostgreSQL 18
container without touching the live database:

```bash
sudo bash ops/verify-restore.sh
```

Read the complete instructions and optional encrypted offsite backups in
[`ops/README.md`](ops/README.md). Routine logs can be inspected with:

```bash
docker compose logs --tail=100 mcp
sudo journalctl -u mcp-db-backup.service -u mcp-oauth-cleanup.service -n 50 --no-pager
```

**Do not** run `docker compose down -v` unless you deliberately want to
delete all database contents. Database volumes persist across normal rebuilds.
The app binds port 4444 to host loopback; PostgreSQL stays on the internal
Docker network.

## OAuth / HTTP request hardening

The private OAuth provider now requires the canonical resource indicator
(https://YOUR-MCP-HOST/mcp) on both /authorize and /token
(including refresh grants). The bound audience is persisted on new access
and refresh tokens; a token with a different stored audience is rejected.
Old opaque tokens issued for this single resource continue only through
their remaining expiry.

FastMCP 4.1's native Host/Origin guard is enabled for all HTTP routes.
The public MCP hostname is allowed, along with container-local loopback for
Docker health checks. Browser requests with an unrelated Origin or Host are
rejected before authorization. Nginx must continue passing the original
Host header (proxy_set_header Host $host).

After updating, verify the public health check and a NEW ChatGPT OAuth
connection (the latter tests authorization and token exchange, not only DB):

```bash
curl -fsS https://YOUR-MCP-HOST/healthz
# Local tests, from the VPS:
curl -i -H 'Host: evil.example' http://127.0.0.1:4444/healthz
curl -i -H 'Origin: https://evil.example' -H 'Host: YOUR-MCP-HOST' http://127.0.0.1:4444/healthz
```

Expected: 421 for Host mismatch and 403 for Origin mismatch.
Existing ChatGPT refresh sessions should continue if the client sends
the standard resource parameter; clients omitting it need to reconnect
with a specification-compliant OAuth flow. CI covers PKCE, resource matching,
audience rejection, refresh and HTTP guards.

## Optional standalone RAG

An **independent, opt-in** document indexing project lives in [rag/](rag/README.md).
Its Docker Compose project, PostgreSQL database, dependencies and files are
separate. Root `docker compose up -d` starts **only MCP**, never RAG.
An **optional** bridge can expose `search_docs` and `get_text` when explicitly\nenabled with `compose.rag.yaml` and `rag/compose.mcp.yaml`. Normal MCP\nstartup contains neither integration nor additional RAG dependencies.\nSee [RAG instructions](rag/README.md#optional-mcp-connection-disabled-by-default).

## Security / readiness

This is **not yet an audited production deployment**, despite the added backup
and credential-storage hardening. Do not place sensitive data or high-privilege
tools behind it before end-to-end OAuth testing and security review. The critical OAuth protocol
routes and PKCE checks are delegated to FastMCP/MCP SDK; this application
implements custom login, persistent tokens, limited-rate password verification,
and token revocation.

Production hardening before wider deployment: independent penetration test,
monitoring, backups with restore tests, regular updates, and stricter client
registration policy if other MCP clients are not wanted.
