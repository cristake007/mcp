"""Read-only live inspection of tuvkarpat.ro through its sitemaps and WordPress API.

Uses the same browser headers and curl transport that were validated manually.
No RAG, embeddings, database, arbitrary shell expressions or external domains.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
# Kept as a compatibility module reference for existing test/diagnostic code.
import subprocess
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit


from app import site_transport as transport, site_wordpress as wordpress
from app import site_client as client
from app import site_content_service as content_service
from app import site_sitemap_reader as sitemap_reader
from app.site_output import SiteInspectionResult
from app.site_sitemaps import (
    xml_locations as _xml_locations,
    index_pages_in_sitemap as _index_pages_in_sitemap,
)
from app.site_text import as_search_text as _as_search_text, page_chunks as _page_chunks
from app.html_text import visible_text as _visible_text, clean_html as _clean_html
from app.site_fields import normalize_fields, extract_with_sources, slice_sources
from app.text_matching import accent_fold_with_positions

ROOT = transport.ROOT
SITEMAP_INDEX = ROOT + "sitemap_index.xml"
USER_AGENT = transport.USER_AGENT
MAX_SOURCE_BYTES = transport.MAX_SOURCE_BYTES
_REFRESH = transport._REFRESH
_EVIDENCE = transport._EVIDENCE
_EXACT_URL = transport._EXACT_URL


# Preserve the legacy inspection API while using the shared site client.
_refresh_scope = client.refresh_scope


def _load_page(url, force_refresh=False, exact_url=False):
    """Backward-compatible loader preserving the inspector's mock seam."""
    return client.load_page(
        url, force_refresh, exact_url,
        reader=_read_page, now=lambda: datetime.now(timezone.utc),
    )


# Keep legacy cache handles for callers that inspect or reset the cache.
_SITEMAP_TTL_SECONDS = sitemap_reader.SITEMAP_TTL_SECONDS
_SITEMAP_CACHE = sitemap_reader.SITEMAP_CACHE
_SITEMAP_CACHE_LOCK = sitemap_reader.SITEMAP_CACHE_LOCK


def _sitemap_xml(url: str) -> str:
    return sitemap_reader.sitemap_xml(
        url, allowed=_allowed, fetch=_fetch, refresh=_REFRESH.get(),
    )


def _allowed(url: str) -> str:
    """Legacy alias: all allowlist checks live in site_transport."""
    return transport.allowed(url)


def _fetch(url: str, accept: str = "text/html,*/*") -> str:
    """Legacy injection seam for catalog and inspector tests."""
    return transport.fetch(url, accept)


def _sitemaps() -> list[str]:
    return sitemap_reader.sitemaps(
        index=SITEMAP_INDEX, xml_reader=_sitemap_xml, allowed=_allowed,
    )


def _sitemap_url(value: str, known: list[str]) -> str:
    return sitemap_reader.sitemap_url(
        value, known, root=ROOT, allowed=_allowed,
    )


def _urls(sitemap_url: str) -> list[str]:
    return sitemap_reader.urls(
        sitemap_url, xml_reader=_sitemap_xml, allowed=_allowed,
    )


def _slug(url: str) -> str:
    return wordpress.slug(url)


def _api_item(kind: str, url: str) -> dict | None:
    """Legacy API lookup, using the shared request context and transport."""
    return wordpress.api_item(
        kind, url, fetch=_fetch, exact_url=_EXACT_URL.get(),
    )


def _read_page(url: str) -> dict:
    """Legacy reader: the WordPress module owns HTML/REST page resolution."""
    return wordpress.read_page(
        url, allowed=_allowed, api_item=_api_item, fetch=_fetch,
    )


BATCH_MAX_PAGES = 10
BATCH_WORKERS = 4
BATCH_CHARS_PER_PAGE = 12000


def _audit_text(data: dict) -> tuple[str, list[str]]:
    """Serialize all visible HTML text or all leaf ACF values with field paths."""
    warnings = []
    if data["format"] != "acf_json":
        text = _visible_text(data.get("html", ""))
        if not text:
            warnings.append("Niciun text găsit în HTML; verifică pagina publică.")
        return text, warnings

    lines = []
    if data.get("title"):
        lines.append("[title]\n" + _visible_text(data["title"]))

    def traverse(value, path):
        if isinstance(value, dict):
            for name, nested in value.items():
                traverse(nested, path + "." + str(name))
        elif isinstance(value, list):
            for i, nested in enumerate(value):
                traverse(nested, path + "[" + str(i) + "]")
        elif isinstance(value, str):
            # Preserve textual values of all ACF keys, not a predefined field subset.
            text = _visible_text(value)
            if text:
                lines.append("[" + path + "]\n" + text)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            lines.append("[" + path + "]\n" + str(value))

    if data.get("acf") is None:
        warnings.append("ACF absent în REST API; conținutul poate fi incomplet.")
    else:
        traverse(data["acf"], "acf")
        if not data["acf"]:
            warnings.append("ACF este gol în REST API.")
    return "\n\n".join(lines), warnings


