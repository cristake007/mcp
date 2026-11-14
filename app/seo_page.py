"""Source-faithful public HTML metadata extraction for course SEO analysis.

Uses the existing fixed-host site transport. This does not scrape Google,
execute JavaScript, infer ranking factors or modify WordPress.
"""
import json
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from app import site_client as site

COURSE_PATH = re.compile(r"^/cursuri/[a-z0-9]+(?:-[a-z0-9]+)*/$")


def validate_course_url(url: str) -> str:
    """Only exact HTTPS course permalinks on the company host."""
    site.allowed(url)
    parsed = urlsplit(url)
    if (len(url) > 500 or parsed.query or parsed.fragment
            or COURSE_PATH.fullmatch(parsed.path) is None):
        raise ValueError("URL-ul trebuie să fie un permalink exact /cursuri/slug/")
    return url


def _meta(soup: BeautifulSoup, key: str, attr: str = "name") -> tuple[str | None, int]:
    matches = [
        tag for tag in soup.find_all("meta")
        if str(tag.get(attr, "")).casefold() == key.casefold()
    ]
    values = [
        str(tag.get("content", "")).strip()
        for tag in matches if str(tag.get("content", "")).strip()
    ]
    return (values[0] if values else None, len(matches))


def _schema(soup: BeautifulSoup) -> tuple[list[str], int, list[str], list[str]]:
    """Read JSON-LD type names and presence of Course properties, not inferred content."""
    type_names = set()
    course_fields = set()
    course_count = 0
    warnings = []

    def visit(node, depth: int = 0):
        nonlocal course_count
        if depth > 10:
            return
        if isinstance(node, list):
            for entry in node[:100]:
                visit(entry, depth + 1)
        elif isinstance(node, dict):
            types = node.get("@type", [])
            types = [types] if isinstance(types, str) else types
            if isinstance(types, list):
                for typ in types:
                    if isinstance(typ, str):
                        type_names.add(typ[:100])
                        if typ.lower() == "course":
                            course_count += 1
                            course_fields.update(k for k in (
                                "name", "description", "provider", "educationalCredentialAwarded",
                                "hasCourseInstance", "offers", "coursePrerequisites",
                            ) if k in node)
            if "@graph" in node:
                visit(node["@graph"], depth + 1)

    scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
    for script in scripts[:30]:
        raw = script.string if script.string is not None else script.get_text()
        if len(raw.encode("utf-8")) > 200_000:
            warnings.append("Bloc JSON-LD prea mare; omis")
            continue
        try:
            visit(json.loads(raw))
        except (ValueError, TypeError, RecursionError):
            warnings.append("Bloc JSON-LD invalid; omis")
    if len(scripts) > 30:
        warnings.append("Peste 30 de blocuri JSON-LD; restul au fost omise")
    return sorted(type_names), course_count, sorted(course_fields), warnings


def extract_metadata(url: str, html: str) -> dict:
    """Parse raw HTML head plus rendered-in-source headings; no JS evaluation."""
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.find("title")
    title = title_tag.get_text(" ", strip=True) if title_tag else None
    description, description_count = _meta(soup, "description")
    robots, robots_count = _meta(soup, "robots")
    googlebot, _ = _meta(soup, "googlebot")
    og_title, _ = _meta(soup, "og:title", "property")
    og_description, _ = _meta(soup, "og:description", "property")
    canonicals = [
        str(tag.get("href", "")).strip()
        for tag in soup.find_all("link", href=True)
        if "canonical" in [str(x).lower() for x in (tag.get("rel") or [])]
    ]
    headings = [
        {"level": int(tag.name[1]), "text": tag.get_text(" ", strip=True)[:300]}
        for tag in soup.find_all(re.compile(r"^h[1-6]$"))
    ]
    types, course_count, course_fields, warnings = _schema(soup)
    if description_count > 1:
        warnings.append("Mai multe meta description în HTML")
    if robots_count > 1:
        warnings.append("Mai multe meta robots în HTML")
    if len(canonicals) > 1:
        warnings.append("Mai multe linkuri canonical în HTML")
    if not title:
        warnings.append("Elementul HTML title lipsește sau este gol")
    return {
        "url": url,
        "source": "public_html",
        "html_lang": (soup.html.get("lang") or None) if soup.html else None,
        "title": title,
        "meta_description": description,
        "meta_robots": robots,
        "meta_googlebot": googlebot,
        "canonical": canonicals[0] if canonicals else None,
        "og_title": og_title,
        "og_description": og_description,
        "h1_count": sum(row["level"] == 1 for row in headings),
        "headings_total": len(headings),
        "headings": headings[:35],
        "headings_truncated": len(headings) > 35,
        "jsonld_types": types,
        "course_jsonld_count": course_count,
        "course_jsonld_fields": course_fields,
        "warnings": warnings,
        "note": (
            "Date observate în HTML-ul public la momentul cererii; "
            "nu sunt rezultate Google, volum de căutare, scor SEO, "
            "DOM după execuția JavaScript sau antete HTTP X-Robots-Tag."
        ),
    }


@site.refresh_scope
def inspect_page(url: str, force_refresh: bool = False) -> dict:
    """Fetch course HTML through the shared HTTPS allowlist and parse metadata."""
    validate_course_url(url)
    if type(force_refresh) is not bool:
        raise ValueError("force_refresh trebuie să fie boolean")
    return extract_metadata(url, site.fetch(url, "text/html,application/xhtml+xml"))
