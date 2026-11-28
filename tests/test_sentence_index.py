import importlib.util
import unittest
from resolver import Document,sha
from alignment import text_and_starts
from sentence_index import SentenceVectorIndex

class Encoder:
    model_id='synthetic-test-only'
    def __init__(self):self.calls=[]
    def encode(self,items):
        self.calls.append(list(items))
        return [[0.,1.] if 'distractor' in t.lower() else [1.,0.] for t in items]

@unittest.skipUnless(importlib.util.find_spec('numpy'),'numpy is optional')
class SentenceIndexTests(unittest.TestCase):
    def test_exact_source_and_reuses_document_across_quotes(self):
        e=Encoder();idx=SentenceVectorIndex(e)
        d=Document('test',['A distractor unrelated to the question.','The workers pursue their common interests.'],'digest')
        a=idx.rank('Les ouvriers poursuivent leurs intérêts communs.',[d])
        b=idx.rank('Les travailleurs défendent leurs intérêts.',[d])
        self.assertEqual(a['candidates'][0]['text'],d.blocks[1])
        self.assertEqual(idx.builds,1);self.assertEqual(idx.hits,1)
        self.assertEqual(len(e.calls),3) # query, document sentences, next query
        for r in a['candidates']+b['candidates']:
            text,_=text_and_starts(d)
            self.assertEqual(r['text'],text[r['char_start']:r['char_end']])
            self.assertFalse(r['boundary_verified'])
            self.assertFalse(r['auto_publish'])

    def test_long_passage_not_capped_at_six_sentences(self):
        e=Encoder();idx=SentenceVectorIndex(e)
        p=' '.join('These workers defend their common interests.' for _ in range(10))
        d=Document('test',[p],'digest')
        q=' '.join('Les ouvriers défendent leurs intérêts communs.' for _ in range(10))
        self.assertEqual(idx.rank(q,[d])['candidates'][0]['text'],p)

    def test_window_budget_preflight_without_encoding(self):
        e=Encoder();idx=SentenceVectorIndex(e)
        d=Document('test',['A long enough sentence.']*10,'digest')
        r=idx.rank('Une phrase assez longue.',[d],max_windows=1)
        self.assertEqual(r['status'],'window_budget_exceeded')
        self.assertFalse(e.calls);self.assertFalse(r['candidates'])

    def test_cache_identity_is_extracted_text_not_html_hash(self):
        e=Encoder();idx=SentenceVectorIndex(e)
        a=Document('first',['A sentence sufficiently long.'],'old')
        b=Document('mirror',['A sentence sufficiently long.'],'new')
        idx.rank('Une phrase assez longue.',[a])
        idx.rank('Une phrase assez longue.',[b])
        self.assertEqual(idx.builds,1)
        b.blocks=['Different source text now.']
        idx.rank('Une phrase assez longue.',[b])
        self.assertEqual(idx.builds,2)

    def test_zero_vectors_rejected(self):
        e=Encoder();e.encode=lambda texts:[[0.,0.] for t in texts]
        with self.assertRaises(ValueError):
            SentenceVectorIndex(e).rank('Une phrase assez longue.',[Document('x',['Some words are here.'],'d')])
