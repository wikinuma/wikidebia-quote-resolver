import json
import sqlite3
import unittest
from unittest.mock import Mock,patch

from campaign_source_probe import probe_rows, prior_result, quote_rows


class SourceProbeTests(unittest.TestCase):
    def test_latest_state_and_prior_retrieval_and_cache_error(self):
        db=sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE campaign_events_v1(seq INTEGER PRIMARY KEY,occurrence,state,evidence)')
        def add(cid,state,e):
            db.execute('INSERT INTO campaign_events_v1(occurrence,state,evidence) VALUES (?,?,?)',
                       (cid,state,json.dumps(e)))
        url='https://www.marxists.org/francais/book.htm'
        case={'id':'a','wiki_title':'Example','mia_url':url,
              'source_key':'k','citation_ref':{'revision':1,'quote_sha256':'q'}}
        result={'status':'source_needs_review','source':{
            'status':'source_unconfirmed_in_bounded_scope',
            'source_evidence_diagnostics':[{'method':'example','truncated':True,'text':'excluded'}]}}
        add('a','review_pending',{'result':result})
        add('a','insufficient_evidence',{'decision':'uncertain'})
        add('b','reviewed_receipt_rights_unreviewed',{'result':result})
        inv={'cases':[case,{**case,'id':'b'},{**case,'id':'c','mia_url':url+'x'}]}
        self.assertEqual(prior_result(db,'a'),('insufficient_evidence',result))
        self.assertIsNone(prior_result(db,'b'))
        loader=Mock(return_value='pinned raw page')
        with patch('campaign_source_probe.source_identity',return_value={'source_key':'k'}), \
             patch('campaign_source_probe.extract_citation_ref',return_value={'text':'French example'}):
            rows,_=probe_rows(db,inv,url,loader,[])
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['fr'],'French example')
        self.assertEqual(rows[0]['source_diagnostics'],[{'method':'example','truncated':True}])
        loader.assert_called_once_with('Example',1)
        with patch('campaign_source_probe.source_identity',return_value={'source_key':'wrong'}):
            rows,_=probe_rows(db,inv,url,loader,[])
        self.assertNotIn('fr',rows[0])
        self.assertEqual(rows[0]['probe_error'],'source_identity_mismatch')
        self.assertEqual(db.execute('SELECT count(*) FROM campaign_events_v1').fetchone()[0],3)

    def test_quote_only_filters_latest_pending_state_and_verifies_identity(self):
        db=sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE campaign_events_v1(seq INTEGER PRIMARY KEY,occurrence,state,evidence)')
        url='https://www.marxists.org/francais/book.htm'
        case={'id':'a','wiki_title':'Example','mia_url':url,
              'source_key':'k','citation_ref':{'revision':1,'quote_sha256':'q'}}
        inv={'cases':[case,{**case,'id':'b'},{**case,'id':'c','mia_url':url+'x'}]}
        for cid,state in [('a','insufficient_evidence'),('b','insufficient_evidence'),
                          ('b','reviewed_receipt_rights_unreviewed'),('c','retry_documentary')]:
            db.execute('INSERT INTO campaign_events_v1(occurrence,state,evidence) VALUES (?,?,?)',
                       (cid,state,'{}'))
        with patch('campaign_source_probe.source_identity',return_value={'source_key':'k'}), \
             patch('campaign_source_probe.extract_citation_ref',return_value={'text':'French example','authors':'Author'}):
            rows=quote_rows(db,inv,None,Mock(return_value='raw'))
            self.assertEqual([r['id'] for r in rows],['a','c'])
            self.assertEqual(rows[0]['fr'],'French example')
            self.assertEqual(rows[0]['authors'],'Author')
            self.assertEqual([r['id'] for r in quote_rows(db,inv,url,Mock(return_value='raw'))],['a'])
        with patch('campaign_source_probe.source_identity',return_value={'source_key':'wrong'}):
            rows=quote_rows(db,inv,url,Mock(return_value='raw'))
        self.assertNotIn('fr',rows[0])
        self.assertEqual(rows[0]['probe_error'],'source_identity_mismatch')
        self.assertEqual(db.execute('SELECT count(*) FROM campaign_events_v1').fetchone()[0],4)
