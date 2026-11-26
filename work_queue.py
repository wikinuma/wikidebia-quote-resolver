"""Conservative offline work/source research queue. Never approves a citation."""
import argparse
import json
from collections import Counter
from pathlib import Path

from navigation import canonical_mia
from review_memory import ACCEPT, load_effective_memory, check_hash


def identity(meta):
    urls = meta['mia_urls']
    if not urls or any(canonical_mia(u) != u for u in urls):
        raise ValueError('non_mia_case')
    return (meta['authors'], meta['work'], meta['article'], tuple(sorted(urls)))


def build_queue(memory, research):
    if memory.get('auto_publish') is not False or research.get('auto_publish') is not False:
        raise ValueError('unsafe_scope')
    if research.get('schema') != 'wikidebia-mia-work-research-1':
        raise ValueError('unknown_research_schema')
    notes = {}
    for entry in research['entries']:
        key = identity(entry)
        if key in notes:
            raise ValueError('duplicate_work_note')
        if entry['status'] not in {'wrong_work_excluded', 'bibliographic_leads_only'}:
            raise ValueError('unknown_research_status')
        notes[key] = {k: entry[k] for k in ('status', 'evidence', 'note')}
    groups = {}
    excluded = 0
    seen = set()
    for case in memory['cases']:
        if case['id'] in seen or case['auto_publish'] is not False:
            raise ValueError('unsafe_or_duplicate_case')
        seen.add(case['id'])
        meta = case['metadata']
        key = identity(meta)
        if case['latest_decision'] in ACCEPT:
            excluded += 1
            continue
        ref = case['citation_ref']
        check_hash(ref['quote_sha256'])
        check_hash(ref['template_sha256'])
        if key not in groups:
            groups[key] = dict(zip(('authors','work','article','mia_urls'), key))
            groups[key].update(cases=[], research=notes.get(key))
        groups[key]['cases'].append({
            'case_id': case['id'], 'decision': case['latest_decision'],
            'quote_sha256': ref['quote_sha256'],
            'template_sha256': ref['template_sha256'],
        })
    out = []
    for key, group in groups.items():
        group['cases'].sort(key=lambda c: c['case_id'])
        group['count'] = len(group['cases'])
        group['decisions'] = dict(Counter(c['decision'] for c in group['cases']))
        out.append(group)
    out.sort(key=lambda g: (-g['count'], identity(g)))
    unresolved = sum(g['count'] for g in out)
    if unresolved != memory['summary']['unresolved'] or excluded != memory['summary']['historically_accepted']:
        raise ValueError('queue_count_mismatch')
    return {'schema':'wikidebia-mia-work-queue-1',
            'summary':{'unresolved':unresolved,'excluded_accepted':excluded,
                       'exact_work_source_groups':len(out),
                       'cases_with_research_note':sum(g['count'] for g in out if g['research'])},
            'groups':out,'auto_publish':False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, default=Path('data/mia'))
    p.add_argument('--out', type=Path)
    a = p.parse_args()
    research = json.loads((a.directory/'work-research.json').read_text())
    queue = build_queue(load_effective_memory(a.directory), research)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(queue, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(queue['summary'], sort_keys=True))


if __name__ == '__main__':
    main()
