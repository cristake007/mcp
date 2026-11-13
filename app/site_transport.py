"""Restricted HTTPS transport for reading the public TÜV Karpat website.

This module owns the fixed-host allowlist, fixed-argument curl invocation,
per-request refresh context, and HTTP cache evidence. It deliberately does not
follow redirects, execute shell commands, or accept arbitrary remote hosts.

It does not know about FastMCP, WordPress entities, audit persistence or RAG.
"""
import contextvars
import subprocess
import tempfile
import uuid
from urllib.parse import urlsplit

ROOT = "https://tuvkarpat.ro/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
    "Gecko/20100101 Firefox/128.0"
)
MAX_SOURCE_BYTES = 4_000_000
_REFRESH = contextvars.ContextVar("site_refresh", default=False)
_EVIDENCE = contextvars.ContextVar("site_evidence", default=None)
_EXACT_URL = contextvars.ContextVar("site_exact_url", default=False)


def allowed(url: str) -> str:
    """Do not accept arbitrary URLs, redirects, ports or userinfo."""
    if not isinstance(url, str) or any(c in url for c in "\r\n\x00"):
        raise ValueError("URL invalid")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "tuvkarpat.ro"
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise ValueError("Sunt acceptate doar URL-uri HTTPS de pe tuvkarpat.ro")
    return url


def fetch(url: str, accept: str = "text/html,*/*") -> str:
    """Execute a fixed, non-shell curl invocation; do not follow redirects."""
    url = allowed(url)
    refresh = _REFRESH.get()
    headers_file = tempfile.TemporaryFile() if refresh else None
    extra = []
    if refresh:
        url += ("&" if "?" in url else "?") + "_mcp_refresh=" + uuid.uuid4().hex
        extra = ["-H", "Cache-Control: no-cache, no-store, max-age=0",
                 "-H", "Pragma: no-cache", "-D", "/dev/fd/" + str(headers_file.fileno())]
    try:
        result = subprocess.run(
            [
                "curl", "-fsS", "--compressed",
                "--proto", "=https", "--connect-timeout", "8",
                "--max-time", "20", "--max-filesize", str(MAX_SOURCE_BYTES),
                "-A", USER_AGENT,
                "-H", "Accept: " + accept,
                "-H", "Referer: " + ROOT,
                "-w", "\n%{http_code}",
                *extra, url,
            ],
            capture_output=True,
            timeout=23,
            check=False,
            **({"pass_fds": (headers_file.fileno(),)} if refresh else {}),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Cererea către site a eșuat sau a expirat") from exc
    finally:
        if headers_file is not None:
            headers_file.seek(0)
            raw_headers = headers_file.read().decode("iso-8859-1")
            headers_file.close()
            headers = {}
            for line in raw_headers.splitlines():
                if line.startswith("HTTP/"):
                    headers = {}
                elif ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.lower()] = value.strip()
            observed = {k: v for k, v in headers.items() if k in (
                "age", "cf-cache-status", "x-cache", "x-cache-status",
                "x-litespeed-cache", "x-proxy-cache", "cache-control", "via")}
            hit = any("hit" in v.lower() or "stale" in v.lower()
                      for k, v in observed.items() if k not in ("cache-control", "via"))
            hit = hit or (headers.get("age", "").isdigit() and int(headers["age"]) > 0)
            evidence = _EVIDENCE.get()
            if evidence is not None:
                evidence.append({"url": url.split("_mcp_refresh=")[0].rstrip("?&"),
                                 "headers": observed, "cache_hit": hit})
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError("curl a eșuat: " + (detail[:250] or str(result.returncode)))
    body, separator, status = result.stdout.rpartition(b"\n")
    if not separator or status.strip() != b"200":
        raise RuntimeError("Răspuns HTTP neașteptat (nu urmărim redirecturi): " +
                           status.decode("ascii", errors="replace")[:20])
    if len(body) > MAX_SOURCE_BYTES:
        raise RuntimeError("Răspuns prea mare")
    return body.decode("utf-8-sig", errors="replace")
