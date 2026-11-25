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
from collections import Counter

from navigation import canonical_mia,discover_case,error_kind,author_check
from resolver import ROOT,Document,subpages,rank,norm,sha
from pilot_full import (PilotClient,WR,soup,extract_blocks,html_revision,html_url,wr_title,
                        redact_text,replay_mismatches)
from alignment import exact_occurrences, aligned_candidates
from bibliography import catalog_route, VERIFIED_AUTHORS
from variants import source_variants
from compare import evaluation
from section_match import extract_headings, automatic_alignment


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
    return Document(final,extract_blocks(body),sha(body),s.title.get_text(' ',strip=True) if s.title else '',headings=extract_headings(s)),body


def collect_mia(client,root,max_pages):
    docs=[]; info={'provider':'mia','root':root,'scope':'bounded_direct_toc_links_not_complete_edition','issues':[],'pages':[]}
    try:
        doc,body=mia_doc(client,root); docs.append(doc)
        links=mia_toc_links(body,doc.url); info['links']=links
        labels={}
        for a in soup(body).find_all('a',href=True):
            u=canonical_mia(urljoin(doc.url,a['href']))
            label=' '.join(a.get_text(' ',strip=True).split())
            if u and 1<=len(norm(label).split())<=20 and len(label)<=220:
                labels.setdefault(u,set()).add(label)
        if len(links)+1>max_pages: info['issues'].append({'kind':'page_limit'})
        for item in links[:max_pages-1]:
            try:
                d,_=mia_doc(client,item['url'])
                d.headings=tuple(dict.fromkeys((*d.headings,*sorted(labels.get(item['url'],set())))))
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
            d=Document(url,extract_blocks(body,True),sha(body),name,html_revision(soup(body)),
                       headings=tuple(dict.fromkeys((*(extract_headings(soup(body))),*([name.rsplit('/',1)[-1]] if '/' in name else [])))))
            docs.append(d)
        except Exception as exc: info['issues'].append({'kind':'fetch_error','title':name,'error':error_kind(exc)})
    info['pages']=[{'url':d.url,'sha256':d.digest,'revision':d.revision,'blocks':len(d.blocks)} for d in docs]
    return docs,info


def automatic_selector(quote):
    """A unique informative 4-6 token span selected from the quote alone.
    Used only for approximate source lookup, never for choosing a template.
    """
    words=norm(quote).split(); freq=Counter(words)
    stop=set('le la les l de des du un une et en a au aux d que qui est sont pour par dans ce cette ces il ils elle elles'.split())
    spans=[]
    for size in (6,5,4):
        counts=Counter(tuple(words[i:i+size]) for i in range(len(words)-size+1))
        for i in range(len(words)-size+1):
            seq=tuple(words[i:i+size])
            if counts[seq]!=1: continue
            score=sum(min(12,len(w))/freq[w] for w in seq if w not in stop and len(w)>=3)
            if score: spans.append((score,-i,size,' '.join(seq)))
    return max(spans)[-1] if spans else ''


def document_alignment(quote,source,fr_docs,en_docs,route_method):
    """Alignment candidate when metadata identifies two standalone documents.
    Do not infer parallel sections from a single fetched page in a larger work.
    """
    result={'status':'not_applicable','candidates':[],'auto_publish':False,'rights':'unreviewed',
            'method':'bibliographic_document_alignment_v1'}
    hits=source.get('occurrences',[])
    if source.get('status')!='exact_in_examined_documents' or len(hits)!=1 or source.get('occurrences_truncated') or len(en_docs)!=1:
        return result
    hit=hits[0]
    matches=[d for d in fr_docs if d.url==hit['url'] and d.digest==hit['sha256']]
    if len(matches)!=1: return result
    fr,en=matches[0],en_docs[0]
    n,m=(sum(len(norm(b).split()) for b in d.blocks) for d in (fr,en))
    if min(n,m)<40 or not .5<=m/max(1,n)<=2:
        return {**result,'status':'incompatible_document_lengths'}
    result['pair']={'status':'bibliographic_document_hypothesis','fr_url':fr.url,'en_url':en.url,
                    'route_method':route_method,'length_ratio':round(m/n,4)}
    attempts=[]
    for kind in ('sentence','paragraph'):
        a=aligned_candidates(quote,fr,en,kind)
        attempts.append({'kind':kind,'status':a['status'],'alignment':a.get('alignment')})
        result['status']=a['status']
        if a['candidates']:
            result['candidates']=[{**c,'pair_route':route_method,'boundary_verified':False,'rights':'unreviewed'} for c in a['candidates']]
            break
        if a['status']!='alignment_budget_exceeded': break
    result['attempts']=attempts
    return result


