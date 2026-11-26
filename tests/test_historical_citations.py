import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from citation_registry import CitationRegistry
from historical_citations import (first_candidate, second_candidate, read_candidate,
                                  verify_and_record)
from test_citation_registry import RAW, ENGLISH, ref, candidate, document
from resolver import sha
from evidence_audit import span_receipt
from work_library import WorkLibrary


class Audit:
    def __init__(self, doc):
        self.doc = doc

    def document(self, url, lang):
        return self.doc


class HistoricalProofTests(unittest.TestCase):
    def setUp(self):
        self.doc = document()
        self.c = candidate(self.doc)
        self.fragments = [[0, len(ENGLISH), sha(ENGLISH.encode())]]
        self.decision = {'decision': 'complete_equivalent', 'fragments': self.fragments}
        self.ref = ref(RAW)

    def test_first_receipt_is_bound_to_exact_original_decision(self):
        old = {'status': 'review_revalidated', 'decision': 'complete_equivalent',
               'citation_ref': self.ref, 'target_url': self.doc.url,
               'target_revision': self.doc.revision, 'document_sha256': self.doc.digest,
               'target_text_sha256': self.c['text_sha256'], 'char_start': 0,
               'char_end': len(ENGLISH), 'fragments': [{'relative_start': 0,
                   'relative_end': len(ENGLISH), 'text_sha256': self.c['text_sha256'],
                   'char_start': 0, 'char_end': len(ENGLISH)}]}
        self.assertEqual(first_candidate(old, self.decision, self.ref), (self.c, self.fragments))
        old['fragments'][0]['char_end'] -= 1
        with self.assertRaises(ValueError):
            first_candidate(old, self.decision, self.ref)

    def test_second_receipt_is_bound_to_unique_candidate(self):
        r = span_receipt(self.doc, self.c)
        old = {'status': 'ready', 'citation_ref': self.ref, 'candidates': [{'receipt': r}]}
        decision = {'candidate_sha256': self.c['text_sha256'], 'fragments': [
            {'relative_start': 0, 'relative_end': len(ENGLISH), 'text_sha256': self.c['text_sha256']}]}
        self.assertEqual(second_candidate(old, decision, self.ref), (self.c, self.fragments))
        old['candidates'].append({'receipt': {**r, 'url': self.doc.url+'b'}})
        with self.assertRaises(ValueError):
            second_candidate(old, decision, self.ref)

    def test_changed_html_rebound_only_same_positive_revision_and_exact_text(self):
        url = 'https://wikirouge.net/texts/en/Test'
        old = document(url=url, revision=7)
        changed = document(url=url, revision=7, digest='e'*64)
        _, new, status = read_candidate(Audit(changed), candidate(old))
        self.assertEqual(status, 'new_proof_same_revision_exact_span')
        self.assertEqual(new['sha256'], changed.digest)
        for invalid in (document(url=url, revision=8, digest='e'*64),
                        document(url=url, revision=7, digest='e'*64, blocks=['different'])):
            with self.assertRaises(ValueError):
                read_candidate(Audit(invalid), candidate(old))
        with self.assertRaises(ValueError):
            read_candidate(Audit(document(digest='e'*64)), self.c)

    def test_registry_record_is_idempotent_and_rejects_bad_fragments(self):
        with TemporaryDirectory() as tmp, WorkLibrary(Path(tmp)/'works.sqlite3') as lib:
            reg = CitationRegistry(lib.db)
            args = (reg, RAW, self.ref, Audit(self.doc), self.c, self.fragments,
                    'complete_equivalent', {'ledger_sha256': 'a'*64})
            self.assertEqual(verify_and_record(*args), verify_and_record(*args))
            self.assertEqual(reg.reconstruct(RAW, self.ref, self.doc)['status'],
                             'reviewed_passage_reconstructed')
            with self.assertRaises(ValueError):
                verify_and_record(reg, RAW, self.ref, Audit(self.doc), self.c,
                                  [[0, len(ENGLISH), '0'*64]], 'complete_equivalent', {})
            self.assertEqual(lib.db.execute('SELECT count(*) FROM prepared_citations_v1').fetchone()[0], 1)

    def test_wrong_revision_cannot_record(self):
        with TemporaryDirectory() as tmp, WorkLibrary(Path(tmp)/'works.sqlite3') as lib:
            with self.assertRaises(ValueError):
                verify_and_record(CitationRegistry(lib.db), RAW, self.ref,
                                  Audit(document(revision=3)), self.c, self.fragments,
                                  'complete_equivalent', {})
