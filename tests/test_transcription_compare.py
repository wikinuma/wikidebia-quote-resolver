import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from alignment_store import encode
from autonomous_review_check import seed_inventory
from citation_registry import CitationRegistry, fingerprint
from grouped_review import apply_review, review_view, REPLY
from prepare_citations import build_campaign_packet, Campaign, CANDIDATES, COMPACT
from review_bundle import write_bundle, read_packet
from review_context import exact_target
from resolver import sha
from test_citation_registry import RAW, ref, source_identity, document
from transcription_compare import (MIA_STATE_URL, STATE_MIRROR_URL, compare_spacing,
                                   compare_documented, supplement_packet)
from work_library import WorkLibrary

ORIGINAL = 'An establishedword appears in this sufficiently long example of a published passage.'
ALTERNATE = ORIGINAL.replace('establishedword','established word')


class TranscriptionComparisonTests(unittest.TestCase):
    def setUp(self):
        self.original = document(url=MIA_STATE_URL,digest=sha(b'original'),blocks=[ORIGINAL])
        self.alternate = self.mirror(ALTERNATE)
        self.target = exact_target(self.original,0,len(ORIGINAL))
        self.case = {'id':'fixture','wiki_title':'Fixture','citation_ref':ref(RAW)}
        self.case['source_key'] = source_identity(RAW,self.case['citation_ref'])['source_key']
        self.candidate = {'url':self.original.url,'sha256':self.original.digest,
                          'revision':self.original.revision,'char_start':0,'char_end':len(ORIGINAL),
                          'text_sha256':sha(ORIGINAL.encode())}
        self.result = {'id':'fixture','candidates':[self.candidate]}

    def mirror(self,text):
        return document(url=STATE_MIRROR_URL,digest=sha(text.encode()),revision=7,
                        blocks=['Before. '+text+' After.'])

    def load(self,url,lang):
        self.assertEqual(lang,'en')
        return {MIA_STATE_URL:self.original, STATE_MIRROR_URL:self.alternate}[url]

    def test_unique_literal_variant_has_exact_identity_not_corrected_text(self):
        result = compare_spacing(self.original,self.target,self.alternate)
        self.assertEqual(result['status'],'spacing_variant_requires_review')
        self.assertEqual(result['added_word_boundaries'],1)
        self.assertEqual(result['original_target'],self.target)
        target = result['alternate_target']
        self.assertEqual(target,exact_target(self.alternate,8,8+len(ALTERNATE)))
        self.assertIsNone(result['semantic_equivalence'])
        self.assertFalse(result['auto_publish'])
        self.assertNotIn(ORIGINAL,json.dumps(result))
        self.assertNotIn(ALTERNATE,json.dumps(result))

    def test_rejects_other_changes_ambiguity_and_uncertain_edges(self):
        for text,status in (
            (ORIGINAL,'no_added_word_boundaries'),
            (ALTERNATE.replace('published','printed'),'no_literal_spacing_match'),
            (ALTERNATE+' '+ALTERNATE,'ambiguous_literal_spacing_match'),
            (ALTERNATE.replace('this sufficiently','thissufficiently'),'mixed_spacing_differences'),
        ):
            with self.subTest(status=status):
                self.assertEqual(compare_spacing(self.original,self.target,self.mirror(text))['status'],status)
        clipped = exact_target(self.original,1,len(ORIGINAL))
        self.assertEqual(compare_spacing(self.original,clipped,self.alternate)['status'],
                         'source_boundary_uncertain')
        changed = {**self.target,'text_sha256':'0'*64}
        with self.assertRaises(ValueError):
            compare_spacing(self.original,changed,self.alternate)
        with self.assertRaisesRegex(ValueError,'undocumented_transcription_pair'):
            compare_spacing(self.original,self.target,document(blocks=[ALTERNATE]))
        self.assertEqual(compare_documented(self.target,
            lambda url,lang:self.original if url==MIA_STATE_URL else {'status':'unavailable'})['status'],
            'alternate_unavailable')

    def ordinary(self):
        return build_campaign_packet([(self.case,self.result)],lambda *_:RAW,self.load,COMPACT)

    def test_packet_adds_alternative_keeps_original_and_roundtrips(self):
        ordinary = self.ordinary()
        original_digest = fingerprint(ordinary)
        packet = supplement_packet(ordinary,self.load)
        self.assertEqual(fingerprint(ordinary),original_digest)
        self.assertTrue(seed_inventory(ordinary) < seed_inventory(packet))
        self.assertEqual(packet['targets']['p1'],ordinary['targets']['p1'])
        self.assertEqual(packet['payload']['passages']['p1'],ordinary['payload']['passages']['p1'])
        row = packet['payload']['groups'][0]['chapters'][0]['citations'][0]
        note = row['transcription_variants'][0]
        self.assertEqual(note['original_candidate'],'p1')
        self.assertIn(note['alternate_candidate'],row['candidates'])
        pid = note['alternate_candidate']
        self.assertEqual(packet['payload']['passages'][pid]['text'],ALTERNATE)
        self.assertIn('Never silently correct',review_view(packet)['instructions'])
        with TemporaryDirectory() as tmp, WorkLibrary(Path(tmp)/'works.sqlite3') as lib:
            path = Path(tmp)/'private.zip'
            digest = fingerprint(packet)
            write_bundle(path,[(digest,packet)],'a'*64)
            self.assertEqual(read_packet(path,1),review_view(packet))
            registry = CitationRegistry(lib.db)
            reply = {'schema':REPLY,'packet_sha256':digest,'reviewer':'synthetic lifecycle test',
                     'decisions':[{'id':row['id'],'decision':'complete_equivalent',
                                   'candidate':pid,'ranges':[[0,len(ALTERNATE)]]}]}
            self.assertEqual(apply_review(packet,reply,digest,registry,
                                          lambda *_:RAW,self.load)['recorded'],1)
            restored = registry.reconstruct(RAW,self.case['citation_ref'],self.alternate)
            self.assertEqual(restored['fragments'][0]['text'],ALTERNATE)
            self.assertFalse(restored['auto_publish'])

    def test_optional_comparison_never_excludes_original_at_payload_limit(self):
        packet = self.ordinary()
        cap = len(encode(packet['payload']))
        supplemented = supplement_packet(packet,self.load,max_payload_chars=cap)
        self.assertEqual(seed_inventory(supplemented),seed_inventory(packet))
        self.assertLessEqual(len(encode(supplemented['payload'])),cap)

    def test_progressive_fallback_retains_comparison_option_after_restart(self):
        with TemporaryDirectory() as tmp, WorkLibrary(Path(tmp)/'works.sqlite3') as lib:
            inventory = {'cases':[self.case],'pages':[],'manifest_sha256':'a'*64}
            audit = SimpleNamespace(document=self.load,publication={})
            campaign = Campaign(lib,inventory)
            campaign.record('fixture',CANDIDATES,{'result':self.result})
            digest,packet = campaign.prepare(lambda *_:RAW,audit,progressive=True,
                                             compare_transcriptions=True)
            self.assertTrue(packet['campaign_compare_transcriptions'])
            self.assertEqual(packet['payload']['transcription_comparison']['additional_candidate_offers'],1)
            reply = {'schema':REPLY,'packet_sha256':digest,'reviewer':'synthetic test',
                     'decisions':[{'id':'q1','decision':'uncertain','candidate':None,'ranges':[]}]}
            campaign.apply(reply,lambda *_:RAW,audit)
            with WorkLibrary(lib.path) as reopened:
                c = Campaign(reopened,inventory)
                _,wide = c.prepare(lambda *_:RAW,audit)
                self.assertEqual(wide['payload']['progressive_review']['stage'],'expanded_fallback')
                self.assertTrue(wide['campaign_compare_transcriptions'])
                self.assertEqual(wide['payload']['transcription_comparison']['additional_candidate_offers'],1)
