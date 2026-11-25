import unittest
from ci.run_pilot import compact, outcome


class CIRunnerTests(unittest.TestCase):
    def setUp(self):
        self.live = dict(status='review_required', offline=False, auto_publish=False, cases=[{}]*8)
        self.replay = dict(status='review_required', offline=True, requests=0, auto_publish=False, cases=[{}]*8,
                           replay_integrity=dict(config_matches=True, model_matches=True, results_match=True, response_mismatches=[]))

    def test_complete_reproducible_run(self):
        self.assertTrue(outcome(self.live, self.replay, 0, 0))

    def test_incomplete_or_missing_report_fails(self):
        self.assertFalse(outcome(self.live, self.replay, 1, 1))
        self.assertFalse(outcome({}, self.replay, 0, 0))
        self.live['status'] = 'incomplete'
        self.assertFalse(outcome(self.live, self.replay, 0, 0))

    def test_replay_mismatch_and_network_fail(self):
        self.replay['replay_integrity']['results_match'] = False
        self.assertFalse(outcome(self.live, self.replay, 0, 0))
        self.replay['replay_integrity']['results_match'] = True
        self.replay['requests'] = 1
        self.assertFalse(outcome(self.live, self.replay, 0, 0))

    def test_missing_cases_fail(self):
        self.live['cases'].pop()
        self.assertFalse(outcome(self.live, self.replay, 0, 0))

    def test_compact_omits_candidate_text(self):
        summary = compact({'cases': [{'id':'one','methods':{'lexical':{'candidates':[{'text':'DO NOT LOG'}]}}}]})
        self.assertNotIn('DO NOT LOG', str(summary))
        self.assertEqual(summary['cases'][0]['methods']['lexical']['candidates'], 1)
