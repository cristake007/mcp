"""Focused regression tests for audit identity, hash, and write boundaries."""
import hashlib
import unittest
from unittest.mock import Mock, patch

from app import site_audit_operations as service
from app.site_fields import extract_with_sources, normalize_fields

URL = "https://tuvkarpat.ro/cursuri/ergonomie/"
OTHER = "https://tuvkarpat.ro/cursuri/alt-curs/"
SELECTORS = normalize_fields(["title", "acf.tematica"])


def page(url=URL, *, link=None):
    return {
        "url": url, "link": link if link is not None else url,
        "format": "acf_json", "title": "Ergonomie",
        "acf": {"tematica": "Țară și știință"},
    }


def checksum(data):
    content, _, _, _ = extract_with_sources(data, SELECTORS)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class AuditOperationTests(unittest.TestCase):
    def test_acknowledgement_requires_canonical_url_match(self):
        wrong_page = page(link=OTHER)
        get_page = Mock(return_value=(wrong_page, {}))
        with patch.object(service.site_audit, "save_reviews") as persist:
            with self.assertRaisesRegex(ValueError, "exact URL"):
                service.acknowledge(
                    URL, checksum(wrong_page), None, SELECTORS,
                    allowed=lambda url: url, load_page=get_page,
                )
            persist.assert_not_called()
        get_page.assert_called_once_with(URL, False, exact_url=True)

    def test_acknowledgement_batch_never_partially_persists(self):
        right = page()
        mismatch = page(OTHER, link=URL)
        def load(url, refresh, exact_url=False):
            return (right if url == URL else mismatch, {})
        with patch.object(service.site_audit, "save_reviews") as persist:
            with self.assertRaisesRegex(ValueError, "exact URL"):
                service.acknowledge(
                    "", "", [
                        {"url": URL, "sha256": checksum(right)},
                        {"url": OTHER, "sha256": checksum(mismatch)},
                    ], SELECTORS,
                    allowed=lambda url: url, load_page=load,
                )
            persist.assert_not_called()

    def test_acknowledgement_success_explicit_and_honours_refresh(self):
        current = page()
        load = Mock(return_value=(current, {}))
        with patch.object(service.site_audit, "save_reviews") as persist:
            result = service.acknowledge(
                URL, checksum(current), None, SELECTORS,
                allowed=lambda url: url, load_page=load, force_refresh=True,
            )
        self.assertEqual(result["reviewed"], 1)
        self.assertEqual(result["coverage"], "selected")
        persist.assert_called_once_with([(URL, checksum(current))], SELECTORS)
        load.assert_called_once_with(URL, True, exact_url=True)

    def test_source_findings_are_deduplicated_and_read_only(self):
        current = page()
        load = Mock(return_value=(current, {"provenance": "unknown"}))
        observations = [
            {"url": URL, "field": "acf.tematica", "original": "Țară",
             "replacement": "Țara"},
            {"url": URL, "field": "acf.tematica", "original": "absent",
             "replacement": "Absent"},
        ]
        with patch.object(service.site_audit, "save_reviews") as persist:
            result = service.verify_findings(
                observations, SELECTORS, True, allowed=lambda x: x,
                load_page=load,
            )
            persist.assert_not_called()
        self.assertEqual([x["status"] for x in result["results"]],
                         ["verified", "not_found"])
        self.assertEqual(load.call_count, 1)
        load.assert_called_once_with(URL, True, exact_url=True)

    def test_field_matching_reports_unicode_character_offsets(self):
        content, _, spans, _ = extract_with_sources(page(), SELECTORS)
        total, hits = service.quote_matches(content, spans, "știință")
        self.assertEqual(total, 1)
        self.assertEqual(hits[0]["field"], "acf.tematica")
        self.assertEqual(hits[0]["end"] - hits[0]["start"], len("știință"))


if __name__ == "__main__":
    unittest.main()
