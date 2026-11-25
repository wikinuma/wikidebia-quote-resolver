"""Bounded discovery of real citations with explicit MIA source links.

No manually chosen target URL or quotation selector. This measures review
candidate coverage, NOT translation accuracy or the size of the whole corpus.
"""
from __future__ import annotations
import argparse
import json
from collections import Counter,defaultdict
from pathlib import Path

from navigation import citation_records,canonical_mia,raw_parse,error_kind,generic_work_name
from resolver import ROOT,WD_API,norm,sha
from pilot_full import PilotClient,replay_mismatches
from auto_retrieve import run_auto
from bibliography import load_catalog


def link_pages(client,rows_per_query=50,max_batches=1):
    pages={}; queries=[]
    if not 1<=max_batches<=100: raise ValueError('invalid_batch_limit')
    for host in ('www.marxists.org','marxists.org'):
        for scheme in ('https','http'):
            cursor={}; seen=set()
            for batch in range(max_batches):
                entry={'host':host,'scheme':scheme,'batch':batch+1}
                try:
                    r=client.api(WD_API,action='query',list='exturlusage',euquery=host,
                                 euprotocol=scheme,eunamespace=0,eulimit=rows_per_query,**cursor)
                    rows=r['query']['exturlusage']
                    if len(rows)>rows_per_query: raise ValueError('excessive_api_rows')
                    entry.update(rows=len(rows),more=bool(r.get('continue')))
                    for row in rows:
                        title=row.get('title'); url=canonical_mia(row.get('url'))
                        if row.get('ns')!=0 or not isinstance(title,str) or not url: continue
                        pages.setdefault(title,set()).add(url)
                    cursor=r.get('continue',{})
                    key=json.dumps(cursor,sort_keys=True)
                    if cursor and (key in seen or not set(cursor)<= {'continue','eucontinue'}):
                        raise ValueError('invalid_or_repeated_continuation')
                    seen.add(key)
                except Exception as exc:
                    entry['error']=error_kind(exc); cursor={}
                queries.append(entry)
                if not cursor: break
    return pages,queries


def work_key(row):
    work=row.get('work','')
    if not work or generic_work_name(work): work=row.get('article','')
    return norm(work)


def choose_sample(records,max_cases,balance_authors=False):
    """Stable order, at most one candidate per work title and per normalized
    quote fingerprint. Selection never depends on retrieval results.
    """
    result=[]; works=set(); quotes=set()
    ordered=sorted(records,key=lambda x:sha((x['wiki_title']+'\0'+x['template_sha256']).encode()))
    if balance_authors:
        groups=defaultdict(list)
        for r in ordered: groups[norm(r.get('authors',''))].append(r)
        ordered=[]
        # Interleave authors by stable hash order; do not use retrieval outcomes.
        keys=sorted(groups,key=lambda k:sha(k.encode()))
        for i in range(max((len(v) for v in groups.values()),default=0)):
            ordered.extend(groups[k][i] for k in keys if i<len(groups[k]))
    for r in ordered:
        key=work_key(r)
        if not key or key in works or r['quote_sha256'] in quotes: continue
        works.add(key); quotes.add(r['quote_sha256']); result.append(r)
        if len(result)>=max_cases: break
    return result


