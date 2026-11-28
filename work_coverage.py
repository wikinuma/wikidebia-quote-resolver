"""Work-level coverage of explicit Marxists.org citations on Wikidébats.

Use the pinned, same-day 854-occurrence inventory instead of crawling all
pages again. No English quotation labels, text, offsets, or fingerprints
are persisted in this report. Count source identities, not inferred works.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from benchmark_index import write
from bibliography import VERIFIED_AUTHORS, load_catalog, match_catalog
from chapter_routes import load_chapters
from navigation import author_check, canonical_mia, discover_case
from pilot_full import PilotClient
from resolver import ROOT, sha
from wikirouge_incremental import open_current
from work_library import WorkLibrary, metadata_projection

BASE = ROOT/'data/mia/benchmarks/20261126/inventory.json'
PIN = '06de13853f1d18b3c16a011a22849efa4cbee3f9b56ba493faf157a31654b286'


def key(meta):
    return (meta['authors'], meta['work'], meta['article'], tuple(sorted(set(meta['mia_urls']))))


def group_cases(cases):
    groups = defaultdict(list)
    seen = set()
    for c in cases:
        m = c['metadata']
        if c['id'] in seen or c['stratum'] != 'explicit_mia' or not m['mia_urls'] or any(canonical_mia(u) != u for u in m['mia_urls']):
            raise ValueError('invalid_mia_inventory')
        seen.add(c['id'])
        groups[key(m)].append(c)
    return {k: sorted(v, key=lambda c:c['id']) for k,v in groups.items()}


def classify(groups, index, catalog, chapters, research):
    result = []
    for k, cases in groups.items():
        m = cases[0]['metadata']
        lookup, route = index.route(m)
        manual = match_catalog(m, catalog)
        known_chapters = [c for c in chapters
                          if set(m['mia_urls']) & {c['fr_url'], c['fr_toc']}
                          and author_check(m['authors'],c['authors']) in VERIFIED_AUTHORS]
        notes = [r for r in research if key(r) == k]
        if route is not None:
            state = 'index_route_candidate'
        elif manual is not None and manual['target_url']:
            state = 'researched_document_route'
        elif known_chapters:
            state = 'chapter_leads_only'
        elif manual is not None or notes:
            state = 'bibliographic_research_only'
        else:
            state = 'unresolved'
        result.append({
            'authors':m['authors'], 'work':m['work'], 'article':m['article'],
            'mia_urls':list(k[3]), 'dates':sorted({c['metadata']['date'] for c in cases if c['metadata']['date']}),
            'citation_occurrences':len(cases),
            'distinct_quotes_within_group':len({c['citation_ref']['quote_sha256'] for c in cases}),
            'status':state, 'index_status':lookup['status'],
            'index_candidate_count':len(lookup['candidates']),
            'index_reasons':sorted({r for c in lookup['candidates'] for r in c['reasons']}),
            'index_route':None if route is None else {
                'route':route['route'],
                'work_metadata':{l:metadata_projection(route['work_metadata'][l]) for l in ('fr','en')}},
            'catalog_entry':manual, 'chapter_leads':known_chapters, 'research_notes':notes,
            'rights':'unreviewed','auto_publish':False
        })
    return sorted(result,key=lambda r:(-r['citation_occurrences'],key(r)))


def summarize(rows,cases):
    counts = Counter(r['status'] for r in rows)
    occurrences = Counter()
    for r in rows:
        occurrences[r['status']] += r['citation_occurrences']
    return {
        'citation_occurrences':len(cases),
        'distinct_quote_fingerprints':len({c['citation_ref']['quote_sha256'] for c in cases}),
        'distinct_mia_urls':len({u for c in cases for u in c['metadata']['mia_urls']}),
        'exact_bibliographic_source_groups':len(rows),
        'groups_with_multiple_distinct_quotes':sum(r['distinct_quotes_within_group']>1 for r in rows),
        'groups_by_status':dict(counts), 'occurrences_by_status':dict(occurrences)
    }


def probe_unknown(rows,groups,index,limit):
    """Only actual index misses, not works deliberately hidden from index.
    One current public pinned-input discovery per previously undocumented group.
    Never store a negative finding as proof of nonexistence.
    """
    selected = [r for r in rows if r['status']=='unresolved'][:limit]
    output = []
    with TemporaryDirectory(prefix='work-coverage-') as tmp, WorkLibrary(Path(tmp)/'works.sqlite3') as library:
        for i,row in enumerate(selected):
            cases=groups[key(row)]
            case=cases[0]
            client=PilotClient(Path(tmp)/('http-'+str(i)),budget=20)
            nav=discover_case(client,{k:case[k] for k in ('id','wiki_title','citation_ref')},
                              local_index=index,work_library=library)
            learned=library.lookup(case['metadata'],index.fingerprint)
            output.append({
                'authors':row['authors'],'work':row['work'],'article':row['article'],'mia_urls':row['mia_urls'],
                'citation_occurrences':row['citation_occurrences'],
                'status':nav['status'],'route_method':nav.get('route_method'),
                'route':nav.get('route'), 'learned_reusable_route':learned is not None,
                'target_accessible':nav.get('mia_target',{}).get('url_accessible'),
                'target_url':nav.get('mia_target',{}).get('requested_url'),
                'requests':client.requests,'decoded_bytes':client.network_body_bytes,
                'error':nav.get('error'), 'library_error':nav.get('work_library_error'),
                'rights':'unreviewed','auto_publish':False
            })
        learned_routes=library.export()['works']
    return {'selected_groups':len(selected),'unprobed_unresolved_groups':sum(r['status']=='unresolved' for r in rows)-len(selected),
            'statuses':dict(Counter(r['status'] for r in output)),
            'learned_reusable_routes':len(learned_routes),
            'network_requests':sum(r['requests'] for r in output),
            'groups':output,'learned_work_metadata':learned_routes}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=ROOT/'reports-live/work-coverage.json')
    p.add_argument('--live-limit',type=int,default=0)
    a=p.parse_args()
    if not 0<=a.live_limit<=20:p.error('live limit must be 0..20')
    raw=BASE.read_bytes()
    if sha(raw)!=PIN:raise ValueError('inventory_pin_mismatch')
    inventory=json.loads(raw)
    cases=inventory['cases'];groups=group_cases(cases)
    catalog=load_catalog(ROOT/'bibliographic-catalog.json')
    chapters=load_chapters(ROOT/'chapter-catalog.json')
    research=json.loads((ROOT/'data/works/research.json').read_text(encoding='utf-8'))['entries']
    with open_current() as index:
        rows=classify(groups,index,catalog,chapters,research)
        report={'schema':'wikidebia-work-coverage-1','inventory_sha256':PIN,'index_sha256':index.fingerprint,
                'inventory_status':inventory['status'],
                'inventory_pages_inspected':inventory['discovery']['pages_inspected'],
                'summary':summarize(rows,cases),'groups':rows,
                'scope':'bibliographic candidate coverage only; exact source/author/title groups are not distinct intellectual works; no quotation equivalence or rights approval',
                'hosted_model_calls':0,'auto_publish':False}
        write(a.out,report)
        print(json.dumps(report['summary'],ensure_ascii=False),flush=True)
        # Logs are a compact work-level table, without any quotation identifiers.
        for r in rows:
            print(json.dumps({k:r[k] for k in ('authors','work','article','mia_urls','citation_occurrences','status','index_status','index_reasons')},ensure_ascii=False),flush=True)
        if a.live_limit:
            report['live_probe']=probe_unknown(rows,groups,index,a.live_limit)
            write(a.out,report)
            print(json.dumps({'live_probe':report['live_probe']},ensure_ascii=False),flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
