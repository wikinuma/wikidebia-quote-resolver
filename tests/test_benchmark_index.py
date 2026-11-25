import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from benchmark_index import aggregate, case_metrics, one_case


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
