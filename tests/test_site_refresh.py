"""Offline refresh and exact-source verification regressions."""
import hashlib
import os
import subprocess
import time
import unittest
from unittest.mock import patch

from app import site_inspector as site
from app.site_fields import extract_with_sources

URL = 'https://tuvkarpat.ro/cursuri/test/'
SITEMAP = 'https://tuvkarpat.ro/cursuri-sitemap.xml'


def page(text='munca și munca'):
    return {'url': URL, 'link': URL, 'format': 'acf_json', 'title': 'Curs',
            'acf': {'rows': [{'text': 'alt curs'}, {'text': text}]}}


def finding(**overrides):
    return {'url': URL, 'field': 'acf.rows[1].text', 'original': 'munca',
            'replacement': 'muncă', **overrides}


class RefreshTests(unittest.TestCase):
    def test_http_refresh_cache_evidence_and_unique_request(self):
        urls = []
        def curl(args, **kwargs):
            urls.append(args[-1])
            self.assertIn('Cache-Control: no-cache, no-store, max-age=0', args)
            self.assertIn('Pragma: no-cache', args)
            fd = kwargs['pass_fds'][0]
            os.write(fd, b'HTTP/1.1 200 OK\r\nCF-Cache-Status: HIT\r\nAge: 42\r\nX-LiteSpeed-Cache: hit\r\n\r\n')
            return subprocess.CompletedProcess(args, 0, b'<main>Actual</main>\n200', b'')
        with patch.object(site.subprocess, 'run', side_effect=curl):
            a = site.inspect_site('get', url='https://tuvkarpat.ro/test/', view='text', force_refresh=True)
            b = site.inspect_site('get', url='https://tuvkarpat.ro/test/', view='text', force_refresh=True)
        self.assertNotEqual(urls[0], urls[1])
        self.assertEqual(a['provenance'], 'cache')
        self.assertEqual(a['http_evidence'][0]['headers']['age'], '42')
        self.assertEqual(a['sha256'], b['sha256'])
        self.assertTrue(a['fetched_at'])
        self.assertFalse(site._REFRESH.get())

    def test_miss_or_missing_headers_never_proves_origin(self):
        for headers in (b'', b'HTTP/2 200\r\nCF-Cache-Status: MISS\r\nX-Cache: BYPASS\r\n\r\n'):
            def curl(args, **kwargs):
                os.write(kwargs['pass_fds'][0], headers)
                return subprocess.CompletedProcess(args, 0, b'<p>text</p>\n200', b'')
            with patch.object(site.subprocess, 'run', side_effect=curl):
                got = site.inspect_site('get', url='https://tuvkarpat.ro/test/', force_refresh=True)
            self.assertEqual(got['provenance'], 'unknown')

    def test_sitemap_cache_bypass_and_no_fallback(self):
        xml = '<urlset><url><loc>' + URL + '</loc></url></urlset>'
        with patch.dict(site._SITEMAP_CACHE, {SITEMAP: (time.monotonic(), xml)}, clear=True):
            with patch.object(site, '_fetch', side_effect=RuntimeError('offline')) as fetch:
                self.assertEqual(site._sitemap_xml(SITEMAP), xml)
                fetch.assert_not_called()
                with patch.object(site, '_sitemaps', return_value=[SITEMAP]):
                    with self.assertRaisesRegex(RuntimeError, 'offline'):
                        site.inspect_site('batch_get', sitemap=SITEMAP, force_refresh=True)
        with patch.object(site, '_fetch', side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(RuntimeError, 'offline'):
                site.inspect_site('get', url=URL, force_refresh=True)

    def test_acf_hash_source_map_and_pending_parts_unchanged(self):
        data = page('munca ' * 600)
        with (patch.object(site, '_sitemaps', return_value=[SITEMAP]),
              patch.object(site, '_urls', return_value=[URL]),
              patch.object(site, '_read_page', return_value=data)):
            normal = site.inspect_site('get', url=URL, view='text', max_chars=1000)
            batch = site.inspect_site('batch_get', sitemap=SITEMAP, max_chars=1000, force_refresh=True)
            first = batch['pages'][0]
            self.assertEqual(first['sha256'], normal['sha256'])
            self.assertEqual(first['source_map'], normal['source_map'])
            self.assertFalse(batch['batch_complete'])
            self.assertEqual(len(batch['pending_parts']), 1)
            request = first['retrieve_remaining']
            self.assertTrue(request['force_refresh'])
            parts = [first['content']]
            while request:
                got = site.inspect_site(**request)
                parts.append(got['content'])
                request = {**request, 'part': got['next_part']} if got['next_part'] is not None else None
            self.assertEqual(''.join(parts), extract_with_sources(data)[0])
            raw1 = site.inspect_site('get', url=URL)
            raw2 = site.inspect_site('get', url=URL, force_refresh=True)
            self.assertEqual(raw1['sha256'], raw2['sha256'])

    def test_refresh_reads_new_content_and_continuation_detects_change(self):
        with patch.object(site, '_read_page', side_effect=[page('old ' * 500), page('new ' * 500)]):
            first = site.inspect_site('get', url=URL, view='text', max_chars=1000)
            with self.assertRaisesRegex(ValueError, 'schimbat'):
                site.inspect_site('get', url=URL, view='text', max_chars=1000,
                                  part=1, expected_hash=first['sha256'], force_refresh=True)

    def test_force_reaches_worker_and_rest_transport(self):
        def fetch(url, accept=''):
            self.assertTrue(site._REFRESH.get())
            return '[{"id": 1, "title": {"rendered": "Curs"}, "acf": {"text": "munca"}}]'
        with (patch.object(site, '_sitemaps', return_value=[SITEMAP]),
              patch.object(site, '_urls', return_value=[URL]),
              patch.object(site, '_fetch', side_effect=fetch)):
            result = site.inspect_site('batch_get', sitemap=SITEMAP, force_refresh=True)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['processed'], 1)


