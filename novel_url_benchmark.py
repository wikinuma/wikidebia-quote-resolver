"""Frozen novel-MIA-URL candidate benchmark; no new crawl or manual mappings.

The current engine and the optional semantic sidecar share exactly one
retrieval pass. Source/document authenticity is independent of bilingual
equivalence. No raw quotation text, embeddings or private HTTP cache leave
the runner. No historical English offsets are provided to retrieval.
"""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import time
from auto_retrieve import Retrieval
from benchmark_index import one_case,aggregate,write
from bibliography import load_catalog
from chapter_routes import load_chapters
from embedding import LocalEncoder
from evidence_audit import Auditor
from fresh_mia_holdout import review_primary
from navigation import canonical_mia
from pilot_full import PilotClient
from resolver import ROOT,norm,sha
from benchmark_fixtures import load_prior_cohort,check_hash
from revision_receipt import RevisionRefresher
from sentence_index import SentenceVectorIndex
from wikirouge_incremental import open_current

BASE=ROOT/'data/mia/benchmarks/20261126/inventory.json'
PIN='06de13853f1d18b3c16a011a22849efa4cbee3f9b56ba493faf157a31654b286'
SCHEMA='wikidebia-novel-mia-url-sample-1'


def urls(case):
    values=case['metadata']['mia_urls']
    if not values or any(canonical_mia(u)!=u for u in values):
        raise ValueError('not_explicit_mia')
    return tuple(sorted(set(values)))


def select_novel(inventory,previous,n=50):
    known_ids={c['id'] for c in previous}
    known_q={c['citation_ref']['quote_sha256'] for c in previous}
    known_t={c['citation_ref']['template_sha256'] for c in previous}
    known_urls={u for c in previous for u in urls(c)}
    groups=defaultdict(list);seen_q=set();seen_t=set();eligible=0
    for c in sorted(inventory,key=lambda c:(sha(c['id'].encode()),c['id'])):
        if c['stratum']!='explicit_mia': raise ValueError('non_mia_inventory')
        us=urls(c);ref=c['citation_ref'];q=ref['quote_sha256'];t=ref['template_sha256']
        check_hash(q);check_hash(t)
        if c['id'] in known_ids or q in known_q or t in known_t or set(us)&known_urls:
            continue
        if q in seen_q or t in seen_t or not c['metadata']['quote_words']: continue
        seen_q.add(q);seen_t.add(t);eligible+=1
        groups[us].append(c)
    # One pass per source group before allocating additional passages to it.
    # Never change membership based on retrieval results.
    chosen=[];rounds=Counter()
    while groups and len(chosen)<n:
        key=min(groups,key=lambda us:(rounds[us],sha('\n'.join(us).encode()),us))
        chosen.append(groups[key].pop(0));rounds[key]+=1
        if not groups[key]: del groups[key]
    if len(chosen)!=n: raise ValueError('insufficient_novel_url_quotes')
    return chosen,{'prior_cases':len(previous),'prior_distinct_urls':len(known_urls),
                   'eligible_distinct_quotes':eligible,
                   'eligible_distinct_urls':len({u for us in rounds for u in us}|{u for us in groups for u in us}),
                   'selected':len(chosen),'selected_distinct_urls':len({u for c in chosen for u in urls(c)}),
                   'selected_author_labels':len({norm(c['metadata']['authors']) for c in chosen}),
                   'selected_by_url':dict(Counter(u for c in chosen for u in urls(c))),
                   'prior_url_overlap':0,'prior_quote_overlap':0,'prior_template_overlap':0,
                   'scope':'Novel exact canonical URLs and quotation/template hashes relative to the prior 150 cases; not proof of unseen works or editions'}


def frozen_sample():
    raw=BASE.read_bytes()
    if sha(raw)!=PIN: raise ValueError('inventory_pin_mismatch')
    inventory=json.loads(raw)
    previous=load_prior_cohort()['cases']+inventory['sample']
    if len(previous)!=150 or len({c['citation_ref']['quote_sha256'] for c in previous})!=150:
        raise ValueError('prior_cohort_mismatch')
    selected,diag=select_novel(inventory['cases'],previous)
    return {'schema':SCHEMA,'inventory_sha256':PIN,'sample':selected,
            'selection':diag,'auto_publish':False}


def semantic_audit(case,row,audit,refresher):
    candidates=row.get('semantic_experiment',{}).get('candidates',[])
    if not candidates:
        return {'status':'no_primary_candidate','rights':'unreviewed','auto_publish':False}
    # No implicit lexical fallback: compare the actual semantic first result.
    view={**row,'review_packet':{'candidates':candidates[:1]},
          'section_alignment':{},'candidates':[]}
    record,_=review_primary(case,view,audit,refresher)
    return record


def comparison(a,b):
    left=a.get('status')=='ready';right=b.get('status')=='ready'
    result={'baseline_ready':left,'semantic_ready':right,'agreement':'neither_ready',
            'character_iou':None,'semantic_equivalence':None,'auto_publish':False}
    if left!=right:
        result['agreement']='baseline_only' if left else 'semantic_only'
    elif left:
        x,y=a['target'],b['target']
        if x['actual_text_sha256']==y['actual_text_sha256']:
            result['agreement']='same_exact_text'
        elif (x['url'],x['revision'],x['document_sha256'])==(y['url'],y['revision'],y['document_sha256']):
            lo=max(x['char_start'],y['char_start']);hi=min(x['char_end'],y['char_end'])
            overlap=max(0,hi-lo)
            union=x['char_end']-x['char_start']+y['char_end']-y['char_start']-overlap
            result['character_iou']=round(overlap/union,6)
            result['agreement']='same_document_overlapping' if overlap else 'same_document_disjoint'
        else: result['agreement']='different_document_or_revision'
    return result


