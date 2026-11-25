import unittest
from resolver import Document,norm
from source_evidence import omission_candidates,multi_anchor_candidates

def doc(text,url='https://www.marxists.org/francais/test.htm'):
    return Document(url,[text],'fixture')

class SourceEvidenceTests(unittest.TestCase):
    def test_explicit_omission_preserves_fragment_coordinates(self):
        q='Les oiseaux traversent la vallée [...] puis regagnent leurs nids chaque soir.'
        d=doc('Les oiseaux traversent la vallée malgré la pluie et le vent puis regagnent leurs nids chaque soir.')
        r=omission_candidates(q,[d])
        self.assertEqual(r['status'],'omission_review_required')
        self.assertEqual(r['candidates'][0]['omitted_words'],6)
        hit=r['candidates'][0]
        self.assertFalse(hit['boundary_verified']); self.assertFalse(r['auto_publish'])
        self.assertEqual([norm(d.blocks[0][a['char_start']:a['char_end']]) for a in hit['fragment_ranges']],
                         ['les oiseaux traversent la vallee','puis regagnent leurs nids chaque soir'])

    def test_no_implicit_omission_or_cross_document_join(self):
        q='Les oiseaux traversent la vallée puis regagnent leurs nids chaque soir.'
        self.assertEqual(omission_candidates(q,[doc(q)])['status'],'not_applicable')
        q=q.replace(' puis',' [...] puis')
        self.assertFalse(omission_candidates(q,[doc('Les oiseaux traversent la vallée','one'),doc('puis regagnent leurs nids chaque soir','two')])['candidates'])

    def test_excessive_gap_is_not_accepted(self):
        q='Les oiseaux traversent la vallée [...] puis regagnent leurs nids chaque soir.'
        self.assertFalse(omission_candidates(q,[doc('Les oiseaux traversent la vallée '+'mot '*10+'puis regagnent leurs nids chaque soir')],max_gap=5)['candidates'])

    def test_multiple_paths_report_ambiguity(self):
        q='Les oiseaux traversent la vallée [...] puis regagnent leurs nids chaque soir.'
        d=doc('Les oiseaux traversent la vallée puis regagnent leurs nids chaque soir. Les oiseaux traversent la vallée puis regagnent leurs nids chaque soir.')
        r=omission_candidates(q,[d],max_gap=0)
        self.assertEqual(r['qualifying_hits'],2)
        self.assertFalse(r['auto_publish'])

    def test_multiple_anchors_recover_changed_selector(self):
        q='Les documents anciens de la bibliothèque restent ouverts aux lecteurs chaque jour malgré les travaux importants.'
        d=doc('Contexte. Les documents anciens de la bibliothèque demeurent ouverts aux lecteurs chaque jour malgré les travaux importants. Fin.')
        r=multi_anchor_candidates(q,[d])
        self.assertTrue(r['candidates'],r)
        c=r['candidates'][0]
        self.assertEqual(c['token_edits'],1)
        self.assertIn('demeurent',c['text'])
        self.assertEqual(c['text'],d.blocks[0][c['char_start']:c['char_end']])
        self.assertFalse(c['boundary_verified'])

    def test_one_seed_not_enough(self):
        q='Les documents anciens de la bibliothèque restent ouverts aux lecteurs chaque jour malgré les travaux importants.'
        d=doc('Les documents anciens de cette autre institution ont été détruits pendant une tempête.')
        self.assertFalse(multi_anchor_candidates(q,[d])['candidates'])

    def test_edit_budget_and_omission_refusals(self):
        q='Les documents anciens de la bibliothèque restent ouverts aux lecteurs chaque jour malgré les travaux importants.'
        r=multi_anchor_candidates(q,[doc(q)],max_cells=1)
        self.assertTrue(r['truncated']); self.assertFalse(r['candidates'])
        self.assertFalse(multi_anchor_candidates(q.replace(' restent',' [...] restent'),[doc(q)])['candidates'])
