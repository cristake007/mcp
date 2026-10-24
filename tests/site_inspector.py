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


    def test_all_acf_fields_preserved_with_paths_and_context(self):
        data = {
            "url": "https://tuvkarpat.ro/cursuri/test/", "format": "acf_json",
            "title": "Buna practică", "acf": {
                "tematica": "<p>Calificarea sudorilor</p>",
                "grup_tinta": "experienta profesională",
                "investitie": "100 lei",
                "reduceri": ["Pentru angajati", "Pentru formatori"],
                "dynamic_field": {"nested": "<table><tr><td>ISO 9606</td><td>Sudură</td></tr></table>"},
            },
        }
        text, warnings = site._audit_text(data)
        self.assertFalse(warnings)
        for expected in (
            "Buna practică", "[acf.tematica]", "Calificarea sudorilor",
            "[acf.grup_tinta]", "experienta profesională",
            "[acf.investitie]", "[acf.reduceri[0]]", "Pentru angajati",
            "[acf.dynamic_field.nested]", "ISO 9606", "Sudură",
        ):
            self.assertIn(expected, text)
        self.assertLess(text.index("[acf.tematica]"), text.index("[acf.grup_tinta]"))

    def test_unknown_html_widgets_and_tables_keep_visible_text(self):
        html = (
            '<body><main><div class="custom-widget"><span>special</span>'
            '<span>ist</span><details><summary>Detalii</summary>'
            '<p>experienta profesională</p></details>'
            '<table><tr><th>Standard</th><td>ISO 9606</td></tr></table>'
            '<img alt="certificare sudori">'
            '<script>REMOVE ME</script></div></main></body>'
        )
        text = site._visible_text(site._clean_html(html))
        for expected in ("specialist", "Detalii", "experienta profesională",
                         "Standard", "ISO 9606", "certificare sudori"):
            self.assertIn(expected, text)
        self.assertNotIn("REMOVE ME", text)

    def test_batch_get_and_recover_every_character_of_large_page(self):
        url = "https://tuvkarpat.ro/cursuri/mare/"
        # Deliberately split a sentence across fixed-size parts; joining
        # parts must reproduce every original character exactly.
        long_content = "Învață prin practică. " * 1600
        data = {"url": url, "format": "acf_json",
                "id": 19, "title": "Specialist", "acf": {"tematica": long_content}}
        with (
            patch.object(site, "_sitemaps", return_value=["https://tuvkarpat.ro/cursuri-sitemap.xml"]),
            patch.object(site, "_urls", return_value=[url]),
            patch.object(site, "_read_page", return_value=data),
        ):
            batch = site.inspect_site("batch_get", sitemap="cursuri-sitemap.xml")
            self.assertEqual(batch["processed"], 1)
            self.assertEqual(batch["total_urls"], 1)
            self.assertTrue(batch["end_of_sitemap"])
            self.assertFalse(batch["batch_complete"])
            self.assertIsNone(batch["next_offset"])
            self.assertEqual(len(batch["pending_parts"]), 1)
            page = batch["pages"][0]
            self.assertEqual(page["next_part"], 1)
            fragments = [page["content"]]
            next_part = page["next_part"]
            while next_part is not None:
                result = site.inspect_site(
                    "get", view="text", url=url, part=next_part,
                    max_chars=12000, expected_hash=page["sha256"]
                )
                self.assertEqual(result["sha256"], page["sha256"])
                fragments.append(result["content"])
                next_part = result["next_part"]
            expected, warnings = site._audit_text(data)
            self.assertFalse(warnings)
            self.assertEqual("".join(fragments), expected)
            self.assertEqual(len(expected), page["total_chars"])

    def test_batch_get_handles_errors_and_limited_pages(self):
        urls = [f"https://tuvkarpat.ro/cursuri/{i}/" for i in range(8)]
        def read(url):
            if url.endswith("/2/"):
                raise RuntimeError("HTTP 429")
            return {"url": url, "format": "acf_json",
                    "id": 20, "title": "Curs", "acf": {"tematica": "Conținut"}}

        with (
            patch.object(site, "_sitemaps", return_value=["https://tuvkarpat.ro/cursuri-sitemap.xml"]),
            patch.object(site, "_urls", return_value=urls),
            patch.object(site, "_read_page", side_effect=read) as reader,
        ):
            result = site.inspect_site("batch_get", sitemap="cursuri-sitemap.xml", limit=25)
            self.assertEqual(result["processed"], 5)
            self.assertEqual(result["next_offset"], 5)
            self.assertFalse(result["batch_complete"])
            self.assertEqual(len(result["errors"]), 1)
            self.assertTrue(result["errors"][0]["url"].endswith("/2/"))
            self.assertEqual(reader.call_count, 5)
            last = site.inspect_site("batch_get", sitemap="cursuri-sitemap.xml", offset=5)
            self.assertEqual(last["processed"], 3)
            self.assertTrue(last["batch_complete"])
            self.assertTrue(last["end_of_sitemap"])
            self.assertIsNone(last["next_offset"])

    def test_get_raw_large_acf_is_split_not_silently_cut(self):
        url = "https://tuvkarpat.ro/cursuri/mare/"
        data = {"url": url, "format": "acf_json", "id": 9, "title": "Nume",
                "acf": {"text": "Experiență profesională. " * 150}}
        with patch.object(site, "_read_page", return_value=data):
            first = site.inspect_site("get", url=url, max_chars=1000)
            self.assertFalse(first["content_complete"])
            self.assertEqual(first["encoding"], "json")
            pieces = [first["fragment"]]
            next_part = first["next_part"]
            while next_part is not None:
                page = site.inspect_site("get", url=url, max_chars=1000,
                                         part=next_part, expected_hash=first["sha256"])
                pieces.append(page["fragment"])
                next_part = page["next_part"]
            import json
            decoded = json.loads("".join(pieces))
            self.assertEqual(decoded["acf"], data["acf"])
            with self.assertRaisesRegex(ValueError, "schimbat"):
                site.inspect_site("get", url=url, view="text", expected_hash="0" * 64)

    def test_batch_rejects_all_sitemap_and_unknown_sitemap(self):
        with self.assertRaisesRegex(ValueError, "explicit"):
            site.inspect_site("batch_get", sitemap="all")
        with patch.object(site, "_sitemaps", return_value=[]):
            with self.assertRaises(ValueError):
                site.inspect_site("batch_get", sitemap="other-sitemap.xml")


if __name__ == "__main__":
    unittest.main()
