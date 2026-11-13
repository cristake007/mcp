"""Private, opt-in Google Search Console read-only API transport.

Only official Google endpoints are contacted; OAuth scopes cannot request writes.
The service-account key is mounted as a read-only file, never logged or passed
to tool parameters. Neither importing this module nor starting FastMCP requires
a key: credentials are loaded only when a GSC operation is invoked.
"""
import json
import os
import threading
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.site_transport import allowed

READONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
CREDENTIALS_PATH = "/run/secrets/gsc.json"
PROPERTY_CHOICES = frozenset(("sc-domain:tuvkarpat.ro", "https://tuvkarpat.ro/"))
MAX_RESPONSE_BYTES = 1_000_000
_lock = threading.Lock()


def property_url() -> str:
    """Bind all calls to the company's Search Console property."""
    value = os.environ.get("MCP_GSC_SITE_URL", "sc-domain:tuvkarpat.ro")
    if value not in PROPERTY_CHOICES:
        raise RuntimeError(
            "MCP_GSC_SITE_URL trebuie să fie sc-domain:tuvkarpat.ro "
            "sau https://tuvkarpat.ro/"
        )
    return value


@lru_cache(maxsize=1)
def _credentials():
    from google.oauth2 import service_account

    try:
        credentials = service_account.Credentials.from_service_account_file(
            CREDENTIALS_PATH, scopes=[READONLY_SCOPE],
        )
    except (OSError, ValueError, KeyError) as exc:
        raise RuntimeError(
            "Acreditările GSC nu pot fi citite din container. "
            "Verifică montarea read-only și permisiunile fișierului."
        ) from exc
    if not credentials.service_account_email.endswith(
        "@automatizare-cursurituv-ro.iam.gserviceaccount.com"
    ):
        raise RuntimeError("Service Account-ul GSC nu corespunde proiectului aprobat.")
    return credentials


def _access_token() -> str:
    from google.auth.transport.requests import Request as AuthRequest

    credentials = _credentials()
    with _lock:
        if not credentials.valid:
            try:
                credentials.refresh(AuthRequest())
            except Exception as exc:
                # Do not expose private-key data or credential-bearing responses.
                raise RuntimeError(
                    "Autentificarea Google GSC a eșuat. Verifică accesul "
                    "Service Account-ului și accesul la internet."
                ) from exc
        return credentials.token


def post_json(endpoint: str, payload: dict) -> dict:
    """POST a bounded JSON body to one of two fixed Google API endpoints.

    These are reporting methods: their POST verb does not modify Search Console.
    """
    if endpoint not in (
        "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
    ) and not (
        endpoint.startswith("https://www.googleapis.com/webmasters/v3/sites/")
        and endpoint.endswith("/searchAnalytics/query")
    ):
        raise ValueError("Endpoint Google nepermis")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(body) > 8_192:
        raise ValueError("Cerere GSC prea mare")
    request = Request(
        endpoint, data=body, method="POST", headers={
            "Authorization": "Bearer " + _access_token(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=25) as response:
            if response.status != 200:
                raise RuntimeError("Google GSC a returnat un răspuns neașteptat")
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        # Never include the body: Google may echo private or sensitive data.
        if exc.code in (401, 403):
            raise RuntimeError(
                f"Google Search Console a refuzat accesul (HTTP {exc.code}). "
                "Verifică permisiunile Service Account-ului și proprietatea GSC."
            ) from None
        if exc.code == 429:
            raise RuntimeError("Google Search Console: limita API a fost atinsă (429).") from None
        raise RuntimeError(f"Google Search Console: eroare HTTP {exc.code}.") from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError("Google Search Console nu este disponibil.") from None
    if len(data) > MAX_RESPONSE_BYTES:
        raise RuntimeError("Răspunsul GSC depășește limita de 1 MB.")
    try:
        result = json.loads(data)
    except ValueError as exc:
        raise RuntimeError("Google GSC a returnat un JSON invalid.") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Google GSC a returnat un format invalid.")
    return result


def search_analytics(payload: dict) -> dict:
    site_url = property_url()
    endpoint = (
        "https://www.googleapis.com/webmasters/v3/sites/"
        + quote(site_url, safe="") + "/searchAnalytics/query"
    )
    return post_json(endpoint, payload)


def inspect_url(url: str) -> dict:
    if not isinstance(url, str) or not 1 <= len(url) <= 2_000:
        raise ValueError("URL-ul trebuie să conțină 1..2000 caractere")
    allowed(url)  # Same allowlist as the shared public-site reader.
    return post_json(
        "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
        {"inspectionUrl": url, "siteUrl": property_url(), "languageCode": "ro-RO"},
    )
