"""Read-only live inspection of tuvkarpat.ro through its sitemaps and WordPress API.

Uses the same browser headers and curl transport that were validated manually.
No RAG, embeddings, database, arbitrary shell expressions or external domains.
"""
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from bs4.element import Comment, NavigableString, Tag
from urllib.parse import quote, urlsplit

from bs4 import BeautifulSoup

ROOT = "https://tuvkarpat.ro/"
SITEMAP_INDEX = ROOT + "sitemap_index.xml"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
    "Gecko/20100101 Firefox/128.0"
)
REMOVE_HTML = (
    "script, meta, iframe, noscript, "
    "body > header, body > footer, "
    ".elementor-location-header, .elementor-location-footer"
)
MAX_SOURCE_BYTES = 4_000_000


def _allowed(url: str) -> str:
    """Do not accept arbitrary URLs, redirects, ports or userinfo."""
    if not isinstance(url, str) or any(c in url for c in "\r\n\x00"):
        raise ValueError("URL invalid")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "tuvkarpat.ro"
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise ValueError("Sunt acceptate doar URL-uri HTTPS de pe tuvkarpat.ro")
    return url


def _fetch(url: str, accept: str = "text/html,*/*") -> str:
    """Execute a fixed, non-shell curl invocation; do not follow redirects."""
    url = _allowed(url)
    try:
        result = subprocess.run(
            [
                "curl", "-fsS", "--compressed",
                "--proto", "=https", "--connect-timeout", "8",
                "--max-time", "20", "--max-filesize", str(MAX_SOURCE_BYTES),
                "-A", USER_AGENT,
                "-H", "Accept: " + accept,
                "-H", "Referer: " + ROOT,
                "-w", "\n%{http_code}",
                url,
            ],
            capture_output=True,
            timeout=23,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Cererea către site a eșuat sau a expirat") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError("curl a eșuat: " + (detail[:250] or str(result.returncode)))
    body, separator, status = result.stdout.rpartition(b"\n")
    if not separator or status.strip() != b"200":
        raise RuntimeError("Răspuns HTTP neașteptat (nu urmărim redirecturi): " +
                           status.decode("ascii", errors="replace")[:20])
    if len(body) > MAX_SOURCE_BYTES:
        raise RuntimeError("Răspuns prea mare")
    return body.decode("utf-8-sig", errors="replace")


def _xml_locations(xml: str, kind: str) -> list[str]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise RuntimeError("Sitemap-ul nu conține XML valid") from exc
    root_name = root.tag.split("}")[-1]
    expected = "sitemapindex" if kind == "sitemap" else "urlset"
    if root_name != expected:
        raise RuntimeError("Tip de sitemap neașteptat: " + root_name)
    return [
        loc.text.strip()
        for item in root
        if item.tag.split("}")[-1] == ("sitemap" if kind == "sitemap" else "url")
        for loc in item
        if loc.tag.split("}")[-1] == "loc" and loc.text and loc.text.strip()
    ]


def _sitemaps() -> list[str]:
    links = _xml_locations(
        _fetch(SITEMAP_INDEX, "application/xml,text/xml,*/*"), "sitemap"
    )
    return [_allowed(link) for link in links]


def _sitemap_url(value: str, known: list[str]) -> str:
    if not value:
        raise ValueError("Precizează sitemap, de exemplu industriale-sitemap.xml")
    candidate = value if value.startswith("https://") else ROOT + value.lstrip("/")
    _allowed(candidate)
    if candidate not in known:
        raise ValueError("Sitemap-ul nu se află în indexul public")
    return candidate


def _urls(sitemap_url: str) -> list[str]:
    links = _xml_locations(
        _fetch(sitemap_url, "application/xml,text/xml,*/*"), "url"
    )
    # Ignore outbound links even if the XML contains unexpected locations.
    urls = []
    for link in links:
        try:
            urls.append(_allowed(link))
        except ValueError:
            continue
    return list(dict.fromkeys(urls))


def _clean_html(raw: str) -> str:
    soup = BeautifulSoup(raw, "html.parser")
    for element in soup.select(REMOVE_HTML):
        element.decompose()
    return (soup.body or soup).prettify()


def _slug(url: str) -> str:
    return urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]


