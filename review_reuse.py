"""Opt-in offline re-association of sealed campaign review passages.

A plan names only old packet IDs and Unicode offsets or bounded padding.
Extensions read only the identical verified cached document. The original packets
remain immutable; a new, ordinary review packet is sealed locally. No search,
network read, receipt registration or publication occurs during preparation.
Normal Campaign.apply reopens the pinned source and exact target document.
"""
import argparse
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path

from alignment_store import encode
from citation_registry import fingerprint, check_target
from alignment import text_and_starts
from review_context import exact_target
from grouped_review import PACKET, REPLY, REJECT, private_dir
from prepare_citations import Campaign, AWAITING, INSUFFICIENT, load_inventory
from resolver import sha
from work_library import WorkLibrary

PLAN = 'wikidebia-reuse-review-plan-1'
EXTENDED_PLAN = 'wikidebia-reuse-review-plan-2'


def applied_packets(campaign):
    packets = {}
    for digest, body, response in campaign.db.execute(
            "SELECT digest,packet,reply FROM campaign_packets_v1 WHERE state='applied'"):
        packet, reply = json.loads(body), json.loads(response)
        if (fingerprint(packet) != digest or packet.get('schema') != PACKET
                or reply.get('schema') != REPLY or reply.get('packet_sha256') != digest):
            raise ValueError('reuse_invalid_historical_packet')
        packets[digest] = (packet, reply)
    return packets


def quote_view(packet, qid):
    rows = [(g, ch, q) for g in packet['payload']['groups']
            for ch in g['chapters'] for q in ch['citations'] if q['id'] == qid]
    if len(rows) != 1:
        raise ValueError('reuse_missing_or_ambiguous_quote')
    return rows[0]


def checked_passage(packet, pid):
    target = packet['targets'][pid]
    view = packet['payload']['passages'][pid]
    text = view['text']
    identity = {k: v for k, v in target.items() if k != 'candidate_key'}
    if (fingerprint(identity) != target['candidate_key']
            or not isinstance(text, str)
            or len(text) != view['length']
            or target['char_end'] - target['char_start'] != len(text)
            or target['char_start'] < 0
            or sha(text.encode()) != target['text_sha256']
            or packet['payload']['documents'][view['document']] != target['document']['url']):
        raise ValueError('reuse_passage_integrity_mismatch')
    return target, text


def extend_cached_passage(prior, source, entry, load_document):
    """Expand an earlier authorized window on the identical cached document.
    Never trust a replacement body, parser output, URL or document revision.
    """
    pid = entry['target_pid']
    if pid not in source['candidates']:
        raise ValueError('reuse_extension_not_authorized')
    padding = entry['extend']
    if (not isinstance(padding, list) or len(padding) != 2
            or any(type(n) is not int or not 0 <= n <= 4000 for n in padding)
            or not any(padding)):
        raise ValueError('reuse_invalid_extension')
    target, _ = checked_passage(prior, pid)
    if load_document is None:
        raise ValueError('reuse_offline_cache_required')
    doc = load_document(target['document']['url'], 'en')
    if doc is None or isinstance(doc, dict):
        raise ValueError('reuse_cached_document_unavailable')
    # In addition to the body hash, check the parser and old coordinates.
    try:
        check_target(doc, target)
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise ValueError('reuse_cached_document_mismatch') from exc
    full, _ = text_and_starts(doc)
    a = max(0, target['char_start'] - padding[0])
    b = min(len(full), target['char_end'] + padding[1])
    if b-a > 15000:
        raise ValueError('reuse_invalid_extension')
    return exact_target(doc, a, b), full[a:b], target


