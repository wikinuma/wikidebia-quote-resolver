import unittest
from resolver import Document,sha
from alignment import text_and_starts
from variants import prefix_distance,source_variants,group_evidence,primary_sufficient_for_review


def doc(text,url='https://www.marxists.org/test'):
    return Document(url,[text],sha(text.encode()))


class VariantsTests(unittest.TestCase):
    def test_substring_in_very_long_paragraph(self):
        source=' '.join(['contexte']*150+['Les oiseaux de la forêt traversent rapidement la vallée chaque matin.']+['suite']*150)
        q='Les oiseaux de la forêt traversent lentement la vallée chaque matin.'
        r=source_variants(q,'oiseaux de la forêt',[doc(source)])
        self.assertEqual(r['status'],'variant_review_required')
        c=r['candidates'][0]
        self.assertEqual(c['token_edits'],1)
        self.assertEqual(c['text'],source[c['char_start']:c['char_end']])
        self.assertIn('rapidement',c['text']); self.assertNotIn('contexte',c['text'])
        self.assertFalse(r['auto_publish']); self.assertFalse(c['boundary_verified'])

    def test_insertions_and_deletions_on_both_sides(self):
        q='Les anciens documents de la bibliothèque restent ouverts aux lecteurs chaque jour.'
        text='Les documents de la bibliothèque restent toujours ouverts aux lecteurs chaque jour.'
        r=source_variants(q,'documents de la bibliothèque',[doc(text)])
        self.assertEqual(r['candidates'][0]['token_edits'],2)
        self.assertEqual(r['candidates'][0]['source_words'],r['candidates'][0]['query_words'])

    def test_no_selector_no_guess(self):
        r=source_variants('Le texte des archives est ici depuis longtemps','texte des archives',[doc('Le document des archives est ici depuis longtemps')])
        self.assertFalse(r['candidates'])

    def test_similar_selector_alone_is_not_enough(self):
        r=source_variants('Le texte des archives est ici depuis longtemps','texte des archives',[doc('Le texte des archives contient mille problèmes administratifs différents')])
        self.assertFalse(r['candidates'])

    def test_multiple_source_occurrences_are_not_collapsed(self):
        q='Le texte des archives est ici depuis très longtemps'
        text='Le texte des archives est là depuis très longtemps.'
        r=source_variants(q,'texte des archives',[doc(text+' '+text)])
        self.assertEqual(r['qualifying_hits'],2)
        self.assertNotEqual(r['candidates'][0]['char_start'],r['candidates'][1]['char_start'])

    def test_budget_does_not_claim_complete(self):
        q='Le texte des archives est ici depuis très longtemps'
        r=source_variants(q,'texte des archives',[doc((q+'. ')*3)],max_hits=1)
        self.assertTrue(r['truncated']); self.assertEqual(r['status'],'search_budget_exceeded')

    def test_explicit_omission_not_promoted(self):
        q='Le texte des archives [...] est ici depuis très longtemps'
        r=source_variants(q,'texte des archives',[doc('Le texte des archives est ici depuis très longtemps')])
        self.assertEqual(r['candidates'][0]['match'],'omission_or_variant_review')
        self.assertFalse(r['auto_publish'])

    def test_ambiguous_prefix(self):
        distance,end,ambiguous=prefix_distance(['a'],['b'],1)
        self.assertEqual(distance,1); self.assertTrue(ambiguous)

    def test_shared_archive_family_and_duplicates(self):
        rows=[{'id':'one','provider':'mia','source_status':'normalized_contiguous','methods':{'lexical':{'candidates':[{'url':'https://www.marxists.org/x','text':'The same passage.'}]}}},
              {'id':'one','provider':'wikirouge','source_status':'normalized_contiguous','methods':{'lexical':{'candidates':[{'url':'https://wikirouge.net/texts/en/X','text':'The SAME passage!'},{'url':'https://wikirouge.net/texts/en/X','text':'The same passages.'}]}}}]
        r=group_evidence(rows)['one']
        self.assertEqual(r['source_families'],['mia_related_archives'])
        m=r['methods']['lexical']
        self.assertEqual(m['candidate_references'],3); self.assertEqual(m['distinct_normalized_passages'],2)
        self.assertEqual(m['cross_provider_duplicate_groups'],1)
        self.assertEqual(len(m['groups'][0]['references']),2)
        self.assertNotIn('The same passage',str(r))

    def test_primary_policy_requires_alignment_and_unique_source(self):
        row={'provider':'mia','status':'review_required','scope_complete':True,'source_status':'normalized_contiguous','methods':{'alignment_sentence':{'candidates':[{}]}}}
        self.assertTrue(primary_sufficient_for_review(row))
        self.assertFalse(primary_sufficient_for_review({**row,'source_status':'unconfirmed'}))
        self.assertFalse(primary_sufficient_for_review({**row,'scope_complete':False}))
        self.assertFalse(primary_sufficient_for_review({**row,'methods':{'lexical':{'candidates':[{}]}}}))
