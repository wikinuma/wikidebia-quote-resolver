import unittest
from benchmark_fixtures import load_prior_cohort,load_semantic_cases

class FrozenEvaluationTests(unittest.TestCase):
    def test_fixed_inputs_and_labels(self):
        cases=load_prior_cohort()['cases']
        labels=load_semantic_cases()
        self.assertEqual(len(cases),100)
        self.assertEqual(len(labels),38)
        self.assertTrue({c['id'] for c in labels}<={c['id'] for c in cases})
        self.assertTrue(all('first' not in c and 'latest_decision' not in c for c in cases))
