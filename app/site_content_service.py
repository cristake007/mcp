"""Read-only site content use cases shared by public-content tools and inspector.

No FastMCP registration, audit database, legacy action dispatch or network
transport. All site access is injected so tests can assert source identity
and preserve legacy request behavior.
"""
import json
from collections.abc import Callable

from app.site_fields import extract_with_sources, slice_sources
from app.site_text import page_chunks


def list_urls(
    sitemap: str, domain: str | int | None, offset: int, limit: int,
    *, known_sitemaps: Callable, select_sitemap: Callable,
    sitemap_urls: Callable, domain_urls: Callable,
) -> dict:
    """List genuine sitemap URLs or WordPress taxonomy members with pagination."""
    selected_domain = None
    if domain is not None:
        from app.course_catalog import COURSE_SITEMAP
        if sitemap and sitemap not in (COURSE_SITEMAP, "cursuri-sitemap.xml"):
            raise ValueError("domain poate fi folosit doar cu sitemapul cursurilor")
        selected_domain, urls = domain_urls(domain)
        selected = COURSE_SITEMAP
    else:
        selected = select_sitemap(sitemap, known_sitemaps())
        urls = sitemap_urls(selected)
    subset = urls[offset:offset + limit]
    next_offset = offset + len(subset)
    response = {
        "sitemap": selected, "total": len(urls), "offset": offset,
        "urls": subset, "next_offset": next_offset if next_offset < len(urls) else None,
    }
    if selected_domain is not None:
        response["domain"] = selected_domain
    return response


def get_page(
    url: str, view: str, selectors: tuple[str, ...] | None,
    part: int, max_chars: int, expected_hash: str,
    force_refresh: bool, *, load_page: Callable,
) -> dict:
    """Extract immutable source text with hashes/continuations, or raw source."""
    if not url:
        raise ValueError("Precizează url")
    data, metadata = load_page(url, force_refresh)
    if view == "text":
        text, warnings, spans, missing = extract_with_sources(data, selectors)
        chunks = page_chunks(text, part, max_chars, expected_hash)
        return {
            **metadata, "url": url, "view": view,
            "format": data["format"], "warnings": warnings,
            "missing_fields": missing,
            "fields": list(selectors) if selectors is not None else None,
            "coverage": "selected" if selectors is not None else "all",
            "source_map": slice_sources(spans, part, max_chars, len(chunks["content"])),
            **chunks,
        }
    if selectors is not None:
        raise ValueError("fields se folosește numai cu view='text' sau batch_get")
    if data["format"] == "acf_json":
        payload = json.dumps(data, ensure_ascii=False)
        chunks = page_chunks(payload, part, max_chars, expected_hash)
        if chunks["total_parts"] == 1:
            return {
                **data, **metadata, "content_complete": True,
                "total_chars": chunks["total_chars"], "total_parts": 1,
                "part": 0, "sha256": chunks["sha256"],
            }
        return {
            **metadata, "url": url, "format": "acf_json",
            "encoding": "json", "fragment": chunks["content"],
            **{k: v for k, v in chunks.items() if k != "content"},
        }
    chunks = page_chunks(data["html"], part, max_chars, expected_hash)
    return {
        **metadata, **{k: v for k, v in data.items() if k != "html"},
        "html": chunks["content"],
        **{k: v for k, v in chunks.items() if k != "content"},
        "truncated": chunks["next_part"] is not None,
    }


def search_entries(
    sitemap: str, *, known_sitemaps: Callable,
    select_sitemap: Callable, sitemap_urls: Callable,
) -> list[tuple[str, str]]:
    """Deduplicate URL membership while preserving first sitemap association."""
    known = known_sitemaps()
    selected = known if not sitemap or sitemap == "all" else [
        select_sitemap(sitemap, known)
    ]
    entries = []
    seen = set()
    for item in selected:
        for url in sitemap_urls(item):
            if url not in seen:
                entries.append((url, item))
                seen.add(url)
    return entries
