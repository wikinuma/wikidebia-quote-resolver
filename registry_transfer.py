"""Private transport of existing reviewed receipts, not a second registry.

Only transfer between trusted environments. The expected digest must arrive
through an independently trusted channel; SHA-256 is integrity, not authorship.
No document bodies, quotation text, HTTP caches, credentials or rights grants.
The normal resolver must still rebind the source and revalidate the target.
"""
import argparse
from collections import defaultdict
import fcntl
import json
from pathlib import Path

from alignment_store import encode
from citation_registry import CitationRegistry, DECISIONS, fingerprint, passage_identity, require_digest
from evidence_audit import document_provider
from grouped_review import private_dir, write_private
from navigation import canonical_mia
from resolver import sha
from translation_handoff import locked_policy, parameter_signature, validate_request
from work_library import WorkLibrary

BUNDLE = 'wikidebia-prepared-receipt-transfer-1'
INVENTORY = 'wikidebia-prepared-receipt-inventory-1'
SELECTION = 'wikidebia-prepared-receipt-selection-1'
RECEIPT = 'wikidebia-prepared-citation-receipt-1'
SOURCE = 'wikidebia-mia-prepared-source-1'
MAX_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 50000
SOURCE_FIELDS = {'schema', 'provider', 'mia_url', 'quote_sha256',
                 'quote_wikitext_sha256', 'metadata_sha256'}


