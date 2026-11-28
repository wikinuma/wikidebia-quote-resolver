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
from pilot_full import recorded_events
from pilot_full import (PilotClient,WR,soup,extract_blocks,html_revision,html_url,wr_title,
                        redact_text,replay_mismatches)
from alignment import exact_occurrences, aligned_candidates
from bibliography import catalog_route, VERIFIED_AUTHORS
from window_rank import rank_short_windows
from variants import source_variants
from source_evidence import omission_candidates,multi_anchor_candidates
from compare import evaluation
from section_match import extract_headings, automatic_alignment
from refinement import DirectAlignmentCache, review_packet, select_source_hit
from source_refinement import long_variants, fragment_paths
from chapter_routes import chapter_route, chapter_urls, retrieve_chapter
from source_pdf import SourcePDFs
from alignment import OMISSION


def explicit_toc(body,root):
    """Legacy MIA indices are not necessarily called index.htm."""
    path=urlsplit(root).path
    if path.endswith('/') or posixpath.basename(path).lower() in ('index.htm','index.html'): return True
    s=soup(body)
    markers={'sommaire','table des matieres','table of contents','contents'}
    title=norm(s.title.get_text(' ',strip=True)) if s.title else ''
    if any(' '+m+' ' in ' '+title+' ' for m in markers): return True
    # An exact visible label, not a mention of "contents" in ordinary prose.
    for node in s.find_all(['h1','h2','h3','h4','p','b','strong','center']):
        if norm(node.get_text(' ',strip=True)) in markers: return True
    return False


