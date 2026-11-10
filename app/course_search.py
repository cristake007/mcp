"""Fast, read-only search of the published TÜV course catalog.

Only the public course sitemap and WordPress course REST collection are used.
The result is exhaustive only if every sitemap course was read successfully.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, NotRequired, TypedDict

from app import site_inspector as site
from app.site_fields import extract_with_sources

COURSE_SITEMAP = site.ROOT + "cursuri-sitemap.xml"
COURSE_API = site.ROOT + "wp-json/wp/v2/cursuri"
PAGE_SIZE = 100
MAX_RECOVERY = 12


class CoursePosition(TypedDict):
    start: int
    end: int


class CourseFieldMatch(TypedDict):
    field: str
    occurrences: int
    positions: list[CoursePosition]
    snippets: list[str]


class CourseTermMatch(TypedDict):
    query: str
    occurrences: int
    fields: list[CourseFieldMatch]


class CourseMatch(TypedDict):
    url: str
    title: str
    terms: list[CourseTermMatch]


class CourseQueryTotal(TypedDict):
    query: str
    matching_courses: int
    occurrences: int


class CourseError(TypedDict):
    url: str
    error: str


class CourseSearchResult(TypedDict):
    queries: list[str]
    match_mode: Literal["contains", "whole_word", "accent_insensitive"]
    sitemap: str
    total_courses: int
    scanned_courses: int
    matching_courses: int
    complete: bool
    matches: list[CourseMatch]
    query_totals: list[CourseQueryTotal]
    errors: list[CourseError]
    missing_courses_count: int
    missing_urls: list[str]
    note: str
    action: NotRequired[str]
    domain: NotRequired[dict]
    domains: NotRequired[list[dict]]
    total_domains: NotRequired[int]
    courses: NotRequired[list[dict]]
    course: NotRequired[dict]


def _validate_queries(queries: list[str], match_mode: str) -> list[str]:
    if not isinstance(queries, list) or not 1 <= len(queries) <= 10:
        raise ValueError("queries trebuie să fie o listă cu 1..10 termeni")
    if match_mode not in ("contains", "whole_word", "accent_insensitive"):
        raise ValueError("match_mode: contains, whole_word sau accent_insensitive")
    normalized = []
    seen = set()
    for query in queries:
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 120:
            raise ValueError("Fiecare termen trebuie să conțină 1..120 caractere")
        term = query.strip()
        if term.casefold() in seen:
            raise ValueError("Termeni duplicați în queries")
        seen.add(term.casefold())
        normalized.append(term)
    return normalized


def _positions(value: str, term: str, mode: str) -> list[tuple[int, int]]:
    if mode == "accent_insensitive":
        folded, source_positions = site._accent_fold_with_positions(value)
        needle, _ = site._accent_fold_with_positions(term)
        if not needle:
            return []
        found = []
        start = 0
        while True:
            index = folded.find(needle, start)
            if index < 0:
                break
            found.append((source_positions[index],
                          source_positions[index + len(needle) - 1] + 1))
            start = index + len(needle)
        return found
    pattern = re.escape(term)
    if mode == "whole_word":
        pattern = r"(?<!\w)" + pattern + r"(?!\w)"
    return [match.span() for match in re.finditer(pattern, value, re.IGNORECASE)]


def _match_course(url: str, item: dict, queries: list[str], match_mode: str) -> CourseMatch | None:
    data = {
        "format": "acf_json",
        "title": item["title"]["rendered"],
        "acf": item["acf"],
    }
    content, warnings, spans, missing = extract_with_sources(data)
    if warnings or missing:
        raise ValueError("Date incomplete în câmpurile WordPress/ACF")
    terms = []
    for query in queries:
        fields = []
        occurrences = 0
        for span in spans:
            value = content[span["content_start"]:span["content_end"]]
            hits = _positions(value, query, match_mode)
            if not hits:
                continue
            occurrences += len(hits)
            fields.append({
                "field": span["field"], "occurrences": len(hits),
                "positions": [{"start": a, "end": b} for a, b in hits[:3]],
                "snippets": [
                    value[max(0, a - 90):min(len(value), b + 90)]
                    for a, b in hits[:3]
                ],
            })
        if fields:
            terms.append({"query": query, "occurrences": occurrences, "fields": fields})
    if not terms:
        return None
    return {"url": url, "title": site._visible_text(item["title"]["rendered"]),
            "terms": terms}


def _collection(sitemap_urls: list[str]) -> dict[str, dict]:
    """Fetch the REST collection in ~2 requests for a 195-course catalog.

    Never infer completeness from WordPress pagination alone: the sitemap is
    authoritative for the searched set, and missing entries are recovered by URL.
    """
    allowed = set(sitemap_urls)
    catalog = {}
    max_pages = (len(allowed) + PAGE_SIZE - 1) // PAGE_SIZE + 5
    for page in range(1, max_pages + 1):
        url = f"{COURSE_API}?per_page={PAGE_SIZE}&page={page}&_fields=id,link,title,acf,domenii,slug"
        items = json.loads(site._fetch(url, "application/json"))
        if not isinstance(items, list) or len(items) > PAGE_SIZE:
            raise RuntimeError("REST API cursuri a returnat o colecție invalidă")
        for item in items:
            if not isinstance(item, dict):
                continue
            link = item.get("link")
            if isinstance(link, str) and link in allowed:
                catalog[link] = item
        if len(catalog) == len(allowed) or len(items) < PAGE_SIZE:
            return catalog
    raise RuntimeError("REST API cursuri a depășit limita de paginare sigură")


def _recover(url: str, refresh: bool) -> dict:
    data, _ = site._load_page(url, refresh, exact_url=True)
    if data.get("format") != "acf_json" or data.get("link") != url:
        raise ValueError("REST API nu corespunde cursului din sitemap")
    return {"link": url, "title": {"rendered": data.get("title", "")},
            "acf": data.get("acf")}


def _search_original(
    queries: list[str],
    match_mode: Literal["contains", "whole_word", "accent_insensitive"] = "contains",
    force_refresh: bool = False,
) -> CourseSearchResult:
    """Search ALL published courses by keyword, standard, title or ACF content.

    ALWAYS prefer search_courses over inspect_site for questions asking which
    courses contain a text, ISO standard, keyword, phrase, or how many match.
    Searches ONLY cursuri-sitemap.xml, never other site pages or archives.
    One call scans the entire course catalog, not just the first batch.
    Multiple terms are OR-matched. For inconsistent ISO/TS formatting, search
    a common identifier (e.g. queries=["29001"]) instead of full exact phrases.
    'contains' is literal case-insensitive substring, 'whole_word' adds Unicode
    word boundaries, and 'accent_insensitive' ignores accents/diacritics.
    Reports actual field paths, occurrences and excerpts without rewriting text.
    complete=false means missing/failed source data: NEVER claim exhaustive
    results then. force_refresh bypasses the MCP sitemap cache and requests
    HTTP revalidation; it cannot guarantee an origin response through a CDN.
    """
    if type(force_refresh) is not bool:
        raise ValueError("force_refresh trebuie să fie boolean")
    queries = _validate_queries(queries, match_mode)
    if COURSE_SITEMAP not in site._sitemaps():
        raise RuntimeError("Sitemap-ul cursurilor nu există în indexul public")
    urls, _ = site._index_pages_in_sitemap(COURSE_SITEMAP, site._urls(COURSE_SITEMAP))
    # Guard against non-course URLs inserted into the public sitemap.
    urls = [url for url in urls if url.startswith(site.ROOT + "cursuri/")
            and site._slug(url) != "cursuri"]
    errors: list[CourseError] = []
    try:
        catalog = _collection(urls)
    except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
        catalog = {}
        errors.append({"url": COURSE_API, "error": "Colecția REST indisponibilă: " + str(exc)[:200]})

    unresolved = [url for url in urls if url not in catalog
                  or not isinstance(catalog[url].get("acf"), (dict, list))
                  or not isinstance(catalog[url].get("title"), dict)
                  or not isinstance(catalog[url]["title"].get("rendered"), str)]
    if unresolved:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [(url, pool.submit(_recover, url, force_refresh))
                       for url in unresolved[:MAX_RECOVERY]]
            for url, future in futures:
                try:
                    catalog[url] = future.result()
                except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
                    catalog.pop(url, None)
                    errors.append({"url": url, "error": str(exc)[:200]})

    matches = []
    scanned = 0
    totals = {term: {"query": term, "matching_courses": 0, "occurrences": 0}
              for term in queries}
    for url in urls:
        item = catalog.get(url)
        if item is None:
            continue
        try:
            result = _match_course(url, item, queries, match_mode)
            scanned += 1
        except (ValueError, KeyError, TypeError) as exc:
            errors.append({"url": url, "error": str(exc)[:200]})
            continue
        if result is None:
            continue
        matches.append(result)
        for term in result["terms"]:
            totals[term["query"]]["matching_courses"] += 1
            totals[term["query"]]["occurrences"] += term["occurrences"]

    missing = [url for url in urls if url not in catalog]
    if len(unresolved) > MAX_RECOVERY:
        errors.append({"url": COURSE_API, "error": (
            f"{len(unresolved)} cursuri au necesitat recuperare; "
            f"limita de siguranță este {MAX_RECOVERY}. Rezultat incomplet."
        )})
    complete = scanned == len(urls) and not errors
    return {
        "queries": queries, "match_mode": match_mode,
        "sitemap": COURSE_SITEMAP, "total_courses": len(urls),
        "scanned_courses": scanned, "matching_courses": len(matches),
        "complete": complete, "matches": matches,
        "query_totals": list(totals.values()), "errors": errors,
        "missing_courses_count": len(missing),
        "missing_urls": missing[:20],
        "note": (
            "Căutare literală în titlu și toate câmpurile ACF vizibile. "
            "Pozițiile sunt caractere Unicode în fiecare câmp (end exclusiv). "
            "Sunt afișate maximum 3 fragmente per câmp; numărul de apariții "
            "este integral. complete=false înseamnă că rezultatul NU este exhaustiv. "
            "Conținutul JavaScript neprezent în REST API nu este verificat."
        ),
    }

# Domain membership is resolved through the WordPress taxonomy, not text
# matching in a title, sitemap URL, or a broken taxonomy archive permalink.
from app import course_catalog as catalog


def _catalog_result(action: str, match_mode: str = "contains") -> CourseSearchResult:
    return {
        "action": action, "queries": [], "match_mode": match_mode,
        "sitemap": COURSE_SITEMAP, "total_courses": 0,
        "scanned_courses": 0, "matching_courses": 0,
        "complete": True, "matches": [], "query_totals": [],
        "errors": [], "missing_courses_count": 0,
        "missing_urls": [], "note": "",
    }


def _list_catalog_courses(domain: str | int | None) -> CourseSearchResult:
    result = _catalog_result("list_courses")
    if domain is None:
        urls = catalog.course_urls()
        items = _collection(urls)
        result["total_courses"] = len(urls)
        ordered = [items[url] for url in urls if url in items]
    else:
        term, ordered = catalog.domain_items(domain)
        result["domain"] = term
        result["total_courses"] = term["count"]
    records = []
    errors = []
    for item in ordered:
        try:
            records.append(catalog.course_summary(item))
        except (ValueError, TypeError) as exc:
            errors.append({"url": str(item.get("link", "")) if isinstance(item, dict) else "",
                           "error": str(exc)[:200]})
    result["courses"] = records
    result["scanned_courses"] = len(records)
    result["missing_courses_count"] = max(0, result["total_courses"] - len(records))
    result["errors"] = errors
    result["complete"] = not errors and len(records) == result["total_courses"]
    result["note"] = ("Lista folosește asocierile din taxonomia WordPress 'domenii'. "
                      "Pentru ACF integral folosește action='get_course'.")
    return result


def _search_catalog_domain(
    queries: list[str], match_mode: str, domain: str | int
) -> CourseSearchResult:
    terms = _validate_queries(queries, match_mode)
    selected, items = catalog.domain_items(domain)
    result = _catalog_result("search", match_mode)
    totals = {query: {"query": query, "matching_courses": 0, "occurrences": 0}
              for query in terms}
    matches = []
    errors = []
    scanned = 0
    for item in items:
        url = item["link"]
        try:
            found = _match_course(url, item, terms, match_mode)
            scanned += 1
        except (ValueError, KeyError, TypeError) as exc:
            errors.append({"url": url, "error": str(exc)[:200]})
            continue
        if found:
            matches.append(found)
            for term in found["terms"]:
                totals[term["query"]]["matching_courses"] += 1
                totals[term["query"]]["occurrences"] += term["occurrences"]
    result.update({
        "queries": terms, "domain": selected, "total_courses": selected["count"],
        "scanned_courses": scanned, "matching_courses": len(matches),
        "matches": matches, "query_totals": list(totals.values()),
        "errors": errors,
        "missing_courses_count": max(0, selected["count"] - scanned),
        "complete": not errors and scanned == selected["count"],
        "note": ("Căutare textuală limitată la membrii reali ai domeniului. "
                 "complete=false înseamnă rezultat incomplet."),
    })
    return result


@site._refresh_scope
def search_courses(
    queries: list[str] | None = None,
    match_mode: Literal["contains", "whole_word", "accent_insensitive"] = "contains",
    force_refresh: bool = False,
    action: Literal["search", "list_domains", "list_courses", "get_course"] = "search",
    domain: str | int | None = None,
    course: str | int | None = None,
) -> CourseSearchResult:
    """Navigate and search the public WordPress course catalog.

    Default action='search' preserves existing calls with queries=['29001'].
    action='list_domains' returns taxonomy IDs, names, slugs and WP counts.
    action='list_courses', optionally domain='ergonomie' or domain=123, returns
    all course IDs, titles, URLs and domain IDs, with pagination done server-side.
    action='get_course', course=WordPress ID or slug, returns full ACF.
    action='search' can restrict text search to a domain without false matches
    from unrelated courses mentioning its name. No external TÜV websites.
    complete=false indicates partial data, never claim exhaustive coverage.
    force_refresh attempts revalidation but cannot guarantee CDN origin.
    """
    if type(force_refresh) is not bool:
        raise ValueError("force_refresh trebuie să fie boolean")
    if action == "list_domains":
        if queries is not None or domain is not None or course is not None:
            raise ValueError("list_domains nu acceptă queries, domain sau course")
        result = _catalog_result(action)
        result["domains"] = catalog.domains()
        result["total_domains"] = len(result["domains"])
        result["note"] = "Taxonomia WordPress domenii, nu arhivele din sitemap."
        return result
    if action == "list_courses":
        if queries is not None or course is not None:
            raise ValueError("list_courses nu acceptă queries sau course")
        return _list_catalog_courses(domain)
    if action == "get_course":
        if course is None or domain is not None or queries is not None:
            raise ValueError("get_course necesită course=ID sau slug, fără alte filtre")
        item = catalog.get_course(course)
        result = _catalog_result(action)
        result["total_courses"] = result["scanned_courses"] = 1
        result["course"] = item
        result["note"] = "Cursul și ACF provin din WordPress REST API."
        return result
    if action != "search":
        raise ValueError("Acțiune necunoscută: " + str(action))
    if course is not None:
        raise ValueError("Pentru ID/slug folosește action='get_course'")
    if domain is None:
        return _search_original(queries, match_mode, force_refresh)
    return _search_catalog_domain(queries, match_mode, domain)
