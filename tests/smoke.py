"""End-to-end OAuth smoke test against a temporary PostgreSQL service in CI.

No external identity provider, server socket, or sensitive user emails required.
"""
import asyncio
import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

import httpx

from app.manage import set_user, disable_user
from app.server import app


async def main():
    await set_user("oauth-smoke@example.org", "A-Strong-Temporary-Passphrase-2026!")
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    redirect_uri = "http://127.0.0.1:8989/callback"
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://mcp.cursurituv.ro",
            follow_redirects=False,
            timeout=20,
        ) as client:
            metadata = await client.get("/.well-known/oauth-authorization-server")
            assert metadata.status_code == 200, (metadata.status_code, metadata.text)
            assert metadata.json()["token_endpoint"].endswith("/token")

            resource = await client.get("/.well-known/oauth-protected-resource/mcp")
            assert resource.status_code == 200, (resource.status_code, resource.text)
            assert resource.json().get("authorization_servers"), resource.text
            assert resource.json().get("resource", "").endswith("/mcp"), resource.text

            registration = await client.post("/register", json={
                "client_name": "MCP OAuth smoke test",
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            })
            assert registration.status_code in (200, 201), (registration.status_code, registration.text)
            client_id = registration.json()["client_id"]

            authorization = await client.get("/authorize", params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": "smoke-csrf",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            })
            assert authorization.status_code in (302, 303, 307), (authorization.status_code, authorization.text)
            login_url = authorization.headers["location"]
            assert "/login?" in login_url, login_url

            page = await client.get(login_url)
            assert page.status_code == 200
            ticket = parse_qs(urlparse(login_url).query)["ticket"][0]
            signed_in = await client.post("/login", data={
                "ticket": ticket,
                "email": "oauth-smoke@example.org",
                "password": "A-Strong-Temporary-Passphrase-2026!",
            })
            assert signed_in.status_code in (302, 303, 307), (signed_in.status_code, signed_in.text)
            redirected = signed_in.headers["location"]
            assert redirected.startswith(redirect_uri + "?"), redirected
            params = parse_qs(urlparse(redirected).query)
            assert params["state"][0] == "smoke-csrf"
            code = params["code"][0]

            token = await client.post("/token", data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            })
            assert token.status_code == 200, (token.status_code, token.text)
            credentials = token.json()
            assert credentials["access_token"] and credentials["refresh_token"]

            repeated = await client.post("/token", data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            })
            assert repeated.status_code != 200, "Authorization code should be one-time"

            headers = {
                "Authorization": "Bearer " + credentials["access_token"],
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            }
            rpc = await client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "ci-smoke", "version": "1.0"},
                },
            })
            assert rpc.status_code == 200, (rpc.status_code, rpc.text)
            assert "result" in rpc.json(), rpc.text

            refresh = await client.post("/token", data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": credentials["refresh_token"],
            })
            assert refresh.status_code == 200, (refresh.status_code, refresh.text)
            assert refresh.json()["access_token"] != credentials["access_token"]

            await disable_user("oauth-smoke@example.org")
            rejected = await client.post("/mcp", headers={
                **headers, "Authorization": "Bearer " + refresh.json()["access_token"]
            }, json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
            })
            assert rejected.status_code in (401, 403), (rejected.status_code, rejected.text)

    print("PASS: OAuth discovery, DCR, PKCE, login, token issuance, one-use code, MCP, refresh, revocation")


if __name__ == "__main__":
    asyncio.run(main())
