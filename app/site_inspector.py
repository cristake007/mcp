"""Read-only live inspection of tuvkarpat.ro through its sitemaps and WordPress API.

Uses the same browser headers and curl transport that were validated manually.
No RAG, embeddings, database, arbitrary shell expressions or external domains.
"""
import hashlib
import json
import re
import subprocess
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from bs4.element import Comment, NavigableString, Tag
from urllib.parse import quote, urlsplit

from bs4 import BeautifulSoup

from app.site_output import SiteInspectionResult

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

# Short-lived sitemap-only cache avoids two redundant requests per batch.
# Live page/ACF data are still retrieved anew every time.
_SITEMAP_TTL_SECONDS = 300
_SITEMAP_CACHE = {}
_SITEMAP_CACHE_LOCK = threading.Lock()


def _sitemap_xml(url: str) -> str:
    url = _allowed(url)
    with _SITEMAP_CACHE_LOCK:
        cached = _SITEMAP_CACHE.get(url)
        if cached and time.monotonic() - cached[0] < _SITEMAP_TTL_SECONDS:
            return cached[1]
    payload = _fetch(url, "application/xml,text/xml,*/*")
    # Never cache broken or non-sitemap HTML challenges.
    try:
        parsed = ET.fromstring(payload)
        if parsed.tag.split("}")[-1] not in ("sitemapindex", "urlset"):
            return payload
    except ET.ParseError:
        return payload
    with _SITEMAP_CACHE_LOCK:
        if len(_SITEMAP_CACHE) >= 32:
            _SITEMAP_CACHE.clear()
        _SITEMAP_CACHE[url] = (time.monotonic(), payload)
    return payload




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
        _sitemap_xml(SITEMAP_INDEX), "sitemap"
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
        _sitemap_xml(sitemap_url), "url"
    )
    # Ignore outbound links even if the XML contains unexpected locations.
    urls = []
    for link in links:
        try:
            urls.append(_allowed(link))
        except ValueError:
            continue
    return list(dict.fromkeys(urls))


def _index_pages_in_sitemap(sitemap_url: str, urls: list[str]) -> tuple[list[str], list[str]]:
    """Exclude an archive landing page from individual-entry audit batches.

    For cursuri-sitemap.xml, /cursuri/ is the archive, not an ACF course.
    This does not affect the normal urls or get actions.
    """
    filename = urlsplit(sitemap_url).path.rsplit("/", 1)[-1]
    match = re.fullmatch(r"([a-z0-9_-]+)-sitemap(?:\d+)?\.xml", filename)
    if match is None:
        return list(urls), []
    archive_path = "/" + match.group(1)
    entries, skipped = [], []
    for item in urls:
        parsed = urlsplit(item)
        if not parsed.query and parsed.path.rstrip("/") == archive_path:
            skipped.append(item)
        else:
            entries.append(item)
    return entries, skipped

def _clean_html(raw: str) -> str:
    soup = BeautifulSoup(raw, "html.parser")
    for element in soup.select(REMOVE_HTML):
        element.decompose()
    # Do not prettify: inserted indentation/newlines can split inline words.
    return str(soup.body or soup)


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
BATCH_MAX_PAGES = 10
BATCH_WORKERS = 4
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


def _batch_page(url: str, chars_per_page: int, fields: tuple[str, ...] | None = None) -> dict:
    from app.site_fields import extract_with_sources, slice_sources
    data = _read_page(url)
    text, warnings, spans, missing = extract_with_sources(data, fields)
    chunks = _page_chunks(text, 0, chars_per_page)
    return {
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
             "expected_hash": chunks["sha256"],
             "fields": list(fields) if fields is not None else None}
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



def _accent_fold_with_positions(value: str) -> tuple[str, list[int]]:
    """Case/accent-fold Unicode while retaining source character offsets.

    A folded Unicode character can expand (e.g. casefold of 'ß'), so simply
    using indexes from a normalized string would misquote source passages.
    """
    folded = []
    positions = []
    for index, char in enumerate(value):
        normalized = "".join(
            part for part in unicodedata.normalize("NFD", char)
            if unicodedata.category(part) != "Mn"
        ).casefold()
        for part in normalized:
            folded.append(part)
            positions.append(index)
    return "".join(folded), positions


