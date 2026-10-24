"""Local, bounded OCR for illustrated PDF pages; source text stays untouched.

Use PDF image bounding boxes, nearby native captions and Tesseract TSV word
coordinates. OCR text is separate from literal pdfplumber extraction. No
document-specific layout rules, remote calls or persistent image blobs.
"""
from collections import defaultdict
from io import BytesIO
import csv
import os
from pathlib import Path
import re
import subprocess

import pdfplumber

OCR_VERSION = 1
MAX_FIGURES_PER_PAGE = 12
MIN_IMAGE_AREA = 1500  # pt²; ignore decorative icons
OCR_DPI = 220
OCR_TIMEOUT = 25


def enabled() -> bool:
    return os.getenv("RAG_OCR_ENABLED", "1").strip().lower() in ("1", "true", "yes")


def _figure_regions(page) -> list[dict]:
    regions = []
    for item in page.images:
        x0, x1 = sorted((float(item["x0"]), float(item["x1"])))
        top, bottom = sorted((float(item["top"]), float(item["bottom"])))
        if (x1-x0) * (bottom-top) < MIN_IMAGE_AREA:
            continue
        rect = (max(0, x0), max(0, top), min(float(page.width), x1),
                min(float(page.height), bottom))
        if rect[2] <= rect[0] or rect[3] <= rect[1]:
            continue
        if any(
            abs(r["bbox"][0] - rect[0]) < 3 and
            abs(r["bbox"][1] - rect[1]) < 3 and
            abs(r["bbox"][2] - rect[2]) < 3 and
            abs(r["bbox"][3] - rect[3]) < 3 for r in regions
        ):
            continue
        regions.append({"bbox": rect, "kind": "embedded_image"})
    return sorted(regions, key=lambda r: (r["bbox"][1], r["bbox"][0]))


def _caption(page, bbox) -> str:
    """Nearest caption beneath the image, independent of figure numbering."""
    lines = page.extract_text_lines()
    x0, top, x1, bottom = bbox
    candidates = []
    for line in lines:
        text = line["text"].strip()
        if not re.match(r"^(?:figure|fig\.?|figura|image|diagram|schemă)\s*\d+[\s:.\-–]", text, re.I):
            continue
        distance = float(line["top"]) - bottom
        overlap = max(0.0, min(x1, float(line["x1"])) - max(x0, float(line["x0"])))
        if -8 <= distance <= 65 and overlap > 0:
            candidates.append((abs(distance), text))
    return min(candidates, default=(0, ""))[1]


def _ocr_crop(image, bbox, *, page_width: float, page_height: float) -> tuple[str, float, list[float]]:
    """TSV groups word boxes into lines; bbox returned in original PDF pt."""
    scale = image.width / page_width
    margin = 4.0
    left = max(0, int((bbox[0] - margin) * scale))
    top = max(0, int((bbox[1] - margin) * scale))
    right = min(image.width, int((bbox[2] + margin) * scale))
    bottom = min(image.height, int((bbox[3] + margin) * scale))
    if right <= left or bottom <= top:
        return "", 0.0, list(bbox)
    # No temporary file or disk writes.
    buffer = BytesIO()
    image.crop((left, top, right, bottom)).save(buffer, format="PNG")
    try:
        run = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l",
             os.getenv("RAG_OCR_LANG", "eng"), "--psm", "11", "tsv"],
            input=buffer.getvalue(), capture_output=True, timeout=OCR_TIMEOUT,
            check=False, env={**os.environ, "OMP_THREAD_LIMIT": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Tesseract unavailable or timeout") from exc
    if run.returncode:
        raise RuntimeError("Tesseract OCR failed: " +
                           run.stderr.decode("utf-8", errors="replace")[-250:])
    lines = defaultdict(list)
    positions = []
    for row in csv.DictReader(run.stdout.decode("utf-8", errors="replace").splitlines(),
                              delimiter="\t"):
        word = row.get("text", "").strip()
        try:
            conf = float(row["conf"])
        except (ValueError, KeyError, TypeError):
            continue
        if conf < 15 or not word:
            continue
        key = (int(row["block_num"]), int(row["par_num"]), int(row["line_num"]))
        lines[key].append((int(row["left"]), word, conf, int(row["top"]),
                           int(row["width"]), int(row["height"])))
        positions.append((int(row["left"]), int(row["top"]), int(row["width"]),
                          int(row["height"]), conf))
    ordered = sorted(lines.values(), key=lambda line: (
        min(word[3] for word in line), min(word[0] for word in line)
    ))
    text = "\n".join(
        " ".join(word[1] for word in sorted(line, key=lambda word: word[0]))
        for line in ordered
    )
    confidence = round(sum(p[4] for p in positions) / len(positions), 2) if positions else 0.0
    if positions:
        bounds = [
            max(0, (min(p[0] for p in positions) + left) / scale),
            max(0, (min(p[1] for p in positions) + top) / scale),
            min(page_width, (max(p[0] + p[2] for p in positions) + left) / scale),
            min(page_height, (max(p[1] + p[3] for p in positions) + top) / scale),
        ]
    else:
        bounds = list(bbox)
    return text, confidence, [round(x, 2) for x in bounds]


def extract_pdf_figures(path: Path) -> tuple[list[dict], list[str]]:
    """Return figure annotations plus OCR labels, excluding any PDF text rewrite.

    Each figure is an embedded-image box (or a full page for scanned pages).
    The nearest native caption is linked by geometry. This is a best-effort
    association, explicitly not a guaranteed visual understanding.
    """
    if not enabled():
        return [], []
    figures = []
    warnings = []
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            regions = _figure_regions(page)
            if len(regions) > MAX_FIGURES_PER_PAGE:
                warnings.append(
                    f"Page {page_no}: {len(regions)} images; OCR limited to "
                    f"{MAX_FIGURES_PER_PAGE} largest image regions"
                )
                regions = sorted(regions, key=lambda r: (
                    -(r["bbox"][2]-r["bbox"][0])*(r["bbox"][3]-r["bbox"][1])
                ))[:MAX_FIGURES_PER_PAGE]
                regions.sort(key=lambda r: (r["bbox"][1], r["bbox"][0]))
            if not regions and not (page.extract_text() or "").strip():
                regions = [{
                    "bbox": (0, 0, float(page.width), float(page.height)),
                    "kind": "scanned_page",
                }]
            if not regions:
                continue
            # Render once per page regardless of number of diagrams.
            dpi = min(OCR_DPI, 72 * 3000 / float(page.width),
                      72 * 4000 / float(page.height))
            if dpi <= 0:
                warnings.append(f"Page {page_no}: invalid page dimensions")
                continue
            image = page.to_image(resolution=dpi).original.convert("RGB")
            for ordinal, region in enumerate(regions, start=1):
                bbox = region["bbox"]
                caption = _caption(page, bbox)
                text, confidence, ocr_bbox = _ocr_crop(
                    image, bbox, page_width=float(page.width),
                    page_height=float(page.height),
                )
                if not text.strip() and not caption:
                    continue
                figures.append({
                    "page": page_no, "ordinal": ordinal,
                    "kind": region["kind"],
                    "bbox": [round(x, 2) for x in bbox],
                    "caption": caption,
                    "ocr_text": text,
                    "ocr_bbox": ocr_bbox,
                    "ocr_confidence": confidence,
                })
    return figures, warnings
