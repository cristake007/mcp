"""Character-based chunks with verified complete coverage of extracted text."""
from dataclasses import dataclass

from .parsing import Unit


@dataclass(frozen=True)
class Chunk:
    start: int
    end: int
    text: str
    locations: list[dict]


def make_chunks(raw_text: str, units: list[Unit], max_chars: int = 750,
                overlap: int = 90) -> list[Chunk]:
    if max_chars <= 0 or overlap < 0 or overlap >= max_chars:
        raise ValueError("Invalid chunk size or overlap")
    if not raw_text:
        return []

    chunks: list[Chunk] = []
    start = 0
    length = len(raw_text)
    while start < length:
        end = min(length, start + max_chars)
        # Prefer a paragraph/line ending near the right boundary when possible.
        if end < length:
            boundary = raw_text.rfind("\n", start + int(max_chars * 0.6), end)
            if boundary > start:
                end = boundary + 1
        locations = [unit.source for unit in units
                     if unit.start < end and unit.end > start]
        chunks.append(Chunk(start, end, raw_text[start:end], locations))
        if end == length:
            break
        start = end - overlap
    verify_coverage(raw_text, chunks)
    return chunks


def verify_coverage(raw_text: str, chunks: list[Chunk]) -> None:
    if not raw_text:
        if chunks:
            raise ValueError("Empty document must have no chunks")
        return
    if not chunks:
        raise ValueError("Nonempty document was not chunked")
    covered = 0
    for chunk in chunks:
        if not (0 <= chunk.start < chunk.end <= len(raw_text)):
            raise ValueError("Invalid chunk bounds")
        if chunk.start > covered:
            raise ValueError(f"Uncovered characters {covered}:{chunk.start}")
        if chunk.text != raw_text[chunk.start:chunk.end]:
            raise ValueError("Chunk content differs from literal extracted text")
        covered = max(covered, chunk.end)
    if covered != len(raw_text):
        raise ValueError(f"Uncovered tail after character {covered}")