class Retrieval:
    def __init__(self,client,max_pages,catalog=()):
        self.client=client; self.max_pages=max_pages; self.works={}; self.source_documents={}; self.catalog=tuple(catalog)

    def work(self,provider,root,lang):
        key=(provider,root,lang)
        if key not in self.works:
            self.works[key]=(collect_mia(self.client,root,self.max_pages) if provider=='mia'
                              else collect_wr(self.client,root,lang,self.max_pages))
        return self.works[key]

    def source(self,quote,selector,nav):
        stages=[]; variants=[]; self.source_documents={}; inspected=set()
        def inspect(docs,info):
            stages.append(info)
            fresh=[]
            for d in docs:
                key=(d.url,d.digest)
                if key in self.source_documents:
                    d.headings=tuple(dict.fromkeys((*self.source_documents[key].headings,*d.headings)))
                self.source_documents[key]=d
                if key not in inspected:
                    fresh.append(d); inspected.add(key)
            stages[-1]={**info,'new_documents_examined':len(fresh)}
            hits=[]; truncated=False
            for d in fresh:
                m,t=exact_occurrences(quote,d); hits+=m; truncated|=t
            if hits or truncated:
                return {'status':'exact_in_examined_documents' if len(hits)==1 and not truncated else 'ambiguous_exact_source',
                        'occurrences':hits,'occurrences_truncated':truncated}
            v=source_variants(quote,selector,fresh)
            variants.extend(v['candidates'])
            if v['truncated']: stages[-1]['variant_search_truncated']=True
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
        meta=nav.get('work_metadata',{}).get('fr',{}).get('mia_urls',[])
        if len(meta)==1:
            docs,info=self.work('mia',meta[0],'fr')
            result=inspect(docs,info)
            if result: return {**result,'stages':stages}
        root=nav.get('route',{}).get('fr_title')
        if root:
            docs,info=self.work('wikirouge',root,'fr')
            result=inspect(docs,info)
            if result: return {**result,'stages':stages}
        variants=list({(c['url'],c['sha256'],c['char_start'],c['char_end']):c for c in variants}.values())
        variants.sort(key=lambda c:(c['token_edits'],c['url'],c['char_start']))
        return {'status':'variant_review_required' if variants else 'source_unconfirmed_in_bounded_scope',
                'occurrences':[],'variants':variants[:5],'stages':stages}

    def retrieve_catalog(self,case,row,quote):
        # Only use explicit researched mappings when the ordinary route is
        # unresolved. Never override a conflicting or ambiguous author.
        route=catalog_route(row['navigation'].get('input',{}),self.catalog)
        if route is None: return row
        entry=route['entry']; row['bibliographic_route']=route
        if entry['target_provider']=='bibliography_only':
            row['status']='bibliographic_lead_only'; return row
        selector=case.get('selector') or automatic_selector(quote)
        # A catalog route is grounded in the exact cited source URL only.
        source=self.source(quote,selector,{'input':row['navigation']['input']})
        row['source']=source
        if source['status'] not in ('exact_in_examined_documents','variant_review_required'):
            row['status']='source_needs_review'; return row
        provider,url=entry['target_provider'],entry['target_url']
        info={'provider':provider,'scope':'researched_single_target_page_not_complete_edition','root':url,'issues':[],'pages':[]}
        row['target_attempts']=[info]
        try:
            if provider=='mia':
                doc,_=mia_doc(self.client,url)
                if canonical_mia(doc.url)!=url: raise ValueError('catalog_target_redirect')
            else:
                body,final=self.client.get(url)
                if wr_title(final,'en')!=wr_title(url,'en'): raise ValueError('catalog_target_redirect')
                doc=Document(final,extract_blocks(body,True),sha(body),wr_title(final,'en'),html_revision(soup(body)))
            if not doc.blocks: raise ValueError('empty_catalog_target')
        except Exception as exc:
            info['issues'].append({'kind':'target_error','error':error_kind(exc)})
            row['status']='catalog_target_unavailable'; return row
        info['pages']=[{'url':doc.url,'sha256':doc.digest,'revision':doc.revision,'blocks':len(doc.blocks)}]
        candidates=rank(quote,[doc])
        row.update(target_provider=provider,target_scope=info['scope'],candidates=candidates,
                   status='catalog_candidate_review_required' if candidates else 'catalog_no_candidate',
                   rights='unreviewed')
        if entry.get('whole_document_parallel'):
            aligned=document_alignment(quote,source,list(self.source_documents.values()),[doc],'researched_bibliographic_catalog')
            row['section_alignment']=aligned
            if aligned['candidates'] and not candidates: row['status']='catalog_candidate_review_required'
        return row

    def retrieve(self,case):
        # Route construction receives only actual input page and selector.
        query={k:case[k] for k in ('id','wiki_title')}
        if 'citation_ref' in case: query['citation_ref']=case['citation_ref']
        else: query['selector']=case['selector']
        quotes={}; nav=discover_case(self.client,query,quote_sink=quotes)
        row={'id':case['id'],'navigation_status':nav['status'],'navigation':nav,'auto_publish':False,'rights':'unreviewed','status':'no_work_route','candidates':[]}
        if nav['status'] not in ('mia_route_candidate','wikirouge_route_candidate'):
            if self.catalog and nav['status'] in ('work_not_resolved','work_link_unconfirmed','work_metadata_unconfirmed') and case['id'] in quotes:
                return self.retrieve_catalog(case,row,quotes[case['id']])
            return row
        quote=quotes[case['id']]
        if nav.get('author_check')=='author_tokens_differ':
            row['status']='author_metadata_conflict'; return row
        selector=case.get('selector') or automatic_selector(quote)
        source=self.source(quote,selector,nav); row['source']=source
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
        # Preserve the original lexical list. A separately measured structural
        # candidate uses heading evidence independent of the quotation.
        fr_docs=list(self.source_documents.values())
        route=nav['route']; meta=nav['work_metadata']
        aligned=automatic_alignment(quote,source,fr_docs,docs,
                                    (route['fr_title'],route['en_title'],nav['input'].get('work','')),
                                    (meta['fr'].get('authors',''),meta['en'].get('authors','')))
        # Direct, independently crosslinked single-document editions may
        # align without translated chapter titles. Require source and target
        # exact metadata URLs, reciprocal roots, matched authors, and no TOC.
        source_url=source.get('occurrences',[{}])[0].get('url') if source.get('occurrences') else None
        direct_parallel=(info['provider']=='mia' and len(docs)==1 and not info['issues']
                         and not info.get('links') and route.get('reciprocal') is True
                         and nav.get('author_check') in VERIFIED_AUTHORS
                         and author_check(meta['fr'].get('authors',''),meta['en'].get('authors','')) in VERIFIED_AUTHORS
                         and source_url is not None and [canonical_mia(source_url)]==meta['fr'].get('mia_urls')
                         and [canonical_mia(docs[0].url)]==meta['en'].get('mia_urls'))
        if not aligned['candidates'] and direct_parallel:
            aligned=document_alignment(quote,source,fr_docs,docs,'reciprocal_work_metadata_direct_documents')
        if aligned['candidates']:
            aligned['evaluation']=evaluation(aligned['candidates'],case,info['provider'])
        else: aligned['evaluation']={'state':'not_executed_or_unresolved'}
        row['section_alignment']=aligned
        return row


def run_auto(client,cases,max_pages,catalog=()):
    r=Retrieval(client,max_pages,catalog)
    rows=[r.retrieve(c) for c in cases]
    counts={status:sum(x['status']==status for x in rows) for status in sorted({x['status'] for x in rows})}
    return redact_text({'schema':'wikidebia-automatic-candidates-0.1','offline':client.offline,'auto_publish':False,
                        'cases':rows,'counts':counts,'requests':client.requests,'cache_hits':client.hits,
                        'http_events':client.events,'scope':'automatic routes with optional researched catalog; no complete quote or rights approved' if catalog else 'automatically discovered bounded documents; no complete quote or rights approved'})


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
                      'candidate_count':len(r['candidates']),'evaluation':r.get('evaluation'),
                      'lexical_candidate_words':sum(c.get('text_words',0) for c in r['candidates']),
                      'section_alignment':{k:v for k,v in r.get('section_alignment',{}).items() if k!='candidates'},
                      'alignment_candidate_words':sum(c.get('text_words',0) for c in r.get('section_alignment',{}).get('candidates',[]))} for r in report['cases']]
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
