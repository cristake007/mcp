"""Literal Unicode text matching shared by site and course searches.

Offsets always refer to the original Python string, never the normalized text.
Matching is intentionally lexical, not semantic or linguistic verification.
"""
import re
import unicodedata


def accent_fold_with_positions(value: str) -> tuple[str, list[int]]:
    """Remove combining accents while mapping folded characters to source offsets."""
    folded: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(value):
        normalized = "".join(
            part for part in unicodedata.normalize("NFD", char)
            if unicodedata.category(part) != "Mn"
        ).casefold()
        for part in normalized:
            folded.append(part)
            positions.append(index)
    return "".join(folded), positions


class TextMatcher:
    """Precompute normalized content once for multiple terms."""

    def __init__(self, text: str, mode: str = "contains") -> None:
        if mode not in ("contains", "exact", "whole_word", "accent_insensitive"):
            raise ValueError("Unsupported match mode")
        self.text = text
        self.mode = mode
        self.folded, self.source_positions = (
            accent_fold_with_positions(text)
            if mode == "accent_insensitive" else ("", [])
        )

    def positions(self, term: str) -> list[tuple[int, int]]:
        """Return [start, end) offsets into the unmodified input."""
        if self.mode != "accent_insensitive":
            pattern = re.escape(term)
            if self.mode == "whole_word":
                pattern = r"(?<!\w)" + pattern + r"(?!\w)"
            return [match.span() for match in re.finditer(
                pattern, self.text, re.IGNORECASE,
            )]
        needle, _ = accent_fold_with_positions(term)
        if not needle:
            return []
        hits: list[tuple[int, int]] = []
        start = 0
        while True:
            index = self.folded.find(needle, start)
            if index < 0:
                return hits
            hits.append((
                self.source_positions[index],
                self.source_positions[index + len(needle) - 1] + 1,
            ))
            start = index + len(needle)


def find_positions(text: str, term: str, mode: str) -> list[tuple[int, int]]:
    """Convenience function for single-term matching."""
    return TextMatcher(text, mode).positions(term)
