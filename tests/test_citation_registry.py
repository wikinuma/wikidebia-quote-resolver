import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from alignment import text_and_starts
from citation_registry import CitationRegistry, prepare_review_source, source_identity
from navigation import citation_record, templates
from resolver import Document, sha
from work_library import WorkLibrary

FR='https://www.marxists.org/francais/test/a.htm'
EN='https://www.marxists.org/archive/test/a.htm'
QUOTE='Un passage de test avec plusieurs mots.'
RAW='{{Citation|citation='+QUOTE+'|auteurs=Auteur test|ouvrage=Ouvrage test|lien='+FR+'|date=1900}}'
ENGLISH='First test sentence. Another test sentence. Final test sentence.'


def ref(raw,revision=1,index=0):
    t=list(templates(raw,{'citation'}))[index]
    rec=citation_record(t,index)
    return {k:rec[k] for k in ('template_index','template_sha256','quote_sha256')} | {'revision':revision}


def document(url=EN,digest=None,revision=None,blocks=None):
    return Document(url,blocks if blocks is not None else [ENGLISH],
                    digest or sha(b'english-test-document'),revision=revision)


def candidate(doc):
    text,_=text_and_starts(doc)
    return {'url':doc.url,'revision':doc.revision,'sha256':doc.digest,
            'text_sha256':sha(text.encode()),'char_start':0,'char_end':len(text)}


