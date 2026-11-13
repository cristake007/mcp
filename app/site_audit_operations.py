"""Audit use cases, independent of MCP tool registration and network transport.

The caller supplies allowlisted site operations. All audit persistence stays in
app.site_audit; only an explicit acknowledgement may write review baselines.
"""
import hashlib
import re
from collections.abc import Callable

from app import site_audit
from app.site_fields import extract_with_sources, normalize_fields
from app.site_sitemaps import index_pages_in_sitemap
from app.site_text import page_chunks


def quote_matches(text: str, spans: list[dict], quote: str, cap: int | None = None):
    """Return exact matches with offsets relative to their original ACF field."""
    matches = []
    total = 0
    for span in spans:
        value = text[span["content_start"]:span["content_end"]]
        cursor = 0
        while True:
            pos = value.find(quote, cursor)
            if pos < 0:
                break
            total += 1
            if cap is None or len(matches) < cap:
                matches.append({
                    "field": span["field"], "start": pos, "end": pos + len(quote),
                    "context": value[max(0, pos - 80):pos + len(quote) + 80],
                })
            cursor = pos + 1 if cap is None else pos + len(quote)
    return total, matches


def _matching_identity(data: dict, requested_url: str) -> None:
    """REST slug lookups alone cannot prove the document's canonical URL."""
    if data.get("link", requested_url) != requested_url:
        raise ValueError("URL-ul REST nu corespunde exact URL-ului observației")


def verify_findings(
    findings: list[dict] | None,
    selectors: tuple[str, ...] | None,
    force_refresh: bool,
    *,
    allowed: Callable[[str], str],
    load_page: Callable,
) -> dict:
    """Verify literal source text; fetch each distinct page once per request."""
    if not isinstance(findings, list) or not 1 <= len(findings) <= 100:
        raise ValueError("verify_findings necesită 1..100 observații")
    pages = {}
    results = []
    for finding in findings:
        result = {"status": "error"}
        if isinstance(finding, dict):
            result.update({k: finding[k] for k in (
                "url", "field", "original", "replacement", "expected_hash") if k in finding})
        try:
            if not isinstance(finding, dict):
                raise ValueError("Observația trebuie să fie un obiect")
            item_url = allowed(finding.get("url"))
            field = finding.get("field")
            normalized = normalize_fields([field])
            if normalized[0] != field:
                raise ValueError("field trebuie să fie calea exactă din source_map")
            original = finding.get("original")
            if not isinstance(original, str) or not 1 <= len(original) <= 10000:
                raise ValueError("original trebuie să conțină 1..10000 caractere")
            if not isinstance(finding.get("replacement"), str):
                raise ValueError("replacement trebuie să fie text")
            expected = finding.get("expected_hash", "")
            if not isinstance(expected, str) or (expected and not re.fullmatch(r"[0-9a-f]{64}", expected)):
                raise ValueError("expected_hash invalid")
            if item_url not in pages:
                try:
                    pages[item_url] = load_page(item_url, force_refresh, exact_url=True)
                except (RuntimeError, ValueError) as exc:
                    pages[item_url] = exc
            page = pages[item_url]
            if isinstance(page, Exception):
                raise page
            data, metadata = page
            result.update(metadata)
            _matching_identity(data, item_url)
            full, warnings, spans, _ = extract_with_sources(data)
            content, hash_warnings, _, missing = extract_with_sources(data, selectors)
            result.update(
                sha256=page_chunks(content, 0, max(1, len(content)))["sha256"],
                warnings=warnings + hash_warnings,
                missing_fields=missing,
            )
            if warnings or hash_warnings or missing or data.get("content_complete") is False:
                raise ValueError("Sursa nu a putut fi citită integral")
            exact = [span for span in spans if span["field"] == field]
            count, matches = quote_matches(full, exact, original)
            result.update(occurrences=count, matches=matches)
            if expected and expected != result["sha256"]:
                result["status"] = "content_changed"
            elif not exact:
                result["status"] = "field_not_found"
            else:
                result["status"] = "verified" if count else "not_found"
        except (ValueError, RuntimeError, TypeError, KeyError) as exc:
            result.update(status="error", error=str(exc)[:300])
        results.append(result)
    return {
        "action": "verify_findings", "results": results,
        "fields": list(selectors) if selectors is not None else None,
        "note": "verified confirmă numai prezența exactă în textul vizibil al câmpului; "
                "nu confirmă corectitudinea lingvistică. Hash-ul folosește selecția fields "
                "din get(view='text'). Pozițiile sunt caractere Unicode, cu end exclusiv.",
    }


