"""Offline regression tests for shared text matching and WordPress REST pagination."""
import json
import unittest
from urllib.parse import parse_qs, urlparse

from app.text_matching import TextMatcher, accent_fold_with_positions, find_positions
from app.wp_api import iter_collection, read_json
from app.site_sitemaps import xml_locations, index_pages_in_sitemap
from app.site_text import as_search_text, page_chunks
from app.html_text import clean_html


class SharedPrimitivesTests(unittest.TestCase):
    def test_unicode_positions_use_original_source_text(self):
        self.assertEqual(find_positions("Țară și știință", "tara", "accent_insensitive"), [(0, 4)])
        self.assertEqual(find_positions("straße", "STRASSE", "accent_insensitive"), [(0, 6)])
        self.assertEqual(find_positions("ISO 9001 ISO9001", "ISO", "whole_word"), [(0, 3)])
        self.assertEqual(find_positions("a (ISO 9606)", "(ISO 9606)", "exact"), [(2, 12)])
        folded, positions = accent_fold_with_positions("Ș")
        self.assertEqual((folded, positions), ("s", [0]))
        matcher = TextMatcher("Țară. Altă țară.", "accent_insensitive")
        self.assertEqual(matcher.positions("tara"), [(0, 4), (11, 15)])

    def test_rest_pagination_and_filter_passthrough(self):
        calls = []

        def fetch(url, accept):
            self.assertEqual(accept, "application/json")
            params = parse_qs(urlparse(url).query)
            calls.append(params)
            self.assertEqual(params.get("domenii"), ["123"])
            self.assertEqual(params.get("_fields"), ["id,slug"])
            if params["page"] == ["1"]:
                return json.dumps([{"id": i} for i in range(100)])
            return json.dumps([{"id": 100}, {"id": 101}])

        results = list(iter_collection(
            fetch, "https://tuvkarpat.ro/wp-json/wp/v2/cursuri",
            fields="id,slug", filters=(("domenii", 123),),
        ))
        self.assertEqual([len(page) for page in results], [100, 2])
        self.assertEqual(len(calls), 2)

    def test_sitemap_parsing_and_archive_exclusion(self):
        sitemap = (
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            '<url><loc>https://tuvkarpat.ro/cursuri/</loc></url>'
            '<url><loc>https://tuvkarpat.ro/cursuri/iso-9001/</loc></url>'
            '</urlset>'
        )
        urls = xml_locations(sitemap, "url")
        entries, skipped = index_pages_in_sitemap(
            "https://tuvkarpat.ro/cursuri-sitemap.xml", urls,
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0], "https://tuvkarpat.ro/cursuri/iso-9001/")
        self.assertEqual(skipped, ["https://tuvkarpat.ro/cursuri/"])
        with self.assertRaises(RuntimeError):
            xml_locations("<invalid>", "url")

    def test_pure_page_text_processing(self):
        cleaned = clean_html(
            "<body><header>Navigation</header><main><h1>Title</h1>"
            "<p>Content</p></main><footer>Footer</footer></body>"
        )
        self.assertNotIn("Navigation", cleaned)
        self.assertNotIn("Footer", cleaned)
        self.assertIn("Content", cleaned)
        text = as_search_text({
            "format": "acf_json", "title": "Title",
            "acf": {"tematica": "<p>Știință</p>"},
        })
        self.assertIn("Știință", text)
        first = page_chunks("Țară" * 300, part=0, size=1000)
        self.assertEqual(first["next_part"], 1)
        second = page_chunks("Țară" * 300, part=1, size=1000,
                             expected_hash=first["sha256"])
        self.assertEqual(first["content"] + second["content"], "Țară" * 300)
        with self.assertRaises(ValueError):
            page_chunks("schimbat", part=0, size=1000,
                        expected_hash=first["sha256"])

    def test_rest_validation(self):
        self.assertEqual(read_json(lambda url, accept: '{"ok":true}', "/x"), {"ok": True})
        with self.assertRaises(RuntimeError):
            list(iter_collection(lambda url, accept: "{}", "/api", fields="id"))
        with self.assertRaises(ValueError):
            list(iter_collection(lambda url, accept: "[]", "/api", fields="id", page_size=101))


if __name__ == "__main__":
    unittest.main()
