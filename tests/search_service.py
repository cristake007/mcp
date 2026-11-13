"""Offline regression coverage of the site-search service, not MCP adapters."""
import contextvars
import unittest

from app import site_search_service as service

URL1 = "https://tuvkarpat.ro/cursuri/primul/"
URL2 = "https://tuvkarpat.ro/cursuri/altul/"
SM = "https://tuvkarpat.ro/cursuri-sitemap.xml"
ENTRIES = [(URL1, SM), (URL2, SM)]


class SearchServiceTests(unittest.TestCase):
    def test_multi_term_search_reuses_one_fetch_per_page(self):
        calls = []
        def read(url):
            calls.append(url)
            return {"url": url, "format": "acf_json", "title": "Introducere",
                    "acf": {"tematica": "Țară, știință, ISO 9001"}}
        def visit(entry, terms, mode):
            return service.search_many_one(entry, terms, mode, read_page=read)
        result = service.search_many(
            ["tara", "stiinta"], "accent_insensitive", "", 0, 20,
            list_entries=lambda _: ENTRIES, inspect_one=visit,
        )
        self.assertTrue(result["complete"])
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["query_totals"][0]["matching_urls"], 2)
        self.assertEqual(result["query_totals"][1]["occurrences"], 2)
        span = result["matches"][0]["terms"][0]["positions"][0]
        source = service.as_search_text({
            "url": URL1, "format": "acf_json", "title": "Introducere",
            "acf": {"tematica": "Țară, știință, ISO 9001"},
        })
        self.assertEqual(source[span["start"]:span["end"]], "Țară")
        self.assertCountEqual(calls, [URL1, URL2])

    def test_context_is_isolated_per_thread(self):
        marker = contextvars.ContextVar("refresh-evidence", default=None)
        token = marker.set("fresh")
        try:
            observed = []
            def visit(entry, terms, mode):
                observed.append(marker.get())
                return None
            result = service.search_many(
                ["ISO"], "exact", SM, 0, 2,
                list_entries=lambda _: ENTRIES, inspect_one=visit,
            )
            self.assertTrue(result["complete"])
            self.assertEqual(observed, ["fresh", "fresh"])
        finally:
            marker.reset(token)

    def test_pagination_error_and_legacy_compatibility(self):
        def visit(entry, terms, mode):
            if entry[0] == URL2:
                raise RuntimeError("offline")
            return {"url": URL1, "sitemap": SM, "terms": [
                {"query": terms[0], "occurrences": 2, "positions": [],
                 "snippets": []},
            ]}
        first = service.search_many(
            ["ISO"], "exact", SM, 0, 1, list_entries=lambda _: ENTRIES,
            inspect_one=visit,
        )
        self.assertEqual(first["next_offset"], 1)
        self.assertFalse(first["complete"])
        second = service.search_many(
            ["ISO"], "exact", SM, 1, 1, list_entries=lambda _: ENTRIES,
            inspect_one=visit,
        )
        self.assertIsNone(second["next_offset"])
        self.assertFalse(second["complete"])
        self.assertEqual(second["errors"][0]["url"], URL2)
        legacy = service.search_legacy(
            "ISO", SM, 0, 2, list_entries=lambda _: ENTRIES,
            inspect_one=lambda entry, term: None,
        )
        self.assertEqual(legacy["action"], "search")
        self.assertEqual(legacy["matches"], [])
        self.assertTrue(legacy["complete"])

    def test_validation_rejects_duplicates_and_empty_terms(self):
        for terms in ([], [" "], ["ISO", "iso"], ["a" * 121]):
            with self.subTest(terms=terms), self.assertRaises(ValueError):
                service.search_many(
                    terms, "exact", SM, 0, 10,
                    list_entries=lambda _: [], inspect_one=lambda *args: None,
                )
        with self.assertRaises(ValueError):
            service.search_many(
                ["ok"], "unknown", SM, 0, 10,
                list_entries=lambda _: [], inspect_one=lambda *args: None,
            )


if __name__ == "__main__":
    unittest.main()
