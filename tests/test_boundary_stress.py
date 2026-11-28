import json
import unittest
from types import SimpleNamespace

from alignment import text_and_starts
from boundary_stress import (clipped_span, recover_reference,
                             measure_boundary_recovery, SCENARIOS)
from grouped_review import build_packet
from review_context import exact_target
from resolver import sha
from test_citation_registry import RAW, QUOTE, EN, document, ref


class BoundaryStressTests(unittest.TestCase):
    def test_clips_contiguous_whole_tokens_on_each_side(self):
        text = 'prefix: ' + ' '.join('word%d' % i for i in range(20)) + ' :suffix'
        lo, hi = 8, len(text)-8
        a, b = clipped_span(text, lo, hi, .25, .25)
        self.assertEqual(text[a:b], ' '.join('word%d' % i for i in range(5,15)))
        self.assertIsNone(clipped_span('tiny example', 0, 12, .1, .1))
        with self.assertRaisesRegex(ValueError, 'invalid_stress_input'):
            clipped_span(text, lo, hi, .75, .25)

    def test_exposes_boundary_loss_despite_seed_retention(self):
        block = ' '.join(['token']*500)
        doc = document(blocks=[block])
        target = exact_target(doc, 0, len(block))
        result = recover_reference(doc, target, 'a'*1800, SCENARIOS['left_25'])
        self.assertEqual(result['original']['fully_restored'], 0)
        self.assertEqual(result['expanded']['fully_restored'], 1)
        self.assertEqual(result['adaptive']['fully_restored'], 0)
        self.assertGreater(result['adaptive']['missing_characters'], 0)
        self.assertEqual(result['adaptive']['missing_right'], 0)

    def test_short_anchor_fallback_recovers_reference(self):
        block = ' '.join(['token']*100)
        doc = document(blocks=[block])
        target = exact_target(doc, 0, len(block))
        result = recover_reference(doc, target, 'a'*1000, SCENARIOS['both_25'])
        self.assertEqual(result['expanded'], result['adaptive'])
        self.assertEqual(result['adaptive']['fully_restored'], 1)
        bad = {**target, 'text_sha256':'0'*64}
        with self.assertRaisesRegex(ValueError, 'reference_evidence_changed'):
            recover_reference(doc, bad, 'a'*1000, SCENARIOS['both_25'])

    def test_aggregate_contains_only_counts_and_scope(self):
        block = ' '.join(['private-fixture-token']*20)
        doc = document(blocks=[block])
        candidate = {'url':doc.url, 'revision':doc.revision, 'sha256':doc.digest,
                     'char_start':0, 'char_end':len(block),
                     'text_sha256':sha(block.encode())}
        case = {'id':'private-case', 'wiki_title':'Private title',
                'citation_ref':ref(RAW)}
        packet = build_packet([(case, {'id':case['id'], 'candidates':[candidate]})],
                              lambda *_: RAW, lambda *_: doc,
                              distinct_occurrences=True)
        report = measure_boundary_recovery([packet], SimpleNamespace(document=lambda *_:doc))
        self.assertEqual(report['reference_anchors'], 1)
        self.assertEqual(report['distinct_occurrences_with_trials'], 1)
        self.assertEqual(report['distinct_documents_with_trials'], 1)
        for scenario in report['scenarios'].values():
            self.assertEqual(set(scenario), {'original','expanded','adaptive'})
            for stats in scenario.values():
                self.assertEqual(stats['trials'], 1)
        self.assertIsNone(report['semantic_equivalence'])
        encoded = json.dumps(report)
        for forbidden in ('private-case','Private title','private-fixture-token', QUOTE, EN):
            self.assertNotIn(forbidden, encoded)
