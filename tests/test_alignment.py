import unittest
import importlib.util
from unittest.mock import Mock
from tempfile import TemporaryDirectory
from pathlib import Path
import sqlite3
from resolver import Document, sha
from alignment import exact_occurrences, segments, align_sections, aligned_candidates, text_and_starts
from embedding import LocalEncoder, candidate_windows, semantic_rank
from compare import compare_methods, page_pairs, evaluation, run_case


def doc(blocks,side='fr',title=''):
    return Document('https://wikirouge.net/texts/'+side+'/'+(title or 'Fixture'),blocks,sha('\n\n'.join(blocks).encode()),title,1)


class AlignmentTests(unittest.TestCase):
    def test_offsets_and_same_paragraph_repetition(self):
        d=doc(['Avant. Une oeuvre d\u2019auteur est ici. Une oeuvre d\u2019auteur est ici.'])
        matches,trunc=exact_occurrences('une \u0153uvre d\'auteur est ici',d)
        self.assertEqual(len(matches),2); self.assertFalse(trunc)
        self.assertEqual(matches[0]['text'],matches[1]['text']); self.assertNotEqual(matches[0]['char_start'],matches[1]['char_start'])
        self.assertFalse(matches[0]['punctuation_verified'])

    def test_cross_paragraph_and_unicode(self):
        d=doc(['Avant, le cafe\u0301','est encore chaud aujourd\u2019hui.'])
        matches,trunc=exact_occurrences('le caf\u00e9 est encore chaud aujourd\'hui',d)
        self.assertEqual(matches[0]['text'],'le cafe\u0301\n\nest encore chaud aujourd\u2019hui'); self.assertEqual(matches[0]['block_end'],2)
        self.assertFalse(trunc)

    def test_ellipsis_not_exact_even_if_tokens_contiguous(self):
        d=doc(['Les oiseaux volent vers le nord.'])
        self.assertEqual(exact_occurrences('Les oiseaux [...] volent vers le nord',d),([],False))
        self.assertEqual(aligned_candidates('Les oiseaux [...] volent vers le nord',d,d)['status'],'source_fr_omission_unverified')

    def test_truncation_explicit(self):
        d=doc(['Une phrase assez longue. '*4]); hits,t=exact_occurrences('une phrase assez longue',d,limit=2)
        self.assertEqual(len(hits),2); self.assertTrue(t)

    def test_exact_substring_no_rewording(self):
        d=doc(['Intro. A longer sentence follows. Another sentence.'],'en')
        text,_=text_and_starts(d)
        for s in segments(d,'sentence'): self.assertEqual(s.text,text[s.start:s.end])
        self.assertEqual(len(segments(d,'sentence')),3)

    def test_alignment_is_monotone_and_conserves_segments(self):
        f=doc(['La richesse du pays a augmenté.','Les paysans ont acheté des terres.','La révolution a changé la société.']); e=doc(['The wealth of the country increased.','The peasants bought land.','The revolution changed society.'],'en')
        a=align_sections(f,e); self.assertEqual(a['status'],'aligned_candidates_only')
        self.assertEqual(sum(r['fr_segments'] for r in a['links']),3); self.assertEqual(sum(r['en_segments'] for r in a['links']),3)
        xs=[r['en_start'] for r in a['links'] if r['en_start'] is not None]; self.assertEqual(xs,sorted(xs))
        r=aligned_candidates(f.blocks[1],f,e); self.assertIn(e.blocks[1],r['candidates'][0]['text']); self.assertFalse(r['auto_publish'])

    def test_merge_split(self):
        f=doc(['La richesse du pays augmente. La propriété change de mains.','Les paysans travaillent ensemble.']); e=doc(['The wealth of the country grows.','Property changes hands.','The peasants work together.'],'en')
        r=aligned_candidates(f.blocks[0],f,e)
        self.assertIn(e.blocks[0],r['candidates'][0]['text']); self.assertIn(e.blocks[1],r['candidates'][0]['text'])

    def test_source_ambiguous_abstains(self):
        f=doc(['Le vent souffle très fort. Le vent souffle très fort.']); e=doc(['The wind is blowing hard.'],'en')
        r=aligned_candidates('Le vent souffle très fort',f,e); self.assertEqual(r['status'],'ambiguous_source_occurrence'); self.assertFalse(r['candidates'])

    def test_missing_source_abstains(self):
        r=aligned_candidates('Le passage ne figure pas',doc(['Texte différent.']),doc(['Different text.'],'en'))
        self.assertEqual(r['status'],'source_fr_unconfirmed'); self.assertFalse(r['candidates'])

    def test_bounded_alignment(self):
        f=doc(['Un paragraphe.']*12); e=doc(['A paragraph.']*12,'en')
        self.assertEqual(align_sections(f,e,max_cells=25)['status'],'alignment_budget_exceeded')

    def test_unverified_pairs_not_inferred(self):
        f=doc(['La richesse a augmenté ici.']); e=doc(['Wealth has increased here.'],'en')
        r=compare_methods(f.blocks[0],[f],[e],[])
        self.assertFalse(r['methods']['alignment_sentence']['candidates']); self.assertFalse(r['auto_publish'])

    def test_direct_leaf_langlink(self):
        f=doc(['Texte français assez long.'],title='Work/04/La concurrence'); e=doc(['English text.'],'en','Work/05/Competition')
        c=Mock(); c.api.return_value={'query':{'pages':[{'title':f.title,'langlinks':[{'lang':'en','title':e.title}]}]}}
        self.assertEqual(page_pairs(c,[f],[e],{f.url},[]),[(f,e,'direct_leaf_langlink')])

    def test_link_to_root_is_not_parallel_chapter(self):
        f=doc(['Texte français assez long.'],title='Work/04/La concurrence'); en=doc(['A table of contents'],'en','Work'); child=doc(['Chapter body.'],'en','Work/05/Competition')
        c=Mock(); c.api.return_value={'query':{'pages':[{'title':f.title,'langlinks':[{'lang':'en','title':en.title}]}]}}
        self.assertFalse(page_pairs(c,[f],[en,child],{f.url},[]))

    def test_incomplete_document_fetch_no_leaf_pair(self):
        f=doc(['Texte français assez long.'],title='Work/04'); e=doc(['English text.'],'en','Work')
        c=Mock(); self.assertFalse(page_pairs(c,[f],[e],{f.url},[{'kind':'subpages_truncated'}])); c.api.assert_not_called()

    def test_evaluation_gold_not_available_not_counted(self):
        self.assertEqual(evaluation([],{},'mia')['state'],'not_evaluable_for_provider')


