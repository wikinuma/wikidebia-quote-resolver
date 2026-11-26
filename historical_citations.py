"""Revalidate only the 38 historically favourable decisions (37 cases).

Frozen reports/ledgers are integrity-checked before reading any public sources.
Record the old explicit editorial decision, not a new semantic judgment.
A same-revision Wikirouge HTML change can receive a NEW exact proof only if
the entire reviewed candidate and all selected fragments are byte-identical.
No negative conclusion, rights approval, or publication is authorized.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import tempfile

from citation_registry import (CitationRegistry, DECISIONS, source_identity,
                               target_identity, selected_fragments, fingerprint)
from evidence_audit import Auditor, load_report, read_span, span_receipt
from grouped_review import pinned_loader
from navigation import error_kind
from pilot_full import PilotClient
from resolver import ROOT, norm, sha
from work_library import WorkLibrary


def unique_index(rows, key):
    result = {}
    for row in rows:
        k = row[key]
        if k in result:
            raise ValueError('duplicate_historical_case')
        result[k] = row
    return result


def load_json(path, expected=None):
    data = Path(path).read_bytes()
    digest = sha(data)
    if expected is not None and expected != digest:
        raise ValueError('historical_artifact_digest_mismatch')
    return json.loads(data), digest


def receipt_candidate(receipt):
    if (receipt['status'] != 'verified_document_and_span'
            or receipt['actual_text_sha256'] != receipt['expected_text_sha256']
            or receipt['document_sha256'] != receipt['recorded_document_sha256']):
        raise ValueError('unverified_historical_candidate')
    return {'url': receipt['url'], 'revision': receipt['revision'],
            'sha256': receipt['document_sha256'],
            'char_start': receipt['char_start'], 'char_end': receipt['char_end'],
            'text_sha256': receipt['actual_text_sha256']}


def first_candidate(row, decision, ref):
    if (row['status'] != 'review_revalidated' or row['decision'] != decision['decision']
            or row['citation_ref'] != ref):
        raise ValueError('historical_first_receipt_mismatch')
    candidate = {'url': row['target_url'], 'revision': row['target_revision'],
                 'sha256': row['document_sha256'], 'text_sha256': row['target_text_sha256'],
                 'char_start': row['char_start'], 'char_end': row['char_end']}
    fragments = decision['fragments']
    if [[f['relative_start'], f['relative_end'], f['text_sha256']] for f in row['fragments']] != fragments:
        raise ValueError('historical_fragment_list_mismatch')
    for f in row['fragments']:
        if (f['char_start'] != candidate['char_start'] + f['relative_start']
                or f['char_end'] != candidate['char_start'] + f['relative_end']):
            raise ValueError('historical_fragment_coordinate_mismatch')
    return candidate, fragments


def second_candidate(row, decision, ref):
    if row['status'] != 'ready' or row['citation_ref'] != ref:
        raise ValueError('historical_second_receipt_mismatch')
    matches = {}
    for c in row['candidates']:
        receipt = c['receipt']
        if receipt.get('actual_text_sha256') == decision['candidate_sha256']:
            candidate = receipt_candidate(receipt)
            matches[fingerprint(candidate)] = candidate
    if len(matches) != 1:
        raise ValueError('historical_second_candidate_not_unique')
    return next(iter(matches.values())), [
        [f['relative_start'], f['relative_end'], f['text_sha256']]
        for f in decision['fragments']]


def read_candidate(audit, historical, lang='en'):
    """Do not turn changed HTML into a claim of identical historical bytes."""
    doc = audit.document(historical['url'], lang)
    if isinstance(doc, dict):
        raise ValueError('historical_document_unavailable')
    if historical['url'] != doc.url or historical.get('revision') != doc.revision:
        raise ValueError('historical_document_identity_changed')
    proof = span_receipt(doc, historical)
    if proof['status'] == 'verified_document_and_span':
        return doc, historical.copy(), 'exact_historical_document_and_span'
    # Only an actual positive wiki revision, plus exactly identical reviewed
    # span, can establish a new proof. MIA has no such pinned revision.
    if (proof['status'] == 'span_matches_changed_document'
            and type(doc.revision) is int and doc.revision > 0):
        new = {**historical, 'sha256': doc.digest}
        if span_receipt(doc, new)['status'] != 'verified_document_and_span':
            raise ValueError('historical_rebound_proof_invalid')
        return doc, new, 'new_proof_same_revision_exact_span'
    raise ValueError(proof['status'])


def verify_and_record(registry, raw, ref, audit, candidate, fragments,
                      decision, provenance):
    source = source_identity(raw, ref)
    doc, current, evidence = read_candidate(audit, candidate)
    target = target_identity(doc, current)
    ranges = [[f[0], f[1]] for f in fragments]
    verified = selected_fragments(source, target, doc, decision, ranges)
    if [f['text_sha256'] for f in verified] != [f[2] for f in fragments]:
        raise ValueError('historical_selected_fragment_hash_changed')
    # Bind the original review and full original proof to this new receipt.
    # Neither this hash nor the reviewer label claims a fresh semantic review.
    review = {'source_key': source['source_key'], 'candidate_key': target['candidate_key'],
              'review_packet_sha256': fingerprint(provenance),
              'reviewer': 'Historical ChatGPT review, evidence revalidated',
              'decision': decision, 'ranges': ranges}
    receipt = registry.record(raw, ref, doc, current, review)
    return {'status': 'historical_decision_recorded', 'evidence': evidence,
            'source_key': source['source_key'], 'candidate_key': target['candidate_key'],
            'historical_document_sha256': candidate['sha256'],
            'current_document_sha256': doc.digest, 'target_url': doc.url,
            'target_revision': doc.revision, 'fragments': verified,
            'receipt_sha256': receipt['entry_sha256'],
            'fresh_semantic_review': False}


def matching_candidate(candidates, url, digest):
    matches = {}
    for c in candidates:
        if c.get('url') == url and c.get('text_sha256') == digest:
            # Ignore method/rank, not actual evidence identity.
            key = tuple(c.get(k) for k in ('url', 'sha256', 'revision', 'text_sha256',
                                          'char_start', 'char_end', 'block', 'span'))
            matches[key] = c
    if len(matches) != 1:
        raise ValueError('historical_individual_candidate_not_unique')
    return next(iter(matches.values()))


def individual_candidate(case, result, decision, raw, audit):
    from chapter_quality import primary
    from navigation import extract_citation_ref
    ref = case['citation_ref']
    if (ref['revision'] != decision['wiki_revision']
            or ref['quote_sha256'] != decision['quote_sha256']
            or decision.get('reuse_authorized') is not False):
        raise ValueError('historical_individual_input_mismatch')
    src = matching_candidate(result['source']['occurrences'], decision['fr_url'],
                             decision['fr_span_sha256'])
    fr, _, _ = read_candidate(audit, src, 'fr')
    _, _, fragment = read_span(fr, src)
    actual = extract_citation_ref(raw, ref, include_text=True, include_link_policy=True)
    if norm(fragment) != norm(actual['text']):
        raise ValueError('historical_individual_source_mismatch')
    target = matching_candidate(primary(result), decision['en_url'], decision['en_span_sha256'])
    doc, current, _ = read_candidate(audit, target)
    lo, hi, text = read_span(doc, current)
    # Convert a legacy block span into exact character coordinates.
    return {**target, 'char_start': lo, 'char_end': hi}, [[0, hi-lo, sha(text.encode())]]


def revalidate(registry, cohorts, first, first_rows, second, second_rows, individual,
               provenance, audit, load_raw):
    cases = {}
    for report in cohorts:
        for c, pair in zip(report['sample'], report['pairs']):
            if c['id'] in cases or pair['id'] != c['id']:
                raise ValueError('historical_cohort_identity_mismatch')
            cases[c['id']] = (c, pair['refined']['result'])
    frows = unique_index(first_rows['cases'], 'case_id')
    srows = unique_index(second_rows['cases'], 'id')
    fdec = unique_index(first['entries'], 'case_id')
    sdec = unique_index(second['cases'], 'case_id')
    idec = unique_index(individual['entries'], 'case_id')
    tasks = [('first', d) for d in fdec.values() if d['decision'] in DECISIONS]
    tasks += [('second', d) for d in sdec.values() if d['decision'] in DECISIONS]
    tasks += [('individual', d) for d in idec.values()
              if d['decision'] == 'equivalent_complete_passage']
    counts = Counter(origin for origin, _ in tasks)
    if counts != {'first': 29, 'second': 8, 'individual': 1} or len({d['case_id'] for _, d in tasks}) != 37:
        raise ValueError('unexpected_favourable_historical_scope')
    if set(frows) != set(cases) or set(srows) != set(sdec):
        raise ValueError('historical_report_cohort_mismatch')
    rows, accepted_sources = [], {}
    for origin, decision in tasks:
        cid = decision['case_id']
        case, result = cases[cid]
        ref = case['citation_ref']
        row = {'case_id': cid, 'origin': origin, 'decision': decision['decision'],
               'source_ref': ref, 'rights': 'unreviewed', 'auto_publish': False}
        try:
            raw = load_raw(case['wiki_title'], ref['revision'])
            source = source_identity(raw, ref)
            if origin == 'first':
                candidate, fragments = first_candidate(frows[cid], decision, ref)
            elif origin == 'second':
                candidate, fragments = second_candidate(srows[cid], decision, ref)
            else:
                candidate, fragments = individual_candidate(case, result, decision, raw, audit)
            label = 'complete_equivalent' if origin == 'individual' else decision['decision']
            prov = {'origin': origin, 'ledger': provenance[origin], 'case_id': cid,
                    'decision': decision, 'historical_candidate': candidate, 'source_ref': ref}
            row.update(verify_and_record(registry, raw, ref, audit, candidate, fragments, label, prov))
            accepted_sources[source['source_key']] = (raw, ref)
        except (ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
            # A failed proof is not evidence of absence or permission to use a
            # different edition. Keep a redacted reason and proceed.
            row.update(status='re_review_required', error=error_kind(exc))
            if isinstance(exc, ValueError) and str(exc).replace('_', '').isalnum() and len(str(exc)) < 100:
                row['reason'] = str(exc)
        rows.append(row)
    lookup = [registry.lookup(raw, ref) for raw, ref in accepted_sources.values()]
    summary = {'historical_decisions': len(tasks),
               'distinct_cases': len({d['case_id'] for _, d in tasks}),
               'recorded_decisions': sum(row['status'] == 'historical_decision_recorded' for row in rows),
               'distinct_cases_recorded': len({row['case_id'] for row in rows
                                               if row['status'] == 'historical_decision_recorded'}),
               'distinct_source_keys_recorded': len(accepted_sources),
               'lookup_statuses': dict(Counter(x['status'] for x in lookup)),
               'evidence': dict(Counter(row.get('evidence', row.get('reason', row['status'])) for row in rows)),
               'fresh_semantic_reviews': 0, 'rights_approved': 0}
    return {'schema': 'wikidebia-historical-prepared-audit-1', 'summary': summary,
            'cases': rows, 'provenance': provenance, 'auto_publish': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--development', type=Path, required=True)
    p.add_argument('--holdout', type=Path, required=True)
    p.add_argument('--first-registry', type=Path, required=True)
    p.add_argument('--second-report', type=Path, required=True)
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    first, fd = load_json(ROOT/'first-candidate-review.json')
    second, sd = load_json(ROOT/'second-pass-review-ledger.json')
    individual, idg = load_json(ROOT/'reviewed-quotations.json')
    first_rows, frd = load_json(a.first_registry, second['previous_registry_sha256'])
    second_rows, srd = load_json(a.second_report, second['report_sha256'])
    if (first['schema'] != 'wikidebia-first-candidate-review-1'
            or second['schema'] != 'wikidebia-second-pass-review-ledger-1'
            or individual['schema'] != 'wikidebia-reviewed-quotation-1'
            or first_rows['schema'] != 'wikidebia-revalidated-review-registry-1'
            or second_rows['schema'] != 'wikidebia-second-pass-1'
            or second_rows['status'] != 'candidates_prepared'
            or first_rows['review_sha256'] != fd
            or first_rows['chapter_report_sha256'] != first['chapter_report_sha256']
            or first_rows['reports'] != first['reports']
            or second_rows['reports'] != first['reports']
            or second_rows['registry_sha256'] != frd
            or any(x.get('auto_publish') is not False for x in
                   (first, second, first_rows, second_rows))):
        raise ValueError('historical_provenance_mismatch')
    cohorts = [load_report(path, digest, 'refined')
               for path, digest in zip((a.development, a.holdout), first['reports'])]
    provenance = {'first': {'ledger_sha256': fd, 'report_sha256': frd,
                            'packet_sha256': first['reviewed_packet_sha256']},
                  'second': {'ledger_sha256': sd, 'report_sha256': srd,
                             'packet_sha256': second['packet_sha256']},
                  'individual': {'ledger_sha256': idg, 'cohort_reports': first['reports']}}
    with tempfile.TemporaryDirectory(prefix='historical-proof-cache-') as tmp, WorkLibrary(a.db) as lib:
        client = PilotClient(Path(tmp), budget=120)
        audit = Auditor(client)
        report = revalidate(CitationRegistry(lib.db), cohorts, first, first_rows, second,
                            second_rows, individual, provenance, audit, pinned_loader(client))
        report['summary']['network_requests'] = client.requests
        report['summary']['network_body_bytes'] = client.network_body_bytes
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2))
    # Unavailable/changed evidence is reported, not a program failure.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