def build_packet(campaign, plan, max_payload_chars=120000, *, skip_examined=False,
                 load_document=None, skip_unavailable=False, append_to_open=False):
    if (not isinstance(plan, dict)
            or set(plan) != {'schema', 'inventory_sha256', 'entries'}
            or plan['schema'] not in (PLAN, EXTENDED_PLAN)
            or plan['inventory_sha256'] != campaign.inventory['manifest_sha256']
            or not isinstance(plan['entries'], list)
            or not 1 <= len(plan['entries']) <= 20
            or max_payload_chars < 1):
        raise ValueError('reuse_invalid_plan')
    open_packets = list(campaign.open_packets())
    if open_packets and not append_to_open:
        raise ValueError('reuse_open_review_exists')
    open_cases = set()
    for digest, packet in open_packets:
        for source in packet['sources'].values():
            for occurrence in source['occurrences']:
                cid = occurrence['case_id']
                state, evidence = campaign.latest(cid)
                if (cid not in campaign.cases or cid in open_cases or state != AWAITING
                        or evidence.get('packet_sha256') != digest):
                    raise ValueError('reuse_inconsistent_open_review')
                open_cases.add(cid)
    history = applied_packets(campaign)
    sources, targets, documents, passages, groups, proofs = {}, {}, {}, {}, [], {}
    seen, doc_ids, target_ids = set(), {}, {}
    skipped_examined, skipped_unavailable = [], []
    for entry in plan['entries']:
        if not isinstance(entry, dict):
            raise ValueError('reuse_invalid_entry')
        extension = (plan['schema'] == EXTENDED_PLAN
                     and set(entry) == {'packet', 'qid', 'target_pid', 'extend'})
        if not extension and set(entry) != {'packet', 'qid', 'donor_packet', 'donor_pid', 'range'}:
            raise ValueError('reuse_invalid_entry')
        prior, reply = history[entry['packet']]
        old_q = entry['qid']
        source = prior['sources'][old_q]
        if len(source['occurrences']) != 1:
            raise ValueError('reuse_distinct_occurrence_required')
        occurrence = source['occurrences'][0]
        cid = occurrence['case_id']
        case = campaign.cases.get(cid)
        state, evidence = campaign.latest(cid)
        decision = [d for d in reply['decisions'] if d['id'] == old_q]
        if (cid in seen or cid in open_cases or not case or state != INSUFFICIENT
                or evidence.get('packet_sha256') != entry['packet']
                or len(decision) != 1 or decision[0]['decision'] not in REJECT
                or decision[0]['candidate'] is not None or decision[0]['ranges'] != []
                or case['citation_ref'] != occurrence['ref']
                or case['wiki_title'] != occurrence['wiki_title']
                or case['source_key'] != source['source_key']):
            raise ValueError('reuse_occurrence_not_current_abstention')
        seen.add(cid)
        group, chapter, quote = quote_view(prior, old_q)
        if not group['work']:
            raise ValueError('reuse_work_identity_missing')
        if extension:
            try:
                new_target, text, target = extend_cached_passage(
                    prior, source, entry, load_document)
            except ValueError as exc:
                if not skip_unavailable or str(exc) not in {
                        'reuse_cached_document_unavailable', 'reuse_cached_document_mismatch'}:
                    raise
                skipped_unavailable.append({'case_id': cid, 'reason': str(exc)})
                continue
        else:
            donor, _ = history[entry['donor_packet']]
            donor_pid = entry['donor_pid']
            old_targets = [checked_passage(prior, pid)[0] for pid in source['candidates']]
            target, donor_text = checked_passage(donor, donor_pid)
            # Same exact document, not just URL, and a donor actually offered to
            # another occurrence of the same bibliographic work.
            if not any(t['document'] == target['document'] for t in old_targets):
                raise ValueError('reuse_document_not_previously_authorized')
            donor_ok = False
            for dq, ds in donor['sources'].items():
                if donor_pid not in ds['candidates']:
                    continue
                dg, _, _ = quote_view(donor, dq)
                if ((dg['authors'], dg['work']) == (group['authors'], group['work'])
                        and any(o['case_id'] != cid for o in ds['occurrences'])):
                    donor_ok = True
                    break
            if not donor_ok:
                raise ValueError('reuse_no_same_work_donor')
            bounds = entry['range']
            if (not isinstance(bounds, list) or len(bounds) != 2
                    or any(type(n) is not int for n in bounds)
                    or not 0 <= bounds[0] < bounds[1] <= len(donor_text)
                    or bounds[1] - bounds[0] > 15000):
                raise ValueError('reuse_invalid_range')
            a, b = bounds
            text = donor_text[a:b]
            if not text.strip():
                raise ValueError('reuse_empty_passage')
            new_target = {'document': deepcopy(target['document']),
                          'char_start': target['char_start'] + a,
                          'char_end': target['char_start'] + b,
                          'text_sha256': sha(text.encode())}
            new_target['candidate_key'] = fingerprint(new_target)
        # Do not recycle a passage (or mere truncation) already shown to
        # this exact occurrence, even if it used a different passage ID.
        already_examined = False
        for packet, _ in history.values():
            for historical in packet['sources'].values():
                if not any(o == occurrence for o in historical['occurrences']):
                    continue
                for pid in historical['candidates']:
                    old, old_text = checked_passage(packet, pid)
                    if (text in old_text or (old['document'] == new_target['document']
                            and old['char_start'] <= new_target['char_start']
                            and new_target['char_end'] <= old['char_end'])):
                        already_examined = True
        if already_examined:
            if not skip_examined:
                raise ValueError('reuse_passage_already_examined')
            skipped_examined.append({'case_id': cid, 'reason': 'reuse_passage_already_examined'})
            continue
        qid = 'q' + str(len(sources) + 1)
        key = new_target['candidate_key']
        pid = target_ids.setdefault(key, 'p' + str(len(target_ids) + 1))
        if pid not in targets:
            doc_key = fingerprint(new_target['document'])
            did = doc_ids.setdefault(doc_key, 'd' + str(len(doc_ids) + 1))
            targets[pid] = new_target
            passages[pid] = {'document': did, 'length': len(text), 'text': text}
            documents[did] = new_target['document']['url']
        sources[qid] = {'source_key': source['source_key'],
                        'occurrences': [deepcopy(occurrence)], 'candidates': [pid],
                        'source_statuses': source.get('source_statuses', []),
                        'unavailable_candidates': 0}
        view = {k: deepcopy(v) for k, v in quote.items()
                if k not in ('id', 'candidates', 'seed_ranges', 'transcription_variants')}
        view.update(id=qid, candidates=[pid])
        groups.append({'authors': group['authors'], 'work': group['work'],
                       'chapters': [{'fr_url': chapter['fr_url'], 'article': chapter['article'],
                                     'citations': [view]}]})
        proofs[qid] = {**deepcopy(entry), 'case_id': cid,
                       ('prior_candidate_key' if extension else 'donor_candidate_key'):
                           target['candidate_key'],
                       'new_candidate_key': key}
    payload = {'groups': groups, 'documents': documents, 'passages': passages}
    if len(encode(payload)) > max_payload_chars:
        raise ValueError('reuse_payload_limit')
    packet = {'schema': PACKET, 'payload': payload, 'sources': sources,
            'targets': targets, 'skipped': [], 'rights': 'unreviewed',
            'auto_publish': False, 'campaign_batch': fingerprint(plan),
            'campaign_reuse': proofs, 'campaign_reuse_skipped_examined': skipped_examined}
    if plan['schema'] == EXTENDED_PLAN:
        packet['campaign_reuse_skipped_unavailable'] = skipped_unavailable
    return packet


