"""Bounded automatic retrieval from navigation metadata, without seeded target URLs.

A comparison pilot, not a publisher. Direct TOC links and prefix inventories
are explicit search scopes; neither proves that a complete edition was searched.
"""
from __future__ import annotations
import argparse
import json
import posixpath
import re
from urllib.parse import urljoin,urlsplit
from pathlib import Path

from navigation import canonical_mia,discover_case,error_kind
from resolver import ROOT,Document,subpages,rank,norm,sha
from pilot_full import (PilotClient,WR,soup,extract_blocks,html_revision,html_url,wr_title,
                        redact_text,replay_mismatches)
from alignment import exact_occurrences
from variants import source_variants
from compare import evaluation


def mia_toc_links(body,root):
    """Only directly linked work-stem or directory-index HTML pages and
    explicitly labelled dated prefaces in the same language archive.
    No URL synthesis and no unbounded recursive link traversal.
    """
    p=urlsplit(root); path=p.path
    directory=path if path.endswith('/') else posixpath.dirname(path)+'/'
    base=posixpath.basename(path)
    index=path.endswith('/') or base.lower() in ('index.htm','index.html')
    stem=base.rsplit('.',1)[0]
    lang=path.split('/')[1] if '/' in path[1:] else ''
    links={}
    for a in soup(body).find_all('a',href=True):
        u=canonical_mia(urljoin(root,a['href']))
        if not u or u==canonical_mia(root): continue
        q=urlsplit(u); name=posixpath.basename(q.path)
        if not name.lower().endswith(('.htm','.html')): continue
        if index and q.path==directory+'index.htm' or index and q.path==directory+'index.html': continue
        label=norm(a.get_text(' ',strip=True))
        same_directory=posixpath.dirname(q.path)+'/'==directory
        same_stem=same_directory and len(stem)>=6 and name.startswith(stem) and name!=base
        preface=bool(re.match(r'^(preface|avant propos)\b',label) and re.search(r'\b(?:18|19|20)\d{2}\b',label)
                     and lang in ('francais','archive') and q.path.startswith('/'+lang+'/'))
        reason='directory_index_link' if index and same_directory else 'work_stem_link' if same_stem else 'explicit_dated_preface_link' if preface else None
        if reason: links.setdefault(u,reason)
    return [{'url':u,'route':reason} for u,reason in links.items()]


def mia_doc(client,url):
    body,final=client.get(url)
    if not canonical_mia(final): raise ValueError('unexpected_mia_final_url')
    s=soup(body)
    return Document(final,extract_blocks(body),sha(body),s.title.get_text(' ',strip=True) if s.title else ''),body


def collect_mia(client,root,max_pages):
    docs=[]; info={'provider':'mia','root':root,'scope':'bounded_direct_toc_links_not_complete_edition','issues':[],'pages':[]}
    try:
        doc,body=mia_doc(client,root); docs.append(doc)
        links=mia_toc_links(body,doc.url); info['links']=links
        if len(links)+1>max_pages: info['issues'].append({'kind':'page_limit'})
        for item in links[:max_pages-1]:
            try:
                d,_=mia_doc(client,item['url'])
                if d.url not in {x.url for x in docs}: docs.append(d)
            except Exception as exc: info['issues'].append({'kind':'fetch_error','url':item['url'],'error':error_kind(exc)})
    except Exception as exc: info['issues'].append({'kind':'root_error','error':error_kind(exc)})
    info['pages']=[{'url':d.url,'sha256':d.digest,'blocks':len(d.blocks)} for d in docs]
    return docs,info


