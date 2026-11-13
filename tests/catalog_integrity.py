"""Adversarial WordPress catalog integrity scenarios (fully offline)."""
import json
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from app import course_catalog as catalog
from app import course_search as search
from app import site_client as site
from app.course_integrity import course_identity, page_size_for_count

SITEMAP = catalog.COURSE_SITEMAP


def record(index: int) -> dict:
    slug = f"curs-{index}"
    return {
        "id": index + 1, "slug": slug, "link": f"https://tuvkarpat.ro/cursuri/{slug}/",
        "title": {"rendered": f"Curs {index} standard ISO"},
        "acf": {"tematica": f"Pregătire ISO numărul {index}"},
        "domenii": [123],
    }


def catalog_responses(items, expected_urls=None):
    urls = expected_urls if expected_urls is not None else [entry["link"] for entry in items]

    def fetch(endpoint, accept="text/html,*/*"):
        if "/domenii?" in endpoint:
            return json.dumps([
                {"id": 123, "name": "Ergonomie", "slug": "ergonomie", "count": len(urls)},
            ])
        q = parse_qs(urlsplit(endpoint).query)
        page = int(q.get("page", ["1"])[0])
        per_page = int(q.get("per_page", ["100"])[0])
        if "/cursuri?" not in endpoint:
            raise AssertionError("Unexpected URL: " + endpoint)
        return json.dumps(items[(page - 1) * per_page:page * per_page])
    return urls, fetch


