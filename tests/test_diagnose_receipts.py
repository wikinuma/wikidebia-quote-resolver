import unittest
from unittest.mock import Mock
from diagnose_receipts import diagnose
from resolver import Document, sha


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.url = 'https://wikirouge.net/texts/en/Test'
        self.entry = {'case_id': 'test', 'url': self.url, 'revision': 12,
                      'sha256': 'a'*64, 'text_sha256': sha(b'Example.'),
                      'char_start': 0, 'char_end': 8}
        self.audit = Mock()

    def result(self, digest, revision, text):
        self.audit.document.return_value = Document(self.url, [text], digest, 'Test', revision)
        return diagnose([self.entry], self.audit)[0]

    def test_strict_match(self):
        self.assertTrue(self.result('a'*64, 12, 'Example.')['strictly_verified'])

    def test_html_only_drift_not_strict(self):
        r = self.result('b'*64, 12, 'Example.')
        self.assertEqual(r['diagnosis'], 'same_revision_exact_span_changed_html')
        self.assertFalse(r['strictly_verified'])
        self.assertFalse(r['auto_publish'])

    def test_revision_mismatch_even_with_identical_text(self):
        self.assertEqual(self.result('a'*64, 13, 'Example.')['diagnosis'], 'revision_mismatch')

    def test_span_mismatch(self):
        self.assertEqual(self.result('a'*64, 12, 'Changed.')['diagnosis'], 'span_mismatch')

    def test_wrong_provider(self):
        with self.assertRaises(ValueError):
            diagnose([{**self.entry, 'url': 'https://www.marxists.org/archive/a.htm'}], self.audit)

    def test_no_text_in_receipt(self):
        r = self.result('a'*64, 12, 'Example.')
        self.assertNotIn('Example.', str(r))
        self.assertEqual(r['rights'], 'unreviewed')
