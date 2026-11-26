"""Measure reuse of the frozen historical memory on public evidence.

No new retrieval, neural encoding or semantic review; misses are counted as
cases requiring the normal fallback, not as new negative judgments.
Only redacted metrics are written. Each invocation starts with a fresh HTTP
cache. The second pass measures within-run reuse, not cross-run freshness.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time

from benchmark_index import write
from evidence_audit import Auditor
from pilot_full import PilotClient
from resolver import ROOT, norm
from review_memory import ACCEPT, load_effective_memory
from review_reuse import try_reuse
from revision_receipt import RevisionRefresher


def run_pass(cases, memory, auditor, refresher):
    c = auditor.client
    requests, hits, network_bytes = c.requests, c.hits, c.network_body_bytes
    start = time.monotonic()
    rows = []
    for case in cases:
        r = try_reuse(memory, case, auditor, refresher)
        reused = r['status'] == 'historical_equivalence_revalidated'
        rows.append({'id': case['id'], 'status': r['status'],
                     'reason': r.get('reason'), 'decision': r.get('historical_decision'),
                     'historical_accepted': case['latest_decision'] in ACCEPT,
                     'candidate_words_not_requiring_new_semantic_review':
                         r['target_receipt']['text_words'] if reused else 0,
                     'fragment_words': sum(len(norm(f['text']).split()) for f in r.get('fragments', [])),
                     'rights': 'unreviewed', 'auto_publish': False})
    return {'inputs': len(rows),
            'revalidated_hits': sum(r['status'] == 'historical_equivalence_revalidated' for r in rows),
            'fallback_required': sum(r['status'] != 'historical_equivalence_revalidated' for r in rows),
            'miss_reasons': dict(Counter(r['reason'] for r in rows if r['reason'])),
            'network_requests': c.requests-requests,
            'cache_hits': c.hits-hits,
            'decoded_network_bytes': c.network_body_bytes-network_bytes,
            'elapsed_seconds': round(time.monotonic()-start, 4),
            'candidate_words_not_requiring_new_semantic_review':
                sum(r['candidate_words_not_requiring_new_semantic_review'] for r in rows),
            'fragment_words': sum(r['fragment_words'] for r in rows),
            'cases': rows}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, default=ROOT/'reports-live/memory-reuse.json')
    a = p.parse_args()
    memory = load_effective_memory(ROOT/'data/mia')
    cases = sorted(memory['cases'], key=lambda r: (r['wiki_title'], r['id']))
    with TemporaryDirectory(prefix='fresh-memory-evidence-') as tmp:
        client = PilotClient(Path(tmp), budget=200)
        audit = Auditor(client)
        refresh = RevisionRefresher(client)
        cold = run_pass(cases, memory, audit, refresh)
        warm = run_pass(cases, memory, audit, refresh)
        if cold['cases'] != warm['cases'] or warm['network_requests'] != 0:
            raise ValueError('within_run_reuse_mismatch')
    report = {'schema': 'wikidebia-memory-reuse-benchmark-1',
              'historical_accepted': memory['summary']['historically_accepted'],
              'cold': cold, 'within_run_warm': warm,
              'hosted_model_calls': 0, 'encoded_texts': 0,
              'new_semantic_reviews': 0, 'actual_ai_quota_saved': None,
              'newly_validated_reuse_rights': 0, 'auto_publish': False}
    write(a.out, report)
    print(json.dumps({**report,
                      'cold': {k:v for k,v in cold.items() if k != 'cases'},
                      'within_run_warm': {k:v for k,v in warm.items() if k != 'cases'}}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
