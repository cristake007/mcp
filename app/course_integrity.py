"""Integrity invariants for public WordPress courses and REST pagination.

Keep this module pure: it performs no HTTP requests and does not depend on
FastMCP or the legacy site inspector.
"""
from urllib.parse import unquote, urlsplit

from app.site_transport import ROOT, allowed


def page_size_for_count(expected: int, maximum: int = 100) -> int:
    """Choose a page size so a consistent collection ends on a short page.

    WordPress can return HTTP 400 for a page beyond the last page. For counts
    divisible by 100 we instead request 99 (or another non-divisor), which
    lets native pagination confirm the final short page while still detecting
    an extra REST entry beyond the expected count.
    """
    if type(expected) is not int or expected < 0 or type(maximum) is not int or not 2 <= maximum <= 100:
        raise ValueError("Invalid expected count or REST page size")
    if expected == 0:
        return maximum
    for candidate in range(maximum, 1, -1):
        if expected % candidate:
            return candidate
    # An expected count of 2 is divisible by 2 only, not by 3, so this
    # fallback is unreachable for maximum >= 3. Keep a safe small page.
    return 1


def course_identity(item: dict) -> tuple[int, str]:
    """Validate an individual REST course's ID, public URL and slug identity.

    ACF can be missing here: search_courses may recover missing ACF by exact
    canonical URL. Structural identity may not be recovered by overwriting
    a conflicting REST collection record.
    """
    if not isinstance(item, dict):
        raise ValueError("Înregistrare REST curs invalidă")
    course_id, link, slug = item.get("id"), item.get("link"), item.get("slug")
    if type(course_id) is not int or course_id <= 0 or not isinstance(link, str) or not isinstance(slug, str) or not slug:
        raise ValueError("Curs REST fără ID, URL sau slug valid")
    allowed(link)
    parsed = urlsplit(link)
    if (parsed.query or not parsed.path.startswith("/cursuri/")
            or parsed.path.rstrip("/") == "/cursuri"
            or unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1]) != unquote(slug)):
        raise ValueError("Slug-ul cursului nu corespunde URL-ului canonic")
    title = item.get("title")
    domains = item.get("domenii")
    if (not isinstance(title, dict)
            or not isinstance(title.get("rendered"), str)
            or not title["rendered"].strip()
            or not isinstance(domains, list)
            or any(type(v) is not int or v <= 0 for v in domains)
            or len(set(domains)) != len(domains)):
        raise ValueError("Metadate REST invalide pentru curs")
    return course_id, link
