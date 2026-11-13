"""Offline security and compatibility tests for the extracted site clients."""
import json
import os
import subprocess
import unittest
from unittest.mock import Mock, patch

from app import site_transport as transport
from app import site_wordpress as wordpress
from app import site_inspector as inspector
from app import site_sitemap_reader as sitemap_reader

ROOT = "https://tuvkarpat.ro/"
COURSE = ROOT + "cursuri/introducere-in-ergonomie/"


class TransportTests(unittest.TestCase):
    def test_host_allowlist_blocks_ssrf_without_running_curl(self):
        invalid = (
            "http://tuvkarpat.ro/", "https://evil.example/",
            "https://tuvkarpat.ro.evil.example/",
            "https://tuvkarpat.ro:443/", "https://user@tuvkarpat.ro/",
            "https://tuvkarpat.ro/#fragment", "file:///etc/passwd",
            "https://tuvkarpat.ro/\r\nInjected", None,
        )
        with patch.object(transport.subprocess, "run") as call:
            for url in invalid:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    transport.fetch(url)
            call.assert_not_called()
        self.assertEqual(inspector._allowed(COURSE), COURSE)

    def test_fixed_curl_arguments_no_redirect_and_status_checked(self):
        success = subprocess.CompletedProcess([], 0, b"\xef\xbb\xbftext\n200", b"")
        with patch.object(transport.subprocess, "run", return_value=success) as call:
            self.assertEqual(inspector._fetch(COURSE), "text")
        args = call.call_args.args[0]
        self.assertEqual(args[0], "curl")
        self.assertNotIn("-L", args)
        self.assertNotIn("--location", args)
        self.assertIn("--proto", args)
        self.assertIn("=https", args)
        self.assertEqual(args[-1], COURSE)
        with patch.object(transport.subprocess, "run", return_value=
                          subprocess.CompletedProcess([], 0, b"redirect\n302", b"")):
            with self.assertRaisesRegex(RuntimeError, "HTTP"):
                transport.fetch(COURSE)
        with patch.object(transport.subprocess, "run", return_value=
                          subprocess.CompletedProcess([], 23, b"", b"timeout")):
            with self.assertRaisesRegex(RuntimeError, "curl"):
                transport.fetch(COURSE)

    def test_refresh_evidence_is_per_request(self):
        def curl(args, **kwargs):
            self.assertIn("Cache-Control: no-cache, no-store, max-age=0", args)
            os.write(kwargs["pass_fds"][0],
                     b"HTTP/2 200\r\nCF-Cache-Status: HIT\r\nAge: 1\r\n\r\n")
            return subprocess.CompletedProcess(args, 0, b"OK\n200", b"")
        context = transport._REFRESH.set(True)
        evidence = []
        recording = transport._EVIDENCE.set(evidence)
        try:
            with patch.object(transport.subprocess, "run", side_effect=curl):
                self.assertEqual(transport.fetch(COURSE), "OK")
            self.assertTrue(evidence[0]["cache_hit"])
            self.assertEqual(evidence[0]["url"], COURSE)
            self.assertEqual(evidence[0]["headers"]["age"], "1")
        finally:
            transport._EVIDENCE.reset(recording)
            transport._REFRESH.reset(context)
        self.assertFalse(transport._REFRESH.get())


