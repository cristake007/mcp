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



def review_coverage(urls: list[str], fields: tuple[str, ...] | None,
                    offset: int = 0, limit: int = 20) -> dict:
    """Summarize explicit approvals for a sitemap without fetching page bodies.

    Reviews record hashes from acknowledged past checks; they do not guarantee
    the online text still matches, or that the reviewed text was error-free.
    """
    baselines = get_reviews(urls, fields)
    selected = urls[offset:offset + limit]
    entries = [{
        "url": url,
        "status": (
            "acknowledged_not_live_verified"
            if url in baselines else "not_acknowledged"
        ),
        "reviewed_at": baselines[url]["reviewed_at"] if url in baselines else None,
    } for url in selected]
    total = len(urls)
    reviewed = sum(url in baselines for url in urls)
    next_offset = offset + len(selected)
    return {
        "auditable_urls": total,
        "acknowledged_urls": reviewed,
        "not_acknowledged_urls": total - reviewed,
        "offset": offset,
        "entries": entries,
        "next_offset": next_offset if next_offset < total else None,
        "status_scope": "persisted_baseline_only",
        "current_site_freshness_checked": False,
        "extraction_completed_in_this_report": False,
        "note": "Raportul listeaza confirmarile explicite, nu verifica live "
                "paginile si nu atesta absenta erorilor lingvistice.",
    }


def batch_coverage(pages: list[dict], unchanged: list[dict],
                   errors: list[dict], requested: int) -> dict:
    """Describe what the batch tool actually returned, without crediting AI review."""
    pending = [p["url"] for p in pages if p.get("next_part") is not None]
    warning_urls = [p["url"] for p in pages if p.get("warnings")]
    missing = [{
        "url": p["url"], "fields": p["missing_fields"],
    } for p in pages if p.get("missing_fields")]
    failed = [e["url"] for e in errors]
    return {
        "requested_urls": requested,
        "fetched_urls": len(pages) + len(unchanged),
        "content_returned_urls": len(pages),
        "previously_acknowledged_unchanged_urls": len(unchanged),
        "fully_returned_urls": sum(p.get("next_part") is None for p in pages),
        "pending_continuation_urls": pending,
        "warning_urls": warning_urls,
        "missing_fields": missing,
        "failed_urls": failed,
        "requires_followup_urls": list(dict.fromkeys(
            pending + warning_urls + [item["url"] for item in missing] + failed
        )),
        "model_reviewed_urls": None,
        "note": "Aceste cifre certifica numai extragerea pentru acest lot, "
                "nu analiza lingvistica. Fragmentele ulterioare trebuie recuperate.",
    }