def review(raw,ref,doc,cand,decision='complete_equivalent',ranges=None):
    ids=prepare_review_source(raw,ref,doc,cand)
    return {'source_key':ids['source']['source_key'],'candidate_key':ids['target']['candidate_key'],
            'review_packet_sha256':'a'*64,'reviewer':'test-editor','decision':decision,
            'ranges':ranges if ranges is not None else [[0,cand['char_end']-cand['char_start']]]}


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'works.sqlite3'
        self.doc=document()
        self.cand=candidate(self.doc)
        self.ref=ref(RAW)
        self.review=review(RAW,self.ref,self.doc,self.cand)

    def save(self,lib):
        return CitationRegistry(lib.db).record(RAW,self.ref,self.doc,self.cand,self.review)

    def test_persistence_and_stable_identity_across_pages(self):
        with WorkLibrary(self.path) as lib:
            self.assertEqual(self.save(lib),self.save(lib))
        moved='Other page text.\n{{Citation|citation=Other quote|lien=https://example.org/a}}\n'+RAW.replace('|date=1900}}','|date=1900 }}')
        moved_ref=ref(moved,revision=2,index=1)
        with WorkLibrary(self.path) as lib:
            registry=CitationRegistry(lib.db)
            result=registry.reconstruct(moved,moved_ref,self.doc)
            self.assertEqual(result['status'],'reviewed_passage_reconstructed')
            self.assertEqual(result['fragments'][0]['text'],ENGLISH)
            self.assertFalse(result['auto_publish'])
            self.assertEqual(result['rights'],'unreviewed')
            self.assertTrue(result['fresh_read_not_established_here'])
            records=lib.db.execute('SELECT entry FROM prepared_citations_v1').fetchall()
            self.assertEqual(len(records),1)
            self.assertNotIn(QUOTE,records[0][0])
            self.assertNotIn(ENGLISH,records[0][0])
            self.assertNotIn('prepared_citations',json.dumps(lib.export()))

    def test_reordered_parameters_match_and_changes_invalidate(self):
        reordered='{{Citation|date=1900|ouvrage=Ouvrage test|lien='+FR+'|citation='+QUOTE+'|auteurs=Auteur test}}'
        with WorkLibrary(self.path) as lib:
            self.save(lib)
            registry=CitationRegistry(lib.db)
            self.assertEqual(registry.lookup(reordered,ref(reordered))['status'],'reviewed_receipt_found')
            for raw in (RAW.replace('plusieurs','autres'),RAW.replace('Auteur test','Autre auteur'),
                        RAW.replace('1900','1901'),RAW.replace('/a.htm','/b.htm'),
                        RAW.replace('}}','|traducteur=Autre traducteur}}')):
                self.assertEqual(registry.lookup(raw,ref(raw))['status'],'miss')
            with self.assertRaises(ValueError):
                registry.lookup(RAW.replace(FR,'https://example.org/a'),ref(RAW.replace(FR,'https://example.org/a')))
            with self.assertRaises(ValueError):
                registry.lookup(RAW,{**self.ref,'template_sha256':'0'*64})

    def test_reject_unbound_or_unverified_review(self):
        with WorkLibrary(self.path) as lib:
            registry=CitationRegistry(lib.db)
            for rv in ({**self.review,'source_key':'a'*64},
                       {**self.review,'candidate_key':'b'*64},
                       {**self.review,'review_packet_sha256':'invalid'},
                       {**self.review,'decision':'wrong_passage'},
                       {**self.review,'ranges':[[0,5]]},
                       {**self.review,'decision':'omission_adjustment','ranges':[[0,5],[10,15]]}):
                with self.assertRaises(ValueError):
                    registry.record(RAW,self.ref,self.doc,self.cand,rv)
            with self.assertRaises(ValueError):
                registry.record(RAW,self.ref,self.doc,{**self.cand,'text_sha256':'1'*64},self.review)
            self.assertEqual(registry.lookup(RAW,self.ref)['status'],'miss')

    def test_exact_fragments_and_explicit_omissions(self):
        raw=RAW.replace(QUOTE,'Un passage [...] avec plusieurs mots.')
        rf=ref(raw)
        rv=review(raw,rf,self.doc,self.cand,'omission_adjustment',[[0,20],[43,len(ENGLISH)]])
        with WorkLibrary(self.path) as lib:
            registry=CitationRegistry(lib.db)
            registry.record(raw,rf,self.doc,self.cand,rv)
            result=registry.reconstruct(raw,rf,self.doc)
            self.assertEqual([f['text'] for f in result['fragments']],
                             [ENGLISH[:20],ENGLISH[43:]])
            for f in result['fragments']:
                self.assertEqual(f['text_sha256'],sha(f['text'].encode()))
            for ranges in ([[0,20],[19,30]],[[0,20],[20,30]],[[0,20],[40,999]],[[0,20]]):
                with self.assertRaises(ValueError):
                    registry.record(raw,rf,self.doc,self.cand,{**rv,'ranges':ranges})

    def test_changed_document_and_conflicting_editions(self):
        with WorkLibrary(self.path) as lib:
            self.save(lib)
            registry=CitationRegistry(lib.db)
            for doc in (document(digest='d'*64),document(revision=2),
                        document(blocks=[ENGLISH[:20],ENGLISH[20:]]),
                        document(url=EN+'x')):
                self.assertEqual(registry.reconstruct(RAW,self.ref,doc)['status'],'target_evidence_changed')
            other=document(url=EN+'x')
            c=candidate(other)
            registry.record(RAW,self.ref,other,c,review(RAW,self.ref,other,c))
            self.assertEqual(registry.lookup(RAW,self.ref)['status'],'ambiguous_reviewed_passages')
            self.assertNotIn('fragments',registry.reconstruct(RAW,self.ref,self.doc))

    def test_corruption_fails_closed(self):
        with WorkLibrary(self.path) as lib:
            self.save(lib)
            with lib.db:
                lib.db.execute("UPDATE prepared_citations_v1 SET entry='{}'")
            with self.assertRaises(ValueError):
                CitationRegistry(lib.db).lookup(RAW,self.ref)

    def test_same_pinned_wiki_span_different_html_not_conflicting(self):
        url = 'https://wikirouge.net/texts/en/Test'
        docs = [document(url=url, revision=7, digest=d*64) for d in ('a', 'b', 'c')]
        with WorkLibrary(self.path) as lib:
            reg = CitationRegistry(lib.db)
            for doc in docs[:2]:
                cand = candidate(doc)
                reg.record(RAW, self.ref, doc, cand, review(RAW, self.ref, doc, cand))
            self.assertEqual(reg.lookup(RAW, self.ref)['status'], 'reviewed_receipt_found')
            self.assertEqual(reg.lookup(RAW, self.ref)['receipt_count'], 2)
            for doc in docs[:2]:
                result = reg.reconstruct(RAW, self.ref, doc)
                self.assertEqual(result['status'], 'reviewed_passage_reconstructed')
                self.assertEqual(result['receipt']['target']['document']['body_sha256'], doc.digest)
            self.assertEqual(reg.reconstruct(RAW, self.ref, docs[2])['status'], 'target_evidence_changed')

    def test_changed_wiki_passage_or_revision_still_conflicts(self):
        url = 'https://wikirouge.net/texts/en/Test'
        for other in (document(url=url, revision=7, digest='b'*64, blocks=['Changed passage.']),
                      document(url=url, revision=8, digest='b'*64)):
            with TemporaryDirectory() as tmp, WorkLibrary(Path(tmp)/'works.sqlite3') as lib:
                reg = CitationRegistry(lib.db)
                for doc in (document(url=url, revision=7), other):
                    cand = candidate(doc)
                    reg.record(RAW, self.ref, doc, cand, review(RAW, self.ref, doc, cand))
                self.assertEqual(reg.lookup(RAW, self.ref)['status'], 'ambiguous_reviewed_passages')

    def test_unversioned_document_body_change_is_not_collapsed(self):
        with WorkLibrary(self.path) as lib:
            reg = CitationRegistry(lib.db)
            for doc in (self.doc, document(digest='b'*64)):
                cand = candidate(doc)
                reg.record(RAW, self.ref, doc, cand, review(RAW, self.ref, doc, cand))
            self.assertEqual(reg.lookup(RAW, self.ref)['status'], 'ambiguous_reviewed_passages')
