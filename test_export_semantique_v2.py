import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from export_semantique_v2 import *

def page(i,title,**kw):
    return {'ns':0,'pageid':i,'title':title,'lastrevid':i+100,'revisions':[{'revid':i+100,'timestamp':'2026-01-01T00:00:00Z'}],**kw}

class ExportTests(unittest.TestCase):
    def test_no_write_content_or_credentials(self):
        c=Client()
        try:
            for kw in ({'action':'edit'},{'action':'login'},{'action':'parse'},{'prop':'extracts'},
                       {'prop':PAGE_PROPS,'rvprop':'content'},{'prop':PAGE_PROPS,'rvprop':'ids|timestamp','rvslots':'main'},
                       {'list':'categorymembers','generator':'allpages'}):
                with self.assertRaises(ExportError): c.api('fr',**kw)
            self.assertEqual(c.calls,0)
        finally: c.close()

    def test_mia_urls_keep_mirrors_and_reject_lookalikes(self):
        good='https://marxists.info/archive/test.htm'
        self.assertEqual(mia_urls([good,'https://marxists.org.evil/a','https://name:pw@marxists.org/a',
                                   'https://marxists.org:999/a','ftp://marxists.org/a',{'url':good}]),[good])

    def test_pageinfo_pagination_and_filters(self):
        c=Mock()
        a='https://www.marxists.org/a.htm'; b='https://marxists.info/b.htm'
        c.api.side_effect=[
            {'query':{'pages':[page(1,'A',extlinks=[{'url':a},{'url':'https://example.org/no'}])]},
             'continue':{'continue':'||','elcontinue':'more'}},
            {'query':{'pages':[{'ns':0,'pageid':1,'title':'A','lastrevid':101,'extlinks':[{'*':b}],
             'langlinks':[{'lang':'fr','title':'B'}]}]}}]
        r=pageinfo(c,'en',ids=[1])[0]
        self.assertEqual(r['external_mia_urls'],sorted([a,b]))
        self.assertEqual(r['revision'],101)
        self.assertEqual(r['native_langlinks'],[{'language':'fr','title':'B'}])
        self.assertEqual(c.api.call_args_list[0].kwargs['rvprop'],'ids|timestamp')
        self.assertEqual(c.api.call_args_list[1].kwargs['elcontinue'],'more')

    def test_missing_or_changed_page_rejected(self):
        c=Mock(); c.api.return_value={'query':{'pages':[page(1,'A')]}}
        with self.assertRaisesRegex(ExportError,'missing_page'): pageinfo(c,'en',ids=[1,2])
        c.api.return_value={'query':{'pages':[page(1,'A',lastrevid=102)]}}
        with self.assertRaisesRegex(ExportError,'revision_not_confirmed'): pageinfo(c,'en',ids=[1])

    def test_multiple_external_links_not_source_certainty(self):
        r={'title':'A','external_mia_urls':['https://marxists.org/a','https://marxists.info/b']}
        got=combine([r],{'A':{'author':['A'],'mia_url':[]}})[0]
        self.assertEqual(got['mia_url_status'],'multiple_external_link_candidates')
        self.assertFalse(any(e['indexed_mia_property'] for e in got['mia_link_evidence']))
        self.assertEqual(len(got['mia_urls']),2)

    def test_ask_known_fields_and_exact_titles(self):
        c=Mock()
        def row(t): return {'fulltext':t,'namespace':0,'exists':'1','printouts':{'Author':['A'],'MIA URL':[],'BODY':['ignored']}}
        c.api.side_effect=[{'query':{'results':{'A':row('A')}},'query-continue-offset':1},{'query':{'results':{'B':row('B'),'Other':row('Other')}}}]
        r=ask(c,'en',['A','B'])
        self.assertEqual(set(r),{'A','B'}); self.assertNotIn('BODY',str(r))
        self.assertIn('[[A]] OR [[B]]',c.api.call_args_list[0].kwargs['query'])
        self.assertIn('?Author',c.api.call_args_list[0].kwargs['query'])

    def test_one_way_interlanguage_and_ambiguity(self):
        f={'language':'fr','title':'F','revision':1,'native_langlinks':[],'mia_urls':[]}
        e={'language':'en','title':'E','revision':2,'native_langlinks':[{'language':'fr','title':'F'}],'mia_urls':[]}
        pairs,unmatched=mapping([f,e,{**e,'title':'E2'}])
        self.assertEqual(len(pairs),2); self.assertTrue(all(p['ambiguous_in_export'] for p in pairs))
        self.assertFalse(pairs[0]['reciprocal']); self.assertFalse(unmatched)
        self.assertEqual(len(mapping([e])[1]),1)

    def test_checkpoint_persistence(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'db.sqlite3'; db=db_open(p)
            with db: db.execute('INSERT INTO pages VALUES(?,?,?,?)',('fr',1,'A','{"done":true}'))
            db.close(); db=db_open(p)
            self.assertEqual(db.execute('SELECT COUNT(metadata) FROM pages').fetchone()[0],1)
            db.close()

    def test_probe_optional_publication(self):
        c=Mock()
        def batch(client,lang,**kw):
            return [{'language':lang,'title':t,'revision':1,'mia_urls':['https://marxists.org'+EXPECTED[lang,t]] if (lang,t) in EXPECTED else [],
                     'semantic_properties':{'author':['A'],'date':['D'],'publication':[]},
                     'native_langlinks':[{'language':'fr','title':{'Manifesto of the Communist Party':'Manifeste du parti communiste','Anti-Dühring':'Anti-Dühring'}[t]}]
                     if lang=='en' and t in ('Manifesto of the Communist Party','Anti-Dühring') else []} for t in PROBES[lang]]
        from unittest.mock import patch
        with patch('export_semantique_v2.batch_records',side_effect=batch):
            rows,issues=probe(c); self.assertEqual(len(rows),7); self.assertFalse(issues)