def _api_item(kind: str, url: str) -> dict | None:
    endpoint = ROOT + "wp-json/wp/v2/" + kind + "?slug=" + quote(_slug(url), safe="")
    payload = json.loads(_fetch(endpoint, "application/json"))
    if not isinstance(payload, list):
        raise RuntimeError("Răspuns REST API invalid")
    return payload[0] if payload else None


def _read_page(url: str) -> dict:
    url = _allowed(url)
    path = urlsplit(url).path
    if path.startswith("/cursuri/") and _slug(url) != "cursuri":
        item = _api_item("cursuri", url)
        if item is None:
            raise ValueError("Cursul nu există în REST API")
        return {
            "url": url, "format": "acf_json", "id": item.get("id"),
            "title": item.get("title", {}).get("rendered", ""),
            "link": item.get("link", url), "acf": item.get("acf"),
        }

    if re.fullmatch(r"/\d{4}/\d{2}/\d{2}/[^/]+/?", path):
        item = _api_item("posts", url)
        if item is not None:
            html = item.get("content", {}).get("rendered", "")
            if html and html.strip():
                return {
                    "url": url, "format": "post_html", "id": item.get("id"),
                    "title": item.get("title", {}).get("rendered", ""),
                    "html": html,
                }

    return {"url": url, "format": "page_html", "html": _clean_html(_fetch(url))}