def _batch_page(url: str, chars_per_page: int, fields: tuple[str, ...] | None = None, force_refresh: bool = False) -> dict:
    data, metadata = _load_page(url, force_refresh)
    text, warnings, spans, missing = extract_with_sources(data, fields)
    chunks = _page_chunks(text, 0, chars_per_page)
    return {
        **metadata,
        "url": url,
        "format": data["format"],
        "id": data.get("id"),
        "warnings": warnings,
        "missing_fields": missing,
        "source_map": slice_sources(spans, 0, chars_per_page, len(chunks["content"])),
        **chunks,
        "retrieve_remaining": (
            {"action": "get", "url": url, "view": "text",
             "part": 1, "max_chars": chars_per_page,
             **({"force_refresh": True} if force_refresh else {}),
             "expected_hash": chunks["sha256"],
             "fields": list(fields) if fields is not None else None}
            if chunks["next_part"] is not None else None
        ),
    }



def _search_one(entry: tuple[str, str], query: str) -> dict | None:
    """Compatibility adapter to the reusable lexical search service."""
    from app.site_search_service import search_one
    return search_one(entry, query, read_page=_read_page)


def _accent_fold_with_positions(value: str) -> tuple[str, list[int]]:
    """Legacy wrapper for callers using the site-inspection module."""
    return accent_fold_with_positions(value)


def _search_many_one(
    entry: tuple[str, str], queries: list[str], match_mode: str,
) -> dict | None:
    """Compatibility adapter to the reusable multi-term search service."""
    from app.site_search_service import search_many_one
    return search_many_one(entry, queries, match_mode, read_page=_read_page)


def _search_entries(sitemap: str) -> list[tuple[str, str]]:
    """Legacy adapter to the read-only content service."""
    return content_service.search_entries(
        sitemap, known_sitemaps=_sitemaps, select_sitemap=_sitemap_url,
        sitemap_urls=_urls,
    )


def _quote_matches(text, spans, quote, cap=None):
    """Compatibility wrapper for existing users of the inspector module."""
    from app.site_audit_operations import quote_matches
    return quote_matches(text, spans, quote, cap)


def _verify_findings(findings, selectors, force_refresh):
    """Verify source findings via the dedicated audit service."""
    from app.site_audit_operations import verify_findings
    return verify_findings(
        findings, selectors, force_refresh,
        allowed=_allowed, load_page=_load_page,
    )


# Independent operation handlers keep the MCP entrypoint focused on input
# validation and routing. The legacy action interface remains compatible.

def _inspect_verify_quote(url, field, quote):
    """Compatibility adapter preserving legacy tool response and mock seams."""
    from app.site_audit_operations import verify_quote
    return verify_quote(url, field, quote, read_page=_read_page)


def _inspect_audit_ack(url, expected_hash, reviews, selectors, force_refresh=False):
    """Explicit acknowledgement; audit logic lives outside this MCP adapter."""
    from app.site_audit_operations import acknowledge
    return acknowledge(
        url, expected_hash, reviews, selectors,
        allowed=_allowed, load_page=_load_page, force_refresh=force_refresh,
    )


def _inspect_audit_report(sitemap, selectors, offset, limit):
    """Read persisted audit coverage through the audit service."""
    from app.site_audit_operations import audit_report
    return audit_report(
        sitemap, selectors, offset, limit,
        sitemaps=_sitemaps, sitemap_url=_sitemap_url, urls=_urls,
    )


def _inspect_urls(sitemap, domain, offset, limit):
    """Legacy URL operation, routed through the shared content service."""
    from app.course_catalog import domain_urls
    return {
        "action": "urls",
        **content_service.list_urls(
            sitemap, domain, offset, limit, known_sitemaps=_sitemaps,
            select_sitemap=_sitemap_url, sitemap_urls=_urls,
            domain_urls=domain_urls,
        ),
    }


