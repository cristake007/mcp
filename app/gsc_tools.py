"""Focused private Search Console tools. No modification or indexing endpoints."""
from datetime import date
from typing import Literal, TypedDict

from app import gsc_client


class PerformanceRow(TypedDict):
    value: str
    clicks: float
    impressions: float
    ctr: float
    position: float


class PerformanceResult(TypedDict):
    property: str
    start_date: str
    end_date: str
    dimension: str
    page_filter: str
    offset: int
    returned: int
    rows: list[PerformanceRow]
    note: str


class InspectionResult(TypedDict):
    url: str
    property: str
    verdict: str | None
    coverage_state: str | None
    indexing_state: str | None
    robots_txt_state: str | None
    page_fetch_state: str | None
    google_canonical: str | None
    user_canonical: str | None
    last_crawl_time: str | None
    sitemaps: list[str]
    note: str


def _date(value: str) -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("Datele trebuie să fie YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Dată invalidă: folosește YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError("Datele trebuie să fie YYYY-MM-DD")
    return parsed


def gsc_performance(
    start_date: str,
    end_date: str,
    dimension: Literal["query", "page", "date", "country", "device"] = "query",
    limit: int = 25,
    offset: int = 0,
    page_filter: str = "",
) -> PerformanceResult:
    """Read Google organic search clicks, impressions, CTR and average position.

    Select a date range (YYYY-MM-DD, max 366 days) and one dimension.
    page_filter optionally restricts results to a site path (e.g. '/cursuri/').
    Values are the data Google Search Console reports for the property,
    not independently measured traffic. ctr is a ratio (0..1), not a percent.
    Rows are ordered by Google; results can omit anonymized and long-tail
    queries and must NOT be treated as an exhaustive query inventory.
    Only 'web' search type and final data are requested. Read-only.
    """
    start, end = _date(start_date), _date(end_date)
    if start > end or (end - start).days > 365:
        raise ValueError("Perioada GSC trebuie să fie 1..366 zile calendaristice")
    if end > date.today():
        raise ValueError("Data de sfârșit nu poate fi în viitor")
    if dimension not in ("query", "page", "date", "country", "device"):
        raise ValueError("Dimensiune GSC invalidă")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit trebuie să fie între 1 și 100")
    if type(offset) is not int or not 0 <= offset <= 5000:
        raise ValueError("offset trebuie să fie între 0 și 5000")
    if not isinstance(page_filter, str) or len(page_filter) > 250 or (
        page_filter and (
            not page_filter.startswith("/") or "\r" in page_filter or "\n" in page_filter
        )
    ):
        raise ValueError("page_filter trebuie să fie o cale de site, ca '/cursuri/'")
    payload = {
        "startDate": start_date, "endDate": end_date,
        "dimensions": [dimension], "type": "web",
        "dataState": "final", "rowLimit": limit, "startRow": offset,
    }
    if page_filter:
        payload["dimensionFilterGroups"] = [{
            "filters": [{"dimension": "page", "operator": "contains", "expression": page_filter}],
        }]
    raw = gsc_client.search_analytics(payload)
    rows = raw.get("rows", [])
    if not isinstance(rows, list) or len(rows) > limit:
        raise RuntimeError("Google GSC a returnat rânduri invalide")
    result_rows = []
    for row in rows:
        if (not isinstance(row, dict)
                or not isinstance(row.get("keys"), list)
                or len(row["keys"]) != 1
                or not isinstance(row["keys"][0], str)
                or any(type(row.get(k)) not in (int, float)
                       for k in ("clicks", "impressions", "ctr", "position"))):
            raise RuntimeError("Google GSC a returnat date de performanță invalide")
        result_rows.append({
            "value": row["keys"][0],
            "clicks": float(row["clicks"]),
            "impressions": float(row["impressions"]),
            "ctr": float(row["ctr"]),
            "position": float(row["position"]),
        })
    return {
        "property": gsc_client.property_url(),
        "start_date": start_date, "end_date": end_date,
        "dimension": dimension, "page_filter": page_filter,
        "offset": offset, "returned": len(result_rows),
        "rows": result_rows,
        "note": (
            "Date GSC de tip web, finalizate dacă sunt disponibile. "
            "CTR este fracție (0..1); poziția este medie. "
            "Rândurile pot omite interogări anonimizate sau rare; "
            "lipsa lor nu dovedește absența căutărilor. "
            "Datele recente pot avea întârzieri."
        ),
    }


def gsc_inspect_url(url: str) -> InspectionResult:
    """Read the Google index's last-known status for one tuvkarpat.ro URL.

    This is NOT a live page test, an indexing request or a guarantee the URL
    will appear in AI answers. Restricts inspection to the company's HTTPS host.
    """
    raw = gsc_client.inspect_url(url)
    inspection = raw.get("inspectionResult")
    if not isinstance(inspection, dict):
        raise RuntimeError("Răspunsul GSC nu conține date de inspecție")
    index = inspection.get("indexStatusResult", {})
    if not isinstance(index, dict):
        raise RuntimeError("Răspuns GSC URL Inspection invalid")
    sitemaps = index.get("sitemap", [])
    if not isinstance(sitemaps, list) or not all(isinstance(v, str) for v in sitemaps):
        raise RuntimeError("GSC a returnat sitemapuri invalide")
    return {
        "url": url,
        "property": gsc_client.property_url(),
        "verdict": index.get("verdict"),
        "coverage_state": index.get("coverageState"),
        "indexing_state": index.get("indexingState"),
        "robots_txt_state": index.get("robotsTxtState"),
        "page_fetch_state": index.get("pageFetchState"),
        "google_canonical": index.get("googleCanonical"),
        "user_canonical": index.get("userCanonical"),
        "last_crawl_time": index.get("lastCrawlTime"),
        "sitemaps": sitemaps,
        "note": (
            "Starea indexată cunoscută de Google, NU verificare live; "
            "nu trimite cereri de indexare și nu modifică Search Console."
        ),
    }
