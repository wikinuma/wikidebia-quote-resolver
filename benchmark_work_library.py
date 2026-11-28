"""Measure work routing for different quotes of the same documented work.

No quote label is read. Inputs are frozen historical French occurrences,
chosen by work identity and pre-existing index evidence, never by retrieval
success. First citation populates the library; the second is different.
Each simulated session has its own fresh HTTP cache in BOTH arms. Measure
only bibliographic navigation, not passage recovery or ChatGPT quota.
"""
import argparse
from collections import defaultdict
from pathlib import Path
import json
from tempfile import TemporaryDirectory
import time

from benchmark_fixtures import load_prior_cohort
from benchmark_index import write
from navigation import discover_case
from pilot_full import PilotClient
from resolver import ROOT
from work_library import WorkLibrary, identity, project_route
from wikirouge_incremental import open_current


def select_groups(cases,index,count=6):
    groups=defaultdict(list)
    for case in cases:
        m=case['metadata'];i=identity(m)
        if not i:
            continue
        attempt,route=index.route(m)
        if route is None:
            continue
        nav={**route,'input':m,'attempts':[attempt]}
        if project_route(nav,index.fingerprint) is None:
            continue
        key=(i['source_url'],tuple(i['titles']),i['authors'],i['citation_year'])
        groups[key].append(case)
    eligible=[sorted(cs,key=lambda c:c['id']) for cs in groups.values() if len(cs)>=2]
    eligible.sort(key=lambda cs:(-len(cs),cs[0]['id']))
    # Balance authors if possible; deterministic and independent of live outcomes.
    picked=[];used=set()
    for cs in eligible:
        a=cs[0]['metadata']['authors']
        if a not in used and len(picked)<count:
            picked.append(cs);used.add(a)
    for cs in eligible:
        if cs not in picked and len(picked)<count: picked.append(cs)
    if len(picked)<count:
        raise ValueError('insufficient_repeated_work_groups')
    return [cs[:2] for cs in picked],len(eligible)


class CountIndex:
    def __init__(self,index):
        self.index=index
        self.fingerprint=index.fingerprint
        self.lookups=0

    def route(self,m):
        self.lookups+=1
        return self.index.route(m)


def run_case(case,index,cache,library=None):
    client=PilotClient(cache,budget=16)
    quote_sink={}
    start=time.monotonic()
    row=discover_case(client,{k:case[k] for k in ('id','wiki_title','citation_ref')},
                      quote_sink=quote_sink,local_index=index,work_library=library)
    if row.get('work_library_error'):
        raise ValueError('work_library_error:'+row['work_library_error'])
    target=row.get('mia_target',{})
    route=row.get('route',{})
    return {'id':case['id'],'status':row['status'],'work_library_hit':row.get('work_library_hit',False),
            'fr_title':route.get('fr_title'),'en_title':route.get('en_title'),
            'fr_url':route.get('fr_url'),'en_url':route.get('en_url'),
            'target_url':target.get('requested_url'),'target_accessible':target.get('url_accessible'),
            'source_identity_verified':case['id'] in quote_sink,
            'requests':client.requests,'cache_hits':client.hits,
            'decoded_bytes':client.network_body_bytes,
            'elapsed_seconds':round(time.monotonic()-start,4)}


def signature(r):
    return tuple(r[k] for k in ('status','fr_title','en_title','fr_url','en_url','target_url','target_accessible','source_identity_verified'))


def measure(mode,groups,index,tmp):
    idx={k:CountIndex(index) if mode=='indexed' else None for k in ('baseline','library')}
    records=[];stats={}
    path=tmp/mode/'works.sqlite3'
    # Open the database independently for each citation, simulating new jobs.
    for phase in (0,1):
        for j,group in enumerate(groups):
            case=group[phase]
            pair={'phase':'learn' if phase==0 else 'new_quote','group':j,'id':case['id']}
            arms=('baseline','library') if (j+phase)%2==0 else ('library','baseline')
            for arm in arms:
                cache=tmp/mode/arm/str(phase)/str(j)
                if arm=='library':
                    with WorkLibrary(path) as lib:
                        pair[arm]=run_case(case,idx[arm],cache,lib)
                        for k,v in lib.stats.items(): stats[k]=stats.get(k,0)+v
                else:
                    pair[arm]=run_case(case,idx[arm],cache)
            pair['same_route']=signature(pair['baseline'])==signature(pair['library'])
            records.append(pair)
    with WorkLibrary(path) as lib: stored=len(lib.export()['works'])
    summary={}
    for phase in ('learn','new_quote'):
        rows=[r for r in records if r['phase']==phase]
        summary[phase]={}
        for arm in ('baseline','library'):
            summary[phase][arm]={k:round(sum(r[arm][k] for r in rows),4)
                                 for k in ('requests','cache_hits','decoded_bytes','elapsed_seconds','work_library_hit')}
        summary[phase]['same_routes']=sum(r['same_route'] for r in rows)
        summary[phase]['cases']=len(rows)
    return {'mode':mode,'summary':summary,'library_stats':stats,'stored_routes':stored,
            'index_lookups':{k:v.lookups if v else None for k,v in idx.items()},'cases':records}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=ROOT/'reports-live/work-library.json')
    p.add_argument('--groups',type=int,default=6)
    a=p.parse_args()
    if not 2<=a.groups<=8:p.error('group count must be 2..8')
    with open_current() as index,TemporaryDirectory(prefix='work-library-test-') as temp:
        groups,eligible=select_groups(load_prior_cohort()['cases'],index,a.groups)
        report={'schema':'wikidebia-work-library-benchmark-1','group_count':len(groups),
                'eligible_repeated_work_groups':eligible,'distinct_quote_fingerprints':len({c['citation_ref']['quote_sha256'] for g in groups for c in g}),
                'index_sha256':index.fingerprint,'scope':'same explicit work identities; fresh separate HTTP sessions in both arms; navigation only',
                'rights':'unreviewed','auto_publish':False,'hosted_model_calls':0,'actual_ai_quota_saved':None}
        for mode in ('indexed','live'):
            report[mode]=measure(mode,groups,index,Path(temp))
            write(a.out,report)
            print(json.dumps({'mode':mode,**{k:v for k,v in report[mode].items() if k!='cases'}},indent=2),flush=True)
        # Do not mask a changed route. A network/source issue is distinguishable
        # in the per-case report, but equality is required for this paired run.
        return int(any(not r['same_route'] for mode in ('indexed','live') for r in report[mode]['cases']))


if __name__=='__main__':
    raise SystemExit(main())
