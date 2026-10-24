"""MCP Apps UI registration, local-only image transport and safe fallback tests."""
import asyncio
import base64
from unittest.mock import Mock

from fastmcp import FastMCP

from app.rag_viewer import (
    VIEWER_URI, VIEWER_MIME, VIEWER_PATH, prepare_sources, register_rag_viewer,
)


async def main() -> None:
    assert VIEWER_PATH.is_file()
    html = VIEWER_PATH.read_text(encoding="utf-8")
    assert "ui/initialize" in html and "ui/notifications/tool-result" in html
    assert "get_rag_page_data" in html and "data:image/jpeg;base64," in html
    assert "<iframe" not in html.lower() and "fetch(" not in html.lower()
    assert "figure_id" in html and "cropped_original_pdf_figure" in html
    assert "<button" not in html and "<header" not in html
    assert "<details" not in html and "zoom" not in html.lower()
    assert "document.createElement" not in html and "innerHTML" not in html
    assert html.count("<img ") == 1

    sample = [
        {"document_id": 8, "path": "manual/MANTIS.pdf",
         "source_type": "ocr_figure", "content": "Ethernet connector",
         "figure": {"figure_id": 12, "page": 12, "caption": "Figure 4 MANTIS connectors"},
         "ocr_confidence": 87.3, "locations": [{"kind": "pdf_ocr", "page": 12}]},
        {"document_id": 8, "path": "manual/MANTIS.pdf",
         "source_type": "ocr_figure", "content": "MANTIS front-panel buttons",
         "figure": {"figure_id": 11, "page": 12,
                    "caption": "Figure 3 MANTIS front-panel buttons"},
         "locations": [{"kind": "pdf_ocr", "page": 12}]},
        {"document_id": 10, "path": "manual/features.pdf",
         "source_type": "ocr_figure", "content": "Rechercher dans: Dossier de fichiers",
         "figure": {"figure_id": 383, "page": 6, "caption": ""},
         "locations": [{"kind": "pdf_ocr", "page": 6}]},
        {"document_id": 8, "path": "manual/MANTIS.pdf",
         "source_type": "original_text", "content": "Ethernet cable",
         "locations": [{"kind": "pdf_page", "page": 12}]},
        {"document_id": 9, "path": "manual/test.pdf",
         "source_type": "original_text", "content": "Activation online",
         "locations": [{"kind": "pdf_page", "page": 22}]},
    ]
    selected = prepare_sources(sample, 4, "Ce conectori are aparatul MANTIS?")
    assert len(selected["sources"]) == 5
    assert len(selected["pages"]) == 1
    assert selected["pages"][0]["caption"] == "Figure 4 MANTIS connectors"
    assert selected["pages"][0]["figure_id"] == 12
    assert selected["pages"][0]["source_type"] == "ocr_figure"
    # No buttons figure, screenshot of folders, or general text allowed.
    assert all(p["figure_id"] == 12 for p in selected["pages"])
    assert prepare_sources(sample, 6, "Ce conectori are aparatul MANTIS?")["pages"] == selected["pages"]
    assert prepare_sources(sample, 4, "informații despre o temă inexistentă")["pages"] == []

    jpeg = bytes.fromhex("ffd8ffd9")
    encoded = base64.b64encode(jpeg).decode()

    def request(path, payload):
        if path == "/search":
            assert payload == {"query": "Ce conectori are aparatul MANTIS?", "limit": 12}
            return {"results": sample}
        if path == "/figures":
            assert payload == {"document_id": 8, "page": 12}
            return {"figures": [
                {"figure_id": 11, "caption": "Figure 3: MANTIS controls",
                 "excerpt": "save mode button", "ocr_confidence": 75},
                {"figure_id": 12, "caption": "Figure 4: MANTIS connectors",
                 "excerpt": "Ethernet USB connector", "ocr_confidence": 88},
            ]}
        if path == "/document-status":
            assert payload == {"document_id": 8}
            return {"document_id": 8, "path": "manual/MANTIS.pdf"}
        if path == "/page-image":
            assert payload in ({"document_id": 8, "page": 12},
                               {"document_id": 8, "page": 12, "figure_id": 12})
            return {"document_id": 8, "path": "manual/MANTIS.pdf", "page": 12,
                    "figure_id": payload.get("figure_id"),
                    "image_kind": "cropped_original_pdf_figure" if payload.get("figure_id") else "rendered_original_pdf_page",
                    "figure_caption": "Figure 4 MANTIS connectors" if payload.get("figure_id") else None,
                    "total_pages": 186, "mime_type": "image/jpeg",
                    "source_sha256": "0"*64, "source_verified": True,
                    "image_base64": encoded}
        raise AssertionError(f"Unexpected API endpoint: {path}")

    server = FastMCP("RAG app integration test")
    register_rag_viewer(server, request)
    tools = {x.name: x for x in await server.list_tools()}
    assert set(tools) == {"show_rag_sources", "get_rag_page_data"}
    ui_meta = tools["show_rag_sources"].meta
    assert ui_meta["ui"]["resourceUri"] == VIEWER_URI, ui_meta
    assert "model" in ui_meta["ui"]["visibility"]
    assert tools["get_rag_page_data"].meta["ui"]["visibility"] == ["app"]

    resources = await server.list_resources()
    found = [r for r in resources if str(r.uri) == VIEWER_URI]
    assert len(found) == 1, resources
    assert found[0].mime_type == VIEWER_MIME, found[0]

    plain = await tools["show_rag_sources"].run({"query": "Ce conectori are aparatul MANTIS?", "max_pages": 6})
    assert plain.structured_content["pages"][0]["page"] == 12, plain
    assert plain.structured_content["pages"][0]["figure_id"] == 12, plain
    assert plain.structured_content["source_policy"] == "local_rag_only"
    assert not any("image_base64" in p for p in plain.structured_content["pages"])

    direct = await tools["show_rag_sources"].run(
        {"document_id": 8, "page": 12, "figure_id": 12},
    )
    assert direct.structured_content["pages"][0]["page"] == 12
    assert direct.structured_content["pages"][0]["figure_id"] == 12
    inferred = await tools["show_rag_sources"].run(
        {"document_id": 8, "page": 12},
    )
    # Multiple figures on a page are ambiguous unless figure_id is grounded.
    assert inferred.structured_content["pages"] == []

    # Full-page transport is prohibited even if directly requested.
    try:
        await tools["get_rag_page_data"].run({"document_id": 8, "page": 12})
        raise AssertionError("Page-only transport unexpectedly succeeded")
    except Exception as exc:
        assert not isinstance(exc, AssertionError)
    figure = await tools["get_rag_page_data"].run(
        {"document_id": 8, "page": 12, "figure_id": 12},
    )
    assert figure.structured_content["figure_id"] == 12
    assert figure.structured_content["image_kind"] == "cropped_original_pdf_figure"
    assert figure.structured_content["source_verified"]

    for invalid in [{"document_id": -1, "page": 1}, {"page": 1}]:
        try:
            await tools["show_rag_sources"].run(invalid)
        except (ValueError, Exception):
            pass
        else:
            raise AssertionError("Invalid identifiers accepted")


if __name__ == "__main__":
    asyncio.run(main())
