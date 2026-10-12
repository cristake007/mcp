# ContextForge MCP — mcp.cursurituv.ro

Independent source-based deployment of IBM ContextForge (upstream source pinned at `520a7b98712f066075d6a704624fbb3dee76a69c`). Python gateway, built admin UI, upstream database migrations, PostgreSQL 18, Redis. No Rust, Ansible, Helm or bundled Nginx.

## Deploy on a Linux host with Docker Compose and existing Nginx

```sh
git clone https://github.com/cristake007/mcp.git
cd mcp
python3 scripts/generate_deployment_env.py
nano .env # Set PLATFORM_ADMIN_EMAIL to an actual mailbox
docker compose config -q
docker compose build gateway
docker compose up -d
docker compose ps
docker compose logs --tail=100 gateway migration
curl -fsS http://127.0.0.1:4444/health
```

The startup sequence runs `migration` once, then `gateway`. The application port is bound only to host loopback `127.0.0.1:4444`. PostgreSQL and Redis are Docker-private and use persistent volumes. No Docker database port is published.

Do **not** commit `.env`; the secret generator refuses to overwrite an existing file. Use strong independent credentials, rotate any exposed secrets, and back up PostgreSQL externally. On future releases, review migrations before `docker compose up`.

## Host Nginx and TLS

Use the existing HTTPS virtual host for `mcp.cursurituv.ro`, and proxy requests to `http://127.0.0.1:4444`. See `nginx/mcp.cursurituv.ro.conf.example` for the proxy location snippet. TLS certificates stay on the **host**, not in Docker. Cloudflare Origin CA wildcard certificates require Cloudflare proxying for browser trust; public-trust wildcard certificates also work without Cloudflare.

Restrict admin access as appropriate. Configure public DNS and TLS before enabling internet access. No Certbot or additional Nginx container is installed by this deployment.

## Tools

There are no pre-enabled external business tools. Configure only the needed tools and virtual servers in ContextForge, with access permissions. See `examples/mcp-tools/` for isolated reference tools; examples are not automatically deployed.

## Build and security notes

`Dockerfile` builds the application and compiled admin assets from the source copied into this repository. `Containerfile.upstream` is only the original reference and is **not** used because it expects Rust source/build files that are intentionally absent. Build validation runs in GitHub Actions; check the latest `Validate local ContextForge image` run before deploying.

The copied runtime is subject to the upstream Apache-2.0 license (`LICENSE`). See `upstream-reference/` for selected upstream documentation. The source is vendored, not kept automatically in sync with IBM: updates need deliberate review and re-import.

**Production use requires a passing image build and actual deployment checks**, especially admin login, database persistence, OAuth client registration, tool authorization, TLS, backup and restore.
