"""Read-only MCP Apps viewer for pages and OCR figures in the private RAG.

No public URLs, public images, iframe PDF parsers or new database access.
The host fetches a static UI resource; browser page images come *only* from
an app-only MCP tool, itself backed by the SHA-256-verified RAG API.
"""
import asyncio
import base64
from pathlib import Path
from typing import Callable

from fastmcp import FastMCP

VIEWER_URI = "ui://rag/document-viewer/v1.html"
VIEWER_MIME = "text/html;profile=mcp-app"
VIEWER_PATH = Path(__file__).with_name("rag_viewer.html")


def prepare_sources(results: list[dict], max_pages: int) -> dict:
    """Keep context and diversify pages; never pretend that a hit verifies a PDF."""
    items = []
    pages = []
    seen = set()
    for record in results[:12]:
        source = record.get("locations") or []
        figure = record.get("figure") or {}
        page = figure.get("page")
        if not page:
            page = next((loc.get("page") for loc in source
                         if loc.get("kind") in ("pdf_page", "pdf_ocr")
                         and type(loc.get("page")) is int), None)
        document_id = record.get("document_id")
        path = record.get("path")
        if type(document_id) is not int or not isinstance(path, str):
            continue
        caption = str(figure.get("caption") or "")
        excerpt = str(record.get("content") or "")[:1300]
        item = {
            "document_id": document_id,
            "path": path,
            "page": page if type(page) is int and page > 0 else None,
            "caption": caption,
            "source_type": record.get("source_type", "original_text"),
            "excerpt": excerpt,
            "ocr_confidence": record.get("ocr_confidence"),
        }
        items.append(item)
        if (len(pages) >= max_pages or not path.lower().endswith(".pdf")
                or not item["page"]):
            continue
        key = (document_id, item["page"])
        if key not in seen:
            seen.add(key)
            pages.append(item)
    return {"sources": items, "pages": pages}


def register_rag_viewer(server: FastMCP, request: Callable) -> None:
    """Register MCP Apps resource, model-facing renderer and app-only image tool."""
    from app.tool_policy import tool_enabled

    if not tool_enabled("show_rag_sources"):
        return

    @server.resource(
        VIEWER_URI,
        name="RAG Document Viewer",
        description="Private HTML viewer for verified original pages of indexed RAG PDFs.",
        mime_type=VIEWER_MIME,
    )
    def rag_document_viewer() -> str:
        return VIEWER_PATH.read_text(encoding="utf-8")

    async def show_rag_sources(query: str = "", document_id: int | None = None,
                               page: int | None = None, max_pages: int = 4) -> dict:
        """Show ORIGINAL local-RAG PDF pages in an interactive MCP App in chat.

        Use this after search_docs, or directly with a natural-language question
        about illustrated manuals. Also use it when the user did not explicitly
        request pictures if original manual images help explain the answer.
        The displayed pages are retrieved exclusively from the authenticated
        local RAG MCP connector: no internet search, standalone PDF skill or
        generated/stock images. The app embeds original page JPEGs and enables
        zoom and navigation. This tool provides a text fallback when the host
        cannot render MCP Apps. It cannot force a host to select this tool.
        Pass a query, OR a grounded document_id+page from search_docs.
        Do not invent document/page IDs. OCR recognition may be inaccurate.
        """
        if type(max_pages) is not int or not 1 <= max_pages <= 6:
            raise ValueError("max_pages must be between 1 and 6")
        if document_id is not None or page is not None:
            if type(document_id) is not int or document_id < 1 or type(page) is not int or page < 1:
                raise ValueError("document_id and page must both be positive integers")
            if query.strip():
                raise ValueError("Use query or document_id+page, not both")
            metadata = await asyncio.to_thread(request, "/document-status",
                                               {"document_id": document_id})
            path = metadata.get("path", "")
            if not isinstance(path, str) or not path.lower().endswith(".pdf"):
                raise ValueError("Source is not an indexed PDF")
            item = {
                "document_id": document_id, "page": page, "path": path,
                "caption": "", "source_type": "selected_page",
                "excerpt": "", "ocr_confidence": None,
            }
            return {
                "query": "", "sources": [item], "pages": [item],
                "source_policy": "local_rag_only",
                "note": "The viewer loads a SHA-256-verified page using the local RAG MCP bridge.",
            }

        query = query.strip()
        if not 1 <= len(query) <= 1000:
            raise ValueError("Provide a natural-language query (1-1000 characters)")
        results = await asyncio.to_thread(request, "/search",
                                          {"query": query, "limit": 12})
        prepared = prepare_sources(results.get("results") or [], max_pages)
        return {
            "query": query, **prepared,
            "source_policy": "local_rag_only",
            "note": "OCR is best-effort. The viewer fetches original PDF page images, "
                    "not internet or generated images. Source pages are candidates "
                    "from RAG search, not a guarantee every image is relevant.",
        }

    if tool_enabled("get_rag_page_data"):
        async def get_rag_page_data(document_id: int, page: int) -> dict:
            """UI-only tool: return a verified original RAG PDF page to the viewer.

            Hidden from normal model tool selection; the sandboxed MCP App
            calls this tool through the host bridge, without exposing tokens.
            """
            if type(document_id) is not int or document_id < 1:
                raise ValueError("Invalid document_id")
            if type(page) is not int or page < 1:
                raise ValueError("Invalid PDF page")
            response = await asyncio.to_thread(
                request, "/page-image", {"document_id": document_id, "page": page},
            )
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
