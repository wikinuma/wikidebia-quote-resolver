"""Private, grouped review of existing MIA-triggered retrieval results.

The AI sees each work, chapter, and candidate passage once. Its reply contains
only identifiers, decisions and half-open character ranges. A separate local
manifest holds the exact source/target identities and is never edited by AI.
Neither selection nor registration authorizes publication.
"""
from collections import Counter
import argparse
import json
import os
from pathlib import Path
import tempfile

from alignment import text_and_starts
from alignment_store import encode
from citation_registry import (CitationRegistry, DECISIONS, fingerprint,
                               source_identity, target_identity, selected_fragments)
from evidence_audit import Auditor, load_report, read_span
from navigation import extract_citation_ref, raw_parse, error_kind
from pilot_full import PilotClient
from resolver import ROOT, WD_API
from review_context import adaptive_target, expand_target, coalesce_contexts
from work_library import WorkLibrary

PACKET = 'wikidebia-grouped-citation-review-1'
REPLY = 'wikidebia-grouped-citation-selection-1'
REJECT = frozenset({'no_equivalent', 'uncertain'})


def pinned_loader(client):
    pages = {}

    def load(title, revision):
        if revision not in pages:
            pages[revision] = raw_parse(client, WD_API, oldid=revision)
        actual, rev, raw = pages[revision]
        if rev != revision or actual.replace('_', ' ') != title.replace('_', ' '):
            raise ValueError('pinned_page_mismatch')
        return raw

    return load


def candidate_queue(result):
    # The existing first-choice queue takes precedence, followed by its
    # structural and lexical alternatives. No new bibliographic search.
    return (result.get('review_packet', {}).get('candidates', [])
            + result.get('section_alignment', {}).get('candidates', [])
            + result.get('candidates', []))


