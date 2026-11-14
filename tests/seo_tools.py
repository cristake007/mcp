"""Offline SEO source and exact-page GSC contracts; no live HTTP or credentials."""
import asyncio
import os
import unittest
from unittest.mock import patch

from fastmcp import FastMCP
from pydantic import TypeAdapter

from app import seo_page, seo_tools
from app.server import mcp


URL = "https://tuvkarpat.ro/cursuri/certificare-in-metoda-de-control-nedistructiv-examinare-cu-ultrasunete/"
HTML = """<!doctype html><html lang="ro-RO"><head>
<title>Curs UT - TÜV Thüringen Karpat</title>
<meta name="description" content="Instruire de examinare cu ultrasunete.">
<meta name="robots" content="index, follow">
<meta property="og:title" content="Control nedistructiv UT">
<link rel="canonical" href="https://tuvkarpat.ro/cursuri/certificare-in-metoda-de-control-nedistructiv-examinare-cu-ultrasunete/">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"WebPage"},{"@type":"Course","name":"UT","provider":{"@type":"Organization"}}]}</script>
</head><body><h1>Examinare cu ultrasunete</h1><h2>Tematică</h2></body></html>"""


class SeoTests(unittest.TestCase):
    def test_html_metadata_no_inference(self):
        with patch.object(seo_page.site, "fetch", return_value=HTML) as fetch:
            data = seo_tools.seo_inspect_page(URL)
        fetch.assert_called_once_with(URL, "text/html,application/xhtml+xml")
        self.assertEqual(data["title"], "Curs UT - TÜV Thüringen Karpat")
        self.assertEqual(data["meta_description"], "Instruire de examinare cu ultrasunete.")
        self.assertEqual(data["canonical"], URL)
        self.assertEqual(data["headings"][0]["text"], "Examinare cu ultrasunete")
        self.assertEqual(data["jsonld_types"], ["Course", "WebPage"])
        self.assertEqual(data["course_jsonld_count"], 1)
        self.assertIn("provider", data["course_jsonld_fields"])
        TypeAdapter(seo_tools.SeoPageResult).validate_python(data)

    def test_no_redirects_or_external_urls_and_no_network_for_invalid_requests(self):
        for url in ("https://evil.example/cursuri/test/", "http://tuvkarpat.ro/cursuri/test/",
                    "https://tuvkarpat.ro/cursuri/test/?a=1",
                    "https://tuvkarpat.ro/wp-admin/", "https://tuvkarpat.ro/cursuri/"):
            with self.subTest(url=url):
                with patch.object(seo_page.site, "fetch") as fetch:
                    with self.assertRaises(ValueError):
                        seo_tools.seo_inspect_page(url)
                    fetch.assert_not_called()
        with patch.object(seo_page.site, "fetch") as fetch:
            with self.assertRaises(ValueError):
                seo_tools.seo_inspect_page(URL, force_refresh="yes")
            fetch.assert_not_called()

    def test_malformed_json_ld_does_not_hide_meta(self):
        html = HTML.replace('{"@context":"https://schema.org","@graph":[{"@type":"WebPage"},{"@type":"Course","name":"UT","provider":{"@type":"Organization"}}]}', "{broken")
        data = seo_page.extract_metadata(URL, html)
        self.assertEqual(data["course_jsonld_count"], 0)
        self.assertTrue(any("JSON-LD" in warning for warning in data["warnings"]))
        self.assertEqual(data["meta_robots"], "index, follow")

    def test_exact_google_filter_and_independent_totals(self):
        raw_page = {"rows": [{"keys": [URL], "clicks": 8,
                               "impressions": 408, "ctr": 8 / 408, "position": 9.9}]}
        raw_queries = {"rows": [{"keys": ["control nedistructiv"], "clicks": 0,
                                 "impressions": 22, "ctr": 0, "position": 16.7}]}
        with patch.object(seo_tools.gsc_client, "search_analytics",
                          side_effect=[raw_page, raw_queries]) as google:
            value = seo_tools.seo_course_queries(URL, "2026-08-01", "2026-11-11")
        self.assertEqual(value["totals"]["impressions"], 408)
        self.assertEqual(value["queries"][0]["impressions"], 22)
        self.assertEqual(google.call_count, 2)
        for invocation in google.call_args_list:
            payload = invocation.args[0]
            self.assertEqual(payload["dimensionFilterGroups"][0]["filters"][0],
                             {"dimension": "page", "operator": "equals", "expression": URL})
            self.assertEqual(payload["dataState"], "final")
        TypeAdapter(seo_tools.SeoQueryResult).validate_python(value)

    def test_no_query_rows_is_not_interpreted_as_no_traffic(self):
        with patch.object(seo_tools.gsc_client, "search_analytics",
                          side_effect=[{"rows": [{"keys": [URL], "clicks": 1,
                            "impressions": 12, "ctr": 1 / 12, "position": 11}]}, {}]):
            data = seo_tools.seo_course_queries(URL, "2026-10-01", "2026-10-10")
        self.assertEqual(data["returned_queries"], 0)
        self.assertEqual(data["totals"]["impressions"], 12)

    def test_input_validation_before_gsc(self):
        bad_args = [
            (URL + "?utm=1", "2026-10-01", "2026-10-02", 10),
            (URL, "2026-10-03", "2026-10-02", 10),
            (URL, "2026-10-01", "2026-10-02", 0),
            (URL, "2026-10-01", "2026-10-02", 101),
            (URL, "2026-10-01", "2026-10-02", True),
        ]
        with patch.object(seo_tools.gsc_client, "search_analytics") as google:
            for args in bad_args:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    seo_tools.seo_course_queries(*args)
            google.assert_not_called()

    def test_gsc_malformed_result_fails_closed(self):
        with patch.object(seo_tools.gsc_client, "search_analytics",
                          return_value={"rows": [{"keys": ["other page"], "clicks": 1,
                                 "impressions": 2, "ctr": 0.5, "position": 1}]}):
            with self.assertRaises(RuntimeError):
                seo_tools.seo_course_queries(URL, "2026-10-01", "2026-10-02")

    def test_typed_registration_and_existing_gsc_toggle(self):
        solo = FastMCP("seo-tools-test")
        solo.tool()(seo_tools.seo_inspect_page)
        solo.tool()(seo_tools.seo_course_queries)
        tools = {item.name: item for item in asyncio.run(solo.list_tools())}
        self.assertEqual(set(tools), {"seo_inspect_page", "seo_course_queries"})
        self.assertIn("headings", tools["seo_inspect_page"].output_schema["required"])
        current = {item.name for item in asyncio.run(mcp.list_tools())}
        disabled = set(filter(None, os.environ.get("MCP_DISABLED_TOOLS", "").split(",")))
        self.assertEqual("seo_inspect_page" in current, "seo_inspect_page" not in disabled)
        self.assertEqual("seo_course_queries" in current, os.environ.get("MCP_GSC_ENABLED") == "1"
                         and "seo_course_queries" not in disabled)


if __name__ == "__main__":
    unittest.main()
