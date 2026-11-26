"""Local budget exhaustion is retryable at every retrieval cache layer."""
import tempfile
import unittest
from unittest.mock import Mock, patch

from resolver import Client, RequestBudgetExceeded
from pilot_full import PilotClient
from auto_retrieve import Retrieval
from evidence_audit import Auditor
from revision_receipt import RevisionRefresher

URL='https://www.marxists.org/archive/marx/test.htm'


class BudgetRecoveryTests(unittest.TestCase):
    def test_client_exhaustion_is_not_a_url_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=PilotClient(tmp,budget=0,delay=0)
            with self.assertRaisesRegex(RequestBudgetExceeded,'request budget exhausted'):
                c.get(URL)
            self.assertFalse(c.failed_urls)
            self.assertEqual(c.requests,0)
            self.assertEqual(c.events[-1]['status'],'error')
            c.budget=1
            with patch.object(Client,'get',return_value=(b'<p>Test.</p>',URL)) as fetch:
                self.assertEqual(c.get(URL)[0],b'<p>Test.</p>')
                fetch.assert_called_once()
            self.assertEqual(c.events[-1]['status'],'ok')

    def test_other_failures_are_still_memoized(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=PilotClient(tmp,budget=1,delay=0)
            with patch.object(Client,'get',side_effect=RuntimeError('source unavailable')) as fetch:
                for _ in range(2):
                    with self.assertRaisesRegex(RuntimeError,'source unavailable'):
                        c.get(URL)
                self.assertEqual(fetch.call_count,1)
            self.assertTrue(c.events[-1]['memoized_failure'])

    def test_partial_work_is_not_reused_as_complete(self):
        for provider,collector in [('mia','collect_mia'),('wikirouge','collect_wr')]:
            with self.subTest(provider=provider):
                r=Retrieval(Mock(),16)
                incomplete=(['one page'],{'issues':[{'kind':'fetch_error','error':'RequestBudgetExceeded'}]})
                complete=(['one page','second page'],{'issues':[]})
                with patch('auto_retrieve.'+collector,side_effect=[incomplete,complete]) as collect:
                    self.assertEqual(r.work(provider,'root','en'),incomplete)
                    self.assertFalse(r.works)
                    self.assertEqual(r.work(provider,'root','en'),complete)
                    self.assertEqual(r.work(provider,'root','en'),complete)
                    self.assertEqual(collect.call_count,2)

    def test_audit_retries_when_case_budget_is_restored(self):
        c=Mock()
        c.get.side_effect=[RequestBudgetExceeded('request budget exhausted'),(b'<p>Actual text.</p>',URL)]
        a=Auditor(c)
        self.assertEqual(a.document(URL,'en')['error'],'RequestBudgetExceeded')
        self.assertFalse(a.docs)
        self.assertEqual(a.document(URL,'en').blocks,['Actual text.'])
        self.assertEqual(c.get.call_count,2)

    def test_pinned_revision_lookup_does_not_cache_budget_failure(self):
        c=Mock()
        c.get.side_effect=[RequestBudgetExceeded('request budget exhausted'),ValueError('unavailable')]
        r=RevisionRefresher(c)
        url='https://wikirouge.net/texts/en/Test'
        self.assertEqual(r.pinned(url,1),(None,None,'RequestBudgetExceeded'))
        self.assertFalse(r.parsed)
        r.pinned(url,1)
        self.assertEqual(c.get.call_count,2)
