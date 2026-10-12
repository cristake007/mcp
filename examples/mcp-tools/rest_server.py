"""Read-only external REST API example with bounded requests."""
import os
from urllib.parse import urljoin

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("reference-rest")


@mcp.tool()
async def get_course(course_id: int) -> dict:
    """Read one course from a configured, trusted REST API."""
    if course_id <= 0:
        raise ValueError("course_id must be positive")
    base = os.environ.get("COURSES_API_BASE_URL", "").strip()
    if not base or not base.startswith("https://"):
        raise RuntimeError("COURSES_API_BASE_URL must be a trusted HTTPS API base URL")
    # Only a fixed path and integer ID are accepted; callers cannot supply arbitrary URLs.
    url = urljoin(base.rstrip("/") + "/", f"courses/{course_id}")
    headers = {}
    if token := os.environ.get("COURSES_API_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0), follow_redirects=False) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError("Upstream course API request failed") from exc


if __name__ == "__main__":
    mcp.run(transport="stdio")
