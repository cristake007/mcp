# VPS webserver setup (Ubuntu / Debian)

This project is supported on a Linux VPS or dedicated server with root/sudo access.
**Shared cPanel hosting is not supported.** FastMCP runs as an ASGI service and the
current deployment uses Docker Compose and PostgreSQL. Do not attempt to run it
through a generic cPanel/Passenger WSGI setup.

Architecture: public HTTPS (Nginx on the host) -> `127.0.0.1:4444`
(Docker-published MCP app) -> private Docker network (PostgreSQL).

The examples use `mcp.example.com`. Replace it with your own hostname everywhere.
**OAuth discovery, /authorize, /token, /login and /mcp must share the same hostname.**

## 1. Prerequisites

- An Ubuntu 22.04/24.04 or currently supported Debian VPS, root/sudo access.
- Public IPv4 address and a DNS `A` record: `mcp.example.com -> VPS IPv4`.
  If the name has an `AAAA` record, its IPv6 must also route correctly; otherwise remove it.
- TCP ports **80 and 443** open in the host and provider firewalls; SSH open for administration.
- No other webserver listening on ports 80/443 (or adapt an existing Nginx vhost).
- Docker Engine + Compose v2, Git, Nginx and Certbot.

Verify DNS from your machine or VPS:

```bash
dig +short A mcp.example.com
dig +short AAAA mcp.example.com
```

If `dig` is unavailable: `sudo apt install dnsutils`. Do not request a
certificate before DNS is pointed at the correct server.

## 2. Install Docker Engine and Compose v2

If Docker Engine and `docker compose` already work, **skip this step**; don't
remove/reinstall a working Docker installation. For a fresh Ubuntu/Debian host,
use Docker's official apt repository:

```bash
sudo apt update
sudo apt install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
. /etc/os-release
# Supported values for this guide are ubuntu and debian.
curl -fsSL "https://download.docker.com/linux/$ID/gpg" | sudo tee /etc/apt/keyrings/docker.asc > /dev/null
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/$ID
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker compose version
```

If apt reports conflicting Docker packages, resolve these according to the
official Ubuntu/Debian Docker docs before continuing. Do not mix distribution
Docker packages with Docker's official packages.

## 3. Install Nginx + Certbot

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo systemctl enable --now nginx
sudo nginx -t
```

If using UFW, allow SSH **before** enabling UFW (and if it is already active,
add the HTTP/HTTPS rule):

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw status
```

Also allow inbound TCP 80/443 in any VPS-provider firewall.

## 4. Configure and start MCP

```bash
git clone https://github.com/cristake007/mcp.git
cd mcp
cp .env.example .env
chmod 600 .env
openssl rand -hex 32
openssl rand -hex 32
nano .env
```

Paste the **first** random value as `POSTGRES_PASSWORD` and the **second**
as `LOGIN_COOKIE_KEY` (different values). Set the public origin **without**
a trailing slash or `/mcp`:

```dotenv
PUBLIC_BASE_URL=https://mcp.example.com
POSTGRES_PASSWORD=REPLACE_WITH_FIRST_RANDOM_VALUE
LOGIN_COOKIE_KEY=REPLACE_WITH_SECOND_RANDOM_VALUE
```

Do not commit `.env`, passwords or personal account emails. Then:

```bash
sudo docker compose config -q
sudo docker compose up -d --build
sudo docker compose ps
curl -fsS http://127.0.0.1:4444/healthz
```

Expect `{"status":"ok"}`. If it fails, use
`sudo docker compose logs --tail=100 mcp` and
`sudo docker compose logs --tail=100 postgres`.

## 5. Install and enable the Nginx vhost

From the repository root:

```bash
sudo cp webserver/nginx.vhost.example /etc/nginx/sites-available/mcp.example.com
sudo nano /etc/nginx/sites-available/mcp.example.com
```

Replace **every** `mcp.example.com` with your real hostname. The vhost
proxies **all routes**, not only `/mcp`. If you already have a vhost for the
same hostname, merge the proxy `location /` into it instead of enabling
duplicate `server_name` blocks.

```bash
sudo ln -s /etc/nginx/sites-available/mcp.example.com /etc/nginx/sites-enabled/mcp.example.com
sudo nginx -t
sudo systemctl reload nginx
```

If you named the vhost file after your actual hostname, use that exact filename
in both commands above.

Test HTTP routing (before requesting HTTPS):

```bash
curl -i http://mcp.example.com/healthz
```

## 6. Issue the HTTPS certificate

```bash
sudo certbot --nginx -d mcp.example.com
sudo nginx -t
sudo certbot renew --dry-run
```

Certbot updates the Nginx vhost with TLS and can configure HTTP-to-HTTPS
redirection. Do not manually paste the pre-Certbot example over the resulting
HTTPS vhost later.

Verify the HTTPS health and OAuth discovery endpoints:

```bash
curl -fsS https://mcp.example.com/healthz
curl -i https://mcp.example.com/.well-known/oauth-authorization-server
curl -i https://mcp.example.com/.well-known/oauth-protected-resource/mcp
```

The health endpoint should say `{"status":"ok"}` and both discovery
endpoints should return successful JSON responses.

## 7. Create accounts and connect ChatGPT

Create a local account for each approved colleague; passwords are prompted interactively:

```bash
sudo docker compose exec mcp python -m app.manage add-user user1@example.com
sudo docker compose exec mcp python -m app.manage add-user user2@example.com
sudo docker compose exec mcp python -m app.manage list-users
```

Replace the sample emails with your own. In ChatGPT, configure a custom remote
MCP connector with `https://mcp.example.com/mcp` and OAuth authentication.
Log in through the self-hosted `/login` page, then test the `ping` and
`whoami` tools. The ChatGPT account must support custom MCP connectors.

## Operations and troubleshooting

```bash
sudo docker compose ps
sudo docker compose logs -f --tail=100 mcp
sudo systemctl status nginx
sudo nginx -t
sudo bash ops/backup.sh  # from the repo root; see ops/README.md
```

Run Docker Compose commands from the project root (where `compose.yaml` is).
Securely store backups: they contain password hashes and OAuth state. Test
restoration before relying on backups. To deploy an update, review changes
and run `git pull` followed by `sudo docker compose up -d --build`.

- **502 Bad Gateway:** MCP container not healthy / port 4444 not listening.
- **Certbot failure:** DNS, `AAAA` record, firewall ports, or duplicated Nginx vhost.
- **OAuth error:** incorrect `PUBLIC_BASE_URL`, invalid TLS, or proxying only
  `/mcp` rather than all paths.
- **Connection refused:** check `sudo docker compose ps` and host Nginx status.
- **Do not publish port 4444 or PostgreSQL 5432 on the public network.**
- **Do not use `docker compose down -v`** unless deliberately deleting database data.

This is a **test-oriented deployment guide**. Passing these steps does not
by itself establish that the application is production-hardened. Before using
sensitive data or high-privilege tools, review OAuth security, add monitoring,
backup-restore checks, and other production controls.

References: https://docs.docker.com/engine/install/ubuntu/ ;
https://docs.docker.com/engine/install/debian/ ;
https://certbot.eff.org/instructions?ws=nginx&os=ubuntufocal
