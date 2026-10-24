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
    expected = {"ping", "whoami"}
    if enabled:
        expected |= {"search_docs", "get_text", "corpus_stats"}
    expected -= disabled
    assert tools == expected, (tools, expected)

    # The adapter must return the server's literal content without changing it.
    from app.rag_tools import register_rag_tools
    from fastmcp import FastMCP

    isolated = FastMCP("optional-tools-test")
    register_rag_tools(isolated)
    custom = {tool.name: tool for tool in await isolated.list_tools()}
    assert set(custom) == {"search_docs", "get_text", "corpus_stats"} - disabled
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
            })
    if "get_text" not in custom:
        return
    with patch("app.rag_tools._request", return_value={
        "document_id": 1, "path": "test.pdf", "text": expected, "exact": True
    }):
        result = await custom["get_text"].run({"document_id": 1, "article": "Art. 15"})
        assert result.structured_content["text"] == expected, result


if __name__ == "__main__":
    asyncio.run(main())
