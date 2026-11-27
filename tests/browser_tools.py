"""Offline tests: public HTML/ACF distinction, optional tools, fixed adapter."""
import asyncio
import os
from unittest.mock import patch
from app.public_html import get_public_html
from app.browser_tools import verify_content_visibility, inspect_rendered_page
from app.server import mcp

URL = "https://tuvkarpat.ro/cursuri/test/"


async def main():
    with patch("app.public_html.site.fetch", return_value=(
        '<html><body><div style="display:none">Reducere 10%</div>'
        '<script>Reducere 20%</script></body></html>'
    )) as fetch:
        r = get_public_html(URL, ["Reducere 10%", "Reducere 20%"])
        assert r["source"] == "public_html" and not r["visibility_verified"]
        assert [m["occurrences"] for m in r["matches"]] == [1, 0]
        assert r["html_excerpt"] == ""
        fetch.assert_called_once_with(URL, "text/html,application/xhtml+xml")
    with patch("app.browser_tools._request", return_value={"source":"rendered_dom"}) as call:
        assert verify_content_visibility(URL, ["10%"], "mobile")["source"] == "rendered_dom"
        call.assert_called_once_with(URL, ["10%"], "mobile")
        inspect_rendered_page(URL)
        call.assert_called_with(URL, [], "desktop")
    names = {t.name for t in await mcp.list_tools()}
    if os.getenv("MCP_CODE_MODE_ENABLED") == "1":
        assert {"search","get_schema","execute"} <= names
        found = await mcp.call_tool("get_schema", {"tools":["get_public_html","verify_content_visibility"]})
        assert "get_public_html" in str(found) and "verify_content_visibility" in str(found)
    else:
        assert "get_public_html" in names
        expected = {"inspect_rendered_page","verify_content_visibility"} if os.getenv("MCP_BROWSER_ENABLED") == "1" else set()
        assert names.intersection({"inspect_rendered_page","verify_content_visibility"}) == expected
    print("PASS: source distinction and browser tool registration")


if __name__ == "__main__":
    asyncio.run(main())