def _search_many_one(
    entry: tuple[str, str], queries: list[str], match_mode: str,
) -> dict | None:
    """Fetch a page once and match every requested term against its text."""
    url, sitemap = entry
    content = _as_search_text(_read_page(url))
    folded_content = ""
    positions = []
    if match_mode == "accent_insensitive":
        folded_content, positions = _accent_fold_with_positions(content)

    found = []
    for query in queries:
        if match_mode == "accent_insensitive":
            folded_query, _ = _accent_fold_with_positions(query)
            if not folded_query:
                # Validation also prevents accent-only queries.
                continue
            hits = []
            start = 0
            while True:
                pos = folded_content.find(folded_query, start)
                if pos < 0:
                    break
                hits.append((positions[pos], positions[pos + len(folded_query) - 1] + 1))
                start = pos + len(folded_query)
        else:
            boundary = match_mode == "whole_word"
            pattern = re.escape(query)
            if boundary:
                pattern = r"(?<!\w)" + pattern + r"(?!\w)"
            hits = [match.span() for match in re.finditer(pattern, content, re.IGNORECASE)]

        if not hits:
            continue
        found.append({
            "query": query,
            "occurrences": len(hits),
            "positions": [{"start": start, "end": end} for start, end in hits[:3]],
            "snippets": [
                content[max(0, start - 90): min(len(content), end + 90)]
                for start, end in hits[:3]
            ],
        })
    if not found:
        return None
    return {"url": url, "sitemap": sitemap, "terms": found}



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
    view: str = "raw",
    part: int = 0,
    expected_hash: str = "",
    fields: list[str] | None = None,
    field: str = "",
    quote: str = "",
    reviews: list[dict] | None = None,
    queries: list[str] | None = None,
    match_mode: str = "exact",
) -> SiteInspectionResult:
    """Inspect live tuvkarpat.ro through one MCP tool.

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
                     "audit_report")
    if not isinstance(action, str) or action not in valid_actions:
        raise ValueError(
            "inspect_site: acțiune invalidă; folosește una dintre: "
            + ", ".join(valid_actions)
        )

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


    from app.site_fields import normalize_fields, extract_with_sources, slice_sources
    selectors = normalize_fields(fields)

    if action == "verify_quote":
        if not url:
            raise ValueError("verify_quote necesită url")
        field_selector = normalize_fields([field])
        if not isinstance(quote, str) or not 1 <= len(quote) <= 500:
            raise ValueError("quote trebuie să conțină 1..500 caractere")
        data = _read_page(url)
        text, warnings, spans, missing = extract_with_sources(data, field_selector)
        matches = []
        total_matches = 0
        for span in spans:
            value = text[span["content_start"]:span["content_end"]]
            cursor = 0
            while True:
                pos = value.find(quote, cursor)
                if pos == -1:
                    break
                total_matches += 1
                if len(matches) < 20:
                    matches.append({
                        "field": span["field"], "start": pos,
                        "end": pos + len(quote),
                        "context": value[max(0, pos - 80):min(len(value), pos + len(quote) + 80)],
                    })
                cursor = pos + len(quote)
        return {
            "action": action, "url": url, "field": field_selector[0],
            "quote": quote, "verified": total_matches > 0,
            "occurrences": total_matches, "matches": matches,
            "warnings": warnings, "missing_fields": missing,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "note": "Potrivire exactă în textul vizibil normalizat al câmpului; "
                    "nu confirmă corectitudinea propunerii lingvistice.",
        }

    if action == "audit_ack":
        from app.site_audit import save_reviews
        entries = reviews if reviews is not None else [{"url": url, "sha256": expected_hash}]
        if not isinstance(entries, list) or not 1 <= len(entries) <= 10:
            raise ValueError("audit_ack necesită 1..10 înregistrări")
        saved = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"url", "sha256"}:
                raise ValueError("Fiecare review necesită numai url și sha256")
            item_url, checksum = entry["url"], entry["sha256"]
            _allowed(item_url)
            if item_url in seen:
                raise ValueError("URL duplicat în audit_ack")
            seen.add(item_url)
            if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
                raise ValueError("SHA-256 invalid în audit_ack")
            data = _read_page(item_url)
            content, warnings, _, missing = extract_with_sources(data, selectors)
            if warnings or missing:
                raise ValueError("Nu se poate confirma conținut incomplet: " + item_url)
            current = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if current != checksum:
                raise ValueError("Conținutul s-a schimbat: " + item_url)
            saved.append((item_url, current))
        # This is the only operation that marks a page as reviewed.
        save_reviews(saved, selectors)
        return {
            "action": action, "reviewed": len(saved),
            "fields": list(selectors) if selectors is not None else None,
            "coverage": "selected" if selectors is not None else "all",
            "urls": [item_url for item_url, _ in saved],
            "note": "Confirmare explicită; nu reprezintă validare gramaticală automată.",
        }

    if action == "sitemaps":
        return {"action": action, "sitemaps": _sitemaps()}

    if action == "audit_report":
        if not sitemap or sitemap == "all":
            raise ValueError("audit_report necesită un singur sitemap explicit")
        from app.site_audit import review_coverage
        selected = _sitemap_url(sitemap, _sitemaps())
        urls = _urls(selected)
        eligible, skipped = _index_pages_in_sitemap(selected, urls)
        report = review_coverage(eligible, selectors, offset, limit)
        return {
            "action": action, "sitemap": selected, "total_urls": len(urls),
            "skipped_index_urls": skipped,
            "fields": list(selectors) if selectors is not None else None,
            "coverage": "selected" if selectors is not None else "all",
            **report,
        }

    if action == "urls":
        selected = _sitemap_url(sitemap, _sitemaps())
        urls = _urls(selected)
        subset = urls[offset:offset + limit]
        next_offset = offset + len(subset)
        return {
            "action": action, "sitemap": selected, "total": len(urls),
            "offset": offset, "urls": subset,
            "next_offset": next_offset if next_offset < len(urls) else None,
        }

    if action == "get":
        if not url:
            raise ValueError("Precizează url")
        data = _read_page(url)
        if view == "text":
            text, warnings, spans, missing = extract_with_sources(data, selectors)
            chunks = _page_chunks(text, part, max_chars, expected_hash)
            return {
                "action": action, "url": url, "view": view,
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
            chunks = _page_chunks(payload, part, max_chars, expected_hash)
            if chunks["total_parts"] == 1:
                return {
                    "action": action, **data, "content_complete": True,
                    "total_chars": chunks["total_chars"], "total_parts": 1,
                    "part": 0, "sha256": chunks["sha256"],
                }
            return {
                "action": action, "url": url, "format": "acf_json",
                "encoding": "json", "fragment": chunks["content"],
                **{k: v for k, v in chunks.items() if k != "content"},
            }
        chunks = _page_chunks(data["html"], part, max_chars, expected_hash)
        return {
            "action": action, **{k: v for k, v in data.items() if k != "html"},
            "html": chunks["content"],
            **{k: v for k, v in chunks.items() if k != "content"},
            "truncated": chunks["next_part"] is not None,
        }

    if action in ("batch_get", "audit_changes"):
        if not sitemap or sitemap == "all":
            raise ValueError(action + " necesită un singur sitemap explicit")
        selected = _sitemap_url(sitemap, _sitemaps())
        urls = _urls(selected)
        eligible_urls, skipped_index_urls = _index_pages_in_sitemap(selected, urls)
        batch_limit = min(limit, BATCH_MAX_PAGES)
        current = eligible_urls[offset:offset + batch_limit]
        chars_per_page = min(max_chars, BATCH_CHARS_PER_PAGE)
        if action == "audit_changes":
            from app.site_audit import get_reviews
            baselines = get_reviews(current, selectors)
        else:
            baselines = {}
        pages = []
        unchanged = []
        errors = []
        with ThreadPoolExecutor(max_workers=min(BATCH_WORKERS, max(1, len(current)))) as pool:
            futures = [pool.submit(_batch_page, item, chars_per_page, selectors) for item in current]
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
            "auditable_urls": len(eligible_urls),
            "skipped_index_urls": skipped_index_urls,
            "fields": list(selectors) if selectors is not None else None,
            "coverage": "selected" if selectors is not None else "all",
            "offset": offset, "requested_limit": limit,
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
                "Only audit_ack explicitly marks pages reviewed; unchanged "
                "means unchanged since that exact field selection was approved."
            ),
        }

    if action == "search_many":
        if not isinstance(queries, list) or not 1 <= len(queries) <= 10:
            raise ValueError("search_many necesită queries cu 1..10 termeni")
        if match_mode not in ("exact", "whole_word", "accent_insensitive"):
            raise ValueError("match_mode: exact, whole_word sau accent_insensitive")
        seen_terms = set()
        for term in queries:
            if not isinstance(term, str) or not 1 <= len(term.strip()) <= 120:
                raise ValueError("Fiecare termen trebuie să conțină 1..120 caractere")
            normalized = term.casefold()
            if normalized in seen_terms:
                raise ValueError("Termeni duplicați în queries")
            seen_terms.add(normalized)
            if match_mode == "accent_insensitive" and not _accent_fold_with_positions(term)[0].strip():
                raise ValueError("Termenul nu conține caractere căutabile")
        entries = _search_entries(sitemap)
        batch = entries[offset:offset + min(limit, 20)]
        matches, errors = [], []
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(_search_many_one, item, queries, match_mode) for item in batch]
            for item, future in zip(batch, futures):
                try:
                    result = future.result()
                    if result:
                        matches.append(result)
                except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
                    errors.append({"url": item[0], "error": str(exc)[:200]})
        totals = {
            term: {"query": term, "matching_urls": 0, "occurrences": 0}
            for term in queries
        }
        for result in matches:
            for match in result["terms"]:
                summary = totals[match["query"]]
                summary["matching_urls"] += 1
                summary["occurrences"] += match["occurrences"]
        next_offset = offset + len(batch)
        next_offset = next_offset if next_offset < len(entries) else None
        return {
            "action": action, "queries": queries, "match_mode": match_mode,
            "sitemap": sitemap or "all", "total_urls": len(entries),
            "scanned": len(batch), "offset": offset, "next_offset": next_offset,
            "complete": next_offset is None and not errors,
            "matches": matches, "query_totals": list(totals.values()),
            "totals_scope": "current_batch", "errors": errors,
            "note": "Continue next_offset and inspect errors for full coverage. "
                    "Results are literal text matches, not linguistic conclusions.",
        }

    if not query or not query.strip() or len(query) > 200:
        raise ValueError("query trebuie să conțină între 1 și 200 de caractere")
    entries = _search_entries(sitemap)
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
