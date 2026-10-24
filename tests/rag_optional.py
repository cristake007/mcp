"""Optional RAG tool registration tests; no RAG Docker/API required."""
import asyncio
import os
from unittest.mock import patch

from app.server import mcp
from app.rag_tools import _request


async def main():
    tools = {tool.name for tool in await mcp.list_tools()}
    assert {"ping", "whoami"} <= tools, tools
    enabled = os.getenv("MCP_RAG_ENABLED") == "1"
    if enabled:
        assert {"search_docs", "get_text"} <= tools, tools
    else:
        assert "search_docs" not in tools and "get_text" not in tools, tools

    # The adapter must return the server's literal content without changing it.
    from app.rag_tools import register_rag_tools
    from fastmcp import FastMCP

    isolated = FastMCP("optional-tools-test")
    register_rag_tools(isolated)
    custom = {tool.name: tool for tool in await isolated.list_tools()}
    assert set(custom) == {"search_docs", "get_text"}
    expected = "Art. 15\n  Verificare anuală.\n"
    with patch("app.rag_tools._request", return_value={
        "document_id": 1, "path": "test.pdf", "text": expected, "exact": True
    }):
        result = await custom["get_text"].run({"document_id": 1, "article": "Art. 15"})
        assert result.structured_content["text"] == expected, result


if __name__ == "__main__":
    asyncio.run(main())
