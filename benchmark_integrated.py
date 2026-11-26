"""Paired retrieval comparison on the frozen 50 new-URL citations.

Same pinned input, independent retrieval caches/budgets, shared read-only
audit cache. Baseline: existing refined retrieval; experimental: TOC
early route + local semantic ranking + bounded hybrid queue. Document
authentication and candidate agreement are not bilingual equivalence.
"""
import argparse,json,time
from pathlib import Path
from collections import Counter
from auto_retrieve import Retrieval
from benchmark_index import one_case,aggregate,write
from bibliography import load_catalog
from chapter_routes import load_chapters
from embedding import LocalEncoder
from evidence_audit import Auditor
from fresh_mia_holdout import review_primary
from novel_url_benchmark import frozen_sample,urls,comparison,PIN
from pilot_full import PilotClient
from resolver import ROOT,norm,sha
from revision_receipt import RevisionRefresher
from sentence_index import SentenceVectorIndex
from toc_heading import TocMatcher
from wikirouge_incremental import open_current

SAMPLE_SHA='19020f67ee6fb3b31ec292082a6ec9143d287a828c5c3215e929bee000c30f1b'


def collected(row):
    return {p['url'] for a in row.get('target_attempts',[]) for p in a.get('pages',[])}


def summarize(pairs):
    s={}
    for arm in ('baseline','integrated'):
        rows=[p[arm+'_audit'] for p in pairs]
        s[arm]={'ready':sum(r['status']=='ready' for r in rows),
                'authenticated':sum(r.get('documentary_authenticity')=='verified_document_and_span' for r in rows),
                'statuses':dict(Counter(r['status'] for r in rows)),
                'review_words':sum(r.get('english_review_words',0) for r in rows),
                'semantic_equivalence':None,'false_positives':None}
    s['routes']=dict(Counter(p['integrated']['result'].get('toc_route_experiment',{}).get('status','not_attempted') for p in pairs))
    s['early_authenticated']=sum(p['early_authenticated'] for p in pairs)
    s['early_target_absent_from_baseline_collection']=sum(p['early_target_absent_from_baseline_collection'] for p in pairs)
    s['early_target_differs_from_baseline_primary']=sum(p['early_target_differs_from_baseline_primary'] for p in pairs)
    s['comparison']=dict(Counter(p['comparison']['agreement'] for p in pairs))
    s['hybrid_first']=sum(p['integrated']['result'].get('review_packet',{}).get('method')=='experimental_hybrid_first_v1' for p in pairs)
    return s


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model-dir',type=Path,required=True)
    p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--sample-file',type=Path,required=True)
    p.add_argument('--out',type=Path,default=ROOT/'reports-live/integrated-mia.json')
    a=p.parse_args();raw=a.sample_file.read_bytes()
    if sha(raw)!=SAMPLE_SHA or json.loads(raw)!=frozen_sample():
        raise ValueError('sample_mismatch')
    frozen=json.loads(raw);cases=frozen['sample'];pairs=[];start=time.monotonic()
    a.cache.mkdir(parents=True,exist_ok=True)
    enc=LocalEncoder(a.model_dir,a.cache/'vectors.sqlite3');sem=SentenceVectorIndex(enc)
    clients={k:PilotClient(a.cache/k,budget=50*32) for k in ('baseline','integrated')}
    matcher=TocMatcher(clients['integrated'],enc)
    ac=PilotClient(a.cache/'audit',budget=50*24);audit=Auditor(ac);refresh=RevisionRefresher(ac)
    report={'schema':'wikidebia-integrated-mia-1','status':'running','sample_sha256':SAMPLE_SHA,'inventory_sha256':PIN,
            'selection':frozen['selection'],'sample':cases,'pairs':pairs,'model_id':enc.model_id,
            'rights':'unreviewed','auto_publish':False,'hosted_model_calls':0}
    write(a.out,report)
    try:
        with open_current() as index:
            common=dict(catalog=load_catalog(ROOT/'bibliographic-catalog.json'),local_index=index,refined=True,
                        chapter_catalog=load_chapters(ROOT/'chapter-catalog.json'))
            engines={'baseline':Retrieval(clients['baseline'],16,**common),
                     'integrated':Retrieval(clients['integrated'],16,**common,semantic_index=sem,toc_matcher=matcher,hybrid=True)}
            report['index_sha256']=index.fingerprint
            order=sorted(cases,key=lambda c:(norm(c['metadata']['authors']),norm(c['metadata']['work']),urls(c),c['id']))
            for i,case in enumerate(order):
                pair={'id':case['id']}
                arms=('baseline','integrated') if i%2==0 else ('integrated','baseline')
                for arm in arms:
                    pair[arm]=one_case(engines[arm],case,32)
                    old=ac.budget;ac.budget=min(old,ac.requests+12)
                    try: receipt,_=review_primary(case,pair[arm]['result'],audit,refresh)
                    finally:ac.budget=old
                    pair[arm+'_audit']=receipt
                left,right=pair['baseline']['result'],pair['integrated']['result']
                l,r=pair['baseline_audit'],pair['integrated_audit']
                route=right.get('toc_route_experiment',{})
                target=route.get('pair',{}).get('en',{}).get('url')
                early=route.get('status')=='early_chapter_candidate' and r.get('status')=='ready' and r.get('target',{}).get('url')==target
                pair.update(comparison=comparison(l,r),early_authenticated=early,
                            early_target_absent_from_baseline_collection=bool(early and target not in collected(left)),
                            early_target_differs_from_baseline_primary=bool(early and target!=l.get('target',{}).get('url')))
                pairs.append(pair)
                report['aggregate']=aggregate(pairs,('baseline','integrated'))
                report['summary']=summarize(pairs);write(a.out,report)
                print(json.dumps({'completed':i+1,'id':case['id'],'route':route.get('status','not_attempted'),
                                  'baseline':l['status'],'integrated':r['status'],'early_authenticated':early,
                                  'new_document':pair['early_target_absent_from_baseline_collection']}),flush=True)
        errors=[p['id'] for p in pairs if any(p[arm]['result']['status']=='execution_error' for arm in ('baseline','integrated'))]
        report.update(status='candidate_comparison_complete' if not errors else 'candidate_comparison_incomplete',
                      execution_errors=errors,elapsed_seconds=round(time.monotonic()-start,3),
                      encoder_stats=enc.stats.copy(),document_index_builds=sem.builds,document_index_hits=sem.hits,
                      toc_matches=len(matcher.matches),content_probes=len(matcher.content_probes),
                      audit_requests=ac.requests,audit_decoded_bytes=ac.network_body_bytes,
                      actual_hosted_ai_tokens=None,actual_hosted_ai_cost=None,semantic_equivalence=None)
        write(a.out,report)
        print(json.dumps({k:v for k,v in report.items() if k not in ('sample','pairs')},indent=2))
        print('report_sha256='+sha(a.out.read_bytes()))
        return int(bool(errors))
    finally:enc.close()

if __name__=='__main__':raise SystemExit(main())