def collect_wr(client,title,lang,max_pages):
    docs=[]; info={'provider':'wikirouge','root':title,'lang':lang,'scope':'bounded_api_prefix_inventory','issues':[],'pages':[]}
    try:
        names,truncated=subpages(client,lang,title,max_pages)
        if truncated: info['issues'].append({'kind':'page_limit'})
    except Exception as exc:
        names=[title]; info['issues'].append({'kind':'prefix_inventory_error','error':error_kind(exc)})
    for name in names:
        try:
            body,url=client.get(html_url(WR+lang+'/',name))
            if wr_title(url,lang)!=name.replace('_',' '): raise ValueError('wiki_title_mismatch')
            d=Document(url,extract_blocks(body,True),sha(body),name,html_revision(soup(body)))
            docs.append(d)
        except Exception as exc: info['issues'].append({'kind':'fetch_error','title':name,'error':error_kind(exc)})
    info['pages']=[{'url':d.url,'sha256':d.digest,'revision':d.revision,'blocks':len(d.blocks)} for d in docs]
    return docs,info


class Retrieval:
    def __init__(self,client,max_pages):
        self.client=client; self.max_pages=max_pages; self.works={}

    def work(self,provider,root,lang):
        key=(provider,root,lang)
        if key not in self.works:
            self.works[key]=(collect_mia(self.client,root,self.max_pages) if provider=='mia'
                              else collect_wr(self.client,root,lang,self.max_pages))
        return self.works[key]

    def source(self,quote,selector,nav):
        stages=[]; variants=[]
        def inspect(docs,info):
            stages.append(info)
            hits=[]; truncated=False
            for d in docs:
                m,t=exact_occurrences(quote,d); hits+=m; truncated|=t
            if hits or truncated:
                return {'status':'exact_in_examined_documents' if len(hits)==1 and not truncated else 'ambiguous_exact_source',
                        'occurrences':hits,'occurrences_truncated':truncated}
            v=source_variants(quote,selector,docs)
            variants.extend(v['candidates'])
            if v['truncated']: info={**info,'variant_search_truncated':True}
            return None
        direct=nav['input']['mia_urls']
        if len(direct)==1:
            info={'provider':'mia','scope':'explicit_citation_url','root':direct[0],'issues':[],'pages':[]}
            try:
                d,_=mia_doc(self.client,direct[0])
                info['pages']=[{'url':d.url,'sha256':d.digest,'blocks':len(d.blocks)}]
                result=inspect([d],info)
                if result: return {**result,'stages':stages}
            except Exception as exc:
                stages.append({**info,'issues':[{'kind':'direct_url_error','error':error_kind(exc)}]})
        meta=nav['work_metadata']['fr']['mia_urls']
        if len(meta)==1:
            docs,info=self.work('mia',meta[0],'fr')
            result=inspect(docs,info)
            if result: return {**result,'stages':stages}
        docs,info=self.work('wikirouge',nav['route']['fr_title'],'fr')
        result=inspect(docs,info)
        if result: return {**result,'stages':stages}
        variants.sort(key=lambda c:(c['token_edits'],c['url'],c['char_start']))
        return {'status':'variant_review_required' if variants else 'source_unconfirmed_in_bounded_scope',
                'occurrences':[],'variants':variants[:5],'stages':stages}

    def retrieve(self,case):
        # Route construction receives only actual input page and selector.
        query={k:case[k] for k in ('id','wiki_title','selector')}
        quotes={}; nav=discover_case(self.client,query,quote_sink=quotes)
        row={'id':case['id'],'navigation_status':nav['status'],'navigation':nav,'auto_publish':False,'rights':'unreviewed','status':'no_work_route','candidates':[]}
        if nav['status'] not in ('mia_route_candidate','wikirouge_route_candidate'): return row
        quote=quotes[case['id']]
        if nav.get('author_check')=='author_tokens_differ':
            row['status']='author_metadata_conflict'; return row
        source=self.source(quote,case['selector'],nav); row['source']=source
        if source['status'] not in ('exact_in_examined_documents','variant_review_required'):
            row['status']='source_needs_review'; return row
        target=nav.get('mia_target',{})
        if target.get('url_accessible'):
            docs,info=self.work('mia',target['final_url'],'en')
            candidates=rank(quote,docs)
        else: candidates=[]; info=None
        if not candidates:
            docs,wr=self.work('wikirouge',nav['route']['en_title'],'en')
            row['target_attempts']=[x for x in (info,wr) if x is not None]
            info=wr; candidates=rank(quote,docs)
        else: row['target_attempts']=[info]
        row.update(target_provider=info['provider'],target_scope=info['scope'],candidates=candidates,
                   status='candidate_review_required' if candidates and source['status']=='exact_in_examined_documents'
                          else 'variant_source_candidate_review_required' if candidates else 'no_target_candidate')
        # Expected targets and needles are used only now, after retrieval.
        row['evaluation']=evaluation(candidates,case,info['provider'])
        return row


