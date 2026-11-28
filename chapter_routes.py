"""Reviewed chapter routes, keyed by an actual French source occurrence.

A chapter identity narrows the search, but is not proof of an exact quotation.
Catalog entries are reusable document mappings, never case-specific answers.
"""
from __future__ import annotations
import json
from pathlib import Path
from navigation import canonical_mia, author_check
from bibliography import VERIFIED_AUTHORS
from refinement import select_source_hit, select_hit_metadata
from resolver import rank
from window_rank import rank_short_windows


def chapter_urls(route):
    """Ordered, explicitly documented English leaves; never synthesize URLs."""
    return tuple(route.get('en_urls', [route['en_url']]))


def load_chapters(path):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('schema')!='wikidebia-chapter-catalog-1':
        raise ValueError('invalid_chapter_catalog_schema')
    entries=data['entries']; seen=set()
    for e in entries:
        if any(not e.get(k) or canonical_mia(e[k])!=e[k] for k in ('fr_url','en_url','fr_toc','en_toc')):
            raise ValueError('invalid_chapter_url')
        if not all('/francais/' in e[k] for k in ('fr_url','fr_toc')) or any('/francais/' in e[k] for k in ('en_url','en_toc')):
            raise ValueError('invalid_chapter_language')
        if e['fr_url'] in seen or e['fr_url']==e['fr_toc'] or e['en_url']==e['en_toc']:
            raise ValueError('ambiguous_or_nonleaf_chapter')
        if not all(isinstance(e.get(k),str) and e[k].strip() for k in ('authors','fr_heading','en_heading','scope')):
            raise ValueError('missing_chapter_evidence')
        if 'en_urls' in e:
            leaves = e['en_urls']
            if (not isinstance(leaves,list) or not 2 <= len(leaves) <= 4
                    or any(not isinstance(u,str) or not u or canonical_mia(u)!=u
                           or '/francais/' in u or u==e['en_toc'] for u in leaves)
                    or leaves[0]!=e['en_url'] or len(set(leaves))!=len(leaves)):
                raise ValueError('invalid_split_chapter_urls')
        seen.add(e['fr_url'])
    return tuple(entries)


def chapter_route(metadata,source,entries):
    """Use a documented chapter only with a unique, bounded source hit.
    A near-exact source variant can identify a document, not an exact quote.
    Distinct normalized alternatives or truncated evidence cause abstention.
    """
    links=metadata.get('mia_urls',[])
    if len(links)!=1:
        return None
    matches=[]
    for entry in entries:
        if links[0] not in (entry['fr_url'],entry['fr_toc']):
            continue
        if author_check(metadata.get('authors',''),entry['authors']) not in VERIFIED_AUTHORS:
            continue
        hit,kind=select_hit_metadata(source,entry['fr_url'])
        if hit is not None:
            matches.append(entry)
    return matches[0] if len(matches)==1 else None


def retrieve_chapter(quote,source,fr_docs,target_doc,route,alignment_cache):
    """Search only the documented leaves. Split editions keep independent
    documents and offsets; a whole-chapter alignment against one part is invalid."""
    fr,hit=select_source_hit(source,fr_docs,route['fr_url'])
    docs=list(target_doc) if isinstance(target_doc,(list,tuple)) else [target_doc]
    urls=chapter_urls(route)
    if (fr is None or tuple(d.url for d in docs)!=urls
            or any(not d.blocks for d in docs)):
        raise ValueError('chapter_document_or_source_mismatch')
    if len(docs)==1:
        aligned=alignment_cache.candidate(source,fr_docs,docs,fr.url,'reviewed_chapter_mapping')
        candidates=rank(quote,docs)
        if not candidates:
            candidates=rank_short_windows(quote,docs)['candidates']
        scope='reviewed_single_chapter'
    else:
        # The French chapter spans multiple English leaves. Rank each leaf
        # independently and include its first available candidate before the
        # remaining candidates, so no part can monopolize a small wide review.
        # No artificial concatenation or cross-page passage is created.
        aligned={'status':'split_chapter_alignment_not_applicable','candidates':[]}
        queues=[]
        for doc in docs:
            local=rank(quote,[doc])
            if not local:
                local=rank_short_windows(quote,[doc])['candidates']
            if local:
                queues.append(local)
        first=[q[0] for q in queues]
        remaining=[c for q in queues for c in q[1:]]
        candidates=sorted(first,key=lambda c:(-c['score'],c['url']))+sorted(
            remaining,key=lambda c:(-c['score'],c['url']))
        candidates=candidates[:5]
        scope='reviewed_split_chapter'
    return {'chapter_route':route,'target_provider':'mia','target_scope':scope,
            'target_attempts':[{'provider':'mia','root':doc.url,'scope':scope,
                                'issues':[],'pages':[{'url':doc.url,'sha256':doc.digest,'blocks':len(doc.blocks)}]}
                               for doc in docs],
            'section_alignment':aligned,'candidates':candidates,
            'status':'chapter_candidate_review_required' if aligned['candidates'] or candidates else 'chapter_no_candidate',
            'auto_publish':False,'rights':'unreviewed'}
