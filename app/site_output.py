"""Published MCP output contract for inspect_site.

The inspector has ten actions returning different JSON objects. A required
action discriminator and optional, strongly typed response fields keep one
tool compatible with every action without modifying any response payload.
Nested audit, pagination and source-map records have explicit shapes.
"""
from typing import Any, Literal, NotRequired, TypedDict


SiteAction = Literal[
    "sitemaps", "urls", "get", "batch_get", "search", "search_many",
    "verify_quote", "audit_changes", "audit_ack", "audit_report",
]
SiteCoverage = Literal["selected", "all"]


class SiteSourceSpan(TypedDict):
    field: str
    start: int
    end: int
    content_start: int
    content_end: int


class SiteContinuation(TypedDict):
    action: Literal["get"]
    url: str
    view: Literal["text"]
    part: int
    max_chars: int
    expected_hash: str
    fields: list[str] | None


class SiteBatchPage(TypedDict):
    url: str
    format: Literal["acf_json", "post_html", "page_html"]
    id: int | None
    warnings: list[str]
    missing_fields: list[str]
    source_map: list[SiteSourceSpan]
    content: str
    total_chars: int
    sha256: str
    part: int
    total_parts: int
    next_part: int | None
    content_complete: bool
    part_complete: bool
    retrieve_remaining: SiteContinuation | None
    change_status: NotRequired[Literal["new", "changed"]]


class SitePendingPart(TypedDict):
    url: str
    retrieve_remaining: SiteContinuation


class SiteUnchanged(TypedDict):
    url: str
    sha256: str
    reviewed_at: str


class SiteError(TypedDict):
    url: str
    error: str


class SiteMissingFields(TypedDict):
    url: str
    fields: list[str]


class SiteBatchCoverage(TypedDict):
    requested_urls: int
    fetched_urls: int
    content_returned_urls: int
    previously_acknowledged_unchanged_urls: int
    fully_returned_urls: int
    pending_continuation_urls: list[str]
    warning_urls: list[str]
    missing_fields: list[SiteMissingFields]
    failed_urls: list[str]
    requires_followup_urls: list[str]
    model_reviewed_urls: int | None
    note: str


class SiteMatchPosition(TypedDict):
    start: int
    end: int


class SiteTermMatch(TypedDict):
    query: str
    occurrences: int
    positions: list[SiteMatchPosition]
    snippets: list[str]


class SiteMatch(TypedDict, total=False):
    # verify_quote: field/start/end/context
    field: str
    start: int
    end: int
    context: str
    # search: url/sitemap/occurrences/snippets
    url: str
    sitemap: str
    occurrences: int
    snippets: list[str]
    # search_many: url/sitemap/terms
    terms: list[SiteTermMatch]


class SiteQueryTotal(TypedDict):
    query: str
    matching_urls: int
    occurrences: int


class SiteAuditEntry(TypedDict):
    url: str
    status: Literal["acknowledged_not_live_verified", "not_acknowledged"]
    reviewed_at: str | None


class SiteInputError(TypedDict):
    code: Literal["invalid_parameter"]
    parameter: Literal["offset", "limit", "max_chars", "part", "expected_hash", "view"]
    message: str
    expected: str
    received: str
    retry_with: dict[str, int | str]
    retry_instruction: str


class SiteInspectionResult(TypedDict):
    action: SiteAction
    ok: NotRequired[bool]
    error: NotRequired[SiteInputError]
    # All other fields are action-specific. None are injected or rewritten.
    sitemaps: NotRequired[list[str]]
    sitemap: NotRequired[str]
    url: NotRequired[str]
    urls: NotRequired[list[str]]
    total: NotRequired[int]
    total_urls: NotRequired[int]
    auditable_urls: NotRequired[int]
    offset: NotRequired[int]
    limit: NotRequired[int]
    next_offset: NotRequired[int | None]
    fields: NotRequired[list[str] | None]
    coverage: NotRequired[SiteCoverage]
    skipped_index_urls: NotRequired[list[str]]
    # get raw / text
    view: NotRequired[Literal["raw", "text"]]
    format: NotRequired[Literal["acf_json", "post_html", "page_html"]]
    id: NotRequired[int | None]
    title: NotRequired[str]
    link: NotRequired[str]
    acf: NotRequired[Any]  # JSON ACF content can be nested or null
    html: NotRequired[str]
    encoding: NotRequired[Literal["json"]]
    fragment: NotRequired[str]
    content: NotRequired[str]
    total_chars: NotRequired[int]
    sha256: NotRequired[str]
    part: NotRequired[int]
    total_parts: NotRequired[int]
    next_part: NotRequired[int | None]
    content_complete: NotRequired[bool]
    part_complete: NotRequired[bool]
    truncated: NotRequired[bool]
    source_map: NotRequired[list[SiteSourceSpan]]
    warnings: NotRequired[list[str]]
    missing_fields: NotRequired[list[str]]
    # batch_get / audit_changes
    requested_limit: NotRequired[int]
    processed: NotRequired[int]
    pages: NotRequired[list[SiteBatchPage]]
    unchanged: NotRequired[list[SiteUnchanged]]
    errors: NotRequired[list[SiteError]]
    coverage_report: NotRequired[SiteBatchCoverage]
    end_of_sitemap: NotRequired[bool]
    batch_complete: NotRequired[bool]
    pending_parts: NotRequired[list[SitePendingPart]]
    # search / search_many / verify_quote
    query: NotRequired[str]
    queries: NotRequired[list[str]]
    match_mode: NotRequired[Literal["exact", "whole_word", "accent_insensitive"]]
    scanned: NotRequired[int]
    complete: NotRequired[bool]
    matches: NotRequired[list[SiteMatch]]
    query_totals: NotRequired[list[SiteQueryTotal]]
    totals_scope: NotRequired[Literal["current_batch"]]
    field: NotRequired[str]
    quote: NotRequired[str]
    verified: NotRequired[bool]
    occurrences: NotRequired[int]
    # audit_ack / audit_report
    reviewed: NotRequired[int]
    acknowledged_urls: NotRequired[int]
    not_acknowledged_urls: NotRequired[int]
    entries: NotRequired[list[SiteAuditEntry]]
    status_scope: NotRequired[Literal["persisted_baseline_only"]]
    current_site_freshness_checked: NotRequired[bool]
    extraction_completed_in_this_report: NotRequired[bool]
    note: NotRequired[str]
