from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from alignment import text_and_starts
from citation_registry import fingerprint
from grouped_review import build_packet, apply_review, REPLY
from review_context import exact_target, expand_target
from resolver import sha
from test_citation_registry import RAW, QUOTE, EN, document, ref
from work_library import WorkLibrary
from citation_registry import CitationRegistry


def seed(doc, lo, hi):
    text, _ = text_and_starts(doc)
    return {'url': doc.url, 'revision': doc.revision, 'sha256': doc.digest,
            'char_start': lo, 'char_end': hi,
            'text_sha256': sha(text[lo:hi].encode())}


class ExpandedReviewTests(unittest.TestCase):
    def setUp(self):
        self.doc = document(blocks=['Unrelated introductory paragraph.']*4 +
                            ['Missing beginning of the quotation.',
                             'Recognized middle sentence.',
                             'Missing final sentence of the quotation.'] +
                            ['Unrelated following paragraph.']*4)
        self.text, self.starts = text_and_starts(self.doc)
        self.start = self.starts[5]
        self.end = self.start + len(self.doc.blocks[5])
        self.original = seed(self.doc, self.start, self.end)
        self.raws = {'First': RAW, 'Second': RAW.replace(QUOTE, 'Autre passage.')}
        self.reads = []

    def load(self, title, revision):
        return self.raws[title]

    def read(self, url, lang):
        self.reads.append(url)
        return self.doc

    def item(self, title, cid, cand):
        return ({'id': cid, 'wiki_title': title, 'citation_ref': ref(self.raws[title])},
                {'id': cid, 'candidates': [cand]})

    def test_expansion_includes_both_missing_boundaries_and_exact_hash(self):
        t = expand_target(self.doc, exact_target(self.doc, self.start, self.end), 140)
        excerpt = self.text[t['char_start']:t['char_end']]
        self.assertIn(self.doc.blocks[4], excerpt)
        self.assertIn(self.doc.blocks[6], excerpt)
        self.assertEqual(t['text_sha256'], sha(excerpt.encode()))
        small = expand_target(self.doc, exact_target(self.doc, self.start, self.end),
                              140, limit=40)
        self.assertLessEqual(small['char_end']-small['char_start'], 40)
        self.assertLessEqual(small['char_start'], self.start)
        self.assertGreaterEqual(small['char_end'], self.end)

    def test_overlaps_are_shared_and_adjusted_ranges_are_importable(self):
        p = build_packet([
            self.item('First', '1', self.original),
            self.item('Second', '2', seed(self.doc, self.starts[6],
                                         self.starts[6]+len(self.doc.blocks[6])))],
            self.load, self.read, expanded_context=True, distinct_occurrences=True)
        self.assertEqual(len(p['targets']), 1)
        self.assertEqual(len(p['sources']), 2)
        self.assertEqual(self.reads, [EN])
        pid, target = next(iter(p['targets'].items()))
        span = p['sources']['q1']['seed_ranges'][pid][0]
        self.assertEqual(span, [self.start-target['char_start'],
                                self.end-target['char_start']])
        lo = self.starts[4]-target['char_start']
        hi = self.starts[6]+len(self.doc.blocks[6])-target['char_start']
        reply = {'schema': REPLY, 'packet_sha256': fingerprint(p), 'reviewer': 'test',
                 'decisions': [{'id':'q1', 'decision':'boundary_adjustment',
                                'candidate':pid, 'ranges':[[lo,hi]]},
                               {'id':'q2', 'decision':'uncertain',
                                'candidate':None, 'ranges':[]}]}
        with TemporaryDirectory() as tmp, WorkLibrary(Path(tmp)/'works.sqlite3') as lib:
            reg = CitationRegistry(lib.db)
            self.assertEqual(apply_review(p, reply, fingerprint(p), reg, self.load,
                                          self.read)['recorded'], 1)
            reconstructed = reg.reconstruct(RAW, ref(RAW), self.doc)
            self.assertEqual(reconstructed['fragments'][0]['text'],
                             '\n\n'.join(self.doc.blocks[4:7]))

    def test_stale_anchor_is_not_expanded(self):
        bad = {**self.original, 'text_sha256': '0'*64}
        p = build_packet([self.item('First', '1', bad)], self.load, self.read,
                         expanded_context=True)
        self.assertFalse(p['targets'])
        self.assertEqual(p['skipped'][0]['status'], 'no_verified_candidate')

    def test_distinct_documents_are_never_joined(self):
        other = document(url=EN+'2', blocks=self.doc.blocks[:])
        p = build_packet([self.item('First', '1', self.original),
                          self.item('Second', '2', seed(other, self.start, self.end))],
                         self.load, lambda url, lang: self.doc if url == EN else other,
                         expanded_context=True, distinct_occurrences=True)
        self.assertEqual(len(p['targets']), 2)
