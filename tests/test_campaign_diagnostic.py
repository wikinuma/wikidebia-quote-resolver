import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from campaign_diagnostic import fingerprint, projection, split, encode, triage_summary


class DiagnosticTest(unittest.TestCase):
    def test_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            mia = 'https://www.marxists.org/francais/example.htm'
            en = 'https://www.marxists.org/archive/example.htm'
            cases = [{'id':str(i), 'wiki_title':'Argument '+str(i), 'mia_url':mia,
                      'source_key':'a'*64, 'citation_ref':{'quote_sha256':'b'*64}} for i in range(3)]
            inv = {'schema':'wikidebia-mia-inventory-1','cases':cases,'pages':[{'cases':cases}]}
            inv['manifest_sha256'] = fingerprint(inv)
            (state/'inventory.json').write_bytes(encode(inv))
            path = state/'works.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE campaign_events_v1(seq INTEGER PRIMARY KEY,occurrence,state,evidence,at)')
                db.execute('CREATE TABLE campaign_packets_v1(digest,packet,state,reply)')
                packet = {'sources':{'q1':{'occurrences':[{'case_id':'0'}],'candidates':['p1']}},
                          'targets':{'p1':{'document':{'url':en,'body_sha256':'c'*64},'char_start':3,'char_end':15,'text_sha256':'d'*64}},
                          'payload':{'groups':[{'authors':'Author','work':'Work','chapters':[{'article':'Chapter','citations':[{'id':'q1','fr':'PRIVATE_QUOTE'}]}]}], 'passages':{'p1':{'text':'PRIVATE_TARGET'}}}}
                pid = fingerprint(packet)
                db.execute('INSERT INTO campaign_packets_v1 VALUES (?,?,?,?)',(pid,encode(packet),'applied',encode({'decisions':[{'id':'q1','decision':'uncertain'}]})))
                retrieval = {'result':{'status':'chapter_candidate_review_required','navigation':{'input':{'authors':'Author','work':'Work','text':'PRIVATE_QUOTE'}},'source':{'status':'exact_in_examined_documents'},'target_attempts':[{'provider':'mia','root':en,'pages':[{'url':en,'sha256':'c'*64,'text':'PRIVATE_TARGET'}]}], 'candidates':[{'url':en,'char_start':3,'char_end':15,'text':'PRIVATE_TARGET'}]},'request_error_kinds':{}}
                events = [('0','review_pending',retrieval),('0','awaiting_review',{'packet_sha256':pid}),('0','insufficient_evidence',{'packet_sha256':pid,'decision':'uncertain'}),('1','retry_documentary',{'reason':'HTTPError','detail':'PRIVATE_SECRET'}),('2','reviewed_receipt_rights_unreviewed',{'receipt_sha256':'e'*64})]
                for cid,status,e in events:
                    db.execute('INSERT INTO campaign_events_v1(occurrence,state,evidence,at) VALUES (?,?,?,0)',(cid,status,encode(e)))
            before = path.read_bytes()
            report = projection(state, inv['manifest_sha256'])
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(len(report['rows']),2)
            self.assertEqual(report['states']['reviewed_receipt_rights_unreviewed'],1)
            self.assertEqual(report['rows'][0]['events'][-1]['review']['decision'],'uncertain')
            manifest, files = split(report)
            self.assertEqual(manifest['unresolved_occurrences'],2)
            self.assertEqual(manifest['triage']['unresolved_occurrences'],2)
            self.assertEqual(manifest['triage']['by_signal'],{'documentary_retry':1,'review_abstention':1})
            self.assertEqual(next(r for r in manifest['triage']['groups'] if r['signal']=='review_abstention')['last_source_status'], 'exact_in_examined_documents')
            self.assertNotIn(b'PRIVATE_',b''.join(files.values()))
            self.assertEqual(split(projection(state)),(manifest,files))
            with self.assertRaisesRegex(ValueError,'unexpected_inventory'):
                projection(state,'wrong')
            with sqlite3.connect(path) as db:
                db.execute('UPDATE campaign_packets_v1 SET packet=?',(encode({}),))
            with self.assertRaisesRegex(ValueError,'packet_digest_mismatch'):
                projection(state)

    def test_triage_uses_latest_event_and_does_not_infer_absence(self):
        url = 'https://www.marxists.org/francais/example.htm'
        prior = {'retrieval':{'status':'chapter_candidate_review_required',
                              'source_status':'exact_in_examined_documents',
                              'bibliography':{'authors':'A','work':'W'}}}
        def row(latest, previous=prior, state='insufficient_evidence'):
            return {'mia_url':url, 'state':state, 'events':[previous,latest]}
        rows = [
            row({'decision':'no_equivalent'}),
            row({'decision':'uncertain'}),
            row({'reason':'already_reviewed_without_new_evidence'}),
            row({}, {'retrieval':{'status':'no_work_route'}}),
            row({}, {'retrieval':{'status':'source_needs_review','source_status':'source_unconfirmed_in_bounded_scope'}}),
            row({}, {'retrieval':{'status':'catalog_no_candidate'}}),
            row({}, {'retrieval':{'status':'catalog_target_unavailable'}}),
            row({'reason':'HTTPError'}, state='retry_documentary'),
            row({'reason':'unknown_new_reason'}),
        ]
        got = triage_summary(rows)
        self.assertEqual(got['unresolved_occurrences'], len(rows))
        self.assertEqual(got['by_signal'], {
            'review_abstention':2, 'unchanged_review_evidence':1,
            'documentary_route_unresolved':1, 'source_not_confirmed':1,
            'no_ranked_candidate':1, 'documentary_unavailable':1,
            'documentary_retry':1, 'other_or_unclassified':1})
        self.assertEqual(got,triage_summary(list(reversed(rows))))
        self.assertIn('not_proven_causes',got['scope'])

if __name__ == '__main__':
    unittest.main()