def run_auto(client,cases,max_pages):
    r=Retrieval(client,max_pages)
    rows=[r.retrieve(c) for c in cases]
    counts={status:sum(x['status']==status for x in rows) for status in sorted({x['status'] for x in rows})}
    return redact_text({'schema':'wikidebia-automatic-candidates-0.1','offline':client.offline,'auto_publish':False,
                        'cases':rows,'counts':counts,'requests':client.requests,'cache_hits':client.hits,
                        'http_events':client.events,'scope':'automatically discovered bounded documents; no complete quote or rights approved'})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache',type=Path,default=ROOT/'.pilot-cache')
    p.add_argument('--out',type=Path,default=ROOT/'reports-live'/'auto-candidates.json')
    p.add_argument('--budget',type=int,default=200)
    p.add_argument('--max-pages',type=int,default=24)
    p.add_argument('--case',action='append',default=[])
    p.add_argument('--replay',action='store_true')
    a=p.parse_args()
    if a.budget<1 or a.max_pages<1: p.error('positive limits required')
    cases=json.loads((ROOT/'cases-v02.json').read_text())['cases']
    cases=[c for c in cases if not a.case or c['id'] in a.case]
    if not cases or set(a.case)-{c['id'] for c in cases}: p.error('unknown or empty case')
    c=PilotClient(a.cache,budget=a.budget); report=run_auto(c,cases,a.max_pages)
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    summary={k:report[k] for k in ('counts','requests','cache_hits','scope','auto_publish')}
    summary['cases']=[{'id':r['id'],'navigation_status':r['navigation_status'],'status':r['status'],
                      'source_status':r.get('source',{}).get('status'),'source_matches':len(r.get('source',{}).get('occurrences',[])),
                      'source_stages':[{'provider':s['provider'],'scope':s['scope'],'pages':len(s['pages']),'issues':s['issues']} for s in r.get('source',{}).get('stages',[])],
                      'target_provider':r.get('target_provider'),'target_scope':r.get('target_scope'),
                      'candidate_count':len(r['candidates']),'evaluation':r.get('evaluation')} for r in report['cases']]
    rc=int(not any(r['status']=='candidate_review_required' for r in report['cases']))
    if a.replay:
        expected={}
        for e in c.events:
            old=expected.get(e['request_url'])
            if old and any(old.get(k)!=e.get(k) for k in ('status','sha256','final_url')):
                raise RuntimeError('inconsistent_live_response')
            expected[e['request_url']]=e
        replay_client=PilotClient(a.cache,budget=a.budget,offline=True,expected_events=expected)
        replay=run_auto(replay_client,cases,a.max_pages)
        same=report['cases']==replay['cases'] and report['counts']==replay['counts']
        mismatches=replay_mismatches(expected,replay_client.events)
        integrity={'results_match':same,'response_mismatches':mismatches,'network_requests':replay_client.requests}
        replay['replay_integrity']=integrity
        a.out.with_name(a.out.stem+'-replay.json').write_text(json.dumps(replay,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        summary['replay_integrity']=integrity
        rc=max(rc,int(not same or bool(mismatches) or replay_client.requests!=0))
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    return rc


if __name__=='__main__': raise SystemExit(main())
