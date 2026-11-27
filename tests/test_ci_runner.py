from copy import deepcopy
import unittest
from ci.run_pilot import (compact, outcome, regression_outcome, EXPECTED_CASES,
                          EXPECTED_VARIANT_HASH, EXPECTED_VARIANT_NORMALIZED)


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

    def test_known_variant_is_regression_success_but_not_pilot_complete(self):
        live = deepcopy(self.live)
        live.update(status='incomplete', provider_policy='explicit_comparison',
                    api_probes={k: {'status': 'available'} for k in
                                ('wikidebats', 'wikirouge_fr', 'wikirouge_en')},
                    works={w + ':' + p: {'scope_complete': True, 'issues': []}
                           for w in ('manifesto', 'engels-condition')
                           for p in ('mia', 'wikirouge')}, cases=[])
        for case, provider in sorted(EXPECTED_CASES):
            row = dict(id=case, provider=provider, status='review_required',
                       source_status='normalized_contiguous', fr_occurrences=[{}],
                       documents={'fr': 1, 'en': 1}, scope_complete=True,
                       pair_issues=[], auto_publish=False, rights='unreviewed')
            if case == 'manifesto-preface-1883':
                row.update(status='incomplete_or_unconfirmed', source_status='unconfirmed',
                           fr_occurrences=[], source_diagnostics={'variant_search': {
                               'status': 'variant_review_required', 'auto_publish': False,
                               'rights': 'unreviewed', 'truncated': False,
                               'candidates_truncated': False, 'qualifying_hits': 1,
                               'candidates': [{'text_sha256': EXPECTED_VARIANT_HASH,
                                               'normalized_sha256': EXPECTED_VARIANT_NORMALIZED,
                                               'token_edits': 5, 'query_words': 46,
                                               'source_words': 49, 'boundary_ambiguous': False}]}})
            live['cases'].append(row)
        replay = {**deepcopy(live), 'offline': True, 'requests': 0,
                  'replay_integrity': self.replay['replay_integrity']}
        self.assertFalse(outcome(live, replay, 1, 1))
        self.assertEqual(regression_outcome(live, replay, 1, 1),
                         'expected_documentary_abstention')
        for mutate in (
            lambda r: r['cases'].pop(),
            lambda r: r['cases'][0].update(status='ranking_error'),
            lambda r: r['works']['manifesto:mia'].update(scope_complete=False),
            lambda r: r['api_probes']['wikidebats'].update(status='unavailable'),
            lambda r: next(c for c in r['cases'] if c['id'] == 'manifesto-preface-1883')[
                'source_diagnostics']['variant_search']['candidates'][0].update(token_edits=6),
            lambda r: r['replay_integrity'].update(results_match=False),
        ):
            with self.subTest(mutate=mutate):
                broken = deepcopy(replay)
                mutate(broken)
                self.assertEqual(regression_outcome(live, broken, 1, 1), 'failed')

    def test_compact_omits_candidate_text(self):
        summary = compact({'cases': [{'id':'one','methods':{'lexical':{'candidates':[{'text':'DO NOT LOG'}]}}}]})
        self.assertNotIn('DO NOT LOG', str(summary))
        self.assertEqual(summary['cases'][0]['methods']['lexical']['candidates'], 1)
