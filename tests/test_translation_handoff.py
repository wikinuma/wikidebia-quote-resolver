from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from citation_registry import CitationRegistry, fingerprint, source_identity
from grouped_review import REPLY
from navigation import templates
from prepared_resolution import CANDIDATES, REVIEWED, RETRY, INSUFFICIENT
from prepare_citations import Campaign
from test_citation_registry import RAW, QUOTE, ENGLISH, FR, EN, ref, candidate, document, review
from translation_handoff import (REQUEST, aggregate, apply_handoff_reply,
                                  prepare_handoff, validate_request)
from work_library import WorkLibrary


def locked(raw=RAW, cid='A0001-C001', title='Fixture', revision=7):
    t = list(templates(raw, {'citation'}))[0]
    return {'id': cid, 'wiki_title': title, 'revision': revision,
            'source_template': str(t.name).strip(),
            'source_parameters': [{'name': str(p.name), 'value': str(p.value)}
                                  for p in t.params]}


def request(*rows):
    return {'schema': REQUEST, 'source_lock_sha256': 'a'*64, 'items': list(rows)}


class TranslationHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lib = WorkLibrary(Path(self.tmp.name)/'works.sqlite3')
        self.addCleanup(self.lib.db.close)
        self.registry = CitationRegistry(self.lib.db)
        self.doc = document()
        self.pages = {('Fixture', 7): RAW}
        self.audit = SimpleNamespace(document=lambda url, lang: self.doc, publication={})
        self.calls = []
        self.retrieval = SimpleNamespace(client=SimpleNamespace(events=[]), retrieve=self.retrieve)

    def raw(self, title, revision):
        return self.pages[(title, revision)]

    def retrieve(self, case):
        self.calls.append(case['id'])
        return {'id': case['id'], 'status': 'candidate_review_required',
                'review_packet': {'candidates': [candidate(self.doc)]}}

    def handoff(self, req, **kwargs):
        return prepare_handoff(req, self.registry, self.retrieval, self.raw, self.audit, **kwargs)

    def reply(self, handoff, decision='complete_equivalent'):
        entry = handoff['packets'][0]
        packet = entry['packet']
        decisions = []
        for q, source in packet['sources'].items():
            pid = source['candidates'][0]
            length = packet['payload']['passages'][pid]['length']
            decisions.append({'id': q, 'decision': decision,
                              'candidate': pid if decision == 'complete_equivalent' else None,
                              'ranges': [[0,length]] if decision == 'complete_equivalent' else []})
        return {'schema': REPLY, 'packet_sha256': entry['packet_sha256'],
                'reviewer': 'synthetic lifecycle test', 'decisions': decisions}

    def test_campaign_receipt_is_reused_by_translation_without_search(self):
        r = ref(RAW,7)
        identity = source_identity(RAW,r)
        case = {'id':'campaign-id','wiki_title':'Fixture','citation_ref':r,
                'source_key':identity['source_key'],'mia_url':identity['mia_url']}
        inv = {'cases':[case],'pages':[],'manifest_sha256':'b'*64}
        campaign = Campaign(self.lib,inv)
        self.assertEqual(campaign.search_one(case,self.retrieval,self.raw,self.audit),CANDIDATES)
        digest, packet = campaign.prepare(self.raw,self.audit)
        reply = self.reply({'packets':[{'packet_sha256':digest,'packet':packet}]})
        campaign.apply(reply,self.raw,self.audit)
        before = campaign.summary()
        self.calls.clear()
        item = locked()
        item['source_parameters'].reverse()
        result = self.handoff(request(item))
        self.assertEqual(self.calls,[])
        self.assertEqual(result['rows'][0]['state'],REVIEWED)
        self.assertEqual(result['rows'][0]['published_passage']['fragments'][0]['text'],ENGLISH)
        self.assertFalse(result['rows'][0]['published_passage']['auto_publish'])
        self.assertEqual(result['rows'][0]['published_passage']['rights'],'unreviewed')
        self.assertEqual(result['packets'],[])
        self.assertEqual(campaign.summary(),before)
        self.assertNotIn(ENGLISH,str(aggregate(result)))
        self.assertNotIn(QUOTE,str(aggregate(result)))

    def test_on_demand_miss_review_and_registry_reuse(self):
        req = request(locked())
        handoff = self.handoff(req)
        self.assertEqual(self.calls,['A0001-C001'])
        self.assertEqual(handoff['rows'][0]['state'],CANDIDATES)
        self.assertNotIn('published_passage',handoff['rows'][0])
        entry = handoff['packets'][0]
        self.assertEqual(entry['packet']['translation_request_sha256'],fingerprint(req))
        self.assertEqual(entry['packet']['source_lock_sha256'],req['source_lock_sha256'])
        self.assertEqual(handoff['rows'][0]['packet_sha256'],entry['packet_sha256'])
        reply = self.reply(handoff)
        with self.assertRaisesRegex(ValueError,'handoff_identity_mismatch'):
            apply_handoff_reply(handoff,'0'*64,reply,self.registry,self.raw,self.audit)
        for _ in range(2):
            self.assertEqual(apply_handoff_reply(handoff,fingerprint(handoff),reply,
                                                self.registry,self.raw,self.audit)['recorded'],1)
        self.assertEqual(self.lib.db.execute('SELECT count(*) FROM prepared_citations_v1').fetchone()[0],1)
        self.calls.clear()
        result = self.handoff(req,search_missing=False)
        self.assertEqual(result['rows'][0]['state'],REVIEWED)
        self.assertEqual(self.calls,[])

    def test_non_mia_never_loads_page_or_searches_even_without_revision(self):
        item = locked(RAW.replace(FR,'https://example.org/article'),revision=None)
        result = self.handoff(request(item))
        self.assertEqual(result['rows'][0]['state'],'ai_translation_required')
        self.assertEqual(self.calls,[])
        self.assertEqual(result['packets'],[])

    def test_unbound_changed_or_ambiguous_citations_not_retrieved(self):
        changed = locked(RAW.replace(QUOTE,'Changed quotation'))
        unpinned = locked(cid='A0002-C001',revision=None)
        result = self.handoff(request(changed,unpinned))
        self.assertEqual([r['reason'] for r in result['rows']],
                         ['locked_citation_not_found','pinned_source_required'])
        self.assertEqual(self.calls,[])
        self.pages[('Fixture',7)] = RAW+'\n'+RAW
        row = self.handoff(request(locked()))['rows'][0]
        self.assertEqual(row['reason'],'ambiguous_locked_citation')
        self.assertEqual(self.calls,[])

    def test_duplicate_raw_occurrence_not_silently_assigned_to_two_ids(self):
        req = request(locked(),locked(cid='A0002-C001'))
        result = self.handoff(req)
        self.assertEqual([r['reason'] for r in result['rows']],
                         ['duplicate_pinned_occurrence']*2)
        self.assertEqual(self.calls,[])

    def test_same_content_on_distinct_pages_keeps_occurrences_separate(self):
        self.pages[('Another',8)] = RAW
        result = self.handoff(request(locked(),locked(cid='A0002-C001',title='Another',revision=8)))
        self.assertEqual(self.calls,['A0001-C001','A0002-C001'])
        self.assertEqual(len(result['packets']),1)
        sources = result['packets'][0]['packet']['sources']
        self.assertEqual(len(sources),2)
        self.assertEqual(len({s['source_key'] for s in sources.values()}),1)
        self.assertEqual({s['occurrences'][0]['case_id'] for s in sources.values()},
                         {'A0001-C001','A0002-C001'})

    def test_changed_document_and_lookup_only_miss_never_supply_old_text(self):
        req = request(locked())
        miss = self.handoff(req,search_missing=False)['rows'][0]
        self.assertEqual(miss['state'],INSUFFICIENT)
        self.assertEqual(self.calls,[])
        r = ref(RAW,7)
        cand = candidate(self.doc)
        self.registry.record(RAW,r,self.doc,cand,review(RAW,r,self.doc,cand))
        self.doc = document(digest='c'*64)
        row = self.handoff(req,search_missing=False)['rows'][0]
        self.assertEqual(row['state'],RETRY)
        self.assertNotIn('published_passage',row)
        self.assertEqual(self.calls,[])

    def test_request_and_packet_integrity(self):
        bad = request(locked(),locked())
        with self.assertRaisesRegex(ValueError,'invalid_or_duplicate_translation_occurrence'):
            validate_request(bad)
        req = request(locked())
        result = self.handoff(req)
        reply = self.reply(result)
        result['source_lock_sha256'] = 'b'*64
        with self.assertRaisesRegex(ValueError,'packet_binding_mismatch'):
            apply_handoff_reply(result,fingerprint(result),reply,
                                self.registry,self.raw,self.audit)
