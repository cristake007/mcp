"""Exercise the real Monty sandbox and existing registration policy offline."""
import asyncio
import json
import os

from app.code_mode import DIRECT_TOOLS, MAX_TOOL_CALLS, configure_code_mode
from app.server import mcp
from fastmcp import FastMCP


def payload(result):
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


async def rejected(server, code):
    try:
        result = await server.call_tool("execute", {"code": code})
    except Exception:
        return
    assert result.is_error, result


async def main():
    names = {t.name for t in await mcp.list_tools()}
    expected = {"search", "get_schema", "execute"}
    if os.getenv("MCP_RAG_ENABLED") == "1":
        disabled = set(os.getenv("MCP_DISABLED_TOOLS", "").split(","))
        direct = DIRECT_TOOLS - disabled
        if "show_rag_sources" in disabled:
            direct -= {"get_rag_page_data"}
        expected |= direct
    assert names == expected, (names, expected)

    schemas = await mcp.call_tool("get_schema", {"tools": ["list_courses"]})
    assert "list_courses" in str(schemas) and "offset" in str(schemas)
    found = await mcp.call_tool("search", {"query": "WordPress courses"})
    assert "courses" in str(found)

    # Validate real tool calls and local filtering without live site requests.
    from unittest.mock import patch
    with patch("app.catalog_tools.engine.list_catalog_courses", return_value={
        "courses": [{"id": 1, "slug": "a", "url": "https://example.org/a",
                     "title": "A", "domenii": []},
                    {"id": 2, "slug": "b", "url": "https://example.org/b",
                     "title": "B", "domenii": []}],
        "total_courses": 2, "complete": True, "missing_courses_count": 0,
        "errors": [],
    }):
        result = await mcp.call_tool("execute", {"code": (
            'a = await call_tool("list_courses", {"limit": 1})\n'
            'b = await call_tool("list_courses", {"offset": a["next_offset"], "limit": 1})\n'
            'return {"titles": [a["courses"][0]["title"], b["courses"][0]["title"]], '
            '"complete": a["complete"] and b["complete"]}'
        )})
    assert payload(result) == {"titles": ["A", "B"], "complete": True}, result

    if "whoami" in os.getenv("MCP_DISABLED_TOOLS", "").split(","):
        await rejected(mcp, 'return await call_tool("whoami", {})')
    if os.getenv("MCP_GSC_ENABLED") != "1":
        await rejected(mcp, 'return await call_tool("gsc_performance", {})')
    await rejected(mcp, 'return await call_tool("not_registered", {})')
    await rejected(mcp, 'return open("/etc/passwd").read()')
    await rejected(mcp, 'return __import__("os").environ')
    await rejected(mcp, 'return "x" * 100001')
    await rejected(mcp, " " * 20001)
    await rejected(mcp, (
        f"for _ in range({MAX_TOOL_CALLS + 1}):\n"
        '    await call_tool("ping", {})\nreturn 1'
    ))

    # The transform does not expand access to unregistered tools.
    isolated = FastMCP("code-mode-isolated")
    configure_code_mode(isolated)
    await rejected(isolated, 'return await call_tool("ping", {})')
    print("PASS: native Code Mode, chaining, discovery, limits and tool policy")


if __name__ == "__main__":
    asyncio.run(main())
