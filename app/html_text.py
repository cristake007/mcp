"""Pure HTML-to-visible-text extraction shared by WordPress site services.

No HTTP fetching, database access, MCP registration or site module imports.
"""
import re

from bs4 import BeautifulSoup
from bs4.element import Comment, NavigableString, Tag


# Preserve text from all ACF source fields and unknown Elementor elements.
# Structural separators retain context from headings, lists and table cells.
BLOCK_TAGS = {
    "article", "aside", "blockquote", "dd", "details", "div", "dl", "dt",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
    "h5", "h6", "header", "hr", "li", "main", "ol", "p", "pre",
    "section", "summary", "table", "tbody", "tfoot", "thead", "tr", "ul",
}

def visible_text(html_or_text: str) -> str:
    """Keep all captured text nodes, including unknown widgets and table cells.

    This processes the returned HTML, not JavaScript-rendered DOM. It does not
    attempt Romanian-language correction, filtering or candidate detection.
    """
    soup = BeautifulSoup(html_or_text, "html.parser")
    for element in soup.select("script, style, meta, iframe, noscript"):
        element.decompose()
    pieces = []

    def visit(node):
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            pieces.append(str(node))
            return
        if not isinstance(node, Tag):
            return
        if node.name in BLOCK_TAGS or node.name == "br":
            pieces.append("\n")
        if node.name == "img" and node.get("alt"):
            pieces.append(" [imagine alt: " + node["alt"] + "] ")
        for child in node.children:
            visit(child)
        if node.name in {"td", "th"}:
            pieces.append("\t")
        elif node.name in BLOCK_TAGS:
            pieces.append("\n")

    visit(soup)
    text = "".join(pieces)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


REMOVE_HTML = (
    "script, meta, iframe, noscript, "
    "body > header, body > footer, "
    ".elementor-location-header, .elementor-location-footer"
)

def clean_html(raw: str) -> str:
    soup = BeautifulSoup(raw, "html.parser")
    for element in soup.select(REMOVE_HTML):
        element.decompose()
    # Do not prettify: inserted indentation/newlines can split inline words.
    return str(soup.body or soup)