def summarize(pairs):
    out={}
    for arm in ('baseline_audit','semantic_audit'):
        rows=[p[arm] for p in pairs]
        out[arm]={'cases':len(rows),'statuses':dict(Counter(r['status'] for r in rows)),
                  'authenticated':sum(r.get('documentary_authenticity')=='verified_document_and_span' for r in rows),
                  'review_words':sum(r.get('english_review_words',0) for r in rows),
                  'ready':sum(r['status']=='ready' for r in rows),
                  'equivalence':None,'false_positives':None}
    sem=[p['baseline']['result'].get('semantic_experiment',{}) for p in pairs]
    out['semantic_search']={'statuses':dict(Counter(s.get('status','not_attempted') for s in sem)),
                            'scopes':dict(Counter(s.get('scope','not_attempted') for s in sem)),
                            'seconds':round(sum(s.get('elapsed_seconds',0) for s in sem),6)}
    out['comparison']=dict(Counter(p['comparison']['agreement'] for p in pairs))
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('select','benchmark'),required=True)
    p.add_argument('--sample-file',type=Path,required=True)
    p.add_argument('--model-dir',type=Path)
    p.add_argument('--cache',type=Path)
    p.add_argument('--out',type=Path,default=ROOT/'reports-live/novel-mia-semantic.json')
    a=p.parse_args()
    frozen=frozen_sample()
    if a.stage=='select':
        write(a.sample_file,frozen)
        print(json.dumps({'selection':frozen['selection'],'sample_sha256':sha(a.sample_file.read_bytes())},indent=2))
        return 0
    if not a.model_dir or not a.cache: p.error('model and cache required')
    raw=a.sample_file.read_bytes()
    if json.loads(raw)!=frozen: raise ValueError('sample_not_reproducible')
    cases=frozen['sample'];pairs=[];start=time.monotonic()
    a.cache.mkdir(parents=True,exist_ok=True)
    enc=LocalEncoder(a.model_dir,a.cache/'vectors.sqlite3');sem=SentenceVectorIndex(enc)
    client=PilotClient(a.cache/'retrieval',budget=len(cases)*32)
    audit_client=PilotClient(a.cache/'audit',budget=len(cases)*14)
    audit=Auditor(audit_client);refresher=RevisionRefresher(audit_client)
    report={'schema':'wikidebia-novel-mia-semantic-1','status':'running','selection':frozen['selection'],
            'sample_sha256':sha(raw),'inventory_sha256':PIN,'model_id':enc.model_id,
            'pairs':pairs,'sample':cases,'hosted_model_calls':0,'auto_publish':False,'rights':'unreviewed'}
    write(a.out,report)
    try:
        with open_current() as index:
            engine=Retrieval(client,16,catalog=load_catalog(ROOT/'bibliographic-catalog.json'),
                             local_index=index,refined=True,chapter_catalog=load_chapters(ROOT/'chapter-catalog.json'),
                             semantic_index=sem)
            report['index_sha256']=index.fingerprint
            # A shared in-run source and document cache; work-group order is
            # independent of outcomes. The baseline result is not modified.
            for case in sorted(cases,key=lambda c:(norm(c['metadata']['authors']),norm(c['metadata']['work']),urls(c),c['id'])):
                result=one_case(engine,case,32)
                row=result['result'];before=(audit_client.requests,audit_client.network_body_bytes)
                old=audit_client.budget;audit_client.budget=min(old,audit_client.requests+14)
                try:
                    baseline,_=review_primary(case,row,audit,refresher)
                    semantic=semantic_audit(case,row,audit,refresher)
                finally: audit_client.budget=old
                pairs.append({'id':case['id'],'baseline':result,'baseline_audit':baseline,
                              'semantic_audit':semantic,'comparison':comparison(baseline,semantic),
                              'audit_requests':audit_client.requests-before[0],
                              'audit_bytes':audit_client.network_body_bytes-before[1]})
                report['aggregate']=aggregate(pairs,('baseline',))
                report['summary']=summarize(pairs);write(a.out,report)
                print(json.dumps({'completed':len(pairs),'id':case['id'],'baseline':baseline['status'],
                                  'semantic':semantic['status'],'scope':row.get('semantic_experiment',{}).get('scope'),
                                  'agreement':pairs[-1]['comparison']['agreement']}),flush=True)
        report.update(status='candidate_comparison_complete',elapsed_seconds=round(time.monotonic()-start,3),
                      encoder_stats=enc.stats.copy(),document_index_builds=sem.builds,document_index_hits=sem.hits,
                      retrieval_requests=client.requests,retrieval_decoded_bytes=client.network_body_bytes,
                      audit_requests=audit_client.requests,audit_decoded_bytes=audit_client.network_body_bytes,
                      semantic_quality=None,actual_hosted_ai_tokens=None,actual_hosted_ai_cost=None)
        write(a.out,report)
        print(json.dumps({k:v for k,v in report.items() if k not in ('sample','pairs')},indent=2))
        print('report_sha256='+sha(a.out.read_bytes()))
        return 0
    finally: enc.close()

if __name__=='__main__':raise SystemExit(main())
