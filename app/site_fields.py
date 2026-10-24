"""Loss-aware field projection for WordPress/ACF page text.

A field selector is a structured source path (not a language filter).
Offsets are Unicode code-point positions in extracted visible text.
"""
import re
from collections.abc import Iterable

from app.site_inspector import _visible_text


def normalize_fields(fields: list[str] | None) -> tuple[str, ...] | None:
    if fields is None:
        return None
    if not isinstance(fields, list) or not 1 <= len(fields) <= 20:
        raise ValueError("fields trebuie să fie o listă cu 1..20 câmpuri sau null")
    result = []
    for item in fields:
        if not isinstance(item, str):
            raise ValueError("Selectorul trebuie să fie text")
        candidate = item if item == "title" or item.startswith("acf.") else "acf." + item
        if not re.fullmatch(r"title|acf\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+|\[\d+\])*", candidate):
            raise ValueError("Selector fields invalid: " + item[:80])
        if candidate not in result:
            result.append(candidate)
    return tuple(result)


def selected(path: str, selectors: tuple[str, ...] | None) -> bool:
    return selectors is None or any(
        path == field or path.startswith(field + ".") or path.startswith(field + "[")
        for field in selectors
    )


def extract_with_sources(data: dict, fields: tuple[str, ...] | None = None) -> tuple[str, list[str], list[dict], list[str]]:
    """Return text, warnings, source spans and missing requested fields.

    Spans identify normalized visible text, not byte positions in raw HTML.
    """
    warnings = []
    sections = []

    def add(path, raw):
        if not selected(path, fields):
            return
        value = _visible_text(raw)
        if value:
            sections.append((path, value))

    if data["format"] != "acf_json":
        add("html.body", data.get("html", ""))
        if not sections:
            warnings.append("Niciun text găsit în HTML; verifică pagina publică.")
    else:
        add("title", data.get("title", ""))

        def traverse(value, path):
            if isinstance(value, dict):
                for key, child in value.items():
                    traverse(child, path + "." + str(key))
            elif isinstance(value, list):
                for i, child in enumerate(value):
                    traverse(child, path + "[" + str(i) + "]")
            elif isinstance(value, str):
                add(path, value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                add(path, str(value))

        if data.get("acf") is None:
            warnings.append("ACF absent în REST API; conținutul poate fi incomplet.")
        else:
            traverse(data["acf"], "acf")
            if not data["acf"]:
                warnings.append("ACF este gol în REST API.")

    paths = [path for path, _ in sections]
    missing = [field for field in (fields or ()) if not any(
        path == field or path.startswith(field + ".") or path.startswith(field + "[")
        for path in paths
    )]
    chunks = []
    spans = []
    cursor = 0
    for path, text in sections:
        if chunks:
            chunks.append("\n\n")
            cursor += 2
        prefix = "[" + path + "]\n" if data["format"] == "acf_json" else ""
        chunks.append(prefix)
        cursor += len(prefix)
        start = cursor
        chunks.append(text)
        cursor += len(text)
        spans.append({"field": path, "start": 0, "end": len(text),
                      "content_start": start, "content_end": cursor})
    return "".join(chunks), warnings, spans, missing


def slice_sources(spans: list[dict], part: int, size: int, content_len: int) -> list[dict]:
    """Map each returned chunk interval back to exact field character offsets."""
    low, high = part * size, part * size + content_len
    result = []
    for entry in spans:
        start = max(low, entry["content_start"])
        end = min(high, entry["content_end"])
        if end <= start:
            continue
        result.append({
            "field": entry["field"],
            "start": entry["start"] + start - entry["content_start"],
            "end": entry["start"] + end - entry["content_start"],
            "content_start": start - low,
            "content_end": end - low,
        })
    return result
