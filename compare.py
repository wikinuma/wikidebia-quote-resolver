#!/usr/bin/env python3
"""Compare retrieval methods on real documents when network access is available.
All results are candidates. Gold URLs/needles are used only after ranking.
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, unquote
from resolver import (ROOT, API, Client, html_document, wikirouge, wiki_quote,
                      locate, rank, norm, sha)
from alignment import exact_occurrences, aligned_candidates
from embedding import LocalEncoder, semantic_rank


def page_pairs(client,fr,en,found,issues):
    """Query langlinks for only those FR leaf pages containing the quote.
    A work-level link to a table of contents does not establish a section pair.
    No guessing from chapter numbers (which differ by edition).
    """
    pairs=[]
    if issues:
        return pairs
    targets={d.title:d for d in en if d.title and d.blocks}
    def leaf(d,docs):
        return not any(x.title.startswith(d.title+'/') for x in docs if x.title!=d.title)
    for d in fr:
        if d.url not in found or not d.title or not leaf(d,fr): continue
        try:
            r=client.api(API['fr'],action='query',titles=d.title,redirects=1,prop='langlinks',lllang='en')['query']['pages'][0]
            if 'missing' in r or r['title']!=d.title: continue
            for ll in r.get('langlinks',[]):
                t=targets.get(ll.get('title',ll.get('*')))
                if ll.get('lang')=='en' and t and leaf(t,en):
                    pairs.append((d,t,'direct_leaf_langlink'))
        except Exception as e:
            issues.append({'kind':'section_langlink_error','title':d.title,'error':str(e)})
    return pairs


def url_identity(url):
    p=urlsplit(url)
    return (p.hostname,unquote(p.path).replace(" ","_"),p.query)


def evaluation(candidates,case,provider):
    needle=norm(case.get('expected_needle','')); url=case.get('expected_en_mia' if provider=='mia' else 'expected_en_wr')
    if not needle or not url: return {'state':'not_evaluable_for_provider'}
    positions=[i for i,r in enumerate(candidates,1) if url_identity(r['url'])==url_identity(url) and (' '+needle+' ') in (' '+norm(r['text'])+' ')]
    return {'state':'short_anchor_only','rank':positions[0] if positions else None,'expected_url':url}


def compare_methods(quote,fr,en,pairs,encoder=None,max_cells=250000,max_windows=6000):
    hits=[]; trunc=False
    for d in fr:
        m,t=exact_occurrences(quote,d); hits+=m; trunc|=t
    # The old method is retained unmodified for an apples-to-apples comparison.
    methods={'lexical':{'candidates':rank(quote,en),'method':'lexical_anchors_v1'}}
    result={'fr_occurrences':hits,'fr_occurrences_truncated':trunc,
            'fr_fuzzy_matches':[] if hits else locate(quote,fr),'methods':methods,
            'source_status':'ambiguous' if len(hits)>1 or trunc else 'normalized_contiguous' if hits else 'unconfirmed',
            'auto_publish':False,'rights':'unreviewed'}
    # Distinct editions may yield repeated matches. Do not automatically choose
    # one. Alignment is only ranked if the source hit is globally unambiguous.
    for kind in ('paragraph','sentence'):
        name='alignment_'+kind; rows=[]; detail=[]
        if len(hits)==1 and not trunc:
            for f,e,route in pairs:
                if f.url!=hits[0]['url'] or f.digest!=hits[0]['sha256']: continue
                a=aligned_candidates(quote,f,e,kind,max_cells)
                detail.append({'fr':f.url,'en':e.url,'route':route,'status':a['status'],'alignment':a.get('alignment')})
                for row in a['candidates']: rows.append({**row,'pair_route':route})
        methods[name]={'candidates':rows,'details':detail,'status':'review_required' if rows else 'not_applicable_or_unresolved','method':'monotone_length_anchors_v1'}
    if encoder is not None:
        try: methods['embedding']=semantic_rank(quote,en,encoder,max_windows=max_windows)
        except Exception as exc: methods['embedding']={'status':'error','error':str(exc),'candidates':[]}
    else: methods['embedding']={'status':'not_requested','candidates':[]}
    return result


def run_case(client,case,provider,max_pages,encoder,max_cells,max_windows):
    start=time.monotonic(); before=(client.requests,client.hits); issues=[]
    result={'id':case['id'],'provider':provider,'issues':issues,'auto_publish':False,'note':case.get('note','')}
    try:
        quote,rev=wiki_quote(client,case['wiki_title'],case['selector'])
        result.update(wiki_revision=rev,quote_sha256=sha(quote.encode()),quote_words=len(norm(quote).split()))
    except Exception as e:
        result.update(status='input_unavailable',error=str(e),elapsed_seconds=round(time.monotonic()-start,3),requests=client.requests-before[0],cache_hits=client.hits-before[1]); return result
    fr=[]; en=[]; pairs=[]
    if provider=='mia':
        for lang,docs in [('fr',fr),('en',en)]:
            for url in case.get('mia_'+lang,[]):
                try: docs.append(html_document(client,url))
                except Exception as e: issues.append({'kind':'fetch_error','url':url,'error':str(e)})
        # Pair lists describe section identity, not quotation identity. They
        # must be provided before ranking. No use of expected_* in this code.
        for pair in case.get('parallel_sections_mia',[]):
            f=next((d for d in fr if d.url==pair['fr']),None); e=next((d for d in en if d.url==pair['en']),None)
            if f and e: pairs.append((f,e,'manually_seeded_parallel_section'))
        result['route']='seeded_mia_documents_not_automatic_discovery'
        result['search_scope']='explicit_seeded_documents_only'
    else:
        try:
            fr,en,routes,err=wikirouge(client,case['work_fr'],quote,max_pages); result['routes']=routes; issues.extend(err)
            result['search_scope']='resolved_work_and_bounded_prefix_subpages'
            found={d.url for d in fr if exact_occurrences(quote,d)[0]}
            pairs=page_pairs(client,fr,en,found,issues)
        except Exception as e: issues.append({'kind':'provider_error','error':str(e)})
    result.update(compare_methods(quote,fr,en,pairs,encoder,max_cells,max_windows))
    for method in result['methods'].values():
        # An unexecuted method is not a negative measurement.
        if method.get('status') in ('not_requested','not_applicable_or_unresolved','error','embedding_window_budget_exceeded'):
            method['evaluation']={'state':'not_executed_or_unresolved'}
        else: method['evaluation']=evaluation(method['candidates'],case,provider)
    result.update(documents={'fr':len(fr),'en':len(en)},search_complete=bool(fr and en and not issues),
                  status='incomplete_search' if issues or not fr or not en else 'review_required',
                  elapsed_seconds=round(time.monotonic()-start,3),requests=client.requests-before[0],cache_hits=client.hits-before[1])
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--provider',choices=['mia','wikirouge'],required=True)
    p.add_argument('--cases',type=Path,default=ROOT/'cases-v02.json'); p.add_argument('--case',action='append',default=[])
    p.add_argument('--cache',type=Path,default=ROOT/'.cache'); p.add_argument('--budget',type=int,default=220)
    p.add_argument('--max-pages',type=int,default=32); p.add_argument('--max-cells',type=int,default=250000); p.add_argument('--max-windows',type=int,default=6000)
    p.add_argument('--model-dir',type=Path); p.add_argument('--embedding-cache',type=Path,default=ROOT/'.cache'/'vectors.sqlite3'); p.add_argument('--out',type=Path)
    args=p.parse_args()
    if min(args.budget,args.max_pages,args.max_cells,args.max_windows)<1: p.error('limits must be positive')
    cases=json.loads(args.cases.read_text())['cases']; selected=[c for c in cases if not args.case or c['id'] in args.case]
    if not selected or set(args.case)-{c['id'] for c in selected}: p.error('unknown or empty case selection')
    client=Client(args.cache,args.budget); enc=None
    report={'schema':'wikidebia-quote-comparison-0.2','provider':args.provider,'auto_publish':False,'cases':[]}
    try:
        if args.model_dir: enc=LocalEncoder(args.model_dir,args.embedding_cache)
        for c in selected: report['cases'].append(run_case(client,c,args.provider,args.max_pages,enc,args.max_cells,args.max_windows))
    except Exception as e: report.update(status='error',error=str(e))
    finally:
        if enc: report['embedding']={'model_id':enc.model_id,**enc.stats}; enc.close()
    report.update(requests=client.requests,cache_hits=client.hits)
    report['status']=report.get('status','incomplete' if any(c['status']!='review_required' or c.get('methods',{}).get('embedding',{}).get('status')=='error' for c in report['cases']) else 'review_required')
    s=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
    if args.out: args.out.parent.mkdir(parents=True,exist_ok=True); args.out.write_text(s,encoding='utf-8')
    else: print(s,end='')
    return int(report['status']!='review_required')

if __name__=='__main__': sys.exit(main())
