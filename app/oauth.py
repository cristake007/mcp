"""Self-hosted OAuth 2.1 provider using FastMCP's native OAuth routes.

FastMCP/MCP SDK handles client registration and OAuth protocol validation (including
PKCE and redirect URI validation). This module handles authentication, consent,
persistent storage, revocation and refresh-token rotation.
"""
import hashlib
import os
import secrets
import time
import uuid
from urllib.parse import urlencode, urlsplit

from fastmcp.server.auth.auth import AccessToken, OAuthProvider
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RegistrationError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from psycopg.types.json import Jsonb

from app.db import connection


def digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _as_json(model) -> dict:
    return model.model_dump(mode="json")


def _without_credential(model, field: str) -> dict:
    """Persist OAuth metadata, never the bearer credential or one-use code."""
    data = _as_json(model)
    data.pop(field, None)
    return data


def allowed_redirect_hosts() -> frozenset[str]:
    """Exact hostnames only; empty means compatibility mode (not hardened)."""
    configured = os.environ.get("OAUTH_ALLOWED_REDIRECT_HOSTS", "")
    hosts = frozenset(host.strip().lower() for host in configured.split(",") if host.strip())
    if any("://" in host or "/" in host or ":" in host or "*" in host for host in hosts):
        raise ValueError("OAUTH_ALLOWED_REDIRECT_HOSTS must contain only exact hostnames")
    return hosts


def approved_redirect(uri: str) -> bool:
    parsed = urlsplit(uri)
    try:
        host = (parsed.hostname or "").lower()
        port = parsed.port  # Access also validates malformed ports.
    except ValueError:
        return False
    if not host or parsed.username or parsed.password or parsed.fragment:
        return False
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if not ((parsed.scheme == "https" and not loopback) or
            (parsed.scheme == "http" and loopback)):
        return False
    # Production can enable an explicit allowlist after observing ChatGPT's
    # registered callback host(s) with the admin-only list-clients command.
    return not allowed_redirect_hosts() or host in allowed_redirect_hosts()