class IntegrityTests(unittest.TestCase):
    def run_listing(self, items, urls=None):
        public, fetch = catalog_responses(items, expected_urls=urls)
        with (patch.object(site, "sitemaps", return_value=[SITEMAP]),
              patch.object(site, "urls", return_value=public),
              patch.object(site, "fetch", side_effect=fetch)):
            return search.search_courses(action="list_courses")

    def test_valid_catalog_195_is_complete_with_two_native_pages(self):
        items = [record(i) for i in range(195)]
        urls, fetch = catalog_responses(items)
        requests = []
        def tracking(endpoint, accept="text/html,*/*"):
            requests.append(endpoint)
            return fetch(endpoint, accept)
        with (patch.object(site, "sitemaps", return_value=[SITEMAP]),
              patch.object(site, "urls", return_value=urls),
              patch.object(site, "fetch", side_effect=tracking)):
            listing = search.search_courses(action="list_courses")
            found = search.search_courses(queries=["ISO"])
        self.assertTrue(listing["complete"])
        self.assertEqual(listing["total_courses"], 195)
        self.assertEqual(listing["scanned_courses"], 195)
        self.assertEqual(listing["missing_courses_count"], 0)
        self.assertTrue(found["complete"])
        self.assertEqual(found["matching_courses"], 195)
        self.assertEqual(sum("page=" in req for req in requests), 4)
        self.assertFalse(any("page=3" in req for req in requests))

    def test_duplicate_url_causes_incomplete_listing_and_search(self):
        first, second = record(0), record(1)
        items = [first, first, second]
        public, fetch = catalog_responses(items, expected_urls=[first["link"], second["link"]])
        with (patch.object(site, "sitemaps", return_value=[SITEMAP]),
              patch.object(site, "urls", return_value=public),
              patch.object(site, "fetch", side_effect=fetch),
              patch.object(site, "load_page", side_effect=lambda url, *_args, **_kwargs: (
                  {"format": "acf_json", "link": url, "title": "Recovered", "acf": {"tematica": "ISO"}}, {},
              ))):
            listing = search.search_courses(action="list_courses")
            found = search.search_courses(queries=["ISO"])
        self.assertFalse(listing["complete"])
        self.assertEqual(listing["courses"], [])
        self.assertEqual(listing["missing_courses_count"], 2)
        self.assertIn("duplicat", listing["errors"][0]["error"])
        self.assertFalse(found["complete"])
        self.assertTrue(found["errors"])
        # Even recovered sources cannot clear an integrity failure.
        self.assertEqual(found["scanned_courses"], 2)

    def test_outside_sitemap_and_duplicate_wordpress_ids_fail_closed(self):
        first, second, third = record(0), record(1), record(2)
        for bad_items, expected in (
            ([first, second, third], "sitemap"),
            ([first, {**second, "id": first["id"]}], "duplicat"),
            ([first, {**second, "slug": "wrong-slug"}], "Slug"),
        ):
            with self.subTest(error=expected):
                listing = self.run_listing(bad_items, urls=[first["link"], second["link"]])
                self.assertFalse(listing["complete"])
                self.assertTrue(any(expected in x["error"] for x in listing["errors"]))

    def test_missing_acf_still_recovers_by_exact_url(self):
        first, second = record(0), record(1)
        second["acf"] = None
        public, fetch = catalog_responses([first, second])
        with (patch.object(site, "sitemaps", return_value=[SITEMAP]),
              patch.object(site, "urls", return_value=public),
              patch.object(site, "fetch", side_effect=fetch),
              patch.object(site, "load_page", return_value=(
                  {"format": "acf_json", "link": second["link"], "title": second["title"]["rendered"],
                   "acf": {"tematica": "ISO recovered"}}, {},
              )) as recover):
            found = search.search_courses(queries=["ISO"])
        self.assertTrue(found["complete"])
        self.assertEqual(found["scanned_courses"], 2)
        recover.assert_called_once_with(second["link"], False, exact_url=True)

    def test_network_failure_returns_explicit_incomplete_listing(self):
        first = record(0)
        with (patch.object(site, "sitemaps", return_value=[SITEMAP]),
              patch.object(site, "urls", return_value=[first["link"]]),
              patch.object(site, "fetch", side_effect=RuntimeError("HTTP 503"))):
            result = search.search_courses(action="list_courses")
        self.assertFalse(result["complete"])
        self.assertEqual(result["missing_courses_count"], 1)
        self.assertIn("503", result["errors"][0]["error"])

    def test_partial_rest_with_bounded_recovery_never_claims_complete(self):
        items = [record(i) for i in range(195)]
        public = [entry["link"] for entry in items]
        _, fetch = catalog_responses(items[:180], expected_urls=public)
        with (patch.object(site, "sitemaps", return_value=[SITEMAP]),
              patch.object(site, "urls", return_value=public),
              patch.object(site, "fetch", side_effect=fetch),
              patch.object(site, "load_page", side_effect=RuntimeError("HTTP 503"))):
            found = search.search_courses(queries=["ISO"])
        self.assertFalse(found["complete"])
        self.assertEqual(found["total_courses"], 195)
        self.assertEqual(found["scanned_courses"], 180)
        self.assertEqual(found["missing_courses_count"], 15)
        self.assertTrue(found["errors"])

    def test_domain_100_checks_extra_page_and_detects_101st(self):
        items = [record(i) for i in range(101)]
        public = [entry["link"] for entry in items]
        requests = []
        def fetch(endpoint, accept="text/html,*/*"):
            if "/domenii?" in endpoint:
                return json.dumps([
                    {"id": 123, "name": "Ergonomie", "slug": "ergonomie", "count": 100}
                ])
            q = parse_qs(urlsplit(endpoint).query)
            page, size = int(q["page"][0]), int(q["per_page"][0])
            requests.append((page, size))
            return json.dumps(items[(page - 1) * size:page * size])
        with (patch.object(site, "sitemaps", return_value=[SITEMAP]),
              patch.object(site, "urls", return_value=public),
              patch.object(site, "fetch", side_effect=fetch)):
            with self.assertRaisesRegex(RuntimeError, "mai multe cursuri"):
                catalog.domain_items(123)
        self.assertEqual(requests, [(1, 99), (2, 99)])

    def test_domain_100_exact_count_still_succeeds_without_overfetch(self):
        items = [record(i) for i in range(100)]
        public = [entry["link"] for entry in items]
        requests = []
        def fetch(endpoint, accept="text/html,*/*"):
            if "/domenii?" in endpoint:
                return json.dumps([
                    {"id": 123, "name": "Ergonomie", "slug": "ergonomie", "count": 100}
                ])
            q = parse_qs(urlsplit(endpoint).query)
            page, size = int(q["page"][0]), int(q["per_page"][0])
            requests.append((page, size))
            return json.dumps(items[(page - 1) * size:page * size])
        with (patch.object(site, "sitemaps", return_value=[SITEMAP]),
              patch.object(site, "urls", return_value=public),
              patch.object(site, "fetch", side_effect=fetch)):
            term, records = catalog.domain_items(123)
        self.assertEqual(len(records), 100)
        self.assertEqual(term["count"], 100)
        self.assertEqual(requests, [(1, 99), (2, 99)])

    def test_pagination_size_avoids_exact_boundary(self):
        for count in (1, 2, 99, 100, 101, 195, 198, 200, 300, 1000):
            with self.subTest(count=count):
                size = page_size_for_count(count)
                self.assertTrue(1 <= size <= 100)
                self.assertNotEqual(count % size, 0)
        with self.assertRaises(ValueError):
            page_size_for_count(-1)

    def test_identity_rejects_inconsistent_title_slug_and_domain_ids(self):
        valid = record(2)
        self.assertEqual(course_identity(valid), (valid["id"], valid["link"]))
        for bad in (
            {**valid, "title": {"rendered": ""}},
            {**valid, "domenii": [123, 123]},
            {**valid, "id": True},
            {**valid, "link": "https://other.example/cursuri/curs-2/"},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                course_identity(bad)


if __name__ == "__main__":
    unittest.main()
