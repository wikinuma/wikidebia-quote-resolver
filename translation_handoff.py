"""Private on-demand handoff for citations from a locked translation source.

The same receipt registry, retrieval engine and review packet builder are
used by the anticipatory campaign. A handoff does not change the kit's
translation review, its source lock, or its publication state.
"""
from collections import Counter
from types import SimpleNamespace
import argparse
import fcntl
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import mwparserfromhell

from citation_policy import link_policy
from citation_registry import CitationRegistry, fingerprint, source_identity
from evidence_audit import Auditor, valid_digest
from grouped_review import apply_review, pinned_loader, private_dir, write_private
from navigation import citation_record, error_kind, safe_title, templates
from pilot_full import PilotClient
from prepare_citations import build_campaign_packet
from prepared_resolution import CANDIDATES, INSUFFICIENT, RETRY, REVIEWED, resolve_occurrence
from resolver import ROOT, norm
from review_bundle import write_bundle
from work_library import WorkLibrary

REQUEST = 'wikidebia-translation-citation-request-1'
HANDOFF = 'wikidebia-translation-citation-handoff-1'
ITEM_FIELDS = {'id', 'wiki_title', 'revision', 'source_template', 'source_parameters'}
MAX_ITEMS = 200


def parameter_signature(parameters):
    if not isinstance(parameters, list):
        raise ValueError('invalid_locked_parameters')
    pairs = []
    seen = set()
    for p in parameters:
        if (not isinstance(p, dict) or set(p) != {'name', 'value'}
                or not isinstance(p['name'], str) or not isinstance(p['value'], str)):
            raise ValueError('invalid_locked_parameter')
        key = p['name'].strip()
        normalized = norm(key.replace('_', ' '))
        if not key or not normalized or normalized in seen:
            raise ValueError('duplicate_or_empty_locked_parameter')
        seen.add(normalized)
        pairs.append((key, p['value'].strip()))
    if not any(k == 'citation' and v for k, v in pairs):
        raise ValueError('missing_locked_citation')
    return tuple(sorted(pairs))


def validate_request(request):
    if (not isinstance(request, dict)
            or set(request) != {'schema', 'source_lock_sha256', 'items'}
            or request['schema'] != REQUEST
            or not valid_digest(request['source_lock_sha256'])
            or not isinstance(request['items'], list)
            or len(request['items']) > MAX_ITEMS):
        raise ValueError('invalid_translation_request')
    ids = set()
    for item in request['items']:
        if (not isinstance(item, dict) or set(item) != ITEM_FIELDS
                or not isinstance(item['id'], str) or not item['id'].strip()
                or item['id'] in ids or len(item['id']) > 200
                or not safe_title(item['wiki_title'])
                or not isinstance(item['source_template'], str)
                or norm(item['source_template']) not in
                   {'citation', 'modele citation', 'template citation'}
                or (item['revision'] is not None and
                    (type(item['revision']) is not int or item['revision'] < 1))):
            raise ValueError('invalid_or_duplicate_translation_occurrence')
        parameter_signature(item['source_parameters'])
        ids.add(item['id'])
    return fingerprint(request)


def locked_policy(item):
    # The policy reads only parameter names and parsed literal values.
    # This is not a fabricated wiki template or historical revision.
    params = [SimpleNamespace(name=k, value=mwparserfromhell.parse(v))
              for k, v in parameter_signature(item['source_parameters'])]
    return link_policy(SimpleNamespace(params=params))


def bind_occurrence(item, load_raw):
    """Find exactly one actual pinned Citation with the complete locked fields.

    No invented revision, quote selector, translated field, or approximate
    metadata can supply a source identity. Identical repeated invocations
    remain unresolved rather than being arbitrarily assigned.
    """
    if item['revision'] is None:
        return None, 'pinned_source_required'
    raw = load_raw(item['wiki_title'], item['revision'])
    wanted = parameter_signature(item['source_parameters'])
    matches = []
    for index, t in enumerate(templates(raw, {'citation'})):
        signature = tuple(sorted((str(p.name).strip(), str(p.value).strip())
                                 for p in t.params))
        if signature == wanted:
            matches.append((index, t))
    if len(matches) != 1:
        return None, 'ambiguous_locked_citation' if matches else 'locked_citation_not_found'
    index, t = matches[0]
    meta = citation_record(t, index, include_link_policy=True)
    if meta['link_policy']['route'] != 'mia':
        return None, 'locked_link_policy_mismatch'
    ref = {'revision': item['revision'], 'template_index': index,
           'template_sha256': meta['template_sha256'], 'quote_sha256': meta['quote_sha256']}
    identity = source_identity(raw, ref)
    return {'id': item['id'], 'wiki_title': item['wiki_title'], 'citation_ref': ref,
            'source_key': identity['source_key'], 'mia_url': identity['mia_url']}, None


