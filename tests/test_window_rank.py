import unittest
from resolver import Document
from alignment import text_and_starts
from window_rank import rank_short_windows

class WindowRankTests(unittest.TestCase):
    def test_cognate_windows_without_dictionary(self):
        q='La démocratie garantit la participation de la population.'
        text=' '.join(['A very long irrelevant sentence about the rain.']*60+['Democracy guarantees the participation of the population.'])
        d=Document('https://www.marxists.org/archive/test.htm',[text],'d')
        r=rank_short_windows(q,[d])
        self.assertTrue(r['candidates'])
        best=r['candidates'][0]
        self.assertIn('Democracy guarantees',best['text'])
        full,_=text_and_starts(d)
        self.assertEqual(best['text'],full[best['char_start']:best['char_end']])
        self.assertFalse(best['boundary_verified']); self.assertFalse(r['auto_publish'])

    def test_no_overlap_abstains(self):
        r=rank_short_windows('La démocratie garantit la participation',[Document('x',['Blue clouds covered the mountain.'],'d')])
        self.assertFalse(r['candidates'])

    def test_budget_is_visible(self):
        r=rank_short_windows('La démocratie garantit la participation',[Document('x',['Democracy encourages popular participation. Democracy guarantees broad participation.'],'d')],max_windows=1)
        self.assertTrue(r['truncated']); self.assertEqual(r['status'],'partial_window_budget')
