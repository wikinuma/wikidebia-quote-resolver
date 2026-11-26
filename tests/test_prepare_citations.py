from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from citation_registry import fingerprint, source_identity
from grouped_review import REPLY
from prepare_citations import (Campaign, AWAITING, CANDIDATES, CLOSED, INSUFFICIENT,
                               QUEUED, RETRY, REVIEWED, classify, duplicate_groups)
from test_citation_registry import RAW, QUOTE, EN, ENGLISH, ref, candidate, document
from work_library import WorkLibrary


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lib = WorkLibrary(Path(self.tmp.name)/'works.sqlite3')
        self.addCleanup(self.lib.db.close)
        self.doc = document()
        self.candidate = candidate(self.doc)
        self.pages = {'One': RAW, 'Two': RAW, 'Three': RAW.replace(QUOTE, 'Autre citation.')}
        cases = []
        for i, (title, raw) in enumerate(self.pages.items(), 1):
            r = ref(raw, i)
            source = source_identity(raw, r)
            cases.append({'id': str(i), 'wiki_title': title, 'citation_ref': r,
                          'source_key': source['source_key'], 'mia_url': source['mia_url']})
        self.inv = {'cases': cases, 'pages': [], 'manifest_sha256': 'a'*64}
        self.c = Campaign(self.lib, self.inv)
        self.audit = SimpleNamespace(document=lambda url, lang: self.doc, publication={})

    def raw(self, title, revision):
        return self.pages[title]

    def result(self, case):
        return {'id': case['id'], 'status': 'candidate_review_required',
                'review_packet': {'candidates': [self.candidate]}}

    def retrieval(self):
        return SimpleNamespace(client=SimpleNamespace(events=[]), retrieve=self.result)

    def stage(self, *ids):
        for cid in ids:
            self.assertEqual(self.c.search_one(self.c.cases[cid], self.retrieval(),
                                              self.raw, self.audit), CANDIDATES)

    def reply(self, digest, packet, decision='complete_equivalent'):
        return {'schema': REPLY, 'packet_sha256': digest, 'reviewer': 'test',
                'decisions': [{'id': q, 'decision': decision,
                               'candidate': s['candidates'][0] if decision == 'complete_equivalent' else None,
                               'ranges': [[0,len(ENGLISH)]] if decision == 'complete_equivalent' else []}
                              for q,s in packet['sources'].items()]}

    def test_occurrence_accounting_and_duplicate_anomalies(self):
        self.assertEqual(len(duplicate_groups(self.inv)), 1)
        self.stage('1','2')
        digest, packet = self.c.prepare(self.raw, self.audit)
        self.assertEqual(len(packet['sources']), 2)
        self.assertEqual(len(packet['targets']), 1)
        self.assertEqual(self.c.summary()['states'][AWAITING], 2)
        self.assertEqual(self.c.summary()['states'][QUEUED], 1)
        self.assertEqual(self.c.prepare(self.raw, self.audit), (digest, packet))
        response = self.reply(digest, packet)
        self.c.apply(response, self.raw, self.audit)
        self.assertEqual(self.c.summary()['states'][REVIEWED], 2)
        self.assertEqual(self.c.apply(response, self.raw, self.audit)['status'], 'already_applied')

    def test_persistent_restart_and_existing_receipt(self):
        self.stage('1')
        digest, packet = self.c.prepare(self.raw, self.audit)
        self.c.apply(self.reply(digest, packet), self.raw, self.audit)
        with WorkLibrary(self.lib.path) as reopened:
            c = Campaign(reopened, self.inv)
            self.assertEqual(c.latest('1')[0], REVIEWED)
            self.assertEqual([x['id'] for x in c.select({QUEUED})], ['2','3'])
            def never(case):
                self.fail('existing verified receipt should avoid retrieval')
            r = SimpleNamespace(client=SimpleNamespace(events=[]), retrieve=never)
            self.assertEqual(c.search_one(c.cases['2'], r, self.raw, self.audit), REVIEWED)
            self.assertEqual(c.summary()['states'][REVIEWED], 2)

    def test_negative_and_temporary_states_remain_distinct(self):
        self.assertEqual(classify({'status':'no_target_candidate'}), INSUFFICIENT)
        self.assertEqual(classify({'status':'no_target_candidate'},
                                  [{'status':'error'}]), RETRY)
        self.assertEqual(classify({'navigation_status':'input_unavailable'}), RETRY)
        with self.assertRaises(ValueError):
            self.c.close_negative('1', 'not searched')
        self.stage('1')
        digest, packet = self.c.prepare(self.raw, self.audit)
        self.c.apply(self.reply(digest, packet, 'no_equivalent'), self.raw, self.audit)
        self.assertEqual(self.c.latest('1')[0], INSUFFICIENT)
        self.c.close_negative('1', 'documentary scope checked by reviewer')
        self.assertEqual(self.c.latest('1')[0], CLOSED)
        self.assertFalse(self.c.latest('1')[1]['absence_of_translation_proven'])

    def test_unavailable_candidate_and_packet_size(self):
        self.stage('1')
        self.audit.document = lambda url, lang: {'status':'document_unavailable'}
        self.assertIsNone(self.c.prepare(self.raw, self.audit))
        self.assertEqual(self.c.latest('1')[0], RETRY)
        self.audit.document = lambda url, lang: self.doc
        self.stage('2')
        self.assertIsNone(self.c.prepare(self.raw, self.audit, max_payload_chars=10))
        self.assertEqual(self.c.latest('2')[0], INSUFFICIENT)

    def test_changed_occurrence_blocks_application(self):
        self.stage('1')
        digest, packet = self.c.prepare(self.raw, self.audit)
        c = Campaign(self.lib, {**self.inv, 'cases': self.inv['cases'][1:]})
        with self.assertRaisesRegex(ValueError, 'occurrence_changed'):
            c.apply(self.reply(digest, packet), self.raw, self.audit)

    def test_invalid_reply_no_transition(self):
        self.stage('1')
        digest, packet = self.c.prepare(self.raw, self.audit)
        response = self.reply(digest, packet)
        response['decisions'][0]['ranges'] = [[0,99999]]
        with self.assertRaises(ValueError):
            self.c.apply(response, self.raw, self.audit)
        self.assertEqual(self.c.latest('1')[0], AWAITING)


    def test_cancel_stale_review_and_reissue(self):
        self.stage('1')
        digest, packet = self.c.prepare(self.raw, self.audit)
        self.c.cancel_review('stale document')
        self.assertEqual(self.c.latest('1')[0], RETRY)
        with self.assertRaisesRegex(ValueError, 'not_open'):
            self.c.apply(self.reply(digest, packet), self.raw, self.audit)
        self.stage('1')
        new_digest, new_packet = self.c.prepare(self.raw, self.audit)
        self.assertNotEqual(new_digest, digest)
        self.c.apply(self.reply(new_digest, new_packet), self.raw, self.audit)
        self.assertEqual(self.c.latest('1')[0], REVIEWED)

    def test_carry_unchanged_only_on_same_page(self):
        old = {**self.inv, 'algorithm': 'same',
               'pages': [{'pageid': i, 'cases': [case], 'citation_issues': {}}
                         for i, case in enumerate(self.inv['cases'], 1)]}
        self.stage('1')
        self.c.record('2', INSUFFICIENT, {'reason': 'test'})
        new_cases = [{**case, 'id': 'new-'+case['id']} for case in old['cases']]
        new = {**old, 'cases': new_cases,
               'pages': [{'pageid': i if i != 2 else 99, 'cases': [case],
                          'citation_issues': {}}
                         for i, case in enumerate(new_cases, 1)]}
        c = Campaign(self.lib, new)
        self.assertEqual(c.carry_unchanged(old), 1)
        self.assertEqual(c.latest('new-1')[0], CANDIDATES)
        self.assertEqual(c.latest('new-1')[1]['result']['id'], 'new-1')
        self.assertEqual(c.latest('new-1')[1]['carried_from'], '1')
        self.assertEqual(c.latest('new-2')[0], QUEUED)
        self.assertEqual(c.carry_unchanged(old), 0)
