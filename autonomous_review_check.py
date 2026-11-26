"""Read-only replay of frozen real citation candidates on GitHub Actions.

No fresh inventory, no new retrieval, no model, no production state, and no
quotation text or review archive in public logs/artifacts. The three arms use
the same pinned source revisions and freshly fetched English documents.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from alignment_store import encode
from citation_registry import fingerprint
from boundary_stress import measure_boundary_recovery
from evidence_audit import Auditor
from grouped_review import build_packet, candidate_queue, pinned_loader, review_view
from pilot_full import PilotClient
from prepare_citations import build_campaign_packet, COMPACT, FALLBACK
from resolver import ROOT, sha
from review_bundle import write_bundle, read_packet
from reviewed_boundaries import load_references, reviewed_boundary_summary, revalidate_reference
from transcription_compare import supplement_packet, summarize_transcriptions

REPORT = ROOT/'data/mia/benchmarks/20261126/report.json'
REPORT_SHA = '04b035052345d0ce86b45d52a20344fcd6a4e9ab1af0a1258109011687715e86'


def load_items(*, include_without_candidates=False):
    data = REPORT.read_bytes()
    if sha(data) != REPORT_SHA:
        raise ValueError('frozen_report_changed')
    report = json.loads(data)
    cases = {c['id']: c for c in report['sample']}
    results = {p['id']: p['refined']['result'] for p in report['pairs']}
    if len(cases) != 50 or len(report['pairs']) != 50 or set(cases) != set(results):
        raise ValueError('frozen_cohort_mismatch')
    return [(c, results[cid]) for cid,c in cases.items()
            if include_without_candidates or candidate_queue(results[cid])]


def seed_inventory(packet):
    """Compare exact verified anchors, not merged review-window counts."""
    result = set()
    for source in packet['sources'].values():
        for pid in source['candidates']:
            target = packet['targets'][pid]
            base = target['char_start']
            ranges = source.get('seed_ranges', {}).get(
                pid, [[0, target['char_end']-base]])
            for lo, hi in ranges:
                if not 0 <= lo < hi <= target['char_end']-base:
                    raise ValueError('invalid_seed_range')
                for occurrence in source['occurrences']:
                    result.add((occurrence['case_id'],
                                fingerprint(target['document']), base+lo, base+hi))
    return result


def progressive_cost(items, baseline_pairs, load_raw, audit, *, compact_packets_out=None):
    """Measure actual serialized review sizes under hypothetical abstentions.

    No linguistic decisions or positive registry receipts are manufactured.
    Escalation subsets are nested and selected by a stable hash of case ID,
    independent of candidate text/rank/size. Rates are scenarios, not forecasts.
    """
    baseline_seeds = set().union(*(seed_inventory(p) for _,p in baseline_pairs))
    eligible = {seed[0] for seed in baseline_seeds}
    cases = [(c,r) for c,r in items if c['id'] in eligible]
    baseline_bytes = sum(len(encode(review_view(p)).encode('utf-8'))
                         for _,p in baseline_pairs)

    def measure(selected, stage):
        packets = []

        def prepare(batch):
            try:
                packet = build_campaign_packet(batch,load_raw,audit.document,stage)
            except ValueError as exc:
                if str(exc) != 'review_payload_limit_split_input' or len(batch) < 2:
                    raise
                mid = len(batch)//2
                prepare(batch[:mid])
                prepare(batch[mid:])
                return
            if packet['skipped']:
                raise ValueError('progressive_replay_lost_verified_occurrence')
            packets.append(packet)

        for i in range(0,len(selected),6):
            prepare(selected[i:i+6])
        seeds = set().union(*(seed_inventory(p) for p in packets))
        ids = {c['id'] for c,_ in selected}
        expected = {seed for seed in baseline_seeds if seed[0] in ids}
        if stage == COMPACT:
            if not seeds <= expected or Counter(x[0] for x in seeds) != Counter(ids):
                raise ValueError('compact_replay_seed_mismatch')
        elif seeds != expected:
            raise ValueError('fallback_replay_seed_mismatch')
        if stage == COMPACT and compact_packets_out is not None:
            compact_packets_out.extend(packets)
        return {'occurrences':len(ids),'packets':len(packets),'verified_seeds':len(seeds),
                'review_utf8_bytes':sum(len(encode(review_view(p)).encode('utf-8'))
                                         for p in packets),
                'english_characters':sum(v['length'] for p in packets
                                           for v in p['payload']['passages'].values())}

    compact = measure(cases,COMPACT)
    order = sorted(eligible,key=lambda cid:(sha(cid.encode()),cid))
    scenarios = []
    for pct in (0,25,50,75,100):
        n = (len(order)*pct+50)//100
        subset = set(order[:n])
        fallback = measure([(c,r) for c,r in cases if c['id'] in subset],FALLBACK)
        total = compact['review_utf8_bytes']+fallback['review_utf8_bytes']
        scenarios.append({'nominal_abstention_pct':pct,'fallback':fallback,
                          'total_review_utf8_bytes':total,
                          'saving_vs_expanded_pct':round(100*(1-total/baseline_bytes),2)
                          if baseline_bytes else None})
    return {'scope':'hypothetical_abstention_rates_on_same_verified_occurrences',
            'baseline_expanded_review_utf8_bytes':baseline_bytes,
            'compact':compact,'scenarios':scenarios,
            'observed_abstention_rate':None,'semantic_equivalence':None,
            'hosted_model_calls':0,'auto_publish':False}


def transcription_replay(packets, audit, directory, mode):
    """Exercise the optional packet path with the same verified ordinary seeds."""
    counts = Counter()
    extra_offers = extra_chars = 0
    pairs = []
    old_bytes = new_bytes = 0
    for packet in packets:
        supplemented = supplement_packet(packet,audit.document)
        if not seed_inventory(packet) <= seed_inventory(supplemented):
            raise ValueError('transcription_replay_original_seed_lost')
        if any(supplemented['targets'].get(pid) != target or
               supplemented['payload']['passages'].get(pid) != packet['payload']['passages'][pid]
               for pid,target in packet['targets'].items()):
            raise ValueError('transcription_replay_original_target_changed')
        stats = supplemented['payload'].get('transcription_comparison',{})
        counts.update(stats.get('statuses',{}))
        extra_offers += stats.get('additional_candidate_offers',0)
        extra_chars += stats.get('additional_english_characters',0)
        old_bytes += len(encode(review_view(packet)).encode('utf-8'))
        new_bytes += len(encode(review_view(supplemented)).encode('utf-8'))
        pairs.append((fingerprint(supplemented),supplemented))
    if pairs:
        path = directory/('transcription-'+mode+'.zip')
        write_bundle(path,pairs,REPORT_SHA)
        for i,(_,packet) in enumerate(pairs,1):
            if read_packet(path,i) != review_view(packet):
                raise ValueError('transcription_replay_bundle_changed')
        path.unlink()
    return {'statuses':dict(counts),'additional_candidate_offers':extra_offers,
            'additional_english_characters':extra_chars,'original_review_utf8_bytes':old_bytes,
            'supplemented_review_utf8_bytes':new_bytes,'verified_roundtrips':len(pairs),
            'semantic_equivalence':None,'rights_approved':0,'auto_publish':False}


def run(items, load_raw, audit, directory, *, references=None, reference_items=None):
    arms = {'original': [], 'expanded': [], 'adaptive': []}
    skipped = {arm: Counter() for arm in arms}
    overflow = 0

    def prepare(batch):
        nonlocal overflow
        try:
            packets = {arm: build_packet(batch,load_raw,audit.document,
                       distinct_occurrences=True,expanded_context=arm=='expanded',
                       adaptive_context=arm=='adaptive')
                       for arm in arms}
        except ValueError as exc:
            if str(exc) != 'review_payload_limit_split_input':
                raise
            if len(batch) == 1:
                overflow += 1
                return
            mid = len(batch)//2
            prepare(batch[:mid])
            prepare(batch[mid:])
            return
        expected = {c['id'] for c,_ in batch}
        inventories = [seed_inventory(p) for p in packets.values()]
        if any(x != inventories[0] for x in inventories[1:]):
            raise ValueError('review_arms_have_different_verified_seeds')
        for arm,p in packets.items():
            ids = [o['case_id'] for s in p['sources'].values() for o in s['occurrences']]
            fails = [x['case_id'] for x in p['skipped']]
            if Counter(ids+fails) != Counter(expected):
                raise ValueError('occurrence_accounting_mismatch')
            skipped[arm].update(x['status'] for x in p['skipped'])
            if ids:
                arms[arm].append((fingerprint(p),p))

    for i in range(0,len(items),6):
        prepare(items[i:i+6])

    summary = {}
    ready = {}
    for arm,pairs in arms.items():
        ready[arm] = {o['case_id'] for _,p in pairs for s in p['sources'].values()
                      for o in s['occurrences']}
        stats = {'packets':len(pairs),'occurrences':len(ready[arm]),
                 'skipped':dict(skipped[arm]),
                 'verified_seed_count':sum(len(seed_inventory(p)) for _,p in pairs),
                 'review_utf8_bytes':sum(len(encode(review_view(p)).encode('utf-8'))
                                         for _,p in pairs),
                 'characters':sum(v['length'] for _,p in pairs
                                  for v in p['payload']['passages'].values())}
        if pairs:
            path = directory/(arm+'.zip')
            index = write_bundle(path,pairs,REPORT_SHA)
            for n,(digest,p) in enumerate(pairs,1):
                if fingerprint(p) != digest or read_packet(path,n) != review_view(p):
                    raise ValueError('bundle_roundtrip_failed')
            stats.update(shared_characters=index['english_chars_after_sharing'],
                         shared_windows=index['shared_windows'],
                         verified_roundtrips=len(pairs))
            path.unlink()
        if stats['occurrences'] + sum(stats['skipped'].values()) + overflow != len(items):
            raise ValueError('total_occurrence_accounting_mismatch')
        summary[arm] = stats
    compact_packets = []
    costs = progressive_cost(items,arms['expanded'],load_raw,audit,
                             compact_packets_out=compact_packets)
    result = {'frozen_report_sha256':REPORT_SHA,'frozen_cohort':50,
            'selected_with_candidates':len(items),'single_packet_overflow':overflow,
            'arms':summary,'ready_in_both':len(ready['original'] & ready['expanded']),
            'ready_in_all':len(set.intersection(*ready.values())),
            'lost_after_adaptation':len(ready['original'] - ready['adaptive']),
            'gained_after_adaptation':len(ready['adaptive'] - ready['original']),
            'lost_after_expansion':len(ready['original'] - ready['expanded']),
            'gained_after_expansion':len(ready['expanded'] - ready['original']),
            'boundary_stress':measure_boundary_recovery(
                [packet for _,packet in arms['original']],audit),
            'progressive_cost':costs,
            'semantic_equivalence':None,'rights_approved':0,
            'hosted_model_calls':0,'auto_publish':False}
    if references is not None:
        windows = {mode:[p for _,p in pairs] for mode,pairs in arms.items()}
        windows['compact'] = compact_packets
        result['reviewed_boundaries'] = reviewed_boundary_summary(
            references,reference_items if reference_items is not None else items,
            audit,windows)
        cases = {c['id']:(c,r) for c,r in
                 (reference_items if reference_items is not None else items)}
        targets = [revalidate_reference(e,*cases[e['case_id']],audit) for e in references]
        result['transcription_comparison'] = {
            'reference_intervals':summarize_transcriptions(targets,audit.document),
            'compact':transcription_replay(compact_packets,audit,directory,'compact'),
            'expanded':transcription_replay(windows['expanded'],audit,directory,'expanded'),
        }
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    args = p.parse_args()
    with TemporaryDirectory(prefix='autonomous-review-check-') as tmp:
        directory = Path(tmp)
        client = PilotClient(directory/'http',budget=500,delay=.25)
        audit = Auditor(client)
        all_items = load_items(include_without_candidates=True)
        references = load_references(REPORT.with_name('reviewed-boundaries.json'),REPORT_SHA)
        summary = run([(c,r) for c,r in all_items if candidate_queue(r)],
                      pinned_loader(client),audit,directory,
                      references=references,reference_items=all_items)
        summary['fresh_requests'] = client.requests
        summary['request_errors'] = sum(e.get('status')=='error' for e in client.events)
    # Whitelisted aggregate output only. Do not write packets or local state.
    args.out.write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,sort_keys=True))
    if not all(arm['occurrences'] for arm in summary['arms'].values()):
        return 2  # do not claim a successful real-source replay with no data
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
