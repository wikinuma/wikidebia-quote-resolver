import unittest
from independent_mia_holdout import sample,select

class IndependentHoldoutTests(unittest.TestCase):
    def test_actual_frozen_holdout(self):
        r=sample();d=r['selection']
        self.assertEqual(d['prior_cases'],200)
        self.assertEqual(len(r['sample']),50)
        self.assertEqual(d['prior_quote_overlap'],0)
        self.assertEqual(d['prior_template_overlap'],0)
        self.assertGreaterEqual(len(d['selected_author_labels']),4)
        self.assertGreaterEqual(d['selected_work_groups'],10)
        self.assertEqual(len({c['citation_ref']['quote_sha256'] for c in r['sample']}),50)
        self.assertEqual(len({c['citation_ref']['template_sha256'] for c in r['sample']}),50)
        self.assertEqual(r,sample())

    def test_selection_independent_of_inventory_order(self):
        r=sample()['sample']
        a,_=select(r,[],5)
        b,_=select(list(reversed(r)),[],5)
        self.assertEqual(a,b)

    def test_previous_fingerprints_rejected(self):
        r=sample()['sample']
        a,_=select(r,r[:3],5)
        self.assertFalse({c['id'] for c in a}&{c['id'] for c in r[:3]})
