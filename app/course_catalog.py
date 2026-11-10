"""WordPress course taxonomy/catalog helpers shared by existing MCP tools.

Taxonomy term IDs define domain membership; term archive links are not used.
Every domain selection is reconciled with both WordPress count and the public
course sitemap. Partial data fail closed instead of silently omitting courses.
"""
import json
from urllib.parse import quote

from app import site_inspector as site

ROOT = site.ROOT
COURSE_SITEMAP = ROOT + "cursuri-sitemap.xml"
COURSE_API = ROOT + "wp-json/wp/v2/cursuri"
DOMAIN_API = ROOT + "wp-json/wp/v2/domenii"
FIELDS = "id,slug,link,title,domenii,acf"
PAGE_SIZE = 100


def course_urls() -> list[str]:
    if COURSE_SITEMAP not in site._sitemaps():
        raise RuntimeError("Sitemap-ul public al cursurilor nu există")
    urls, _ = site._index_pages_in_sitemap(
        COURSE_SITEMAP, site._urls(COURSE_SITEMAP))
    return [url for url in urls if url.startswith(ROOT + "cursuri/")
            and site._slug(url) != "cursuri"]


def domains() -> list[dict]:
    """Retrieve all taxonomy terms; never follow term.link (may return 404)."""
    terms = []
    seen = set()
    for page in range(1, 51):
        url = (f"{DOMAIN_API}?per_page={PAGE_SIZE}&page={page}"
               "&_fields=id,name,slug,count")
        entries = json.loads(site._fetch(url, "application/json"))
        if not isinstance(entries, list) or len(entries) > PAGE_SIZE:
            raise RuntimeError("Lista taxonomiei domenii este invalidă")
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
        if len(entries) < PAGE_SIZE:
            return terms
    raise RuntimeError("Prea multe pagini în taxonomia domenii")


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
    if not isinstance(item, dict):
        raise ValueError("Curs invalid")
    link = item.get("link")
    site._allowed(link)
    if (not link.startswith(ROOT + "cursuri/")
            or type(item.get("id")) is not int
            or not isinstance(item.get("slug"), str)
            or not isinstance(item.get("title"), dict)
            or not isinstance(item["title"].get("rendered"), str)
            or not isinstance(item.get("domenii"), list)
            or not all(type(v) is int for v in item["domenii"])
            or not isinstance(item.get("acf"), (dict, list))):
        raise ValueError("Curs fără ID, titlu, domenii sau ACF valid")
    return {
        "id": item["id"], "slug": item["slug"], "url": link,
        "title": site._visible_text(item["title"]["rendered"]),
        "domenii": item["domenii"],
    }


def domain_items(domain: str | int) -> tuple[dict, list[dict]]:
    """Domain members from native WP filter, validated against sitemap/count."""
    term = resolve_domain(domain)
    urls = course_urls()
    allowed = set(urls)
    found = {}
    for page in range(1, 51):
        url = (f"{COURSE_API}?domenii={term['id']}&per_page={PAGE_SIZE}"
               f"&page={page}&_fields={FIELDS}")
        entries = json.loads(site._fetch(url, "application/json"))
        if not isinstance(entries, list) or len(entries) > PAGE_SIZE:
            raise RuntimeError("REST API a returnat o colecție de cursuri invalidă")
        for item in entries:
            info = course_summary(item)
            if term["id"] not in info["domenii"]:
                raise RuntimeError("REST API a returnat un curs din alt domeniu")
            if info["url"] not in allowed:
                raise RuntimeError("Cursul din domeniu lipsește din sitemap: " + info["url"])
            if info["url"] in found:
                raise RuntimeError("Curs duplicat în REST API: " + info["url"])
            found[info["url"]] = item
        if len(found) >= term["count"] or len(entries) < PAGE_SIZE:
            break
    else:
        raise RuntimeError("Limita de paginare pentru cursuri a fost depășită")
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
        item = json.loads(site._fetch(endpoint, "application/json"))
        if not isinstance(item, dict) or item.get("id") != course:
            raise ValueError("REST API a returnat alt curs")
    elif isinstance(course, str) and 1 <= len(course.strip()) <= 250:
        slug = course.strip()
        endpoint = f"{COURSE_API}?slug={quote(slug, safe='')}&_fields={FIELDS}"
        entries = json.loads(site._fetch(endpoint, "application/json"))
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
