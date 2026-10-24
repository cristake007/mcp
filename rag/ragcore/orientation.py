"""Automatically normalize PDF orientation *in memory/on temporary storage*.

Never change PDFs below rag/documents. Most digital PDFs expose reliable text
orientation matrices; Tesseract OSD is a conservative fallback for raster
scans without native text. No document-specific layouts, names or rules.
"""
from collections import Counter
from contextlib import contextmanager
from io import BytesIO
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile

import pdfplumber
from pypdf import PdfReader, PdfWriter

ORIENTATION_VERSION = 1
MIN_GLYPHS = 40
DOMINANCE = 0.72
OSD_MIN_CONFIDENCE = 5.0


def enabled() -> bool:
    return os.getenv("RAG_AUTO_ROTATE_ENABLED", "1").strip().lower() in (
        "1", "yes", "true"
    )


def native_rotation(page) -> int | None:
    """Required clockwise page rotation from dominant native glyph direction.

    pdfplumber character matrices use (a,b) for the text baseline; +90°
    means the PDF needs a 90° clockwise /Rotate to become upright. Unlike
    Tesseract OSD, this is independent of language and layout.
    """
    directions = Counter()
    for char in page.chars:
        if not char.get("text", "").strip():
            continue
        matrix = char.get("matrix")
        if not matrix or len(matrix) < 2:
            continue
        a, b = float(matrix[0]), float(matrix[1])
        if a * a + b * b < 1e-6:
            continue
        raw = (math.degrees(math.atan2(b, a)) + 360) % 360
        target = (int(round(raw / 90)) * 90) % 360
        delta = abs((raw - target + 180) % 360 - 180)
        if delta <= 10:
            directions[target] += 1
    if sum(directions.values()) < MIN_GLYPHS:
        return None
    angle, count = directions.most_common(1)[0]
    if count / sum(directions.values()) < DOMINANCE:
        return None
    return angle


def scanned_rotation(page) -> int | None:
    """Low-cost optional Tesseract OSD on raster pages without native text.

    OSD's 'Rotate' value specifies the correction. Low confidence means
    leave the page unchanged rather than introduce an incorrect rotation.
    """
    try:
        resolution = min(
            120.0, 72.0 * 1600 / float(page.width),
            72.0 * 1600 / float(page.height),
        )
        if resolution < 8:
            return None
        image = page.to_image(resolution=resolution).original
        stream = BytesIO()
        image.save(stream, format="PNG", dpi=(int(resolution), int(resolution)))
        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", "0", "-l", "osd"],
            input=stream.getvalue(), capture_output=True, check=False,
            timeout=20, env={**os.environ, "OMP_THREAD_LIMIT": "1"},
        )
        if result.returncode:
            return None
        response = result.stdout.decode("utf-8", errors="replace")
        match = re.search(r"(?m)^Rotate:\s*(0|90|180|270)\s*$", response)
        confidence = re.search(
            r"(?m)^Orientation confidence:\s*([\d.]+)\s*$", response
        )
        if not match or not confidence or float(confidence.group(1)) < OSD_MIN_CONFIDENCE:
            return None
        return int(match.group(1))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def detect_rotations(path: Path) -> dict[int, int]:
    """Return 1-based page -> clockwise correction. Missing keys mean 0°."""
    result = {}
    with pdfplumber.open(path) as doc:
        for page_number, page in enumerate(doc.pages, start=1):
            direction = native_rotation(page)
            # A handful of graphic labels is NOT enough evidence to rotate
            # an entire page. OSD only runs when native text is scarce.
            if direction is None and len(page.chars) < MIN_GLYPHS:
                direction = scanned_rotation(page)
            if direction in (90, 180, 270):
                result[page_number] = direction
    return result


@contextmanager
def normalized_pdf(path: Path):
    """Yield (temporary_read_path, page_rotations), preserving original bytes.

    Temporary copy is made only if at least one page requires correction.
    The original document hash remains the stable index identity.
    """
    if not enabled():
        yield path, {}
        return
    rotations = detect_rotations(path)
    if not rotations:
        yield path, {}
        return
    with tempfile.TemporaryDirectory(prefix="rag-upright-") as directory:
        destination = Path(directory) / "normalized.pdf"
        reader = PdfReader(str(path), strict=False)
        writer = PdfWriter()
        for page_number, page in enumerate(reader.pages, start=1):
            copied = writer.add_page(page)
            angle = rotations.get(page_number, 0)
            if angle:
                copied.rotate(angle)
        with destination.open("wb") as stream:
            writer.write(stream)
        yield destination, rotations
