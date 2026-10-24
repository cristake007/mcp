"""Simple process-wide tool registration policy; no database or runtime changes.

Only known tools are configurable. Defaults remain enabled. Rag tools are
always absent when MCP_RAG_ENABLED is not 1, regardless of this allowlist.
"""
import os

TOOLS = ("ping", "whoami", "inspect_site", "search_docs", "get_text", "corpus_stats", "list_docs", "document_status")


def disabled_tools() -> frozenset[str]:
    value = os.environ.get("MCP_DISABLED_TOOLS", "")
    names = frozenset(name.strip() for name in value.split(",") if name.strip())
    unknown = names.difference(TOOLS)
    if unknown:
        raise ValueError(f"Unknown MCP_DISABLED_TOOLS: {', '.join(sorted(unknown))}")
    return names


def tool_enabled(name: str) -> bool:
    if name not in TOOLS:
        raise ValueError(f"Unknown MCP tool: {name}")
    return name not in disabled_tools()
