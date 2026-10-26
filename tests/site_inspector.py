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
    def test_mcp_output_schema_all_actions(self):
        """One declared output contract must accept every action unchanged."""
        import asyncio
        import hashlib
        from fastmcp import FastMCP
        from pydantic import TypeAdapter
        from app.site_output import SiteInspectionResult
        from app.site_fields import extract_with_sources

        mcp = FastMCP("site-schema-offline")
        mcp.tool()(site.inspect_site)
        tools = asyncio.run(mcp.list_tools())
        self.assertEqual([tool.name for tool in tools], ["inspect_site"])
        tool = tools[0]
        schema = tool.output_schema
        self.assertEqual(schema["type"], "object")
        self.assertIn("action", schema["required"])
        self.assertEqual(len(schema["properties"]["action"]["enum"]), 10)
        for field in ("pages", "coverage_report", "source_map", "pending_parts",
                      "query_totals", "entries", "matches", "next_offset", "acf"):
            self.assertIn(field, schema["properties"])
        self.assertIn("source_map", schema["properties"]["pages"]["items"]["properties"])
        self.assertIn("content_start", schema["properties"]["source_map"]["items"]["properties"])
        self.assertIn("requires_followup_urls", schema["properties"]["coverage_report"]["properties"])
        self.assertIn("positions", schema["properties"]["matches"]["items"]["properties"]["terms"]["items"]["properties"])

        site_map = "https://tuvkarpat.ro/cursuri-sitemap.xml"
        page_url = "https://tuvkarpat.ro/cursuri/test/"
        page = {
            "url": page_url, "format": "acf_json", "id": 42,
            "title": "Curs test", "link": page_url,
            "acf": {"tematica": "Test pentru audit"},
        }
        raw, warnings, _, missing = extract_with_sources(page, None)
        self.assertEqual((warnings, missing), ([], []))
        checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        report = {
            "auditable_urls": 1, "acknowledged_urls": 0,
            "not_acknowledged_urls": 1, "offset": 0,
            "entries": [{"url": page_url, "status": "not_acknowledged",
                         "reviewed_at": None}],
            "next_offset": None, "status_scope": "persisted_baseline_only",
            "current_site_freshness_checked": False,
            "extraction_completed_in_this_report": False,
            "note": "Baseline only",
        }
        cases = [
            ("sitemaps", {}),
            ("urls", {"sitemap": "cursuri-sitemap.xml"}),
            ("get", {"url": page_url, "view": "text",
                     "fields": ["title", "acf.tematica"]}),
            ("get", {"url": page_url, "view": "raw"}),
            ("batch_get", {"sitemap": "cursuri-sitemap.xml", "limit": 1,
                           "fields": ["title", "acf.tematica"]}),
            ("audit_changes", {"sitemap": "cursuri-sitemap.xml", "limit": 1}),
            ("search", {"query": "Test", "sitemap": "cursuri-sitemap.xml"}),
            ("search_many", {"queries": ["Test"], "sitemap": "cursuri-sitemap.xml"}),
            ("verify_quote", {"url": page_url, "field": "acf.tematica",
                              "quote": "Test"}),
            ("audit_report", {"sitemap": "cursuri-sitemap.xml"}),
            ("audit_ack", {"url": page_url, "expected_hash": checksum}),
        ]
        adapter = TypeAdapter(SiteInspectionResult)
        with (patch.object(site, "_sitemaps", return_value=[site_map]),
              patch.object(site, "_urls", return_value=[page_url]),
              patch.object(site, "_read_page", return_value=page),
              patch("app.site_audit.get_reviews", return_value={}),
              patch("app.site_audit.review_coverage", return_value=report),
              patch("app.site_audit.save_reviews")):
            for action, params in cases:
                with self.subTest(action=action, view=params.get("view")):
                    output = site.inspect_site(action=action, **params)
                    self.assertEqual(adapter.validate_python(output), output)
                    result = asyncio.run(tool.run({"action": action, **params}))
                    self.assertEqual(result.structured_content, output)

    def test_validation_errors_are_typed_and_self_correcting(self):
        """Invalid parameters return a retry hint, not a tool traceback."""
        import asyncio
        from fastmcp import FastMCP
        from pydantic import TypeAdapter
        from app.site_output import SiteInspectionResult

        adapter = TypeAdapter(SiteInspectionResult)
        samples = [
            ("batch_get", {"sitemap": "cursuri-sitemap.xml", "limit": 196},
             "limit", 10),
            ("search", {"query": "ISO", "limit": 500},
             "limit", 20),
            ("urls", {"sitemap": "cursuri-sitemap.xml", "limit": 101},
             "limit", 100),
            ("urls", {"sitemap": "cursuri-sitemap.xml", "limit": 0},
             "limit", 1),
            ("urls", {"sitemap": "cursuri-sitemap.xml", "limit": True},
             "limit", 20),
            ("urls", {"sitemap": "cursuri-sitemap.xml", "limit": "200"},
             "limit", 20),
            ("urls", {"sitemap": "cursuri-sitemap.xml", "offset": -2},
             "offset", 0),
            ("get", {"url": "https://tuvkarpat.ro/a/", "max_chars": 100},
             "max_chars", 1000),
            ("get", {"url": "https://tuvkarpat.ro/a/", "part": -2},
             "part", 0),
            ("get", {"url": "https://tuvkarpat.ro/a/", "view": "invalid"},
             "view", "raw"),
            ("get", {"url": "https://tuvkarpat.ro/a/", "expected_hash": "abc"},
             "expected_hash", ""),
        ]
        mcp = FastMCP("site-validation-offline")
        mcp.tool()(site.inspect_site)
        tool = asyncio.run(mcp.list_tools())[0]
        self.assertIn("error", tool.output_schema["properties"])
        self.assertIn("retry_with",
                      tool.output_schema["properties"]["error"]["properties"])
        with (patch.object(site, "_sitemaps", side_effect=AssertionError("network called")),
              patch.object(site, "_read_page", side_effect=AssertionError("network called"))):
            for action, params, key, suggestion in samples:
                with self.subTest(action=action, parameter=key, supplied=params[key]):
                    result = site.inspect_site(action, **params)
                    self.assertEqual(adapter.validate_python(result), result)
                    self.assertFalse(result["ok"])
                    error = result["error"]
                    self.assertEqual(error["code"], "invalid_parameter")
                    self.assertEqual(error["parameter"], key)
                    self.assertIn(key, error["message"])
                    self.assertIn("Reapelează", error["retry_instruction"])
                    self.assertEqual(error["retry_with"], {key: suggestion})
                    execution = asyncio.run(tool.run({"action": action, **params}))
                    self.assertEqual(execution.structured_content, result)

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
        urls = [f"https://tuvkarpat.ro/cursuri/{i}/" for i in range(13)]
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
            self.assertEqual(result["processed"], 10)
            self.assertEqual(result["next_offset"], 10)
            self.assertFalse(result["batch_complete"])
            self.assertEqual(len(result["errors"]), 1)
            self.assertTrue(result["errors"][0]["url"].endswith("/2/"))
            self.assertEqual(reader.call_count, 10)
            last = site.inspect_site("batch_get", sitemap="cursuri-sitemap.xml", offset=10)
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


    def test_fields_selector_is_optional_and_structured(self):
        from app.site_fields import normalize_fields, extract_with_sources
        self.assertIsNone(normalize_fields(None))
        self.assertEqual(normalize_fields(["title", "tematica", "acf.grup_tinta"]),
                         ("title", "acf.tematica", "acf.grup_tinta"))
        for invalid in ([], ["acf."], ["title;drop table"], ["acf.tematica.*"]):
            with self.assertRaises(ValueError):
                normalize_fields(invalid)
        page = {
            "format": "acf_json", "title": "Buna practică",
            "acf": {"tematica": ["Pregateste elevii", "<p>Învață sudură</p>"],
                    "grup_tinta": "Specialiști", "investitie": "100 lei",
                    "custom": {"nested": "Text necunoscut"}},
        }
        text, warnings, spans, missing = extract_with_sources(
            page, normalize_fields(["title", "acf.tematica", "grup_tinta"])
        )
        self.assertFalse(warnings)
        self.assertFalse(missing)
        self.assertIn("Pregateste elevii", text)
        self.assertIn("Învață sudură", text)
        self.assertNotIn("investitie", text)
        self.assertNotIn("Text necunoscut", text)
        self.assertEqual(
            [s["field"] for s in spans],
            ["title", "acf.tematica[0]", "acf.tematica[1]", "acf.grup_tinta"]
        )
        for span in spans:
            self.assertEqual(len(text[span["content_start"]:span["content_end"]]),
                             span["end"] - span["start"])
        _, _, _, missing = extract_with_sources(page, normalize_fields(["acf.inexistent"]))
        self.assertEqual(missing, ["acf.inexistent"])

    def test_batch_selected_fields_chunk_positions_and_exact_continuation(self):
        from app.site_fields import normalize_fields, extract_with_sources
        url = "https://tuvkarpat.ro/cursuri/test/"
        page = {"format": "acf_json", "url": url, "id": 12, "title": "Curs",
                "acf": {"tematica_descriere": "<p>" + "Învățare profesională. " * 350 + "</p>",
                        "investitie": "200 lei", "grup_tinta": "Auditori"}}
        fields = ["title", "acf.tematica_descriere"]
        with (
            patch.object(site, "_sitemaps", return_value=["https://tuvkarpat.ro/cursuri-sitemap.xml"]),
            patch.object(site, "_urls", return_value=[url]),
            patch.object(site, "_read_page", return_value=page),
        ):
            batch = site.inspect_site(
                "batch_get", sitemap="cursuri-sitemap.xml", limit=5,
                max_chars=1000, fields=fields
            )
            item = batch["pages"][0]
            self.assertEqual(batch["coverage"], "selected")
            self.assertNotIn("200 lei", item["content"])
            self.assertEqual(item["retrieve_remaining"]["fields"],
                             ["title", "acf.tematica_descriere"])
            parts = [item]
            nxt = item["next_part"]
            while nxt is not None:
                page_part = site.inspect_site(
                    "get", url=url, view="text", part=nxt, max_chars=1000,
                    expected_hash=item["sha256"], fields=fields
                )
                parts.append(page_part)
                nxt = page_part["next_part"]
            expected_text, _, expected_spans, _ = extract_with_sources(
                page, normalize_fields(fields)
            )
            self.assertEqual("".join(p["content"] for p in parts), expected_text)
            full_by_field = {
                s["field"]: expected_text[s["content_start"]:s["content_end"]]
                for s in expected_spans
            }
            for part in parts:
                for span in part["source_map"]:
                    observed = part["content"][span["content_start"]:span["content_end"]]
                    self.assertEqual(
                        observed,
                        full_by_field[span["field"]][span["start"]:span["end"]]
                    )
            with self.assertRaisesRegex(ValueError, "schimbat"):
                site.inspect_site(
                    "get", url=url, view="text", part=1, max_chars=1000,
                    expected_hash=item["sha256"], fields=["acf.investitie"]
                )

    def test_full_acf_coverage_by_default_and_reject_fields_for_raw(self):
        url = "https://tuvkarpat.ro/cursuri/test/"
        page = {"url": url, "format": "acf_json",
                "title": "Test", "acf": {"investitie": "400 lei", "tematica": "Pregătire"}}
        with patch.object(site, "_read_page", return_value=page):
            got = site.inspect_site("get", url=url, view="text")
            self.assertEqual(got["coverage"], "all")
            self.assertIn("400 lei", got["content"])
            self.assertIn("Pregătire", got["content"])
            self.assertEqual([x["field"] for x in got["source_map"]],
                             ["title", "acf.investitie", "acf.tematica"])
            with self.assertRaisesRegex(ValueError, "view"):
                site.inspect_site("get", url=url, fields=["title"])


    def test_course_archive_does_not_consume_a_batch_slot(self):
        sitemap = "https://tuvkarpat.ro/cursuri-sitemap.xml"
        index_url = "https://tuvkarpat.ro/cursuri/"
        courses = [f"https://tuvkarpat.ro/cursuri/curs-{i:03d}/" for i in range(195)]
        urls = [index_url] + courses
        def read_course(url):
            self.assertNotEqual(url, index_url)
            return {"url": url, "format": "acf_json", "title": url, "id": 12,
                    "acf": {"tematica": "Audit de diacritice"}}
        with (
            patch.object(site, "_sitemaps", return_value=[sitemap]),
            patch.object(site, "_urls", return_value=urls),
            patch.object(site, "_read_page", side_effect=read_course) as reader,
        ):
            first = site.inspect_site("batch_get", sitemap="cursuri-sitemap.xml",
                                      limit=5, fields=["title", "acf.tematica"])
            self.assertEqual(first["total_urls"], 196)
            self.assertEqual(first["auditable_urls"], 195)
            self.assertEqual(first["skipped_index_urls"], [index_url])
            self.assertEqual(first["processed"], 5)
            self.assertEqual(first["next_offset"], 5)
            self.assertEqual([p["url"] for p in first["pages"]], courses[:5])
            self.assertEqual(reader.call_count, 5)
            second = site.inspect_site("batch_get", sitemap="cursuri-sitemap.xml",
                                       offset=5, limit=5)
            self.assertEqual([p["url"] for p in second["pages"]], courses[5:10])
            last = site.inspect_site("batch_get", sitemap="cursuri-sitemap.xml",
                                     offset=190, limit=5)
            self.assertEqual([p["url"] for p in last["pages"]], courses[-5:])
            self.assertEqual(last["processed"], 5)
            self.assertIsNone(last["next_offset"])
            self.assertTrue(last["end_of_sitemap"])
            self.assertEqual(reader.call_count, 15)
            # The original sitemap listing remains unchanged for normal urls.
            listed = site.inspect_site("urls", sitemap="cursuri-sitemap.xml", limit=2)
            self.assertEqual(listed["total"], 196)
            self.assertEqual(listed["urls"], [index_url, courses[0]])

    def test_only_matching_archive_root_is_excluded(self):
        sitemap = "https://tuvkarpat.ro/cursuri-sitemap.xml"
        urls = ["https://tuvkarpat.ro/cursuri",
                "https://tuvkarpat.ro/cursuri/cursuri/",
                "https://tuvkarpat.ro/industriale/",
                "https://tuvkarpat.ro/cursuri/test/"]
        auditable, skipped = site._index_pages_in_sitemap(sitemap, urls)
        self.assertEqual(skipped, [urls[0]])
        self.assertEqual(auditable, urls[1:])
        auditable, skipped = site._index_pages_in_sitemap(
            "https://tuvkarpat.ro/industriale-sitemap.xml", urls)
        self.assertEqual(skipped, [urls[2]])
        self.assertEqual(auditable, [urls[0], urls[1], urls[3]])



    def test_verify_quote_is_exact_and_reports_source_field(self):
        url = "https://tuvkarpat.ro/cursuri/test/"
        page = {"url": url, "format": "acf_json", "title": "Verificare",
                "acf": {"tematica": ["Acord corect", "în conformitate cu cerințelor"],
                        "grup_tinta": "Alți angajați"}}
        with patch.object(site, "_read_page", return_value=page):
            result = site.inspect_site(
                "verify_quote", url=url, field="acf.tematica",
                quote="în conformitate cu cerințelor")
            self.assertTrue(result["verified"])
            self.assertEqual(result["occurrences"], 1)
            self.assertEqual(result["matches"][0]["field"], "acf.tematica[1]")
            self.assertEqual(result["matches"][0]["start"], 0)
            self.assertIn("cerințelor", result["matches"][0]["context"])
            not_found = site.inspect_site(
                "verify_quote", url=url, field="acf.tematica",
                quote="În conformitate cu cerințelor")
            self.assertFalse(not_found["verified"])
            absent = site.inspect_site(
                "verify_quote", url=url, field="acf.altceva", quote="test")
            self.assertEqual(absent["missing_fields"], ["acf.altceva"])
        with self.assertRaises(ValueError):
            site.inspect_site("verify_quote", url=url, field="acf.tematica", quote="")

    def test_verify_quote_on_cleaned_html(self):
        url = "https://tuvkarpat.ro/industriale/test/"
        page = {"url": url, "format": "page_html",
                "html": "<main><p>Pregatirea auditorilor</p></main>"}
        with patch.object(site, "_read_page", return_value=page):
            got = site.inspect_site(
                "verify_quote", url=url, field="html.body",
                quote="Pregatirea auditorilor")
        self.assertTrue(got["verified"])
        self.assertEqual(got["matches"][0]["field"], "html.body")

    def test_incremental_only_sends_new_or_modified_content(self):
        import hashlib
        from app.site_fields import extract_with_sources, normalize_fields
        from unittest.mock import Mock
        sitemap = "https://tuvkarpat.ro/cursuri-sitemap.xml"
        urls = ["https://tuvkarpat.ro/cursuri/prima/",
                "https://tuvkarpat.ro/cursuri/alta/"]
        pages = {url: {"url": url, "format": "acf_json", "title": "Curs",
                       "acf": {"tematica": "Text auditat"}} for url in urls}
        selectors = normalize_fields(["title", "acf.tematica"])
        content, _, _, _ = extract_with_sources(pages[urls[0]], selectors)
        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with (patch.object(site, "_sitemaps", return_value=[sitemap]),
              patch.object(site, "_urls", return_value=urls),
              patch.object(site, "_read_page", side_effect=lambda u: pages[u]),
              patch("app.site_audit.get_reviews",
                    return_value={urls[0]: {"sha256": sha, "reviewed_at": "2026-10-24T10:00:00+00:00"}}) as get_review,
              patch("app.site_audit.save_reviews") as save_review):
            got = site.inspect_site(
                "audit_changes", sitemap="cursuri-sitemap.xml",
                fields=["title", "acf.tematica"])
            self.assertEqual(got["processed"], 2)
            self.assertEqual([x["url"] for x in got["unchanged"]], [urls[0]])
            self.assertEqual([x["url"] for x in got["pages"]], [urls[1]])
            self.assertEqual(got["pages"][0]["change_status"], "new")
            self.assertTrue(got["batch_complete"])
            get_review.assert_called_once_with(urls, selectors)
            save_review.assert_not_called()

    def test_audit_ack_requires_current_exact_hash_and_is_explicit(self):
        import hashlib
        from app.site_fields import extract_with_sources, normalize_fields
        url = "https://tuvkarpat.ro/cursuri/exemplu/"
        page = {"url": url, "format": "acf_json", "title": "Curs",
                "acf": {"tematica": "Munca și muncă"}}
        selectors = normalize_fields(["title", "acf.tematica"])
        content, _, _, _ = extract_with_sources(page, selectors)
        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with (patch.object(site, "_read_page", return_value=page),
              patch("app.site_audit.save_reviews") as save_review):
            result = site.inspect_site(
                "audit_ack", fields=["title", "acf.tematica"],
                reviews=[{"url": url, "sha256": sha}])
            self.assertEqual(result["reviewed"], 1)
            save_review.assert_called_once_with([(url, sha)], selectors)
            save_review.reset_mock()
            with self.assertRaisesRegex(ValueError, "schimbat"):
                site.inspect_site(
                    "audit_ack", url=url, expected_hash="0"*64,
                    fields=["title", "acf.tematica"])
            save_review.assert_not_called()
            with self.assertRaisesRegex(ValueError, "duplicat"):
                site.inspect_site(
                    "audit_ack", fields=["title", "acf.tematica"],
                    reviews=[{"url": url, "sha256": sha}, {"url": url, "sha256": sha}])
            save_review.assert_not_called()

    def test_audit_ack_rejects_missing_fields_and_invalid_batch(self):
        url = "https://tuvkarpat.ro/cursuri/exemplu/"
        page = {"url": url, "format": "acf_json", "title": "Curs",
                "acf": {"tematica": "Text"}}
        with (patch.object(site, "_read_page", return_value=page),
              patch("app.site_audit.save_reviews") as save_review):
            with self.assertRaisesRegex(ValueError, "incomplet"):
                site.inspect_site(
                    "audit_ack", url=url, expected_hash="0"*64,
                    fields=["acf.grup_tinta"])
            save_review.assert_not_called()
            with self.assertRaises(ValueError):
                site.inspect_site("audit_ack", reviews=[{"url": url}])
            save_review.assert_not_called()



    def test_optimized_batch_covers_195_courses_in_20_calls(self):
        sitemap = "https://tuvkarpat.ro/cursuri-sitemap.xml"
        archive = "https://tuvkarpat.ro/cursuri/"
        urls = [f"https://tuvkarpat.ro/cursuri/curs-{n}/" for n in range(195)]
        seen = []
        def read(url):
            seen.append(url)
            return {"url": url, "format": "acf_json", "title": "Curs",
                    "acf": {"tematica": "Pregătire profesională"}}
        with (patch.object(site, "_sitemaps", return_value=[sitemap]),
              patch.object(site, "_urls", return_value=[archive] + urls),
              patch.object(site, "_read_page", side_effect=read)):
            offset, rounds, fetched = 0, 0, []
            while True:
                batch = site.inspect_site(
                    "batch_get", sitemap="cursuri-sitemap.xml", offset=offset,
                    fields=["title", "acf.tematica"])
                rounds += 1
                self.assertLessEqual(batch["processed"], 10)
                self.assertTrue(batch["batch_complete"])
                self.assertEqual(batch["auditable_urls"], 195)
                fetched.extend(x["url"] for x in batch["pages"])
                if batch["next_offset"] is None:
                    break
                offset = batch["next_offset"]
            self.assertEqual(rounds, 20)
            self.assertEqual(fetched, urls)
            self.assertEqual(set(seen), set(urls))
            self.assertEqual(len(seen), 195)

    def test_search_many_fetches_once_and_keeps_multi_term_counts(self):
        entries = [("https://tuvkarpat.ro/cursuri/test/",
                    "https://tuvkarpat.ro/cursuri-sitemap.xml")]
        page = {"format": "acf_json", "title": "ISO 9001",
                "acf": {"tematica": "ISO 9001 și ISO 90010. munca și muncă. "
                                     "țară, tara, ŢARĂ și ȚARĂ."}}
        with (patch.object(site, "_search_entries", return_value=entries),
              patch.object(site, "_read_page", return_value=page) as reader):
            exact = site.inspect_site(
                "search_many", queries=["ISO 9001", "munca"], match_mode="exact",
                sitemap="cursuri-sitemap.xml")
            self.assertEqual(reader.call_count, 1)
            self.assertTrue(exact["complete"])
            self.assertEqual([x["occurrences"] for x in exact["matches"][0]["terms"]],
                             [3, 1])
            self.assertEqual(exact["query_totals"][0]["matching_urls"], 1)
            self.assertEqual(exact["query_totals"][0]["occurrences"], 3)
            self.assertEqual(exact["totals_scope"], "current_batch")

            whole_word = site.inspect_site(
                "search_many", queries=["ISO 9001", "munca"],
                match_mode="whole_word")
            self.assertEqual([x["occurrences"] for x in whole_word["matches"][0]["terms"]],
                             [2, 1])
            accent = site.inspect_site(
                "search_many", queries=["munca", "tara"],
                match_mode="accent_insensitive")
            term_counts = {t["query"]: t["occurrences"] for t in accent["matches"][0]["terms"]}
            self.assertEqual(term_counts, {"munca": 2, "tara": 4})
            text = site._as_search_text(page)
            for term in accent["matches"][0]["terms"]:
                for span in term["positions"]:
                    match = text[span["start"]:span["end"]]
                    folded, _ = site._accent_fold_with_positions(match)
                    self.assertEqual(folded, term["query"])

    def test_search_many_works_with_special_punctuation_and_accents(self):
        entries = [("https://tuvkarpat.ro/pagina/", "https://tuvkarpat.ro/page-sitemap.xml")]
        page = {"url": entries[0][0], "format": "page_html",
                "html": "<main>(ISO 9606) și t\u0326ara, țara, ştiinţă, știință.</main>"}
        with (patch.object(site, "_search_entries", return_value=entries),
              patch.object(site, "_read_page", return_value=page)):
            exact = site.inspect_site("search_many", queries=["(ISO 9606)"])
            self.assertEqual(exact["matches"][0]["terms"][0]["occurrences"], 1)
            folded = site.inspect_site("search_many", queries=["tara", "stiinta"],
                                       match_mode="accent_insensitive")
            self.assertEqual([p["occurrences"] for p in folded["matches"][0]["terms"]],
                             [2, 2])
            content = site._as_search_text(page)
            for term in folded["matches"][0]["terms"]:
                for span in term["positions"]:
                    self.assertIn(term["query"], site._accent_fold_with_positions(
                        content[span["start"]:span["end"]])[0])

    def test_search_many_error_pagination_and_strict_validation(self):
        entries = [(f"https://tuvkarpat.ro/test/{n}/", "https://tuvkarpat.ro/page-sitemap.xml")
                   for n in range(3)]
        def read(url):
            if url == entries[1][0]:
                raise RuntimeError("HTTP 429")
            return {"url": url, "format": "page_html", "html": "<p>Test ISO 9606</p>"}
        with (patch.object(site, "_search_entries", return_value=entries),
              patch.object(site, "_read_page", side_effect=read) as reader):
            first = site.inspect_site("search_many", queries=["ISO 9606"],
                                      limit=2)
            self.assertFalse(first["complete"])
            self.assertEqual(first["next_offset"], 2)
            self.assertEqual(first["scanned"], 2)
            self.assertEqual(len(first["errors"]), 1)
            second = site.inspect_site("search_many", queries=["ISO 9606"],
                                       offset=2, limit=2)
            self.assertTrue(second["complete"])
            self.assertIsNone(second["next_offset"])
            self.assertEqual(reader.call_count, 3)
        for kwargs in (
            {"queries": []}, {"queries": "ISO"}, {"queries": ["   "]},
            {"queries": ["a"] * 11}, {"queries": ["ISO", "iso"]},
            {"queries": ["ABC"], "match_mode": "regex"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                site.inspect_site("search_many", **kwargs)



    def test_batch_coverage_tracks_missing_fields_and_unfinished_parts(self):
        sitemap = "https://tuvkarpat.ro/cursuri-sitemap.xml"
        urls = ["https://tuvkarpat.ro/cursuri/ok/",
                "https://tuvkarpat.ro/cursuri/long/",
                "https://tuvkarpat.ro/cursuri/missing/",
                "https://tuvkarpat.ro/cursuri/error/"]
        def read(url):
            if url == urls[3]:
                raise RuntimeError("HTTP 503")
            if url == urls[1]:
                payload = "text " * 450
            else:
                payload = "Pregătirea auditori"
            acf = {"tematica": payload}
            if url == urls[2]:
                acf = {"alt": payload}
            return {"url": url, "title": "Curs", "format": "acf_json", "acf": acf}
        with (patch.object(site, "_sitemaps", return_value=[sitemap]),
              patch.object(site, "_urls", return_value=urls),
              patch.object(site, "_read_page", side_effect=read)):
            batch = site.inspect_site(
                "batch_get", sitemap="cursuri-sitemap.xml",
                fields=["title", "acf.tematica"], max_chars=1000, limit=10)
            report = batch["coverage_report"]
            self.assertEqual(report["requested_urls"], 4)
            self.assertEqual(report["fetched_urls"], 3)
            self.assertEqual(report["content_returned_urls"], 3)
            self.assertEqual(report["fully_returned_urls"], 2)
            self.assertEqual(report["pending_continuation_urls"], [urls[1]])
            self.assertEqual(report["missing_fields"],
                             [{"url": urls[2], "fields": ["acf.tematica"]}])
            self.assertEqual(report["failed_urls"], [urls[3]])
            self.assertEqual(set(report["requires_followup_urls"]),
                             set(urls[1:]))
            self.assertIsNone(report["model_reviewed_urls"])
            self.assertFalse(batch["batch_complete"])

    def test_audit_report_is_explicit_baseline_only_and_paginated(self):
        sitemap = "https://tuvkarpat.ro/cursuri-sitemap.xml"
        archive = "https://tuvkarpat.ro/cursuri/"
        urls = [f"https://tuvkarpat.ro/cursuri/test-{i}/" for i in range(5)]
        snapshot = {
            urls[0]: {"sha256": "a"*64, "reviewed_at": "2026-10-24T12:00:00+00:00"},
            urls[2]: {"sha256": "b"*64, "reviewed_at": "2026-10-24T12:05:00+00:00"},
        }
        from app.site_fields import normalize_fields
        selectors = normalize_fields(["title", "acf.tematica"])
        with (patch.object(site, "_sitemaps", return_value=[sitemap]),
              patch.object(site, "_urls", return_value=[archive]+urls),
              patch("app.site_audit.get_reviews", return_value=snapshot) as get_reviews,
              patch.object(site, "_read_page") as read):
            report = site.inspect_site(
                "audit_report", sitemap="cursuri-sitemap.xml",
                fields=["title", "acf.tematica"], limit=2)
            self.assertEqual(report["total_urls"], 6)
            self.assertEqual(report["auditable_urls"], 5)
            self.assertEqual(report["acknowledged_urls"], 2)
            self.assertEqual(report["not_acknowledged_urls"], 3)
            self.assertEqual(report["next_offset"], 2)
            self.assertFalse(report["current_site_freshness_checked"])
            self.assertFalse(report["extraction_completed_in_this_report"])
            self.assertEqual(report["entries"][0]["status"],
                             "acknowledged_not_live_verified")
            self.assertEqual(report["entries"][1]["status"], "not_acknowledged")
            next_page = site.inspect_site(
                "audit_report", sitemap="cursuri-sitemap.xml",
                fields=["title", "acf.tematica"], offset=4, limit=2)
            self.assertIsNone(next_page["next_offset"])
            self.assertEqual(len(next_page["entries"]), 1)
            get_reviews.assert_called_with(urls, selectors)
            read.assert_not_called()
        with self.assertRaisesRegex(ValueError, "explicit"):
            site.inspect_site("audit_report", sitemap="all")



if __name__ == "__main__":
    unittest.main()
