"""PostgreSQL storage. No authentication data is kept only in process memory."""
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import psycopg
from psycopg.rows import dict_row


@asynccontextmanager
async def connection() -> AsyncIterator[psycopg.AsyncConnection]:
    conn = await psycopg.AsyncConnection.connect(
        os.environ["DATABASE_URL"], row_factory=dict_row, connect_timeout=5
    )
    async with conn:
        yield conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  failures INT NOT NULL DEFAULT 0,
  locked_until TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS oauth_clients (
  client_id TEXT PRIMARY KEY,
  data JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS oauth_pending (
  ticket_hash TEXT PRIMARY KEY,
  client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
  data JSONB NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_codes (
  code_hash TEXT PRIMARY KEY,
  client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
  user_id UUID NOT NULL REFERENCES users(id),
  data JSONB NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_tokens (
  token_hash TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('access','refresh')),
  client_id TEXT NOT NULL REFERENCES oauth_clients(client_id),
  user_id UUID NOT NULL REFERENCES users(id),
  pair_id UUID NOT NULL,
  data JSONB NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS oauth_tokens_pair_idx ON oauth_tokens(pair_id);
CREATE TABLE IF NOT EXISTS login_attempts (
  ip TEXT PRIMARY KEY,
  attempts INTEGER NOT NULL,
  started_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS site_audit_reviews (
  url TEXT NOT NULL,
  fields_key TEXT NOT NULL,
  content_hash CHAR(64) NOT NULL,
  reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (url, fields_key)
);
"""


async def init_db() -> None:
    async with connection() as conn:
        async with conn.cursor() as cur:
            # Serializes startup migrations if multiple app instances boot.
            await cur.execute("SELECT pg_advisory_xact_lock(857394)")
            await cur.execute(SCHEMA)
            await cur.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 1"
            )
            if not await cur.fetchone():
                # Baseline migration for earlier installations: remove OAuth
                # credentials from JSONB without invalidating hashed tokens.
                await cur.execute(
                    "UPDATE oauth_tokens SET data = data - 'token' WHERE data ? 'token'"
                )
                await cur.execute(
                    "UPDATE oauth_codes SET data = data - 'code' WHERE data ? 'code'"
                )
                await cur.execute(
                    "INSERT INTO schema_migrations(version) VALUES (1)"
                )


async def healthy() -> bool:
    async with connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1")
            return (await cur.fetchone()) is not None


async def get_active_user_id(email: str, password: str, ip: str):
    """Check password, rate-limit IP and account; return UUID or None."""
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, VerificationError

    ph = PasswordHasher()
    email = email.strip().lower()
    ip = ip[:64]
    async with connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO login_attempts (ip, attempts, started_at)
                VALUES (%s, 1, now())
                ON CONFLICT (ip) DO UPDATE SET
                  attempts = CASE
                    WHEN login_attempts.started_at < now() - interval '15 minutes'
                    THEN 1 ELSE login_attempts.attempts + 1 END,
                  started_at = CASE
                    WHEN login_attempts.started_at < now() - interval '15 minutes'
                    THEN now() ELSE login_attempts.started_at END
                RETURNING attempts
                """, (ip,)
            )
            attempt = await cur.fetchone()
            if attempt["attempts"] > 20:
                return None

            await cur.execute(
                "SELECT id, password_hash, active, failures, "
                "locked_until FROM users WHERE email=%s FOR UPDATE",
                (email,)
            )
            user = await cur.fetchone()
            if not user or not user["active"]:
                return None
            if user["locked_until"] and user["locked_until"].timestamp() > __import__("time").time():
                return None
            try:
                valid = ph.verify(user["password_hash"], password)
            except (VerifyMismatchError, VerificationError):
                valid = False
            if not valid:
                await cur.execute(
                    """
                    UPDATE users SET
                      failures = failures + 1,
                      locked_until = CASE WHEN failures + 1 >= 5
                        THEN now() + interval '15 minutes' ELSE NULL END
                    WHERE id = %s
                    """, (user["id"],)
                )
                return None
            await cur.execute(
                "UPDATE users SET failures=0, locked_until=NULL WHERE id=%s",
                (user["id"],)
            )
            # Successful authentication must not consume the IP brute-force
            # budget, especially when colleagues share one office NAT address.
            await cur.execute("DELETE FROM login_attempts WHERE ip=%s", (ip,))
            return user["id"]


async def user_for_token(token_hash: str):
    async with connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT u.email FROM oauth_tokens t JOIN users u ON u.id=t.user_id
                WHERE t.token_hash=%s AND t.kind='access' AND t.expires_at>now()
                  AND u.active""", (token_hash,)
            )
            return await cur.fetchone()