def fields(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError('invalid_transfer_fields')


def integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('invalid_transfer_integer')


def digest_fields(value, names):
    for name in names:
        require_digest(value[name])


def validate_entry(key, entry):
    """Validate the complete existing receipt contract without inventing
    documentary or semantic verification. Unknown fields/versions fail closed.
    """
    require_digest(key)
    fields(entry, {'schema', 'source', 'target', 'decision', 'fragments',
                   'review_packet_sha256', 'reviewer', 'semantic_status', 'rights',
                   'auto_publish'})
    if (entry['schema'] != RECEIPT or entry['rights'] != 'unreviewed'
            or entry['auto_publish'] is not False
            or entry['semantic_status'] != 'editorial_decision_recorded'
            or not isinstance(entry['decision'], str) or entry['decision'] not in DECISIONS
            or not isinstance(entry['reviewer'], str) or not entry['reviewer'].strip()
            or len(entry['reviewer']) > 120):
        raise ValueError('invalid_transfer_receipt_scope')
    require_digest(entry['review_packet_sha256'])
    source = entry['source']
    fields(source, SOURCE_FIELDS | {'source_key', 'has_explicit_ellipsis', 'occurrence'})
    if (source['schema'] != SOURCE or source['provider'] != 'mia'
            or not isinstance(source['mia_url'], str)
            or canonical_mia(source['mia_url']) != source['mia_url']
            or type(source['has_explicit_ellipsis']) is not bool):
        raise ValueError('invalid_transfer_source')
    digest_fields(source, ('quote_sha256', 'quote_wikitext_sha256',
                           'metadata_sha256', 'source_key'))
    if fingerprint({k: source[k] for k in SOURCE_FIELDS}) != source['source_key']:
        raise ValueError('transfer_source_key_mismatch')
    occurrence = source['occurrence']
    fields(occurrence, {'revision', 'template_index', 'template_sha256'})
    integer(occurrence['revision'], 1)
    integer(occurrence['template_index'])
    require_digest(occurrence['template_sha256'])
    target = entry['target']
    fields(target, {'document', 'char_start', 'char_end', 'text_sha256', 'candidate_key'})
    doc = target['document']
    fields(doc, {'url', 'revision', 'body_sha256', 'blocks_sha256'})
    if not isinstance(doc['url'], str):
        raise ValueError('invalid_transfer_document')
    document_provider(doc['url'], 'en')
    if doc['revision'] is not None:
        integer(doc['revision'], 1)
    digest_fields(doc, ('body_sha256', 'blocks_sha256'))
    digest_fields(target, ('text_sha256', 'candidate_key'))
    if fingerprint({k: v for k, v in target.items() if k != 'candidate_key'}) != target['candidate_key']:
        raise ValueError('transfer_candidate_key_mismatch')
    lo, hi = target['char_start'], target['char_end']
    integer(lo)
    integer(hi, lo + 1)
    fragments = entry['fragments']
    if not isinstance(fragments, list) or not fragments:
        raise ValueError('invalid_transfer_fragments')
    previous = None
    for fragment in fragments:
        fields(fragment, {'char_start', 'char_end', 'text_sha256'})
        a, b = fragment['char_start'], fragment['char_end']
        integer(a, lo)
        integer(b, a + 1)
        if b > hi or (previous is not None and a <= previous):
            raise ValueError('invalid_transfer_fragment_coordinates')
        require_digest(fragment['text_sha256'])
        previous = b
    complete = (len(fragments) == 1 and fragments[0]['char_start'] == lo
                and fragments[0]['char_end'] == hi)
    decision = entry['decision']
    if (decision == 'complete_equivalent'
            and (not complete or fragments[0]['text_sha256'] != target['text_sha256'])):
        raise ValueError('invalid_transfer_complete_span')
    if decision == 'boundary_adjustment' and (len(fragments) != 1 or complete):
        raise ValueError('invalid_transfer_boundary')
    if (decision == 'omission_adjustment'
            and (len(fragments) < 2 or not source['has_explicit_ellipsis'])):
        raise ValueError('invalid_transfer_omission')
    if fingerprint(entry) != key:
        raise ValueError('transfer_entry_digest_mismatch')
    return source['source_key']


def literal_selector(quote_hash, metadata_hash):
    """Stable *filter*, not a source identity or proof. The existing receipt
    retains the stronger source key and must still be rebound and revalidated.
    No reparse of a quotation's rendered text is needed for selecting receipts.
    """
    require_digest(quote_hash)
    require_digest(metadata_hash)
    return fingerprint({'quote_wikitext_sha256': quote_hash,
                        'metadata_sha256': metadata_hash})


def build_selection(requests):
    """From kit requests already bound to one French lock. No network, DB,
    text, title, URL or revision in the output. The receiving registry may
    contain multiple editorial receipts for a selector; transfer all of them.
    """
    lock = None
    request_digests, ids, selectors = set(), set(), set()
    for request in requests:
        digest = validate_request(request)
        if digest in request_digests:
            raise ValueError('duplicate_selection_request')
        request_digests.add(digest)
        if lock is None:
            lock = request['source_lock_sha256']
        elif lock != request['source_lock_sha256']:
            raise ValueError('mixed_selection_source_locks')
        for item in request['items']:
            if item['id'] in ids:
                raise ValueError('duplicate_selection_occurrence')
            ids.add(item['id'])
            if len(ids) > MAX_ENTRIES:
                raise ValueError('selection_occurrence_limit')
            if item['revision'] is None or locked_policy(item)['route'] != 'mia':
                continue
            pairs = parameter_signature(item['source_parameters'])
            quote = dict(pairs)['citation']
            selectors.add(literal_selector(sha(quote.encode('utf-8')),
                            fingerprint([(k, v) for k, v in pairs if k != 'citation'])))
        if len(request_digests) > MAX_ENTRIES:
            raise ValueError('selection_request_limit')
    if lock is None:
        raise ValueError('selection_requires_request')
    return {'schema': SELECTION, 'source_lock_sha256': lock,
            'request_sha256s': sorted(request_digests), 'selectors': sorted(selectors)}


def selection_keys(value, expected_sha):
    require_digest(expected_sha)
    fields(value, {'schema', 'source_lock_sha256', 'request_sha256s', 'selectors'})
    if value['schema'] != SELECTION:
        raise ValueError('invalid_selection_schema')
    require_digest(value['source_lock_sha256'])
    for key in ('request_sha256s', 'selectors'):
        rows = value[key]
        if not isinstance(rows, list) or len(rows) > MAX_ENTRIES:
            raise ValueError('invalid_selection_list')
        for row in rows:
            require_digest(row)
        if rows != sorted(set(rows)):
            raise ValueError('noncanonical_selection_list')
    if not value['request_sha256s'] or fingerprint(value) != expected_sha:
        raise ValueError('selection_digest_mismatch')
    return set(value['selectors'])


def _stored_rows(registry, source_keys=None, *, selectors=None):
    if selectors is not None and source_keys is not None:
        raise ValueError('conflicting_transfer_scope')
    if selectors is not None and not selectors:
        return []
    selected = None if source_keys is None else set(source_keys)
    if selected is not None:
        for key in selected:
            require_digest(key)
    if selected is not None and not selected:
        return []
    # Use the existing index for scoped exports; avoid SQLite variable limits.
    db = registry.db
    if selected is None:
        cursors = [db.execute('SELECT entry_sha256,source_key,entry FROM prepared_citations_v1')]
    else:
        cursors = (db.execute('SELECT entry_sha256,source_key,entry FROM prepared_citations_v1 WHERE source_key=?',
                              (key,)) for key in sorted(selected))
    rows = []
    for cursor in cursors:
        for key, source_key, raw in cursor:
            entry = json.loads(raw)
            if validate_entry(key, entry) != source_key or encode(entry) != raw:
                raise ValueError('transfer_registry_integrity_mismatch')
            if selectors is not None:
                source = entry['source']
                if literal_selector(source['quote_wikitext_sha256'],
                                    source['metadata_sha256']) not in selectors:
                    continue
            rows.append({'entry_sha256': key, 'entry': entry})
            if len(rows) > MAX_ENTRIES:
                raise ValueError('transfer_entry_limit')
    return sorted(rows, key=lambda row: row['entry_sha256'])


def inventory(registry, source_keys=None, *, selection=None, selection_sha=None):
    selectors = _selection_scope(selection, selection_sha)
    return {'schema': INVENTORY,
            'entry_sha256s': [row['entry_sha256'] for row in
                              _stored_rows(registry, source_keys, selectors=selectors)]}


def _selection_scope(selection, selection_sha):
    if (selection is None) != (selection_sha is None):
        raise ValueError('selection_requires_expected_digest')
    return None if selection is None else selection_keys(selection, selection_sha)


def known_inventory(value):
    fields(value, {'schema', 'entry_sha256s'})
    keys = value['entry_sha256s']
    if value['schema'] != INVENTORY or not isinstance(keys, list) or len(keys) > MAX_ENTRIES:
        raise ValueError('invalid_transfer_inventory')
    for key in keys:
        require_digest(key)
    if keys != sorted(set(keys)):
        raise ValueError('noncanonical_transfer_inventory')
    return set(keys)


def export_bundle(registry, *, source_keys=None, known=None, selection=None, selection_sha=None):
    selectors = _selection_scope(selection, selection_sha)
    known = set() if known is None else known_inventory(known)
    rows = [row for row in _stored_rows(registry, source_keys, selectors=selectors)
            if row['entry_sha256'] not in known]
    bundle = {'schema': BUNDLE, 'entries': rows, 'rights': 'unreviewed', 'auto_publish': False}
    if len(encode(bundle).encode('utf-8')) + 1 > MAX_BYTES:
        raise ValueError('transfer_byte_limit')
    return bundle


def import_bundle(registry, bundle, expected_sha):
    require_digest(expected_sha)
    fields(bundle, {'schema', 'entries', 'rights', 'auto_publish'})
    rows = bundle['entries']
    if (bundle['schema'] != BUNDLE or bundle['rights'] != 'unreviewed'
            or bundle['auto_publish'] is not False or not isinstance(rows, list)
            or len(rows) > MAX_ENTRIES):
        raise ValueError('invalid_transfer_bundle')
    if len(encode(bundle).encode('utf-8')) + 1 > MAX_BYTES:
        raise ValueError('transfer_byte_limit')
    if fingerprint(bundle) != expected_sha:
        raise ValueError('transfer_bundle_digest_mismatch')
    keys, validated, affected = [], [], set()
    for row in rows:
        fields(row, {'entry_sha256', 'entry'})
        key, entry = row['entry_sha256'], row['entry']
        source_key = validate_entry(key, entry)
        keys.append(key)
        affected.add(source_key)
        validated.append((key, source_key, encode(entry)))
    if keys != sorted(set(keys)):
        raise ValueError('noncanonical_transfer_entries')
    # Preserve every competing review. Never overwrite or select a winner.
    # BEGIN IMMEDIATE makes the entire import and conflict report atomic.
    db = registry.db
    if db.in_transaction:
        raise ValueError('transfer_requires_clean_transaction')
    db.execute('BEGIN IMMEDIATE')
    inserted = 0
    try:
        for key, source_key, raw in validated:
            found = db.execute('SELECT source_key,entry FROM prepared_citations_v1 WHERE entry_sha256=?',
                               (key,)).fetchone()
            if found is not None:
                if tuple(found) != (source_key, raw):
                    raise ValueError('transfer_existing_entry_mismatch')
                continue
            db.execute('INSERT INTO prepared_citations_v1 VALUES (?,?,?)', (key, source_key, raw))
            inserted += 1
        groups = defaultdict(set)
        for row in _stored_rows(registry, affected):
            entry = row['entry']
            groups[entry['source']['source_key']].add(passage_identity(entry))
        conflicts = sum(len(group) > 1 for group in groups.values())
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return {'bundle_sha256': expected_sha, 'entries': len(rows), 'inserted': inserted,
            'already_present': len(rows) - inserted, 'conflicting_sources': conflicts,
            'rights': 'unreviewed', 'auto_publish': False,
            'fresh_documentary_verification': False}


def read_private_json(path):
    # Read a bounded regular file; never unpack a ZIP, load code or copy a DB.
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('transfer_byte_limit')
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError('duplicate_transfer_json_key')
            out[key] = value
        return out
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
    if raw != (encode(value) + '\n').encode('utf-8'):
        raise ValueError('noncanonical_transfer_json')
    return value


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state', type=Path)
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('select')
    s.add_argument('--request', type=Path, action='append', required=True)
    s.add_argument('--out', type=Path, required=True)
    for command in ('inventory', 'export'):
        s = sub.add_parser(command)
        s.add_argument('--out', type=Path, required=True)
        scope = s.add_mutually_exclusive_group()
        scope.add_argument('--source-key', action='append')
        scope.add_argument('--selection', type=Path)
        s.add_argument('--selection-sha256')
        if command == 'export':
            s.add_argument('--known-inventory', type=Path)
    s = sub.add_parser('import')
    s.add_argument('--bundle', type=Path, required=True)
    s.add_argument('--sha256', required=True)
    args = p.parse_args(argv)
    if args.command == 'select':
        value = build_selection(read_private_json(path) for path in args.request)
        out = private_dir(args.out.parent) / args.out.name
        write_private(out, value)
        print(json.dumps({'sha256': fingerprint(value), 'selectors': len(value['selectors']),
                          'requests': len(value['request_sha256s']), 'bytes': out.stat().st_size,
                          'rights': 'unreviewed', 'auto_publish': False}, sort_keys=True))
        return 0
    if args.state is None:
        p.error('--state is required for inventory, export and import')
    selection = None
    selection_sha = None
    if args.command in ('inventory', 'export'):
        if (args.selection is None) != (args.selection_sha256 is None):
            p.error('--selection and --selection-sha256 must be supplied together')
        if args.selection is not None:
            selection = read_private_json(args.selection)
            selection_sha = args.selection_sha256
            selection_keys(selection, selection_sha)
    state = private_dir(args.state)
    # Same campaign lock and same existing DB as both preparation paths.
    with (state / 'campaign.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with WorkLibrary(state / 'works.sqlite3') as library:
            registry = CitationRegistry(library.db)
            if args.command == 'import':
                result = import_bundle(registry, read_private_json(args.bundle), args.sha256)
            else:
                if args.command == 'inventory':
                    value = inventory(registry, args.source_key, selection=selection,
                                      selection_sha=selection_sha)
                    count = len(value['entry_sha256s'])
                else:
                    known = read_private_json(args.known_inventory) if args.known_inventory else None
                    value = export_bundle(registry, source_keys=args.source_key, known=known,
                                          selection=selection, selection_sha=selection_sha)
                    count = len(value['entries'])
                out = private_dir(args.out.parent) / args.out.name
                write_private(out, value)
                result = {'sha256': fingerprint(value), 'entries': count,
                          'bytes': out.stat().st_size, 'rights': 'unreviewed', 'auto_publish': False}
            print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