class SitemapReaderTests(unittest.TestCase):
    def test_cached_xml_is_reused_but_force_refresh_fails_closed(self):
        sitemap = ROOT + "cursuri-sitemap.xml"
        xml = "<urlset><url><loc>" + COURSE + "</loc></url></urlset>"
        fetch = Mock(return_value=xml)
        with patch.dict(sitemap_reader.SITEMAP_CACHE, {}, clear=True):
            self.assertEqual(sitemap_reader.sitemap_xml(
                sitemap, allowed=transport.allowed, fetch=fetch,
                refresh=False,
            ), xml)
            self.assertEqual(sitemap_reader.sitemap_xml(
                sitemap, allowed=transport.allowed,
                fetch=Mock(side_effect=RuntimeError("network unavailable")),
                refresh=False,
            ), xml)
            with self.assertRaisesRegex(RuntimeError, "network unavailable"):
                sitemap_reader.sitemap_xml(
                    sitemap, allowed=transport.allowed,
                    fetch=Mock(side_effect=RuntimeError("network unavailable")),
                    refresh=True,
                )

    def test_sitemap_discovery_excludes_external_urls(self):
        sitemap = ROOT + "cursuri-sitemap.xml"
        index = ROOT + "sitemap_index.xml"
        xml_index = ("<sitemapindex><sitemap><loc>" + sitemap
                     + "</loc></sitemap></sitemapindex>")
        xml_pages = ("<urlset><url><loc>" + COURSE + "</loc></url>"
                     "<url><loc>https://evil.example/</loc></url>"
                     "<url><loc>" + COURSE + "</loc></url></urlset>")
        data = {index: xml_index, sitemap: xml_pages}
        reader = lambda url: data[url]
        self.assertEqual(sitemap_reader.sitemaps(
            index=index, xml_reader=reader, allowed=transport.allowed,
        ), [sitemap])
        self.assertEqual(sitemap_reader.urls(
            sitemap, xml_reader=reader, allowed=transport.allowed,
        ), [COURSE])
        self.assertEqual(sitemap_reader.sitemap_url(
            "cursuri-sitemap.xml", [sitemap], root=ROOT,
            allowed=transport.allowed,
        ), sitemap)
        with self.assertRaises(ValueError):
            sitemap_reader.sitemap_url(
                "https://evil.example/sitemap.xml", [sitemap], root=ROOT,
                allowed=transport.allowed,
            )


class WordPressTests(unittest.TestCase):
    def test_rest_slug_lookup_and_canonical_identity(self):
        item = {"id": 24759, "link": COURSE, "title": {"rendered": "Ergonomie"},
                "acf": {"tematica": "Știință"}}
        fetch = Mock(return_value=json.dumps([item]))
        got = wordpress.api_item("cursuri", COURSE, fetch=fetch, exact_url=True)
        self.assertEqual(got["id"], 24759)
        self.assertIn("slug=introducere-in-ergonomie", fetch.call_args.args[0])
        item["link"] = ROOT + "cursuri/alt-curs/"
        fetch.return_value = json.dumps([item])
        with self.assertRaisesRegex(ValueError, "exact URL"):
            wordpress.api_item("cursuri", COURSE, fetch=fetch, exact_url=True)
        self.assertEqual(transport._EXACT_URL.get(), inspector._EXACT_URL.get())

    def test_course_page_uses_rest_and_html_fallback(self):
        rest = {"id": 24759, "title": {"rendered": "Ergonomie"},
                "link": COURSE, "acf": {"program": "Curs"}}
        lookup = Mock(return_value=rest)
        fetch = Mock()
        result = wordpress.read_page(
            COURSE, allowed=transport.allowed, api_item=lookup, fetch=fetch,
        )
        self.assertEqual(result["format"], "acf_json")
        self.assertEqual(result["acf"], {"program": "Curs"})
        fetch.assert_not_called()
        html = "<body><header>Menu</header><main>Public text</main></body>"
        fetch.return_value = html
        result = wordpress.read_page(
            ROOT + "despre-noi/", allowed=transport.allowed,
            api_item=lookup, fetch=fetch,
        )
        self.assertEqual(result["format"], "page_html")
        self.assertIn("Public text", result["html"])
        self.assertNotIn("Menu", result["html"])

    def test_missing_rest_course_never_silently_falls_back(self):
        fetch = Mock()
        with self.assertRaisesRegex(ValueError, "REST API"):
            wordpress.read_page(
                COURSE, allowed=transport.allowed,
                api_item=lambda *_: None, fetch=fetch,
            )
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
