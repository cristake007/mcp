"""Read-only PostgreSQL tool example. Database role MUST be SELECT-only."""
import os

import psycopg
from psycopg.rows import dict_row
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("reference-postgres")


@mcp.tool()
async def find_courses_by_title(search: str, limit: int = 10) -> list[dict]:
    """Search course titles via parameterized query; assumes example courses table exists."""
    search = search.strip()
    if not 2 <= len(search) <= 100 or not 1 <= limit <= 25:
        raise ValueError("search must contain 2–100 characters and limit must be 1–25")
    dsn = os.environ.get("READONLY_DATABASE_URL")
    if not dsn:
        raise RuntimeError("READONLY_DATABASE_URL is not set")
    # No user-supplied SQL or table names. Enforce read-only both in session and DB role.
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        async with conn.cursor() as cur:
            await cur.execute("SET default_transaction_read_only = on")
            await cur.execute("SET statement_timeout = '5000ms'")
            await cur.execute(
                "SELECT id, title FROM courses WHERE title ILIKE %s ORDER BY title LIMIT %s",
                (f"%{search.replace('%', r'\%').replace('_', r'\_')}%", limit),
            )
            return await cur.fetchall()


if __name__ == "__main__":
    mcp.run(transport="stdio")
