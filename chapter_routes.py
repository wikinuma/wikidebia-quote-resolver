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
    """Retrieve only inside an independently identified chapter, preserving
    abstention on missing or changed source coordinates."""
    fr,hit=select_source_hit(source,fr_docs,route['fr_url'])
    if fr is None or target_doc.url!=route['en_url'] or not target_doc.blocks:
        raise ValueError('chapter_document_or_source_mismatch')
    aligned=alignment_cache.candidate(source,fr_docs,[target_doc],fr.url,'reviewed_chapter_mapping')
    candidates=rank(quote,[target_doc])
    if not candidates:
        candidates=rank_short_windows(quote,[target_doc])['candidates']
    return {'chapter_route':route,'target_provider':'mia','target_scope':'reviewed_single_chapter',
            'target_attempts':[{'provider':'mia','root':target_doc.url,'scope':'reviewed_single_chapter',
                                'issues':[],'pages':[{'url':target_doc.url,'sha256':target_doc.digest,'blocks':len(target_doc.blocks)}]}],
            'section_alignment':aligned,'candidates':candidates,
            'status':'chapter_candidate_review_required' if aligned['candidates'] or candidates else 'chapter_no_candidate',
            'auto_publish':False,'rights':'unreviewed'}
