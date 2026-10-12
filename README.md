# ContextForge deployment — mcp.cursurituv.ro

A minimal self-hosted deployment of [IBM ContextForge](https://github.com/IBM/mcp-context-forge), based on the **official upstream container image**. No previous custom MCP code is used and no ContextForge application modules are copied or maintained locally.

## Components
- ContextForge gateway and built-in administration UI (official image)
- PostgreSQL 18, private container network, persistent volume
- Redis, private container network, persistent volume
- Existing **host Nginx** terminates HTTPS and proxies to `127.0.0.1:4444`
- Separate one-shot ContextForge database/bootstrap migration container

## Before deployment
1. Create DNS record for `mcp.cursurituv.ro` and configure the already-installed host Nginx with valid TLS.
2. Copy `.env.example` to `.env`, pin a verified upstream image release, and generate strong, **distinct** values for every secret. Do not commit `.env`.
3. Verify the image's release documentation and environment compatibility. The stack has not yet been deployed or integration-tested.
4. Review the administration UI security for internet-facing use. Consider restricting `/admin` by IP/VPN.

## Commands
```sh
cp .env.example .env
# edit .env and generate secrets
docker compose config
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail=100 gateway
curl -fsS http://127.0.0.1:4444/health
```

## Host Nginx
Use `nginx/mcp.cursurituv.ro.conf.example` as a *reference location block*, integrate it into your existing HTTPS virtual host. Do not activate an HTTP-only public deployment.

## Tools
This repository intentionally contains **no preinstalled business MCP tools**. Register only the tools needed in the ContextForge UI, then assign them to a virtual server and enforce least-privilege access. An empty tools registry is a valid starting point.

## Provenance and license
Deployment structure reviewed against IBM/mcp-context-forge `main` on 2026-10-12. Runtime code stays upstream in the official image. ContextForge is Apache-2.0 licensed; see `LICENSE`. Track upstream releases and validate updates in staging before changing the image pin.

## Important
This is an initial configuration **not yet deployed or verified against a live release**. No existing host Nginx or Docker services have been changed.
