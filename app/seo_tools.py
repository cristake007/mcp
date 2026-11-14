"""Focused read-only SEO tools for private course-page research.

No Google Keyword Planner, Trends, external SERP scraper or ranking promises.
The GSC query tool requires the existing private MCP_GSC_ENABLED overlay.
"""
from datetime import date
from typing import Any, TypedDict

from app import gsc_client
from app.gsc_tools import _date
from app.seo_page import inspect_page, validate_course_url


class SeoHeading(TypedDict):
    level: int
    text: str


class SeoPageResult(TypedDict):
    url: str
    source: str
    html_lang: str | None
    title: str | None
    meta_description: str | None
    meta_robots: str | None
    meta_googlebot: str | None
    canonical: str | None
    og_title: str | None
    og_description: str | None
    h1_count: int
    headings_total: int
    headings: list[SeoHeading]
    headings_truncated: bool
    jsonld_types: list[str]
    course_jsonld_count: int
    course_jsonld_fields: list[str]
    warnings: list[str]
    note: str


class SeoQueryRow(TypedDict):
    query: str
    clicks: float
    impressions: float
    ctr: float
    position: float


class SeoTotals(TypedDict):
    clicks: float
    impressions: float
    ctr: float
    position: float


class SeoQueryResult(TypedDict):
    url: str
    property: str
    start_date: str
    end_date: str
    page_data_present: bool
    totals: SeoTotals | None
    returned_queries: int
    queries: list[SeoQueryRow]
    note: str


def seo_inspect_page(url: str, force_refresh: bool = False) -> SeoPageResult:
    """Inspect actual public HTML SEO metadata for one TÜV course page.

    Returns HTML title, description, robots, canonical, H1-H6 and JSON-LD
    type/field presence. No WordPress credentials, Google credentials or
    new paid service. No changes are made to the page.
    """
    return inspect_page(url, force_refresh)


def _metrics(row: Any, expected_key: str | None = None) -> dict:
    if (not isinstance(row, dict)
            or not isinstance(row.get("keys"), list)
            or len(row["keys"]) != 1
            or not isinstance(row["keys"][0], str)
            or (expected_key is not None and row["keys"][0] != expected_key)
            or any(type(row.get(key)) not in (int, float)
                   for key in ("clicks", "impressions", "ctr", "position"))):
        raise RuntimeError("Google GSC a returnat un rând SEO invalid")
    return {key: float(row[key]) for key in ("clicks", "impressions", "ctr", "position")}


def seo_course_queries(
    url: str,
    start_date: str,
    end_date: str,
    limit: int = 50,
) -> SeoQueryResult:
    """Read exact-page Google Search Console queries and independently reported totals.

    Uses the existing restricted Google service account. The page filter
    is EQUALS (not substring contains), preventing sibling course mixing.
    Totals come from Google's page-dimension report, NOT the sum of query
    rows; anonymized/rare queries may be missing. No external SEO data.
    """
    validate_course_url(url)
    start, end = _date(start_date), _date(end_date)
    if start > end or (end - start).days > 365 or end > date.today():
        raise ValueError("Perioada SEO trebuie să fie până la 366 zile, fără date viitoare")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit trebuie să fie 1..100")
    base = {
        "startDate": start_date, "endDate": end_date,
        "type": "web", "dataState": "final",
        "dimensionFilterGroups": [{
            "filters": [{"dimension": "page", "operator": "equals", "expression": url}],
        }],
    }
    raw_page = gsc_client.search_analytics({
        **base, "dimensions": ["page"], "rowLimit": 1, "startRow": 0,
    })
    page_rows = raw_page.get("rows", [])
    if not isinstance(page_rows, list) or len(page_rows) > 1:
        raise RuntimeError("Google GSC a returnat totaluri invalide")
    totals = _metrics(page_rows[0], expected_key=url) if page_rows else None
    raw_queries = gsc_client.search_analytics({
        **base, "dimensions": ["query"], "rowLimit": limit, "startRow": 0,
    })
    query_rows = raw_queries.get("rows", [])
    if not isinstance(query_rows, list) or len(query_rows) > limit:
        raise RuntimeError("Google GSC a returnat interogări invalide")
    queries = []
    for row in query_rows:
        metrics = _metrics(row)
        queries.append({"query": row["keys"][0], **metrics})
    return {
        "url": url, "property": gsc_client.property_url(),
        "start_date": start_date, "end_date": end_date,
        "page_data_present": totals is not None, "totals": totals,
        "returned_queries": len(queries), "queries": queries,
        "note": (
            "Performanță Google Search web raportată pentru URL-ul exact; "
            "datele recente pot întârzia. Interogările pot omite căutări anonimizate "
            "și nu se însumează pentru a reconstitui totalurile paginii. "
            "Poziția este medie, CTR este fracție (0..1). "
            "Nu sunt date Keyword Planner, volum lunar de căutare, "
            "clasament SERP live sau estimări de concurență."
        ),
    }
