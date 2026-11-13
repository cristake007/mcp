"""Offline regression tests for the task-oriented read-only site tool surface."""
import asyncio
import hashlib
import unittest
from unittest.mock import patch

from fastmcp import FastMCP
from pydantic import TypeAdapter

from app import site_client as site
from app import site_tools


URL = "https://tuvkarpat.ro/cursuri/introducere-in-ergonomie/"
SITEMAP = "https://tuvkarpat.ro/cursuri-sitemap.xml"
PAGE = {"url": URL, "format": "acf_json", "id": 24759,
        "title": "Introducere în Ergonomie",
        "acf": {"tematica": "Țară și știință, ergonomie și sănătate."}}


class SiteToolsTests(unittest.TestCase):
    def test_list_sitemaps_and_paginated_urls(self):
        with patch.object(site, "sitemaps", return_value=[SITEMAP]), patch.object(
            site, "urls", return_value=[URL, URL + "second/"],
        ):
            self.assertEqual(site_tools.list_sitemaps()["sitemaps"], [SITEMAP])
            first = site_tools.list_site_urls(sitemap="cursuri-sitemap.xml", limit=1)
            self.assertEqual(first["urls"], [URL])
            self.assertEqual(first["next_offset"], 1)
            last = site_tools.list_site_urls(
                sitemap="cursuri-sitemap.xml", offset=1, limit=1,
            )
            self.assertIsNone(last["next_offset"])
        TypeAdapter(site_tools.SiteURLList).validate_python(first)
        with self.assertRaises(ValueError):
            site_tools.list_site_urls()
        with self.assertRaises(ValueError):
            site_tools.list_site_urls(sitemap="cursuri-sitemap.xml", limit=101)

    def test_full_acf_text_and_hash_continuation(self):
        with patch.object(site, "read_page", return_value=PAGE):
            first = site_tools.get_site_text(URL, fields=["acf.tematica"], max_chars=1000)
            self.assertEqual(first["view"], "text")
            self.assertEqual(first["format"], "acf_json")
            self.assertEqual(first["missing_fields"], [])
            self.assertIn("Țară și știință", first["content"])
            self.assertEqual(first["source_map"][0]["field"], "acf.tematica")
            self.assertEqual(first["sha256"], hashlib.sha256(
                first["content"].encode("utf-8")
            ).hexdigest())
            self.assertIsNone(first["next_part"])
            TypeAdapter(site_tools.SiteText).validate_python(first)
            with self.assertRaises(ValueError):
                site_tools.get_site_text(URL, max_chars=1000, expected_hash="0" * 64)
            with self.assertRaises(ValueError):
                site_tools.get_site_text(URL, max_chars=999)

    def test_site_search_matches_actual_unicode_offsets(self):
        with patch.object(site, "sitemaps", return_value=[SITEMAP]), patch.object(
            site, "urls", return_value=[URL],
        ), patch.object(site, "read_page", return_value=PAGE):
            found = site_tools.search_site(
                "tara", match_mode="accent_insensitive", sitemap="cursuri-sitemap.xml",
            )
        self.assertTrue(found["complete"])
        self.assertEqual(found["scanned"], 1)
        self.assertEqual(len(found["matches"]), 1)
        self.assertEqual(found["query_totals"][0]["occurrences"], 1)
        self.assertEqual(found["match_mode"], "accent_insensitive")
        TypeAdapter(site_tools.SiteSearch).validate_python(found)
        with self.assertRaises(ValueError):
            site_tools.search_site("tara", limit=21)

    def test_fastmcp_schemas_are_explicit_and_read_only(self):
        mcp = FastMCP("site-tools-offline")
        funcs = (site_tools.list_sitemaps, site_tools.list_site_urls,
                 site_tools.get_site_text, site_tools.search_site)
        for func in funcs:
            mcp.tool()(func)
        tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
        self.assertEqual(set(tools), {
            "list_sitemaps", "list_site_urls", "get_site_text", "search_site",
        })
        self.assertNotIn("action", tools["get_site_text"].parameters["properties"])
        self.assertNotIn("audit_ack", str(tools["get_site_text"].parameters))
        self.assertIn("source_map", tools["get_site_text"].output_schema["required"])
        with patch.object(site, "sitemaps", return_value=[SITEMAP]):
            res = asyncio.run(tools["list_sitemaps"].run({}))
        self.assertEqual(res.structured_content["sitemaps"], [SITEMAP])


if __name__ == "__main__":
    unittest.main()
