"""Small WordPress REST client built around the native pagination API.

The HTTP transport is injected so site security, cache/revalidation controls
and existing offline tests all use the same source of truth.
"""
import json
from collections.abc import Callable, Iterator
from urllib.parse import urlencode

JsonFetch = Callable[[str, str], str]


def read_json(fetch: JsonFetch, url: str):
    """Decode a WordPress REST response without changing the HTTP transport."""
    return json.loads(fetch(url, "application/json"))


def iter_collection(
    fetch: JsonFetch,
    endpoint: str,
    *,
    fields: str,
    filters: tuple[tuple[str, str | int], ...] = (),
    page_size: int = 100,
    max_pages: int = 50,
) -> Iterator[list[dict]]:
    """Read native REST pages, stopping at the first short page.

    Consumers remain responsible for completeness checks against the sitemap
    or taxonomy count, and may stop early when their expected set is complete.
    """
    if not 1 <= page_size <= 100 or max_pages < 1:
        raise ValueError("Invalid WordPress REST pagination limits")
    for page in range(1, max_pages + 1):
        query = urlencode(
            [*filters, ("per_page", page_size), ("page", page), ("_fields", fields)],
            safe=",",
        )
        entries = read_json(fetch, f"{endpoint}?{query}")
        if not isinstance(entries, list) or len(entries) > page_size:
            raise RuntimeError("Invalid WordPress REST collection response")
        yield entries
        if len(entries) < page_size:
            return
    raise RuntimeError("WordPress REST pagination exceeded its safety limit")
