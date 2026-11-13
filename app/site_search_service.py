"""Site-wide lexical search use cases without MCP registration or HTTP ownership.

Readers and sitemap discovery are injected. This preserves the exact source
matching, bounded parallelism, pagination and contextvars cache-refresh state.
The published search contracts remain backward compatible.
"""
import contextvars
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from app.site_text import as_search_text
from app.text_matching import TextMatcher, accent_fold_with_positions


def search_one(
    entry: tuple[str, str], query: str, *, read_page: Callable,
) -> dict | None:
    """Legacy single-term search with original non-overlapping count semantics."""
    url, sitemap = entry
    content = as_search_text(read_page(url))
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


def search_many_one(
    entry: tuple[str, str], queries: list[str], match_mode: str, *, read_page: Callable,
) -> dict | None:
    """Read one page once and reuse a normalized Unicode matcher for all terms."""
    url, sitemap = entry
    content = as_search_text(read_page(url))
    matcher = TextMatcher(content, match_mode)
    found = []
    for query in queries:
        hits = matcher.positions(query)
        if not hits:
            continue
        found.append({
            "query": query, "occurrences": len(hits),
            "positions": [{"start": start, "end": end} for start, end in hits[:3]],
            "snippets": [
                content[max(0, start - 90): min(len(content), end + 90)]
                for start, end in hits[:3]
            ],
        })
    if not found:
        return None
    return {"url": url, "sitemap": sitemap, "terms": found}


def _scan_batch(
    entries: list[tuple[str, str]],
    callback: Callable,
    *arguments,
) -> tuple[list[dict], list[dict]]:
    """Bounded parallel reads; copy context separately into each worker thread."""
    matches, errors = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(contextvars.copy_context().run, callback, entry, *arguments)
            for entry in entries
        ]
        for entry, future in zip(entries, futures):
            try:
                match = future.result()
                if match:
                    matches.append(match)
            except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
                errors.append({"url": entry[0], "error": str(exc)[:200]})
    return matches, errors


def search_many(
    queries: list[str] | None,
    match_mode: str,
    sitemap: str,
    offset: int,
    limit: int,
    *,
    list_entries: Callable,
    inspect_one: Callable,
) -> dict:
    """Paginated multi-term search with totals scoped to the current batch."""
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
        if match_mode == "accent_insensitive" and not accent_fold_with_positions(term)[0].strip():
            raise ValueError("Termenul nu conține caractere căutabile")
    entries = list_entries(sitemap)
    batch = entries[offset:offset + min(limit, 20)]
    matches, errors = _scan_batch(batch, inspect_one, queries, match_mode)
    totals = {
        term: {"query": term, "matching_urls": 0, "occurrences": 0}
        for term in queries
    }
    for match in matches:
        for term in match["terms"]:
            summary = totals[term["query"]]
            summary["matching_urls"] += 1
            summary["occurrences"] += term["occurrences"]
    next_offset = offset + len(batch)
    next_offset = next_offset if next_offset < len(entries) else None
    return {
        "action": "search_many", "queries": queries, "match_mode": match_mode,
        "sitemap": sitemap or "all", "total_urls": len(entries),
        "scanned": len(batch), "offset": offset, "next_offset": next_offset,
        "complete": next_offset is None and not errors,
        "matches": matches, "query_totals": list(totals.values()),
        "totals_scope": "current_batch", "errors": errors,
        "note": "Continue next_offset and inspect errors for full coverage. "
                "Results are literal text matches, not linguistic conclusions.",
    }


def search_legacy(
    query: str, sitemap: str, offset: int, limit: int,
    *, list_entries: Callable, inspect_one: Callable,
) -> dict:
    """Legacy single-term response shape, preserving its historical semantics."""
    if not query or not query.strip() or len(query) > 200:
        raise ValueError("query trebuie să conțină între 1 și 200 de caractere")
    entries = list_entries(sitemap)
    batch = entries[offset:offset + min(limit, 20)]
    matches, errors = _scan_batch(batch, inspect_one, query)
    next_offset = offset + len(batch)
    next_offset = next_offset if next_offset < len(entries) else None
    return {
        "action": "search", "query": query, "sitemap": sitemap or "all",
        "total_urls": len(entries), "scanned": len(batch), "offset": offset,
        "next_offset": next_offset,
        "complete": next_offset is None and not errors,
        "matches": matches, "errors": errors,
    }
