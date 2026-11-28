from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from alignment import text_and_starts
from alignment_store import encode
from citation_registry import fingerprint, source_identity
from grouped_review import build_packet, REPLY, review_view
from prepare_citations import Campaign, AWAITING, INSUFFICIENT, REVIEWED
from resolver import sha
from review_bundle import build_bundle
from review_reuse import PLAN, prepare
from test_citation_registry import RAW, QUOTE, ref, document
from work_library import WorkLibrary


class ReuseReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.lib = WorkLibrary(Path(tmp.name)/'works.sqlite3')
        self.addCleanup(self.lib.db.close)
        self.doc = document(blocks=['First test sentence. Another test 🐈 sentence. Final test sentence.'])
        self.text, _ = text_and_starts(self.doc)
        self.pages = {'One': RAW, 'Two': RAW.replace(QUOTE, 'Une autre citation de test.'),
                      'Three': RAW.replace(QUOTE, 'Une troisième citation de test.')}
        cases = []
        for i, (title, raw) in enumerate(self.pages.items(), 1):
            r = ref(raw, i)
            source = source_identity(raw, r)
            cases.append({'id': str(i), 'wiki_title': title, 'citation_ref': r,
                          'source_key': source['source_key'], 'mia_url': source['mia_url']})
        self.c = Campaign(self.lib, {'cases': cases, 'pages': [], 'manifest_sha256': 'a'*64})
        self.audit = SimpleNamespace(document=lambda url, lang: self.doc, publication={})
        self.mid = self.text.index('Another')
        self.end = self.text.index(' Final')
        self.prior = self.history('1', 0, self.mid, 'uncertain')
        self.donor = self.history('2', self.mid, len(self.text), 'complete_equivalent')
        self.plan = {'schema': PLAN, 'inventory_sha256': 'a'*64,
                     'entries': [{'packet': self.prior, 'qid': 'q1',
                                  'donor_packet': self.donor, 'donor_pid': 'p1',
                                  'range': [0, self.end-self.mid]}]}

    def raw(self, title, revision):
        return self.pages[title]

    def history(self, cid, a, b, decision):
        candidate = {'url': self.doc.url, 'revision': self.doc.revision,
                     'sha256': self.doc.digest, 'text_sha256': sha(self.text[a:b].encode()),
                     'char_start': a, 'char_end': b}
        result = {'id': cid, 'review_packet': {'candidates': [candidate]}}
        packet = build_packet([(self.c.cases[cid], result)], self.raw, self.audit.document,
                              distinct_occurrences=True)
        digest = fingerprint(packet)
        with self.c.db:
            self.c.db.execute('INSERT INTO campaign_packets_v1 VALUES (?,?,?,NULL)',
                              (digest, encode(packet), 'open'))
            self.c._append(cid, AWAITING, {'packet_sha256': digest})
        reply = {'schema': REPLY, 'packet_sha256': digest, 'reviewer': 'test',
                 'decisions': [{'id': 'q1', 'decision': decision,
                                'candidate': 'p1' if decision == 'complete_equivalent' else None,
                                'ranges': [[0, b-a]] if decision == 'complete_equivalent' else []}]}
        self.c.apply(reply, self.raw, self.audit)
        return digest

    def test_prepare_and_import_new_proof_without_mutating_old_receipt(self):
        old = self.c.db.execute('SELECT digest,packet,state,reply FROM campaign_packets_v1 ORDER BY digest').fetchall()
        self.assertEqual(self.c.latest('1')[0], INSUFFICIENT)
        self.assertEqual(self.c.latest('2')[0], REVIEWED)
        preview = prepare(self.c, self.plan)
        self.assertFalse(preview['prepared'])
        self.assertEqual(self.c.latest('1')[0], INSUFFICIENT)
        result = prepare(self.c, self.plan, write=True)
        self.assertEqual(result['packet_sha256'], preview['packet_sha256'])
        self.assertEqual(result['network_requests'], 0)
        digest, packet = self.c.open_packet()
        self.assertEqual(digest, result['packet_sha256'])
        text = self.text[self.mid:self.end]
        self.assertEqual(review_view(packet)['passages']['p1']['text'], text)
        self.assertEqual(packet['targets']['p1']['char_start'], self.mid)
        self.assertEqual(packet['targets']['p1']['char_end'], self.end)
        self.assertEqual(packet['campaign_reuse']['q1']['donor_packet'], self.donor)
        index, _, _ = build_bundle([(digest, packet)], 'a'*64)
        self.assertEqual(index['occurrences'], 1)
        self.assertEqual(self.c.latest('1')[0], AWAITING)
        with self.assertRaisesRegex(ValueError, 'reuse_open_review_exists'):
            prepare(self.c, self.plan, write=True)
        reply = {'schema': REPLY, 'packet_sha256': digest, 'reviewer': 'test',
                 'decisions': [{'id': 'q1', 'decision': 'complete_equivalent',
                                'candidate': 'p1', 'ranges': [[0, len(text)]]}]}
        self.assertEqual(self.c.apply(reply, self.raw, self.audit)['recorded'], 1)
        self.assertEqual(self.c.latest('1')[0], REVIEWED)
        self.assertEqual(self.c.latest('2')[0], REVIEWED)
        self.assertEqual(self.c.apply(reply, self.raw, self.audit)['status'], 'already_applied')
        rows = self.c.db.execute('SELECT digest,packet,state,reply FROM campaign_packets_v1 WHERE digest != ? ORDER BY digest', (digest,)).fetchall()
        self.assertEqual(rows, old)
        with self.assertRaisesRegex(ValueError, 'reuse_occurrence_not_current_abstention'):
            prepare(self.c, self.plan, write=True)

    def test_reject_bad_offsets_and_inventory(self):
        for bounds in ([-1, 1], [0, 999], [False, 2], [2, 2]):
            plan = deepcopy(self.plan)
            plan['entries'][0]['range'] = bounds
            with self.assertRaisesRegex(ValueError, 'reuse_invalid_range'):
                prepare(self.c, plan, write=True)
        with self.assertRaisesRegex(ValueError, 'reuse_invalid_plan'):
            prepare(self.c, {**self.plan, 'inventory_sha256': 'b'*64}, write=True)
        self.assertIsNone(self.c.open_packet())

    def test_reject_earlier_reviewed_subspan(self):
        # Create an earlier genuine offer for the same source and a later
        # abstention on a narrower window. All history, not just the most
        # recent packet, must be considered.
        self.history('1', self.mid, self.end, 'uncertain')
        latest = self.history('1', 0, self.mid-1, 'uncertain')
        plan = deepcopy(self.plan)
        plan['entries'][0]['packet'] = latest
        with self.assertRaisesRegex(ValueError, 'reuse_passage_already_examined'):
            prepare(self.c, plan, write=True)
        self.assertIsNone(self.c.open_packet())

    def test_explicit_skip_examined_preserves_other_candidate(self):
        self.history('1', self.mid, self.end, 'uncertain')
        latest = self.history('1', 0, self.mid-1, 'uncertain')
        plan = deepcopy(self.plan)
        plan['entries'][0]['packet'] = latest
        empty = prepare(self.c, plan, write=True, skip_examined=True)
        self.assertEqual(empty['occurrences'], 0)
        self.assertEqual(empty['skipped_examined'], [
            {'case_id': '1', 'reason': 'reuse_passage_already_examined'}])
        self.assertFalse(empty['prepared'])
        self.assertIsNone(self.c.open_packet())
        third = self.history('3', 0, self.mid, 'uncertain')
        plan['entries'].append({**plan['entries'][0], 'packet': third})
        ready = prepare(self.c, plan, write=True, skip_examined=True)
        self.assertEqual(ready['occurrences'], 1)
        self.assertEqual(ready['skipped_examined'], empty['skipped_examined'])
        self.assertEqual(self.c.latest('1')[0], INSUFFICIENT)
        self.assertEqual(self.c.latest('2')[0], REVIEWED)
        self.assertEqual(self.c.latest('3')[0], AWAITING)

    def test_reject_document_change(self):
        original = self.doc
        self.doc = document(digest=sha(b'changed'), blocks=original.blocks)
        changed = self.history('2', self.mid, len(self.text)-1, 'uncertain')
        plan = deepcopy(self.plan)
        plan['entries'][0]['donor_packet'] = changed
        with self.assertRaisesRegex(ValueError, 'reuse_document_not_previously_authorized'):
            prepare(self.c, plan, write=True)
        self.assertIsNone(self.c.open_packet())

    def test_reject_tampered_historical_packet(self):
        body = self.c.db.execute('SELECT packet FROM campaign_packets_v1 WHERE digest=?',
                                 (self.donor,)).fetchone()[0]
        packet = json.loads(body)
        packet['payload']['passages']['p1']['text'] += ' changed'
        with self.c.db:
            self.c.db.execute('UPDATE campaign_packets_v1 SET packet=? WHERE digest=?',
                              (encode(packet), self.donor))
        with self.assertRaisesRegex(ValueError, 'reuse_invalid_historical_packet'):
            prepare(self.c, self.plan, write=True)


if __name__ == '__main__':
    unittest.main()
