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
        stats_result = {
            "terms": ["autorizare", "suspendare"],
            "matching_documents": 3, "matching_articles": 1,
            "exhaustive_counts": True,
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
        listing = {
            "total_documents": 1,
            "documents": [{"document_id": 14, "path": "manual.pdf", "warnings_count": 0}],
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

    if "get_text" not in custom:
        return
    with patch("app.rag_tools._request", return_value={
        "document_id": 1, "path": "test.pdf", "text": expected, "exact": True
    }):
        result = await custom["get_text"].run({"document_id": 1, "article": "Art. 15"})
        assert result.structured_content["text"] == expected, result


if __name__ == "__main__":
    asyncio.run(main())
