import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from citation_registry import CitationRegistry, fingerprint
from grouped_review import build_packet, review_view, apply_review, REPLY
from test_citation_registry import RAW, FR, EN, QUOTE, ENGLISH, ref, document, candidate
from work_library import WorkLibrary


class GroupedReviewTests(unittest.TestCase):
    def setUp(self):
        self.doc = document()
        self.cand = candidate(self.doc)
        self.pages = {'First': RAW, 'Second': RAW.replace(QUOTE, 'Autre citation du même ouvrage.'),
                      'Duplicate': RAW, 'Other': RAW.replace(FR, 'https://example.org/test')}
        self.reads = []

    def load(self, title, revision):
        return self.pages[title]

    def read(self, url, lang):
        self.reads.append((url, lang))
        return self.doc

    def item(self, title, n, candidates=None):
        case = {'id': str(n), 'wiki_title': title, 'citation_ref': ref(self.pages[title], n)}
        result = {'id': str(n), 'review_packet': {'candidates': [self.cand] if candidates is None else candidates}}
        return case, result

    def packet(self, titles=('First', 'Second', 'Duplicate')):
        return build_packet([self.item(t, n) for n, t in enumerate(titles, 1)], self.load, self.read)

    def response(self, packet):
        return {'schema': REPLY, 'packet_sha256': fingerprint(packet), 'reviewer': 'test-reviewer',
                'decisions': [{'id': q, 'decision': 'complete_equivalent',
                               'candidate': s['candidates'][0], 'ranges': [[0, len(ENGLISH)]]}
                              for q, s in packet['sources'].items()]}

    def test_group_and_deduplicate(self):
        p = self.packet()
        self.assertEqual(len(p['sources']), 2)
        self.assertEqual(len(p['sources']['q1']['occurrences']), 2)
        self.assertEqual(len(p['targets']), 1)
        v = review_view(p)
        self.assertEqual(len(v['groups']), 1)
        self.assertEqual(len(v['groups'][0]['chapters']), 1)
        self.assertEqual(len(v['groups'][0]['chapters'][0]['citations']), 2)
        self.assertEqual(len(v['passages']), 1)
        self.assertEqual(self.reads, [(EN, 'en')])
        self.assertNotIn('targets', v)
        self.assertNotIn('sources', v)

    def test_non_mia_and_bad_candidate(self):
        p = self.packet(('Other',))
        self.assertFalse(p['sources'])
        self.assertEqual(p['skipped'][0]['status'], 'ai_translation_required')
        self.assertFalse(self.reads)
        p = build_packet([self.item('First', 1, [{**self.cand, 'text_sha256': '0'*64}])],
                         self.load, self.read)
        self.assertFalse(p['sources'])
        self.assertEqual(p['skipped'][0]['status'], 'no_verified_candidate')

    def test_distinct_chapters_share_work(self):
        self.pages['Second'] = self.pages['Second'].replace('/a.htm', '/b.htm')
        p = self.packet(('First', 'Second'))
        self.assertEqual(len(p['payload']['groups']), 1)
        self.assertEqual(len(p['payload']['groups'][0]['chapters']), 2)
        self.assertEqual(len(p['targets']), 1)

    def test_reply_records_and_reconstructs_without_text(self):
        p = self.packet()
        rv = self.response(p)
        with TemporaryDirectory() as tmp, WorkLibrary(Path(tmp)/'works.sqlite3') as lib:
            registry = CitationRegistry(lib.db)
            for _ in range(2):
                out = apply_review(p, rv, fingerprint(p), registry, self.load, self.read)
                self.assertEqual(out['recorded'], 2)
            self.assertEqual(lib.db.execute('SELECT count(*) FROM prepared_citations_v1').fetchone()[0], 2)
            res = registry.reconstruct(RAW, ref(RAW, 1), self.doc)
            self.assertEqual(res['fragments'][0]['text'], ENGLISH)
            self.assertFalse(res['auto_publish'])

    def test_invalid_reply_is_rejected_before_any_write(self):
        p = self.packet()
        rv = self.response(p)
        with TemporaryDirectory() as tmp, WorkLibrary(Path(tmp)/'works.sqlite3') as lib:
            registry = CitationRegistry(lib.db)
            variants = []
            bad = copy.deepcopy(rv)
            bad['decisions'][1]['candidate'] = 'p999'
            variants.append(bad)
            bad = copy.deepcopy(rv)
            bad['decisions'][1]['ranges'] = [[0, 9999]]
            variants.append(bad)
            bad = copy.deepcopy(rv)
            bad['decisions'].pop()
            variants.append(bad)
            bad = copy.deepcopy(rv)
            bad['decisions'][1]['text'] = 'not allowed'
            variants.append(bad)
            for bad in variants:
                with self.assertRaises(ValueError):
                    apply_review(p, bad, fingerprint(p), registry, self.load, self.read)
            self.assertEqual(lib.db.execute('SELECT count(*) FROM prepared_citations_v1').fetchone()[0], 0)
            changed = copy.deepcopy(p)
            changed['payload']['passages']['p1']['text'] += ' changed'
            with self.assertRaises(ValueError):
                apply_review(changed, rv, fingerprint(p), registry, self.load, self.read)

    def test_stale_evidence_and_abstention(self):
        p = self.packet()
        rv = self.response(p)
        with TemporaryDirectory() as tmp, WorkLibrary(Path(tmp)/'works.sqlite3') as lib:
            registry = CitationRegistry(lib.db)
            self.doc = document(digest='d'*64)
            with self.assertRaises(ValueError):
                apply_review(p, rv, fingerprint(p), registry, self.load, self.read)
            self.assertEqual(lib.db.execute('SELECT count(*) FROM prepared_citations_v1').fetchone()[0], 0)
            rv['decisions'] = [{'id': q, 'decision': 'uncertain', 'candidate': None, 'ranges': []}
                               for q in p['sources']]
            out = apply_review(p, rv, fingerprint(p), registry, self.load, self.read)
            self.assertEqual(out['recorded'], 0)

    def test_packet_limit_does_not_truncate(self):
        with self.assertRaisesRegex(ValueError, 'split_input'):
            build_packet([self.item('First', 1)], self.load, self.read, max_payload_chars=10)
