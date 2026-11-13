"""Offline coverage for search_courses: no requests to the live website."""
import asyncio
import json
import unittest
from unittest.mock import patch

from fastmcp import FastMCP
from pydantic import TypeAdapter

from app import course_search as courses
from app import site_client as site

SITEMAP = courses.COURSE_SITEMAP
ARCHIVE = site.ROOT + "cursuri/"
AUDITOR = ARCHIVE + "auditor-29001/"
MANAGER = ARCHIVE + "manager-29001/"
INTRO = ARCHIVE + "prezentare-29001/"


def item(url, title, acf):
    return {"link": url, "title": {"rendered": title}, "acf": acf}


class CourseSearchTests(unittest.TestCase):
    def _sitemap(self):
        return (
            patch.object(site, "sitemaps", return_value=[
                site.ROOT + "page-sitemap.xml", SITEMAP]),
            patch.object(site, "urls", return_value=[
                ARCHIVE, AUDITOR, MANAGER, INTRO]),
        )

    def _catalog(self):
        return [
            item(AUDITOR, "Auditor ISO/TS 29001:2020",
                 {"tematica": "Conform ISO/TS 29001: 2020"}),
            item(MANAGER, "Manager ISO 29001:2020",
                 {"tematica": "SR EN ISO / TS 29001:2020"}),
            item(INTRO, "Prezentarea cerințelor SR EN ISO/TS 29001:2020",
                 {"tematica": "SR EN ISO / TS 29001:2020"}),
        ]

    def test_three_variant_courses_in_one_bulk_call(self):
        index, listing = self._sitemap()
        with index, listing, patch.object(
            site, "fetch", return_value=json.dumps(self._catalog()),
        ) as fetch:
            result = courses.search_courses(["29001"], force_refresh=True)
        self.assertEqual(result["total_courses"], 3)
        self.assertEqual(result["scanned_courses"], 3)
        self.assertEqual(result["matching_courses"], 3)
        self.assertTrue(result["complete"])
        self.assertEqual([entry["url"] for entry in result["matches"]],
                         [AUDITOR, MANAGER, INTRO])
        self.assertEqual(result["query_totals"][0]["matching_courses"], 3)
        self.assertEqual(fetch.call_count, 1)
        self.assertIn("per_page=100", fetch.call_args.args[0])
        self.assertEqual(result["matches"][1]["terms"][0]["fields"][0]["field"], "title")
        self.assertEqual(result["matches"][1]["terms"][0]["fields"][1]["field"], "acf.tematica")

    def test_literal_terms_or_matched_and_unicode_offsets(self):
        index, listing = self._sitemap()
        with index, listing, patch.object(
            site, "fetch", return_value=json.dumps(self._catalog())
        ):
            narrow = courses.search_courses(["ISO/TS 29001:2020"])
            self.assertEqual(narrow["matching_courses"], 2)
            accent = courses.search_courses(
                ["cerintelor"], match_mode="accent_insensitive")
            self.assertEqual(accent["matching_courses"], 1)
            words = courses.search_courses(["29001"], match_mode="whole_word")
            self.assertEqual(words["matching_courses"], 3)
            several = courses.search_courses(["Auditor", "Manager"])
            self.assertEqual(several["matching_courses"], 2)
            self.assertEqual([t["matching_courses"] for t in several["query_totals"]],
                             [1, 1])

    def test_recover_single_missing_course_and_report_failed_sources(self):
        index, listing = self._sitemap()
        partial = self._catalog()[:2]
        data = {"format": "acf_json", "title": self._catalog()[2]["title"]["rendered"],
                "link": INTRO, "acf": self._catalog()[2]["acf"]}
        with index, listing, patch.object(
            site, "fetch", return_value=json.dumps(partial)
        ), patch.object(site, "load_page", return_value=(data, {})) as recovered:
            result = courses.search_courses(["29001"])
            self.assertTrue(result["complete"])
            self.assertEqual(result["matching_courses"], 3)
            recovered.assert_called_once_with(INTRO, False, exact_url=True)

        with index, listing, patch.object(
            site, "fetch", side_effect=RuntimeError("Bulk REST failed")
        ), patch.object(site, "load_page", side_effect=RuntimeError("Course failed")):
            result = courses.search_courses(["29001"])
            self.assertFalse(result["complete"])
            self.assertEqual(result["scanned_courses"], 0)
            self.assertEqual(result["matching_courses"], 0)
            self.assertEqual(result["missing_courses_count"], 3)
            self.assertTrue(result["errors"])

    def test_missing_acf_never_claims_complete(self):
        index, listing = self._sitemap()
        catalog = self._catalog()
        catalog[1]["acf"] = None
        with index, listing, patch.object(
            site, "fetch", return_value=json.dumps(catalog)
        ), patch.object(site, "load_page", side_effect=ValueError("ACF unavailable")):
            result = courses.search_courses(["29001"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["scanned_courses"], 2)
        self.assertEqual(result["matching_courses"], 2)
        self.assertTrue(any(e["url"] == MANAGER for e in result["errors"]))

    def test_reject_invalid_inputs(self):
        for queries in ([], [" "], ["same", "SAME"], ["x" * 121]):
            with self.subTest(queries=queries), self.assertRaises(ValueError):
                courses.search_courses(queries)
        with self.assertRaises(ValueError):
            courses.search_courses(["test"], match_mode="unknown")
        with self.assertRaises(ValueError):
            courses.search_courses(["test"], force_refresh="true")

    def test_typed_fastmcp_contract(self):
        mcp = FastMCP("course-search-offline")
        mcp.tool()(courses.search_courses)
        tools = asyncio.run(mcp.list_tools())
        self.assertEqual([tool.name for tool in tools], ["search_courses"])
        self.assertIn("complete", tools[0].output_schema["required"])
        index, listing = self._sitemap()
        with index, listing, patch.object(
            site, "fetch", return_value=json.dumps(self._catalog())
        ):
            result = asyncio.run(tools[0].run({"queries": ["29001"]}))
        self.assertEqual(result.structured_content["matching_courses"], 3)
        TypeAdapter(courses.CourseSearchResult).validate_python(result.structured_content)


    def test_wordpress_domain_navigation_and_scoped_search(self):
        index, listing = self._sitemap()
        items = self._catalog()
        for n, entry in enumerate(items):
            entry.update({"id": 100 + n, "slug": entry["link"].rstrip("/").split("/")[-1],
                          "domenii": [123] if n < 2 else [456]})
        terms = [{"id": 123, "slug": "ergonomie", "name": "Ergonomie", "count": 2},
                 {"id": 456, "slug": "altul", "name": "Altul", "count": 1}]

        def fetch(url, accept=""):
            if "/domenii?" in url:
                return json.dumps(terms)
            if "domenii=123" in url:
                return json.dumps(items[:2])
            if "slug=auditor-29001" in url:
                return json.dumps(items[:1])
            if "/cursuri?" in url:
                return json.dumps(items)
            raise AssertionError("Unexpected REST request: " + url)

        with index, listing, patch.object(site, "fetch", side_effect=fetch):
            domains = courses.search_courses(action="list_domains")
            self.assertTrue(domains["complete"])
            self.assertEqual(domains["total_domains"], 2)
            self.assertEqual(domains["domains"][0]["count"], 2)

            listing_result = courses.search_courses(
                action="list_courses", domain="ergonomie")
            self.assertTrue(listing_result["complete"])
            self.assertEqual(listing_result["total_courses"], 2)
            self.assertEqual([c["id"] for c in listing_result["courses"]], [100, 101])
            self.assertEqual(listing_result["domain"]["id"], 123)

            scoped = courses.search_courses(
                ["29001"], domain="ergonomie", force_refresh=True)
            self.assertTrue(scoped["complete"])
            self.assertEqual(scoped["matching_courses"], 2)
            self.assertEqual(scoped["scanned_courses"], 2)
            unrestricted = courses.search_courses(["29001"])
            self.assertEqual(unrestricted["matching_courses"], 3)

            detail = courses.search_courses(
                action="get_course", course="auditor-29001")
            self.assertEqual(detail["course"]["id"], 100)
            self.assertEqual(detail["course"]["domenii"], [123])
            self.assertIn("tematica", detail["course"]["acf"])

    def test_domain_count_mismatch_is_not_silently_accepted(self):
        from app import course_catalog as catalog
        index, listing = self._sitemap()
        items = self._catalog()[:2]
        for n, entry in enumerate(items):
            entry.update({"id": 100 + n,
                          "slug": entry["link"].rstrip("/").split("/")[-1],
                          "domenii": [123]})
        terms = [{"id": 123, "slug": "ergonomie",
                  "name": "Ergonomie", "count": 3}]

        def fetch(url, accept=""):
            if "/domenii?" in url:
                return json.dumps(terms)
            if "domenii=123" in url:
                return json.dumps(items)
            raise AssertionError(url)

        with index, listing, patch.object(site, "fetch", side_effect=fetch):
            with self.assertRaisesRegex(RuntimeError, "declară 3"):
                catalog.domain_items("ergonomie")


if __name__ == "__main__":
    unittest.main()