class FindingTests(unittest.TestCase):
    def test_statuses_positions_repeated_and_deduplicated_reads(self):
        data = page()
        checksum = hashlib.sha256(extract_with_sources(data)[0].encode()).hexdigest()
        entries = [finding(expected_hash=checksum), finding(expected_hash='0' * 64),
                   finding(field='acf.rows[0].text'), finding(field='acf.rows[9].text'),
                   finding(original='Munca'), finding(url='https://evil.test/cursuri/test/')]
        with patch.object(site, '_read_page', return_value=data) as read:
            result = site.inspect_site('verify_findings', findings=entries, force_refresh=True)
        self.assertEqual(read.call_count, 1)
        self.assertEqual([r['status'] for r in result['results']],
                         ['verified', 'content_changed', 'not_found', 'field_not_found', 'not_found', 'error'])
        matches = result['results'][0]['matches']
        self.assertEqual([(m['start'], m['end']) for m in matches], [(0, 5), (9, 14)])
        self.assertTrue(all(m['field'] == 'acf.rows[1].text' for m in matches))
        self.assertEqual(matches[0]['context'], 'munca și munca')

    def test_wrong_course_url_and_incomplete_sources(self):
        for data in (page(), {**page(), 'acf': None}, {**page(), 'acf': {}},
                     {**page(), 'content_complete': False}):
            entry = finding(url=URL.replace('/test/', '/other/test/')) if data == page() else finding()
            with patch.object(site, '_read_page', return_value=data):
                got = site.inspect_site('verify_findings', findings=[entry])['results'][0]
            self.assertEqual(got['status'], 'error')

    def test_failure_is_individual_deduplicated_and_no_cache_fallback(self):
        with patch.object(site, '_read_page', side_effect=RuntimeError('HTTP 404')) as read:
            got = site.inspect_site('verify_findings', findings=[finding(), finding()], force_refresh=True)
        self.assertEqual(read.call_count, 1)
        self.assertEqual([r['status'] for r in got['results']], ['error', 'error'])
        self.assertIn('404', got['results'][0]['error'])

    def test_hash_selection_same_as_get_and_exact_leaf_only(self):
        with patch.object(site, '_read_page', return_value=page()):
            selected = site.inspect_site('get', url=URL, view='text', fields=['acf.rows'])
            got = site.inspect_site('verify_findings', fields=['acf.rows'], findings=[
                finding(expected_hash=selected['sha256']), finding(field='acf.rows')])
        self.assertEqual([r['status'] for r in got['results']], ['verified', 'field_not_found'])

    def test_rest_identity_requires_explicit_matching_link(self):
        import json
        for link in (None, URL.replace('/test/', '/other/')):
            item = {'title': {'rendered': 'Curs'}, 'acf': {'text': 'munca'}}
            if link:
                item['link'] = link
            with patch.object(site, '_fetch', return_value=json.dumps([item])):
                got = site.inspect_site('verify_findings', findings=[finding()])['results'][0]
            self.assertEqual(got['status'], 'error')

    def test_overlapping_matches_are_all_reported(self):
        with patch.object(site, '_read_page', return_value=page('aaaa')):
            got = site.inspect_site('verify_findings', findings=[finding(original='aa')])['results'][0]
        self.assertEqual([m['start'] for m in got['matches']], [0, 1, 2])

    def test_invalid_input_and_partial_http(self):
        for entry in (None, finding(field='rows'), finding(original=''), finding(expected_hash='bad')):
            with patch.object(site, '_read_page') as read:
                got = site.inspect_site('verify_findings', findings=[entry])['results'][0]
            self.assertEqual(got['status'], 'error')
            read.assert_not_called()
        partial = subprocess.CompletedProcess([], 18, b'{"acf":', b'transfer closed')
        with patch.object(site.subprocess, 'run', return_value=partial):
            got = site.inspect_site('verify_findings', findings=[finding()], force_refresh=True)['results'][0]
        self.assertEqual(got['status'], 'error')


if __name__ == '__main__':
    unittest.main()
