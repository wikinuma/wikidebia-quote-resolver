#!/usr/bin/env python3
"""Autonomous private MIA preparation campaign, using the existing resolver.

No model calls, wiki writes, publication or assumption of reuse rights.
Each inventory occurrence has its own resumable lifecycle. The same SQLite
database contains the work library, alignments, accepted receipts and campaign
history. Only a separately adjudicated bounded search can be closed negative.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

from alignment_store import encode
from auto_retrieve import Retrieval
from bibliography import load_catalog
from chapter_routes import load_chapters
from citation_inventory import atomic_private, refresh, validate_previous
from citation_registry import CitationRegistry, DECISIONS, fingerprint, source_identity
from evidence_audit import Auditor
from grouped_review import (apply_review, build_packet, candidate_queue,
                            pinned_loader, private_dir, review_view)
from navigation import error_kind
from pilot_full import PilotClient, redact_text
from resolver import Client, ROOT
from wikirouge_incremental import open_current
from work_library import WorkLibrary

QUEUED = 'queued'
CANDIDATES = 'review_pending'
AWAITING = 'awaiting_review'
REVIEWED = 'reviewed_receipt_rights_unreviewed'
RETRY = 'retry_documentary'
INSUFFICIENT = 'insufficient_evidence'
CLOSED = 'closed_without_result'
STATES = (QUEUED, CANDIDATES, AWAITING, REVIEWED, RETRY, INSUFFICIENT, CLOSED)


def load_inventory(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    validate_previous(value)
    ids = [c['id'] for c in value['cases']]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate_inventory_occurrence')
    return value


def duplicate_groups(inventory):
    groups = defaultdict(list)
    for case in inventory['cases']:
        groups[case['citation_ref']['quote_sha256']].append(case)
    return [{'quote_sha256': key,
             'occurrences': [{'id': c['id'], 'wiki_title': c['wiki_title']}
                             for c in rows]}
            for key, rows in sorted(groups.items())
            if len({c['wiki_title'] for c in rows}) > 1]


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


class Campaign:
    def __init__(self, library, inventory):
        self.db = library.db
        self.registry = CitationRegistry(self.db)
        self.inventory = inventory
        self.cases = {c['id']: c for c in inventory['cases']}
        if len(self.cases) != len(inventory['cases']):
            raise ValueError('duplicate_inventory_occurrence')
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS campaign_events_v1 '
                            '(seq INTEGER PRIMARY KEY, occurrence TEXT NOT NULL, '
                            'state TEXT NOT NULL, evidence TEXT NOT NULL, at REAL NOT NULL)')
            self.db.execute('CREATE INDEX IF NOT EXISTS campaign_events_occ_v1 '
                            'ON campaign_events_v1(occurrence,seq)')
            self.db.execute('CREATE TABLE IF NOT EXISTS campaign_packets_v1 '
                            '(digest TEXT PRIMARY KEY, packet TEXT NOT NULL, '
                            'state TEXT NOT NULL, reply TEXT)')

    def latest(self, cid):
        row = self.db.execute('SELECT state,evidence FROM campaign_events_v1 '
                              'WHERE occurrence=? ORDER BY seq DESC LIMIT 1',
                              (cid,)).fetchone()
        return (row[0], json.loads(row[1])) if row else (QUEUED, {})

    def _append(self, cid, state, evidence):
        if cid not in self.cases or state not in STATES:
            raise ValueError('invalid_campaign_transition')
        self.db.execute('INSERT INTO campaign_events_v1 '
                        '(occurrence,state,evidence,at) VALUES (?,?,?,?)',
                        (cid, state, encode(evidence), time.time()))

    def record(self, cid, state, evidence):
        with self.db:
            self._append(cid, state, evidence)

    def select(self, states, limit=0):
        rows = [c for c in self.cases.values() if self.latest(c['id'])[0] in states]
        return rows[:limit] if limit else rows

    def summary(self):
        counts = Counter(self.latest(cid)[0] for cid in self.cases)
        anomalies = duplicate_groups(self.inventory)
        issues = Counter()
        for page in self.inventory['pages']:
            issues.update(page['citation_issues'])
        return {'inventory_sha256': self.inventory['manifest_sha256'],
                'occurrences': len(self.cases),
                'unrepresented_mia_occurrences': sum(issues.values()),
                'inventory_issues': dict(issues),
                'states': {s: counts[s] for s in STATES},
                'duplicate_quote_groups_across_pages': len(anomalies),
                'duplicate_quote_occurrences': sum(len(g['occurrences']) for g in anomalies),
                'hosted_model_calls': 0, 'auto_publish': False}

    def search_one(self, case, retrieval, load_raw, audit):
        cid = case['id']
        ref = case['citation_ref']
        start = len(retrieval.client.events)
        try:
            raw = load_raw(case['wiki_title'], ref['revision'])
            if source_identity(raw, ref)['source_key'] != case['source_key']:
                raise ValueError('inventory_source_identity_mismatch')
            found = self.registry.lookup(raw, ref)
            if found['status'] == 'ambiguous_reviewed_passages':
                self.record(cid, INSUFFICIENT, {'reason': 'conflicting_reviewed_passages'})
                return INSUFFICIENT
            if found['status'] == 'reviewed_receipt_found':
                url = found['receipt']['target']['document']['url']
                doc = audit.document(url, 'en')
                restored = self.registry.reconstruct(raw, ref, doc)
                if restored['status'] == 'reviewed_passage_reconstructed':
                    self.record(cid, REVIEWED, {
                        'origin': 'revalidated_existing_receipt',
                        'receipt_sha256': restored['receipt']['entry_sha256'],
                        'publication': audit.publication.get(('en', url), {}),
                        'rights': 'unreviewed'})
                    return REVIEWED
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
            self.record(cid, state, evidence)
            return state
        except Exception as exc:
            self.record(cid, RETRY, {'reason': error_kind(exc)})
            return RETRY

    def open_packet(self):
        row = self.db.execute("SELECT digest,packet FROM campaign_packets_v1 "
                              "WHERE state='open' ORDER BY rowid LIMIT 1").fetchone()
        if row is None:
            return None
        digest, body = row
        packet = json.loads(body)
        if fingerprint(packet) != digest:
            raise ValueError('stored_packet_digest_mismatch')
        return digest, packet

    def prepare(self, load_raw, audit, limit=20, max_payload_chars=120000):
        existing = self.open_packet()
        if existing is not None:
            return existing
        selected = self.select({CANDIDATES}, limit)
        while selected:
            items = [(c, self.latest(c['id'])[1]['result']) for c in selected]
            try:
                packet = build_packet(items, load_raw, audit.document,
                                      distinct_occurrences=True,
                                      max_payload_chars=max_payload_chars)
            except ValueError as exc:
                if str(exc) != 'review_payload_limit_split_input':
                    raise
                if len(selected) > 1:
                    selected = selected[:max(1, len(selected)//2)]
                    continue
                self.record(selected[0]['id'], INSUFFICIENT,
                            {'reason': 'single_review_payload_limit'})
                return None
            included = [o['case_id'] for s in packet['sources'].values()
                        for o in s['occurrences']]
            skipped = [s['case_id'] for s in packet['skipped']]
            if Counter(included + skipped) != Counter(c['id'] for c in selected):
                raise ValueError('review_occurrence_accounting_mismatch')
            packet['campaign_batch'] = uuid.uuid4().hex
            digest = fingerprint(packet)
            with self.db:
                for failure in packet['skipped']:
                    cid = failure['case_id']
                    old = self.latest(cid)[1]
                    self._append(cid, RETRY, {**old, 'review_skip': failure})
                if included:
                    self.db.execute('INSERT INTO campaign_packets_v1 VALUES (?,?,?,NULL)',
                                    (digest, encode(packet), 'open'))
                    for cid in included:
                        self._append(cid, AWAITING, {'packet_sha256': digest})
            return (digest, packet) if included else None
        return None

    def apply(self, reply, load_raw, audit):
        digest = reply['packet_sha256']
        row = self.db.execute('SELECT packet,state,reply FROM campaign_packets_v1 '
                              'WHERE digest=?', (digest,)).fetchone()
        if not row:
            raise ValueError('unknown_campaign_packet')
        packet, state, prior_reply = json.loads(row[0]), row[1], row[2]
        if fingerprint(packet) != digest:
            raise ValueError('stored_packet_digest_mismatch')
        if state == 'applied':
            if prior_reply != encode(reply):
                raise ValueError('packet_already_applied_with_different_reply')
            return {'status': 'already_applied', 'packet_sha256': digest}
        if state != 'open':
            raise ValueError('campaign_packet_not_open')
        # An inventory refresh must not silently promote changed occurrences.
        for source in packet['sources'].values():
            for occurrence in source['occurrences']:
                cid = occurrence['case_id']
                case = self.cases.get(cid)
                if (not case or case['citation_ref'] != occurrence['ref']
                        or case['wiki_title'] != occurrence['wiki_title']
                        or self.latest(cid) != (AWAITING, {'packet_sha256': digest})):
                    raise ValueError('campaign_packet_occurrence_changed')
        result = apply_review(packet, reply, digest, self.registry, load_raw, audit.document)
        updates = []
        for decision in reply['decisions']:
            source = packet['sources'][decision['id']]
            for occurrence in source['occurrences']:
                evidence = {'packet_sha256': digest, 'decision': decision['decision'],
                            'reviewer': reply['reviewer']}
                status = INSUFFICIENT
                if decision['decision'] in DECISIONS:
                    target = packet['targets'][decision['candidate']]['document']
                    raw = load_raw(occurrence['wiki_title'], occurrence['ref']['revision'])
                    found = self.registry.lookup(raw, occurrence['ref'], preferred_document=target)
                    if found['status'] == 'reviewed_receipt_found':
                        status = REVIEWED
                        evidence.update(receipt_sha256=found['receipt']['entry_sha256'],
                                        publication=audit.publication.get(('en', target['url']), {}),
                                        rights='unreviewed')
                    else:
                        evidence['reason'] = found['status']
                else:
                    evidence['reason'] = 'review_abstention_not_exhaustive'
                updates.append((occurrence['case_id'], status, evidence))
        # Accepted registry inserts are idempotent. If interrupted before this
        # transaction, reapplying the same valid reply finishes the transition.
        with self.db:
            for cid, status, evidence in updates:
                self._append(cid, status, evidence)
            self.db.execute("UPDATE campaign_packets_v1 SET state='applied',reply=? "
                            'WHERE digest=?', (encode(reply), digest))
        return result

    def cancel_review(self, reason):
        existing = self.open_packet()
        if existing is None or not reason.strip():
            raise ValueError('open_packet_and_cancellation_reason_required')
        digest, packet = existing
        with self.db:
            for source in packet['sources'].values():
                for occurrence in source['occurrences']:
                    cid = occurrence['case_id']
                    if cid in self.cases and self.latest(cid)[0] == AWAITING:
                        self._append(cid, RETRY, {'reason': 'review_cancelled',
                                                  'packet_sha256': digest,
                                                  'detail': reason.strip()})
            self.db.execute("UPDATE campaign_packets_v1 SET state='cancelled' "
                            'WHERE digest=?', (digest,))

    def carry_unchanged(self, previous):
        """Carry exact unchanged occurrence evidence across revisions on the
        same page only, never across pages or ambiguous repeated citations.
        Positive receipts are deliberately rechecked by search."""
        if not previous or previous['algorithm'] != self.inventory['algorithm']:
            return 0
        def positions(inventory):
            result = defaultdict(list)
            for page in inventory['pages']:
                for case in page['cases']:
                    result[(page['pageid'], case['source_key'])].append(case['id'])
            return result
        old, new = positions(previous), positions(self.inventory)
        count = 0
        with self.db:
            for key, ids in new.items():
                before = old.get(key, [])
                if len(ids) != 1 or len(before) != 1 or ids == before:
                    continue
                cid, prior = ids[0], before[0]
                if self.latest(cid)[0] != QUEUED:
                    continue
                status, evidence = self.latest(prior)
                if status not in {RETRY, INSUFFICIENT, CLOSED, CANDIDATES}:
                    continue
                if status == CANDIDATES:
                    if evidence.get('result', {}).get('id') != prior:
                        raise ValueError('prior_candidate_occurrence_mismatch')
                    evidence['result']['id'] = cid
                self._append(cid, status, {**evidence, 'carried_from': prior})
                count += 1
        return count

    def close_negative(self, cid, reason):
        if (cid not in self.cases or self.latest(cid)[0] != INSUFFICIENT
                or not isinstance(reason, str) or not reason.strip()):
            raise ValueError('negative_closure_requires_reviewed_scope_and_reason')
        self.record(cid, CLOSED, {'reason': reason.strip(),
                                  'scope': 'operator_adjudicated_documentary_search',
                                  'absence_of_translation_proven': False})


def show(value):
    print(json.dumps(value, ensure_ascii=False, sort_keys=True), flush=True)


def execute(args, state, library):
    path = state/'inventory.json'
    if args.command == 'inventory':
        # Do not invalidate pending reviews on an incremental refresh.
        exists = library.db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                    "AND name='campaign_packets_v1'").fetchone()
        if exists and library.db.execute("SELECT 1 FROM campaign_packets_v1 WHERE state='open'").fetchone():
            raise ValueError('apply_open_review_before_refresh')
        previous = load_inventory(path) if path.exists() else None
        with tempfile.TemporaryDirectory() as tmp:
            report, manifest = refresh(Client(Path(tmp), budget=args.budget, delay=.25),
                                       previous)
        if manifest is not None:
            campaign = Campaign(library, manifest)
            report['unchanged_occurrences_carried'] = campaign.carry_unchanged(previous)
            atomic_private(path, manifest)
            atomic_private(state/'duplicate-anomalies.json', duplicate_groups(manifest))
        show(report)
        return int(manifest is None)
    campaign = Campaign(library, load_inventory(path))
    if args.command == 'status':
        show(campaign.summary())
        return 0
    if args.command == 'cases':
        for case in campaign.select(set(args.filter or STATES), args.limit):
            status, evidence = campaign.latest(case['id'])
            show({'id': case['id'], 'wiki_title': case['wiki_title'],
                  'mia_url': case['mia_url'], 'state': status,
                  'reason': evidence.get('reason'),
                  'retrieval_status': evidence.get('result', {}).get('status')})
        return 0
    if args.command == 'cancel-review':
        campaign.cancel_review(args.reason)
        show(campaign.summary())
        return 0
    if args.command == 'close-negative':
        campaign.close_negative(args.occurrence, args.reason)
        show(campaign.summary())
        return 0
    with tempfile.TemporaryDirectory(prefix='mia-fresh-evidence-') as tmp:
        audit = Auditor(PilotClient(Path(tmp), budget=args.audit_budget))
        fresh = pinned_loader(audit.client)
        if args.command == 'search':
            states = {QUEUED}
            if args.retry_documentary:
                states = {RETRY}
            if args.reconsider:
                states = {INSUFFICIENT}
            if args.recheck_reviewed:
                states = {REVIEWED}
            selected = campaign.select(states, args.limit)
            client = PilotClient(state/'http', budget=args.budget)
            raw = pinned_loader(client)
            with open_current() as index:
                retrieval = Retrieval(client, args.max_pages,
                                      load_catalog(ROOT/'bibliographic-catalog.json'),
                                      index, True, load_chapters(ROOT/'chapter-catalog.json'),
                                      work_library=library, strict_link_policy=True)
                for n, case in enumerate(selected, 1):
                    if client.requests >= args.budget:
                        break
                    client.budget = min(args.budget, client.requests + args.case_budget)
                    outcome = campaign.search_one(case, retrieval, raw, audit)
                    show({'processed_in_run': n, 'selected': len(selected),
                          'occurrence': case['id'], 'state': outcome,
                          'network_requests': client.requests,
                          'fresh_audit_requests': audit.client.requests})
            show(campaign.summary())
        elif args.command == 'review':
            batch = campaign.prepare(fresh, audit, args.limit, args.max_payload_chars)
            if batch:
                digest, packet = batch
                out = state/'outgoing'/('review-'+digest[:16]+'.json')
                atomic_private(out, review_view(packet))
                show({'review_file': str(out), 'packet_sha256': digest,
                      'occurrences': sum(len(s['occurrences']) for s in packet['sources'].values())})
            show(campaign.summary())
        elif args.command == 'apply':
            reply = json.loads(args.reply.read_text(encoding='utf-8'))
            show(campaign.apply(reply, fresh, audit))
            show(campaign.summary())
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state', type=Path, required=True,
                   help='Private directory outside Git or within .cache')
    p.add_argument('--audit-budget', type=int, default=500)
    sub = p.add_subparsers(dest='command', required=True)
    inv = sub.add_parser('inventory')
    inv.add_argument('--budget', type=int, default=1200)
    sub.add_parser('status')
    listing = sub.add_parser('cases')
    listing.add_argument('--filter', choices=STATES, action='append')
    listing.add_argument('--limit', type=int, default=0)
    cancel = sub.add_parser('cancel-review')
    cancel.add_argument('--reason', required=True)
    run = sub.add_parser('search')
    run.add_argument('--limit', type=int, default=50, help='0 means all matching occurrences')
    run.add_argument('--budget', type=int, default=2500)
    run.add_argument('--case-budget', type=int, default=300)
    run.add_argument('--max-pages', type=int, default=64)
    mode = run.add_mutually_exclusive_group()
    mode.add_argument('--retry-documentary', action='store_true')
    mode.add_argument('--reconsider', action='store_true')
    mode.add_argument('--recheck-reviewed', action='store_true')
    review = sub.add_parser('review')
    review.add_argument('--limit', type=int, default=20)
    review.add_argument('--max-payload-chars', type=int, default=120000)
    apply = sub.add_parser('apply')
    apply.add_argument('reply', type=Path)
    negative = sub.add_parser('close-negative')
    negative.add_argument('occurrence')
    negative.add_argument('--reason', required=True)
    a = p.parse_args()
    if (any(getattr(a, k, 1) < 1 for k in
            ('budget', 'audit_budget', 'case_budget', 'max_pages', 'max_payload_chars'))
            or getattr(a, 'limit', 0) < 0):
        p.error('invalid processing limits')
    os.umask(0o077)
    state = private_dir(a.state)
    with (state/'campaign.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with WorkLibrary(state/'works.sqlite3') as library:
            return execute(a, state, library)


if __name__ == '__main__':
    raise SystemExit(main())
