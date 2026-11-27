"""Explicit public HTML source, never WordPress REST/ACF or browser visibility."""
import re
from bs4 import BeautifulSoup
from app import site_client as site


def get_public_html(url: str, texts: list[str] | None = None,
                    max_chars: int = 0, offset: int = 0,
                    force_refresh: bool = False) -> dict:
    """Fetch actual public HTML with curl, not REST/ACF. Search literal text in
    HTML text nodes (including CSS-hidden elements); no CSS or JS evaluation.
    By default return compact matches, not raw HTML. Set max_chars to 1..12000
    to obtain a raw HTML excerpt; follow next_offset for additional excerpts.
    Use verify_content_visibility for computed browser visibility when enabled.
    """
    site.allowed(url)
    if len(url) > 1000 or type(max_chars) is not int or not 0 <= max_chars <= 12000:
        raise ValueError("Invalid URL length or max_chars (0..12000)")
    if type(offset) is not int or offset < 0 or type(force_refresh) is not bool:
        raise ValueError("Invalid offset or refresh flag")
    texts = [] if texts is None else texts
    if not isinstance(texts, list) or len(texts) > 10 or any(
        not isinstance(x, str) or not x.strip() or len(x) > 300 for x in texts
    ):
        raise ValueError("Provide at most 10 nonempty literal texts of up to 300 characters")
    data, evidence = site.load_page(
        url, force_refresh=force_refresh,
        reader=lambda u: {"html": site.fetch(u, "text/html,application/xhtml+xml")},
    )
    raw = data["html"]
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup.select("script, style, meta, iframe, noscript, template, head"):
        tag.decompose()
    text = re.sub(r"\s+", " ", (soup.body or soup).get_text(" ", strip=True))
    matches = []
    for query in texts:
        q = re.sub(r"\s+", " ", query.strip())
        hits = list(re.finditer(re.escape(q), text, re.IGNORECASE))
        matches.append({"text": query, "occurrences": len(hits),
                        "snippets": [text[max(0, h.start()-70):h.end()+70] for h in hits[:3]]})
    end = min(len(raw), offset + max_chars)
    return {"url": url, "source": "public_html", "visibility_verified": False,
            "matches": matches, "html_chars": len(raw),
            "html_excerpt": raw[offset:end] if max_chars else "",
            "next_offset": end if max_chars and end < len(raw) else None,
            **evidence,
            "note": "HTML text presence is not computed browser visibility."}
