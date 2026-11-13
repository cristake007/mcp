"""Shared public-site access facade for course catalog, search and inspection.

This module is deliberately independent of site_inspector and MCP registration.
All consumers share one fixed-host transport, sitemap cache and WordPress
reader; request-level refresh/evidence context is stored by site_transport.
"""
import functools
import inspect
from datetime import datetime, timezone

from app import site_sitemap_reader as sitemap_reader
from app import site_transport as transport
from app import site_wordpress as wordpress

ROOT = transport.ROOT
SITEMAP_INDEX = ROOT + "sitemap_index.xml"
_REFRESH = transport._REFRESH
_EVIDENCE = transport._EVIDENCE
_EXACT_URL = transport._EXACT_URL


def refresh_scope(function):
    """Scope refresh across nested calls, restoring context on all outcomes."""
    signature = inspect.signature(function)

    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        refresh = signature.bind_partial(*args, **kwargs).arguments.get(
            "force_refresh", False,
        )
        token = _REFRESH.set(refresh)
        try:
            return function(*args, **kwargs)
        finally:
            _REFRESH.reset(token)
    return wrapped


def allowed(url: str) -> str:
    return transport.allowed(url)


def fetch(url: str, accept: str = "text/html,*/*") -> str:
    return transport.fetch(url, accept)


def sitemap_xml(url: str) -> str:
    return sitemap_reader.sitemap_xml(
        url, allowed=allowed, fetch=fetch, refresh=_REFRESH.get(),
    )


def sitemaps() -> list[str]:
    return sitemap_reader.sitemaps(
        index=SITEMAP_INDEX, xml_reader=sitemap_xml, allowed=allowed,
    )


def sitemap_url(value: str, known: list[str]) -> str:
    return sitemap_reader.sitemap_url(
        value, known, root=ROOT, allowed=allowed,
    )


def urls(sitemap: str) -> list[str]:
    return sitemap_reader.urls(
        sitemap, xml_reader=sitemap_xml, allowed=allowed,
    )


def api_item(kind: str, url: str) -> dict | None:
    return wordpress.api_item(
        kind, url, fetch=fetch, exact_url=_EXACT_URL.get(),
    )


def read_page(url: str) -> dict:
    return wordpress.read_page(
        url, allowed=allowed, api_item=api_item, fetch=fetch,
    )


def load_page(
    url: str,
    force_refresh: bool = False,
    exact_url: bool = False,
    *,
    reader=None,
    now=None,
) -> tuple[dict, dict]:
    """Return page and request evidence, always resetting nested context.

    reader/now are internal injection seams for legacy compatibility and tests.
    They are not exposed as MCP tool parameters.
    """
    refresh_token = _REFRESH.set(force_refresh)
    identity_token = _EXACT_URL.set(exact_url)
    evidence = []
    evidence_token = _EVIDENCE.set(evidence)
    try:
        data = (reader or read_page)(url)
        timestamp = now() if now is not None else datetime.now(timezone.utc)
        return data, {
            "fetched_at": timestamp.isoformat(),
            # Cache miss/bypass is not evidence that CDN returned the origin.
            "provenance": "cache" if any(item["cache_hit"] for item in evidence) else "unknown",
            "http_evidence": evidence,
        }
    finally:
        _EVIDENCE.reset(evidence_token)
        _REFRESH.reset(refresh_token)
        _EXACT_URL.reset(identity_token)