def mia_toc_links(body,root):
    """Only directly linked work-stem or directory-index HTML pages and
    explicitly labelled dated prefaces in the same language archive.
    No URL synthesis and no unbounded recursive link traversal.
    """
    p=urlsplit(root); path=p.path
    directory=path if path.endswith('/') else posixpath.dirname(path)+'/'
    base=posixpath.basename(path)
    index=explicit_toc(body,root)
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
        if not label or label in ('home','index','accueil','sommaire','retour','previous','next','back','download','zip','pdf'): continue
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
    def __init__(self,client,max_pages,catalog=(),local_index=None,refined=False,chapter_catalog=(),semantic_index=None,toc_matcher=None,hybrid=False,work_library=None,strict_link_policy=False,prefer_catalog=False,source_pdf_catalog=()):
        self.client=client; self.max_pages=max_pages; self.works={}; self.source_documents={}; self.catalog=tuple(catalog)
        self.local_index=local_index
        self.refined=refined
        self.chapter_catalog=tuple(chapter_catalog)
        self.direct_cache=DirectAlignmentCache(store=work_library.alignment_store if work_library is not None else None)
        self.work_library=work_library
        self.strict_link_policy=strict_link_policy
        self.prefer_catalog=prefer_catalog
        self.source_pdfs=SourcePDFs(client,source_pdf_catalog) if source_pdf_catalog else None
        self.semantic_index=semantic_index
        self.toc_matcher=toc_matcher
        self.hybrid=hybrid
        if (toc_matcher is not None or hybrid) and (semantic_index is None or not refined):
            raise ValueError('experimental_retrieval_requires_refined_semantic_index')
        if toc_matcher is not None and toc_matcher.client is not client:
            raise ValueError('toc_client_must_share_retrieval_budget')

    def work(self,provider,root,lang):
        key=(provider,root,lang)
        if key not in self.works:
            result=(collect_mia(self.client,root,self.max_pages) if provider=='mia'
                    else collect_wr(self.client,root,lang,self.max_pages))
            # Successful raw responses are cached by the HTTP client. Retry
            # the incomplete collection at the next case's fresh budget.
            if any(i.get('error')=='RequestBudgetExceeded' for i in result[1].get('issues',[])):
                return result
            self.works[key]=result
        return self.works[key]

    def source(self,quote,selector,nav,preferred_source=None):
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
        if preferred_source:
            info={'provider':'mia','scope':'researched_source_document','root':preferred_source,'issues':[],'pages':[]}
            try:
                d,_=mia_doc(self.client,preferred_source)
                if canonical_mia(d.url)!=preferred_source: raise ValueError('catalog_source_redirect')
                info['pages']=[{'url':d.url,'sha256':d.digest,'blocks':len(d.blocks)}]
                result=inspect([d],info)
                if result: return {**result,'stages':stages}
            except Exception as exc:
                stages.append({**info,'issues':[{'kind':'researched_source_error','error':error_kind(exc)}]})
        direct=nav['input']['mia_urls']; examined_root=None
        if len(direct)==1:
            info={'provider':'mia','scope':'explicit_citation_url','root':direct[0],'issues':[],'pages':[]}
            try:
                d,body=mia_doc(self.client,direct[0])
                info['pages']=[{'url':d.url,'sha256':d.digest,'blocks':len(d.blocks)}]
                result=inspect([d],info)
                if result: return {**result,'stages':stages}
                if explicit_toc(body,d.url):
                    docs,toc_info=self.work('mia',direct[0],'fr')
                    examined_root=direct[0]
                    result=inspect(docs,toc_info)
                    if result: return {**result,'stages':stages}
            except Exception as exc:
                stages.append({**info,'issues':[{'kind':'direct_url_error','error':error_kind(exc)}]})
        meta=nav.get('work_metadata',{}).get('fr',{}).get('mia_urls',[])
        if len(meta)==1 and meta[0]!=examined_root:
            docs,info=self.work('mia',meta[0],'fr')
            result=inspect(docs,info)
            if result: return {**result,'stages':stages}
        root=nav.get('route',{}).get('fr_title')
        if root:
            docs,info=self.work('wikirouge',root,'fr')
            result=inspect(docs,info)
            if result: return {**result,'stages':stages}
        diagnostics=[]
        docs=list(self.source_documents.values())
        # Explicit omitted fragments are a distinct kind of evidence, never
        # exact contiguous quotation. Multi-anchor fallback runs only when
        # the original conservative single-selector method found nothing.
        omitted=omission_candidates(quote,docs)
        diagnostics.append({k:v for k,v in omitted.items() if k!='candidates'})
        if omitted['candidates']:
            variants=omitted['candidates']
        if not variants:
            multi=multi_anchor_candidates(quote,docs)
            diagnostics.append({k:v for k,v in multi.items() if k!='candidates'})
            variants.extend(multi['candidates'])
        if self.refined and (not variants or (OMISSION.search(quote) and not omitted['candidates'])):
            extra=fragment_paths(quote,docs) if OMISSION.search(quote) else long_variants(quote,docs)
            diagnostics.append({k:v for k,v in extra.items() if k!='candidates'})
            if extra['candidates']:
                variants=extra['candidates']
        variants=list({(c['url'],c['sha256'],c['char_start'],c['char_end']):c for c in variants}.values())
        variants.sort(key=lambda c:(c['token_edits'],c['url'],c['char_start']))
        return {'status':'variant_review_required' if variants else 'source_unconfirmed_in_bounded_scope',
                'occurrences':[],'variants':variants[:5],'stages':stages,'source_evidence_diagnostics':diagnostics}

    def semantic_sidecar(self,row,quote,docs):
        """Opt-in candidate comparison on already retrieved documents.
        Never replace the baseline first-pass packet or trigger another GET."""
        if self.semantic_index is None:
            return row
        urls=row.get('navigation',{}).get('input',{}).get('mia_urls',[])
        if not urls or any(canonical_mia(u)!=u for u in urls):
            return row
        from sentence_index import rank_scoped
        import time
        start=time.monotonic()
        try:
            semantic=rank_scoped(self.semantic_index,quote,docs,
                                 row.get('section_alignment',{}).get('candidates',[])+row.get('candidates',[]))
        except Exception as exc:
            semantic={'status':'execution_error','error':error_kind(exc),'candidates':[],
                      'auto_publish':False,'rights':'unreviewed'}
        semantic['elapsed_seconds']=round(time.monotonic()-start,6)
        row['semantic_experiment']=semantic
        if self.hybrid:
            from rank_fusion import fused_review_queue
            baseline=row.get('section_alignment',{}).get('candidates',[])+row.get('candidates',[])
            try:
                row['hybrid_review_queue']=fused_review_queue(semantic['candidates'],baseline,docs)
            except Exception as exc:
                row['hybrid_review_queue']={'status':'fusion_unavailable','error':error_kind(exc),
                                            'candidates':[],'fallback_preserved':True,'auto_publish':False}
        return row

    def retrieve_mapped_chapter(self,row,quote,source,chapter):
        try:
            docs=[]
            for url in chapter_urls(chapter):
                doc,body=mia_doc(self.client,url)
                if doc.url!=url or explicit_toc(body,doc.url):
                    raise ValueError('chapter_target_not_expected_leaf')
                docs.append(doc)
            row.update(retrieve_chapter(quote,source,list(self.source_documents.values()),
                                        docs,chapter,self.direct_cache))
            return self.semantic_sidecar(row,quote,docs)
        except Exception as exc:
            row['chapter_route_failure']={'route':chapter,'error':error_kind(exc)}
            # Missing parts or changed URLs cannot silently become a complete chapter.
            row['status']='chapter_source_or_target_unavailable'
            return row

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
        source_nav=row['navigation'] if self.refined else {'input':row['navigation']['input']}
        source=self.source(quote,selector,source_nav,preferred_source=entry.get('source_document'))
        row['source']=source
        if source['status'] not in ('exact_in_examined_documents','variant_review_required'):
            row['status']='source_needs_review'; return row
        if self.refined and entry.get('source_document'):
            # A specific chapter mapping must not be used for another chapter
            # merely because the encompassing work contains the quotation.
            doc,_=select_source_hit(source,list(self.source_documents.values()),entry['source_document'])
            if doc is None:
                row['status']='catalog_source_document_unconfirmed'; return row
        provider,url=entry['target_provider'],entry['target_url']
        info={'provider':provider,'scope':'researched_single_target_page_not_complete_edition','root':url,'issues':[],'pages':[]}
        row['target_attempts']=[info]
        if entry.get('target_toc'):
            chapter=chapter_route(row['navigation']['input'],source,self.chapter_catalog) if self.refined else None
            if chapter is not None and chapter['en_toc']==url:
                return self.retrieve_mapped_chapter(row,quote,source,chapter)
            docs,info=self.work('mia',url,'en')
            row['target_attempts']=[info]
            candidates=rank(quote,docs)
            if not candidates:
                fallback=rank_short_windows(quote,docs); candidates=fallback['candidates']
                row['lexical_fallback']={k:v for k,v in fallback.items() if k!='candidates'}
            aligned=automatic_alignment(quote,source,list(self.source_documents.values()),docs,
                                      (entry['source_title'],entry['target_title']),(entry['authors'],),
                                      alignment_cache=self.direct_cache if self.refined else None)
            row.update(target_provider='mia',target_scope=info['scope'],candidates=candidates,section_alignment=aligned,
                       status='catalog_candidate_review_required' if candidates or aligned['candidates'] else 'catalog_no_candidate')
            return self.semantic_sidecar(row,quote,docs)
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
        if not candidates:
            fallback=rank_short_windows(quote,[doc]); candidates=fallback['candidates']
            row['lexical_fallback']={k:v for k,v in fallback.items() if k!='candidates'}
        row.update(target_provider=provider,target_scope=info['scope'],candidates=candidates,
                   status='catalog_candidate_review_required' if candidates else 'catalog_no_candidate',
                   rights='unreviewed')
        expected_source=entry.get('source_document',entry['source_url'])
        actual_source=source['occurrences'][0]['url'] if len(source['occurrences'])==1 else None
        if entry.get('whole_document_parallel'):
            if self.refined:
                aligned=self.direct_cache.candidate(source,list(self.source_documents.values()),[doc],expected_source,'researched_bibliographic_catalog')
            elif actual_source and canonical_mia(actual_source)==expected_source:
                aligned=document_alignment(quote,source,list(self.source_documents.values()),[doc],'researched_bibliographic_catalog')
            else:
                aligned={'status':'not_applicable','candidates':[]}
            row['section_alignment']=aligned
            if aligned['candidates'] and not candidates: row['status']='catalog_candidate_review_required'
        return self.semantic_sidecar(row,quote,[doc])

    def retrieve(self,case):
        row=self._retrieve(case)
        if self.refined:
            row['review_packet']=review_packet(row)
            queue=row.get('hybrid_review_queue',{}).get('candidates',[])
            if self.hybrid and queue and row.get('semantic_experiment',{}).get('candidates'):
                row['review_packet']={'method':'experimental_hybrid_first_v1','candidates':queue[:1],
                                      'deferred_distinct_candidates':len(queue)-1,
                                      'first_pass_only':True,'fallback_preserved':True,
                                      'auto_publish':False,'rights':'unreviewed'}
        return row

    def _retrieve(self,case):
        # Route construction receives only actual input page and selector.
        query={k:case[k] for k in ('id','wiki_title')}
        if 'citation_ref' in case: query['citation_ref']=case['citation_ref']
        else: query['selector']=case['selector']
        quotes={}
        kwargs={'local_index':self.local_index} if self.local_index is not None else {}
        if self.work_library is not None: kwargs['work_library']=self.work_library
        if self.strict_link_policy: kwargs['strict_link_policy']=True
        nav=discover_case(self.client,query,quote_sink=quotes,**kwargs)
        row={'id':case['id'],'navigation_status':nav['status'],'navigation':nav,'auto_publish':False,'rights':'unreviewed','status':'no_work_route','candidates':[]}
        if nav['status']=='ai_translation_required':
            row['status']='ai_translation_required'
            return row
        # An explicitly selected documented alternative is a new retrieval
        # path, not a way to override author conflicts or skip source checks.
        if (self.prefer_catalog and case['id'] in quotes
                and nav.get('author_check') != 'author_tokens_differ'
                and nav['status'] in ('mia_route_candidate','wikirouge_route_candidate',
                                      'work_not_resolved','work_link_unconfirmed','work_metadata_unconfirmed')
                and catalog_route(nav.get('input', {}), self.catalog) is not None):
            row['catalog_preferred'] = True
            return self.retrieve_catalog(case,row,quotes[case['id']])
        if nav['status'] not in ('mia_route_candidate','wikirouge_route_candidate'):
            if self.catalog and nav['status'] in ('work_not_resolved','work_link_unconfirmed','work_metadata_unconfirmed') and case['id'] in quotes:
                return self.retrieve_catalog(case,row,quotes[case['id']])
            return row
        quote=quotes[case['id']]
        if nav.get('author_check')=='author_tokens_differ':
            row['status']='author_metadata_conflict'; return row
        if self.source_pdfs is not None:
            def fetch_leaf(url):
                doc,body=mia_doc(self.client,url)
                if explicit_toc(body,doc.url):
                    raise ValueError('source_pdf_target_is_toc')
                return doc
            alternate=self.source_pdfs.retrieve(row,quote,self.direct_cache,fetch_leaf)
            if alternate is not None:
                return alternate
        selector=case.get('selector') or automatic_selector(quote)
        source=self.source(quote,selector,nav); row['source']=source
        if source['status'] not in ('exact_in_examined_documents','variant_review_required'):
            row['status']='source_needs_review'
            if self.catalog and source['status']=='source_unconfirmed_in_bounded_scope':
                row['automatic_attempt']={'status':row['status'],'source_status':source['status']}
                return self.retrieve_catalog(case,row,quote)
            return row
        if self.refined and self.chapter_catalog:
            chapter=chapter_route(nav['input'],source,self.chapter_catalog)
            if chapter is not None:
                return self.retrieve_mapped_chapter(row,quote,source,chapter)
        if self.toc_matcher is not None:
            from early_toc import try_early_toc
            candidate,diagnostic=try_early_toc(self,row,quote,source)
            if candidate is not None:
                return candidate
            row['toc_route_experiment']=diagnostic
        target=nav.get('mia_target',{})
        if target.get('url_accessible'):
            docs,info=self.work('mia',target['final_url'],'en')
            candidates=rank(quote,docs)
            if not candidates:
                fallback=rank_short_windows(quote,docs); candidates=fallback['candidates']
                row['lexical_fallback']={k:v for k,v in fallback.items() if k!='candidates'}
        else: candidates=[]; info=None
        if not candidates:
            docs,wr=self.work('wikirouge',nav['route']['en_title'],'en')
            row['target_attempts']=[x for x in (info,wr) if x is not None]
            info=wr; candidates=rank(quote,docs)
            if not candidates:
                fallback=rank_short_windows(quote,docs); candidates=fallback['candidates']
                row['lexical_fallback']={k:v for k,v in fallback.items() if k!='candidates'}
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
        aligned={'status':'not_applicable','candidates':[]}
        if not self.refined:
            aligned=automatic_alignment(quote,source,fr_docs,docs,
                                        (route['fr_title'],route['en_title'],nav['input'].get('work','')),
                                        (meta['fr'].get('authors',''),meta['en'].get('authors','')))
        # Direct, independently crosslinked single-document editions may
        # align without translated chapter titles. Require source and target
        # exact metadata URLs, reciprocal roots, matched authors, and no TOC.
        source_url=source.get('occurrences',[{}])[0].get('url') if source.get('occurrences') else None
        indexed_direct=(nav.get('route_method')=='local_wikirouge_snapshot'
                        and route.get('scope')=='direct_native_link'
                        and nav.get('citation_mia_url_in_root_metadata') is True)
        direct_parallel=(info['provider']=='mia' and len(docs)==1 and not info['issues']
                         and not info.get('links') and (route.get('reciprocal') is True or indexed_direct)
                         and nav.get('author_check') in VERIFIED_AUTHORS
                         and author_check(meta['fr'].get('authors',''),meta['en'].get('authors','')) in VERIFIED_AUTHORS
                         and len(meta['fr'].get('mia_urls',[]))==1
                         and (self.refined or (source_url is not None and [canonical_mia(source_url)]==meta['fr'].get('mia_urls')))
                         and [canonical_mia(docs[0].url)]==meta['en'].get('mia_urls'))
        if not aligned['candidates'] and direct_parallel:
            method='indexed_native_link_direct_documents' if indexed_direct else 'reciprocal_work_metadata_direct_documents'
            if self.refined:
                aligned=self.direct_cache.candidate(source,fr_docs,docs,meta['fr']['mia_urls'][0],method)
            else:
                aligned=document_alignment(quote,source,fr_docs,docs,method)
        if self.refined and not aligned['candidates']:
            structural=automatic_alignment(quote,source,fr_docs,docs,
                                        (route['fr_title'],route['en_title'],nav['input'].get('work','')),
                                        (meta['fr'].get('authors',''),meta['en'].get('authors','')),
                                        alignment_cache=self.direct_cache)
            if structural['candidates'] or aligned['status']=='not_applicable': aligned=structural
        if aligned['candidates']:
            aligned['evaluation']=evaluation(aligned['candidates'],case,info['provider'])
        else: aligned['evaluation']={'state':'not_executed_or_unresolved'}
        row['section_alignment']=aligned
        if self.refined and aligned['candidates'] and not row['candidates']:
            row['status']='aligned_candidate_review_required'
        if self.catalog and not row['candidates'] and not aligned['candidates']:
            row['automatic_attempt']={'status':row['status'],'source_status':source['status']}
            return self.retrieve_catalog(case,row,quote)
        return self.semantic_sidecar(row,quote,docs)


