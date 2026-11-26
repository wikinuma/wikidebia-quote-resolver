import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from citation_registry import target_identity
from evidence_audit import span_receipt
from grouped_review import build_packet
from reviewed_boundaries import (SCHEMA, SCOPE, load_references,
                                 reviewed_boundary_summary, window_coverage)
from test_citation_registry import FR, EN, RAW, QUOTE, document, ref
from resolver import sha


def span(doc, lo, hi):
    text = '\n\n'.join(doc.blocks)
    return {'url':doc.url,'sha256':doc.digest,'revision':doc.revision,
            'char_start':lo,'char_end':hi,'text_sha256':sha(text[lo:hi].encode())}


class ReviewedBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fr = document(url=FR,blocks=[QUOTE])
        self.en = document(blocks=['Before. Exact equivalent extent. After.'])
        self.case = {'id':'test','wiki_title':'Test','citation_ref':ref(RAW)}
        self.source = span(self.fr,0,len(QUOTE))
        self.target = span(self.en,8,32)
        self.entry = {'case_id':'test','citation_ref':self.case['citation_ref'],
                      'fr':self.source,'en':self.target,'reviewer':'test-editor',
                      'decision':'equivalent_contiguous_extent',
                      'review_note':'Synthetic lifecycle fixture, not a bilingual judgment.',
                      'text_integrity':'transcription_defect_observed',
                      'rights':'unreviewed','reuse_authorized':False}
        self.result = {'id':'test','source':{'status':'exact_in_examined_documents',
                       'occurrences':[self.source]},
                       'candidates':[span(self.en,0,len(self.en.blocks[0]))]}
        self.audit = SimpleNamespace(
            document=lambda url,lang: self.fr if lang == 'fr' else self.en,
            input=lambda case: ({'status':'verified_pinned_citation',
                                  **self.case['citation_ref']},QUOTE),
            span=lambda entry,lang: span_receipt(self.fr if lang == 'fr'
                                                 else self.en,entry))

    def packet(self, start, end):
        result = {**self.result,'candidates':[span(self.en,start,end)]}
        return build_packet([(self.case,result)],lambda *_:RAW,lambda *_:self.en,
                            distinct_occurrences=True)

    def test_actual_windows_distinguish_exact_subspan_partial_and_absence(self):
        target = target_identity(self.en,self.target)
        for label,packets in {
            'exact_window':[self.packet(8,32)],
            'contained_requires_subspan':[self.packet(0,39)],
            'partial_overlap':[self.packet(10,32)],
            'different_or_disjoint':[self.packet(0,8)],
            'not_offered':[],
        }.items():
            self.assertEqual(window_coverage(self.case,target,packets),label)
        # Separate partial windows must not be interpreted as one complete candidate.
        p = build_packet([(self.case,{**self.result,'candidates':[
            span(self.en,8,20),span(self.en,20,32)]})],lambda *_:RAW,
            lambda *_:self.en,distinct_occurrences=True)
        self.assertEqual(window_coverage(self.case,target,[p]),'partial_overlap')
        with self.assertRaisesRegex(ValueError,'duplicate_packet_occurrence'):
            window_coverage(self.case,target,[p,p])

    def test_summary_does_not_claim_actual_accuracy_or_reuse_rights(self):
        report = reviewed_boundary_summary([self.entry],[(self.case,self.result)],
                                           self.audit,{'compact':[self.packet(0,39)]})
        self.assertEqual(report['documentary_references_revalidated'],1)
        self.assertEqual(report['arms']['compact']['complete_reference_available'],1)
        self.assertEqual(report['reference_text_integrity'],
                         {'transcription_defect_observed':1})
        self.assertIsNone(report['actual_reviewer_accuracy'])
        self.assertIsNone(report['representative_accuracy'])
        self.assertEqual(report['rights_approved'],0)
        self.assertFalse(report['auto_publish'])
        encoded = json.dumps(report)
        for forbidden in (QUOTE,self.en.blocks[0],FR,EN,self.target['text_sha256']):
            self.assertNotIn(forbidden,encoded)

    def test_source_mismatch_and_changed_target_fail_closed(self):
        args = ([(self.case,self.result)],self.audit,{'compact':[]})
        changed = {**self.entry,'en':{**self.target,'text_sha256':'0'*64}}
        with self.assertRaisesRegex(ValueError,'document_or_span_changed'):
            reviewed_boundary_summary([changed],*args)
        self.audit.input = lambda case: ({'status':'verified_pinned_citation',
                                         **self.case['citation_ref']},'Changed quote')
        with self.assertRaisesRegex(ValueError,'source_not_equivalent'):
            reviewed_boundary_summary([self.entry],*args)

    def test_manifest_is_bound_to_cohort_and_rejects_duplicate_or_authorized_entry(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'references.json'
            manifest = {'schema':SCHEMA,'report_sha256':'a'*64,
                        'scope':SCOPE,'entries':[self.entry]}
            def write(entries):
                path.write_text(json.dumps({**manifest,'entries':entries}),encoding='utf-8')
            write([self.entry])
            self.assertEqual(load_references(path,'a'*64),[self.entry])
            with self.assertRaisesRegex(ValueError,'invalid_boundary_reference_manifest'):
                load_references(path,'b'*64)
            for entries in ([self.entry,self.entry],
                            [{**self.entry,'reuse_authorized':True}],
                            [{**self.entry,'en':{**self.target,'text':'forbidden'}}]):
                write(entries)
                with self.assertRaises(ValueError):
                    load_references(path,'a'*64)
