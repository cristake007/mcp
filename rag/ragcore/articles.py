"""Article boundaries in extracted text, preserving literal source offsets.

This is a conservative helper, not a legal-document understanding model.
Only headings at the beginning of a line count; uncertain text is left as-is.
"""
import re
from dataclasses import dataclass

ARTICLE_HEADING = re.compile(
    r"(?im)^[ \t]*(?:Art\.|Articolul)\s*(\d{1,4})(?:\s*\^\s*(\d+))?(?=\s|[.()\-–—:]|$)"
)


@dataclass(frozen=True)
class Article:
    label: str
    start: int
    end: int

    def info(self, *, include_text: str | None = None, max_inline: int = 4500) -> dict:
        result = {
            "label": self.label,
            "char_start": self.start,
            "char_end": self.end,
            "length": self.end - self.start,
        }
        if include_text is not None:
            if self.end - self.start <= max_inline:
                result["text"] = include_text[self.start:self.end]
            else:
                result["text_requires_get_text"] = True
        return result


def articles(raw_text: str) -> list[Article]:
    headings = list(ARTICLE_HEADING.finditer(raw_text))
    results = []
    for i, match in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(raw_text)
        label = match.group(1) + (("^" + match.group(2)) if match.group(2) else "")
        results.append(Article(label, match.start(), end))
    return results


def article_for_hit(raw_text: str, start: int, end: int,
                    query: str = "") -> Article | None:
    """Select an article intersecting a result, preferring lexical query hits.

    Retrieval chunks may cross article boundaries. Only the intersecting
    portion of each article is scored; if no query term matches, largest
    character overlap is a documented fallback, not a semantic guarantee.
    """
    candidates = [item for item in articles(raw_text)
                  if item.start < end and item.end > start]
    if not candidates:
        return None
    terms = {word for word in re.findall(r"[^\\W_]+", query.casefold())
             if len(word) >= 4}
    def score(item: Article) -> tuple[int, int]:
        a = max(start, item.start)
        b = min(end, item.end)
        overlapping_text = raw_text[a:b].casefold()
        return (sum(word in overlapping_text for word in terms), b - a)
    return max(candidates, key=score)


def article_by_label(raw_text: str, label: str) -> Article:
    requested = label.strip().lower()
    requested = re.sub(r"^(?:art\.|articolul)\s*", "", requested)
    matches = [item for item in articles(raw_text) if item.label.lower() == requested]
    if not matches:
        raise ValueError(f"Article {label!r} not found in extracted document text")
    if len(matches) != 1:
        raise ValueError(
            f"Article {label!r} occurs {len(matches)} times; use explicit offsets from search"
        )
    return matches[0]
