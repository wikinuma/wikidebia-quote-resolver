"""Paired holdout evaluation of the frozen engine, not a tuning benchmark.

Exclude exact quote/template fingerprints and IDs from all 200 prior cases.
Balance authors, work labels, page titles and source URLs without any
retrieval outcome. All source URLs have appeared in earlier cohorts:
this tests new quotations, not new works or fully independent editions.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import time

from auto_retrieve import Retrieval
from benchmark_index import one_case,aggregate,write
from benchmark_integrated import collected,summarize
from bibliography import load_catalog
from chapter_routes import load_chapters
from embedding import LocalEncoder
from evidence_audit import Auditor
from fresh_mia_holdout import review_primary
from novel_url_benchmark import BASE,PIN,frozen_sample,urls,comparison
from pilot_full import PilotClient
from resolver import ROOT,sha,norm
from review_memory import load_effective_memory,check_hash
from revision_receipt import RevisionRefresher
from sentence_index import SentenceVectorIndex
from toc_structure import AugmentedTocMatcher
from wikirouge_incremental import open_current

def select(inventory,previous,n=50):
    known_ids={c['id'] for c in previous}
    known_q={c['citation_ref']['quote_sha256'] for c in previous}
    known_t={c['citation_ref']['template_sha256'] for c in previous}
    known_pages={norm(c['wiki_title']) for c in previous}
    known_urls={u for c in previous for u in urls(c)}
    prior_authors=Counter(norm(c['metadata']['authors']) for c in previous)
    def tie(c):return sha(('independent-holdout-v1:'+c['id']).encode())
    remaining=[];seen_q=set();seen_t=set()
    for c in sorted(inventory,key=lambda c:(tie(c),c['id'])):
        if c['stratum']!='explicit_mia':raise ValueError('non_mia_inventory')
        urls(c)
        q,t=(c['citation_ref'][k] for k in ('quote_sha256','template_sha256'))
        check_hash(q);check_hash(t)
        if (c['id'] in known_ids or q in known_q or t in known_t or
                q in seen_q or t in seen_t or not c['metadata']['quote_words']):
            continue
        remaining.append(c);seen_q.add(q);seen_t.add(t)
    eligible=len(remaining);chosen=[]
    authors=Counter();works=Counter();pages=Counter();sources=Counter()
    def keys(c):
        m=c['metadata'];a=norm(m['authors']);u=urls(c)
        w=(a,norm(m['work'] or m['article']) or repr(u))
        return a,w,norm(c['wiki_title']),u
    def score(c):
        a,w,p,u=keys(c)
        return authors[a],works[w],pages[p],int(p in known_pages),sources[u],prior_authors[a],tie(c),c['id']
    while remaining and len(chosen)<n:
        c=min(remaining,key=score);remaining.remove(c);chosen.append(c)
        a,w,p,u=keys(c);authors[a]+=1;works[w]+=1;pages[p]+=1;sources[u]+=1
    if len(chosen)!=n:raise ValueError('insufficient_holdout')
    return chosen,{'prior_cases':len(previous),'eligible_distinct_quotes':eligible,'selected':n,
                   'selected_author_labels':dict(Counter(c['metadata']['authors'] for c in chosen)),
                   'selected_work_groups':len(works),'selected_page_titles':len(pages),
                   'selected_distinct_urls':len({u for c in chosen for u in urls(c)}),
                   'selected_new_page_titles':len(set(pages)-known_pages),
                   'selected_novel_urls':len({u for c in chosen for u in urls(c)}-known_urls),
                   'prior_quote_overlap':0,'prior_template_overlap':0,
                   'scope':'Balanced new exact quote/template fingerprints, not a random corpus estimate or work-disjoint sample'}

def sample():
    raw=BASE.read_bytes()
    if sha(raw)!=PIN:raise ValueError('inventory_pin_mismatch')
    inv=json.loads(raw)
    previous=load_effective_memory(ROOT/'data/mia')['cases']+inv['sample']+frozen_sample()['sample']
    if len(previous)!=200 or len({c['citation_ref']['quote_sha256'] for c in previous})!=200:
        raise ValueError('previous_cohort_mismatch')
    cases,diag=select(inv['cases'],previous)
    return {'schema':'wikidebia-independent-mia-holdout-1','inventory_sha256':PIN,
            'sample':cases,'selection':diag,'auto_publish':False}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('select','benchmark'),required=True)
    p.add_argument('--sample-file',type=Path,required=True)
    p.add_argument('--model-dir',type=Path)
    p.add_argument('--cache',type=Path)
    p.add_argument('--out',type=Path,default=ROOT/'reports-live/independent-mia.json')
    p.add_argument('--certificate',type=Path)
    p.add_argument('--packet',type=Path)
    a=p.parse_args();frozen=sample()
    if a.stage=='select':
        write(a.sample_file,frozen)
        print(json.dumps({'selection':frozen['selection'],'sample_sha256':sha(a.sample_file.read_bytes())},indent=2))
        return 0
    if not a.model_dir or not a.cache or bool(a.certificate)!=bool(a.packet):p.error('invalid benchmark options')
    raw=a.sample_file.read_bytes()
    if json.loads(raw)!=frozen:raise ValueError('sample_not_reproducible')
    cases=frozen['sample'];pairs=[];private=[];start=time.monotonic()
    a.cache.mkdir(parents=True,exist_ok=True)
    enc=LocalEncoder(a.model_dir,a.cache/'vectors.sqlite3');sem=SentenceVectorIndex(enc)
    clients={k:PilotClient(a.cache/k,budget=50*32) for k in ('baseline','integrated')}
    matcher=AugmentedTocMatcher(clients['integrated'],enc)
    ac=PilotClient(a.cache/'audit',budget=50*24);audit=Auditor(ac);refresh=RevisionRefresher(ac)
    report={'schema':'wikidebia-independent-mia-comparison-1','status':'running','sample_sha256':sha(raw),
            'inventory_sha256':PIN,'selection':frozen['selection'],'sample':cases,'pairs':pairs,
            'model_id':enc.model_id,'rights':'unreviewed','auto_publish':False,'hosted_model_calls':0}
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
                pair={'id':case['id']};packets={}
                arms=('baseline','integrated') if i%2==0 else ('integrated','baseline')
                for arm in arms:
                    pair[arm]=one_case(engines[arm],case,32)
                    old=ac.budget;ac.budget=min(old,ac.requests+12)
                    try:receipt,packet=review_primary(case,pair[arm]['result'],audit,refresh)
                    finally:ac.budget=old
                    pair[arm+'_audit']=receipt
                    if packet is not None and receipt['status']=='ready':packets[arm]=packet
                left,right=pair['baseline']['result'],pair['integrated']['result']
                l,r=pair['baseline_audit'],pair['integrated_audit']
                route=right.get('toc_route_experiment',{})
                target=route.get('pair',{}).get('en',{}).get('url')
                early=route.get('status')=='early_chapter_candidate' and r.get('status')=='ready' and r.get('target',{}).get('url')==target
                pair.update(comparison=comparison(l,r),early_authenticated=early,
                            early_target_absent_from_baseline_collection=bool(early and target not in collected(left)),
                            early_target_differs_from_baseline_primary=bool(early and target!=l.get('target',{}).get('url')))
                if packets:
                    quotes={v['fr_quote'] for v in packets.values()}
                    if len(quotes)!=1:raise ValueError('paired_quote_mismatch')
                    labels=('X','Y') if int(sha(('holdout-review-v1:'+case['id']).encode())[-1],16)%2 else ('Y','X')
                    private.append({'id':case['id'],'quote_sha256':case['citation_ref']['quote_sha256'],
                                    'fr_quote':next(iter(quotes)),
                                    **{label:{'text':packets[arm]['en_candidate'],'sha256':pair[arm+'_audit']['target']['actual_text_sha256']}
                                       for arm,label in zip(('baseline','integrated'),labels) if arm in packets}})
                pairs.append(pair)
                report['aggregate']=aggregate(pairs,('baseline','integrated'))
                report['summary']=summarize(pairs);write(a.out,report)
                print(json.dumps({'completed':i+1,'id':case['id'],'baseline':l['status'],'integrated':r['status'],
                                  'route':route.get('status','not_attempted')}),flush=True)
        errors=[p['id'] for p in pairs if any(p[arm]['result']['status']=='execution_error' for arm in ('baseline','integrated'))]
        report.update(status='complete' if not errors else 'incomplete',execution_errors=errors,
                      elapsed_seconds=round(time.monotonic()-start,3),encoder_stats=enc.stats.copy(),
                      document_index_builds=sem.builds,document_index_hits=sem.hits,
                      audit_requests=ac.requests,audit_decoded_bytes=ac.network_body_bytes,
                      actual_hosted_ai_tokens=None,actual_hosted_ai_cost=None,semantic_equivalence=None)
        write(a.out,report)
        print(json.dumps({k:v for k,v in report.items() if k not in ('sample','pairs')},indent=2))
        digest=sha(a.out.read_bytes());print('report_sha256='+digest)
        if a.certificate:
            data=json.dumps({'schema':'wikidebia-independent-mia-blind-review-1','report_sha256':digest,
                             'cases':private,'rights':'unreviewed','auto_publish':False},ensure_ascii=False,separators=(',',':')).encode()
            a.packet.parent.mkdir(parents=True,exist_ok=True)
            subprocess.run(['openssl','cms','-encrypt','-binary','-aes-256-gcm','-outform','DER',
                            '-recip',str(a.certificate),'-out',str(a.packet)],input=data,check=True)
            print(json.dumps({'packet_cases':len(private),'packet_sha256':sha(data),
                              'encrypted_sha256':sha(a.packet.read_bytes())}))
        return int(bool(errors))
    finally:enc.close()

if __name__=='__main__':raise SystemExit(main())
