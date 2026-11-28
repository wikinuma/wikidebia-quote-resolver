import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from benchmark_index import aggregate, case_metrics, one_case, select_mia, scan_mia_pages


class PairedMetricsTests(unittest.TestCase):
    def test_distinguish_candidates_from_verification(self):
        row={'navigation':{'status':'wikirouge_route_candidate','input':{'quote_words':9}},
             'status':'candidate_review_required',
             'candidates':[{'text_sha256':'same','text_words':12}],
             'section_alignment':{'candidates':[{'text_sha256':'same','text_words':12}]}}
        metrics=case_metrics(row)
        self.assertTrue(metrics['work_candidate'])
        self.assertTrue(metrics['english_passage_candidate'])
        self.assertEqual(metrics['candidate_words_for_review'],12)
        self.assertEqual(metrics['source_words_for_review'],9)
        agg=aggregate([{'legacy':{'metrics':metrics,'result':row},'indexed':{'metrics':metrics,'result':row}}])
        self.assertIsNone(agg['indexed']['verified_exact_quotes'])
        self.assertIsNone(agg['indexed']['false_positives'])

    def test_case_budget_and_network_deltas(self):
        client=SimpleNamespace(requests=3,hits=4,network_body_bytes=100,events=[],budget=100)
        r=Mock(client=client)
        def retrieve(case):
            self.assertEqual(client.budget,8)
            client.requests+=2
            client.hits+=1
            client.network_body_bytes+=30
            client.events.append({'status':'error','error':'request budget exhausted'})
            return {'id':case['id'],'status':'unresolved','auto_publish':False}
        r.retrieve.side_effect=retrieve
        got=one_case(r,{'id':'x','wiki_title':'p','citation_ref':{},'expected':'gold'},5)
        self.assertEqual(r.retrieve.call_args.args[0],{'id':'x','wiki_title':'p','citation_ref':{}})
        self.assertEqual(client.budget,100)
        self.assertEqual(got['metrics']['network_requests'],2)
        self.assertEqual(got['metrics']['decoded_network_body_bytes'],30)
        self.assertEqual(got['metrics']['budget_errors'],1)


class MiaOnlySamplingTests(unittest.TestCase):
    def record(self,i,urls,work='Same work',words=12,title=None):
        return {'wiki_title':title or 'Page '+str(i),'revision':i+1,
                'template_index':0,'template_sha256':'t'+str(i),'quote_sha256':'q'+str(i),
                'authors':'Author','work':work,'article':'','date':'',
                'mia_urls':urls,'quote_words':words}

    def test_never_fill_with_non_mia(self):
        url='https://www.marxists.org/francais/example.htm'
        rows=[self.record(0,[url]),self.record(1,[]),self.record(2,['https://example.org/a'])]
        selected,diag=select_mia(rows,50,set(),set(),set())
        self.assertEqual(len(selected),1)
        self.assertEqual(diag['eligible_unique_quotes'],1)
        self.assertEqual(selected[0]['stratum'],'explicit_mia')

    def test_repeated_works_and_independent_priority(self):
        url='https://www.marxists.org/francais/example.htm'
        rows=[self.record(i,[url],words=3 if i==1 else 12) for i in range(5)]
        rows.append(self.record(9,[url],work='Other work',title='Development'))
        chosen,diag=select_mia(rows,5,{'development'},set(),set())
        self.assertEqual(len(chosen),5)
        self.assertEqual(diag['distinct_work_groups'],1)
        self.assertEqual(diag['development_overlap_selected'],0)
        self.assertEqual(diag['length_outside_previous_limits_selected'],1)
        chosen,diag=select_mia(rows,6,{'development'},set(),set())
        self.assertEqual(diag['development_overlap_selected'],1)
        self.assertTrue(chosen[-1]['development_overlap'])

    def test_page_link_does_not_make_unlinked_quote_eligible(self):
        import importlib.util
        if not importlib.util.find_spec('mwparserfromhell'):
            self.skipTest('navigation parser optional')
        raw='{{Citation|citation=some words|ouvrage=A|lien=https://example.org/a}}{{Citation|citation=other words|ouvrage=B|lien=https://marxists.org/a.htm}}'
        c=Mock()
        c.api.return_value={'query':{'pages':[{'ns':0,'title':'P','revisions':[{'revid':9,'slots':{'main':{'content':raw}}}]}]}}
        rows,diag=scan_mia_pages(c,{'P':{'https://www.marxists.org/a.htm'}},10)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['work'],'B')
        self.assertEqual(rows[0]['revision'],9)
