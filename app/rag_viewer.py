"""Read-only MCP Apps viewer for pages and OCR figures in the private RAG.

No public URLs, public images, iframe PDF parsers or new database access.
The host fetches a static UI resource; browser page images come *only* from
an app-only MCP tool, itself backed by the SHA-256-verified RAG API.
"""
import asyncio
import base64
from pathlib import Path
import re
import unicodedata
from typing import Callable

from fastmcp import FastMCP

VIEWER_URI = "ui://rag/figure-image/v3.html"
VIEWER_MIME = "text/html;profile=mcp-app"
VIEWER_PATH = Path(__file__).with_name("rag_figure_image_v3.html")


_SEARCH_STOPWORDS = frozenset(
    "ce care cum are este sunt avea despre pentru acest aceasta aparatul "
    "aparat device equipment the with from show display figure image poza "
    "imagine imagini foto me mi arata arate sa și si din de la pe o un unei "
    "in în ale al a cu folosind tell how what is does can please "
    "manual document poza fotografii".split()
)


def _search_terms(value: str) -> set[str]:
    """Conservative bilingual lexical evidence, independent of PDF layouts."""
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(c for c in value if not unicodedata.combining(c))
    result = set()
    for word in re.findall(r"[a-z0-9]+", value):
        if word in _SEARCH_STOPWORDS or len(word) < 3:
            continue
        # Shared subject stems (RO/EN) useful when Romanian questions
        # reference English technical figures. No document-specific names.
        if word.startswith(("conector", "connector")):
            word = "connector"
        elif word.startswith(("activare", "activat", "activati", "activating",
                              "activation", "activate")):
            word = "activation"
        elif word.startswith(("licent", "licens", "licenc")):
            word = "license"
        elif word.startswith(("buton", "button")):
            word = "button"
        elif word.startswith(("conect", "connect")):
            word = "connection"
        result.add(word)
    return result


def prepare_sources(results: list[dict], max_pages: int, query: str = "") -> dict:
    """Select ONE strongly related indexed figure, never noisy OCR matches.

    User questions in Romanian may reference figures captioned in English.
    Both the caption and the OCR text must collectively corroborate the
    question. Raw vector retrieval rank alone never authorizes an image.
    """
    sources, candidates, seen = [], [], set()
    query_terms = _search_terms(query)
    for index, record in enumerate(results[:12]):
        locations = record.get("locations") or []
        figure = record.get("figure") or {}
        page = figure.get("page") or next(
            (location.get("page") for location in locations
             if location.get("kind") in ("pdf_page", "pdf_ocr")
             and type(location.get("page")) is int), None
        )
        document_id, path = record.get("document_id"), record.get("path")
        if type(document_id) is not int or not isinstance(path, str):
            continue
        figure_id = figure.get("figure_id")
        if type(figure_id) is not int or figure_id < 1:
            figure_id = None
        caption = str(figure.get("caption") or "")
        excerpt = str(record.get("content") or "")[:1300]
        item = {
            "document_id": document_id,
            "path": path, "page": page if type(page) is int and page > 0 else None,
            "figure_id": figure_id, "caption": caption,
            "source_type": record.get("source_type", "original_text"),
            "excerpt": excerpt, "ocr_confidence": record.get("ocr_confidence"),
        }
        sources.append(item)
        if (record.get("source_type") != "ocr_figure"
                or not path.lower().endswith(".pdf") or not item["page"]
                or figure_id is None or (document_id, figure_id) in seen):
            continue
        seen.add((document_id, figure_id))
        caption_hits = query_terms & _search_terms(caption)
        ocr_hits = query_terms & _search_terms(excerpt)
        evidence = caption_hits | ocr_hits
        required = 2 if len(query_terms) >= 2 else 1
        if len(evidence) < required:
            continue
        # Captions carry more trust than noisy OCR. The search order breaks
        # ties but cannot turn a screenshot of unrelated folders into a hit.
        score = 3 * len(caption_hits) + len(ocr_hits) + .001 * (12-index)
        candidates.append((score, item))

    candidates.sort(key=lambda pair: -pair[0])
    # A pure image widget shows ONE image, regardless of a model-supplied
    # max_pages>1. More figures can be requested in separate tool invocations.
    return {"sources": sources, "pages": [candidates[0][1]] if candidates else []}


