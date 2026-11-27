"""Read-only authenticated MCP adapter for the optional isolated browser."""
import json
from typing import Literal
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from app.site_transport import allowed

PROFILES = Literal["desktop", "tablet", "mobile"]
MAX_RESPONSE = 200_000


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _request(url: str, texts: list[str], viewport: str) -> dict:
    allowed(url)
    if len(url) > 1000 or viewport not in ("desktop", "tablet", "mobile"):
        raise ValueError("Invalid URL or viewport")
    if not isinstance(texts, list) or len(texts) > 10 or any(
        not isinstance(x, str) or not x.strip() or len(x) > 300 for x in texts
    ):
        raise ValueError("Provide at most 10 nonempty literal texts of up to 300 characters")
    req = Request("http://browser-audit:8080/inspect",
                  data=json.dumps({"url": url, "texts": texts, "viewport": viewport}).encode(),
                  headers={"Content-Type": "application/json"}, method="POST")
    with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=40) as response:
        raw = response.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise RuntimeError("Browser response exceeded limit")
    result = json.loads(raw)
    if not isinstance(result, dict) or result.get("url") != url or result.get("profile") != viewport:
        raise RuntimeError("Unexpected browser response identity")
    return result


def inspect_rendered_page(url: str, viewport: PROFILES = "desktop") -> dict:
    """Inspect a fresh restricted Chromium DOM of a public tuvkarpat.ro page.
    Returns title, bounded innerText, headings and loading limitations.
    innerText is not proof that every character is visually perceptible.
    Use verify_content_visibility for a targeted computed-style check.
    """
    return _request(url, [], viewport)


def verify_content_visibility(url: str, texts: list[str],
                              viewport: PROFILES = "desktop") -> dict:
    """Check literal text against rendered DOM and computed CSS at one viewport.
    Distinguishes visible, hidden, partial, not_found and indeterminate in the
    tested fresh session; includes reasons and missing-resource warnings.
    No clicking, login, scrolling interaction, cookie consent or form submission.
    Run separately for desktop, tablet and mobile when needed. Off-screen
    scrollable text can be visible in layout; this is not a screenshot/occlusion test.
    """
    if not texts:
        raise ValueError("At least one text is required")
    return _request(url, texts, viewport)
