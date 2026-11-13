"""Deterministic source-text preparation and hash-bound pagination.

The parser keeps original Unicode character offsets, while page chunks
are always reconstructed from the same normalized input.
"""
import hashlib
import re

from bs4 import BeautifulSoup


def as_search_text(data: dict) -> str:
    if data["format"] == "acf_json":
        pieces = [data.get("title", "")]
        def collect(value):
            if isinstance(value, dict):
                for nested in value.values():
                    collect(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect(nested)
            elif isinstance(value, str):
                pieces.append(BeautifulSoup(value, "html.parser").get_text(" ", strip=True))
        collect(data.get("acf"))
        return re.sub(r"\s+", " ", " ".join(pieces)).strip()
    soup = BeautifulSoup(data["html"], "html.parser")
    # Menus within Elementor templates are navigation, not page content.
    for nav in soup.select("nav, .elementor-nav-menu"):
        nav.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def page_chunks(content: str, part: int, size: int, expected_hash: str = "") -> dict:
    """Deterministic, reconstructible slices. No characters disappear."""
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if expected_hash and expected_hash != checksum:
        raise ValueError("Conținutul paginii s-a schimbat; reia de la part=0.")
    total_parts = max(1, (len(content) + size - 1) // size)
    if not 0 <= part < total_parts:
        raise ValueError("part este în afara intervalului 0..%d" % (total_parts - 1))
    piece = content[part * size:(part + 1) * size]
    return {
        "content": piece,
        "total_chars": len(content),
        "sha256": checksum,
        "part": part,
        "total_parts": total_parts,
        "next_part": part + 1 if part + 1 < total_parts else None,
        "content_complete": total_parts == 1,
        "part_complete": True,
    }
