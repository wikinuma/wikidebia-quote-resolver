#!/usr/bin/env python3
"""CI entry point: run both phases, report failures, never upload source cache."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports-live'


def read_report(path):
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def compact(report):
    works = {}
    for key, work in report.get('works', {}).items():
        works[key] = {
            'scope': work.get('scope'),
            'scope_complete': work.get('scope_complete'),
            'issues': [i.get('kind') for i in work.get('issues', [])],
            'native_langlink': work.get('api_attempt', {}).get('native_langlink', work.get('native_langlink')),
            'routes': work.get('routes', []),
            'pages': {lang: len(side.get('pages', [])) if isinstance(side, dict) else len(side)
                      for lang, side in work.get('sides', {}).items()},
        }
    cases = []
    for case in report.get('cases', []):
        d = case.get('source_diagnostics', {})
        cases.append({
            'id': case.get('id'), 'provider': case.get('provider'),
            'status': case.get('status'), 'source_status': case.get('source_status'),
            'source_occurrences': len(case.get('fr_occurrences', [])),
            'source_diagnostics': {k: d[k] for k in ('has_ellipsis', 'selector_hit_count') if k in d},
            'source_variants': {k: v for k,v in d.get('variant_search',{}).items() if k!='candidates'},
            'variant_candidates': [{k: v for k,v in c.items() if k in ('url','text_sha256','normalized_sha256','token_edits','query_words','source_words','similarity','boundary_ambiguous','match')} for c in d.get('variant_search',{}).get('candidates',[])],
            'methods': {name: {'state': m.get('evaluation', {}).get('state'),
                               'rank': m.get('evaluation', {}).get('rank'),
                               'candidates': len(m.get('candidates', []))}
                        for name, m in case.get('methods', {}).items()},
        })
    return {
        'status': report.get('status', 'missing_report'),
        'requests': report.get('requests'), 'cache_hits': report.get('cache_hits'),
        'elapsed_seconds': report.get('elapsed_seconds'),
        'api_status': {k: v.get('status') for k, v in report.get('api_probes', {}).items()},
        'works': works, 'cases': cases,
        'provider_policy': report.get('provider_policy'),
        'skipped_provider_routes': report.get('skipped_provider_routes',[]),
        'evidence_counts': {case: {'source_families': e['source_families'], 'methods': {name: {k:v for k,v in m.items() if k!='groups'} for name,m in e['methods'].items()}} for case,e in report.get('evidence_groups',{}).items()},
    }


def outcome(live, replay, live_rc, replay_rc):
    integrity = replay.get('replay_integrity', {})
    return (live_rc == replay_rc == 0 and
            live.get('status') == replay.get('status') == 'review_required' and
            live.get('offline') is False and replay.get('offline') is True and
            replay.get('requests') == 0 and
            live.get('auto_publish') is False and replay.get('auto_publish') is False and
            len(live.get('cases', [])) == len(replay.get('cases', [])) == 8 and
            integrity.get('config_matches') is True and integrity.get('model_matches') is True and
            integrity.get('results_match') is True and integrity.get('response_mismatches') == [])


def main():
    OUT.mkdir(exist_ok=True)
    live_path = OUT / 'full-work.json'
    replay_path = OUT / 'full-work-replay.json'
    summary_path = OUT / 'ci-summary.json'
    preferred_path = OUT / 'preferred-offline.json'
    # No stale output can stand in for the current run.
    for path in (live_path, replay_path, summary_path, preferred_path):
        path.unlink(missing_ok=True)
    command = [sys.executable, str(ROOT / 'pilot_full.py'), '--cache', str(ROOT / '.pilot-cache')]
    live_rc = subprocess.run(command + ['--out', str(live_path)], cwd=ROOT).returncode
    replay_rc = subprocess.run(command + ['--offline', '--replay-report', str(live_path), '--out', str(replay_path)], cwd=ROOT).returncode if live_path.is_file() else None
    live, replay = read_report(live_path), read_report(replay_path)
    ok = outcome(live, replay, live_rc, replay_rc)
    preferred_rc = subprocess.run(command + ['--offline', '--provider', 'preferred', '--out', str(preferred_path)], cwd=ROOT).returncode if live_path.is_file() else None
    preferred = read_report(preferred_path)
    # This third run is a cache-only routing experiment, not a new live benchmark.
    preferred_summary = compact(preferred)
    preferred_summary['unique_requested_urls'] = len({e['request_url'] for e in preferred.get('http_events',[])})
    preferred_summary['comparison_live_unique_requested_urls'] = len({e['request_url'] for e in live.get('http_events',[])})
    summary = {'live_exit': live_rc, 'replay_exit': replay_rc, 'pilot_complete_for_review': ok,
               'live': compact(live), 'preferred_offline_exit': preferred_rc, 'preferred_offline': preferred_summary, 'replay': {k: replay.get(k) for k in ('status', 'requests', 'cache_hits', 'elapsed_seconds', 'replay_integrity')}}
    text = json.dumps(summary, ensure_ascii=False, indent=2) + '\n'
    summary_path.write_text(text, encoding='utf-8')
    print(text, flush=True)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