class EmbeddingTests(unittest.TestCase):
    def test_rank_uses_source_substring_with_fake_encoder(self):
        q='La couleur de la mer'; d=doc(['A red apple. The sea is blue. It is beautiful.'],'en')
        class Fake:
            model_id='fake-not-neural'
            def encode(self,items): return [[1.,0.] if s in (q,'The sea is blue.') else [0.,1.] for s in items]
        r=semantic_rank(q,[d],Fake()); self.assertEqual(r['candidates'][0]['text'],'The sea is blue.'); self.assertFalse(r['auto_publish'])
        self.assertFalse(r['candidates'][0]['boundary_verified']); self.assertFalse(r['candidates'][0]['score_is_probability'])

    def test_window_budget_explicit(self):
        d=doc(['This is the first. This is the second. This is the third.'],'en')
        rows,t=candidate_windows('Voici une phrase française',[d],1); self.assertTrue(t); self.assertFalse(rows)

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "numpy optional; install requirements-embeddings.txt")
    def test_chunk_and_cache_fake_model(self):
        import numpy as np
        with TemporaryDirectory() as t:
            enc=LocalEncoder.__new__(LocalEncoder); enc.np=np; enc.max_tokens=8; enc.dim=2; enc.model_id='fake'; enc.stats={'cache_hits':0,'encoded_texts':0,'chunked_texts':0,'encoded_chunks':0}
            enc.model=Mock(); enc.model.tokenizer.side_effect=lambda s,**kw:{'input_ids':list(range(len(s.split())+2))}
            enc.model.encode.side_effect=lambda texts,**kw:np.array([[1.,0.] for _ in texts],dtype=np.float32)
            enc.db=sqlite3.connect(Path(t)/'vectors.db'); enc.db.execute('CREATE TABLE vectors (model TEXT,text_sha TEXT,dim INTEGER,vec BLOB,PRIMARY KEY(model,text_sha))')
            long='one two three four five six seven eight nine ten eleven twelve thirteen'
            a=enc.encode([long]); self.assertEqual(enc.stats['chunked_texts'],1); self.assertEqual(enc.stats['encoded_chunks'],3)
            self.assertTrue(all(len(s.split())+2<=8 for s in enc.model.encode.call_args.args[0]))
            b=enc.encode([long]); self.assertEqual(enc.stats['cache_hits'],1); self.assertEqual(enc.model.encode.call_count,1); np.testing.assert_array_equal(a,b)
            enc.close()


if __name__=='__main__': unittest.main()
