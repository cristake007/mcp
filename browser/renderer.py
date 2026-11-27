"""Fresh Chromium contexts; all HTTP is mediated by the fixed-host gateway."""
import asyncio
import base64
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from playwright.async_api import async_playwright

PROFILES = {"desktop": (1440, 900), "tablet": (768, 1024), "mobile": (390, 844)}
CHECK = Path(__file__).with_name("visibility.js").read_text()
SLOT = threading.BoundedSemaphore(1)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def allowed(url):
    if not isinstance(url, str) or len(url) > 4000 or any(c in url for c in "\r\n\x00\\"):
        return False
    p = urlsplit(url)
    return p.scheme == "https" and p.netloc == "tuvkarpat.ro" and p.path.startswith("/") and not p.fragment


def gateway(url):
    req = Request("http://browser-fetch:8081/fetch",
                  data=json.dumps({"url": url}).encode(),
                  headers={"Content-Type": "application/json"}, method="POST")
    with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=12) as response:
        raw = response.read(6_000_001)
    if len(raw) > 6_000_000:
        raise ValueError("Gateway response too large")
    data = json.loads(raw)
    return data["status"], data["headers"], base64.b64decode(data["body"], validate=True)


async def inspect(url, texts, profile):
    width, height = PROFILES[profile]
    failures = []
    counters = {"requests": 0, "bytes": 0, "blocked_external": 0, "critical_failures": 0}
    pending = asyncio.Semaphore(8)
    async with async_playwright() as pw:
        # Container has no internet route. The dead proxy is defense in depth;
        # only explicitly fulfilled requests can obtain public resource bytes.
        browser = await pw.chromium.launch(headless=True, proxy={"server":"http://127.0.0.1:9"},
            args=["--proxy-bypass-list=<-loopback>", "--disable-background-networking"])
        try:
            context = await browser.new_context(viewport={"width":width,"height":height},
                device_scale_factor=1, is_mobile=profile=="mobile", has_touch=profile!="desktop",
                service_workers="block", locale="ro-RO", accept_downloads=False)
            async def route_request(route):
                request = route.request
                kind = request.resource_type
                reason = None
                try:
                    counters["requests"] += 1
                    if counters["requests"] > 200:
                        reason = "request_limit"
                    elif not allowed(request.url):
                        counters["blocked_external"] += 1
                        reason = "outside_fixed_host"
                    elif request.method != "GET":
                        reason = "non_get"
                    elif kind in ("media", "font"):
                        reason = "optional_resource_skipped"
                    else:
                        async with pending:
                            status, headers, body = await asyncio.to_thread(gateway, request.url)
                        counters["bytes"] += len(body)
                        if counters["bytes"] > 32_000_000:
                            reason = "byte_limit"
                        else:
                            await route.fulfill(status=status, headers=headers, body=body)
                            return
                except Exception:
                    reason = "fetch_failed"
                if kind in ("document","stylesheet","script") and (reason != "outside_fixed_host" or kind != "script"):
                    counters["critical_failures"] += 1
                if len(failures) < 20:
                    failures.append({"type":kind,"reason":reason,
                        "host":urlsplit(request.url).hostname or "", "path":urlsplit(request.url).path[:200]})
                try:
                    await route.abort()
                except Exception:
                    pass

            await context.route("**/*", route_request)
            page = await context.new_page()
            navigation_error = None
            try:
                response = await page.goto(url, wait_until="load", timeout=23000)
                if response is None or response.status != 200 or page.url != url:
                    raise RuntimeError("Unexpected navigation response or URL")
                await page.wait_for_timeout(750)
            except Exception:
                navigation_error = "navigation incomplete or failed"
            if navigation_error:
                result = {"matches":[{"text":t,"observed":"indeterminate","occurrences":0} for t in texts],
                          "scan_truncated":False,"title":"","inner_text":"","headings":[]}
            else:
                result = await asyncio.wait_for(page.evaluate(CHECK, {"texts":texts,"includeText":not texts}), timeout=5)
            complete = not navigation_error and not counters["critical_failures"] and not result["scan_truncated"]
            for match in result["matches"]:
                match["verdict"] = match["observed"] if complete else "indeterminate"
            return {"url":url,"profile":profile,"viewport":{"width":width,"height":height},
                    "source":"rendered_dom","fetched_at":datetime.now(timezone.utc).isoformat(),
                    "verification_scope":"restricted fresh Chromium session; computed styles and text geometry",
                    "complete":complete,"navigation_error":navigation_error,"resources":counters,
                    "resource_warnings":failures,"limitations":[
                        "Third-party resources, cookies from HTTP responses, non-GET requests and redirects are blocked.",
                        "Fonts and media are skipped; external scripts may change behavior in a normal browser.",
                        "No interaction, consent, scrolling, shadow DOM, iframe, pseudo-element or pixel occlusion verification.",
                        "Off-screen scrollable content can be visible in layout; matches spanning separate blocks may not be found."
                    ],**result}
        finally:
            await browser.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def respond(self,status,value):
        raw=json.dumps(value,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.respond(200 if self.path=="/healthz" else 404,{"status":"ok"})

    def do_POST(self):
        if self.path!="/inspect":
            return self.respond(404,{"error":"not found"})
        if not SLOT.acquire(blocking=False):
            return self.respond(503,{"error":"browser busy; retry later"})
        try:
            length=int(self.headers.get("Content-Length","0"))
            if not 0<length<=18000:
                raise ValueError("Invalid request length")
            data=json.loads(self.rfile.read(length))
            if set(data)!={"url","texts","viewport"}:
                raise ValueError("Invalid request keys")
            url,texts,profile=data["url"],data["texts"],data["viewport"]
            if not allowed(url) or len(url)>1000 or profile not in PROFILES or not isinstance(texts,list) or len(texts)>10 or any(
                not isinstance(t,str) or not t.strip() or len(t)>300 for t in texts
            ):
                raise ValueError("Invalid inspection parameters")
            self.respond(200,asyncio.run(inspect(url,texts,profile)))
        except ValueError as exc:
            self.respond(400,{"error":str(exc)[:200]})
        except Exception:
            self.respond(502,{"error":"Browser inspection failed; no visibility conclusion"})
        finally:
            SLOT.release()


if __name__=="__main__":
    ThreadingHTTPServer(("0.0.0.0",8080),Handler).serve_forever()
