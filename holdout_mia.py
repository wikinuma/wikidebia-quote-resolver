"""Select fresh MIA-linked quotations without reusing the frozen development cases.

Quote/template fingerprints are excluded. New pages, work labels and exact
source URLs are preferred; remaining overlap is disclosed, never silently
labelled work-independent.
"""
from collections import Counter
import json
from pathlib import Path

from benchmark_index import scan_mia_pages,select_mia,known_examples
from inventory import link_pages,work_key
from resolver import norm


def select_holdout(rows,baseline,known_titles,known_works,known_ids,catalog,sample):
    quote_hashes={r['citation_ref']['quote_sha256'] for r in baseline}
    template_hashes={r['citation_ref']['template_sha256'] for r in baseline}
    frozen_ids={r['id'] for r in baseline}
    urls={u for r in baseline for u in r['metadata']['mia_urls']}
    urls.update(e['source_url'] for e in catalog)
    works=set(known_works)
    works.update(work_key(r['metadata']) for r in baseline)
    works.update(norm(t) for e in catalog for t in [e['source_title'],*e.get('source_titles',[])])
    pages=set(known_titles)|{norm(r['wiki_title']) for r in baseline}
    pool=[r for r in rows if r['quote_sha256'] not in quote_hashes and r['template_sha256'] not in template_hashes]
    # A row on a known source URL receives the same lower priority as one
    # from a known title/work. This is conservative if a page cites several
    # distinct works.
    priority_pages=pages|{norm(r['wiki_title']) for r in pool if urls.intersection(r['mia_urls'])}
    selected,diag=select_mia(pool,sample,priority_pages,works,known_ids|frozen_ids)
    # select_mia may include previously known IDs to fill a development
    # sample. Exact frozen IDs are forbidden here, not simply deprioritized.
    selected=[r for r in selected if r['id'] not in frozen_ids and
              r['citation_ref']['quote_sha256'] not in quote_hashes and
              r['citation_ref']['template_sha256'] not in template_hashes]
    for c in selected:
        flags={'known_page':norm(c['wiki_title']) in pages,
               'known_work_label':work_key(c['metadata']) in works,
               'known_source_url':bool(urls.intersection(c['metadata']['mia_urls'])),
               'known_development_id':c['id'] in known_ids}
        c['holdout_overlap']=flags
        c['holdout_group']='unseen_page_work_url' if not any(flags.values()) else 'familiar_document_or_page'
    return selected,{**diag,'selected':len(selected),'excluded_frozen_quote_fingerprints':len(quote_hashes),
                     'excluded_frozen_template_fingerprints':len(template_hashes),
                     'raw_records_after_frozen_exclusion':len(pool),
                     'by_novelty':dict(Counter(c['holdout_group'] for c in selected)),
                     'known_page_overlap':sum(c['holdout_overlap']['known_page'] for c in selected),
                     'known_work_overlap':sum(c['holdout_overlap']['known_work_label'] for c in selected),
                     'known_url_overlap':sum(c['holdout_overlap']['known_source_url'] for c in selected),
                     'scope':'unseen exact quotation and template fingerprints relative to frozen cohort; known work/page/URL overlap disclosed; not a random census or semantic deduplication'}


def discover_holdout(client,manifest,baseline,catalog,sample=50,scan_pages=1200):
    titles,works,ids=known_examples(manifest)
    linked,queries=link_pages(client,rows_per_query=50,max_batches=100)
    rows,scan=scan_mia_pages(client,linked,scan_pages)
    selected,diag=select_holdout(rows,baseline,titles,works,ids,catalog,sample)
    return selected,{**diag,'inventory':{**scan,'queries':queries,'raw_mia_citations':len(rows)}}
