"""Manage the two private accounts. Run through docker compose exec mcp."""
import argparse
import asyncio
import getpass
import re
import sys

from argon2 import PasswordHasher

from app.db import connection, init_db


async def set_user(email: str, password: str) -> None:
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(email) > 254:
        raise ValueError("Invalid email")
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    hashed = PasswordHasher().hash(password)
    async with connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT pg_advisory_xact_lock(857393)")
            await cur.execute("SELECT 1 FROM users WHERE email=%s", (email,))
            exists = await cur.fetchone()
            if not exists:
                await cur.execute("SELECT count(*) AS n FROM users")
                count = (await cur.fetchone())["n"]
                if count >= 2:
                    raise ValueError("Maximum two users. Disable does not free a slot.")
            await cur.execute(
                """INSERT INTO users(email,password_hash) VALUES (%s,%s)
                ON CONFLICT(email) DO UPDATE SET password_hash=EXCLUDED.password_hash,
                active=TRUE, failures=0, locked_until=NULL
                RETURNING id""", (email, hashed),
            )
            user = await cur.fetchone()
            # Password changes revoke existing sessions.
            await cur.execute("DELETE FROM oauth_tokens WHERE user_id=%s", (user["id"],))
            await cur.execute("DELETE FROM oauth_codes WHERE user_id=%s", (user["id"],))


async def disable_user(email: str) -> None:
    async with connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET active=FALSE WHERE email=%s RETURNING id",
                (email.strip().lower(),),
            )
            row = await cur.fetchone()
            if not row:
                raise ValueError("User not found")
            await cur.execute("DELETE FROM oauth_tokens WHERE user_id=%s", (row["id"],))
            await cur.execute("DELETE FROM oauth_codes WHERE user_id=%s", (row["id"],))


async def list_users() -> None:
    async with connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT email,active FROM users ORDER BY email")
            for row in await cur.fetchall():
                print(f'{row["email"]}\t{"active" if row["active"] else "disabled"}')


def main() -> None:
    parser = argparse.ArgumentParser(description="Private MCP user administration")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    add = sub.add_parser("add-user")
    add.add_argument("email")
    disable = sub.add_parser("disable-user")
    disable.add_argument("email")
    sub.add_parser("list-users")
    args = parser.parse_args()

    try:
        if args.command == "init-db":
            asyncio.run(init_db())
            print("Database initialized")
        elif args.command == "add-user":
            password = getpass.getpass("Password (min 12 chars): ")
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                raise ValueError("Passwords do not match")
            asyncio.run(set_user(args.email, password))
            print("Account created or updated; previous tokens revoked")
        elif args.command == "disable-user":
            asyncio.run(disable_user(args.email))
            print("Account disabled; tokens revoked")
        elif args.command == "list-users":
            asyncio.run(list_users())
    except (ValueError, KeyboardInterrupt) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
