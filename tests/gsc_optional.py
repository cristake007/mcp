"""Offline GSC OAuth and tool registration tests; no live credentials/API."""
import asyncio
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from fastmcp import FastMCP
from pydantic import TypeAdapter

from app import gsc_client, gsc_tools
from app.server import mcp


URL = "https://tuvkarpat.ro/cursuri/introducere-in-ergonomie/"


class GscToolsTests(unittest.TestCase):
    def test_inputs_are_strict_and_no_network_on_validation_errors(self):
        for start, end in (
            ("2026-11-15", "2026-11-01"),
            ("2026-11-1", "2026-11-02"),
            ("2025-01-01", "2026-11-01"),
            ("2026-11-01", "2027-12-01"),
        ):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                gsc_tools.gsc_performance(start, end)
        with self.assertRaises(ValueError):
            gsc_tools.gsc_performance("2026-11-01", "2026-11-03", limit=1001)
        with self.assertRaises(ValueError):
            gsc_tools.gsc_performance("2026-11-01", "2026-11-03", offset=-1)
        with self.assertRaises(ValueError):
            gsc_tools.gsc_performance("2026-11-01", "2026-11-03", page_filter="https://evil.test/")
        for url in ("https://example.com/cursuri/", "http://tuvkarpat.ro/cursuri/", 
                    "https://tuvkarpat.ro.evil.test/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                gsc_client.inspect_url(url)

    def test_property_is_strict_and_supports_domain_or_prefix(self):
        with patch.dict(os.environ, {"MCP_GSC_SITE_URL": "sc-domain:tuvkarpat.ro"}):
            self.assertEqual(gsc_client.property_url(), "sc-domain:tuvkarpat.ro")
        with patch.dict(os.environ, {"MCP_GSC_SITE_URL": "https://tuvkarpat.ro/"}):
            self.assertEqual(gsc_client.property_url(), "https://tuvkarpat.ro/")
        with patch.dict(os.environ, {"MCP_GSC_SITE_URL": "sc-domain:other.example"}):
            with self.assertRaises(RuntimeError):
                gsc_client.property_url()

    def test_performance_request_and_response_are_bounded(self):
        fake = {"rows": [
            {"keys": [URL], "clicks": 3, "impressions": 90,
             "ctr": 1 / 30, "position": 11.5},
        ]}
        with patch.object(gsc_client, "search_analytics", return_value=fake) as call:
            value = gsc_tools.gsc_performance(
                "2026-11-01", "2026-11-05",
                dimension="page", page_filter="/cursuri/", limit=20,
            )
        self.assertEqual(value["rows"][0]["value"], URL)
        self.assertEqual(value["rows"][0]["impressions"], 90)
        self.assertTrue(0 < value["rows"][0]["ctr"] < 1)
        self.assertEqual(value["returned"], 1)
        payload = call.call_args.args[0]
        self.assertEqual(payload["dataState"], "final")
        self.assertEqual(payload["rowLimit"], 20)
        self.assertEqual(payload["dimensionFilterGroups"][0]["filters"][0]["expression"],
                         "/cursuri/")
        TypeAdapter(gsc_tools.PerformanceResult).validate_python(value)

    def test_index_inspection_returns_last_known_google_state(self):
        fake = {"inspectionResult": {"indexStatusResult": {
            "verdict": "PASS", "coverageState": "Submitted and indexed",
            "indexingState": "INDEXING_ALLOWED", "googleCanonical": URL,
            "lastCrawlTime": "2026-11-11T14:20:00Z", "sitemap": ["https://tuvkarpat.ro/cursuri-sitemap.xml"],
        }}}
        with patch.object(gsc_client, "inspect_url", return_value=fake) as call:
            x = gsc_tools.gsc_inspect_url(URL)
        call.assert_called_once_with(URL)
        self.assertEqual(x["verdict"], "PASS")
        self.assertEqual(x["google_canonical"], URL)
        self.assertEqual(x["sitemaps"], ["https://tuvkarpat.ro/cursuri-sitemap.xml"])
        self.assertIn("NU verificare live", x["note"])
        TypeAdapter(gsc_tools.InspectionResult).validate_python(x)

    def test_no_credential_file_is_read_at_module_import(self):
        # This succeeds without /run/secrets/gsc.json and no real Google API call.
        self.assertEqual(gsc_client.CREDENTIALS_PATH, "/run/secrets/gsc.json")
        self.assertTrue(callable(gsc_tools.gsc_inspect_url))

    def test_transport_is_a_strict_fixed_endpoint_and_redacts_http_errors(self):
        with self.assertRaises(ValueError):
            gsc_client.post_json("https://evil.example/other", {})
        with patch.object(gsc_client, "_access_token", return_value="fake-token"):
            with patch.object(gsc_client, "urlopen",
                side_effect=HTTPError("https://www.googleapis.com", 403, "Forbidden", {}, None)):
                with self.assertRaisesRegex(RuntimeError, "HTTP 403") as err:
                    gsc_client.search_analytics({"startDate": "2026-11-01"})
                self.assertNotIn("fake-token", str(err.exception))
            response = unittest.mock.MagicMock()
            response.status = 200
            response.read.return_value = json.dumps({"rows": []}).encode()
            with patch.object(gsc_client, "urlopen") as http:
                http.return_value.__enter__.return_value = response
                result = gsc_client.search_analytics({"startDate": "2026-11-01"})
                self.assertEqual(result, {"rows": []})
                request = http.call_args.args[0]
                self.assertEqual(request.get_header("Authorization"), "Bearer fake-token")
                self.assertIn("sc-domain%3Atuvkarpat.ro", request.full_url)

    def test_typed_mcp_and_private_registration_toggle(self):
        server = FastMCP("gsc-standalone-test")
        server.tool()(gsc_tools.gsc_performance)
        server.tool()(gsc_tools.gsc_inspect_url)
        described = {item.name: item for item in asyncio.run(server.list_tools())}
        self.assertEqual(set(described), {"gsc_performance", "gsc_inspect_url"})
        self.assertIn("rows", described["gsc_performance"].output_schema["required"])
        enabled = os.environ.get("MCP_GSC_ENABLED") == "1"
        disabled = set(filter(None, os.environ.get("MCP_DISABLED_TOOLS", "").split(",")))
        actual = {tool.name for tool in asyncio.run(mcp.list_tools())}
        for name in ("gsc_performance", "gsc_inspect_url"):
            self.assertEqual(name in actual, enabled and name not in disabled)


if __name__ == "__main__":
    unittest.main()