def _inspect_get(url, view, selectors, part, max_chars, expected_hash, force_refresh):
    """Legacy page extraction, routed through the shared content service."""
    return {
        "action": "get",
        **content_service.get_page(
            url, view, selectors, part, max_chars, expected_hash,
            force_refresh, load_page=_load_page,
        ),
    }


def _inspect_batch(action, sitemap, domain, offset, limit, max_chars, selectors, force_refresh):
    """Implementation of the batch site-inspection operation."""
    selected_domain = None
    if domain is not None:
        from app.course_catalog import COURSE_SITEMAP, domain_urls
        if sitemap and sitemap not in (COURSE_SITEMAP, "cursuri-sitemap.xml"):
            raise ValueError("domain poate fi folosit doar cu sitemapul cursurilor")
        selected_domain, eligible_urls = domain_urls(domain)
        selected = COURSE_SITEMAP
        urls = eligible_urls
        skipped_index_urls = []
        # Domain batches are handled by the server, not manually by the LLM.
        # A bounded 100-course response avoids unbounded output payloads.
        batch_limit = min(max(0, len(eligible_urls) - offset), 100)
        chars_per_page = max_chars
    else:
        if not sitemap or sitemap == "all":
            raise ValueError(action + " necesită un singur sitemap explicit")
        selected = _sitemap_url(sitemap, _sitemaps())
        urls = _urls(selected)
        eligible_urls, skipped_index_urls = _index_pages_in_sitemap(selected, urls)
        batch_limit = min(limit, BATCH_MAX_PAGES)
        chars_per_page = min(max_chars, BATCH_CHARS_PER_PAGE)
    current = eligible_urls[offset:offset + batch_limit]
    if action == "audit_changes":
        from app.site_audit import get_reviews
        baselines = get_reviews(current, selectors)
    else:
        baselines = {}
    pages = []
    unchanged = []
    errors = []
    with ThreadPoolExecutor(max_workers=min(BATCH_WORKERS, max(1, len(current)))) as pool:
        futures = [pool.submit(_batch_page, item, chars_per_page, selectors, force_refresh) for item in current]
        for item, future in zip(current, futures):
            try:
                page = future.result()
                old = baselines.get(item)
                if (old is not None and old["sha256"] == page["sha256"]
                        and not page["warnings"] and not page["missing_fields"]):
                    unchanged.append({
                        "url": item, "sha256": page["sha256"],
                        "reviewed_at": old["reviewed_at"],
                    })
                else:
                    if action == "audit_changes":
                        page["change_status"] = "new" if old is None else "changed"
                    pages.append(page)
            except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
                errors.append({"url": item, "error": str(exc)[:300]})
    next_offset = offset + len(current)
    pending = [
        {"url": p["url"], "retrieve_remaining": p["retrieve_remaining"]}
        for p in pages if p["next_part"] is not None
    ]
    from app.site_audit import batch_coverage
    coverage_report = batch_coverage(pages, unchanged, errors, len(current))
    return {
        "action": action, "sitemap": selected, "total_urls": len(urls),
        **({"domain": selected_domain} if selected_domain is not None else {}),
        "auditable_urls": len(eligible_urls),
        "skipped_index_urls": skipped_index_urls,
        "fields": list(selectors) if selectors is not None else None,
        "coverage": "selected" if selectors is not None else "all",
        "offset": offset, "requested_limit": batch_limit if domain is not None else limit,
        "processed": len(current), "pages": pages, "unchanged": unchanged,
        "errors": errors,
        "coverage_report": coverage_report,
        "next_offset": next_offset if next_offset < len(eligible_urls) else None,
        "end_of_sitemap": next_offset >= len(eligible_urls),
        "batch_complete": not errors and not pending
                          and all(not p["warnings"] and not p["missing_fields"]
                                  for p in pages),
        "pending_parts": pending,
        "note": (
            "end_of_sitemap describes URL pagination only. "
            "Do not claim an exhaustive audit until all batches, errors "
            "and pending parts have been reviewed. HTML is not JS-rendered. "
            "A selected domain is the complete WordPress taxonomy selection, "
            "subject to 100-course response pagination. "
            "Only audit_ack explicitly marks pages reviewed; unchanged "
            "means unchanged since that exact field selection was approved."
        ),
    }


def _inspect_search_many(queries, match_mode, sitemap, offset, limit):
    """Legacy MCP adapter, delegating matching and pagination to a service."""
    from app.site_search_service import search_many
    return search_many(
        queries, match_mode, sitemap, offset, limit,
        list_entries=_search_entries, inspect_one=_search_many_one,
    )


