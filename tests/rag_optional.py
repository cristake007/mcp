"""Optional RAG tool registration tests; no RAG Docker/API required."""
import asyncio
import os
from unittest.mock import patch

from app.server import mcp
from app.rag_tools import _request


async def main():
    tools = {tool.name for tool in await mcp.list_tools()}
    disabled = {name.strip() for name in os.getenv("MCP_DISABLED_TOOLS", "").split(",") if name.strip()}
    enabled = os.getenv("MCP_RAG_ENABLED") == "1"
    expected = {"ping", "whoami", "inspect_site"}
    if enabled:
        expected |= {"search_docs", "get_text", "corpus_stats", "list_docs", "document_status", "corpus_health", "get_page_image", "show_rag_sources", "get_rag_page_data"}
    expected -= disabled
    assert tools == expected, (tools, expected)

    # The adapter must return the server's literal content without changing it.
    from app.rag_tools import register_rag_tools
    from fastmcp import FastMCP

    isolated = FastMCP("optional-tools-test")
    register_rag_tools(isolated)
    custom = {tool.name: tool for tool in await isolated.list_tools()}
    assert set(custom) == {"search_docs", "get_text", "corpus_stats", "list_docs", "document_status", "corpus_health", "get_page_image", "show_rag_sources", "get_rag_page_data"} - disabled
    expected = "Art. 15\n  Verificare anuală.\n"
    if "corpus_stats" in custom:
        schema = custom["corpus_stats"].output_schema
        assert schema["type"] == "object"
        assert {"terms", "matching_documents", "occurrences_per_term",
                "document_examples", "article_examples", "exhaustive_counts"} <= set(schema["properties"])
        # Nested examples must have a discoverable schema, not a generic dict.
        assert "document_id" in str(schema) and "char_start" in str(schema)
        stats_result = {
            "terms": ["autorizare", "suspendare"],
            "mode": "all", "match_type": "linguistic",
            "match_method": "Romanian Snowball stemming, token-by-token; no semantic inference",
            "not_semantic": True, "exhaustive_counts": True,
            "documents_scanned": 3, "articles_detected": 7,
            "matching_documents": 1, "matching_articles": 1,
            "documents_with_matching_articles": 1,
            "occurrences_per_term": [5, 2], "examples_limit": 3,
            "document_examples": [{
                "document_id": 14, "path": "manual.pdf",
                "occurrences_per_term": [5, 2], "matching_articles": 1,
            }],
            "article_examples": [{
                "document_id": 14, "path": "manual.pdf", "article": "Art. 15",
                "char_start": 100, "char_end": 600,
                "occurrences_per_term": [3, 1],
            }],
            "document_examples_truncated": False,
            "article_examples_truncated": False,
            "note": "Counts refer to extracted text, not implied meaning.",
        }
        with patch("app.rag_tools._request", return_value=stats_result) as request:
            result = await custom["corpus_stats"].run({
                "term": "autorizare",
                "second_term": "suspendare",
                "mode": "all",
                "examples_limit": 3,
            })
            assert result.structured_content == stats_result, result
            request.assert_called_once_with("/stats", {
                "term": "autorizare", "second_term": "suspendare",
                "mode": "all", "examples_limit": 3,
                "match_type": "linguistic",
            })
    if "list_docs" in custom:
        schema = custom["list_docs"].output_schema
        assert schema["type"] == "object"
        assert {"total_documents", "offset", "limit", "documents",
                "next_offset", "source_check"} <= set(schema["properties"])
        assert "orientation_version" in str(schema) and "potential_issue" in str(schema)
        listing = {
            "total_documents": 1, "offset": 0, "limit": 10,
            "documents": [{
                "document_id": 14, "path": "manual.pdf", "parser": "pdf",
                "indexed_at": "2026-10-25T11:30:00+00:00",
                "text_chars": 4000, "chunks": 4, "embeddings": 4,
                "figures": 2, "ocr_chunks": 2, "orientation_version": 1,
                "rotated_pages": 0, "embedding_model": "multilingual-e5-small",
                "warnings_count": 0, "has_warnings": False, "potential_issue": None,
            }],
            "next_offset": None, "source_check": "not_performed",
        }
        with patch("app.rag_tools._request", return_value=listing) as request:
            result = await custom["list_docs"].run({"offset": 0, "limit": 10})
            assert result.structured_content == listing, result
            request.assert_called_once_with("/documents", {"offset": 0, "limit": 10})
    if "document_status" in custom:
        record = {"document_id": 14, "status": "indexed_complete",
                  "chunks": 20, "stored_text_coverage_complete": True,
                  "source_check": "not_performed"}
        with patch("app.rag_tools._request", return_value=record) as request:
            result = await custom["document_status"].run({"document_id": 14})
            assert result.structured_content == record, result
            request.assert_called_once_with("/document-status", {"document_id": 14})

    if "corpus_health" in custom:
        health = {
            "status": "attention_required",
            "summary": {"new_files": 1, "modified_files": 0, "missing_files": 0,
                        "unindexed_files": 1},
            "files": [{"path": "new.pdf", "status": "new_unindexed"}],
            "next_offset": None, "source_directory_checked": True,
        }
        with patch("app.rag_tools._request", return_value=health) as request:
            result = await custom["corpus_health"].run({"offset": 0, "limit": 20})
            assert result.structured_content == health, result
            request.assert_called_once_with("/corpus-health", {"offset": 0, "limit": 20})

    if "get_page_image" in custom:
        import base64
        from mcp.types import ImageContent, TextContent
        jpeg = bytes([0xff, 0xd8, 0xff, 0xd9])
        payload = {
            "document_id": 14, "path": "manual.pdf", "page": 2,
            "total_pages": 20, "mime_type": "image/jpeg",
            "image_base64": base64.b64encode(jpeg).decode("ascii"),
            "source_verified": True,
        }
        with patch("app.rag_tools._request", return_value=payload) as request:
            result = await custom["get_page_image"].run({"document_id": 14, "page": 2})
            assert len(result.content) == 2, result
            assert isinstance(result.content[0], TextContent), result
            assert "manual.pdf" in result.content[0].text, result
            assert isinstance(result.content[1], ImageContent), result
            assert result.content[1].mimeType == "image/jpeg", result
            assert base64.b64decode(result.content[1].data) == jpeg, result
            request.assert_called_once_with(
                "/page-image", {"document_id": 14, "page": 2},
            )
        # Figure selection forwards only the index-grounded figure ID.
        with patch("app.rag_tools._request", return_value={
            **payload, "figure_id": 21,
            "image_kind": "cropped_original_pdf_figure",
        }) as request:
            result = await custom["get_page_image"].run({
                "document_id": 14, "page": 2, "figure_id": 21,
            })
            assert isinstance(result.content[1], ImageContent), result
            request.assert_called_once_with(
                "/page-image", {"document_id": 14, "page": 2, "figure_id": 21},
            )

    if "get_text" not in custom:
        return
    with patch("app.rag_tools._request", return_value={
        "document_id": 1, "path": "test.pdf", "text": expected, "exact": True
    }):
        result = await custom["get_text"].run({"document_id": 1, "article": "Art. 15"})
        assert result.structured_content["text"] == expected, result


if __name__ == "__main__":
    asyncio.run(main())
