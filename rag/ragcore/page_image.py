"""Render an indexed PDF page as a bounded image for the optional private RAG API.

Images are produced on demand from original PDF bytes; no image database,
external model, OCR service, generated illustration, or public URL is involved.
"""
import base64
from io import BytesIO
import math
from pathlib import Path

import pdfplumber

from .store import file_digest, init_schema

MAX_PDF_SIZE = 30 * 1024 * 1024
MAX_IMAGE_BYTES = 850_000


def get_page_image(conn, folder: Path, document_id: int, page: int,
                   figure_id: int | None = None) -> dict:
    """Return original PDF page rendering, only for a current indexed source.

    The metadata/byte digest is verified before AND after rendering. This
    operation is read-only and authorizes paths by indexed document ID, not
    by arbitrary user-provided filenames.
    """
    if type(document_id) is not int or document_id < 1:
        raise ValueError("document_id must be a positive integer")
    if type(page) is not int or page < 1:
        raise ValueError("page must be a positive integer")
    if figure_id is not None and (type(figure_id) is not int or figure_id < 1):
        raise ValueError("Invalid figure_id")
    init_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT path, sha256, page_rotations FROM rag_documents WHERE id=%s",
            (document_id,),
        )
        item = cur.fetchone()
        figure = None
        if figure_id is not None:
            cur.execute(
                """SELECT id, bbox, caption FROM rag_figures
                   WHERE id=%s AND document_id=%s AND page=%s""",
                (figure_id, document_id, page),
            )
            figure = cur.fetchone()
            if figure is None:
                raise ValueError("Figure is not indexed for this document and page")
    if item is None:
        raise ValueError("Unknown indexed document ID")
    rel = item["path"]
    if not rel.lower().endswith(".pdf"):
        raise ValueError("Page images are supported only for PDF documents")

    # Never accept the original path from an MCP caller; look it up in SQL.
    # Disallow symlinks and traversal even if an index record was tampered with.
    if folder.is_symlink() or not folder.is_dir():
        raise RuntimeError("RAG documents directory is unavailable")
    root = folder.resolve(strict=True)
    path = root / rel
    if Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise ValueError("Unsafe indexed document path")
    if not path.resolve(strict=False).is_relative_to(root):
        raise ValueError("Indexed document path escapes documents directory")
    current = root
    for segment in Path(rel).parts:
        current = current / segment
        if current.is_symlink():
            raise ValueError("Symlinks are not allowed in document path")
    try:
        before = path.stat()
        if not path.is_file():
            raise FileNotFoundError("Original PDF not found")
        if before.st_size > MAX_PDF_SIZE:
            raise ValueError("Original PDF exceeds the indexing file size limit")
        if file_digest(path) != item["sha256"]:
            raise ValueError("Original PDF differs from indexed revision; reindex it first")
    except FileNotFoundError as exc:
        raise ValueError("Original PDF is missing; check corpus_health") from exc
    except PermissionError as exc:
        raise RuntimeError("Original PDF cannot be read") from exc

    with pdfplumber.open(path) as pdf:
        total_pages = len(pdf.pages)
        if page > total_pages:
            raise ValueError(f"Invalid page {page}; PDF contains {total_pages} pages")
        source = pdf.pages[page - 1]
        clockwise = int(item["page_rotations"].get(str(page), 0))
        if clockwise not in (0, 90, 180, 270):
            raise ValueError("Invalid stored page orientation")
        # Match the *normalized* page aspect ratio for display, even though
        # the original PDF on disk remains unmodified.
        display_width = float(source.height if clockwise in (90, 270) else source.width)
        display_height = float(source.width if clockwise in (90, 270) else source.height)
        bbox = None
        if figure is not None:
            bbox = figure["bbox"]
            if (not isinstance(bbox, list) or len(bbox) != 4
                    or any(type(c) not in (float, int) or not math.isfinite(c)
                           for c in bbox)):
                raise ValueError("Indexed figure has invalid coordinates")
            x0, y0, x1, y1 = bbox
            if not (0 <= x0 < x1 <= display_width + 1
                    and 0 <= y0 < y1 <= display_height + 1):
                raise ValueError("Indexed figure exceeds page bounds")
            # Render at higher resolution for diagram labels, crop after
            # normalization. Rendered *whole page* is still pixel-bounded.
            dpi = min(180.0, 72.0 * 2300 / display_width,
                      72.0 * 2800 / display_height)
        else:
            dpi = min(110.0, 72.0 * 1050 / display_width,
                      72.0 * 1500 / display_height)
        if dpi <= 0 or dpi < 4:
            raise ValueError("PDF page dimensions cannot be rendered safely")
        rendered = source.to_image(resolution=dpi, antialias=True).original.convert("RGB")
        if clockwise:
            # PIL positive degrees are CCW; PDF /Rotate positive is CW.
            rendered = rendered.rotate(-clockwise, expand=True)
        if bbox is not None:
            # Figure coordinates were saved during OCR indexing in the
            # normalized display page coordinate system (points from top).
            scale_x = rendered.width / display_width
            scale_y = rendered.height / display_height
            margin = 7.0  # preserve arrows/borders near image region
            crop = (
                max(0, math.floor((x0 - margin) * scale_x)),
                max(0, math.floor((y0 - margin) * scale_y)),
                min(rendered.width, math.ceil((x1 + margin) * scale_x)),
                min(rendered.height, math.ceil((y1 + margin) * scale_y)),
            )
            if crop[2] - crop[0] < 16 or crop[3] - crop[1] < 16:
                raise ValueError("Indexed figure crop is too small")
            rendered = rendered.crop(crop)
            # Bound image width without distorting aspect ratio.
            if rendered.width > 1600 or rendered.height > 1600:
                rendered.thumbnail((1600, 1600))

    stream = BytesIO()
    for quality in (85, 75, 65, 55):
        stream.seek(0)
        stream.truncate()
        rendered.save(stream, format="JPEG", quality=quality, optimize=True)
        if stream.tell() <= MAX_IMAGE_BYTES:
            break
    if stream.tell() > MAX_IMAGE_BYTES:
        raise ValueError("Rendered page exceeds image size limit")
    image_data = stream.getvalue()

    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size, after.st_mtime_ns, after.st_ino
    ) or file_digest(path) != item["sha256"]:
        raise ValueError("Original PDF changed during image rendering; retry after reindexing")

    return {
        "document_id": document_id,
        "path": rel,
        "page": page,
        "total_pages": total_pages,
        "mime_type": "image/jpeg",
        "image_base64": base64.b64encode(image_data).decode("ascii"),
        "image_width": rendered.width,
        "image_height": rendered.height,
        "source_sha256": item["sha256"],
        "source_verified": True,
        "image_kind": ("cropped_original_pdf_figure" if figure is not None
                       else "rendered_original_pdf_page"),
        "figure_id": figure_id,
        "figure_caption": figure["caption"] if figure is not None else None,
        "orientation_corrected_clockwise": clockwise,
        "note": "Imagine originala PDF randata la cerere, nu imagine generata AI. "
                "Orientarea reflecta normalizarea folosita la indexare. "
                "Continutul vizual nu este indexat semantic; pagina se "
                "alege din referintele returnate de search_docs.",
    }
