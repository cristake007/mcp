"""Compatibility-only MCP dispatcher for the original search_courses tool.

All search, listing and taxonomy domain logic lives in independent services.
New MCP clients call focused tools, which bypass this dispatcher entirely.
"""
from typing import Literal

from app import course_catalog as catalog
from app import course_search_service as engine
from app import site_client as site
from app.course_search_service import (
    CourseError, CourseFieldMatch, CourseMatch, CoursePosition,
    CourseQueryTotal, CourseSearchResult, CourseTermMatch,
)


@site.refresh_scope
def search_courses(
    queries: list[str] | None = None,
    match_mode: Literal["contains", "whole_word", "accent_insensitive"] = "contains",
    force_refresh: bool = False,
    action: Literal["search", "list_domains", "list_courses", "get_course"] = "search",
    domain: str | int | None = None,
    course: str | int | None = None,
) -> CourseSearchResult:
    """Legacy polymorphic tool; preserved for existing client integrations.

    Use list_domains, list_courses, get_course and find_courses for new clients.
    All results retain the historical response schema and completeness flags.
    """
    if type(force_refresh) is not bool:
        raise ValueError("force_refresh trebuie să fie boolean")
    if action == "list_domains":
        if queries is not None or domain is not None or course is not None:
            raise ValueError("list_domains nu acceptă queries, domain sau course")
        result = engine._catalog_result()
        result["action"] = action
        result["domains"] = catalog.domains()
        result["total_domains"] = len(result["domains"])
        result["note"] = "Taxonomia WordPress domenii, nu arhivele din sitemap."
        return result
    if action == "list_courses":
        if queries is not None or course is not None:
            raise ValueError("list_courses nu acceptă queries sau course")
        result = engine.list_catalog_courses(domain=domain, force_refresh=force_refresh)
        result["action"] = action
        return result
    if action == "get_course":
        if course is None or domain is not None or queries is not None:
            raise ValueError("get_course necesită course=ID sau slug, fără alte filtre")
        item = catalog.get_course(course)
        result = engine._catalog_result()
        result["action"] = action
        result["total_courses"] = result["scanned_courses"] = 1
        result["course"] = item
        result["note"] = "Cursul și ACF provin din WordPress REST API."
        return result
    if action != "search":
        raise ValueError("Acțiune necunoscută: " + str(action))
    if course is not None:
        raise ValueError("Pentru ID/slug folosește action='get_course'")
    result = engine.search_catalog(
        queries, match_mode, domain=domain, force_refresh=force_refresh,
    )
    # Historical domain-scoped responses carry action='search', unlike the
    # original global search response; preserve both shapes.
    if domain is not None:
        result["action"] = action
    return result