def discover_inventory(client,excluded_titles,excluded_works,max_scan_pages,max_cases,max_batches=1,balance_authors=False):
    linked,queries=link_pages(client) if max_batches==1 else link_pages(client,max_batches=max_batches)
    scan=sorted((t for t in linked if norm(t) not in excluded_titles),key=lambda t:sha(t.encode()))[:max_scan_pages]
    records=[]; inspected=[]; issues=[]; total_templates=0; eligible_before_exclusions=0
    for title in scan:
        try:
            actual,rev,raw=raw_parse(client,WD_API,title=title)
            if norm(actual) in excluded_titles:
                inspected.append({'title':title,'canonical_title':actual,'excluded':True})
                continue
            rows,errors=citation_records(raw)
            total_templates+=len(rows)+len(errors)
            inspected.append({'title':title,'canonical_title':actual,'revision':rev,
                              'templates':len(rows)+len(errors),'invalid_templates':len(errors)})
            for r in rows:
                if len(r['mia_urls'])!=1 or not 8<=r['quote_words']<=250 or not work_key(r): continue
                eligible_before_exclusions+=1
                if work_key(r) in excluded_works: continue
                records.append({**r,'wiki_title':actual,'revision':rev})
        except Exception as exc: issues.append({'title':title,'error':error_kind(exc)})
    chosen=choose_sample(records,max_cases,balance_authors)
    cases=[]
    for r in chosen:
        ref={k:r[k] for k in ('revision','template_index','template_sha256','quote_sha256')}
        cases.append({'id':'inventory-'+sha((r['wiki_title']+'\0'+str(r['revision'])+'\0'+str(r['template_index'])+'\0'+r['template_sha256']).encode())[:20],
                      'wiki_title':r['wiki_title'],'citation_ref':ref,
                      'metadata':{k:r[k] for k in ('authors','work','article','date','mia_urls','quote_words')}})
    return {'scope':'first bounded exturlusage batches; selected raw Citation templates; not a census',
            'queries':queries,'distinct_linked_pages':len(linked),'pages_selected_for_inspection':len(scan),
            'inspected_pages':inspected,'issues':issues,'templates_inspected':total_templates,
            'eligible_before_work_exclusions':eligible_before_exclusions,
            'eligible_citations':len(records),'eligible_distinct_work_titles':len({work_key(r) for r in records}),
            'selected':cases,'selection':('author-balanced; ' if balance_authors else '')+'stable hash order; distinct work titles; development pages and works excluded; no target answers'}


def run_inventory(client,known,max_scan_pages,max_cases,max_work_pages,max_batches=1,balance_authors=False,catalog=()):
    excluded_titles={norm(c['wiki_title']) for c in known}
    excluded_works={norm(c.get('work_fr','')) for c in known if c.get('work_fr')}
    inventory=discover_inventory(client,excluded_titles,excluded_works,max_scan_pages,max_cases,max_batches,balance_authors)
    # Retrieval gets only a pinned citation reference, no preselected target.
    cases=[{k:r[k] for k in ('id','wiki_title','citation_ref')} for r in inventory['selected']]
    r=run_auto(client,cases,max_work_pages)
    assisted=run_auto(client,cases,max_work_pages,catalog) if catalog else None
    return {'assisted_retrieval':assisted,'schema':'wikidebia-citation-inventory-0.1','offline':client.offline,'auto_publish':False,
            'inventory':inventory,'retrieval':r,'requests':client.requests,'cache_hits':client.hits,
            'http_events':client.events,'evaluation_scope':'candidate coverage only; independent human reference not yet available'}


