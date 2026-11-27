"""Fixed-host, public-IP-only HTTPS fetcher. No cookies or arbitrary headers."""
import base64
import http.client
import ipaddress
import json
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote

HOST = "tuvkarpat.ro"
MAX_BODY = 4_000_000
SLOTS = threading.BoundedSemaphore(8)
SSL = ssl.create_default_context()


def validate_url(url):
    if not isinstance(url, str) or len(url) > 4000 or any(c in url for c in "\r\n\x00\\"):
        raise ValueError("Invalid URL")
    p = urlsplit(url)
    if p.scheme != "https" or p.netloc != HOST or p.fragment or not p.path.startswith("/"):
        raise ValueError("Only exact public HTTPS host allowed")
    path = unquote(p.path).lower()
    if any(part in (".", "..") for part in path.split("/")) or path.startswith(
        ("/wp-admin", "/wp-login", "/xmlrpc.php", "/wp-json/")
    ):
        raise ValueError("Administrative/API paths are not browser resources")
    return p


def public_addresses(host):
    addresses = list(dict.fromkeys(x[4][0] for x in socket.getaddrinfo(
        host, 443, type=socket.SOCK_STREAM
    )))
    if not addresses or any(not ipaddress.ip_address(x).is_global or
                            ipaddress.ip_address(x).is_multicast for x in addresses):
        raise ValueError("DNS did not resolve exclusively to public addresses")
    return addresses


class PinnedHTTPS(http.client.HTTPSConnection):
    def connect(self):
        last = None
        for address in public_addresses(HOST):
            try:
                sock = socket.create_connection((address, 443), timeout=5)
                try:
                    self.sock = SSL.wrap_socket(sock, server_hostname=HOST)
                except Exception:
                    sock.close()
                    raise
                return
            except OSError as exc:
                last = exc
        raise OSError("Public HTTPS connection failed") from last


def fetch(url):
    p = validate_url(url)
    connection = PinnedHTTPS(HOST, timeout=8)
    try:
        connection.request("GET", p.path + ("?" + p.query if p.query else ""), headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36",
            "Accept": "*/*", "Accept-Encoding": "identity", "Connection": "close",
        })
        response = connection.getresponse()
        # A challenge response is not the requested public document or resource.
        # Preserve that distinction; do not attempt to solve the challenge.
        if (response.getheader("cf-mitigated") or "").strip().lower() == "challenge":
            raise ValueError("Cloudflare challenge response")
        if 300 <= response.status < 400:
            raise ValueError("Redirects are blocked")
        if response.status != 200:
            raise ValueError("Upstream HTTP " + str(response.status))
        body = response.read(MAX_BODY + 1)
        if len(body) > MAX_BODY:
            raise ValueError("Upstream response too large")
        headers = {k.lower(): v for k, v in response.getheaders()
                   if k.lower() in {"content-type", "content-encoding", "content-security-policy",
                                    "x-content-type-options", "access-control-allow-origin"}}
        return {"status": 200, "headers": headers, "body": base64.b64encode(body).decode("ascii")}
    finally:
        connection.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.respond(200 if self.path == "/healthz" else 404, {"status": "ok"})

    def do_POST(self):
        if self.path != "/fetch":
            return self.respond(404, {"error": "not found"})
        if not SLOTS.acquire(blocking=False):
            return self.respond(503, {"error": "busy"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 6000:
                raise ValueError("Invalid body size")
            data = json.loads(self.rfile.read(size))
            if set(data) != {"url"}:
                raise ValueError("Invalid payload")
            self.respond(200, fetch(data["url"]))
        except Exception as exc:
            self.respond(400, {"error": str(exc)[:200] if isinstance(exc, ValueError) else "fetch failed"})
        finally:
            SLOTS.release()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8081), Handler).serve_forever()