def build_packet(items, load_raw, load_document, *, max_candidates=4,
                 max_passage_chars=15000, max_payload_chars=120000,
                 distinct_occurrences=False, expanded_context=False, adaptive_context=False):
    """items: (case, retrieval_result) pairs; loaders return pinned raw page
    and verified-parser Document respectively. Failures remain unresolved.
    All text is private; the caller must keep the manifest locally.
    """
    if min(max_candidates, max_passage_chars, max_payload_chars) < 1:
        raise ValueError('invalid_packet_limits')
    if expanded_context and adaptive_context:
        raise ValueError('conflicting_context_modes')
    with_context = expanded_context or adaptive_context
    sources, targets, documents = {}, {}, {}
    seed_keys = {}
    groups, passage_view, source_ids, target_ids, doc_ids = {}, {}, {}, {}, {}
    skipped, seen = [], set()
    docs = {}
    expanded_count = 0
    for case, result in items:
        cid = case['id']
        if cid in seen or result.get('id') != cid:
            raise ValueError('duplicate_or_mismatched_case')
        seen.add(cid)
        ref = case['citation_ref']
        try:
            raw = load_raw(case['wiki_title'], ref['revision'])
            meta = extract_citation_ref(raw, ref, include_text=True, include_link_policy=True)
            if meta['link_policy']['route'] != 'mia':
                skipped.append({'case_id': cid, 'status': 'ai_translation_required'})
                continue
            source = source_identity(raw, ref)
            if len(meta['text']) > max_passage_chars:
                raise ValueError('source_length_limit')
        except Exception as exc:
            skipped.append({'case_id': cid, 'status': 'source_unavailable',
                            'error': error_kind(exc)})
            continue
        source_key = source['source_key']
        occurrence = {'case_id': cid, 'wiki_title': case['wiki_title'], 'ref': ref}
        review_key = cid if distinct_occurrences else source_key
        if review_key not in source_ids:
            qid = 'q' + str(len(source_ids) + 1)
            source_ids[review_key] = qid
            # Exact verified bibliographic fields; absent titles cannot
            # accidentally merge unrelated works. Chapter URL stays literal
            # to the routing policy, not a guessed title or root.
            work = meta['work'] or meta['article']
            work_key = (meta['authors'], work, '' if work else source['mia_url'])
            chapter_key = (source['mia_url'], meta['article'])
            group = groups.setdefault(work_key, {})
            chapter = group.setdefault(chapter_key, [])
            row = {'id': qid, 'fr': meta['text'], 'date': meta['date'],
                   'candidates': [], 'source_statuses': []}
            if distinct_occurrences:
                row['wiki_title'] = case['wiki_title']
            chapter.append(row)
            sources[qid] = {'source_key': source_key, 'occurrences': [],
                            'candidates': [], 'unavailable_candidates': 0}
        else:
            qid = source_ids[review_key]
        local = sources[qid]
        verified_seeds = seed_keys.setdefault(qid, set())
        local['occurrences'].append(occurrence)
        status = result.get('source', {}).get('status', 'unconfirmed')
        local.setdefault('source_statuses', [])
        if status not in local['source_statuses']:
            local['source_statuses'].append(status)
        for candidate in candidate_queue(result):
            if len(verified_seeds) >= max_candidates:
                local['candidate_limit_reached'] = True
                break
            try:
                url = candidate['url']
                # Check supported URL before any target read.
                from evidence_audit import document_provider
                document_provider(url, 'en')
                if url not in docs:
                    docs[url] = load_document(url, 'en')
                doc = docs[url]
                seed = target_identity(doc, candidate)
                if seed['candidate_key'] in verified_seeds:
                    continue
                target = seed
                _, _, passage = read_span(doc, candidate)
                if len(passage) > max_passage_chars:
                    raise ValueError('target_length_limit')
                if with_context:
                    target = (adaptive_target(doc, seed, meta['text'], max_passage_chars)
                              if adaptive_context else
                              expand_target(doc, seed, len(meta['text']), max_passage_chars))
                    if target != seed:
                        expanded_count += 1
                        text, _ = text_and_starts(doc)
                        passage = text[target['char_start']:target['char_end']]
            except Exception:
                local['unavailable_candidates'] += 1
                continue
            verified_seeds.add(seed['candidate_key'])
            key = target['candidate_key']
            if key not in target_ids:
                pid = 'p' + str(len(target_ids) + 1)
                target_ids[key] = pid
                identity = target['document']
                doc_key = fingerprint(identity)
                if doc_key not in doc_ids:
                    did = 'd' + str(len(doc_ids) + 1)
                    doc_ids[doc_key] = did
                    documents[did] = identity
                did = doc_ids[doc_key]
                targets[pid] = target
                passage_view[pid] = {'document': did, 'text': passage, 'length': len(passage)}
            pid = target_ids[key]
            if pid not in local['candidates']:
                local['candidates'].append(pid)
            if with_context:
                ranges = local.setdefault('seed_hints', {}).setdefault(pid, [])
                span = [seed['char_start'], seed['char_end']]
                if span not in ranges:
                    ranges.append(span)
    context_stats = None
    if with_context:
        context_stats = coalesce_contexts(targets, passage_view, sources, docs,
                                          max_passage_chars)
        context_stats['expanded_seed_attempts'] = expanded_count
    work_view = []
    for (authors, work, _), chapters in groups.items():
        chapter_view = []
        for (url, article), rows in chapters.items():
            ready = []
            for row in rows:
                local = sources[row['id']]
                if not local['candidates']:
                    skipped.extend({'case_id': o['case_id'], 'status': 'no_verified_candidate'}
                                   for o in local['occurrences'])
                    continue
                view = {**row, 'candidates': local['candidates'],
                        'source_statuses': local['source_statuses']}
                if with_context:
                    view['seed_ranges'] = local['seed_ranges']
                ready.append(view)
            if ready:
                chapter_view.append({'fr_url': url, 'article': article, 'citations': ready})
        if chapter_view:
            work_view.append({'authors': authors, 'work': work, 'chapters': chapter_view})
    sources = {q: s for q, s in sources.items() if s['candidates']}
    payload = {'groups': work_view, 'passages': passage_view,
               'documents': {k: v['url'] for k, v in documents.items()}}
    if context_stats is not None:
        payload['context_stats'] = context_stats
    if len(encode(payload)) > max_payload_chars:
        raise ValueError('review_payload_limit_split_input')
    return {'schema': PACKET, 'payload': payload, 'sources': sources,
            'targets': targets, 'skipped': skipped, 'rights': 'unreviewed',
            'auto_publish': False}


def review_view(packet):
    """Only this compact view goes to the reviewer; no raw pages or hashes
    repeated for each candidate. The local manifest must remain unchanged.
    """
    return {'schema': PACKET, 'packet_sha256': fingerprint(packet),
            'reply_schema': REPLY,
            'instructions': ('For every citation choose one listed candidate or abstain. '
                             'Return decisions only: id, decision, candidate, ranges. '
                             'Accepted decisions: complete_equivalent, boundary_adjustment, '
                             'omission_adjustment. Ranges are half-open Unicode character '
                             'offsets relative to the exact candidate text, not its document. '
                             'For a complete candidate use [0,length]. Omission requires '
                             'explicit French ellipsis. For uncertainty or no equivalent use '
                             'uncertain or no_equivalent, candidate=null, ranges=[]. '
                             'Never copy or translate the passage in the reply. '
                             'Compare full meaning, attribution and boundaries; no publication approval.'
                             + (' Seed ranges are original search anchors, not selection limits. '
                                'Review the full expanded passage for missing beginnings or endings; '
                                'select only the exact equivalent ranges.'
                                if 'context_stats' in packet['payload'] else '')),
            **packet['payload']}


