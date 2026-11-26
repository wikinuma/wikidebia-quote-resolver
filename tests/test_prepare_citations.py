from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from citation_registry import fingerprint, source_identity
from alignment import text_and_starts
from resolver import sha
from grouped_review import review_view
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


    def test_requeue_only_legacy_abstentions_from_saved_candidates(self):
        from unittest.mock import patch
        from grouped_review import build_packet
        self.stage('1','2','3')
        # Reproduce a packet from the previous code while keeping all
        # original result evidence in campaign_events_v1.
        def legacy(*args, **kwargs):
            kwargs['expanded_context'] = False
            return build_packet(*args, **kwargs)
        with patch('prepare_citations.build_packet', side_effect=legacy):
            digest, packet = self.c.prepare(self.raw, self.audit, limit=2)
        rv = self.reply(digest, packet)
        rv['decisions'][0].update(decision='uncertain',candidate=None,ranges=[])
        self.c.apply(rv, self.raw, self.audit)
        self.assertEqual(self.c.latest('1')[0], INSUFFICIENT)
        self.assertEqual(self.c.latest('2')[0], REVIEWED)
        self.assertEqual(self.c.requeue_legacy_abstentions(), {'requeued':1})
        self.assertEqual(self.c.latest('1')[0], CANDIDATES)
        self.assertEqual(self.c.latest('2')[0], REVIEWED)
        self.assertEqual(self.c.requeue_legacy_abstentions(), {})
        digest, packet = self.c.prepare(self.raw, self.audit)
        self.assertIn('context_stats', packet['payload'])
        self.c.apply(self.reply(digest, packet, 'uncertain'), self.raw, self.audit)
        self.assertEqual(self.c.requeue_legacy_abstentions(), {})


    def test_multi_packet_resume_and_independent_import(self):
        self.stage('1','2','3')
        pairs = self.c.prepare_many(self.raw,self.audit,packets=3,limit=1)
        self.assertEqual(len(pairs),3)
        self.assertEqual(self.c.summary()['states'][AWAITING],3)
        self.assertEqual(self.c.prepare_many(self.raw,self.audit,packets=3),pairs)
        with WorkLibrary(self.lib.path) as lib:
            c = Campaign(lib,self.inv)
            self.assertEqual(c.prepare_many(self.raw,self.audit),pairs)
            with self.assertRaises(ValueError):
                c.cancel_review('ambiguous cancellation')
            replies = [self.reply(d,p,'uncertain') for d,p in pairs]
            bad = dict(replies[1],decisions=[])
            with self.assertRaises(ValueError):
                c.apply_many([replies[0],replies[0]],self.raw,self.audit)
            self.assertEqual(c.summary()['states'][AWAITING],3)
            with self.assertRaises(ValueError):
                c.apply_many([replies[0],dict(replies[1],packet_sha256='0'*64)],
                             self.raw,self.audit)
            self.assertEqual(c.summary()['states'][AWAITING],3)
            out = c.apply_many([replies[0],bad,replies[2]],self.raw,self.audit)
            self.assertEqual(out['statuses'],{'applied':2,'rejected':1})
            self.assertEqual(c.summary()['states'][AWAITING],1)
            self.assertEqual(c.prepare_many(self.raw,self.audit),[pairs[1]])
            out = c.apply_many(replies,self.raw,self.audit)
            self.assertEqual(out['statuses'],{'already_applied':2,'applied':1})
            self.assertEqual(c.summary()['states'][AWAITING],0)
            self.assertEqual(c.summary()['states'][INSUFFICIENT],3)

    def test_multi_wave_stops_when_audit_budget_used(self):
        self.stage('1')
        self.audit.client = SimpleNamespace(requests=1,budget=1)
        self.assertEqual(self.c.prepare_many(self.raw,self.audit),[])
        self.assertEqual(self.c.latest('1')[0],CANDIDATES)

    def test_cancel_one_of_multiple_packets(self):
        self.stage('1','2')
        pairs = self.c.prepare_many(self.raw,self.audit,limit=1)
        self.c.cancel_review('stale',pairs[1][0])
        self.assertEqual(list(self.c.open_packets()),[pairs[0]])
        self.assertEqual(self.c.latest('2')[0],RETRY)
        self.assertEqual(self.c.latest('1')[0],AWAITING)

    def test_progressive_review_escalates_only_abstentions_and_preserves_evidence(self):
        # Separate occurrences with different quotations; use a deliberately
        # synthetic fixture to exercise lifecycle, not linguistic correctness.
        self.stage('1','3')
        saved = self.c.latest('3')[1]
        digest, packet = self.c.prepare(self.raw,self.audit,progressive=True)
        self.assertEqual(packet['payload']['progressive_review']['stage'],'compact')
        self.assertIn('Abstentions automatically',review_view(packet)['instructions'])
        reply = self.reply(digest,packet)
        for row in reply['decisions']:
            if packet['sources'][row['id']]['occurrences'][0]['case_id'] == '3':
                row.update(decision='uncertain',candidate=None,ranges=[])
            else:
                row['ranges'] = [[0,packet['payload']['passages'][row['candidate']]['length']]]
        out = self.c.apply(reply,self.raw,self.audit)
        self.assertEqual(out['requeued_for_expanded_review'],1)
        self.assertEqual(self.c.latest('1')[0],REVIEWED)
        self.assertEqual(self.c.latest('3')[0],CANDIDATES)
        evidence = self.c.latest('3')[1]
        self.assertEqual(evidence['result'],saved['result'])
        self.assertEqual(evidence['context_stage'],'expanded_fallback')
        self.assertEqual(evidence['packet_sha256'],digest)
        self.assertEqual(self.c.summary()['expanded_fallback_pending'],1)
        self.assertEqual(self.c.apply(reply,self.raw,self.audit)['status'],'already_applied')
        self.stage('2')
        # Even after restarting and without the progressive option, an
        # escalated occurrence gets a wide packet before untouched cases.
        with WorkLibrary(self.lib.path) as lib:
            c = Campaign(lib,self.inv)
            wide_digest, wide = c.prepare(self.raw,self.audit,limit=1)
            self.assertEqual(wide['payload']['progressive_review']['stage'],'expanded_fallback')
            self.assertEqual([o['case_id'] for source in wide['sources'].values()
                              for o in source['occurrences']],['3'])
            self.assertEqual(c.prepare(self.raw,self.audit,progressive=True),(wide_digest,wide))
            out = c.apply(self.reply(wide_digest,wide,'no_equivalent'),self.raw,self.audit)
            self.assertEqual(out['requeued_for_expanded_review'],0)
            self.assertEqual(c.latest('3')[0],INSUFFICIENT)
            self.assertEqual(c.requeue_legacy_abstentions(),{})
            self.assertEqual(c.latest('1')[0],REVIEWED)

    def test_progressive_shortlist_then_full_saved_candidates(self):
        # One candidate from each of three separate positions, far enough
        # apart to remain distinct even after window expansion.
        self.doc = document(blocks=['word '*900,'second '*900,'third '*900])
        text, starts = text_and_starts(self.doc)
        candidates = []
        for start in starts:
            snippet = text[start:start+40]
            candidates.append({'url':self.doc.url,'revision':self.doc.revision,
                               'sha256':self.doc.digest,'char_start':start,
                               'char_end':start+40,'text_sha256':sha(snippet.encode())})
        self.audit.document = lambda *_: self.doc
        self.c.record('1',CANDIDATES,{'result':{'id':'1','candidates':candidates}})
        digest, packet = self.c.prepare(self.raw,self.audit,progressive=True)
        source = next(iter(packet['sources'].values()))
        self.assertEqual(sum(map(len,source['seed_ranges'].values())),1)
        self.assertEqual(self.c.apply(self.reply(digest,packet,'no_equivalent'),
                                      self.raw,self.audit)['requeued_for_expanded_review'],1)
        wide_digest, wide = self.c.prepare(self.raw,self.audit,progressive=True)
        source = next(iter(wide['sources'].values()))
        self.assertEqual(sum(map(len,source['seed_ranges'].values())),3)
        self.c.apply(self.reply(wide_digest,wide,'uncertain'),self.raw,self.audit)
        self.assertEqual(self.c.latest('1')[0],INSUFFICIENT)
        self.assertIsNone(self.c.prepare(self.raw,self.audit,progressive=True))

    def test_progressive_rejects_changed_saved_retrieval_evidence(self):
        self.stage('1')
        digest,packet = self.c.prepare(self.raw,self.audit,progressive=True)
        with self.lib.db:
            self.lib.db.execute("UPDATE campaign_events_v1 SET evidence=? WHERE occurrence=? AND state=?",
                                ('{}','1',CANDIDATES))
        with self.assertRaisesRegex(ValueError,'compact_candidate_evidence_changed'):
            self.c.apply(self.reply(digest,packet,'uncertain'),self.raw,self.audit)
        self.assertEqual(self.c.latest('1')[0],AWAITING)

    def test_progressive_bulk_abstentions_resume_as_wide_wave(self):
        self.stage('1','3')
        pairs = self.c.prepare_many(self.raw,self.audit,limit=1,progressive=True)
        replies = [self.reply(d,p,'uncertain') for d,p in pairs]
        self.assertEqual(self.c.apply_many(replies,self.raw,self.audit)['requeued_for_expanded_review'],2)
        self.assertEqual(self.c.apply_many(replies,self.raw,self.audit)['requeued_for_expanded_review'],0)
        wide = self.c.prepare_many(self.raw,self.audit,limit=1,progressive=True)
        self.assertEqual(len(wide),2)
        self.assertTrue(all(p['payload']['progressive_review']['stage']=='expanded_fallback'
                            for _,p in wide))
        self.assertEqual(self.c.apply_many([self.reply(d,p,'uncertain') for d,p in wide],
                                          self.raw,self.audit)['requeued_for_expanded_review'],0)
        self.assertEqual(self.c.summary()['states'][INSUFFICIENT],2)
