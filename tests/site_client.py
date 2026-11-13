"""Regressions for independent catalog and shared public-site client."""
import ast
import inspect
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app import site_client as client
from app import site_transport as transport
from app import course_catalog as catalog
from app import course_search as courses
from app import catalog_tools


class IndependentSiteClientTests(unittest.TestCase):
    def test_catalog_modules_do_not_import_legacy_inspector(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("site_client", "course_catalog", "course_search", "catalog_tools"):
            source = (root / "app" / f"{name}.py").read_text(encoding="utf-8")
            imports = []
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Import):
                    imports.extend(x.name for x in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.append(node.module or "")
                    imports.extend(x.name for x in node.names)
            self.assertNotIn("site_inspector", imports, msg=name)

    def test_client_context_is_restored_after_error(self):
        outer = transport._REFRESH.set(False)
        exact = transport._EXACT_URL.set(False)
        try:
            seen = []
            def read(url):
                seen.append((transport._REFRESH.get(), transport._EXACT_URL.get()))
                raise RuntimeError("Network failure")
            with self.assertRaisesRegex(RuntimeError, "Network failure"):
                client.load_page("https://tuvkarpat.ro/cursuri/example/",
                                 True, True, reader=read)
            self.assertEqual(seen, [(True, True)])
            self.assertFalse(transport._REFRESH.get())
            self.assertFalse(transport._EXACT_URL.get())
            self.assertIsNone(transport._EVIDENCE.get())
        finally:
            transport._EXACT_URL.reset(exact)
            transport._REFRESH.reset(outer)

    def test_refresh_scope_reaches_catalog_transport_and_resets(self):
        def read_catalog():
            return [{"id": 123, "name": "Ergonomie", "slug": "ergonomie", "count": 2}]
        import json
        evidence = []
        def fetch(url, accept="text/html,*/*"):
            evidence.append(transport._REFRESH.get())
            self.assertIn("/wp-json/wp/v2/domenii?", url)
            return json.dumps(read_catalog())
        with patch.object(client, "fetch", side_effect=fetch):
            result = catalog_tools.list_domains(force_refresh=True)
        self.assertEqual(result["total_domains"], 1)
        self.assertEqual(evidence, [True])
        self.assertFalse(transport._REFRESH.get())

    def test_shared_client_keeps_canonical_url_identity(self):
        url = "https://tuvkarpat.ro/cursuri/introducere-in-ergonomie/"
        invalid = {"id": 8, "link": "https://tuvkarpat.ro/cursuri/alt-curs/",
                   "title": {"rendered": "Course"}, "acf": {"tematica": "Text"}}
        import json
        with patch.object(client, "fetch", return_value=json.dumps([invalid])):
            with self.assertRaisesRegex(ValueError, "exact URL"):
                client.load_page(url, force_refresh=True, exact_url=True)

    def test_course_catalog_retains_sitemap_completeness(self):
        url = "https://tuvkarpat.ro/cursuri/intro/"
        with patch.object(client, "sitemaps", return_value=[catalog.COURSE_SITEMAP]), patch.object(
            client, "urls", return_value=[url],
        ):
            self.assertEqual(catalog.course_urls(), [url])
        with patch.object(client, "sitemaps", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "Sitemap"):
                catalog.course_urls()


if __name__ == "__main__":
    unittest.main()
