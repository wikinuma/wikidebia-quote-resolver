"""Private, read-only documentary replay of transferred prepared receipts.

Import into an isolated temporary registry; reconstruct the exact historical
Citation from its pinned revision and exercise the normal lookup-only handoff.
No retrieval, translation, new receipt, wiki write, or rights approval.
Only aggregate counts leave the process. This is not a locked kit Work or a
semantic re-review, and does not assert that the historical citation is current.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from citation_registry import CitationRegistry, fingerprint, passage_identity, source_identity
from evidence_audit import Auditor
from navigation import WD_API, raw_parse, templates
from pilot_full import PilotClient
from prepared_resolution import REVIEWED
from registry_transfer import import_bundle, read_private_json
from translation_handoff import REQUEST, prepare_handoff
from work_library import WorkLibrary


def replay(bundle, expected_sha, registry, load_revision, audit):
    imported = import_bundle(registry, bundle, expected_sha)
    entries = bundle['entries']
    states = Counter()
    retry_reasons = Counter()
    pages = {}
    retrieval_calls = 0

    def no_retrieval(_):
        nonlocal retrieval_calls
        retrieval_calls += 1
        raise AssertionError('lookup_only_called_retrieval')

    retrieval = SimpleNamespace(client=SimpleNamespace(events=[]),
                                retrieve=no_retrieval)

    def load(title, revision):
        if revision not in pages:
            raise ValueError('unread_revision')
        actual, observed, raw = pages[revision]
        if observed != revision or title != actual:
            raise ValueError('pinned_revision_mismatch')
        return raw

    for row in entries:
        entry = row['entry']
        source = entry['source']
        occurrence = source['occurrence']
        rev = occurrence['revision']
        ref = {**occurrence, 'quote_sha256': source['quote_sha256']}
        if rev not in pages:
            try:
                pages[rev] = load_revision(rev)
            except Exception:
                # Remember this unavailable revision within this bounded audit.
                pages[rev] = None
        if pages[rev] is None:
            states['source_unavailable'] += 1
            continue
        try:
            title, observed, raw = pages[rev]
            if observed != rev or source_identity(raw, ref) != source:
                raise ValueError('source_identity_changed')
            template = list(templates(raw, {'citation'}))[ref['template_index']]
            item = {'id': 'receipt', 'wiki_title': title, 'revision': rev,
                    'source_template': str(template.name).strip(),
                    'source_parameters': [
                        {'name': str(p.name).strip(), 'value': str(p.value).strip()}
                        for p in template.params]}
        except (ValueError, KeyError, IndexError, TypeError):
            states['source_identity_unconfirmed'] += 1
            continue
        request = {'schema': REQUEST, 'items': [item],
                   'source_lock_sha256': fingerprint(
                       {'scope': 'historical_receipt_replay_not_a_kit_lock',
                        'receipt': row['entry_sha256'], 'item': item})}
        handoff = prepare_handoff(request, registry, retrieval, load, audit,
                                  search_missing=False)
        if handoff['packets'] or handoff['auto_publish'] is not False:
            raise ValueError('unexpected_audit_handoff')
        result, = handoff['rows']
        if result['state'] != REVIEWED:
            if 'published_passage' in result:
                raise ValueError('unverified_text_returned')
            states[result['state']] += 1
            if result['state'] == 'retry_documentary':
                # Enumerate a small fixed vocabulary, not arbitrary error text.
                reason = result.get('evidence', {}).get('reason') or result.get('reason')
                retry_reasons[reason if reason in {
                    'stored_passage_not_revalidated', 'pinned_revision_mismatch',
                    'inventory_source_identity_mismatch'} else 'other'] += 1
            continue
        passage = result['published_passage']
        if (result['citation_ref'] != ref
                or result['source_key'] != source['source_key']
                or passage['rights'] != 'unreviewed'
                or passage['auto_publish'] is not False
                or passage['editorial_review_required'] is not True):
            raise ValueError('audit_handoff_identity_mismatch')
        # A second receipt is acceptable for the same locked occurrence only
        # if the source identity and the existing registry's passage identity
        # agree. Never label this as revalidation of the original envelope.
        selected = passage['receipt']
        if (selected['source']['source_key'] != source['source_key']
                or passage_identity(selected) != passage_identity(entry)):
            raise ValueError('audit_selected_passage_mismatch')
        from resolver import sha
        fragments = passage['fragments']
        if (not fragments
                or [{k: f[k] for k in ('char_start', 'char_end', 'text_sha256')}
                    for f in fragments] != entry['fragments']
                or any(sha(f['text'].encode()) != f['text_sha256'] for f in fragments)):
            raise ValueError('audit_fragment_mismatch')
        if selected['entry_sha256'] != row['entry_sha256']:
            states['equivalent_receipt_reconstructed'] += 1
        else:
            states['exact_receipt_reconstructed'] += 1

    if sum(states.values()) != len(entries) or retrieval_calls:
        raise ValueError('audit_accounting_or_retrieval_failure')
    verified = states['exact_receipt_reconstructed']
    usable = verified + states['equivalent_receipt_reconstructed']
    return {'schema': 'wikidebia-prepared-bundle-replay-1',
            'scope': 'historical_pinned_receipts_lookup_only_not_current_kit_work',
            'bundle_sha256': expected_sha, 'entries': len(entries),
            'conflicting_sources': imported['conflicting_sources'],
            'states': dict(sorted(states.items())),
            'exact_receipts_reconstructed': verified,
            'equivalent_receipts_reconstructed': usable - verified,
            'entries_with_revalidated_passage': usable,
            'retry_reasons': dict(sorted(retry_reasons.items())),
            'all_receipts_revalidated': bool(entries) and verified == len(entries),
            'all_entries_have_revalidated_passage': bool(entries) and usable == len(entries),
            'source_revisions_attempted': len(pages),
            'retrieval_calls': retrieval_calls, 'model_calls': 0,
            'rights': 'unreviewed', 'auto_publish': False}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--expected-entries', type=int, required=True)
    p.add_argument('--budget', type=int, default=1500)
    a = p.parse_args(argv)
    if not 1 <= a.expected_entries <= 50000 or not 1 <= a.budget <= 5000:
        p.error('invalid_audit_bounds')
    os.umask(0o077)
    try:
        bundle = read_private_json(a.bundle)
        if len(bundle['entries']) != a.expected_entries:
            raise ValueError('unexpected_receipt_count')
        with TemporaryDirectory(prefix='prepared-bundle-audit-') as tmp:
            root = Path(tmp)
            client = PilotClient(root/'http', budget=a.budget)
            with WorkLibrary(root/'works.sqlite3') as library:
                result = replay(bundle, a.sha256, CitationRegistry(library.db),
                                lambda rev: raw_parse(client, WD_API, oldid=rev),
                                Auditor(client))
            result['fresh_audit_requests'] = client.requests
            result['request_error_count'] = sum(
                e.get('status') == 'error' for e in client.events)
        print(json.dumps(result, sort_keys=True))
        return 0 if result['all_entries_have_revalidated_passage'] else 2
    except Exception as exc:
        # Never print the private bundle, title, passage or exception message.
        print(json.dumps({'status': 'audit_failed', 'error_type': type(exc).__name__,
                          'auto_publish': False, 'rights': 'unreviewed'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
