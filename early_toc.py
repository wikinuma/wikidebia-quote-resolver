"""Optional early retrieval of an out-of-budget, corroborated MIA chapter.

Use only an independently verified root route and a uniquely localized
citation in its explicitly linked French document. No catalog child hints,
synthetic URLs or acceptance. Restore the original request ceiling even
on failure; reserve part of the remaining budget for ordinary retrieval.
"""
from bibliography import VERIFIED_AUTHORS
from navigation import author_check,canonical_mia,error_kind
from refinement import select_source_hit
from resolver import rank,sha
from window_rank import rank_short_windows


def eligible_roots(nav):
    urls=nav.get('input',{}).get('mia_urls',[])
    if len(urls)!=1 or canonical_mia(urls[0])!=urls[0] or not urls[0].startswith('https://www.marxists.org/francais/'):
        return None
    if nav.get('route_method')!='local_wikirouge_snapshot' or nav.get('author_check') not in VERIFIED_AUTHORS:
        return None
    meta=nav.get('work_metadata',{})
    fr,en=meta.get('fr',{}),meta.get('en',{})
    if author_check(fr.get('authors',''),en.get('authors','')) not in VERIFIED_AUTHORS:
        return None
    f,e=fr.get('mia_urls',[]),en.get('mia_urls',[])
    if len(f)!=1 or len(e)!=1 or f[0]==urls[0]:
        return None
    if canonical_mia(f[0])!=f[0] or canonical_mia(e[0])!=e[0]:
        return None
    if not f[0].startswith('https://www.marxists.org/francais/') or not e[0].startswith('https://www.marxists.org/archive/'):
        return None
    return urls[0],f[0],e[0]


def try_early_toc(engine,row,quote,source):
    diagnostic={'method':'guarded_toc_route_v1','status':'not_applicable',
                'ordinary_fallback_available':True,'rights':'unreviewed','auto_publish':False}
    roots=eligible_roots(row.get('navigation',{}))
    if roots is None: return None,diagnostic
    url,fr_root,en_root=roots
    fr,hit=select_source_hit(source,list(engine.source_documents.values()),url)
    if fr is None:
        return None,{**diagnostic,'status':'source_not_uniquely_supported'}
    c=engine.client;matcher=engine.toc_matcher
    old=c.budget;before=c.requests
    diagnostic.update(fr_root=fr_root,en_root=en_root,source_url=url,
                      source_sha256=fr.digest,source_evidence_kind=hit['evidence_kind'])
    if old-before<9:
        return None,{**diagnostic,'status':'fallback_budget_reserved'}
    # A failed speculative route cannot use the entire case allowance.
    c.budget=min(old-8,before+4)
    try:
        library=getattr(engine,'work_library',None)
        matcher_id=None
        saved=None
        if library is not None:
            try:
                matcher_id=sha(('guarded_chapter_v1|'+type(matcher).__name__+'|'+matcher.encoder.model_id).encode())
                saved=library.chapter(fr_root,en_root,url,matcher_id,fr.digest)
                if saved is not None:
                    # Reuse a chapter hypothesis only when both TOC bodies and
                    # both chapter bodies still match exactly. The quotation
                    # itself is independently localized above on every call.
                    current=[matcher.toc(fr_root,'fr')[1],matcher.toc(en_root,'en')[1]]
                    if current!=saved['toc_receipts']:
                        saved=None
                    else:
                        doc=matcher.document(saved['pair']['en']['url'])
                        if doc.digest!=saved['document_hashes']['en'] or canonical_mia(doc.url)!=saved['pair']['en']['url']:
                            saved=None
                library.stats['chapter_hits' if saved is not None else 'chapter_misses']+=1
            except Exception as exc:
                diagnostic['work_library_error']=error_kind(exc)
                saved=None
        m=({'pairs':[saved['pair']],'fr_toc':saved['toc_receipts'][0],
            'en_toc':saved['toc_receipts'][1],'matcher_variant':'persisted_revalidated_chapter'}
           if saved is not None else matcher.match_for_source(fr_root,en_root,url))
        pairs=[p for p in m['pairs'] if p['fr']['url']==url]
        diagnostic.update(toc_receipts=[m['fr_toc'],m['en_toc']],supported_pairs=len(m['pairs']),
                          matcher_variant=m.get('matcher_variant','original'))
        if m.get('structure_error'):
            diagnostic['structure_error']=m['structure_error']
        if len(pairs)!=1:
            diagnostic['status']='no_unique_supported_chapter';return None,diagnostic
        pair=pairs[0];diagnostic['pair']=pair
        ordinary_pos=pair['en'].get('ordinary_position',pair['en']['position'])
        # V2's explicit file order is not the old collector's traversal order.
        # Missing ordinary position means the ordinary TOC parser omitted it.
        if ordinary_pos is not None and ordinary_pos<engine.max_pages-1:
            diagnostic['status']='within_ordinary_document_budget';return None,diagnostic
        probe=({'status':'sampled_content_support','scope':'previous_probe_same_document_and_toc_hashes',
                'fr_document_sha256':saved['document_hashes']['fr'],
                'en_document_sha256':saved['document_hashes']['en'],'auto_publish':False}
               if saved is not None else matcher.content_probe(pair))
        diagnostic['content_probe']=probe
        if probe['status']!='sampled_content_support' or probe['fr_document_sha256']!=fr.digest:
            diagnostic['status']='chapter_content_not_corroborated';return None,diagnostic
        en=matcher.document(pair['en']['url'])
        if en.digest!=probe['en_document_sha256'] or canonical_mia(en.url)!=pair['en']['url']:
            diagnostic['status']='target_document_changed';return None,diagnostic
        if library is not None and matcher_id is not None and saved is None:
            try:
                library.remember_chapter(fr_root,en_root,pair,diagnostic['toc_receipts'],probe,matcher_id)
            except Exception as exc:
                diagnostic['work_library_error']=error_kind(exc)
        lexical=rank(quote,[en])
        if not lexical: lexical=rank_short_windows(quote,[en])['candidates']
        aligned=engine.direct_cache.candidate(source,list(engine.source_documents.values()),[en],url,
                                              'corroborated_toc_document_hypothesis')
        # Work on a separate row until all checks succeed; do not leave
        # speculative candidates in the ordinary fallback result.
        candidate={**row,'target_provider':'mia','target_scope':'corroborated_single_toc_chapter',
                   'target_attempts':[{'provider':'mia','root':en.url,'scope':'corroborated_single_toc_chapter',
                                       'issues':[],'pages':[{'url':en.url,'sha256':en.digest,
                                                            'revision':en.revision,'blocks':len(en.blocks)}]}],
                   'candidates':lexical,'section_alignment':aligned,
                   'status':'toc_candidate_review_required'}
        candidate=engine.semantic_sidecar(candidate,quote,[en])
        if not candidate.get('semantic_experiment',{}).get('candidates'):
            diagnostic['status']='semantic_candidate_unavailable';return None,diagnostic
        if engine.hybrid and not candidate.get('hybrid_review_queue',{}).get('candidates'):
            diagnostic['status']='hybrid_queue_unavailable';return None,diagnostic
        diagnostic['status']='early_chapter_candidate'
        candidate['toc_route_experiment']=diagnostic
        return candidate,diagnostic
    except Exception as exc:
        diagnostic.update(status='early_route_unavailable',error=error_kind(exc))
        return None,diagnostic
    finally:
        c.budget=old
        diagnostic['network_requests']=c.requests-before
