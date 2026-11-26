"""Replay directly read bilingual reference intervals against review windows.

References are explicit editorial judgments with exact documentary evidence,
not labels inferred from candidate rank or from synthetic truncations. This
measures reference containment, not the correctness of a reviewer's response.
No reference text, production receipt or rights authorization is emitted.
"""
from collections import Counter
import json

from citation_registry import target_identity
from evidence_audit import read_span, valid_digest
from resolver import norm

SCHEMA = 'wikidebia-reviewed-boundary-references-1'
SCOPE = 'convenience_selected_positive_documentary_diagnostics_not_independent_holdout'
SPAN_FIELDS = {'url','sha256','revision','char_start','char_end','text_sha256'}
ENTRY_FIELDS = {'case_id','citation_ref','fr','en','reviewer','decision','review_note',
                'text_integrity','rights','reuse_authorized'}
INTEGRITY = {'no_visible_transcription_defect','transcription_defect_observed'}


def load_references(path, report_sha):
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(manifest,dict) or
            set(manifest) != {'schema','report_sha256','scope','entries'} or
            manifest['schema'] != SCHEMA or manifest['scope'] != SCOPE or
            not valid_digest(report_sha) or manifest['report_sha256'] != report_sha or
            not isinstance(manifest['entries'],list) or not manifest['entries']):
        raise ValueError('invalid_boundary_reference_manifest')
    seen = set()
    for e in manifest['entries']:
        if (not isinstance(e,dict) or set(e) != ENTRY_FIELDS or
                not isinstance(e['case_id'],str) or not e['case_id'] or e['case_id'] in seen or
                not isinstance(e['citation_ref'],dict) or
                e['decision'] != 'equivalent_contiguous_extent' or
                e['text_integrity'] not in INTEGRITY or
                e['rights'] != 'unreviewed' or e['reuse_authorized'] is not False or
                any(not isinstance(e[k],str) or not e[k].strip()
                    for k in ('reviewer','review_note'))):
            raise ValueError('invalid_boundary_reference_entry')
        for span in (e['fr'],e['en']):
            if (not isinstance(span,dict) or set(span) != SPAN_FIELDS or
                    not isinstance(span['url'],str) or
                    not valid_digest(span['sha256']) or not valid_digest(span['text_sha256']) or
                    type(span['char_start']) is not int or type(span['char_end']) is not int or
                    not 0 <= span['char_start'] < span['char_end'] or
                    (span['revision'] is not None and
                     (type(span['revision']) is not int or span['revision'] <= 0))):
                raise ValueError('invalid_boundary_reference_span')
        seen.add(e['case_id'])
    return manifest['entries']


def revalidate_reference(entry, case, result, audit):
    if (entry['case_id'] != case['id'] or result['id'] != case['id'] or
            entry['citation_ref'] != case['citation_ref']):
        raise ValueError('reviewed_boundary_input_identity_changed')
    source = result.get('source',{})
    matches = [hit for hit in source.get('occurrences',[])
               if all(hit.get(k) == v for k,v in entry['fr'].items())]
    if source.get('status') != 'exact_in_examined_documents' or len(matches) != 1:
        raise ValueError('reviewed_boundary_source_not_bound_to_exact_occurrence')
    receipt, quote = audit.input(case)
    if (receipt.get('status') != 'verified_pinned_citation' or
            receipt.get('revision') != case['citation_ref']['revision'] or
            receipt.get('quote_sha256') != case['citation_ref']['quote_sha256'] or
            receipt.get('template_sha256') != case['citation_ref']['template_sha256']):
        raise ValueError('reviewed_boundary_pinned_input_unavailable')
    fr, en = entry['fr'], entry['en']
    for lang, span in (('fr',fr),('en',en)):
        if audit.span(span,lang).get('status') != 'verified_document_and_span':
            raise ValueError('reviewed_boundary_document_or_span_changed')
    fdoc = audit.document(fr['url'],'fr')
    edoc = audit.document(en['url'],'en')
    if (isinstance(fdoc,dict) or isinstance(edoc,dict) or
            fdoc.url != fr['url'] or fdoc.revision != fr['revision']):
        raise ValueError('reviewed_boundary_document_unavailable')
    _,_,text = read_span(fdoc,fr)
    if not norm(quote) or norm(quote) != norm(text):
        raise ValueError('reviewed_boundary_source_not_equivalent_to_pinned_input')
    return target_identity(edoc,en)


def window_coverage(case, target, packets):
    """All coordinates are in the exact same verified document version.

    A union of unrelated candidate windows is not a single usable passage.
    """
    offered = []
    count = 0
    for packet in packets:
        for source in packet['sources'].values():
            for o in source['occurrences']:
                if o['case_id'] != case['id']:
                    continue
                if o['ref'] != case['citation_ref'] or o['wiki_title'] != case['wiki_title']:
                    raise ValueError('reviewed_boundary_packet_identity_changed')
                count += 1
                offered.extend(packet['targets'][pid] for pid in source['candidates'])
    if count > 1:
        raise ValueError('reviewed_boundary_duplicate_packet_occurrence')
    if not count:
        return 'not_offered'
    lo,hi = target['char_start'],target['char_end']
    same = [t for t in offered if t['document'] == target['document']]
    if any(t['char_start'] == lo and t['char_end'] == hi for t in same):
        return 'exact_window'
    if any(t['char_start'] <= lo and t['char_end'] >= hi for t in same):
        return 'contained_requires_subspan'
    if any(t['char_start'] < hi and t['char_end'] > lo for t in same):
        return 'partial_overlap'
    return 'different_or_disjoint'


def reviewed_boundary_summary(entries, items, audit, arms):
    cases = {case['id']:(case,result) for case,result in items}
    if len(cases) != len(items) or not entries or not arms:
        raise ValueError('invalid_reviewed_boundary_inputs')
    counts = {mode:Counter() for mode in arms}
    integrity = Counter()
    for entry in entries:
        if entry['case_id'] not in cases:
            raise ValueError('reviewed_boundary_reference_outside_cohort')
        case,result = cases[entry['case_id']]
        target = revalidate_reference(entry,case,result,audit)
        integrity[entry['text_integrity']] += 1
        for mode,packets in arms.items():
            counts[mode][window_coverage(case,target,packets)] += 1
    return {'scope':SCOPE,
            'documentary_references_revalidated':len(entries),
            'reference_text_integrity':dict(integrity),
            'arms':{mode:{'outcomes':dict(stats),
                          'complete_reference_available':stats['exact_window']+
                                                         stats['contained_requires_subspan']}
                    for mode,stats in counts.items()},
            'actual_reviewer_accuracy':None,'representative_accuracy':None,
            'rights_approved':0,'auto_publish':False}
