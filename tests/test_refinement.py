import unittest
from refinement import DirectAlignmentCache, select_source_hit, review_packet
from resolver import Document,norm,sha
from alignment import exact_occurrences, text_and_starts
from bibliography import load_catalog, match_catalog
from pathlib import Path


class RefinementTests(unittest.TestCase):
    def setUp(self):
        self.q='Les travailleurs sont unis dans cette lutte.'
        self.f=Document('fr',[self.q,' '.join(['Une phrase de contexte.']*15)],'f')
        self.e=Document('en',['The workers are united in this struggle.',' '.join(['A sentence of context.']*15)],'e')
        hits,_=exact_occurrences(self.q,self.f)
        self.exact={'status':'exact_in_examined_documents','occurrences':hits}
        h=hits[0]
        self.variant={**h,'match':'approximate_tokens_review','similarity':.98,'token_edits':1,
                      'normalized_sha256':sha(norm(h['text']).encode()),'boundary_ambiguous':True}
        self.source={'status':'variant_review_required','variants':[self.variant]}

    def test_reuse_alignment_across_quotes_and_variant(self):
        cache=DirectAlignmentCache()
        a=cache.candidate(self.exact,[self.f],[self.e],'fr','test')
        b=cache.candidate(self.source,[self.f],[self.e],'fr','test')
        self.assertTrue(a['candidates'])
        self.assertTrue(b['candidates'])
        self.assertEqual(cache.computations,1)
        self.assertEqual(cache.hits,1)
        c=b['candidates'][0]
        self.assertEqual(c['source_evidence_kind'],'approximate_tokens_review')
        self.assertTrue(c['source_boundary_ambiguous'])
        self.assertFalse(c['boundary_verified'])
        self.assertFalse(b['auto_publish'])

    def test_variant_unique_location_and_fingerprint_required(self):
        for bad in ({**self.source,'variants':[self.variant,self.variant]},
                    {**self.source,'variants':[{**self.variant,'similarity':.8}]},
                    {**self.source,'variants':[{**self.variant,'normalized_sha256':'wrong'}]},
                    {**self.source,'variants':[{**self.variant,'char_end':999999}]},
                    {**self.source,'stages':[{'variant_search_truncated':True}]}):
            self.assertEqual(select_source_hit(bad,[self.f],'fr'),(None,None))

    def test_mirror_same_variant_not_two_independent_sources(self):
        mirrored={**self.variant,'url':'mirror','sha256':'other'}
        fr,hit=select_source_hit({**self.source,'variants':[self.variant,mirrored]},[self.f],'fr')
        self.assertIs(fr,self.f)
        self.assertEqual(hit['url'],'fr')
        self.assertEqual(select_source_hit({**self.source,'variants':[self.variant,{**mirrored,'normalized_sha256':'different'}]},[self.f],'fr'),(None,None))

    def test_omission_is_only_an_envelope(self):
        v={**self.variant,'match':'explicit_omission_review','token_edits':0,
           'fragment_ranges':[{'char_start':0,'char_end':4},{'char_start':6,'char_end':12}]}
        r=DirectAlignmentCache().candidate({'status':'variant_review_required','variants':[v]},[self.f],[self.e],'fr','test')
        self.assertTrue(r['candidates'][0]['omission_envelope_only'])
        self.assertFalse(r['candidates'][0]['boundary_verified'])

    def test_prioritized_packet_keeps_alternatives(self):
        row={'candidates':[{'text':'Different text'},{'text':'Other wording'}],
             'section_alignment':{'candidates':[{'text':'Aligned wording'}]}}
        packet=review_packet(row)
        self.assertEqual(packet['candidates'],row['section_alignment']['candidates'])
        self.assertEqual(packet['deferred_distinct_candidates'],2)
        self.assertEqual(len(row['candidates']),2)
        self.assertFalse(packet['auto_publish'])

    def test_catalog_does_not_conflate_plekhanov_1904_and_1897(self):
        entries=load_catalog(Path(__file__).resolve().parents[1]/'bibliographic-catalog.json')
        meta={'mia_urls':['https://www.marxists.org/francais/plekhanov/works/1904/00/plekhanov_19040000.htm'],
              'article':"La conception matérialiste de l'histoire",'authors':'Gueorgui Plekhanov'}
        self.assertIsNone(match_catalog(meta,entries))
        harman=[x for x in entries if x['source_url']==
                'https://www.marxists.org/francais/harman/1979/qcqlm/index.htm']
        self.assertEqual(len(harman),1)
        self.assertTrue(harman[0]['target_toc'])
        self.assertTrue(all(not x['whole_document_parallel'] for x in entries
                            if x.get('target_toc')))
