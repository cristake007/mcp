"""Offline tests for inspect_site: never call public WordPress or Cloudflare."""
import subprocess
import unittest
from unittest.mock import patch

from app import site_inspector as site


INDEX = """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://tuvkarpat.ro/post-sitemap.xml</loc></sitemap>
<sitemap><loc>https://tuvkarpat.ro/cursuri-sitemap.xml</loc></sitemap></sitemapindex>"""
URLSET = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://tuvkarpat.ro/2022/10/13/articol/</loc></url>
<url><loc>https://tuvkarpat.ro/2023/04/20/alt-articol/</loc></url></urlset>"""


class SiteInspectorTests(unittest.TestCase):
    def test_fixed_domain_and_redirect_restrictions(self):
        for url in (
            "https://other.example/", "http://tuvkarpat.ro/",
            "https://tuvkarpat.ro.evil.com/",
            "https://user@tuvkarpat.ro/", "https://tuvkarpat.ro:8443/",
            "https://tuvkarpat.ro/path#fragment",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                site._allowed(url)

    def test_curl_uses_fixed_args_and_rejects_redirect(self):
        ok = subprocess.CompletedProcess([], 0, stdout=b"<body>ok</body>\n200", stderr=b"")
        with patch.object(site.subprocess, "run", return_value=ok) as run:
            self.assertEqual(site._fetch("https://tuvkarpat.ro/test/"), "<body>ok</body>")
            args = run.call_args.args[0]
            self.assertIn("--proto", args)
            self.assertIn("=https", args)
            self.assertIn("Referer: https://tuvkarpat.ro/", args)
            self.assertNotIn("-L", args)
            self.assertFalse(run.call_args.kwargs.get("shell", False))
        redirect = subprocess.CompletedProcess([], 0, stdout=b"redirect\n301", stderr=b"")
        with patch.object(site.subprocess, "run", return_value=redirect):
            with self.assertRaisesRegex(RuntimeError, "redirecturi"):
                site._fetch("https://tuvkarpat.ro/test/")

    def test_sitemap_and_url_pagination(self):
        def mock_fetch(url, accept=""):
            return INDEX if url.endswith("sitemap_index.xml") else URLSET

        with patch.object(site, "_fetch", side_effect=mock_fetch):
            root = site.inspect_site("sitemaps")
            self.assertEqual(len(root["sitemaps"]), 2)
            urls = site.inspect_site("urls", sitemap="post-sitemap.xml", limit=1)
            self.assertEqual(urls["total"], 2)
            self.assertEqual(urls["next_offset"], 1)
            second = site.inspect_site("urls", sitemap="post-sitemap.xml", offset=1)
            self.assertIsNone(second["next_offset"])
            with self.assertRaises(ValueError):
                site.inspect_site("urls", sitemap="unknown-sitemap.xml")

    def test_preserve_tables_links_widgets_and_remove_shell(self):
        raw = """<html><head><meta name="x"></head><body>
<header>HEADER</header><div class="elementor-location-header">DUPLICATE HEADER</div>
<main><div class="elementor-widget"><table><tr><td>ISO 9606</td></tr></table>
<a href="/document">Document</a><details><summary>Tab</summary>Text necunoscut</details>
</div><iframe>NO IFRAME</iframe><noscript>NO NOSCRIPT</noscript></main>
<footer>FOOTER</footer><div class="elementor-location-footer">DUPLICATE FOOTER</div>
<script>NO SCRIPT</script></body></html>"""
        cleaned = site._clean_html(raw)
        for text in ("ISO 9606", "<table>", 'href="/document"', "<details>", "Text necunoscut"):
            self.assertIn(text, cleaned)
        for text in ("HEADER", "FOOTER", "NO SCRIPT", "NO IFRAME", "NO NOSCRIPT", "<meta"):
            self.assertNotIn(text, cleaned)

    def test_post_and_acf_and_html_fallback(self):
        post = '{"id":12,"title":{"rendered":"Articol"},"content":{"rendered":"<p>ISO 9606</p>"}}'
        course = '{"id":34,"title":{"rendered":"Sudură"},"link":"https://tuvkarpat.ro/cursuri/curs/","acf":{"durata":"2 zile"}}'
        with patch.object(site, "_fetch", return_value="["+post+"]"):
            got = site.inspect_site("get", url="https://tuvkarpat.ro/2022/10/13/articol/")
            self.assertEqual(got["format"], "post_html")
            self.assertEqual(got["html"], "<p>ISO 9606</p>")
        with patch.object(site, "_fetch", return_value="["+course+"]"):
            got = site.inspect_site("get", url="https://tuvkarpat.ro/cursuri/curs/")
            self.assertEqual(got["format"], "acf_json")
            self.assertEqual(got["acf"]["durata"], "2 zile")
        with patch.object(site, "_fetch", side_effect=["[]", "<body><main><p>Text păstrat</p></main></body>"]):
            got = site.inspect_site("get", url="https://tuvkarpat.ro/2022/10/13/articol/")
            self.assertEqual(got["format"], "page_html")
            self.assertIn("Text păstrat", got["html"])

    def test_search_literal_and_partial_scan(self):
        urls = [
            ("https://tuvkarpat.ro/post/1", "https://tuvkarpat.ro/post-sitemap.xml"),
            ("https://tuvkarpat.ro/post/2", "https://tuvkarpat.ro/post-sitemap.xml"),
            ("https://tuvkarpat.ro/post/3", "https://tuvkarpat.ro/post-sitemap.xml"),
        ]
        def fake_page(url):
            return {"url": url, "format": "page_html",
                    "html": "<main><nav>ISO 9606 in menu</nav><p>ISO 9606 live</p></main>"}
        with patch.object(site, "_search_entries", return_value=urls), patch.object(site, "_read_page", side_effect=fake_page):
            first = site.inspect_site("search", query="iso 9606", limit=2)
            self.assertFalse(first["complete"])
            self.assertEqual(first["next_offset"], 2)
            self.assertEqual([x["occurrences"] for x in first["matches"]], [1, 1])
            second = site.inspect_site("search", query="iso 9606", offset=2, limit=2)
            self.assertTrue(second["complete"])
            self.assertIsNone(second["next_offset"])


if __name__ == "__main__":
    unittest.main()
