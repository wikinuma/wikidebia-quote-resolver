"""Shared lookup/revalidation/search path for one pinned MIA occurrence.

The campaign persists only evidence. Translation callers may separately receive
the exact reconstructed published fragments for private editorial review.
No rights approval, generated translation, or publication is performed.
"""
from collections import Counter

from citation_registry import source_identity
from grouped_review import candidate_queue
from navigation import error_kind
from pilot_full import redact_text

CANDIDATES = 'review_pending'
REVIEWED = 'reviewed_receipt_rights_unreviewed'
RETRY = 'retry_documentary'
INSUFFICIENT = 'insufficient_evidence'


def classify(row, events=()):
    if candidate_queue(row):
        return CANDIDATES
    # The resolver's inventories are bounded. Neither a missing route nor
    # exhausting the current candidates proves absence of a translation.
    if (any(e.get('status') == 'error' for e in events)
            or row.get('navigation_status') == 'input_unavailable'
            or row.get('status', '').endswith('_unavailable')):
        return RETRY
    return INSUFFICIENT


def resolve_occurrence(case, registry, retrieval, load_raw, audit, *, search_missing=True):
    """Return (state, metadata-only evidence, private reconstructed passage).

    Revalidate the complete source identity and the stored target before reuse.
    A failed reconstruction never supplies old text. Conflicting receipts do
    not trigger a silent replacement. The caller retains occurrence identity.
    """
    cid = case['id']
    ref = case['citation_ref']
    start = len(retrieval.client.events)
    try:
        raw = load_raw(case['wiki_title'], ref['revision'])
        if source_identity(raw, ref)['source_key'] != case['source_key']:
            raise ValueError('inventory_source_identity_mismatch')
        found = registry.lookup(raw, ref)
        if found['status'] == 'ambiguous_reviewed_passages':
            return INSUFFICIENT, {'reason': 'conflicting_reviewed_passages'}, None
        if found['status'] == 'reviewed_receipt_found':
            url = found['receipt']['target']['document']['url']
            doc = audit.document(url, 'en')
            restored = registry.reconstruct(raw, ref, doc)
            if restored['status'] == 'reviewed_passage_reconstructed':
                evidence = {
                    'origin': 'revalidated_existing_receipt',
                    'receipt_sha256': restored['receipt']['entry_sha256'],
                    'publication': audit.publication.get(('en', url), {}),
                    'rights': 'unreviewed'}
                return REVIEWED, evidence, restored
        if not search_missing:
            if found['status'] == 'reviewed_receipt_found':
                return RETRY, {'reason': 'stored_passage_not_revalidated'}, None
            return INSUFFICIENT, {'reason': 'prepared_receipt_not_found_search_not_run'}, None
        row = retrieval.retrieve(case)
        if row.get('id') != cid or row.get('status') == 'ai_translation_required':
            raise ValueError('retrieval_inventory_mismatch')
        events = retrieval.client.events[start:]
        state = classify(row, events)
        if found['status'] == 'reviewed_receipt_found' and state == INSUFFICIENT:
            state = RETRY
        evidence = {'result': redact_text(row),
                    'request_error_kinds': dict(Counter(
                        e.get('error_kind', 'unknown') for e in events
                        if e.get('status') == 'error')),
                    'prior_registry_status': found['status']}
        return state, evidence, None
    except Exception as exc:
        return RETRY, {'reason': error_kind(exc)}, None