def run_auto(client,cases,max_pages,catalog=(),local_index=None,refined=False,chapter_catalog=(),work_library=None,semantic_index=None,toc_matcher=None,hybrid=False,strict_link_policy=False):
    r=Retrieval(client,max_pages,catalog,local_index,refined,chapter_catalog,semantic_index,toc_matcher,hybrid,work_library,strict_link_policy)
    rows=[r.retrieve(c) for c in cases]
    counts={status:sum(x['status']==status for x in rows) for status in sorted({x['status'] for x in rows})}
    alignment_stats=({'alignment_stats':{**work_library.alignment_store.stats,
                      'alignment_memory_hits':r.direct_cache.hits,
                      'alignment_computations':r.direct_cache.computations}} if work_library is not None else {})
    return redact_text({**alignment_stats,'schema':'wikidebia-automatic-candidates-0.1','offline':client.offline,'auto_publish':False,
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
    p.add_argument('--local-index',type=Path,help='Path to verified snapshot manifest; default is legacy live navigation')
    p.add_argument('--refined',action='store_true',help='Cache direct alignments and expose a first-pass review packet')
    p.add_argument('--catalog',type=Path,help='Optional researched bibliography')
    p.add_argument('--chapter-catalog',type=Path,help='Optional reviewed chapter mappings; used in refined mode')
    p.add_argument('--work-library',type=Path,help='Persistent work-level metadata SQLite database; no quotation decisions')
    p.add_argument('--link-policy',action='store_true',help='Restrict recovery to literal MIA Citation links; automatic with --work-library')
    p.add_argument('--cases-file',type=Path,help='JSON object with pinned cases array; default pilot cases')
    p.add_argument('--model-dir',type=Path,help='Optional pinned local safetensors model, with work library and refined mode')
    p.add_argument('--vector-cache',type=Path,help='Shared SQLite cache for local model inputs')
    a=p.parse_args()
    if bool(a.model_dir)!=bool(a.vector_cache) or (a.model_dir and not (a.work_library and a.refined)):
        p.error('local semantic mode requires --model-dir, --vector-cache, --work-library and --refined')
    if a.work_library and a.replay: p.error('strict replay requires unchanged library state; use without --work-library')
    if a.budget<1 or a.max_pages<1: p.error('positive limits required')
    if a.local_index:
        from wikirouge_index import open_snapshot
        with open_snapshot(a.local_index) as index:
            return execute(a,index)
    if a.work_library:
        from wikirouge_incremental import open_current
        with open_current() as index:
            return execute(a,index)
    return execute(a,None)


def execute(a,local_index):
    cases=json.loads((a.cases_file or ROOT/'cases-v02.json').read_text())['cases']
    cases=[c for c in cases if not a.case or c['id'] in a.case]
    if not cases or set(a.case)-{c['id'] for c in cases}: p.error('unknown or empty case')
    from bibliography import load_catalog
    catalog=load_catalog(a.catalog or ROOT/'bibliographic-catalog.json') if a.catalog or a.work_library else ()
    from chapter_routes import load_chapters
    chapters=load_chapters(a.chapter_catalog or ROOT/'chapter-catalog.json') if a.chapter_catalog or a.work_library else ()
    c=PilotClient(a.cache,budget=a.budget)
    if a.work_library:
        from work_library import WorkLibrary
        with WorkLibrary(a.work_library) as library:
            kwargs={}
            enc=None
            try:
                if a.model_dir:
                    from embedding import LocalEncoder
                    from sentence_index import SentenceVectorIndex
                    from toc_structure import AugmentedTocMatcher
                    enc=LocalEncoder(a.model_dir,a.vector_cache)
                    kwargs=dict(semantic_index=SentenceVectorIndex(enc),toc_matcher=AugmentedTocMatcher(c,enc),hybrid=True)
                report=run_auto(c,cases,a.max_pages,catalog,local_index,refined=a.refined,chapter_catalog=chapters,work_library=library,strict_link_policy=True,**kwargs)
                report['work_library_stats']=library.stats.copy()
                if enc is not None: report['encoder_stats']=enc.stats.copy()
            finally:
                if enc is not None: enc.close()
    else:
        report=run_auto(c,cases,a.max_pages,catalog,local_index,refined=a.refined,chapter_catalog=chapters,strict_link_policy=a.link_policy)
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    summary={k:report[k] for k in ('counts','requests','cache_hits','scope','auto_publish')}
    for key in ('work_library_stats','encoder_stats','alignment_stats'):
        if key in report: summary[key]=report[key]
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
        expected=recorded_events(c.events)
        replay_client=PilotClient(a.cache,budget=a.budget,offline=True,expected_events=expected)
        replay=run_auto(replay_client,cases,a.max_pages,catalog,local_index,refined=a.refined,chapter_catalog=chapters,strict_link_policy=a.link_policy)
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