def prepare(campaign, plan, *, write=False, skip_examined=False,
            load_document=None, skip_unavailable=False, append_to_open=False):
    packet = build_packet(campaign, plan, skip_examined=skip_examined,
                          load_document=load_document, skip_unavailable=skip_unavailable,
                          append_to_open=append_to_open)
    digest = fingerprint(packet)
    registered = write and bool(packet['sources'])
    if registered:
        # The CLI holds the ordinary campaign lock throughout. Registration
        # and transitions are one transaction; apply uses the existing checks.
        with campaign.db:
            campaign.db.execute('INSERT INTO campaign_packets_v1 VALUES (?,?,?,NULL)',
                                (digest, encode(packet), 'open'))
            for source in packet['sources'].values():
                campaign._append(source['occurrences'][0]['case_id'], AWAITING,
                                 {'packet_sha256': digest})
    return {'packet_sha256': digest if packet['sources'] else None,
            'occurrences': len(packet['sources']),
            'skipped_examined': packet['campaign_reuse_skipped_examined'],
            'skipped_unavailable': packet.get('campaign_reuse_skipped_unavailable', []),
            'candidate_chars': sum(p['length'] for p in packet['payload']['passages'].values()),
            'prepared': registered, 'network_requests': 0, 'auto_publish': False,
            'rights': 'unreviewed'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state', type=Path, required=True)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--prepare', action='store_true',
                   help='Register a new open packet; otherwise preview only')
    p.add_argument('--skip-examined', action='store_true',
                   help='Report and omit passages already examined for that exact occurrence')
    p.add_argument('--skip-unavailable', action='store_true',
                   help='Omit cache misses or document mismatches, never substitute evidence')
    p.add_argument('--append-to-open', action='store_true',
                   help='Allow disjoint sealed packets alongside already open, consistent reviews')
    a = p.parse_args()
    os.umask(0o077)
    state = private_dir(a.state)
    plan = json.loads(a.plan.read_text(encoding='utf-8'))
    with (state/'campaign.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with WorkLibrary(state/'works.sqlite3') as library:
            campaign = Campaign(library, load_inventory(state/'inventory.json'))
            loader = None
            if plan.get('schema') == EXTENDED_PLAN:
                from evidence_audit import Auditor
                from pilot_full import PilotClient
                loader = Auditor(PilotClient(state/'http', offline=True)).document
            print(encode(prepare(campaign, plan, write=a.prepare,
                                 skip_examined=a.skip_examined, load_document=loader,
                                 skip_unavailable=a.skip_unavailable,
                                 append_to_open=a.append_to_open)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
