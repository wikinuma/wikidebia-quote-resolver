#!/usr/bin/env python3
"""Paired comparison of legacy and indexed discovery on pinned real citations.

Both arms use the same retrieval engine, case order, bounds and separate cold
caches. No model calls. No unverified candidate is counted as an exact quote.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import tempfile
import time
import zipfile

from auto_retrieve import Retrieval
from inventory import link_pages, work_key
from navigation import citation_records, error_kind, canonical_mia
from pilot_full import PilotClient, redact_text
from resolver import ROOT, WD_API, norm, sha
from wikirouge_index import DEFAULT, open_snapshot


def known_examples(manifest):
    known=[]
    for name in ('cases.json','cases-v02.json','development-inventory.json'):
        known+=json.loads((ROOT/name).read_text(encoding='utf-8'))['cases']
    catalog=json.loads((ROOT/'bibliographic-catalog.json').read_text(encoding='utf-8'))
    works={norm(r['source_title']) for r in catalog['entries']}
    titles={norm(r['wiki_title']) for r in known if r.get('wiki_title')}
    works.update(norm(r['work_fr']) for r in known if r.get('work_fr'))
    m=json.loads(Path(manifest).read_text(encoding='utf-8'))
    with zipfile.ZipFile(Path(manifest).parent/m['archive']['filename']) as z:
        old=json.loads(z.read('wikirouge-index/benchmark.json'))
    ids={c['id'] for c in old['cases']}
    works.update(norm(c[k]) for c in old['cases'] for k in ('article','work') if c.get(k))
    return titles,works,ids


def pinned_record(record):
    ref={k:record[k] for k in ('revision','template_index','template_sha256','quote_sha256')}
    case_id='inventory-'+sha((record['wiki_title']+'\0'+str(record['revision'])+'\0'+str(record['template_index'])+'\0'+record['template_sha256']).encode())[:20]
    return {'id':case_id,'wiki_title':record['wiki_title'],'citation_ref':ref,
            'metadata':{k:record[k] for k in ('authors','work','article','date','mia_urls','quote_words')}}


def scan_mia_pages(client, linked, max_pages):
    """Only parse pages enumerated by the public exturlusage API.
    Batch raw revision reads, preserving actual template fingerprints.
    A MIA link elsewhere on the page never makes a Citation eligible.
    """
    selected=sorted(linked,key=lambda title:sha(title.encode()))[:max_pages]
    rows=[]; issues=[]; inspected=0; invalid_templates=0; seen=set()
    for i in range(0,len(selected),10):
        batch=selected[i:i+10]
        try:
            result=client.api(WD_API,action='query',titles='|'.join(batch),redirects=1,
                              prop='revisions',rvprop='ids|content',rvslots='main')
            pages=result.get('query',{}).get('pages',[])
            if not isinstance(pages,list) or len(pages)>len(batch):
                raise ValueError('invalid_revision_batch')
            for p in pages:
                title=p.get('title','')
                revisions=p.get('revisions',[])
                if p.get('ns')!=0 or not title or len(revisions)!=1:
                    issues.append('missing_page_or_revision'); continue
                rev=revisions[0]; raw=rev.get('slots',{}).get('main',{}).get('content')
                rid=rev.get('revid')
                if not isinstance(raw,str) or type(rid) is not int or rid<=0:
                    issues.append('missing_revision_content'); continue
                if rid in seen: continue
                seen.add(rid); inspected+=1
                records,errors=citation_records(raw)
                invalid_templates+=len(errors)
                for r in records:
                    if r['mia_urls'] and r['quote_words']>0:
                        rows.append({**r,'wiki_title':title,'revision':rid})
        except Exception as exc:
            issues.append(error_kind(exc))
    return rows,{'pages_discovered':len(linked),'pages_selected':len(selected),
                 'pages_inspected':inspected,'page_cap_reached':len(linked)>len(selected),
                 'invalid_templates':invalid_templates,'issues':issues}


def select_mia(records,sample,excluded_titles,excluded_works,excluded_ids):
    """Prioritize new works and diverse authors, without imposing a one-citation
    per work cap that could make a representative MIA-only sample impossible.
    Never substitute non-MIA citations. Deduplicate identical quotations.
    """
    unique={}
    for r in sorted(records,key=lambda r:sha((r['wiki_title']+'\0'+r['template_sha256']).encode())):
        urls=r.get('mia_urls',[])
        if not urls or any(canonical_mia(u)!=u for u in urls) or not r['quote_words']:
            continue
        key=r['quote_sha256']
        case={**pinned_record(r),'stratum':'explicit_mia'}
        development=(norm(r['wiki_title']) in excluded_titles or
                     work_key(r) in excluded_works or case['id'] in excluded_ids)
        candidate={**case,'development_overlap':development,
                   'selection_group':work_key(r) or urls[0]}
        # Prefer independent occurrence when the same quote appears twice.
        if key not in unique or (unique[key]['development_overlap'] and not development):
            unique[key]=candidate
    pool=list(unique.values())
    picked=[]; remaining=list(pool)
    # Independent samples first. Within each tier, maximize work diversity,
    # then author diversity, then stable citation hash. No retrieval outcome.
    used_works=Counter(); used_authors=Counter()
    while remaining and len(picked)<sample:
        row=min(remaining,key=lambda r:(r['development_overlap'],
                 used_works[r['selection_group']],
                 used_authors[norm(r['metadata']['authors'])],
                 sha(r['id'].encode())))
        remaining.remove(row)
        picked.append(row)
        used_works[row['selection_group']]+=1
        used_authors[norm(row['metadata']['authors'])]+=1
    return picked,{'eligible_unique_quotes':len(pool),
                   'independent_unique_quotes':sum(not r['development_overlap'] for r in pool),
                   'development_overlap_selected':sum(r['development_overlap'] for r in picked),
                   'distinct_work_groups':len(used_works),
                   'multiple_mia_links_selected':sum(len(r['metadata']['mia_urls'])>1 for r in picked),
                   'length_outside_previous_limits_selected':sum(not 8<=r['metadata']['quote_words']<=250 for r in picked)}


def cohort(client, manifest, sample, scan_pages):
    titles,works,excluded_ids=known_examples(manifest)
    linked,queries=link_pages(client,rows_per_query=50,max_batches=100)
    rows,scan=scan_mia_pages(client,linked,scan_pages)
    chosen,selection=select_mia(rows,sample,titles,works,excluded_ids)
    return chosen,{'mia_inventory':{**scan,'queries':queries,'raw_mia_citations':len(rows),
                   'continuation_at_limit':any(q.get('batch')==100 and q.get('more') for q in queries)},
                   **selection,'strata':{'explicit_mia':len(chosen)},
                   'known_excluded_ids':len(excluded_ids),'known_excluded_work_keys':len(works),
                   'authors':len({norm(r['metadata']['authors']) for r in chosen if r['metadata']['authors']}),
                   'selection':'only French Citation templates with explicit canonicalizable marxists.org lien; independent cases first; diverse works and authors; repeat works and disclosed development overlap permitted to reach sample; not random'}


def case_metrics(row):
    nav=row.get('navigation',{})
    source=row.get('source',{})
    aligned=row.get('section_alignment',{}).get('candidates',[])
    lexical=row.get('candidates',[])
    unique={}
    for c in lexical+aligned:
        key=c.get('text_sha256') or (c.get('url'),c.get('char_start'),c.get('char_end'))
        unique[key]=max(unique.get(key,0),c.get('text_words',0))
    return {'work_candidate':nav.get('status') in ('mia_route_candidate','wikirouge_route_candidate'),
            'local_route':nav.get('route_method')=='local_wikirouge_snapshot',
            'french_unique_source':source.get('status')=='exact_in_examined_documents',
            'english_passage_candidate':bool(lexical or aligned),
            'aligned_candidate':bool(aligned),'candidate_words_for_review':sum(unique.values()),
            'candidate_count':len(unique),
            'source_words_for_review':nav.get('input',{}).get('quote_words',0) if unique else 0}


def one_case(retrieval, case, case_budget):
    c=retrieval.client
    start=time.monotonic()
    requests,hits,body,events=c.requests,c.hits,c.network_body_bytes,len(c.events)
    old=c.budget; c.budget=min(old,c.requests+case_budget)
    try:
        query={k:case[k] for k in ('id','wiki_title','citation_ref')}
        row=redact_text(retrieval.retrieve(query))
    except Exception as exc:
        row={'id':case['id'],'status':'execution_error','error':error_kind(exc),'auto_publish':False,'rights':'unreviewed'}
    finally:
        c.budget=old
    recent=c.events[events:]
    metrics=case_metrics(row)
    metrics.update(network_requests=c.requests-requests,cache_hits=c.hits-hits,
                   decoded_network_body_bytes=c.network_body_bytes-body,
                   request_errors=sum(e.get('status')=='error' for e in recent),
                   budget_errors=sum('budget exhausted' in e.get('error','') for e in recent),
                   elapsed_seconds=round(time.monotonic()-start,4))
    return {'metrics':metrics,'result':row}


def aggregate(pairs):
    out={}
    for arm in ('legacy','indexed'):
        rows=[p[arm] for p in pairs]
        counts=Counter(x['result']['status'] for x in rows)
        metrics={k:round(sum(x['metrics'][k] for x in rows),4) for k in rows[0]['metrics']} if rows else {}
        out[arm]={'cases':len(rows),'statuses':dict(counts),**metrics,
                  'verified_english_works':None,'verified_english_passages':None,
                  'verified_exact_quotes':None,'verified_reuse_rights':None,'false_positives':None}
    return out


def write(path, report):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temp.replace(path)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,default=DEFAULT)
    p.add_argument('--sample',type=int,default=50)
    p.add_argument('--scan-pages',type=int,default=1200)
    p.add_argument('--case-budget',type=int,default=24)
    p.add_argument('--max-work-pages',type=int,default=8)
    p.add_argument('--out',type=Path,default=ROOT/'reports-live'/'paired-index.json')
    a=p.parse_args()
    if not 1<=a.sample<=80 or not 1<=a.scan_pages<=3000 or not 1<=a.case_budget<=60 or not 1<=a.max_work_pages<=24:
        p.error('bounded positive limits required')
    with tempfile.TemporaryDirectory(prefix='paired-index-') as tmp, open_snapshot(a.manifest) as index:
        cache=Path(tmp)
        discover=PilotClient(cache/'discover',budget=400+(a.scan_pages+9)//10+50)
        start=time.monotonic()
        cases,diag=cohort(discover,a.manifest,a.sample,a.scan_pages)
        report={'schema':'wikidebia-paired-index-1','snapshot_sha256':index.fingerprint,
                'requested_cases':a.sample,'scope':'explicit_mia_only','sample':cases,'discovery':{**diag,
                    'network_requests':discover.requests,'decoded_network_body_bytes':discover.network_body_bytes,
                    'elapsed_seconds':round(time.monotonic()-start,4)},
                'bounds':{'case_requests':a.case_budget,'max_work_pages':a.max_work_pages},
                'metric_contract':{'bytes':'decoded response body bytes read, including partial failures; excludes headers and wire compression',
                    'review_words':'deduplicated candidate words within each case plus separately counted French quote words; not tokens or quota',
                    'verified':'no independent gold review; null is not zero, candidates are not successes',
                    'model_calls':0,'caches':'separate empty persistent caches per arm; shared discovery measured separately; alternating arm order'},
                'pairs':[],'auto_publish':False,'status':'running'}
        write(a.out,report)
        print(json.dumps({'selected':len(cases),'strata':diag['strata'],'authors':diag['authors'],'selection':{k:v for k,v in diag.items() if k!='mia_inventory'}}),flush=True)
        clients={arm:PilotClient(cache/arm,budget=a.sample*a.case_budget) for arm in ('legacy','indexed')}
        engines={arm:Retrieval(c,a.max_work_pages,local_index=index if arm=='indexed' else None) for arm,c in clients.items()}
        for i,case in enumerate(cases):
            pair={'id':case['id'],'stratum':case['stratum']}
            order=('legacy','indexed') if i%2==0 else ('indexed','legacy')
            for arm in order:
                pair[arm]=one_case(engines[arm],case,a.case_budget)
            report['pairs'].append(pair)
            report['aggregate']=aggregate(report['pairs'])
            write(a.out,report)
            print(json.dumps({'completed':i+1,'selected':len(cases),
                              'legacy':pair['legacy']['metrics'],'indexed':pair['indexed']['metrics']}),flush=True)
        report['aggregate']=aggregate(report['pairs'])
        report['by_stratum']={s:aggregate([p for p in report['pairs'] if p['stratum']==s]) for s in diag['strata']}
        report['status']='candidate_comparison_complete' if len(cases)>=a.sample else 'insufficient_sample'
        write(a.out,report)
        print(json.dumps({'status':report['status'],'aggregate':report['aggregate'],'by_stratum':report['by_stratum']},indent=2),flush=True)
        return int(len(cases)<a.sample)


if __name__=='__main__':
    raise SystemExit(main())
