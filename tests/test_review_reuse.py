import copy
import unittest

from resolver import Document, sha
from review_memory import proof
from review_reuse import MemoryFirst, try_reuse

FR = 'https://www.marxists.org/francais/test.htm'
EN = 'https://www.marxists.org/archive/test.htm'
QUOTE = 'Texte synthetique pour les tests.'
TEXT = 'An exact synthetic passage. An omitted part. Another fragment.'
DIGEST = 'a' * 64


def fixture(decision='complete_equivalent'):
    ref = {'revision': 123, 'template_index': 0,
           'template_sha256': 'b' * 64, 'quote_sha256': sha(QUOTE.encode())}
    case = {'id': 'new-case', 'wiki_title': 'Example', 'citation_ref': ref,
            'metadata': {'mia_urls': [FR]}}
    spans = [(0, len(TEXT))] if decision == 'complete_equivalent' else [(0, 27), (44, len(TEXT))]
    fragments = [{'relative_start': lo, 'relative_end': hi,
                  'text_sha256': sha(TEXT[lo:hi].encode())} for lo, hi in spans]
    p = proof(decision, EN, None, DIGEST, sha(TEXT.encode()), 0, len(TEXT), fragments)
    row = {'id': 'old-case', 'citation_ref': {**ref, 'revision': 100},
           'metadata': case['metadata'], 'latest_decision': decision, 'first': p,
           'auto_publish': False, 'rights': 'unreviewed'}
    return case, {'cases': [copy.deepcopy(row)]}


class Audit:
    def __init__(self):
        self.inputs = 0
        self.documents = 0
        self.doc = Document(EN, [TEXT], DIGEST)
        self.publication = {('en', EN): {'provider': 'mia'}}

    def input(self, case):
        self.inputs += 1
        return ({**case['citation_ref'], 'status': 'verified_pinned_citation',
                 'mia_urls': [FR]}, QUOTE)

    def document(self, url, lang):
        self.documents += 1
        return self.doc


class Retrieval:
    def __init__(self):
        self.calls = []

    def retrieve(self, case):
        self.calls.append(case['id'])
        return {'status': 'candidate_review_required'}


class MemoryReuseTests(unittest.TestCase):
    def test_hit_skips_retrieval_even_on_identical_template_in_new_revision(self):
        case, memory = fixture()
        a, r = Audit(), Retrieval()
        engine = MemoryFirst(r, memory, a)
        result = engine.resolve(case)
        self.assertEqual(result['path'], 'memory')
        self.assertEqual(engine.stats, {'reused': 1, 'retrieved': 0})
        self.assertEqual((a.inputs, a.documents, r.calls), (1, 1, []))
        m = result['memory']
        self.assertEqual(m['fragments'][0]['text'], TEXT)
        self.assertEqual(m['target_receipt']['status'], 'verified_document_and_span')
        self.assertFalse(m['new_semantic_validation'])
        self.assertFalse(m['semantic_review_required'])
        self.assertFalse(m['french_archive_span_revalidated'])
        self.assertTrue(m['editorial_review_required'])
        self.assertEqual(m['rights'], 'unreviewed')
        self.assertFalse(result['auto_publish'])

    def test_exact_omission_fragments_preserved_separately(self):
        case, memory = fixture('omission_adjustment')
        m = try_reuse(memory, case, Audit())
        self.assertEqual(m['status'], 'historical_equivalence_revalidated')
        self.assertEqual([f['text'] for f in m['fragments']], [TEXT[:27], TEXT[44:]])

    def test_changed_template_and_ambiguous_memory_never_use_audit(self):
        for change in ('template', 'duplicate', 'url'):
            with self.subTest(change=change):
                case, memory = fixture()
                if change == 'template':
                    case['citation_ref']['template_sha256'] = 'c' * 64
                elif change == 'duplicate':
                    memory['cases'] *= 2
                else:
                    case['metadata']['mia_urls'] = [FR.replace('test', 'other')]
                a, r = Audit(), Retrieval()
                result = MemoryFirst(r, memory, a).resolve(case)
                self.assertEqual(result['path'], 'retrieval')
                self.assertEqual((a.inputs, a.documents, r.calls), (0, 0, ['new-case']))

    def test_negative_and_superseded_decisions_do_not_skip_retrieval(self):
        case, memory = fixture()
        row = memory['cases'][0]
        row['second'] = {'decision': 'wrong_passage'}
        row['latest_decision'] = 'wrong_passage'
        a, r = Audit(), Retrieval()
        result = MemoryFirst(r, memory, a).resolve(case)
        self.assertEqual(result['path'], 'retrieval')
        self.assertEqual(result['memory']['reason'], 'historical_unresolved')
        self.assertEqual((a.inputs, a.documents, r.calls), (0, 0, ['new-case']))

    def test_changed_document_or_missing_target_falls_back(self):
        for change in ('html', 'text', 'missing', 'url', 'revision'):
            with self.subTest(change=change):
                case, memory = fixture()
                a, r = Audit(), Retrieval()
                if change == 'html':
                    a.doc.digest = 'c' * 64
                elif change == 'text':
                    a.doc.blocks = ['Changed target text.']
                elif change == 'missing':
                    a.doc = {'status': 'document_unavailable'}
                elif change == 'url':
                    a.doc.url += '2'
                else:
                    a.doc.revision = 1
                result = MemoryFirst(r, memory, a).resolve(case)
                self.assertEqual(result['path'], 'retrieval')
                self.assertNotIn('fragments', result['memory'])
                self.assertEqual(r.calls, ['new-case'])

    def test_changed_input_never_reads_target(self):
        case, memory = fixture()
        a = Audit()
        a.input = lambda c: ({**c['citation_ref'], 'status': 'verified_pinned_citation',
                               'mia_urls': [FR]}, 'wrong text')
        r = Retrieval()
        result = MemoryFirst(r, memory, a).resolve(case)
        self.assertEqual(result['memory']['reason'], 'input_not_verified')
        self.assertEqual((a.documents, r.calls), (0, ['new-case']))

    def test_corrupt_reviewed_fragment_falls_back(self):
        case, memory = fixture('omission_adjustment')
        memory['cases'][0]['first']['fragments'][0]['text_sha256'] = 'c' * 64
        r = Retrieval()
        result = MemoryFirst(r, memory, Audit()).resolve(case)
        self.assertEqual(result['memory']['reason'], 'fragment_evidence_changed')
        self.assertEqual(r.calls, ['new-case'])


if __name__ == '__main__':
    unittest.main()
