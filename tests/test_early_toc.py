import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace
from early_toc import eligible_roots,try_early_toc
from auto_retrieve import Retrieval
from resolver import Document

FR='https://www.marxists.org/francais/test/chapter.htm'
FROOT='https://www.marxists.org/francais/test/index.htm'
EROOT='https://www.marxists.org/archive/test/index.htm'
EN='https://www.marxists.org/archive/test/chapter.htm'
AUTHOR='Karl Marx'

def fixture():
    nav={'input':{'mia_urls':[FR]},'route_method':'local_wikirouge_snapshot',
         'author_check':'same_normalized_author_tokens',
         'work_metadata':{'fr':{'authors':AUTHOR,'mia_urls':[FROOT]},'en':{'authors':AUTHOR,'mia_urls':[EROOT]}}}
    doc=Document(FR,['French source passage.'],'f')
    en=Document(EN,['English source passage.'],'e')
    pair={'fr':{'url':FR,'position':20},'en':{'url':EN,'position':19}}
    m=Mock()
    m.match.return_value={'pairs':[pair],'fr_toc':{},'en_toc':{}}
    m.content_probe.return_value={'status':'sampled_content_support','fr_document_sha256':'f','en_document_sha256':'e'}
    m.document.return_value=en
    client=SimpleNamespace(budget=32,requests=2)
    e=SimpleNamespace(client=client,toc_matcher=m,max_pages=16,source_documents={FR:doc},
                      direct_cache=Mock(),hybrid=True)
    e.direct_cache.candidate.return_value={'candidates':[]}
    e.semantic_sidecar=lambda r,q,d:{**r,'semantic_experiment':{'candidates':[{'url':EN}]},
                                     'hybrid_review_queue':{'candidates':[{'url':EN}]}}
    return e,{'navigation':nav,'status':'original'},doc,pair

class EarlyRouteTests(unittest.TestCase):
    def test_guarded_route_and_budget_restoration(self):
        e,row,doc,pair=fixture()
        with patch('early_toc.select_source_hit',return_value=(doc,{'evidence_kind':'exact_normalized_source'})),patch('early_toc.rank',return_value=[{'url':EN}]):
            out,d=try_early_toc(e,row,'quote',{})
        self.assertEqual(d['status'],'early_chapter_candidate')
        self.assertEqual(out['target_attempts'][0]['pages'][0]['url'],EN)
        self.assertEqual(e.client.budget,32)
        self.assertEqual(row['status'],'original')
        self.assertFalse(out['toc_route_experiment']['auto_publish'])

    def test_unconfirmed_or_changed_document_falls_back(self):
        for mode in ('ambiguous','inconclusive','changed','no_semantic','inside'):
            with self.subTest(mode=mode):
                e,row,doc,pair=fixture()
                if mode=='ambiguous':e.toc_matcher.match.return_value['pairs']=[pair,pair]
                elif mode=='inconclusive':e.toc_matcher.content_probe.return_value['status']='sampled_content_inconclusive'
                elif mode=='changed':e.toc_matcher.content_probe.return_value['fr_document_sha256']='different'
                elif mode=='no_semantic':e.semantic_sidecar=lambda r,q,d:r
                else:pair['en']['position']=1
                with patch('early_toc.select_source_hit',return_value=(doc,{'evidence_kind':'exact_normalized_source'})),patch('early_toc.rank',return_value=[{'url':EN}]):
                    out,d=try_early_toc(e,row,'quote',{})
                self.assertIsNone(out)
                self.assertEqual(e.client.budget,32)
                self.assertNotIn('candidates',row)
                self.assertTrue(d['ordinary_fallback_available'])

    def test_budget_failure_isolated(self):
        e,row,doc,pair=fixture()
        def fail(*args):
            self.assertEqual(e.client.budget,6)
            raise RuntimeError('unavailable')
        e.toc_matcher.match.side_effect=fail
        with patch('early_toc.select_source_hit',return_value=(doc,{'evidence_kind':'exact_normalized_source'})):
            out,d=try_early_toc(e,row,'quote',{})
        self.assertIsNone(out)
        self.assertEqual(d['status'],'early_route_unavailable')
        self.assertEqual(e.client.budget,32)
        e.client.requests=30
        with patch('early_toc.select_source_hit',return_value=(doc,{'evidence_kind':'exact_normalized_source'})):
            self.assertEqual(try_early_toc(e,row,'quote',{})[1]['status'],'fallback_budget_reserved')
        self.assertEqual(e.toc_matcher.match.call_count,1)

    def test_non_mia_and_conflicting_author_refused(self):
        e,row,doc,pair=fixture()
        self.assertEqual(eligible_roots(row['navigation']),(FR,FROOT,EROOT))
        row['navigation']['work_metadata']['en']['authors']='Another Author'
        self.assertIsNone(eligible_roots(row['navigation']))
        row['navigation']['input']['mia_urls']=[]
        self.assertIsNone(eligible_roots(row['navigation']))

    def test_opt_in_requires_shared_client_and_encoder(self):
        with self.assertRaises(ValueError):Retrieval(Mock(),16,hybrid=True)
        with self.assertRaises(ValueError):Retrieval(Mock(),16,refined=True,semantic_index=Mock(),toc_matcher=Mock())
