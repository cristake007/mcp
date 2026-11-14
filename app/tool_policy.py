"""Simple process-wide tool registration policy; no database or runtime changes.

Only known tools are configurable. Defaults remain enabled. Rag tools are
always absent when MCP_RAG_ENABLED is not 1, regardless of this allowlist.
"""
import os

TOOLS = (
    "ping", "whoami",
    # Task-oriented catalog tools, plus the original compatibility interfaces.
    "list_domains", "list_courses", "get_course", "find_courses",
    "search_courses", "inspect_site",
    "list_sitemaps", "list_site_urls", "get_site_text", "search_site",
    # Optional Google Search Console API (read-only, private MCP only).
    "gsc_performance", "gsc_inspect_url", "seo_course_queries",
    "seo_inspect_page",
    # Optional document-RAG tools.
    "search_docs", "get_text", "get_document_context", "corpus_stats",
    "list_docs", "document_status", "corpus_health", "get_page_image",
    "show_rag_sources", "get_rag_page_data",
)


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