def apply_review(packet, reply, expected_sha, registry, load_raw, load_document):
    """Validate the entire reply before adding any accepted receipt. An
    interrupted DB write is retryable/idempotent; no wiki write is possible.
    expected_sha comes from the trusted local preparation, not the reply.
    """
    if (packet.get('schema') != PACKET or fingerprint(packet) != expected_sha
            or reply.get('schema') != REPLY or reply.get('packet_sha256') != expected_sha
            or set(reply) != {'schema', 'packet_sha256', 'reviewer', 'decisions'}):
        raise ValueError('review_packet_mismatch')
    reviewer = reply['reviewer']
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 120:
        raise ValueError('missing_reviewer_identity')
    decisions = reply['decisions']
    if not isinstance(decisions, list):
        raise ValueError('invalid_decisions')
    by_id = {}
    for row in decisions:
        if not isinstance(row, dict) or set(row) != {'id', 'decision', 'candidate', 'ranges'}:
            raise ValueError('invalid_decision_fields')
        qid = row['id']
        if qid in by_id or qid not in packet['sources']:
            raise ValueError('unexpected_or_duplicate_citation')
        by_id[qid] = row
    if set(by_id) != set(packet['sources']):
        raise ValueError('incomplete_review')
    pending, docs = [], {}
    counts = Counter()
    for qid, row in by_id.items():
        decision, pid, ranges = row['decision'], row['candidate'], row['ranges']
        if decision in REJECT:
            if pid is not None or ranges != []:
                raise ValueError('abstention_has_passage')
            counts[decision] += 1
            continue
        source_row = packet['sources'][qid]
        if decision not in DECISIONS or pid not in source_row['candidates']:
            raise ValueError('candidate_not_offered_for_citation')
        occurrence = source_row['occurrences'][0]
        ref = occurrence['ref']
        raw = load_raw(occurrence['wiki_title'], ref['revision'])
        source = source_identity(raw, ref)
        if source['source_key'] != source_row['source_key']:
            raise ValueError('source_evidence_changed')
        target = packet['targets'][pid]
        identity = target['document']
        url = identity['url']
        if url not in docs:
            docs[url] = load_document(url, 'en')
        doc = docs[url]
        candidate = {'url': url, 'revision': identity['revision'],
                     'sha256': identity['body_sha256'],
                     'text_sha256': target['text_sha256'],
                     'char_start': target['char_start'], 'char_end': target['char_end']}
        if target_identity(doc, candidate) != target:
            raise ValueError('target_evidence_changed')
        selected_fragments(source, target, doc, decision, ranges)
        review = {'source_key': source['source_key'], 'candidate_key': target['candidate_key'],
                  'review_packet_sha256': expected_sha, 'reviewer': reviewer,
                  'decision': decision, 'ranges': ranges}
        pending.append((raw, ref, doc, candidate, review))
        counts[decision] += 1
    receipts = [registry.record(*args) for args in pending]
    return {'counts': dict(counts), 'recorded': len(receipts), 'receipts': receipts,
            'rights': 'unreviewed', 'auto_publish': False}


def write_private(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                   'w', encoding='utf-8') as f:
        f.write(encode(value) + '\n')


def private_dir(path):
    path = path.resolve()
    root = ROOT.resolve()
    if path == root or (path.is_relative_to(root) and
                         not path.is_relative_to(root / '.cache')):
        raise ValueError('private_output_must_be_outside_repo_or_in_dot_cache')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('prepare')
    a.add_argument('--report', nargs=2, action='append', required=True,
                   metavar=('FILE', 'SHA256'))
    a.add_argument('--out', type=Path, required=True)
    a.add_argument('--max-payload-chars', type=int, default=120000)
    b = sub.add_parser('apply')
    b.add_argument('--packet', type=Path, required=True)
    b.add_argument('--packet-sha', required=True)
    b.add_argument('--reply', type=Path, required=True)
    b.add_argument('--db', type=Path, required=True)
    args = p.parse_args()
    with tempfile.TemporaryDirectory(prefix='private-grouped-review-') as tmp:
        auditor = Auditor(PilotClient(Path(tmp), budget=500))
        loader = pinned_loader(auditor.client)
        if args.command == 'prepare':
            items = []
            for path, digest in args.report:
                report = load_report(path, digest, 'refined')
                items.extend((c, pair['refined']['result'])
                             for c, pair in zip(report['sample'], report['pairs']))
            packet = build_packet(items, loader, auditor.document,
                                  max_payload_chars=args.max_payload_chars)
            out = private_dir(args.out)
            write_private(out / 'manifest.json', packet)
            write_private(out / 'review.json', review_view(packet))
            print(json.dumps({'packet_sha256': fingerprint(packet),
                              'sources': len(packet['sources']),
                              'passages': len(packet['targets']),
                              'skipped': dict(Counter(s['status'] for s in packet['skipped'])),
                              'rights': 'unreviewed', 'auto_publish': False}))
        else:
            packet = json.loads(args.packet.read_text(encoding='utf-8'))
            reply = json.loads(args.reply.read_text(encoding='utf-8'))
            with WorkLibrary(args.db) as library:
                result = apply_review(packet, reply, args.packet_sha,
                                      CitationRegistry(library.db), loader, auditor.document)
            print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
