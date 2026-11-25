#!/usr/bin/env python3
"""Development-only paired recheck of the exact previously selected MIA cohort.

The refined catalogue was researched after inspecting the baseline results.
This is NOT an independent generalization or a verified accuracy benchmark.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import time

from auto_retrieve import Retrieval
from benchmark_index import aggregate, one_case, write
from bibliography import load_catalog
from navigation import canonical_mia
from pilot_full import PilotClient
from resolver import ROOT, norm
from wikirouge_index import open_snapshot, DEFAULT

BASELINE_SHA='c800e9e37586d760a10c14bbb037e6e4a493fd5c130694b9723914678812ecea'
ARMS=('indexed','refined')


def pinned_cases(path, expected_sha, snapshot_sha):
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha:
        raise ValueError('baseline_report_sha256_mismatch')
    report=json.loads(raw)
    if report.get('schema')!='wikidebia-paired-index-1' or report.get('scope')!='explicit_mia_only' or report.get('status')!='candidate_comparison_complete' or report.get('snapshot_sha256')!=snapshot_sha:
        raise ValueError('baseline_scope_mismatch')
    cases=report['sample']
    if len(cases)<50 or len(cases)>80 or len({c['id'] for c in cases})!=len(cases):
        raise ValueError('invalid_baseline_sample')
    if len({c['citation_ref']['quote_sha256'] for c in cases})!=len(cases):
        raise ValueError('duplicate_baseline_quotation')
    for c in cases:
        urls=c['metadata']['mia_urls']
        if c.get('stratum')!='explicit_mia' or not urls or any(canonical_mia(u)!=u for u in urls):
            raise ValueError('non_mia_baseline_case')
    return cases


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline-report',type=Path,required=True)
    p.add_argument('--baseline-sha',default=BASELINE_SHA)
    p.add_argument('--manifest',type=Path,default=DEFAULT)
    p.add_argument('--catalog',type=Path,default=ROOT/'bibliographic-catalog.json')
    p.add_argument('--case-budget',type=int,default=32)
    p.add_argument('--max-work-pages',type=int,default=16)
    p.add_argument('--out',type=Path,default=ROOT/'reports-live'/'refined-index.json')
    p.add_argument('--fresh-holdout',action='store_true')
    p.add_argument('--holdout-sample',type=int,default=50)
    p.add_argument('--scan-pages',type=int,default=1200)
    a=p.parse_args()
    if not 1<=a.case_budget<=60 or not 1<=a.max_work_pages<=24 or not 50<=a.holdout_sample<=80 or not 1<=a.scan_pages<=3000:
        p.error('bounded positive limits required')
    with tempfile.TemporaryDirectory(prefix='refined-index-') as tmp, open_snapshot(a.manifest) as index:
        cases=pinned_cases(a.baseline_report,a.baseline_sha,index.fingerprint)
        catalog=load_catalog(a.catalog)
        discovery=None
        if a.fresh_holdout:
            from holdout_mia import discover_holdout
            discover=PilotClient(Path(tmp)/'discover',budget=400+(a.scan_pages+9)//10+50)
            started=time.monotonic()
            cases,diag=discover_holdout(discover,a.manifest,cases,catalog,a.holdout_sample,a.scan_pages)
            discovery={**diag,'network_requests':discover.requests,'decoded_network_body_bytes':discover.network_body_bytes,
                       'elapsed_seconds':round(time.monotonic()-started,4)}
            print(json.dumps({k:v for k,v in discovery.items() if k!='inventory'}),flush=True)
        clients={arm:PilotClient(Path(tmp)/arm,budget=len(cases)*a.case_budget) for arm in ARMS}
        engines={arm:Retrieval(c,a.max_work_pages,catalog=catalog if arm=='refined' else (),
                              local_index=index,refined=arm=='refined') for arm,c in clients.items()}
        report={'schema':'wikidebia-mia-holdout-1' if a.fresh_holdout else 'wikidebia-refined-index-1','scope':'explicit_mia_only',
                'evaluation_scope':('fresh quotation fingerprints; report overlap with development works/pages/URLs' if a.fresh_holdout else
                                    'development recheck on frozen 50 citations; catalog derived after baseline inspection'),
                'discovery':discovery,
                'baseline_report_sha256':a.baseline_sha,'snapshot_sha256':index.fingerprint,
                'sample':cases,'catalog_sha256':hashlib.sha256(a.catalog.read_bytes()).hexdigest(),
                'bounds':{'case_requests':a.case_budget,'max_work_pages':a.max_work_pages},
                'metric_contract':{'model_calls':0,'verification':'candidates only; independent accuracy and rights not measured',
                    'first_pass_words':'English words in prioritized packet; indexed baseline receives all distinct candidates; alternatives remain available in refined arm; excludes French/context and later fallback; not LLM tokens',
                    'work_candidate':'local/live work route or researched bibliographic lead; not verified passage'},
                'pairs':[],'auto_publish':False,'status':'running'}
        write(a.out,report)
        if a.fresh_holdout and len(cases)<a.holdout_sample:
            report['status']='insufficient_fresh_sample'; write(a.out,report)
            return 1
        start=time.monotonic()
        for i,c in enumerate(cases):
            pair={'id':c['id'],'stratum':c['stratum']}
            if a.fresh_holdout: pair['holdout_group']=c['holdout_group']
            for arm in (ARMS if i%2==0 else tuple(reversed(ARMS))):
                pair[arm]=one_case(engines[arm],c,a.case_budget)
            report['pairs'].append(pair)
            report['aggregate']=aggregate(report['pairs'],ARMS)
            write(a.out,report)
            print(json.dumps({'completed':i+1,'selected':len(cases),
                              **{arm:{'status':pair[arm]['result']['status'],**pair[arm]['metrics']} for arm in ARMS}}),flush=True)
        report['alignment_cache']={arm:{'hits':engines[arm].direct_cache.hits,
                            'computations':engines[arm].direct_cache.computations} for arm in ARMS}
        bad=[(p['id'],arm) for p in report['pairs'] for arm in ARMS if
             p[arm]['result'].get('status')=='execution_error' or
             p[arm]['result'].get('navigation',{}).get('status')=='input_unavailable']
        report['input_or_execution_failures']=bad
        report['elapsed_seconds']=round(time.monotonic()-start,3)
        report['status']=('holdout_candidate_comparison_complete' if a.fresh_holdout else 'development_comparison_complete') if not bad else 'comparison_incomplete'
        if a.fresh_holdout:
            report['by_novelty']={g:aggregate([p for p in report['pairs'] if p['holdout_group']==g],ARMS)
                                  for g in discovery['by_novelty']}
        write(a.out,report)
        print(json.dumps({k:report[k] for k in ('status','aggregate','alignment_cache','input_or_execution_failures','elapsed_seconds','by_novelty') if k in report},indent=2),flush=True)
        return int(bool(bad))


if __name__=='__main__':
    raise SystemExit(main())
