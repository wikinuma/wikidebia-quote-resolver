from copy import deepcopy
import unittest

from benchmark_refine import BASELINE_SHA, FROZEN_TITLE_CORRECTION, correct_frozen_titles


class FrozenTitleCorrectionTests(unittest.TestCase):
    def setUp(self):
        c = FROZEN_TITLE_CORRECTION
        self.case = {'id':c['id'], 'wiki_title':c['old_title'],
                     'citation_ref':deepcopy(c['citation_ref']),
                     'metadata':{'mia_urls':['https://www.marxists.org/francais/example.htm']}}
        self.other = {'id':'other','wiki_title':'Other page','citation_ref':{}}

    def test_only_title_changes_and_baseline_untouched(self):
        cases = [self.other,self.case]
        before = deepcopy(cases)
        updated, changes = correct_frozen_titles(cases, BASELINE_SHA)
        self.assertEqual(cases,before)
        self.assertEqual(updated,[self.other,{**self.case,
            'wiki_title':FROZEN_TITLE_CORRECTION['new_title']}])
        self.assertEqual(changes,[FROZEN_TITLE_CORRECTION])

    def test_other_baseline_and_unexpected_identity(self):
        self.assertEqual(correct_frozen_titles([self.case],'0'*64),([self.case],[]))
        for invalid in ({**self.case,'wiki_title':'Another title'},
                        {**self.case,'citation_ref':{**self.case['citation_ref'],'revision':1}}):
            with self.assertRaisesRegex(ValueError,'frozen_title_correction_identity_mismatch'):
                correct_frozen_titles([invalid],BASELINE_SHA)
        for invalid in ([self.other],[self.case,self.case]):
            with self.assertRaisesRegex(ValueError,'frozen_title_correction_case_missing_or_duplicate'):
                correct_frozen_titles(invalid,BASELINE_SHA)
