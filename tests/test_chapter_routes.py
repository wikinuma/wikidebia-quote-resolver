import unittest
from pathlib import Path
from resolver import ROOT, Document,sha
from alignment import exact_occurrences
from chapter_routes import load_chapters, chapter_route,chapter_urls,retrieve_chapter
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

    def test_unique_variant_may_route_but_not_approve(self):
        from resolver import norm
        hit={**self.source['occurrences'][0],'match':'approximate_tokens_review','similarity':.98,
             'normalized_sha256':sha(norm(self.q).encode())}
        src={'status':'variant_review_required','variants':[hit]}
        self.assertEqual(chapter_route(self.meta,src,self.entries),self.e)
        r=retrieve_chapter(self.q,src,[self.fr],self.en,self.e,DirectAlignmentCache())
        self.assertFalse(r['auto_publish'])
        self.assertEqual(r['rights'],'unreviewed')
        for invalid in ({**src,'variants':[hit,hit]},
                        {**src,'variants':[{**hit,'similarity':.70}]},
                        {**src,'source_evidence_diagnostics':[{'truncated':True}]},
                        {**src,'variants':[hit,{**hit,'url':'https://other.invalid/','normalized_sha256':'different'}]}):
            self.assertIsNone(chapter_route(self.meta,invalid,self.entries))
        with self.assertRaises(ValueError):
            retrieve_chapter(self.q,{'status':'variant_review_required','variants':[{**hit,'text':'altered'}]},[self.fr],self.en,self.e,DirectAlignmentCache())

    def test_hrr_numbering_does_not_imply_same_chapter(self):
        h=[e for e in self.entries if e['fr_url'].endswith('/hrr43.htm')]
        self.assertEqual(len(h),1)
        self.assertTrue(h[0]['en_url'].endswith('/ch42.htm'))

    def test_new_targeted_chapters_are_bound_to_source_and_toc(self):
        pairs = [
            ("https://www.marxists.org/francais/trotsky/livres/hrrusse/hrr30.htm",
             "https://www.marxists.org/archive/trotsky/1930/hrr/ch29.htm"),
            ("https://www.marxists.org/francais/trotsky/livres/hrrusse/hrr31.htm",
             "https://www.marxists.org/archive/trotsky/1930/hrr/ch30.htm"),
            ("https://www.marxists.org/francais/trotsky/livres/hrrusse/hrr37.htm",
             "https://www.marxists.org/archive/trotsky/1930/hrr/ch36.htm"),
            ("https://www.marxists.org/francais/trotsky/livres/hrrusse/hrr38.htm",
             "https://www.marxists.org/archive/trotsky/1930/hrr/ch37.htm"),
            ("https://www.marxists.org/francais/kautsky/works/1892/00/kautsky_18920000_03.htm",
             "https://www.marxists.org/archive/kautsky/1892/erfurt/ch03.htm"),
            ("https://www.marxists.org/francais/kautsky/works/1892/00/kautsky_18920000_05.htm",
             "https://www.marxists.org/archive/kautsky/1892/erfurt/ch05.htm"),
            ("https://www.marxists.org/francais/lenin/works/1916/vlimperi/vlimp1.htm",
             "https://www.marxists.org/archive/lenin/works/1916/imp-hsc/ch01.htm"),
            ("https://www.marxists.org/francais/lenin/works/1916/vlimperi/vlimp2.htm",
             "https://www.marxists.org/archive/lenin/works/1916/imp-hsc/ch02.htm"),
            ("https://www.marxists.org/francais/lenin/works/1916/vlimperi/vlimp7.htm",
             "https://www.marxists.org/archive/lenin/works/1916/imp-hsc/ch07.htm"),
            ("https://www.marxists.org/francais/lenin/works/1916/vlimperi/vlimp10.htm",
             "https://www.marxists.org/archive/lenin/works/1916/imp-hsc/ch10.htm"),
            ("https://www.marxists.org/francais/lenin/works/1917/08/er3.htm",
             "https://www.marxists.org/archive/lenin/works/1917/staterev/ch03.htm"),
        ]
        for fr, en in pairs:
            with self.subTest(fr=fr):
                entry = next(e for e in self.entries if e['fr_url'] == fr)
                self.assertEqual(entry['en_url'], en)
                source = {'status': 'exact_in_examined_documents',
                          'occurrences': [{'url': fr}]}
                for link in (fr, entry['fr_toc']):
                    meta = {'authors': entry['authors'], 'mia_urls': [link]}
                    self.assertEqual(chapter_route(meta, source, self.entries), entry)
                # A different explicit source leaf must not be silently replaced.
                wrong = {**meta, 'mia_urls': [fr + '.other']}
                self.assertIsNone(chapter_route(wrong, source, self.entries))
        # Kautsky chapter IV is split across two English pages.
        split=next(e for e in self.entries if e['fr_url'].endswith('/kautsky_18920000_04.htm'))
        self.assertEqual([u.rsplit('/',1)[1] for u in chapter_urls(split)],['ch04.htm','ch04a.htm'])

    def test_split_chapter_preserves_document_boundaries_and_both_parts(self):
        route=next(e for e in self.entries if e['fr_url'].endswith('/kautsky_18920000_04.htm'))
        q='Les travailleurs sont unis dans cette lutte.'
        fr=Document(route['fr_url'],[q],'f')
        hits,_=exact_occurrences(q,fr)
        source={'status':'exact_in_examined_documents','occurrences':hits}
        docs=[Document(u,['The workers are united in this struggle.'],'digest'+str(i))
              for i,u in enumerate(chapter_urls(route))]
        cache=Mock()
        result=retrieve_chapter(q,source,[fr],docs,route,cache)
        cache.candidate.assert_not_called()
        self.assertEqual(result['target_scope'],'reviewed_split_chapter')
        self.assertEqual(result['section_alignment']['status'],'split_chapter_alignment_not_applicable')
        self.assertEqual(set(x['url'] for x in result['candidates'][:2]),set(chapter_urls(route)))
        self.assertEqual([x['root'] for x in result['target_attempts']],list(chapter_urls(route)))
        self.assertFalse(result['auto_publish'])
        for incomplete in (docs[:1],list(reversed(docs)),docs+[docs[0]]):
            with self.assertRaisesRegex(ValueError,'chapter_document_or_source_mismatch'):
                retrieve_chapter(q,source,[fr],incomplete,route,cache)

    def test_split_chapter_catalog_rejects_bad_leaf_sets(self):
        import json
        from tempfile import TemporaryDirectory
        route=next(e for e in self.entries if 'en_urls' in e)
        urls=list(chapter_urls(route))
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'catalog.json'
            for bad in ([urls[0]], [urls[0],urls[0]], list(reversed(urls)),
                        [urls[0],route['en_toc']], [urls[0],route['fr_url']],
                        [urls[0],'https://example.org/page']):
                path.write_text(json.dumps({'schema':'wikidebia-chapter-catalog-1',
                                            'entries':[{**route,'en_urls':bad}]}))
                with self.assertRaisesRegex(ValueError,'invalid_split_chapter_urls'):
                    load_chapters(path)

