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

```bash
docker compose logs -f --tail=100 mcp
sudo ./ops/backup.sh
docker compose down
```

**Do not** run `docker compose down -v` unless you deliberately want to delete
all database contents. The new backup script is documented in
[`ops/README.md`](ops/README.md): schedule it daily, store an encrypted offsite
copy, and test restoration on an isolated host. Database volumes persist
across normal rebuilds.

The application only publishes port `4444` on host loopback; the database
is private to Docker. TLS terminates at host Nginx.

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