def _as_search_text(data: dict) -> str:
    if data["format"] == "acf_json":
        pieces = [data.get("title", "")]
        def collect(value):
            if isinstance(value, dict):
                for nested in value.values():
                    collect(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect(nested)
            elif isinstance(value, str):
                pieces.append(BeautifulSoup(value, "html.parser").get_text(" ", strip=True))
        collect(data.get("acf"))
        return re.sub(r"\s+", " ", " ".join(pieces)).strip()
    soup = BeautifulSoup(data["html"], "html.parser")
    # Menus within Elementor templates are navigation, not page content.
    for nav in soup.select("nav, .elementor-nav-menu"):
        nav.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()



# Preserve text from all ACF source fields and unknown Elementor elements.
# Structural separators retain context from headings, lists and table cells.
BLOCK_TAGS = {
    "article", "aside", "blockquote", "dd", "details", "div", "dl", "dt",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
    "h5", "h6", "header", "hr", "li", "main", "ol", "p", "pre",
    "section", "summary", "table", "tbody", "tfoot", "thead", "tr", "ul",
}
BATCH_MAX_PAGES = 5
BATCH_CHARS_PER_PAGE = 12000


def _visible_text(html_or_text: str) -> str:
    """Keep all captured text nodes, including unknown widgets and table cells.

    This processes the returned HTML, not JavaScript-rendered DOM. It does not
    attempt Romanian-language correction, filtering or candidate detection.
    """
    soup = BeautifulSoup(html_or_text, "html.parser")
    for element in soup.select("script, style, meta, iframe, noscript"):
        element.decompose()
    pieces = []

    def visit(node):
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            pieces.append(str(node))
            return
        if not isinstance(node, Tag):
            return
        if node.name in BLOCK_TAGS or node.name == "br":
            pieces.append("\n")
        if node.name == "img" and node.get("alt"):
            pieces.append(" [imagine alt: " + node["alt"] + "] ")
        for child in node.children:
            visit(child)
        if node.name in {"td", "th"}:
            pieces.append("\t")
        elif node.name in BLOCK_TAGS:
            pieces.append("\n")

    visit(soup)
    text = "".join(pieces)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


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


def _page_chunks(content: str, part: int, size: int, expected_hash: str = "") -> dict:
    """Deterministic, reconstructible slices. No characters disappear."""
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if expected_hash and expected_hash != checksum:
        raise ValueError("Conținutul paginii s-a schimbat; reia de la part=0.")
    total_parts = max(1, (len(content) + size - 1) // size)
    if not 0 <= part < total_parts:
        raise ValueError("part este în afara intervalului 0..%d" % (total_parts - 1))
    piece = content[part * size:(part + 1) * size]
    return {
        "content": piece,
        "total_chars": len(content),
        "sha256": checksum,
        "part": part,
        "total_parts": total_parts,
        "next_part": part + 1 if part + 1 < total_parts else None,
        "content_complete": total_parts == 1,
        "part_complete": True,
    }


def _batch_page(url: str, chars_per_page: int) -> dict:
    data = _read_page(url)
    text, warnings = _audit_text(data)
    chunks = _page_chunks(text, 0, chars_per_page)
    return {
        "url": url,
        "format": data["format"],
        "id": data.get("id"),
        "warnings": warnings,
        **chunks,
        "retrieve_remaining": (
            {"action": "get", "url": url, "view": "text",
             "part": 1, "max_chars": chars_per_page,
             "expected_hash": chunks["sha256"]}
            if chunks["next_part"] is not None else None
        ),
    }



def _search_one(entry: tuple[str, str], query: str) -> dict | None:
    url, sitemap = entry
    data = _read_page(url)
    content = _as_search_text(data)
    lowered, needle = content.casefold(), query.casefold()
    count = lowered.count(needle)
    if not count:
        return None
    snippets = []
    start = 0
    for _ in range(min(count, 3)):
        pos = lowered.find(needle, start)
        if pos == -1:
            break
        snippets.append(content[max(0, pos - 90): min(len(content), pos + len(query) + 90)])
        start = pos + len(query)
    return {"url": url, "sitemap": sitemap, "occurrences": count, "snippets": snippets}


def _search_entries(sitemap: str) -> list[tuple[str, str]]:
    known = _sitemaps()
    selected = known if not sitemap or sitemap == "all" else [_sitemap_url(sitemap, known)]
    entries = []
    seen = set()
    for item in selected:
        for url in _urls(item):
            if url not in seen:
                entries.append((url, item))
                seen.add(url)
    return entries


def inspect_site(
    action: str,
    url: str = "",
    sitemap: str = "",
    query: str = "",
    offset: int = 0,
    limit: int = 20,
    max_chars: int = 60000,
) -> dict:
    """Inspect live tuvkarpat.ro content.

    action='sitemaps': list available sitemaps.
    action='urls': list paginated URLs from sitemap='industriale-sitemap.xml'.
    action='get': retrieve URL content (ACF JSON for courses, HTML for posts/pages).
    action='search': exact case-insensitive phrase search over sitemap='all' (default)
      or one sitemap. Searches only a bounded page batch per call. Follow
      next_offset until it is null; results are NOT exhaustive before then.
    Only the public tuvkarpat.ro HTTPS host is queried. No semantic inference.
    """
    if action not in ("sitemaps", "urls", "get", "search"):
        raise ValueError("Acțiune invalidă: sitemaps, urls, get, search")
    if not 0 <= offset <= 100000:
        raise ValueError("offset trebuie să fie între 0 și 100000")
    if not 1 <= limit <= 100:
        raise ValueError("limit trebuie să fie între 1 și 100")
    if not 1000 <= max_chars <= 250000:
        raise ValueError("max_chars trebuie să fie între 1000 și 250000")

    if action == "sitemaps":
        return {"action": action, "sitemaps": _sitemaps()}

    if action == "urls":
        selected = _sitemap_url(sitemap, _sitemaps())
        urls = _urls(selected)
        part = urls[offset:offset + limit]
        nxt = offset + len(part)
        return {
            "action": action, "sitemap": selected, "total": len(urls),
            "offset": offset, "urls": part,
            "next_offset": nxt if nxt < len(urls) else None,
        }

    if action == "get":
        if not url:
            raise ValueError("Precizează url")
        data = _read_page(url)
        if "html" in data:
            original = data["html"]
            data["html"] = original[:max_chars]
            data["truncated"] = len(original) > max_chars
            data["total_chars"] = len(original)
        return {"action": action, **data}

    if not query or not query.strip() or len(query) > 200:
        raise ValueError("query trebuie să conțină între 1 și 200 de caractere")
    entries = _search_entries(sitemap)
    # Protect the website against unbounded scans per tool invocation.
    scan_count = min(limit, 20)
    batch = entries[offset:offset + scan_count]
    matches = []
    errors = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_search_one, item, query) for item in batch]
        for item, future in zip(batch, futures):
            try:
                match = future.result()
                if match:
                    matches.append(match)
            except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
                errors.append({"url": item[0], "error": str(exc)[:200]})
    nxt = offset + len(batch)
    next_offset = nxt if nxt < len(entries) else None
    return {
        "action": action, "query": query, "sitemap": sitemap or "all",
        "total_urls": len(entries), "scanned": len(batch), "offset": offset,
        "next_offset": next_offset,
        "complete": next_offset is None and not errors,
        "matches": matches, "errors": errors,
    }