def prepare_handoff(request, registry, retrieval, load_raw, audit, *,
                    search_missing=True, max_payload_chars=120000, batch_size=6):
    request_sha = validate_request(request)
    if max_payload_chars < 1 or batch_size < 1:
        raise ValueError('invalid_translation_handoff_limits')
    rows = {}
    cases = {}
    occurrence_ids = Counter()

    # First bind every occurrence, before starting retrieval or reusing text.
    for item in request['items']:
        cid = item['id']
        row = rows[cid] = {'id': cid, 'source_parameters_sha256':
                           fingerprint(item['source_parameters'])}
        policy = locked_policy(item)
        if policy['route'] != 'mia':
            row.update(state='ai_translation_required', reason=policy['reason'])
            continue
        try:
            case, reason = bind_occurrence(item, load_raw)
        except Exception as exc:
            row.update(state=RETRY, reason=error_kind(exc))
            continue
        if case is None:
            row.update(state='source_binding_required', reason=reason)
            continue
        cases[cid] = case
        ref = case['citation_ref']
        key = (ref['revision'], ref['template_index'])
        occurrence_ids[key] += 1

    candidates = []
    for cid, case in cases.items():
        row = rows[cid]
        ref = case['citation_ref']
        if occurrence_ids[(ref['revision'], ref['template_index'])] != 1:
            row.update(state='source_binding_required', reason='duplicate_pinned_occurrence')
            continue
        row['citation_ref'] = ref
        row['source_key'] = case['source_key']
        state, evidence, restored = resolve_occurrence(
            case, registry, retrieval, load_raw, audit, search_missing=search_missing)
        row.update(state=state, evidence=evidence)
        if state == REVIEWED:
            # Private exact fragments only. Never a translated/approved kit field.
            row['published_passage'] = {
                'receipt': restored['receipt'], 'fragments': restored['fragments'],
                'editorial_review_required': True, 'rights': 'unreviewed',
                'auto_publish': False}
        elif state == CANDIDATES:
            candidates.append((case, evidence['result']))

    packets = []

    def prepare(batch):
        try:
            packet = build_campaign_packet(batch, load_raw, audit.document,
                                           max_payload_chars=max_payload_chars)
        except ValueError as exc:
            if str(exc) != 'review_payload_limit_split_input':
                raise
            if len(batch) > 1:
                mid = len(batch)//2
                prepare(batch[:mid])
                prepare(batch[mid:])
            else:
                rows[batch[0][0]['id']].update(state=INSUFFICIENT,
                                               reason='single_review_payload_limit')
            return
        expected = {c['id'] for c, _ in batch}
        included = [o['case_id'] for source in packet['sources'].values()
                    for o in source['occurrences']]
        skipped = [s['case_id'] for s in packet['skipped']]
        if Counter(included + skipped) != Counter(expected):
            raise ValueError('translation_handoff_occurrence_accounting')
        for failure in packet['skipped']:
            rows[failure['case_id']].update(state=RETRY, reason=failure['status'])
        if included:
            packet['translation_request_sha256'] = request_sha
            packet['source_lock_sha256'] = request['source_lock_sha256']
            digest = fingerprint(packet)
            packets.append({'packet_sha256': digest, 'packet': packet})
            for cid in included:
                rows[cid]['packet_sha256'] = digest

    for offset in range(0, len(candidates), batch_size):
        prepare(candidates[offset:offset+batch_size])
    return {'schema': HANDOFF, 'request_sha256': request_sha,
            'source_lock_sha256': request['source_lock_sha256'],
            'rows': list(rows.values()), 'packets': packets,
            'rights': 'unreviewed', 'auto_publish': False}


