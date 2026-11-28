import random
import unittest
from unittest.mock import Mock,patch
from resolver import Document,norm
from variants import prefix_distance
from source_refinement import bounded_prefix_distance,fragment_paths,long_variants
from refinement import select_source_hit
from bibliography import load_catalog,match_catalog
from auto_retrieve import Retrieval
from pathlib import Path


class SourceRefinementTests(unittest.TestCase):
    def doc(self,text,url='fr'):
        return Document(url,[text],'fixture')

    def test_banded_distance_matches_dense_for_accepted_paths(self):
        rng=random.Random(73)
        for _ in range(300):
            q=[rng.choice('abcd') for _ in range(rng.randrange(1,35))]
            s=[rng.choice('abcd') for _ in range(rng.randrange(0,40))]
            budget=rng.randrange(0,9)
            a=prefix_distance(q,s,budget)
            b=bounded_prefix_distance(q,s,budget)
            if a[0]<=budget:
                self.assertEqual(a,b)
            else:
                self.assertEqual(b[0],budget+1)

    def test_long_quote_with_minor_typo(self):
        q=' '.join('word'+str(i) for i in range(310))
        d=self.doc('Before '+q.replace('word153 ','differentword ')+' after')
        r=long_variants(q,[d])
        self.assertTrue(r['candidates'])
        self.assertEqual(r['candidates'][0]['token_edits'],1)
        self.assertFalse(r['candidates'][0]['boundary_verified'])

    def test_edge_omission_not_exact(self):
        q='Les documents anciens de la bibliothèque restent ouverts aux lecteurs chaque jour [...]'
        d=self.doc('Contexte. '+q.split(' [...]')[0]+'. Suite.')
        r=fragment_paths(q,[d])
        self.assertEqual(len(r['candidates']),1)
        h=r['candidates'][0]
        self.assertTrue(h['edge_omission_only'])
        self.assertFalse(h['boundary_verified'])
        fr,hit=select_source_hit({'status':'variant_review_required','variants':[h]},[d],'fr')
        self.assertIs(fr,d)
        self.assertEqual(hit['evidence_kind'],'fragment_variant_omission_review')

    def test_fuzzy_fragment_and_explicit_gap(self):
        a=' '.join('token'+str(i) for i in range(35))
        b=' '.join('other'+str(i) for i in range(35))
        q=a+' [...] '+b
        d=self.doc(a.replace('token15 ','changedtoken ')+' hidden words '+b)
        r=fragment_paths(q,[d])
        self.assertTrue(r['candidates'],r)
        h=r['candidates'][0]
        self.assertEqual(h['token_edits'],1)
        self.assertEqual(h['omitted_words'],2)
        self.assertEqual(len(h['fragment_ranges']),2)
        self.assertFalse(h['boundary_verified'])

    def test_no_cross_document_or_implicit_gap(self):
        a='Les documents anciens restent ouverts aux lecteurs.'
        b='Les lecteurs peuvent emprunter de nombreux ouvrages.'
        self.assertFalse(fragment_paths(a+' [...] '+b,[self.doc(a),self.doc(b,'other')])['candidates'])
        self.assertEqual(fragment_paths(a+' '+b,[self.doc(a+' '+b)])['status'],'not_applicable')

    def test_catalog_requires_actual_chapter_location(self):
        cat=load_catalog(Path(__file__).resolve().parents[1]/'bibliographic-catalog.json')
        entry=next(x for x in cat if x['target_title']=='A Contribution to the Critique of Political Economy: Preface')
        meta={'mia_urls':[entry['source_url']],'work':entry['source_title'],'authors':entry['authors']}
        self.assertEqual(match_catalog(meta,cat),entry)
        r=Retrieval(Mock(),8,cat,refined=True)
        row={'navigation':{'input':meta},'candidates':[]}
        with patch.object(r,'source',return_value={'status':'exact_in_examined_documents','occurrences':[{'url':'different-chapter'}]}):
            got=r.retrieve_catalog({'id':'x'},row,'not the preface')
        self.assertEqual(got['status'],'catalog_source_document_unconfirmed')
        r.client.get.assert_not_called()

    def test_engels_titles_are_documented_aliases(self):
        cat=load_catalog(Path(__file__).resolve().parents[1]/'bibliographic-catalog.json')
        entry=next(x for x in cat if x['source_title']=='Les syndicats (I)')
        for t in ['Les syndicats (I)','Les syndicats (II)','Les syndicats (1881)']:
            self.assertEqual(match_catalog({'mia_urls':[entry['source_url']],'article':t,'authors':'Friedrich Engels'},cat),entry)
        self.assertIsNone(match_catalog({'mia_urls':[entry['source_url']],'article':'Another article','authors':'Friedrich Engels'},cat))
