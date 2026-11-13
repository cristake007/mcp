"""Sitemap discovery and short-lived XML cache for the fixed-host site.

The cache is only for sitemap XML (not live page bodies). Callers inject the
fixed-host transport, URL validator, and refresh flag. On forced refresh,
failures never fall back to stale cached XML.
"""
import threading
import time
import xml.etree.ElementTree as ET

from app.site_sitemaps import xml_locations

SITEMAP_TTL_SECONDS = 300
SITEMAP_CACHE = {}
SITEMAP_CACHE_LOCK = threading.Lock()


def sitemap_xml(url: str, *, allowed, fetch, refresh: bool) -> str:
    url = allowed(url)
    with SITEMAP_CACHE_LOCK:
        cached = SITEMAP_CACHE.get(url)
        if not refresh and cached and time.monotonic() - cached[0] < SITEMAP_TTL_SECONDS:
            return cached[1]
    payload = fetch(url, "application/xml,text/xml,*/*")
    # Never cache broken or non-sitemap HTML challenges.
    try:
        parsed = ET.fromstring(payload)
        if parsed.tag.split("}")[-1] not in ("sitemapindex", "urlset"):
            return payload
    except ET.ParseError:
        return payload
    with SITEMAP_CACHE_LOCK:
        if len(SITEMAP_CACHE) >= 32:
            SITEMAP_CACHE.clear()
        SITEMAP_CACHE[url] = (time.monotonic(), payload)
    return payload


def sitemaps(*, index: str, xml_reader, allowed) -> list[str]:
    links = xml_locations(xml_reader(index), "sitemap")
    return [allowed(link) for link in links]


def sitemap_url(value: str, known: list[str], *, root: str, allowed) -> str:
    if not value:
        raise ValueError("Precizează sitemap, de exemplu industriale-sitemap.xml")
    candidate = value if value.startswith("https://") else root + value.lstrip("/")
    allowed(candidate)
    if candidate not in known:
        raise ValueError("Sitemap-ul nu se află în indexul public")
    return candidate


def urls(sitemap: str, *, xml_reader, allowed) -> list[str]:
    links = xml_locations(xml_reader(sitemap), "url")
    # Ignore outbound XML entries and preserve the first occurrence of each URL.
    checked = []
    for link in links:
        try:
            checked.append(allowed(link))
        except ValueError:
            continue
    return list(dict.fromkeys(checked))
