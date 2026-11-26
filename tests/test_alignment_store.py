import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from alignment import exact_occurrences, align_sections
from refinement import DirectAlignmentCache
from resolver import Document, sha
from section_match import automatic_alignment
from work_library import WorkLibrary


class PersistentAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name)/'works.sqlite3'
        self.q1 = 'Les travailleurs sont unis dans cette lutte.'
        self.q2 = 'Le travail commun est une force importante.'
        self.fr = Document('https://www.marxists.org/francais/test/a.htm',
            [self.q1, self.q2, ' '.join(['Une phrase de contexte.']*15)],
            'a'*64, headings=('Lutte de classe',))
        self.en = Document('https://www.marxists.org/archive/test/a.htm',
            ['The workers are united in this struggle.',
             'Common work is an important force.',
             ' '.join(['A sentence of context.']*15)],
            'b'*64, headings=('Class struggle',))

    def source(self, quote):
        hits, truncated = exact_occurrences(quote, self.fr)
        self.assertEqual(len(hits), 1)
        self.assertFalse(truncated)
        return {'status':'exact_in_examined_documents','occurrences':hits}

    def test_reopen_direct_and_structural_distinct_citations(self):
        expected = automatic_alignment(self.q2,self.source(self.q2),
            [self.fr],[self.en],(),())
        with WorkLibrary(self.db) as lib:
            cache = DirectAlignmentCache(store=lib.alignment_store)
            one = cache.candidate(self.source(self.q1),[self.fr],[self.en],self.fr.url,'test')
            self.assertTrue(one['candidates'])
            self.assertEqual(cache.computations,1)
            self.assertEqual(lib.alignment_store.stats['alignment_disk_writes'],1)
        with WorkLibrary(self.db) as lib:
            cache = DirectAlignmentCache(store=lib.alignment_store)
            with patch('refinement.align_sections',side_effect=AssertionError('realignment')):
                two = automatic_alignment(self.q2,self.source(self.q2),
                    [self.fr],[self.en],(),(),alignment_cache=cache)
            self.assertEqual(two, expected)
            self.assertTrue(two['candidates'])
            self.assertFalse(two['auto_publish'])
            self.assertFalse(two['candidates'][0]['boundary_verified'])
            self.assertEqual(cache.computations,0)
            self.assertEqual(lib.alignment_store.stats['alignment_disk_hits'],1)
            raw = lib.db.execute('SELECT result FROM document_alignments_v1').fetchone()[0]
            self.assertNotIn(self.q1,raw)
            self.assertNotIn('The workers',raw)
            self.assertNotIn('quote_sha256',raw)
            self.assertNotIn('document_alignments',json.dumps(lib.export()))

    def test_identity_changes_and_budget_are_not_hits(self):
        with WorkLibrary(self.db) as lib:
            cache = DirectAlignmentCache(store=lib.alignment_store)
            cache.get(self.fr,self.en,'sentence')
        changed = [
            (Document(self.fr.url,self.fr.blocks,'c'*64),self.en,'sentence',250000),
            (Document(self.fr.url,[*self.fr.blocks,'Extra.'],self.fr.digest),self.en,'sentence',250000),
            (Document(self.fr.url,self.fr.blocks,self.fr.digest,revision=42),self.en,'sentence',250000),
            (self.fr,Document(self.en.url+'x',self.en.blocks,self.en.digest),'sentence',250000),
            (self.fr,self.en,'paragraph',250000),
            (self.fr,self.en,'sentence',1),
        ]
        for fr,en,kind,budget in changed:
            with self.subTest(kind=kind,budget=budget,fr=fr.url,en=en.url):
                with WorkLibrary(self.db) as lib:
                    cache = DirectAlignmentCache(store=lib.alignment_store)
                    self.assertEqual(cache.get(fr,en,kind,budget),
                                     align_sections(fr,en,kind,budget))
                    self.assertEqual(cache.computations,1)
                    self.assertEqual(lib.alignment_store.stats['alignment_disk_hits'],0)
                    if budget==1:
                        self.assertEqual(lib.alignment_store.stats['alignment_disk_writes'],0)

    def test_changed_algorithm_invalidates(self):
        with WorkLibrary(self.db) as lib:
            DirectAlignmentCache(store=lib.alignment_store).get(self.fr,self.en,'paragraph')
        with WorkLibrary(self.db) as lib, patch('alignment_store.algorithm',return_value='different'):
            cache = DirectAlignmentCache(store=lib.alignment_store)
            cache.get(self.fr,self.en,'paragraph')
            self.assertEqual(cache.computations,1)

    def test_corrupt_record_recomputed(self):
        with WorkLibrary(self.db) as lib:
            DirectAlignmentCache(store=lib.alignment_store).get(self.fr,self.en,'paragraph')
            with lib.db:
                lib.db.execute("UPDATE document_alignments_v1 SET result='{}'")
        with WorkLibrary(self.db) as lib:
            cache = DirectAlignmentCache(store=lib.alignment_store)
            self.assertEqual(cache.get(self.fr,self.en,'paragraph'),
                             align_sections(self.fr,self.en,'paragraph'))
            self.assertEqual(cache.computations,1)
            self.assertEqual(lib.alignment_store.stats['alignment_disk_invalid'],1)
