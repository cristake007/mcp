"""End-to-end OAuth smoke test against a temporary PostgreSQL service in CI.

No external identity provider, server socket, or sensitive user emails required.
"""
import asyncio
import base64
import hashlib
import os
import secrets
from urllib.parse import parse_qs, urlparse

import httpx

from app.manage import set_user, disable_user, enable_user, cleanup, list_clients
from app.db import connection, init_db
from app.oauth import digest, approved_redirect
from app.http_security import same_resource
from app.server import app, get_login_ip, allowed_client_origins
from starlette.requests import Request


async def main():
    assert same_resource("https://MCP.cursurituv.ro/mcp", "https://mcp.cursurituv.ro/mcp")
    assert not same_resource("https://evil.example/mcp", "https://mcp.cursurituv.ro/mcp")
    assert not same_resource("https://mcp.cursurituv.ro/mcp?anything=1", "https://mcp.cursurituv.ro/mcp")
    assert approved_redirect("https://chatgpt.com/callback")
    assert not approved_redirect("http://example.com/callback")
    assert not approved_redirect("https://good.example@evil.example/callback")
    old_redirect_allowlist = os.environ.get("OAUTH_ALLOWED_REDIRECT_HOSTS")
    os.environ["OAUTH_ALLOWED_REDIRECT_HOSTS"] = "chatgpt.com,127.0.0.1"
    assert approved_redirect("https://chatgpt.com/callback")
    assert approved_redirect("http://127.0.0.1:8989/callback")
    assert not approved_redirect("https://chatgpt.com.evil.com/callback")
    assert not approved_redirect("https://attacker.example/callback")
    if old_redirect_allowlist is None:
        os.environ.pop("OAUTH_ALLOWED_REDIRECT_HOSTS")
    else:
        os.environ["OAUTH_ALLOWED_REDIRECT_HOSTS"] = old_redirect_allowlist

    trusted_proxy_request = Request({
        "type": "http",
        "client": ("172.18.0.1", 12345),
        "headers": [(b"x-real-ip", b"203.0.113.42")],
    })
    assert get_login_ip(trusted_proxy_request) == "203.0.113.42"
    untrusted_peer_request = Request({
        "type": "http",
        "client": ("8.8.8.8", 12345),
        "headers": [(b"x-real-ip", b"1.2.3.4")],
    })
    assert get_login_ip(untrusted_peer_request) == "8.8.8.8"

    await set_user("oauth-smoke@example.org", "A-Strong-Temporary-Passphrase-2026!")
    # Allowlists are created exclusively by the administrator; no hardcoded
    # two-account limit should prevent a small team from signing in.
    await set_user("oauth-smoke-two@example.org", "A-Strong-Temporary-Passphrase-2026!")
    await set_user("oauth-smoke-three@example.org", "A-Strong-Temporary-Passphrase-2026!")
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
            host_rejected = await client.get("/healthz", headers={"Host": "evil.example"})
            assert host_rejected.status_code == 421, host_rejected.text
            from unittest.mock import patch
            with patch("builtins.print") as emit:
                origin_rejected = await client.get(
                    "/healthz", headers={"Origin": "https://evil.example"},
                )
            assert origin_rejected.status_code == 403, origin_rejected.text
            diagnostic = [
                str(call.args[0]) for call in emit.call_args_list
                if call.args and str(call.args[0]).startswith("MCP_HTTP_403 ")
            ]
            assert len(diagnostic) == 1, diagnostic
            assert '"origin": "https://evil.example"' in diagnostic[0]
            assert '"path": "/healthz"' in diagnostic[0]
            assert "password" not in diagnostic[0]
            assert "ticket" not in diagnostic[0]
            assert "https://chatgpt.com" in allowed_client_origins()
            assert "https://desktop.chat.openai.com" in allowed_client_origins()
            for origin in (
                "https://mcp.cursurituv.ro",
                "https://chatgpt.com",
                "https://desktop.chat.openai.com",
                "https://chat.openai.com",
            ):
                response = await client.get("/healthz", headers={"Origin": origin})
                assert response.status_code == 200, (origin, response.text)

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

            # Explicit redirect allowlists reject dynamically registered
            # malicious callback targets with an OAuth registration error.
            os.environ["OAUTH_ALLOWED_REDIRECT_HOSTS"] = "chatgpt.com,127.0.0.1"
            blocked = await client.post("/register", json={
                "client_name": "Impersonated ChatGPT",
                "redirect_uris": ["https://attacker.example/callback"],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            })
            assert blocked.status_code == 400, (blocked.status_code, blocked.text)
            os.environ.pop("OAUTH_ALLOWED_REDIRECT_HOSTS")

            for missing_or_wrong_resource in (None, "https://evil.example/mcp"):
                params_bad = {
                    "response_type": "code",
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "state": "invalid-resource-test",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
                if missing_or_wrong_resource is not None:
                    params_bad["resource"] = missing_or_wrong_resource
                denied = await client.get("/authorize", params=params_bad)
                assert denied.status_code == 302, denied.text
                denied_query = parse_qs(urlparse(denied.headers["location"]).query)
                assert denied_query.get("error") == ["invalid_target"], denied_query

            authorization = await client.get("/authorize", params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": "smoke-csrf",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": "https://mcp.cursurituv.ro/mcp",
            })
            assert authorization.status_code in (302, 303, 307), (authorization.status_code, authorization.text)
            login_url = authorization.headers["location"]
            assert "/login?" in login_url, login_url

            page = await client.get(login_url)
            assert page.status_code == 200
            assert "127.0.0.1" in page.text
            assert "Destinația autorizării:" in page.text
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

            # Existing databases may contain legacy plaintext credentials in JSONB.
            # The startup migration must scrub them without invalidating the code.
            async with connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT data FROM oauth_codes WHERE code_hash=%s",
                        (digest(code),),
                    )
                    stored = await cur.fetchone()
                    assert stored and "code" not in stored["data"]
                    await cur.execute(
                        """UPDATE oauth_codes
                        SET data=jsonb_set(data, '{code}', to_jsonb(%s::text))
                        WHERE code_hash=%s""",
                        (code, digest(code)),
                    )
                    await cur.execute("DELETE FROM schema_migrations WHERE version=1")
            await init_db()
            async with connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT data FROM oauth_codes WHERE code_hash=%s",
                        (digest(code),),
                    )
                    stored = await cur.fetchone()
                    assert stored and "code" not in stored["data"]

            wrong_resource = await client.post("/token", data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
                "resource": "https://evil.example/mcp",
            })
            assert wrong_resource.status_code == 400, wrong_resource.text
            assert wrong_resource.json()["error"] == "invalid_target"

            missing_resource = await client.post("/token", data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            })
            assert missing_resource.status_code == 400
            assert missing_resource.json()["error"] == "invalid_target"

            token = await client.post("/token", data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
                "resource": "https://mcp.cursurituv.ro/mcp",
            })
            assert token.status_code == 200, (token.status_code, token.text)
            credentials = token.json()
            assert credentials["access_token"] and credentials["refresh_token"]
            assert (await app.state.fastmcp_auth.load_access_token(credentials["access_token"])).resource == "https://mcp.cursurituv.ro/mcp"

            async with connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT kind,data FROM oauth_tokens WHERE token_hash IN (%s,%s)",
                        (digest(credentials["access_token"]), digest(credentials["refresh_token"])),
                    )
                    saved = await cur.fetchall()
                    assert len(saved) == 2
                    assert all("token" not in row["data"] for row in saved)
                    # Verify the scrub also works for legacy active tokens.
                    for kind, raw in (
                        ("access", credentials["access_token"]),
                        ("refresh", credentials["refresh_token"]),
                    ):
                        await cur.execute(
                            """UPDATE oauth_tokens
                            SET data=jsonb_set(data, '{token}', to_jsonb(%s::text))
                            WHERE token_hash=%s AND kind=%s""",
                            (raw, digest(raw), kind),
                        )
                    await cur.execute("DELETE FROM schema_migrations WHERE version=1")
            await init_db()
            async with connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT data FROM oauth_tokens WHERE token_hash IN (%s,%s)",
                        (digest(credentials["access_token"]), digest(credentials["refresh_token"])),
                    )
                    saved = await cur.fetchall()
                    assert len(saved) == 2
                    assert all("token" not in row["data"] for row in saved)

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

            # A DB token record with the wrong audience must never authenticate.
            async with connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """UPDATE oauth_tokens SET data=jsonb_set(data, '{resource}', to_jsonb(%s::text))
                        WHERE token_hash=%s AND kind='access'""",
                        ("https://evil.example/mcp", digest(credentials["access_token"])),
                    )
            wrong_audience = await client.post("/mcp", headers=headers, json={
                "jsonrpc": "2.0", "id": 99, "method": "tools/list", "params": {},
            })
            assert wrong_audience.status_code == 401, wrong_audience.text
            async with connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """UPDATE oauth_tokens SET data=jsonb_set(data, '{resource}', to_jsonb(%s::text))
                        WHERE token_hash=%s AND kind='access'""",
                        ("https://mcp.cursurituv.ro/mcp", digest(credentials["access_token"])),
                    )

            wrong_refresh_target = await client.post("/token", data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": credentials["refresh_token"],
                "resource": "https://evil.example/mcp",
            })
            assert wrong_refresh_target.status_code == 400
            assert wrong_refresh_target.json()["error"] == "invalid_target"

            refresh = await client.post("/token", data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": credentials["refresh_token"],
                "resource": "https://mcp.cursurituv.ro/mcp",
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
            await enable_user("oauth-smoke@example.org")
            async with connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT active FROM users WHERE email=%s",
                        ("oauth-smoke@example.org",),
                    )
                    user = await cur.fetchone()
                    assert user and user["active"], user

    await cleanup()
    print("PASS: OAuth discovery, DCR, redirect allowlist, PKCE, login, token issuance, one-use code, MCP, refresh, revocation")


if __name__ == "__main__":
    asyncio.run(main())
