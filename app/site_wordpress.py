"""WordPress REST and page-reading logic, independent of MCP tools.

Site transport and API lookup are injected to preserve one fixed-host security
boundary and allow isolated offline testing. Course identity checks are applied
by the caller using the exact URL context.
"""
import json
import re
from urllib.parse import quote, urlsplit

from app.html_text import clean_html
from app.site_transport import ROOT


def slug(url: str) -> str:
    return urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]


def api_item(kind: str, url: str, *, fetch, exact_url: bool = False) -> dict | None:
    endpoint = ROOT + "wp-json/wp/v2/" + kind + "?slug=" + quote(slug(url), safe="")
    payload = json.loads(fetch(endpoint, "application/json"))
    if not isinstance(payload, list):
        raise RuntimeError("Răspuns REST API invalid")
    if payload and exact_url and payload[0].get("link") != url:
        raise ValueError("URL-ul REST nu corespunde exact URL-ului observației")
    return payload[0] if payload else None


def read_page(url: str, *, allowed, api_item, fetch) -> dict:
    url = allowed(url)
    path = urlsplit(url).path
    if path.startswith("/cursuri/") and slug(url) != "cursuri":
        item = api_item("cursuri", url)
        if item is None:
            raise ValueError("Cursul nu există în REST API")
        return {
            "url": url, "format": "acf_json", "id": item.get("id"),
            "title": item.get("title", {}).get("rendered", ""),
            "link": item.get("link", url), "acf": item.get("acf"),
        }

    if re.fullmatch(r"/\d{4}/\d{2}/\d{2}/[^/]+/?", path):
        item = api_item("posts", url)
        if item is not None:
            html = item.get("content", {}).get("rendered", "")
            if html and html.strip():
                return {
                    "url": url, "format": "post_html", "id": item.get("id"),
                    "title": item.get("title", {}).get("rendered", ""),
                    "html": html,
                }

    return {"url": url, "format": "page_html", "html": clean_html(fetch(url))}
