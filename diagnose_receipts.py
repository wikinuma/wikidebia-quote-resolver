"""Diagnose prior span receipt failures without relaxing strict verification.

Read only unique public English Wikirouge documents, publish hashes/coordinates
only, and distinguish rendering drift from revision or span mismatch.
Never accept semantic equivalence or reuse rights.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import tempfile
import time

from alignment import text_and_starts
from evidence_audit import Auditor, document_provider, span_receipt, valid_digest
from pilot_full import PilotClient
from resolver import ROOT, sha


def diagnose(entries, audit):
    rows = []
    for entry in entries:
        if (document_provider(entry['url'], 'en') != 'wikirouge'
                or type(entry['revision']) is not int or entry['revision'] <= 0
                or not valid_digest(entry['sha256']) or not valid_digest(entry['text_sha256'])):
            raise ValueError('invalid_pinned_receipt')
        doc = audit.document(entry['url'], 'en')
        base = {'case_id': entry['case_id'], 'url': entry['url'],
                'expected_revision': entry['revision'], 'expected_document_sha256': entry['sha256'],
                'expected_text_sha256': entry['text_sha256'], 'char_start': entry['char_start'],
                'char_end': entry['char_end'], 'strictly_verified': False,
                'equivalence': 'unreviewed', 'rights': 'unreviewed', 'auto_publish': False}
        if isinstance(doc, dict):
            rows.append({**base, 'diagnosis': 'document_unavailable', 'error': doc.get('error')})
            continue
        text, _ = text_and_starts(doc)
        base.update(observed_revision=doc.revision, observed_document_sha256=doc.digest,
                    observed_extracted_text_sha256=sha(text.encode()), observed_characters=len(text))
        try:
            receipt = span_receipt(doc, entry)
        except ValueError:
            rows.append({**base, 'diagnosis': 'invalid_span_coordinates'})
            continue
        status = receipt['status']
        diagnosis = ('same_revision_exact_span_changed_html'
                     if status == 'span_matches_changed_document' else status)
        rows.append({**base, 'diagnosis': diagnosis, 'receipt': receipt,
                     'strictly_verified': status == 'verified_document_and_span'})
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture', type=Path, default=ROOT/'experiments/mia-nonreproduced-receipts.json')
    p.add_argument('--out', type=Path, default=ROOT/'reports-live/mia-receipt-diagnostic.json')
    a = p.parse_args()
    source = json.loads(a.fixture.read_bytes())
    if source['schema'] != 'wikidebia-receipt-diagnostic-input-1':
        raise ValueError('unknown_input_schema')
    entries = source['entries']
    if not 1 <= len(entries) <= 30:
        raise ValueError('diagnostic_case_limit')
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='mia-receipt-diagnostic-') as tmp:
        client = PilotClient(Path(tmp), budget=20)
        rows = diagnose(entries, Auditor(client))
        summary = {'entries': len(rows), 'documents': len({e['url'] for e in entries}),
                   'diagnoses': dict(Counter(r['diagnosis'] for r in rows)),
                   'strictly_verified': sum(r['strictly_verified'] for r in rows),
                   'network_requests': client.requests, 'decoded_network_body_bytes': client.network_body_bytes,
                   'elapsed_seconds': round(time.monotonic()-start, 3), 'model_calls': 0,
                   'additional_equivalences': 0, 'rights_approved': 0}
        report = {'schema': 'wikidebia-receipt-diagnostic-1',
                  'fixture_sha256': sha(a.fixture.read_bytes()),
                  'source_report_sha256': source['source_report_sha256'],
                  'summary': summary, 'cases': rows, 'auto_publish': False}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
