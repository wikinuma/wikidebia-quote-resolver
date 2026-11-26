"""Read-only replay of frozen real citation candidates on GitHub Actions.

No fresh inventory, no new retrieval, no model, no production state, and no
quotation text or review archive in public logs/artifacts. The two arms use
the same pinned source revisions and freshly fetched English documents.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from citation_registry import fingerprint
from evidence_audit import Auditor
from grouped_review import build_packet, candidate_queue, pinned_loader, review_view
from pilot_full import PilotClient
from resolver import ROOT, sha
from review_bundle import write_bundle, read_packet

REPORT = ROOT/'data/mia/benchmarks/20261126/report.json'
REPORT_SHA = '04b035052345d0ce86b45d52a20344fcd6a4e9ab1af0a1258109011687715e86'


def load_items():
    data = REPORT.read_bytes()
    if sha(data) != REPORT_SHA:
        raise ValueError('frozen_report_changed')
    report = json.loads(data)
    cases = {c['id']: c for c in report['sample']}
    results = {p['id']: p['refined']['result'] for p in report['pairs']}
    if len(cases) != 50 or len(report['pairs']) != 50 or set(cases) != set(results):
        raise ValueError('frozen_cohort_mismatch')
    return [(c, results[cid]) for cid,c in cases.items()
            if candidate_queue(results[cid])]


def run(items, load_raw, audit, directory):
    arms = {'original': [], 'expanded': []}
    skipped = {'original': Counter(), 'expanded': Counter()}
    overflow = 0

    def prepare(batch):
        nonlocal overflow
        try:
            packets = {arm: build_packet(batch,load_raw,audit.document,
                       distinct_occurrences=True,expanded_context=arm=='expanded')
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
    return {'frozen_report_sha256':REPORT_SHA,'frozen_cohort':50,
            'selected_with_candidates':len(items),'single_packet_overflow':overflow,
            'arms':summary,'ready_in_both':len(ready['original'] & ready['expanded']),
            'lost_after_expansion':len(ready['original'] - ready['expanded']),
            'gained_after_expansion':len(ready['expanded'] - ready['original']),
            'semantic_equivalence':None,'rights_approved':0,
            'hosted_model_calls':0,'auto_publish':False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    args = p.parse_args()
    with TemporaryDirectory(prefix='autonomous-review-check-') as tmp:
        directory = Path(tmp)
        client = PilotClient(directory/'http',budget=500,delay=.25)
        audit = Auditor(client)
        summary = run(load_items(),pinned_loader(client),audit,directory)
        summary['fresh_requests'] = client.requests
        summary['request_errors'] = sum(e.get('status')=='error' for e in client.events)
    # Whitelisted aggregate output only. Do not write packets or local state.
    args.out.write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,sort_keys=True))
    if not summary['arms']['expanded']['occurrences']:
        return 2  # do not claim a successful real-source replay with no data
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
