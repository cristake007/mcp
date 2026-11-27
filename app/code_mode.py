"""Opt-in native FastMCP Code Mode; no arbitrary host Python execution."""
import asyncio
import json
import os

# The interactive viewer and image content need the original MCP envelopes,
# not the textual/structured result unwrapped by the Code Mode sandbox.
DIRECT_TOOLS = frozenset({"get_page_image", "show_rag_sources", "get_rag_page_data"})
MAX_TOOL_CALLS = 20
MAX_CODE_CHARS = 20_000
MAX_RESULT_BYTES = 100_000
WALL_TIMEOUT_SECS = 90


def configure_code_mode(server) -> None:
    """Leave the classic tool catalog untouched unless explicitly enabled."""
    enabled = os.environ.get("MCP_CODE_MODE_ENABLED", "0")
    if enabled not in ("0", "1"):
        raise ValueError("MCP_CODE_MODE_ENABLED must be 0 or 1")
    if enabled == "0":
        return

    # Fail at startup if the optional dependency is missing, not during a call.
    import pydantic_monty  # noqa: F401
    from fastmcp.exceptions import ToolError
    from fastmcp.experimental.transforms.code_mode import (
        CodeMode, GetSchemas, MontySandboxProvider, Search,
    )

    class BoundedMonty(MontySandboxProvider):
        async def run(self, code, *, inputs=None, external_functions=None):
            if len(code) > MAX_CODE_CHARS:
                raise ToolError("Code exceeds the execution size limit.")
            try:
                # FastMCP 4.1 only limits CPU time between host callbacks;
                # also bound elapsed time including backend calls.
                async with asyncio.timeout(WALL_TIMEOUT_SECS):
                    result = await super().run(
                        code, inputs=inputs, external_functions=external_functions,
                    )
            except TimeoutError as exc:
                raise ToolError("Code Mode execution timed out.") from exc
            if len(json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")) > MAX_RESULT_BYTES:
                raise ToolError("Result exceeds the output limit; return a smaller selection.")
            return result

    class PreserveInteractiveTools(CodeMode):
        async def transform_tools(self, tools):
            meta = await super().transform_tools(tools)
            return [*meta, *(tool for tool in tools if tool.name in DIRECT_TOOLS)]

    server.add_transform(PreserveInteractiveTools(
        discovery_tools=[Search(default_limit=6), GetSchemas()],
        sandbox_provider=BoundedMonty(limits={
            "max_feed_duration_secs": 10,
            "max_turn_duration_secs": 5,
            "max_memory": 50_000_000,
            "max_recursion_depth": 100,
        }),
        max_tool_calls=MAX_TOOL_CALLS,
        execute_description=(
            "Write sandboxed Python to combine registered tools with "
            "await call_tool(name, arguments). Return only relevant results "
            "using return; preserve source references, exact quotations, "
            "coverage flags and errors where relevant. Use search and get_schema "
            "to discover tools and their return shapes. For page images and "
            "interactive RAG viewers use the directly exposed tools instead."
        ),
    ))