def summary(report,assisted=False):
    inv=report['inventory']; r=report['assisted_retrieval'] if assisted else report['retrieval']
    rows=[]
    for c in r['cases']:
        meta=c['navigation'].get('input',{})
        rows.append({'id':c['id'],'wiki_title':meta.get('wiki_title'),'work':meta.get('work'),'authors':meta.get('authors'),
                     'status':c['status'],'navigation_status':c['navigation_status'],'source_status':c.get('source',{}).get('status'),
                     'quote_words':meta.get('quote_words'),
                     'source_evidence_diagnostics':c.get('source',{}).get('source_evidence_diagnostics',[]),
                     'variant_evidence':[ {k:v for k,v in x.items() if k in ('url','match','query_words','source_words','token_edits','omitted_words','gap_words','similarity','seed_support','boundary_ambiguous')} for x in c.get('source',{}).get('variants',[]) ],
                     'article':meta.get('article'),'source_urls':meta.get('mia_urls'),
                     'navigation_attempts':c['navigation'].get('attempts',[]),
                     'navigation_error':c['navigation'].get('error'),
                     'resolved_fr_work':c['navigation'].get('route',{}).get('fr_title'),
                     'resolved_en_work':c['navigation'].get('route',{}).get('en_title'),
                     'resolved_fr_authors':c['navigation'].get('work_metadata',{}).get('fr',{}).get('authors'),
                     'resolved_en_authors':c['navigation'].get('work_metadata',{}).get('en',{}).get('authors'),
                     'author_check':c['navigation'].get('author_check'),
                     'bibliographic_route':c.get('bibliographic_route'),
                     'source_stages':[{'provider':s['provider'],'scope':s['scope'],'pages':len(s['pages']),'new_documents_examined':s.get('new_documents_examined'),'issues':s['issues']} for s in c.get('source',{}).get('stages',[])],
                     'target_provider':c.get('target_provider'),'lexical_candidates':len(c['candidates']),
                     'lexical_fallback':c.get('lexical_fallback'),
                     'target_stages':[{'provider':s['provider'],'scope':s['scope'],'pages':len(s['pages']),'issues':s['issues']} for s in c.get('target_attempts',[])],
                     'alignment_status':c.get('section_alignment',{}).get('status'),
                     'candidate_words':sum(x.get('text_words',0) for x in c['candidates']),
                     'aligned_words':sum(x.get('text_words',0) for x in c.get('section_alignment',{}).get('candidates',[])),
                     'candidate_urls':sorted({x['url'] for x in c['candidates']}),
                     'aligned_candidate_urls':sorted({x['url'] for x in c.get('section_alignment',{}).get('candidates',[])})})
    return {'scope':report['evaluation_scope'],'requests':report['requests'],'cache_hits':report['cache_hits'],
            'inventory':{k:v for k,v in inv.items() if k not in ('inspected_pages','selected')},
            'selected_cases':len(inv['selected']),'counts':r['counts'],'cases':rows,'auto_publish':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache',type=Path,default=ROOT/'.pilot-cache')
    p.add_argument('--out',type=Path,default=ROOT/'reports-live'/'inventory.json')
    p.add_argument('--scan-pages',type=int,default=24)
    p.add_argument('--sample',type=int,default=8)
    p.add_argument('--max-work-pages',type=int,default=12)
    p.add_argument('--budget',type=int,default=220)
    p.add_argument('--replay',action='store_true')
    p.add_argument('--batches',type=int,default=1)
    p.add_argument('--balance-authors',action='store_true')
    p.add_argument('--exclude-file',type=Path)
    p.add_argument('--catalog',type=Path)
    a=p.parse_args()
    if not 1<=a.scan_pages<=80 or not 1<=a.sample<=20 or not 1<=a.max_work_pages<=40 or not 1<=a.budget<=400 or not 1<=a.batches<=10:
        p.error('bounded positive limits required')
    known=json.loads((ROOT/'cases-v02.json').read_text())['cases']
    if a.exclude_file: known+=json.loads(a.exclude_file.read_text())['cases']
    catalog=load_catalog(a.catalog) if a.catalog else ()
    c=PilotClient(a.cache,budget=a.budget)
    args=(known,a.scan_pages,a.sample,a.max_work_pages,a.batches,a.balance_authors,catalog)
    report=run_inventory(c,*args)
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    small=summary(report)
    if catalog: small['assisted_comparison']=summary(report,assisted=True)
    rc=int(not report['inventory']['selected'])
    if a.replay:
        expected={}
        for e in c.events:
            old=expected.get(e['request_url'])
            if old and any(old.get(k)!=e.get(k) for k in ('status','sha256','final_url')):
                raise RuntimeError('inconsistent_live_response')
            expected[e['request_url']]=e
        replay_client=PilotClient(a.cache,budget=a.budget,offline=True,expected_events=expected)
        replay=run_inventory(replay_client,*args)
        same=(report['inventory']==replay['inventory'] and
              report['retrieval']['cases']==replay['retrieval']['cases'] and
              report['retrieval']['counts']==replay['retrieval']['counts'] and
              (not catalog or report['assisted_retrieval']['cases']==replay['assisted_retrieval']['cases'] and
               report['assisted_retrieval']['counts']==replay['assisted_retrieval']['counts']))
        mismatches=replay_mismatches(expected,replay_client.events)
        integrity={'results_match':same,'response_mismatches':mismatches,'network_requests':replay_client.requests}
        replay['replay_integrity']=integrity
        a.out.with_name(a.out.stem+'-replay.json').write_text(json.dumps(replay,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        small['replay_integrity']=integrity
        rc=max(rc,int(not same or bool(mismatches) or replay_client.requests!=0))
    print(json.dumps(small,ensure_ascii=False,indent=2),flush=True)
    return rc


if __name__=='__main__': raise SystemExit(main())
