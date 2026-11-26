"""Memory-first retrieval for exact, previously reviewed MIA citations.

The caller supplies a fresh documentary auditor (new HTTP cache per run,
shared across cases). Never treat a persistent HTTP cache as fresh evidence.
A hit reuses a historical semantic judgment, not permission to publish.
Negative, ambiguous, incomplete or stale records never suppress retrieval.
"""
from alignment import text_and_starts
from evidence_audit import span_receipt
from navigation import canonical_mia, error_kind
from resolver import sha
from review_memory import ACCEPT, lookup, proof


def latest_proof(row):
    # Never fall back to an earlier accepted decision if superseded.
    for key in ('restored_review', 'second', 'first'):
        if key in row:
            p = row[key]
            if p['decision'] != row['latest_decision']:
                raise ValueError('historical_decision_mismatch')
            if p['decision'] not in ACCEPT:
                return None
            if p['proof_status'] != 'historical_review_receipt_not_fresh_verification':
                raise ValueError('unexpected_proof_status')
            lo, hi = p['char_start'], p['char_end']
            fragments = [
                {**f, 'relative_start': f['char_start']-lo,
                 'relative_end': f['char_end']-lo}
                for f in p['fragments']
            ]
            checked = proof(p['decision'], p['url'], p['revision'],
                            p['document_sha256'], p['candidate_sha256'],
                            lo, hi, fragments)
            if checked != p:
                raise ValueError('historical_proof_mismatch')
            return checked
    raise ValueError('missing_historical_decision')


def try_reuse(memory, case, auditor, refresher=None):
    """Return a private, structured attempt. Only a revalidated hit has text."""
    out = {'schema': 'wikidebia-memory-reuse-1', 'id': case['id'],
           'status': 'miss', 'rights': 'unreviewed', 'auto_publish': False,
           'new_semantic_validation': False}
    try:
        meta = case['metadata']
        ref = case['citation_ref']
        urls = meta['mia_urls']
        if not urls or any(canonical_mia(u) != u for u in urls):
            return {**out, 'reason': 'outside_mia_scope'}
        row = lookup(memory, meta, ref)
        if row is None:
            return {**out, 'reason': 'no_unique_exact_memory'}
        out['historical_case_id'] = row['id']
        if row['auto_publish'] is not False or row['rights'] != 'unreviewed':
            raise ValueError('invalid_historical_scope')
        p = latest_proof(row)
        if p is None:
            return {**out, 'reason': 'historical_unresolved',
                    'historical_decision': row['latest_decision']}
        out['historical_decision'] = p['decision']

        i, quote = auditor.input(case)
        if (i['status'] != 'verified_pinned_citation' or not isinstance(quote, str)
                or i['revision'] != ref['revision']
                or i['quote_sha256'] != ref['quote_sha256']
                or i['template_sha256'] != ref['template_sha256']
                or sha(quote.encode()) != ref['quote_sha256']
                or i['mia_urls'] != urls):
            return {**out, 'reason': 'input_not_verified'}
        out['input_receipt'] = i
        doc = auditor.document(p['url'], 'en')
        if isinstance(doc, dict):
            return {**out, 'reason': 'target_unavailable'}
        if doc.url != p['url'] or doc.revision != p['revision']:
            return {**out, 'reason': 'target_identity_changed'}
        entry = {'url': p['url'], 'revision': p['revision'],
                 'sha256': p['document_sha256'], 'text_sha256': p['candidate_sha256'],
                 'char_start': p['char_start'], 'char_end': p['char_end']}
        receipt = span_receipt(doc, entry)
        refresh = None
        if (receipt['status'] == 'span_matches_changed_document'
                and p['revision'] is not None and refresher is not None):
            refresh = refresher.refresh(doc, entry)
            if refresh['status'] == 'verified_same_revision_span':
                receipt = refresh['fresh_receipt']
        if receipt['status'] != 'verified_document_and_span':
            return {**out, 'reason': 'target_evidence_changed'}
        text, _ = text_and_starts(doc)
        fragments = []
        for f in p['fragments']:
            lo, hi = f['char_start'], f['char_end']
            fragment = text[lo:hi]
            if sha(fragment.encode()) != f['text_sha256']:
                return {**out, 'reason': 'fragment_evidence_changed'}
            fragments.append({**f, 'text': fragment})
        if not fragments:
            raise ValueError('missing_accepted_fragments')
        return {**out, 'status': 'historical_equivalence_revalidated',
                'target_receipt': receipt, 'revision_refresh': refresh,
                'publication_metadata': auditor.publication.get(('en', doc.url), {}),
                'fragments': fragments, 'semantic_review_required': False,
                'editorial_review_required': True,
                'french_archive_span_revalidated': False,
                'semantic_review_provenance': 'historical_ChatGPT_decision'}
    except (KeyError, TypeError, ValueError, RuntimeError, AttributeError) as exc:
        return {**out, 'reason': 'memory_evidence_error', 'error': error_kind(exc)}


class MemoryFirst:
    """Opt-in adapter around an existing Retrieval; ordinary path unchanged."""
    def __init__(self, retrieval, memory, auditor, refresher=None):
        self.retrieval = retrieval
        self.memory = memory
        self.auditor = auditor
        self.refresher = refresher
        self.stats = {'reused': 0, 'retrieved': 0}

    def resolve(self, case):
        attempt = try_reuse(self.memory, case, self.auditor, self.refresher)
        if attempt['status'] == 'historical_equivalence_revalidated':
            self.stats['reused'] += 1
            return {'id': case['id'], 'path': 'memory', 'memory': attempt,
                    'retrieval': None, 'auto_publish': False, 'rights': 'unreviewed'}
        self.stats['retrieved'] += 1
        return {'id': case['id'], 'path': 'retrieval', 'memory': attempt,
                'retrieval': self.retrieval.retrieve(case),
                'auto_publish': False, 'rights': 'unreviewed'}
