"""Architecture and output parity checks for dedicated and legacy MCP tools."""
import ast
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from app import site_client as site
from app import site_content_service as content
from app import site_tools
from app import site_inspector as legacy_site
from app import course_search as legacy_course
from app import course_search_service as engine
from app import catalog_tools

ROOT = "https://tuvkarpat.ro/"
SITEMAP = ROOT + "cursuri-sitemap.xml"
URL = ROOT + "cursuri/introducere-in-ergonomie/"
COURSE = {
    "id": 24759, "slug": "introducere-in-ergonomie", "link": URL,
    "title": {"rendered": "Introducere în Ergonomie"},
    "acf": {"tematica": "Țară, știință și ergonomie."},
    "domenii": [123],
}
PAGE = {
    "url": URL, "link": URL, "format": "acf_json", "id": 24759,
    "title": COURSE["title"]["rendered"], "acf": COURSE["acf"],
}


class InterfaceBoundaryTests(unittest.TestCase):
    def test_focused_tools_do_not_import_legacy_dispatchers(self):
        root = Path(__file__).resolve().parents[1] / "app"
        for name in ("site_tools", "site_content_service", "site_client",
                     "catalog_tools", "course_search_service"):
            with self.subTest(module=name):
                tree = ast.parse((root / f"{name}.py").read_text(encoding="utf-8"))
                modules = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        modules.extend(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom):
                        modules.append(node.module or "")
                        modules.extend(alias.name for alias in node.names)
                self.assertFalse(any(
                    value == "site_inspector" or value.endswith(".site_inspector")
                    or value == "course_search" or value.endswith(".course_search")
                    for value in modules
                ), msg=name)

    def test_search_site_output_matches_legacy_search_many(self):
        with (patch.object(site, "sitemaps", return_value=[SITEMAP]),
              patch.object(site, "urls", return_value=[URL]),
              patch.object(site, "read_page", return_value=PAGE),
              patch.object(legacy_site, "_sitemaps", return_value=[SITEMAP]),
              patch.object(legacy_site, "_urls", return_value=[URL]),
              patch.object(legacy_site, "_read_page", return_value=PAGE)):
            direct = site_tools.search_site(
                "tara", sitemap="cursuri-sitemap.xml",
                match_mode="accent_insensitive",
            )
            old = legacy_site.inspect_site(
                "search_many", queries=["tara"],
                sitemap="cursuri-sitemap.xml",
                match_mode="accent_insensitive",
            )
        self.assertEqual(direct["matches"], old["matches"])
        self.assertEqual(direct["query_totals"], old["query_totals"])
        self.assertTrue(direct["complete"])
        self.assertEqual(direct["match_mode"], "accent_insensitive")

    def test_direct_text_parity_with_legacy_get(self):
        with (patch.object(site, "read_page", return_value=PAGE),
              patch.object(legacy_site, "_read_page", return_value=PAGE)):
            new = site_tools.get_site_text(URL, fields=["acf.tematica"])
            old = legacy_site.inspect_site("get", url=URL, view="text",
                                           fields=["acf.tematica"])
        for key in ("content", "sha256", "source_map", "missing_fields",
                    "next_part", "coverage", "content_complete"):
            self.assertEqual(new[key], old[key], msg=key)

    def test_legacy_action_search_and_direct_engine_are_equivalent(self):
        with (patch.object(site, "sitemaps", return_value=[SITEMAP]),
              patch.object(site, "urls", return_value=[URL]),
              patch.object(site, "fetch", return_value=json.dumps([COURSE]))):
            direct = engine.search_catalog(["ergonomie"], force_refresh=True)
            old = legacy_course.search_courses(["ergonomie"], force_refresh=True)
            focused = catalog_tools.find_courses("ergonomie", force_refresh=True)
        self.assertEqual(direct["matching_courses"], 1)
        self.assertNotIn("action", direct)
        self.assertEqual(direct["matches"], old["matches"])
        self.assertEqual(direct["matches"], focused["matches"])
        self.assertEqual(direct["complete"], old["complete"])
        self.assertEqual(direct["complete"], focused["complete"])

    def test_legacy_action_list_and_direct_engine_are_equivalent(self):
        with (patch.object(site, "sitemaps", return_value=[SITEMAP]),
              patch.object(site, "urls", return_value=[URL]),
              patch.object(site, "fetch", return_value=json.dumps([COURSE]))):
            direct = engine.list_catalog_courses()
            old = legacy_course.search_courses(action="list_courses")
            focused = catalog_tools.list_courses(limit=5)
        self.assertNotIn("action", direct)
        self.assertEqual(old["action"], "list_courses")
        self.assertEqual(direct["courses"], old["courses"])
        self.assertEqual(direct["courses"], focused["courses"])
        self.assertTrue(focused["complete"])


if __name__ == "__main__":
    unittest.main()
