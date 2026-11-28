from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from audit_prepared_bundle import replay
from citation_registry import CitationRegistry, fingerprint
from registry_transfer import export_bundle
from test_citation_registry import RAW, QUOTE, ENGLISH, ref, candidate, document, review
from work_library import WorkLibrary


class PreparedBundleReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.source = WorkLibrary(root/'source.sqlite3')
        self.dest = WorkLibrary(root/'dest.sqlite3')
        self.addCleanup(self.source.db.close)
        self.addCleanup(self.dest.db.close)
        self.registry = CitationRegistry(self.source.db)
        self.target = CitationRegistry(self.dest.db)
        self.doc = document()
        self.rf = ref(RAW, 7)
        c = candidate(self.doc)
        self.registry.record(RAW, self.rf, self.doc, c, review(RAW,self.rf,self.doc,c))
        self.bundle = export_bundle(self.registry)
        self.digest = fingerprint(self.bundle)

    def run_audit(self, raw=RAW, doc=None, revision=7):
        audit = SimpleNamespace(document=lambda *_: doc or self.doc, publication={})
        return replay(self.bundle, self.digest, self.target,
                      lambda rev: ('Fixture', revision, raw), audit)

    def test_exact_lookup_only_and_no_text_in_report(self):
        result = self.run_audit()
        self.assertTrue(result['all_receipts_revalidated'])
        self.assertEqual(result['exact_receipts_reconstructed'],1)
        self.assertEqual(result['retrieval_calls'],0)
        self.assertEqual(result['rights'],'unreviewed')
        self.assertFalse(result['auto_publish'])
        self.assertNotIn(QUOTE,str(result))
        self.assertNotIn(ENGLISH,str(result))
        self.assertEqual(export_bundle(self.registry),self.bundle)
        self.assertEqual(export_bundle(self.target),self.bundle)

    def test_two_concordant_receipts_are_not_two_exact_envelopes(self):
        c = candidate(self.doc)
        rv = review(RAW,self.rf,self.doc,c)
        rv['reviewer'] = 'another reviewer'
        self.registry.record(RAW,self.rf,self.doc,c,rv)
        self.bundle = export_bundle(self.registry)
        self.digest = fingerprint(self.bundle)
        got = self.run_audit()
        self.assertEqual(got['entries'],2)
        self.assertEqual(got['states'],{
            'exact_receipt_reconstructed':1,'equivalent_receipt_reconstructed':1})
        self.assertEqual(got['entries_with_revalidated_passage'],2)
        self.assertFalse(got['all_receipts_revalidated'])
        self.assertTrue(got['all_entries_have_revalidated_passage'])

    def test_changed_source_or_target_abstains(self):
        for raw,doc,revision,expected in [
            (RAW.replace('plusieurs','autres'),self.doc,7,'source_identity_unconfirmed'),
            (RAW,self.doc,8,'source_identity_unconfirmed'),
            (RAW,document(digest='c'*64),7,'retry_documentary')]:
            with self.subTest(expected=expected):
                got = self.run_audit(raw,doc,revision)
                self.assertFalse(got['all_receipts_revalidated'])
                self.assertEqual(got['states'],{expected:1})
                self.assertFalse(got['all_entries_have_revalidated_passage'])
                if expected == 'retry_documentary':
                    self.assertEqual(got['retry_reasons'],{'stored_passage_not_revalidated':1})

    def test_unavailable_revision_and_wrong_digest(self):
        def unavailable(_):
            raise RuntimeError('private details')
        audit = SimpleNamespace(document=lambda *_: self.fail('unexpected request'),
                                publication={})
        got = replay(self.bundle,self.digest,self.target,unavailable,audit)
        self.assertEqual(got['states'],{'source_unavailable':1})
        self.assertFalse(got['all_receipts_revalidated'])
        with self.assertRaisesRegex(ValueError,'transfer_bundle_digest_mismatch'):
            replay(self.bundle,'a'*64,self.target,unavailable,audit)