def verify_quote(
    url: str, field: str, quote: str, *, read_page: Callable,
) -> dict:
    """Check one quotation against a named source field."""
    if not url:
        raise ValueError("verify_quote necesită url")
    field_selector = normalize_fields([field])
    if not isinstance(quote, str) or not 1 <= len(quote) <= 500:
        raise ValueError("quote trebuie să conțină 1..500 caractere")
    data = read_page(url)
    text, warnings, spans, missing = extract_with_sources(data, field_selector)
    total_matches, matches = quote_matches(text, spans, quote, cap=20)
    return {
        "action": "verify_quote", "url": url, "field": field_selector[0],
        "quote": quote, "verified": total_matches > 0,
        "occurrences": total_matches, "matches": matches,
        "warnings": warnings, "missing_fields": missing,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "note": "Potrivire exactă în textul vizibil normalizat al câmpului; "
                "nu confirmă corectitudinea propunerii lingvistice.",
    }


def acknowledge(
    url: str,
    expected_hash: str,
    reviews: list[dict] | None,
    selectors: tuple[str, ...] | None,
    *,
    allowed: Callable,
    load_page: Callable,
    force_refresh: bool = False,
) -> dict:
    """Atomically record only exact, live-matching page hashes.

    Unlike the original implementation, an exact course URL is verified via
    the same canonical REST identity check as verify_findings. A slug match
    with a different canonical link can never be acknowledged.
    """
    entries = reviews if reviews is not None else [{"url": url, "sha256": expected_hash}]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 10:
        raise ValueError("audit_ack necesită 1..10 înregistrări")
    saved = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"url", "sha256"}:
            raise ValueError("Fiecare review necesită numai url și sha256")
        item_url, checksum = entry["url"], entry["sha256"]
        allowed(item_url)
        if item_url in seen:
            raise ValueError("URL duplicat în audit_ack")
        seen.add(item_url)
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ValueError("SHA-256 invalid în audit_ack")
        data, _ = load_page(item_url, force_refresh, exact_url=True)
        _matching_identity(data, item_url)
        content, warnings, _, missing = extract_with_sources(data, selectors)
        if warnings or missing or data.get("content_complete") is False:
            raise ValueError("Nu se poate confirma conținut incomplet: " + item_url)
        current = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if current != checksum:
            raise ValueError("Conținutul s-a schimbat: " + item_url)
        saved.append((item_url, current))
    # Only the explicit acknowledgement path writes review baselines.
    site_audit.save_reviews(saved, selectors)
    return {
        "action": "audit_ack", "reviewed": len(saved),
        "fields": list(selectors) if selectors is not None else None,
        "coverage": "selected" if selectors is not None else "all",
        "urls": [item_url for item_url, _ in saved],
        "note": "Confirmare explicită; nu reprezintă validare gramaticală automată.",
    }


def audit_report(
    sitemap: str,
    selectors: tuple[str, ...] | None,
    offset: int,
    limit: int,
    *,
    sitemaps: Callable,
    sitemap_url: Callable,
    urls: Callable,
) -> dict:
    """Read persisted baselines without implying a new live-site review."""
    if not sitemap or sitemap == "all":
        raise ValueError("audit_report necesită un singur sitemap explicit")
    selected = sitemap_url(sitemap, sitemaps())
    all_urls = urls(selected)
    eligible, skipped = index_pages_in_sitemap(selected, all_urls)
    report = site_audit.review_coverage(eligible, selectors, offset, limit)
    return {
        "action": "audit_report", "sitemap": selected,
        "total_urls": len(all_urls), "skipped_index_urls": skipped,
        "fields": list(selectors) if selectors is not None else None,
        "coverage": "selected" if selectors is not None else "all",
        **report,
    }
