"""Explicit review baselines for incremental site audits (PostgreSQL only).

A downloaded page is never marked reviewed automatically. A baseline stores
only the normalized selection identity and its SHA-256, not page contents.
All authenticated MCP users of this deployment share the audit baseline.
"""
import json
import os

import psycopg


def _field_key(fields: tuple[str, ...] | None) -> str:
    # Selector order does not alter extracted field order/content.
    return json.dumps(None if fields is None else sorted(fields), ensure_ascii=False)


def get_reviews(urls: list[str], fields: tuple[str, ...] | None) -> dict[str, dict]:
    if not urls:
        return {}
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT url, content_hash, reviewed_at FROM site_audit_reviews
                   WHERE fields_key=%s AND url=ANY(%s)""",
                (_field_key(fields), urls),
            )
            return {
                url: {"sha256": checksum, "reviewed_at": reviewed_at.isoformat()}
                for url, checksum, reviewed_at in cur.fetchall()
            }


def save_reviews(items: list[tuple[str, str]], fields: tuple[str, ...] | None) -> None:
    """Atomically store a batch only after all live hashes were validated."""
    key = _field_key(fields)
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO site_audit_reviews(url, fields_key, content_hash, reviewed_at)
                   VALUES (%s,%s,%s,now())
                   ON CONFLICT (url, fields_key) DO UPDATE SET
                   content_hash=EXCLUDED.content_hash, reviewed_at=now()""",
                [(url, key, checksum) for url, checksum in items],
            )