def apply_handoff_reply(handoff, expected_sha, reply, registry, load_raw, audit):
    """Apply through the shared exact-evidence receipt path, never a kit lock."""
    if (not valid_digest(expected_sha) or fingerprint(handoff) != expected_sha
            or handoff.get('schema') != HANDOFF or handoff.get('auto_publish') is not False
            or handoff.get('rights') != 'unreviewed'):
        raise ValueError('translation_handoff_identity_mismatch')
    matches = [x for x in handoff['packets']
               if x['packet_sha256'] == reply.get('packet_sha256')]
    if len(matches) != 1:
        raise ValueError('translation_handoff_packet_not_found')
    entry = matches[0]
    packet = entry['packet']
    if (packet.get('translation_request_sha256') != handoff['request_sha256']
            or packet.get('source_lock_sha256') != handoff['source_lock_sha256']):
        raise ValueError('translation_handoff_packet_binding_mismatch')
    return apply_review(packet, reply, entry['packet_sha256'],
                        registry, load_raw, audit.document)


def aggregate(handoff):
    return {'handoff_sha256': fingerprint(handoff),
            'request_sha256': handoff['request_sha256'],
            'source_lock_sha256': handoff['source_lock_sha256'],
            'occurrences': len(handoff['rows']),
            'states': dict(Counter(row['state'] for row in handoff['rows'])),
            'packets': len(handoff['packets']), 'rights': 'unreviewed',
            'auto_publish': False}


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True,
                        help='Same private state directory as prepare_citations.py')
    parser.add_argument('--audit-budget', type=int, default=500)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--request', type=Path, required=True)
    prep.add_argument('--lookup-only', action='store_true')
    prep.add_argument('--budget', type=int, default=300)
    prep.add_argument('--max-pages', type=int, default=64)
    prep.add_argument('--max-payload-chars', type=int, default=120000)
    apply = sub.add_parser('apply')
    apply.add_argument('--handoff-sha', required=True)
    apply.add_argument('--reply', type=Path, required=True)
    args = parser.parse_args(argv)
    if min(getattr(args, k, 1) for k in
           ('audit_budget', 'budget', 'max_pages', 'max_payload_chars')) < 1:
        parser.error('invalid_processing_limits')
    os.umask(0o077)
    state = private_dir(args.state)
    with (state/'campaign.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with WorkLibrary(state/'works.sqlite3') as library, TemporaryDirectory() as tmp:
            registry = CitationRegistry(library.db)
            audit = Auditor(PilotClient(Path(tmp), budget=args.audit_budget))
            fresh = pinned_loader(audit.client)
            if args.command == 'apply':
                if not valid_digest(args.handoff_sha):
                    parser.error('invalid_handoff_digest')
                handoff = read_json(state/'translation-handoffs'/args.handoff_sha/'handoff.json')
                result = apply_handoff_reply(handoff, args.handoff_sha, read_json(args.reply),
                                             registry, fresh, audit)
                print(json.dumps(result, sort_keys=True))
                return 0
            request = read_json(args.request)
            validate_request(request)
            client = PilotClient(state/'http', budget=args.budget)
            if args.lookup_only:
                handoff = prepare_handoff(request, registry, SimpleNamespace(client=client),
                                          fresh, audit, search_missing=False,
                                          max_payload_chars=args.max_payload_chars)
            else:
                from auto_retrieve import Retrieval
                from bibliography import load_catalog
                from chapter_routes import load_chapters
                from wikirouge_incremental import open_current
                with open_current() as index:
                    retrieval = Retrieval(client, args.max_pages,
                        load_catalog(ROOT/'bibliographic-catalog.json'), index, True,
                        load_chapters(ROOT/'chapter-catalog.json'), work_library=library,
                        strict_link_policy=True)
                    handoff = prepare_handoff(request, registry, retrieval, fresh, audit,
                                              max_payload_chars=args.max_payload_chars)
            summary = aggregate(handoff)
            out = private_dir(state/'translation-handoffs'/summary['handoff_sha256'])
            path = out/'handoff.json'
            if path.exists():
                if fingerprint(read_json(path)) != summary['handoff_sha256']:
                    raise ValueError('stored_translation_handoff_changed')
            else:
                write_private(path, handoff)
            if handoff['packets']:
                pairs = [(p['packet_sha256'], p['packet']) for p in handoff['packets']]
                write_bundle(out/'review.zip', pairs, handoff['request_sha256'])
            print(json.dumps({**summary, 'fresh_audit_requests': audit.client.requests,
                              'retrieval_requests': client.requests}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
