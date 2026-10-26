"""HTTP OAuth token resource-indicator enforcement.

The MCP SDK parses the resource field but its token handler does not enforce it
for authorization-code and refresh-token grants. This ASGI wrapper validates
RFC 8707 before either grant is processed; other routes are unchanged.
"""
from urllib.parse import parse_qs, urlsplit


class Origin403Diagnostics:
    """Log only metadata for 403 responses; never log OAuth tickets or secrets.

    The FastMCP origin guard may reject a macOS browser request before the
    OAuth login handler runs. Nginx logs normally omit the Origin header, so
    this limited record lets administrators identify the exact legitimate
    origin without weakening the security guard.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_checked(message):
            if message["type"] == "http.response.start" and message["status"] == 403:
                # Never log cookies, authorization codes, request bodies,
                # OAuth ticket query parameters, bearer tokens or user data.
                headers = dict(scope.get("headers") or [])
                raw = headers.get(b"origin", b"")
                origin = raw.decode("utf-8", errors="replace").strip()
                parsed_origin = urlsplit(origin)
                if origin == "null":
                    observed_origin = "null"
                elif (parsed_origin.scheme and parsed_origin.netloc
                      and not parsed_origin.path and not parsed_origin.query
                      and not parsed_origin.fragment
                      and not parsed_origin.username
                      and not parsed_origin.password):
                    observed_origin = parsed_origin.scheme + "://" + parsed_origin.netloc[:190]
                elif not origin:
                    observed_origin = "<absent>"
                else:
                    observed_origin = "<malformed>"
                path = scope.get("path", "")
                # Only fixed path labels: no user-controlled query strings.
                path = path if path in (
                    "/login", "/authorize", "/token", "/register", "/mcp",
                    "/healthz", "/.well-known/oauth-authorization-server",
                    "/.well-known/oauth-protected-resource/mcp",
                ) else "<other>"
                import json
                print("MCP_HTTP_403 " + json.dumps({
                    "method": scope.get("method", ""),
                    "path": path,
                    "origin": observed_origin,
                }, ensure_ascii=True), flush=True)
            await send(message)

        await self.app(scope, receive, send_checked)



from starlette.responses import JSONResponse


def same_resource(value: str, canonical: str) -> bool:
    """Compare canonical MCP resource URI, tolerating scheme/host case only."""
    try:
        got = urlsplit(value)
        expected = urlsplit(canonical)
        if (got.username is not None or got.password is not None
                or got.query or got.fragment):
            return False
        return (
            got.scheme == expected.scheme
            and got.hostname == expected.hostname
            and (got.port or 443) == (expected.port or 443)
            and got.path == expected.path
        )
    except ValueError:
        return False


class TokenResourceGuard:
    """Require the target resource on POST /token, without rewriting body."""

    def __init__(self, app, resource_url: str):
        self.app = app
        self.resource_url = resource_url

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") != "/token":
            return await self.app(scope, receive, send)

        # Nginx limits /token to 16KiB. Also cap direct ASGI requests.
        body = bytearray()
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > 16384:
                response = JSONResponse({"error": "invalid_request"}, status_code=413)
                return await response(scope, receive, send)
            more = message.get("more_body", False)

        try:
            headers = {k.lower(): v for k, v in scope.get("headers", [])}
            content_type = headers.get(b"content-type", b"").decode("ascii").split(";", 1)[0].strip().lower()
            if content_type != "application/x-www-form-urlencoded":
                raise ValueError("unsupported content type")
            data = parse_qs(body.decode("utf-8"), keep_blank_values=True, strict_parsing=True, max_num_fields=32)
            resources = data.get("resource", [])
            if len(resources) != 1 or not same_resource(resources[0], self.resource_url):
                response = JSONResponse(
                    {"error": "invalid_target", "error_description": "Unknown or missing MCP resource"},
                    status_code=400,
                    headers={"Cache-Control": "no-store"},
                )
                return await response(scope, receive, send)
        except (ValueError, UnicodeDecodeError):
            response = JSONResponse(
                {"error": "invalid_request"}, status_code=400,
                headers={"Cache-Control": "no-store"},
            )
            return await response(scope, receive, send)

        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
