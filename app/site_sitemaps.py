"""Pure sitemap parsing and archive-filtering rules.

No HTTP, filesystem or database access; the caller decides which URLs
to trust and how to validate the host.
"""
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit


def xml_locations(xml: str, kind: str) -> list[str]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise RuntimeError("Sitemap-ul nu conține XML valid") from exc
    root_name = root.tag.split("}")[-1]
    expected = "sitemapindex" if kind == "sitemap" else "urlset"
    if root_name != expected:
        raise RuntimeError("Tip de sitemap neașteptat: " + root_name)
    return [
        loc.text.strip()
        for item in root
        if item.tag.split("}")[-1] == ("sitemap" if kind == "sitemap" else "url")
        for loc in item
        if loc.tag.split("}")[-1] == "loc" and loc.text and loc.text.strip()
    ]


def index_pages_in_sitemap(sitemap_url: str, urls: list[str]) -> tuple[list[str], list[str]]:
    """Exclude an archive landing page from individual-entry audit batches.

    For cursuri-sitemap.xml, /cursuri/ is the archive, not an ACF course.
    This does not affect the normal urls or get actions.
    """
    filename = urlsplit(sitemap_url).path.rsplit("/", 1)[-1]
    match = re.fullmatch(r"([a-z0-9_-]+)-sitemap(?:\d+)?\.xml", filename)
    if match is None:
        return list(urls), []
    archive_path = "/" + match.group(1)
    entries, skipped = [], []
    for item in urls:
        parsed = urlsplit(item)
        if not parsed.query and parsed.path.rstrip("/") == archive_path:
            skipped.append(item)
        else:
            entries.append(item)
    return entries, skipped