class DatabaseOAuthProvider(OAuthProvider):
    """One-provider OAuth server; clients and token state survive restarts."""

    def __init__(self, base_url: str):
        super().__init__(
            base_url=base_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
        )

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT data FROM oauth_clients WHERE client_id=%s", (client_id,)
                )
                row = await cur.fetchone()
                return OAuthClientInformationFull.model_validate(row["data"]) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise ValueError("Missing client_id")
        if not client_info.redirect_uris or any(
            not approved_redirect(str(uri)) for uri in client_info.redirect_uris
        ):
            raise RegistrationError("invalid_redirect_uri", "OAuth callback host is not permitted")
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """INSERT INTO oauth_clients(client_id, data) VALUES (%s,%s)
                    ON CONFLICT (client_id) DO NOTHING""",
                    (client_info.client_id, Jsonb(_as_json(client_info))),
                )

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if not client.client_id or not params.code_challenge:
            raise AuthorizeError("invalid_request", "Registered client and PKCE are required")
        if not approved_redirect(str(params.redirect_uri)):
            raise AuthorizeError("unauthorized_client", "OAuth callback host is not permitted")
        ticket = secrets.token_urlsafe(36)
        payload = {
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "scopes": params.scopes or [],
            "code_challenge": params.code_challenge,
            "state": params.state,
        }
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """INSERT INTO oauth_pending(ticket_hash,client_id,data,expires_at)
                    VALUES (%s,%s,%s,now() + interval '10 minutes')""",
                    (digest(ticket), client.client_id, Jsonb(payload)),
                )
        return f"{str(self.base_url).rstrip('/')}/login?{urlencode({'ticket': ticket})}"

    async def pending(self, ticket: str) -> dict | None:
        if not ticket or len(ticket) > 256:
            return None
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """SELECT p.data,c.data AS client FROM oauth_pending p
                    JOIN oauth_clients c ON c.client_id=p.client_id
                    WHERE p.ticket_hash=%s AND p.expires_at>now()""",
                    (digest(ticket),),
                )
                row = await cur.fetchone()
                return row

    async def complete_login(self, ticket: str, user_id) -> str | None:
        """One-time consumption of login ticket after password validation."""
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """DELETE FROM oauth_pending
                    WHERE ticket_hash=%s AND expires_at>now()
                    RETURNING client_id,data""",
                    (digest(ticket),),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                client_id = row["client_id"]
                params = row["data"]
                code = secrets.token_urlsafe(40)
                auth_code = AuthorizationCode(
                    code=code,
                    client_id=client_id,
                    redirect_uri=params["redirect_uri"],
                    redirect_uri_provided_explicitly=params["redirect_uri_provided_explicitly"],
                    scopes=params["scopes"],
                    expires_at=time.time() + 300,
                    code_challenge=params["code_challenge"],
                )
                await cur.execute(
                    """INSERT INTO oauth_codes(code_hash, client_id, user_id, data, expires_at)
                    VALUES (%s,%s,%s,%s,now() + interval '5 minutes')""",
                    (digest(code), client_id, user_id, Jsonb(_without_credential(auth_code, "code"))),
                )
        return construct_redirect_uri(
            params["redirect_uri"], code=code, state=params.get("state")
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """SELECT c.data FROM oauth_codes c JOIN users u ON u.id=c.user_id
                    WHERE c.code_hash=%s AND c.client_id=%s
                      AND c.expires_at>now() AND u.active""",
                    (digest(authorization_code), client.client_id),
                )
                row = await cur.fetchone()
                return AuthorizationCode.model_validate({**row["data"], "code": authorization_code}) if row else None

    async def _issue_pair(self, conn, client_id: str, user_id, scopes: list[str]) -> OAuthToken:
        access = secrets.token_urlsafe(48)
        refresh = secrets.token_urlsafe(48)
        pair = uuid.uuid4()
        access_ttl, refresh_ttl = 3600, 30 * 86400
        token_info = AccessToken(
            token=access,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time() + access_ttl),
            claims={"sub": str(user_id)},
        )
        refresh_info = RefreshToken(
            token=refresh,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time() + refresh_ttl),
        )
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO oauth_tokens
                (token_hash,kind,client_id,user_id,pair_id,data,expires_at)
                VALUES (%s,'access',%s,%s,%s,%s,now() + interval '1 hour')""",
                (digest(access), client_id, user_id, pair, Jsonb(_without_credential(token_info, "token"))),
            )
            await cur.execute(
                """INSERT INTO oauth_tokens
                (token_hash,kind,client_id,user_id,pair_id,data,expires_at)
                VALUES (%s,'refresh',%s,%s,%s,%s,now() + interval '30 days')""",
                (digest(refresh), client_id, user_id, pair, Jsonb(_without_credential(refresh_info, "token"))),
            )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=access_ttl,
            refresh_token=refresh,
            scope=" ".join(scopes),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """DELETE FROM oauth_codes WHERE code_hash=%s AND client_id=%s
                    AND expires_at>now() RETURNING user_id, data""",
                    (digest(authorization_code.code), client.client_id),
                )
                row = await cur.fetchone()
                if row is None:
                    raise TokenError("invalid_grant", "Authorization code expired or consumed")
                await cur.execute("SELECT active FROM users WHERE id=%s", (row["user_id"],))
                user = await cur.fetchone()
                if not user or not user["active"]:
                    raise TokenError("invalid_grant", "User disabled")
                return await self._issue_pair(
                    conn, client.client_id, row["user_id"], authorization_code.scopes
                )

    async def _lookup_token(self, value: str, kind: str, client_id: str | None = None):
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """SELECT t.data,t.user_id,t.client_id,t.pair_id
                    FROM oauth_tokens t JOIN users u ON u.id=t.user_id
                    WHERE t.token_hash=%s AND t.kind=%s AND t.expires_at>now()
                      AND u.active""",
                    (digest(value), kind),
                )
                row = await cur.fetchone()
                if row and (client_id is None or row["client_id"] == client_id):
                    return row
                return None

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        row = await self._lookup_token(refresh_token, "refresh", client.client_id)
        return RefreshToken.model_validate({**row["data"], "token": refresh_token}) if row else None

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        granted = scopes or refresh_token.scopes
        if not set(granted).issubset(set(refresh_token.scopes)):
            raise TokenError("invalid_scope", "Cannot increase refresh token scopes")
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """DELETE FROM oauth_tokens WHERE token_hash=%s AND kind='refresh'
                    AND client_id=%s AND expires_at>now()
                    RETURNING user_id,pair_id""",
                    (digest(refresh_token.token), client.client_id),
                )
                row = await cur.fetchone()
                if not row:
                    raise TokenError("invalid_grant", "Refresh token expired or consumed")
                await cur.execute("DELETE FROM oauth_tokens WHERE pair_id=%s", (row["pair_id"],))
                await cur.execute("SELECT active FROM users WHERE id=%s", (row["user_id"],))
                user = await cur.fetchone()
                if not user or not user["active"]:
                    raise TokenError("invalid_grant", "User disabled")
                return await self._issue_pair(conn, client.client_id, row["user_id"], granted)

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = await self._lookup_token(token, "access")
        return AccessToken.model_validate({**row["data"], "token": token}) if row else None

    async def verify_token(self, token: str) -> AccessToken | None:
        return await self.load_access_token(token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        async with connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT pair_id FROM oauth_tokens WHERE token_hash=%s", (digest(token.token),)
                )
                row = await cur.fetchone()
                if row:
                    await cur.execute(
                        "DELETE FROM oauth_tokens WHERE pair_id=%s", (row["pair_id"],)
                    )
