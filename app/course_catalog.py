"""WordPress course taxonomy/catalog helpers shared by existing MCP tools.

Taxonomy term IDs define domain membership; term archive links are not used.
Every domain selection is reconciled with both WordPress count and the public
course sitemap. Partial data fail closed instead of silently omitting courses.
"""
import json
from urllib.parse import quote

from app import site_client as site
from app.site_sitemaps import index_pages_in_sitemap
from app.site_wordpress import slug as url_slug
from app.html_text import visible_text
from app.course_integrity import course_identity, page_size_for_count
from app.wp_api import iter_collection, read_json

ROOT = site.ROOT
COURSE_SITEMAP = ROOT + "cursuri-sitemap.xml"
COURSE_API = ROOT + "wp-json/wp/v2/cursuri"
DOMAIN_API = ROOT + "wp-json/wp/v2/domenii"
FIELDS = "id,slug,link,title,domenii,acf"
PAGE_SIZE = 100


def course_urls() -> list[str]:
    if COURSE_SITEMAP not in site.sitemaps():
        raise RuntimeError("Sitemap-ul public al cursurilor nu există")
    urls, _ = index_pages_in_sitemap(
        COURSE_SITEMAP, site.urls(COURSE_SITEMAP))
    return [url for url in urls if url.startswith(ROOT + "cursuri/")
            and url_slug(url) != "cursuri"]


def domains() -> list[dict]:
    """Retrieve taxonomy terms using native WordPress REST pagination."""
    terms = []
    seen = set()
    for entries in iter_collection(
        site.fetch, DOMAIN_API, fields="id,name,slug,count", max_pages=50,
    ):
        for entry in entries:
            if (not isinstance(entry, dict)
                    or type(entry.get("id")) is not int
                    or type(entry.get("count")) is not int
                    or entry["count"] < 0
                    or not isinstance(entry.get("name"), str)
                    or not isinstance(entry.get("slug"), str)
                    or entry["id"] in seen):
                raise RuntimeError("Termen de domeniu invalid sau duplicat")
            seen.add(entry["id"])
            terms.append({key: entry[key] for key in ("id", "name", "slug", "count")})
    return terms


def resolve_domain(domain: str | int) -> dict:
    if type(domain) is int and domain > 0:
        matches = [t for t in domains() if t["id"] == domain]
    elif isinstance(domain, str) and domain.strip():
        value = domain.strip().casefold()
        matches = [t for t in domains()
                   if t["slug"].casefold() == value or t["name"].casefold() == value]
    else:
        raise ValueError("domain trebuie să fie ID, slug sau nume de domeniu")
    if len(matches) != 1:
        raise ValueError("Domeniu inexistent sau ambiguu: " + str(domain)[:100])
    return matches[0]


def course_summary(item: dict) -> dict:
    course_id, link = course_identity(item)
    if not isinstance(item.get("acf"), (dict, list)):
        raise ValueError("Curs fără ACF valid")
    return {
        "id": course_id, "slug": item["slug"], "url": link,
        "title": visible_text(item["title"]["rendered"]),
        "domenii": item["domenii"],
    }


def domain_items(domain: str | int) -> tuple[dict, list[dict]]:
    """Domain members from native WP filtering, reconciled with sitemap/count."""
    term = resolve_domain(domain)
    urls = course_urls()
    allowed = set(urls)
    found = {}
    ids = set()
    page_size = page_size_for_count(term["count"])
    for entries in iter_collection(
        site.fetch, COURSE_API, fields=FIELDS,
        filters=(("domenii", term["id"]),),
        page_size=page_size, max_pages=50,
    ):
        for item in entries:
            info = course_summary(item)
            if term["id"] not in info["domenii"]:
                raise RuntimeError("REST API a returnat un curs din alt domeniu")
            if info["url"] not in allowed:
                raise RuntimeError("Cursul din domeniu lipsește din sitemap: " + info["url"])
            if info["url"] in found or info["id"] in ids:
                raise RuntimeError("Curs duplicat în REST API: " + info["url"])
            found[info["url"]] = item
            ids.add(info["id"])
            if len(found) > term["count"]:
                raise RuntimeError("REST API a returnat mai multe cursuri decât declară domeniul")
    if len(found) != term["count"]:
        raise RuntimeError(
            f"Domeniul {term['slug']} declară {term['count']} cursuri, "
            f"dar REST API a returnat {len(found)}; nu se poate afirma completitudinea.")
    return term, [found[url] for url in urls if url in found]


def domain_urls(domain: str | int) -> tuple[dict, list[str]]:
    term, items = domain_items(domain)
    return term, [item["link"] for item in items]


def get_course(course: str | int) -> dict:
    if type(course) is int and course > 0:
        endpoint = f"{COURSE_API}/{course}?_fields={FIELDS}"
        item = read_json(site.fetch, endpoint)
        if not isinstance(item, dict) or item.get("id") != course:
            raise ValueError("REST API a returnat alt curs")
    elif isinstance(course, str) and 1 <= len(course.strip()) <= 250:
        slug = course.strip()
        endpoint = f"{COURSE_API}?slug={quote(slug, safe='')}&_fields={FIELDS}"
        entries = json.loads(site.fetch(endpoint, "application/json"))
        if not isinstance(entries, list) or len(entries) != 1:
            raise ValueError("Curs inexistent sau ambiguu")
        item = entries[0]
        if item.get("slug") != slug:
            raise ValueError("REST API a returnat alt slug")
    else:
        raise ValueError("course trebuie să fie ID WordPress sau slug")
    info = course_summary(item)
    if info["url"] not in set(course_urls()):
        raise RuntimeError("Cursul nu se află în sitemapul public")
    return {**info, "acf": item["acf"]}
