"""Small, task-oriented MCP catalog tools.

The public contract does not expose a polymorphic "action" parameter.
The legacy search_courses action interface remains available while existing
clients migrate; both routes call the same catalog/search implementations.
"""
from typing import Literal, NotRequired, TypedDict

from app import course_catalog as catalog
from app import course_search as legacy
from app import site_inspector as site


class Domain(TypedDict):
    id: int
    name: str
    slug: str
    count: int


class DomainListing(TypedDict):
    total_domains: int
    domains: list[Domain]


class CourseSummary(TypedDict):
    id: int
    slug: str
    url: str
    title: str
    domenii: list[int]


class CourseDetail(CourseSummary):
    acf: dict | list


class CourseListing(TypedDict):
    total_courses: int
    offset: int
    returned: int
    next_offset: int | None
    complete: bool
    missing_courses_count: int
    courses: list[CourseSummary]
    errors: list[legacy.CourseError]
    domain: NotRequired[Domain]


class CourseFindResult(TypedDict):
    total_courses: int
    scanned_courses: int
    matching_courses: int
    complete: bool
    missing_courses_count: int
    missing_urls: list[str]
    matches: list[legacy.CourseMatch]
    errors: list[legacy.CourseError]
    domain: NotRequired[Domain]


@site._refresh_scope
def list_domains(force_refresh: bool = False) -> DomainListing:
    """List the genuine WordPress 'domenii' taxonomy with IDs and course counts.

    Domain membership is NOT inferred from words in course descriptions.
    """
    if type(force_refresh) is not bool:
        raise ValueError("force_refresh must be boolean")
    values = catalog.domains()
    return {"total_domains": len(values), "domains": values}


@site._refresh_scope
def list_courses(
    domain: str | int | None = None,
    offset: int = 0,
    limit: int = 25,
    force_refresh: bool = False,
) -> CourseListing:
    """List published WordPress courses, optionally restricted by real taxonomy.

    Pagination applies to the verified catalog, not to partial REST pages.
    complete=false and missing_courses_count signal source inconsistencies.
    Follow next_offset to traverse the entire list.
    """
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be 1..100")
    if type(force_refresh) is not bool:
        raise ValueError("force_refresh must be boolean")
    result = legacy.search_courses(
        action="list_courses", domain=domain, force_refresh=force_refresh,
    )
    courses = result["courses"]
    subset = courses[offset:offset + limit]
    next_offset = offset + len(subset)
    response: CourseListing = {
        "total_courses": result["total_courses"],
        "offset": offset,
        "returned": len(subset),
        "next_offset": next_offset if next_offset < len(courses) else None,
        "complete": result["complete"],
        "missing_courses_count": result["missing_courses_count"],
        "courses": subset,
        "errors": result["errors"],
    }
    if "domain" in result:
        response["domain"] = result["domain"]
    return response


@site._refresh_scope
def get_course(course: str | int, force_refresh: bool = False) -> CourseDetail:
    """Get one published course by WordPress ID or exact slug, including full ACF."""
    if type(force_refresh) is not bool:
        raise ValueError("force_refresh must be boolean")
    return catalog.get_course(course)


def find_courses(
    query: str,
    domain: str | int | None = None,
    match_mode: Literal["contains", "whole_word", "accent_insensitive"] = "contains",
    force_refresh: bool = False,
) -> CourseFindResult:
    """Find course content with exact source snippets and field positions.

    Query covers title and all available ACF text. To restrict the result,
    supply a real WordPress domain ID/slug, not a domain keyword.
    complete=false means source coverage is incomplete.
    """
    result = legacy.search_courses(
        queries=[query], match_mode=match_mode,
        domain=domain, force_refresh=force_refresh,
    )
    response: CourseFindResult = {
        key: result[key] for key in (
            "total_courses", "scanned_courses", "matching_courses",
            "complete", "missing_courses_count", "missing_urls",
            "matches", "errors",
        )
    }
    if "domain" in result:
        response["domain"] = result["domain"]
    return response