def _inspect_search(query, sitemap, offset, limit):
    """Legacy MCP adapter for single-term search."""
    from app.site_search_service import search_legacy
    return search_legacy(
        query, sitemap, offset, limit,
        list_entries=_search_entries, inspect_one=_search_one,
    )


@_refresh_scope
def inspect_site(
    action: str,
    url: str = "",
    sitemap: str = "",
    query: str = "",
    offset: int = 0,
    limit: int = 20,
    max_chars: int = 60000,
    view: str = "raw",
    part: int = 0,
    expected_hash: str = "",
    fields: list[str] | None = None,
    field: str = "",
    quote: str = "",
    reviews: list[dict] | None = None,
    queries: list[str] | None = None,
    match_mode: str = "exact",
    force_refresh: bool = False,
    findings: list[dict] | None = None,
    domain: str | int | None = None,
) -> SiteInspectionResult:
    """Inspect live tuvkarpat.ro through one advanced MCP tool.

    For user requests to find/count/compare courses containing text or ISO
    standards, prefer search_courses. For domain-targeted extraction, use
    action=batch_get, domain="ergonomie" (or the WordPress domain ID). The
    entire domain, up to 100 courses per call, is processed automatically;
    beyond 100 use next_offset. Taxonomy membership is from WordPress REST API,
    not text matches or archive URLs. Without domain, batches remain 10 pages.

    force_refresh: bypass MCP caches and request HTTP revalidation with a unique
      request URL. Intermediaries may ignore it; provenance remains unknown unless
      cache evidence is observed. No cached fallback on failure.
    verify_findings: 1..100 exact URL/field/original/replacement observations;
      optional expected_hash uses the same text/fields hash as get(view='text').
      Reads each URL once; offsets are Unicode positions in visible field text.
      verified confirms presence only, never linguistic correctness.
    sitemaps: list all advertised sitemaps.
    urls: paginated URLs for an explicit sitemap.
    get: retrieve a URL, view='raw' for HTML / ACF, view='text' for all
      visible HTML text or all ACF text fields (labeled with paths). For a large
      page request each next_part using the same max_chars and expected_hash.
    batch_get: up to ten individual pages per call; archive /cursuri/
      does not consume an audit slot. Offsets apply to auditable URLs.
      Optional fields=['title','acf.grup_tinta','acf.tematica'] reduces
      returned content. Without fields, all available text is kept.
      source_map identifies field paths and exact visible-text positions.
      Default batch text limit is 12000 characters PER page, with additional
      parts recoverable through get(view='text', part=...).
      Follow both next_offset and pending_parts; inspect all errors.
    search: legacy literal case-insensitive match, max twenty URLs per call.
    search_many: 1..10 terms (queries) in one fetch per URL, max 20 URLs
      per call. match_mode='exact' (case-insensitive substring), 'whole_word'
      (Unicode word boundaries), or 'accent_insensitive' (case/diacritics
      insensitive substring). Results are paginated and return exact snippets
      and original-text offsets. Query totals cover only the current batch.
    verify_quote: exact quotation from one field path, with character offsets.
    audit_changes: batch_get only new/changed text since explicitly approved hashes.
    audit_ack: explicitly acknowledge a verified batch (url + sha256), or
      one url + expected_hash, for the requested fields. Baselines persist in
      PostgreSQL; no read, get, batch_get or audit_changes marks content reviewed.
    audit_report: paginated sitemap coverage from explicit confirmations only;
      does not re-fetch pages or assert that their content is still current.
    Each batch_get/audit_changes response includes per-batch coverage_report
      with missing fields, warnings, failed URLs, unfinished text parts.
    Valid numeric bounds: offset 0..100000; limit 1..100; max_chars
      1000..250000; part >= 0. Request at most 10 pages per batch_get or
      audit_changes; search and search_many scan at most 20 URLs per call.
      Use next_offset/next_part for the remainder, not an oversized limit.
    On invalid common parameters, returns ok=false and an error with
      parameter, accepted range and retry_with correction. Repeat the
      same action and unchanged other parameters with retry_with applied;
      the rejected call has fetched zero pages.
    Diacritics and grammar must be interpreted by the LLM, not this tool.
    """
    valid_actions = ("sitemaps", "urls", "get", "batch_get", "search",
                     "search_many", "verify_quote", "audit_changes", "audit_ack",
                     "audit_report", "verify_findings")
    if not isinstance(action, str) or action not in valid_actions:
        raise ValueError(
            "inspect_site: acțiune invalidă; folosește una dintre: "
            + ", ".join(valid_actions)
        )

    if domain is not None and action not in ("urls", "batch_get", "audit_changes"):
        raise ValueError("domain se folosește cu urls, batch_get sau audit_changes")

    # Return a typed, recoverable error instead of a FastMCP traceback for
    # malformed common parameters. A failed call never fetches or audits data.
    def invalid(parameter: str, value, expected: str, replacement) -> SiteInspectionResult:
        return {
            "action": action, "ok": False,
            "error": {
                "code": "invalid_parameter",
                "parameter": parameter,
                "message": (
                    f"Parametrul '{parameter}' este invalid: {value!r}. "
                    f"Valoare permisă: {expected}. "
                    f"Reapelează inspect_site cu {parameter}={replacement!r}."
                ),
                "expected": expected,
                "received": repr(value)[:120],
                "retry_with": {parameter: replacement},
                "retry_instruction": (
                    "Reapelează inspect_site cu valoarea din retry_with, "
                    "păstrând action și toți ceilalți parametri ai cererii inițiale. "
                    "Nu considera cererea curentă drept executată."
                ),
            },
        }

    if type(force_refresh) is not bool:
        return invalid("force_refresh", force_refresh, "boolean", False)
    if type(offset) is not int or not 0 <= offset <= 100000:
        recommended = (max(0, min(offset, 100000))
                       if type(offset) is int else 0)
        return invalid("offset", offset, "număr întreg între 0 și 100000", recommended)
    if type(limit) is not int or not 1 <= limit <= 100:
        # Existing behavior is unchanged: batch_* processes at most 10 per
        # call and search* at most 20, even for higher valid requested limits.
        effective_limit = (10 if action in ("batch_get", "audit_changes")
                           else 20 if action in ("search", "search_many")
                           else 100 if action in ("urls", "audit_report") else 20)
        recommended = (1 if type(limit) is int and limit < 1
                       else effective_limit)
        return invalid(
            "limit", limit,
            "număr întreg între 1 și 100; batch_get/audit_changes: maxim 10 "
            "URL-uri procesate pe apel; search/search_many: maxim 20 URL-uri "
            "scanate pe apel. Pentru restul folosește next_offset când există",
            recommended,
        )
    if type(max_chars) is not int or not 1000 <= max_chars <= 250000:
        recommended = (
            max(1000, min(max_chars, 250000)) if type(max_chars) is int
            else 12000 if action in ("batch_get", "audit_changes") else 60000
        )
        return invalid(
            "max_chars", max_chars, "număr întreg între 1000 și 250000; "
            "batch_get/audit_changes returnează maxim 12000 caractere "
            "per pagină per apel, cu restul prin get + next_part",
            recommended,
        )
    if type(part) is not int or part < 0:
        return invalid("part", part, "număr întreg nenegativ; la continuare folosește next_part", 0)
    if (not isinstance(expected_hash, str)
            or (expected_hash and not re.fullmatch(r"[0-9a-f]{64}", expected_hash))):
        return invalid(
            "expected_hash", expected_hash, "șir gol sau SHA-256 hexadecimal "
            "din răspunsul inițial", "",
        )
    if view not in ("raw", "text"):
        return invalid("view", view, "'raw' sau 'text'", "raw")


    selectors = normalize_fields(fields)

    if action == "verify_findings":
        return _verify_findings(findings, selectors, force_refresh)

    if action == "verify_quote":
        return _inspect_verify_quote(url, field, quote)

    if action == "audit_ack":
        return _inspect_audit_ack(url, expected_hash, reviews, selectors, force_refresh)

    if action == "sitemaps":
        return {"action": action, "sitemaps": _sitemaps()}

    if action == "audit_report":
        return _inspect_audit_report(sitemap, selectors, offset, limit)

    if action == "urls":
        return _inspect_urls(sitemap, domain, offset, limit)

    if action == "get":
        return _inspect_get(url, view, selectors, part, max_chars, expected_hash, force_refresh)

    if action in ("batch_get", "audit_changes"):
        return _inspect_batch(action, sitemap, domain, offset, limit, max_chars, selectors, force_refresh)

    if action == "search_many":
        return _inspect_search_many(queries, match_mode, sitemap, offset, limit)

    return _inspect_search(query, sitemap, offset, limit)
