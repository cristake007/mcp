"""Focused, read-only MCP tools for discovering and inspecting website content.

These are public-content operations only; database audit acknowledgements remain
outside this interface. Legacy inspect_site(action=...) is retained for existing
clients. All operations use the same allowlisted site backend and evidence rules.
"""
import re
from typing import Any, Literal, NotRequired, TypedDict

from app import site_inspector as site
from app.site_fields import normalize_fields
from app.site_output import SiteSourceSpan


class SitemapList(TypedDict):
    sitemaps: list[str]


class SiteURLList(TypedDict):
    sitemap: str
    total: int
    offset: int
    urls: list[str]
    next_offset: int | None
    domain: NotRequired[dict[str, Any]]


class SiteText(TypedDict):
    url: str
    view: Literal["text"]
    format: Literal["acf_json", "post_html", "page_html"]
    content: str
    fields: list[str] | None
    coverage: Literal["selected", "all"]
    missing_fields: list[str]
    source_map: list[SiteSourceSpan]
    warnings: list[str]
    total_chars: int
    sha256: str
    part: int
    total_parts: int
    next_part: int | None
    content_complete: bool
    part_complete: bool
    fetched_at: str
    provenance: Literal["cache", "unknown"]
    http_evidence: list[dict[str, Any]]


class SiteQueryTotal(TypedDict):
    query: str
    matching_urls: int
    occurrences: int


class SiteTextPosition(TypedDict):
    start: int
    end: int


class SiteTerm(TypedDict):
    query: str
    occurrences: int
    positions: list[SiteTextPosition]
    snippets: list[str]


class SiteHit(TypedDict):
    url: str
    sitemap: str
    terms: list[SiteTerm]


class SiteSearch(TypedDict):
    query: str
    match_mode: Literal["exact", "whole_word", "accent_insensitive"]
    sitemap: str
    total_urls: int
    scanned: int
    offset: int
    next_offset: int | None
    complete: bool
    matches: list[SiteHit]
    query_totals: list[SiteQueryTotal]
    totals_scope: Literal["current_batch"]
    errors: list[dict[str, str]]


def _validate_window(offset: int, limit: int, max_limit: int) -> None:
    if type(offset) is not int or not 0 <= offset <= 100000:
        raise ValueError("offset must be an integer between 0 and 100000")
    if type(limit) is not int or not 1 <= limit <= max_limit:
        raise ValueError(f"limit must be an integer between 1 and {max_limit}")


@site._refresh_scope
def list_sitemaps(force_refresh: bool = False) -> SitemapList:
    """List the public XML sitemaps of tuvkarpat.ro.

    All URLs are restricted to the fixed HTTPS site; this is not a web crawler.
    """
    if type(force_refresh) is not bool:
        raise ValueError("force_refresh must be boolean")
    return {"sitemaps": site._sitemaps()}


@site._refresh_scope
def list_site_urls(
    sitemap: str = "",
    domain: str | int | None = None,
    offset: int = 0,
    limit: int = 50,
    force_refresh: bool = False,
) -> SiteURLList:
    """List URLs from one known sitemap or from one WordPress course domain.

    Pass sitemap='cursuri-sitemap.xml' or domain='ergonomie' / domain=123.
    Follows native sitemap membership, not an external search engine.
    Continue from next_offset until null to enumerate all URLs.
    """
    _validate_window(offset, limit, 100)
    if type(force_refresh) is not bool:
        raise ValueError("force_refresh must be boolean")
    if domain is None and not sitemap:
        raise ValueError("Provide a sitemap or WordPress domain")
    result = site._inspect_urls(sitemap, domain, offset, limit)
    return {key: value for key, value in result.items() if key != "action"}


@site._refresh_scope
def get_site_text(
    url: str,
    fields: list[str] | None = None,
    part: int = 0,
    max_chars: int = 12000,
    expected_hash: str = "",
    force_refresh: bool = False,
) -> SiteText:
    """Read source-faithful visible text from an allowlisted site URL.

    WordPress courses return full ACF fields with source_map offsets.
    Read all next_part fragments with the SAME max_chars and expected_hash
    to avoid silently mixing changing page versions. No JS execution.
    """
    if type(part) is not int or part < 0:
        raise ValueError("part must be a non-negative integer")
    if type(max_chars) is not int or not 1000 <= max_chars <= 250000:
        raise ValueError("max_chars must be an integer between 1000 and 250000")
    if not isinstance(expected_hash, str) or (
        expected_hash and re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
    ):
        raise ValueError("expected_hash must be an empty string or lowercase SHA-256")
    if type(force_refresh) is not bool:
        raise ValueError("force_refresh must be boolean")
    result = site._inspect_get(
        url, "text", normalize_fields(fields), part, max_chars,
        expected_hash, force_refresh,
    )
    return {key: value for key, value in result.items() if key != "action"}


@site._refresh_scope
def search_site(
    query: str,
    sitemap: str = "",
    offset: int = 0,
    limit: int = 20,
    match_mode: Literal["exact", "whole_word", "accent_insensitive"] = "exact",
    force_refresh: bool = False,
) -> SiteSearch:
    """Search visible text on site pages, with original Unicode offsets.

    Scans at most 20 URLs per call; follow next_offset and check errors.
    For an exhaustive COURSE catalog search use find_courses instead.
    Search results are lexical matches, never linguistic judgments.
    """
    _validate_window(offset, limit, 20)
    if type(force_refresh) is not bool:
        raise ValueError("force_refresh must be boolean")
    result = site._inspect_search_many([query], match_mode, sitemap, offset, limit)
    return {
        "query": query,
        **{key: value for key, value in result.items() if key not in ("action", "queries", "note")},
    }
