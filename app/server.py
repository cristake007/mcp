"""FastMCP HTTP server with a private, first-party OAuth login screen."""
import hashlib
import hmac
import html
import os
from ipaddress import ip_address
from urllib.parse import urlparse

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Route

from app.db import get_active_user_id, healthy, user_for_token
from app.http_security import TokenResourceGuard
from app.oauth import DatabaseOAuthProvider, digest
from app.tool_policy import tool_enabled

BASE_URL = os.environ["PUBLIC_BASE_URL"].rstrip("/")
COOKIE_KEY = os.environ["LOGIN_COOKIE_KEY"]
parsed = urlparse(BASE_URL)
if not parsed.hostname or (parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1")):
    raise RuntimeError("PUBLIC_BASE_URL must be public HTTPS (or localhost for development)")
if len(COOKIE_KEY) < 32 or COOKIE_KEY.startswith("CHANGE_ME"):
    raise RuntimeError("LOGIN_COOKIE_KEY must contain at least 32 random characters")

provider = DatabaseOAuthProvider(base_url=BASE_URL)
mcp = FastMCP(name="TUV Private MCP", auth=provider)


# RAG tools are absent unless explicitly activated via compose.rag.yaml.
# No RAG imports occur in normal MCP mode, even if RAG is installed.
if os.environ.get("MCP_RAG_ENABLED", "0") == "1":
    from app.rag_tools import register_rag_tools
    register_rag_tools(mcp)


def ping() -> str:
    """Check that the authenticated MCP connection works."""
    return "pong"


async def whoami() -> dict[str, str]:
    """Return the email associated with the current MCP access token."""
    token = get_access_token()
    if token is None:
        raise PermissionError("Authentication required")
    user = await user_for_token(digest(token.token))
    if not user:
        raise PermissionError("Invalid or disabled account")
    return {"email": user["email"]}

# Register only enabled tools. Disabled tools are not advertised or callable.
if tool_enabled("ping"):
    mcp.tool()(ping)
if tool_enabled("whoami"):
    mcp.tool()(whoami)


def login_cookie(ticket: str) -> str:
    return hmac.new(
        COOKIE_KEY.encode("utf-8"), ("mcp-login:" + ticket).encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def page(ticket: str, client_name: str, redirect_uri: str, error: str = "") -> HTMLResponse:
    safe_ticket = html.escape(ticket, quote=True)
    safe_client = html.escape(client_name or "MCP client")
    callback_host = urlparse(redirect_uri).hostname or "necunoscut"
    safe_callback = html.escape(callback_host)
    message = '<p class="error">Email sau parolă incorectă ori cont blocat temporar.</p>' if error else ""
    body = f"""<!doctype html>
<html lang="ro"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Autentificare MCP</title>
<style>
*{{box-sizing:border-box}} body{{font-family:system-ui,-apple-system,sans-serif;background:#f5f7fa;color:#202936;
min-height:100vh;display:grid;place-items:center;margin:0;padding:18px}}
main{{background:white;width:min(100%,390px);padding:30px;border:1px solid #e1e7ee;border-radius:12px}}
h1{{font-size:24px;margin:0 0 12px}}p{{color:#536174;line-height:1.5}}
label{{display:block;margin-top:18px;margin-bottom:6px;font-weight:600}}
input{{width:100%;font:inherit;padding:11px;border:1px solid #b2bdcc;border-radius:6px}}
button{{width:100%;font:inherit;background:#164194;color:white;border:0;border-radius:6px;
padding:12px;margin-top:23px;cursor:pointer}}
small{{display:block;margin-top:18px;color:#687589}}
.error{{color:#b01727}}
</style></head>
<body><main>
<h1>Autentificare MCP</h1>
<p>Conectare securizată pentru <strong>{safe_client}</strong>. Prin autentificare autorizezi accesul acestui client la instrumentele MCP.</p>
<p>Destinația autorizării: <strong>{safe_callback}</strong>. Verifică destinația înainte să introduci parola.</p>
{message}
<form method="post" action="/login" autocomplete="on">
<input type="hidden" name="ticket" value="{safe_ticket}">
<label for="email">Email</label><input id="email" name="email" type="email" autocomplete="username" required maxlength="254">
<label for="password">Parolă</label><input id="password" name="password" type="password" autocomplete="current-password" required maxlength="1024">
<button type="submit">Autentificare și autorizare</button>
</form><small>Acces permis numai utilizatorilor configurați de administrator.</small>
</main></body></html>"""
    response = HTMLResponse(body)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response


async def get_login(request: Request):
    ticket = request.query_params.get("ticket", "")
    pending = await provider.pending(ticket)
    if not pending:
        return PlainTextResponse("Sesiunea de autorizare este invalidă sau expirată.", status_code=400)
    client_name = pending["client"].get("client_name") or "MCP client"
    response = page(ticket, client_name, pending["data"]["redirect_uri"])
    response.set_cookie(
        "mcp_login_challenge", login_cookie(ticket),
        max_age=600, path="/login", secure=parsed.scheme == "https",
        httponly=True, samesite="lax",
    )
    return response


def get_login_ip(request: Request) -> str:
    """Use the IP supplied by the trusted host reverse proxy.

    The MCP container is reachable only on host loopback and its private Docker
    network. Our host Nginx overwrites X-Real-IP with the actual remote address.
    Never use this header if the container is publicly exposed without a
    trusted reverse proxy.
    """
    peer = request.client.host if request.client else "unknown"
    try:
        if ip_address(peer).is_private:
            forwarded = request.headers.get("x-real-ip", "")
            if forwarded:
                return str(ip_address(forwarded))
    except ValueError:
        pass
    return peer


async def post_login(request: Request):
    async with request.form(max_fields=5, max_files=0) as form:
        ticket = str(form.get("ticket") or "")
        email = str(form.get("email") or "")
        password = str(form.get("password") or "")
    if not ticket or len(ticket)>256 or len(email)>254 or len(password)>1024:
        return PlainTextResponse("Cerere invalidă.", status_code=400)
    received = request.cookies.get("mcp_login_challenge", "")
    if not received or not hmac.compare_digest(received, login_cookie(ticket)):
        return PlainTextResponse("Verificarea sesiunii a eșuat.", status_code=403)
    pending = await provider.pending(ticket)
    if not pending:
        return PlainTextResponse("Sesiunea a expirat. Reîncearcă din ChatGPT.", status_code=400)
    ip = get_login_ip(request)
    user_id = await get_active_user_id(email, password, ip)
    if user_id is None:
        return page(ticket, pending["client"].get("client_name") or "MCP client", pending["data"]["redirect_uri"], error="invalid")
    redirect_url = await provider.complete_login(ticket, user_id)
    if not redirect_url:
        return PlainTextResponse("Sesiunea a expirat. Reîncearcă din ChatGPT.", status_code=400)
    response = RedirectResponse(redirect_url, status_code=303)
    response.delete_cookie("mcp_login_challenge", path="/login")
    response.headers["Cache-Control"] = "no-store"
    return response


async def health(_request: Request):
    try:
        ok = await healthy()
    except Exception:
        ok = False
    return JSONResponse({"status": "ok" if ok else "unavailable"}, status_code=200 if ok else 503)


async def index(_request: Request):
    return PlainTextResponse("TUV Private MCP. Endpoint: /mcp")


# FastMCP 4.1 native DNS-rebinding guard: validate Host and Origin for every
# route, not only /mcp. Health checks use container-local loopback.
app = mcp.http_app(
    path="/mcp",
    stateless_http=True,
    json_response=True,
    host_origin_protection=True,
    allowed_hosts=[parsed.hostname, "127.0.0.1", "localhost"],
    allowed_origins=[BASE_URL],
)
# The underlying SDK does not enforce the resource field at /token.
app.add_middleware(TokenResourceGuard, resource_url=BASE_URL + "/mcp")
app.routes.extend([
    Route("/login", get_login, methods=["GET"]),
    Route("/login", post_login, methods=["POST"]),
    Route("/healthz", health, methods=["GET"]),
    Route("/", index, methods=["GET"]),
])
