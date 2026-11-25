import unittest
from pathlib import Path
from resolver import ROOT, Document,sha
from alignment import exact_occurrences
from chapter_routes import load_chapters, chapter_route,retrieve_chapter
from refinement import DirectAlignmentCache,review_packet
from chapter_quality import review_receipt
from unittest.mock import Mock


class ChapterRoutesTests(unittest.TestCase):
    def setUp(self):
        self.entries=load_chapters(ROOT/'chapter-catalog.json')
        self.e=self.entries[0]
        self.q='Les travailleurs sont unis dans cette lutte.'
        self.fr=Document(self.e['fr_url'],[self.q,' '.join(['Une phrase de contexte.']*15)],'f')
        self.en=Document(self.e['en_url'],['The workers are united in this struggle.',' '.join(['A sentence of context.']*15)],'e')
        hits,_=exact_occurrences(self.q,self.fr)
        self.source={'status':'exact_in_examined_documents','occurrences':hits}
        self.meta={'authors':self.e['authors'],'mia_urls':[self.e['fr_url']]}

    def test_requires_exact_source_and_author(self):
        self.assertEqual(chapter_route(self.meta,self.source,self.entries),self.e)
        for meta in ({**self.meta,'authors':'Wrong Author'}, {**self.meta,'mia_urls':['https://www.marxists.org/francais/other.htm']}):
            self.assertIsNone(chapter_route(meta,self.source,self.entries))
        for src in ({**self.source,'occurrences_truncated':True},{**self.source,'status':'variant_review_required'},
                    {**self.source,'occurrences':self.source['occurrences']*2}):
            self.assertIsNone(chapter_route(self.meta,src,self.entries))

    def test_cannot_replace_known_chapter_with_another_document(self):
        other=Document(self.e['en_toc'],self.en.blocks,self.en.digest)
        with self.assertRaises(ValueError):
            retrieve_chapter(self.q,self.source,[self.fr],other,self.e,DirectAlignmentCache())

    def test_only_target_chapter_candidates_and_no_automatic_approval(self):
        r=retrieve_chapter(self.q,self.source,[self.fr],self.en,self.e,DirectAlignmentCache())
        p=review_packet(r)
        self.assertTrue(p['candidates'])
        self.assertTrue(all(x['url']==self.e['en_url'] for x in r['candidates']+r['section_alignment']['candidates']))
        self.assertFalse(r['auto_publish'])
        self.assertFalse(p['candidates'][0]['boundary_verified'])

    def test_review_requires_frozen_input(self):
        d={'case_id':'a','wiki_revision':1,'quote_sha256':'q','decision':'equivalent_complete_passage'}
        c={'id':'a','citation_ref':{'revision':2,'quote_sha256':'q'}}
        audit=Mock()
        self.assertEqual(review_receipt(c,{},d,audit)['status'],'review_input_mismatch')
        audit.input.assert_not_called()
