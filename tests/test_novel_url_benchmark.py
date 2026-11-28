import unittest
from novel_url_benchmark import select_novel,frozen_sample,comparison
from sentence_index import rank_scoped
from resolver import Document

def case(n,url,q=None,t=None):
    return {'id':'case'+str(n),'stratum':'explicit_mia',
            'citation_ref':{'quote_sha256':q or format(n,'064x'),'template_sha256':t or format(n+100,'064x')},
            'metadata':{'authors':'Author','work':'Work','mia_urls':['https://www.marxists.org/francais/'+url+'.htm'],'quote_words':20}}

class NovelURLTests(unittest.TestCase):
    def test_frozen_real_selection(self):
        s=frozen_sample();d=s['selection']
        self.assertEqual(d['prior_cases'],150)
        self.assertEqual(d['prior_distinct_urls'],50)
        self.assertEqual(d['selected'],50)
        self.assertEqual(d['selected_distinct_urls'],14)
        self.assertEqual(d['eligible_distinct_quotes'],152)
        self.assertEqual(len({c['citation_ref']['quote_sha256'] for c in s['sample']}),50)

    def test_exclusions_and_round_robin(self):
        prior=[case(1,'old')]
        rows=[case(2,'old'),case(3,'new',q=prior[0]['citation_ref']['quote_sha256']),
              case(4,'new',t=prior[0]['citation_ref']['template_sha256']),
              case(5,'a'),case(6,'a'),case(7,'b')]
        s,d=select_novel(rows,prior,2)
        self.assertEqual({c['id'] for c in s}&{'case5','case6','case7'},{c['id'] for c in s})
        self.assertEqual(d['selected_distinct_urls'],2)
        self.assertEqual(d['eligible_distinct_quotes'],3)
        self.assertEqual((s,d),select_novel(rows[::-1],prior,2))

    def test_comparison_is_not_semantic_validation(self):
        x={'status':'ready','target':{'url':'u','revision':1,'document_sha256':'h',
                                     'actual_text_sha256':'x','char_start':0,'char_end':10}}
        y={'status':'ready','target':{**x['target'],'actual_text_sha256':'y','char_start':5,'char_end':15}}
        r=comparison(x,y)
        self.assertEqual(r['agreement'],'same_document_overlapping')
        self.assertEqual(r['character_iou'],.333333)
        self.assertIsNone(r['semantic_equivalence'])

    def test_scope_fallback_is_explicit_and_no_unfetched_docs(self):
        from unittest.mock import Mock
        a=Document('a',['text'],'a');b=Document('b',['text'],'b')
        idx=Mock()
        idx.rank.side_effect=[{'status':'segment_budget_exceeded','window_count':0,'candidates':[]},
                              {'status':'review_required','window_count':1,'candidates':[]}]
        r=rank_scoped(idx,'quote',[a,b],[{'url':'a','sha256':'a'},{'url':'invented','sha256':'x'}])
        self.assertEqual(r['scope'],'baseline_candidate_documents_only')
        self.assertTrue(r['scope_restricted'])
        self.assertEqual(idx.rank.call_args.args[1],[a])
