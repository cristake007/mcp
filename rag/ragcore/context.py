"""Lossless (relative to indexed raw_text) contextual reading for any RAG file type.

Search chunks remain small for ranking. This reader slices the unmodified
indexed document, with explicit absolute character intervals and continuation
cursors. Optional lexical structure hints never replace source text.
"""
import hashlib
import re


def _line_kind(line: str) -> str:
    """Conservative line-shape hints, not semantic claims about the document."""
    visible = line.strip()
    if not visible:
        return "blank"
    if re.match(r"^[-*•]\s+", visible):
        return "bullet_candidate"
    if re.match(r"^(?:\d{1,4}(?:\.\d{1,4})*[.)]?|[A-Za-z][.)])\s+", visible):
        return "numbered_item_candidate"
    if ("\t" in visible or visible.startswith("|")) and len(visible) < 500:
        return "table_like"
    if (len(visible) <= 110 and visible.upper() == visible
            and any(char.isalpha() for char in visible)):
        return "heading_candidate"
    return "text_line"


def _blocks(content: str, start: int) -> list[dict]:
    """Partition the entire returned text, including blank lines and separators."""
    cursor = start
    blocks = []
    for line in content.splitlines(keepends=True):
        end = cursor + len(line)
        blocks.append({
            "char_start": cursor, "char_end": end,
            "kind": _line_kind(line), "text": line,
        })
        cursor = end
    assert cursor == start + len(content)
    return blocks


def read_context(raw_text: str, *, document_id: int, path: str, parser: str,
                 warnings: list[str], start: int | None = None,
                 end: int | None = None, offset: int = 0,
                 limit: int = 12000, context_chars: int = 4000,
                 expected_hash: str = "") -> dict:
    """Read a contiguous exact portion, never silently clipping the selected span.

    Without an anchor this paginates the entire indexed document.
    With start/end from a search hit it exposes a clearly bounded context
    window around the hit, not a claim to return the entire document.
    next_offset is an ABSOLUTE character offset to use with the SAME
    anchor and revision hash until null.
    """
    if type(document_id) is not int or document_id < 1:
        raise ValueError("Invalid document_id")
    if (start is None) != (end is None):
        raise ValueError("Provide both start and end, or neither")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if type(limit) is not int or not 1000 <= limit <= 16000:
        raise ValueError("limit must be between 1000 and 16000")
    if type(context_chars) is not int or not 0 <= context_chars <= 20000:
        raise ValueError("context_chars must be between 0 and 20000")
    if not isinstance(expected_hash, str) or (
        expected_hash and not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
    ):
        raise ValueError("expected_hash must be an exact lowercase SHA-256")
    total = len(raw_text)
    if start is None:
        range_start, range_end = 0, total
        scope = "full_document"
    else:
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= total:
            raise ValueError("Anchor positions must be within indexed text")
        range_start = max(0, start - context_chars)
        range_end = min(total, end + context_chars)
        # Expand to source line boundaries, not arbitrary 750-character chunks.
        previous_break = raw_text.rfind("\n", 0, range_start)
        range_start = previous_break + 1
        following_break = raw_text.find("\n", range_end)
        range_end = total if following_break < 0 else following_break + 1
        scope = "hit_context"
    digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    if expected_hash and expected_hash != digest:
        raise ValueError("Indexed document revision changed; restart from offset=0")
    # Zero means first page for either scope. Subsequent cursors are absolute.
    cursor = range_start if offset == 0 else offset
    if not range_start <= cursor <= range_end:
        raise ValueError("offset outside selected context; restart from offset=0")
    page_end = min(range_end, cursor + limit)
    content = raw_text[cursor:page_end]
    continuation = page_end if page_end < range_end else None
    blocks = _blocks(content, cursor)
    return {
        "document_id": document_id,
        "path": path,
        "parser": parser,
        "scope": scope,
        "text": content,
        "blocks": blocks,
        "char_start": cursor,
        "char_end": page_end,
        "range_start": range_start,
        "range_end": range_end,
        "range_chars": range_end - range_start,
        "document_chars": total,
        "sha256": digest,
        "next_offset": continuation,
        "range_complete": continuation is None,
        "full_document_selected": range_start == 0 and range_end == total,
        "exact_indexed_text": True,
        "warnings": warnings,
        "original_file_completeness_guaranteed": False,
        "note": (
            "Text and line-shaped blocks are exact copies of indexed raw_text; "
            "line kinds are heuristic. Read every next_offset with the same "
            "anchor and expected_hash before claiming the selected range is complete. "
            "Hit context is bounded and may exclude other relevant sections. "
            "Parser warnings and non-text images can indicate content missing "
            "from the initial extraction; do not claim complete source-file coverage."
        ),
    }