def register_rag_viewer(server: FastMCP, request: Callable) -> None:
    """Register MCP Apps resource, model-facing renderer and app-only image tool."""
    from app.tool_policy import tool_enabled

    if not tool_enabled("show_rag_sources"):
        return

    @server.resource(
        VIEWER_URI,
        name="RAG Figure Gallery",
        description="Private HTML display of verified original CROPPED figures from indexed PDFs.",
        mime_type=VIEWER_MIME,
    )
    def rag_document_viewer() -> str:
        return VIEWER_PATH.read_text(encoding="utf-8")

    async def show_rag_sources(query: str = "", document_id: int | None = None,
                               page: int | None = None, figure_id: int | None = None,
                               max_pages: int = 1) -> dict:
        """Show ONLY ONE relevant original indexed figure image inside ChatGPT.

        Use this after search_docs, or directly with a natural-language question
        about illustrated manuals. Also use it when the user did not explicitly
        request pictures if original manual images help explain the answer.
        The MCP App displays a cropped JPEG from an indexed figure_id.
        The interface is a PLAIN IMAGE: no zoom, full-page button, page
        navigation, gallery or embedded explanatory text. Never show weak
        OCR matches as unrelated extra pictures.
        Image transport is restricted to indexed figures by the server.
        No internet, PDF skill or generated images are used.
        Use query, OR grounded document_id+page+optional figure_id
        from search_docs. The host still controls tool selection.
        Do not invent document/page IDs. OCR recognition may be inaccurate.
        """
        if type(max_pages) is not int or not 1 <= max_pages <= 6:
            raise ValueError("max_pages must be between 1 and 6")
        if document_id is not None or page is not None or figure_id is not None:
            if type(document_id) is not int or document_id < 1 or type(page) is not int or page < 1:
                raise ValueError("document_id and page must both be positive integers")
            if figure_id is not None and (type(figure_id) is not int or figure_id < 1):
                raise ValueError("figure_id must be a positive integer")
            if query.strip():
                raise ValueError("Use query or document_id+page, not both")
            metadata = await asyncio.to_thread(request, "/document-status",
                                               {"document_id": document_id})
            path = metadata.get("path", "")
            if not isinstance(path, str) or not path.lower().endswith(".pdf"):
                raise ValueError("Source is not an indexed PDF")
            selected = []
            if figure_id is not None:
                # The image endpoint verifies the figure-page relationship.
                selected = [{"figure_id": figure_id, "caption": "", "excerpt": "",
                             "ocr_confidence": None}]
            else:
                # When a model supplies only document_id+page (the common
                # case), prefer ALL figures indexed on that page to a PDF
                # preview. Avoid guessing the specific figure intended.
                response = await asyncio.to_thread(
                    request, "/figures", {"document_id": document_id, "page": page},
                )
                discovered = response.get("figures") or []
                # Without a grounded ID, guessing a figure from a page with
                # multiple images would silently display the wrong diagram.
                selected = discovered if len(discovered) == 1 else []
            items = [{
                "document_id": document_id, "page": page, "path": path,
                "figure_id": entry.get("figure_id"),
                "caption": str(entry.get("caption") or ""),
                "source_type": "selected_figure" if entry.get("figure_id") else "selected_page",
                "excerpt": str(entry.get("excerpt") or "")[:1300],
                "ocr_confidence": entry.get("ocr_confidence"),
            } for entry in selected]
            # If the page has no indexed figures, report no picture rather
            # than falling back to rendering an entire PDF page.
            return {
                "query": "", "sources": items, "pages": items,
                "source_policy": "local_rag_only",
                "note": "An individual indexed figure is required. "
                        "For multi-figure pages pass figure_id from search_docs.",
            }

        query = query.strip()
        if not 1 <= len(query) <= 1000:
            raise ValueError("Provide a natural-language query (1-1000 characters)")
        results = await asyncio.to_thread(request, "/search",
                                          {"query": query, "limit": 12})
        prepared = prepare_sources(results.get("results") or [], max_pages, query)
        return {
            "query": query, **prepared,
            "source_policy": "local_rag_only",
            "note": "Only one strongly query-matched source figure is shown. "
                    "No controls, surrounding prose, unrelated OCR hits or PDF navigation.",
        }

    if tool_enabled("get_rag_page_data"):
        async def get_rag_page_data(document_id: int, page: int,
                                    figure_id: int) -> dict:
            """UI-only tool: return ONLY a verified original RAG figure JPEG.

            Hidden from normal model tool selection; the sandboxed MCP App
            calls this tool through the host bridge, without exposing tokens.
            """
            if type(document_id) is not int or document_id < 1:
                raise ValueError("Invalid document_id")
            if type(page) is not int or page < 1:
                raise ValueError("Invalid PDF page")
            if type(figure_id) is not int or figure_id < 1:
                raise ValueError("A valid indexed figure_id is required")
            payload = {"document_id": document_id, "page": page,
                       "figure_id": figure_id}
            response = await asyncio.to_thread(request, "/page-image", payload)
            if (response.get("figure_id") != figure_id or
                    response.get("image_kind") != "cropped_original_pdf_figure"):
                raise RuntimeError("RAG response is not the requested original figure")
            encoded = response.get("image_base64")
            if (response.get("mime_type") != "image/jpeg"
                    or not response.get("source_verified")
                    or not isinstance(encoded, str)):
                raise RuntimeError("Unverified original PDF page response")
            try:
                payload = base64.b64decode(encoded, validate=True)
            except (ValueError, base64.binascii.Error) as exc:
                raise RuntimeError("Invalid image encoding") from exc
            if not 1 <= len(payload) <= 850_000 or not payload.startswith(b"\xff\xd8"):
                raise RuntimeError("Invalid JPEG image")
            return {
                "document_id": document_id, "page": response["page"],
                "total_pages": response["total_pages"], "path": response["path"],
                "mime_type": "image/jpeg", "image_base64": encoded,
                "source_sha256": response["source_sha256"],
                "source_verified": True,
                "figure_id": figure_id,
                "figure_caption": response.get("figure_caption"),
                "image_kind": response.get("image_kind"),
            }

        server.tool(
            name="get_rag_page_data",
            meta={"ui": {"visibility": ["app"]}},
        )(get_rag_page_data)

    server.tool(
        name="show_rag_sources",
        meta={
            "ui": {"resourceUri": VIEWER_URI, "visibility": ["model", "app"]},
            "openai/outputTemplate": VIEWER_URI,
        },
    )(show_rag_sources)
