"""Offline contract checks for the focused catalog MCP surface."""
import asyncio
import unittest
from unittest.mock import patch

from fastmcp import FastMCP
from pydantic import TypeAdapter

from app import catalog_tools


class CatalogToolsTests(unittest.TestCase):
    def test_domains_explicit_schema(self):
        domains = [{"id": 123, "name": "Ergonomie", "slug": "ergonomie", "count": 2}]
        with patch.object(catalog_tools.catalog, "domains", return_value=domains) as load:
            result = catalog_tools.list_domains(force_refresh=True)
        self.assertEqual(result["total_domains"], 1)
        self.assertEqual(result["domains"], domains)
        load.assert_called_once_with()
        TypeAdapter(catalog_tools.DomainListing).validate_python(result)

    def test_catalog_pagination_and_completeness(self):
        items = [
            {"id": n, "slug": str(n), "url": f"https://tuvkarpat.ro/cursuri/{n}/",
             "title": f"Course {n}", "domenii": [123]}
            for n in range(1, 4)
        ]
        response = {
            "total_courses": 3, "courses": items, "complete": True,
            "missing_courses_count": 0, "errors": [],
            "domain": {"id": 123, "name": "Ergonomie", "slug": "ergonomie", "count": 3},
        }
        with patch.object(catalog_tools.engine, "list_catalog_courses", return_value=response) as search:
            first = catalog_tools.list_courses(domain=123, limit=2)
            second = catalog_tools.list_courses(domain=123, offset=2, limit=2)
        self.assertEqual(first["returned"], 2)
        self.assertEqual(first["next_offset"], 2)
        self.assertEqual(second["returned"], 1)
        self.assertIsNone(second["next_offset"])
        self.assertEqual(second["courses"][0]["id"], 3)
        search.assert_any_call(domain=123, force_refresh=False)
        TypeAdapter(catalog_tools.CourseListing).validate_python(first)
        with self.assertRaises(ValueError):
            catalog_tools.list_courses(limit=101)
        with self.assertRaises(ValueError):
            catalog_tools.list_courses(offset=-1)

    def test_get_course_is_direct_and_find_has_no_actions(self):
        course = {"id": 9, "slug": "fmea", "url": "https://tuvkarpat.ro/cursuri/fmea/",
                  "title": "FMEA", "domenii": [1], "acf": {"tematica": "Test"}}
        with patch.object(catalog_tools.catalog, "get_course", return_value=course) as get:
            result = catalog_tools.get_course("fmea")
        get.assert_called_once_with("fmea")
        TypeAdapter(catalog_tools.CourseDetail).validate_python(result)
        search_result = {
            "total_courses": 3, "scanned_courses": 3, "matching_courses": 1,
            "complete": True, "missing_courses_count": 0, "missing_urls": [],
            "matches": [], "errors": [],
        }
        with patch.object(catalog_tools.engine, "search_catalog", return_value=search_result) as search:
            found = catalog_tools.find_courses("29001", domain="calitate")
        search.assert_called_once_with(
            queries=["29001"], match_mode="contains", domain="calitate", force_refresh=False,
        )
        self.assertEqual(found["matching_courses"], 1)
        TypeAdapter(catalog_tools.CourseFindResult).validate_python(found)

    def test_fastmcp_contracts_are_separate(self):
        mcp = FastMCP("catalog-contracts")
        for func in (
            catalog_tools.list_domains, catalog_tools.list_courses,
            catalog_tools.get_course, catalog_tools.find_courses,
        ):
            mcp.tool()(func)
        tools = {item.name: item for item in asyncio.run(mcp.list_tools())}
        self.assertEqual(set(tools), {"list_domains", "list_courses", "get_course", "find_courses"})
        self.assertNotIn("action", tools["list_courses"].parameters["properties"])
        self.assertNotIn("action", tools["get_course"].parameters["properties"])
        self.assertIn("courses", tools["list_courses"].output_schema["required"])


if __name__ == "__main__":
    unittest.main()
