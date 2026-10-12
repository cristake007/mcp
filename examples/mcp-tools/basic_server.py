"""Deterministic example tool; no external dependencies other than MCP SDK."""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("reference-basic")


@mcp.tool()
def duration_in_days(hours: int, hours_per_day: int = 8) -> dict[str, int | float]:
    """Convert nonnegative hours to training days for a positive working-day length."""
    if hours < 0 or hours_per_day <= 0 or hours_per_day > 24:
        raise ValueError("hours must be >= 0 and hours_per_day must be between 1 and 24")
    return {"hours": hours, "hours_per_day": hours_per_day, "days": hours / hours_per_day}


if __name__ == "__main__":
    mcp.run(transport="stdio")
