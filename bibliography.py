"""Reusable, explicitly researched bibliographic links, not automatic discovery.

Catalog entries identify documents, never quotations. Exact source URL,
normalized title and author are required. No lookup against benchmark answers.
No article text is stored. All rights remain unapproved.
"""
from __future__ import annotations
import json
from pathlib import Path
from navigation import canonical_mia,author_check,generic_work_name
from resolver import norm
from pilot_full import wr_title

SCHEMA='wikidebia-bibliographic-catalog-1'
VERIFIED_AUTHORS={'same_normalized_author_tokens','same_documented_author_alias'}


def load_catalog(path):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('schema')!=SCHEMA: raise ValueError('unsupported_bibliographic_catalog')
    entries=data['entries']; seen=set()
    for row in entries:
        source=canonical_mia(row['source_url'])
        if source is None or source!=row['source_url'] or not source.startswith('https://www.marxists.org/francais/'):
            raise ValueError('invalid_catalog_source')
        if source in seen: raise ValueError('duplicate_catalog_source')
        seen.add(source)
        if not norm(row['source_title']) or not norm(row['authors']) or not row.get('evidence') or not row.get('rights_note'):
            raise ValueError('incomplete_catalog_evidence')
        provider=row.get('target_provider')
        target=row.get('target_url')
        if provider=='mia':
            if canonical_mia(target)!=target or not target or target.startswith('https://www.marxists.org/francais/'):
                raise ValueError('invalid_catalog_mia_target')
        elif provider=='wikirouge':
            if not isinstance(target,str) or not target.startswith('https://wikirouge.net/texts/en/') or not wr_title(target,'en'):
                raise ValueError('invalid_catalog_wikirouge_target')
        elif provider=='bibliography_only':
            if target is not None: raise ValueError('bibliography_only_has_text_target')
        else: raise ValueError('invalid_catalog_provider')
        specific=row.get('source_document')
        if specific is not None and (canonical_mia(specific)!=specific or not specific.startswith('https://www.marxists.org/francais/') or specific not in row['evidence']):
            raise ValueError('invalid_catalog_source_document')
        if row.get('whole_document_parallel') and provider=='bibliography_only':
            raise ValueError('invalid_catalog_alignment_scope')
    return tuple(entries)


def match_catalog(metadata,entries):
    urls=metadata.get('mia_urls',[])
    if len(urls)!=1: return None
    titles={norm(metadata.get(k,'')) for k in ('work','article') if metadata.get(k) and not generic_work_name(metadata[k])}
    matches=[r for r in entries if r['source_url']==canonical_mia(urls[0])
             and norm(r['source_title']) in titles
             and author_check(metadata.get('authors',''),r['authors']) in VERIFIED_AUTHORS]
    return matches[0] if len(matches)==1 else None


def catalog_route(metadata,entries):
    entry=match_catalog(metadata,entries)
    if entry is None: return None
    return {'method':'researched_bibliographic_catalog','entry':entry,
            'author_check':author_check(metadata.get('authors',''),entry['authors']),
            'source_url_exact':True,'title_match':True,
            'auto_publish':False,'rights':'unreviewed'}
