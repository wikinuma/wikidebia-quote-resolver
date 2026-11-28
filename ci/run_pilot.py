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


EXPECTED_CASES = {(case, provider) for case in (
    'manifesto-i', 'manifesto-ii', 'manifesto-preface-1883', 'engels-competition'
) for provider in ('mia', 'wikirouge')}
EXPECTED_VARIANT_HASH = '04f2de087e9a38a666307d4c49d7502d541df7036f0a4a15d834d051a1b6c507'
EXPECTED_VARIANT_NORMALIZED = '093657d49a2239f79a92482b3a0d50143391377d95dec71bce97169136ada92c'


def known_documentary_abstention(report):
    """The fixed 1883 preface variant is a negative documentary control.

    Never upgrade a candidate to a confirmed occurrence. Other incomplete
    cases, missing inputs, incomplete work scopes and changed variants fail.
    """
    if report.get('status') != 'incomplete' or report.get('auto_publish') is not False:
        return False
    if report.get('provider_policy') != 'explicit_comparison':
        return False
    works = report.get('works', {})
    if set(works) != {work + ':' + p for work in ('manifesto', 'engels-condition')
                     for p in ('mia', 'wikirouge')}:
        return False
    if any(w.get('scope_complete') is not True or w.get('issues') != []
           for w in works.values()):
        return False
    if {k: v.get('status') for k, v in report.get('api_probes', {}).items()} != {
        'wikidebats': 'available', 'wikirouge_fr': 'available', 'wikirouge_en': 'available'
    }:
        return False
    cases = report.get('cases', [])
    if len(cases) != len(EXPECTED_CASES) or {
        (c.get('id'), c.get('provider')) for c in cases
    } != EXPECTED_CASES:
        return False
    abstained = 0
    for c in cases:
        if (c.get('scope_complete') is not True or c.get('pair_issues') != []
                or c.get('auto_publish') is not False or c.get('rights') != 'unreviewed'
                or not all(c.get('documents', {}).get(lang, 0) > 0 for lang in ('fr', 'en'))):
            return False
        if c.get('status') == 'review_required':
            if c.get('source_status') != 'normalized_contiguous' or not c.get('fr_occurrences'):
                return False
            continue
        if (c.get('id') != 'manifesto-preface-1883'
                or c.get('status') != 'incomplete_or_unconfirmed'
                or c.get('source_status') != 'unconfirmed' or c.get('fr_occurrences') != []):
            return False
        variants = c.get('source_diagnostics', {}).get('variant_search', {})
        candidates = variants.get('candidates', [])
        if (variants.get('status') != 'variant_review_required'
                or variants.get('auto_publish') is not False
                or variants.get('rights') != 'unreviewed'
                or variants.get('truncated') is not False
                or variants.get('candidates_truncated') is not False
                or variants.get('qualifying_hits') != 1 or len(candidates) != 1):
            return False
        v = candidates[0]
        if (v.get('text_sha256') != EXPECTED_VARIANT_HASH
                or v.get('normalized_sha256') != EXPECTED_VARIANT_NORMALIZED
                or v.get('token_edits') != 5 or v.get('query_words') != 46
                or v.get('source_words') != 49 or v.get('boundary_ambiguous') is not False):
            return False
        abstained += 1
    return abstained == 2


def regression_outcome(live, replay, live_rc, replay_rc):
    if outcome(live, replay, live_rc, replay_rc):
        return 'complete_for_review'
    integrity = replay.get('replay_integrity', {})
    if (live_rc == replay_rc == 1
            and live.get('offline') is False and replay.get('offline') is True
            and replay.get('requests') == 0
            and integrity.get('config_matches') is True
            and integrity.get('model_matches') is True
            and integrity.get('results_match') is True
            and integrity.get('response_mismatches') == []
            and known_documentary_abstention(live)
            and known_documentary_abstention(replay)):
        return 'expected_documentary_abstention'
    return 'failed'


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
    regression = regression_outcome(live, replay, live_rc, replay_rc)
    preferred_rc = subprocess.run(command + ['--offline', '--provider', 'preferred', '--out', str(preferred_path)], cwd=ROOT).returncode if live_path.is_file() else None
    preferred = read_report(preferred_path)
    # This third run is a cache-only routing experiment, not a new live benchmark.
    preferred_summary = compact(preferred)
    preferred_summary['unique_requested_urls'] = len({e['request_url'] for e in preferred.get('http_events',[])})
    preferred_summary['comparison_live_unique_requested_urls'] = len({e['request_url'] for e in live.get('http_events',[])})
    summary = {'live_exit': live_rc, 'replay_exit': replay_rc, 'pilot_complete_for_review': ok, 'regression_outcome': regression,
               'live': compact(live), 'preferred_offline_exit': preferred_rc, 'preferred_offline': preferred_summary, 'replay': {k: replay.get(k) for k in ('status', 'requests', 'cache_hits', 'elapsed_seconds', 'replay_integrity')}}
    text = json.dumps(summary, ensure_ascii=False, indent=2) + '\n'
    summary_path.write_text(text, encoding='utf-8')
    print(text, flush=True)
    if regression == 'expected_documentary_abstention':
        print('::warning::Pilot INCOMPLETE: fixed 1883 source variant correctly rejected; not complete for review.',
              flush=True)
    return int(regression == 'failed')


if __name__ == '__main__':
    raise SystemExit(main())
