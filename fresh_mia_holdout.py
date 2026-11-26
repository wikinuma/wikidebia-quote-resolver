"""Fresh explicit-MIA holdout, excluding BOTH previously reviewed cohorts.

Selection is frozen before retrieval. Exact quote/template fingerprints are
excluded; known work labels, exact MIA URLs and Wikirouge work-root candidates
are used to prioritize novelty. No semantic deduplication claim.
"""
from collections import Counter
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time

from auto_retrieve import Retrieval
from benchmark_index import known_examples, scan_mia_pages, pinned_record, one_case, aggregate, write
from bibliography import load_catalog
from chapter_routes import load_chapters
from evidence_audit import Auditor, read_span
from inventory import link_pages,work_key
from navigation import canonical_mia, generic_work_name, error_kind
from pilot_full import PilotClient
from resolver import ROOT,norm,sha
from review_export import select_primary
from review_memory import load_effective_memory,check_hash
from revision_receipt import RevisionRefresher
from wikirouge_index import DEFAULT,LIMIT
from wikirouge_incremental import open_current,HISTORY

INVENTORY_SCHEMA='wikidebia-fresh-mia-inventory-1'
REPORT_SCHEMA='wikidebia-fresh-mia-holdout-1'
CATALOG=ROOT/'bibliographic-catalog.json'
CHAPTERS=ROOT/'chapter-catalog.json'
MEMORY=ROOT/'data/mia'
MAX_REVIEW_CHARACTERS=15000


def identity(meta,index):
    labels={norm(meta[k]) for k in ('work','article') if meta.get(k)
            and not generic_work_name(meta[k]) and norm(meta[k])}
    urls=set(meta.get('mia_urls',[]))
    if not urls or any(canonical_mia(u)!=u for u in urls):
        raise ValueError('non_explicit_mia_case')
    ids=index.ids_for_urls(urls)
    for key in ('work','article'):
        value=meta.get(key,'')
        if value and not generic_work_name(value):
            ids |= index.ids_for_title('fr',value)
    # Ambiguous title/URL matches are retained as possible overlap signals.
    roots=set()
    if len(ids)<=LIMIT:
        for pid in ids:
            page=index.page('fr',pid)
            root=page['work_root'] or page['title']
            roots.add(norm(root))
    work=work_key(meta)
    group=('root:'+next(iter(roots)) if len(roots)==1 else
           'label:'+norm(meta.get('authors',''))+':'+work if work else
           'url:'+sorted(urls)[0])
    return {'labels':labels,'urls':urls,'roots':roots,'group':group,
            'uncertain':not bool(labels and norm(meta.get('authors',''))) or len(ids)>LIMIT or len(roots)>1}


def select(cases,memory,index,catalog,chapters,legacy,sample=50):
    known_pages,known_labels,known_ids=map(set,legacy)
    quote_hashes=set();template_hashes=set();urls=set();roots=set()
    for c in memory['cases']:
        ref=c['citation_ref']
        quote_hashes.add(ref['quote_sha256']);template_hashes.add(ref['template_sha256'])
        known_ids.add(c['id']);known_pages.add(norm(c['wiki_title']))
        f=identity(c['metadata'],index)
        known_labels.update(f['labels']);urls.update(f['urls']);roots.update(f['roots'])
    for e in catalog:
        for label in (e['source_title'],*e.get('source_titles',[])):
            meta={'work':label,'authors':e['authors'],
                  'mia_urls':[e['source_url']]+([e['source_document']] if e.get('source_document') else [])}
            f=identity(meta,index)
            known_labels.update(f['labels']);urls.update(f['urls']);roots.update(f['roots'])
    for e in chapters:
        f=identity({'work':'','article':e['fr_heading'],'authors':e['authors'],
                    'mia_urls':[e['fr_toc'],e['fr_url']]},index)
        urls.update(f['urls']);roots.update(f['roots'])
        # Chapter headings alone are not work labels.
    unique={};excluded=Counter();pool=[]
    for c in sorted(cases,key=lambda c:sha(c['id'].encode())):
        ref=c['citation_ref']
        for key in ('quote_sha256','template_sha256'): check_hash(ref[key])
        if c['stratum']!='explicit_mia': raise ValueError('non_mia_inventory')
        if c['id'] in known_ids or ref['quote_sha256'] in quote_hashes or ref['template_sha256'] in template_hashes:
            excluded['previous_case_or_fingerprint']+=1;continue
        if not c['metadata']['quote_words']: continue
        f=identity(c['metadata'],index)
        overlap={'work_label':bool(f['labels']&known_labels),
                 'source_url':bool(f['urls']&urls),
                 'wikirouge_root':bool(f['roots']&roots),
                 'wiki_page':norm(c['wiki_title']) in known_pages,
                 'identity_uncertain':f['uncertain']}
        group='known_work_signal' if any(overlap[k] for k in ('work_label','source_url','wikirouge_root')) else (
            'uncertain_work_identity' if f['uncertain'] else 'no_known_work_signal')
        candidate={**c,'holdout_overlap':overlap,'holdout_group':group,'selection_group':f['group']}
        priority={'no_known_work_signal':0,'uncertain_work_identity':1,'known_work_signal':2}
        q=ref['quote_sha256']
        old=unique.get(q)
        if old is None or priority[group]<priority[old['holdout_group']]:
            unique[q]=candidate
    remaining=list(unique.values())
    counts=Counter(c['holdout_group'] for c in remaining)
    authors=Counter();works=Counter();picked=[]
    seen_templates=set()
    while remaining and len(picked)<sample:
        c=min(remaining,key=lambda c:(priority[c['holdout_group']],works[c['selection_group']],
              authors[norm(c['metadata']['authors'])],sha(c['id'].encode())))
        remaining.remove(c)
        if c['citation_ref']['template_sha256'] in seen_templates: continue
        picked.append(c);seen_templates.add(c['citation_ref']['template_sha256'])
        works[c['selection_group']]+=1;authors[norm(c['metadata']['authors'])]+=1
    return picked,{'previous_quotes_excluded':len(quote_hashes),'previous_templates_excluded':len(template_hashes),
                   'excluded_occurrences':dict(excluded),'eligible_unique_quotes':len(unique),
                   'eligible_by_novelty':dict(counts),'selected':len(picked),
                   'selected_by_novelty':dict(Counter(c['holdout_group'] for c in picked)),
                   'selected_overlap':{k:sum(c['holdout_overlap'][k] for c in picked)
                                       for k in ('work_label','source_url','wikirouge_root','wiki_page','identity_uncertain')},
                   'distinct_work_groups':len(works),'distinct_author_labels':len(authors),
                   'scope':'exact fingerprint exclusion; novelty by observable metadata signals, not proof of disjoint works or random sampling'}


def settings(index,memory,catalog,chapters):
    return {'base_snapshot_sha256':index.base_fingerprint,'index_chain_sha256':index.fingerprint,
            'memory_sha256':sha(json.dumps(memory,sort_keys=True,ensure_ascii=True,separators=(',',':')).encode()),
            'catalog_sha256':sha(CATALOG.read_bytes()),'chapter_catalog_sha256':sha(CHAPTERS.read_bytes())}


def discover(client,index,memory,catalog,chapters,sample,scan_pages):
    start=time.monotonic()
    linked,queries=link_pages(client,rows_per_query=200,max_batches=100)
    rows,scan=scan_mia_pages(client,linked,scan_pages)
    by_id={}
    for r in rows:
        c={**pinned_record(r),'stratum':'explicit_mia'}
        if c['id'] in by_id and c!=by_id[c['id']]:
            raise ValueError('conflicting_inventory_identity')
        by_id[c['id']]=c
    cases=sorted(by_id.values(),key=lambda c:c['id'])
    chosen,selection=select(cases,memory,index,catalog,chapters,known_examples(DEFAULT),sample)
    issues=bool(scan['issues'] or scan['page_cap_reached'] or any(q.get('error') or (q['batch']==100 and q.get('more')) for q in queries))
    return {'schema':INVENTORY_SCHEMA,'scope':'explicit_mia_only','status':'bounded_partial_inventory' if issues else 'inventory_complete_within_api_scope',
            'inputs':settings(index,memory,catalog,chapters),'requested_sample':sample,
            'discovery':{**scan,'queries':queries,'raw_mia_occurrences':len(rows),'distinct_pinned_occurrences':len(cases),
                         'network_requests':client.requests,'decoded_network_body_bytes':client.network_body_bytes,
                         'elapsed_seconds':round(time.monotonic()-start,3),'model_calls':0},
            'selection':selection,'cases':cases,'sample':chosen,'auto_publish':False}


def recheck_inventory(inv,index,memory,catalog,chapters,sample):
    if (inv['schema']!=INVENTORY_SCHEMA or inv['scope']!='explicit_mia_only' or inv['auto_publish'] is not False
            or inv['inputs']!=settings(index,memory,catalog,chapters) or inv['requested_sample']!=sample):
        raise ValueError('inventory_input_mismatch')
    selected,diag=select(inv['cases'],memory,index,catalog,chapters,known_examples(DEFAULT),sample)
    if selected!=inv['sample'] or diag!=inv['selection'] or len(selected)!=sample:
        raise ValueError('sample_not_reproducible_or_incomplete')
    return selected


def review_primary(case,result,audit,refresher):
    target,origin=select_primary(result)
    record={'id':case['id'],'status':'no_primary_candidate','origin':origin,
            'rights':'unreviewed','bilingual_equivalence':'unreviewed','boundary_verified':False,'auto_publish':False}
    if target is None: return record,None
    record['status']='review_unavailable'
    try:
        inp,quote=audit.input(case)
        record['input']=inp
        if inp['status']!='verified_pinned_citation' or sha(quote.encode())!=case['citation_ref']['quote_sha256']:
            raise ValueError('pinned_input_not_verified')
        source=result.get('source',{})
        spans=source.get('occurrences',[])+source.get('variants',[])
        record['source_status']=source.get('status')
        record['source_span_count']=len(spans)
        record['source_receipts']=[audit.span(s,'fr') for s in spans[:4]]
        record['source_audit_truncated']=len(spans)>4
        receipt=audit.span(target,'en')
        record['target']=receipt
        doc=audit.document(target['url'],'en')
        if isinstance(doc,dict): raise ValueError('target_unavailable')
        if receipt['status']=='span_matches_changed_document' and type(target.get('revision')) is int:
            refresh=refresher.refresh(doc,target)
            record['revision_refresh']=refresh
            if refresh['status']=='verified_same_revision_span':
                receipt=refresh['fresh_receipt'];record['target']=receipt
        if receipt['status']!='verified_document_and_span':
            raise ValueError('target_span_not_verified')
        record['documentary_authenticity']='verified_document_and_span'
        lo,hi,text=read_span(doc,target)
        if sha(text.encode())!=target['text_sha256']:
            raise ValueError('target_hash_mismatch')
        if max(len(text),len(quote))>MAX_REVIEW_CHARACTERS:
            raise ValueError('review_packet_length_limit')
        record.update(status='ready',english_review_words=len(norm(text).split()),
                      french_review_words=len(norm(quote).split()),
                      publication_metadata=audit.publication.get(('en',target['url'])))
        return record,{'id':case['id'],'citation_ref':case['citation_ref'],'metadata':case['metadata'],
                       'fr_quote':quote,'en_candidate':text,'receipt':record}
    except Exception as exc:
        record['error']=error_kind(exc)
        return record,None


def retrieve(cases,index,inv,inv_sha,catalog,chapters,cache,out,certificate=None,packet_path=None):
    start=time.monotonic();pairs=[];private=[]
    client=PilotClient(cache/'retrieval',budget=len(cases)*32)
    engine=Retrieval(client,16,catalog=catalog,local_index=index,refined=True,chapter_catalog=chapters)
    audit_client=PilotClient(cache/'audit',budget=len(cases)*10)
    audit=Auditor(audit_client);refresher=RevisionRefresher(audit_client)
    report={'schema':REPORT_SCHEMA,'scope':'explicit_mia_only','status':'running',
            'inputs':inv['inputs'],'inventory_sha256':inv_sha,'sample':cases,'selection':inv['selection'],
            'discovery':{k:v for k,v in inv['discovery'].items() if k!='queries'},
            'bounds':{'case_requests':32,'max_work_pages':16,'source_receipts_per_candidate':4},
            'pairs':pairs,'auto_publish':False,'rights':'unreviewed'}
    write(out,report)
    # Group work to exploit one in-run work/document/alignment cache.
    for c in sorted(cases,key=lambda c:(c['selection_group'],c['id'])):
        pair={'id':c['id'],'holdout_group':c['holdout_group'],'refined':one_case(engine,c,32)}
        before=(audit_client.requests,audit_client.network_body_bytes,time.monotonic())
        old_budget=audit_client.budget
        audit_client.budget=min(old_budget,audit_client.requests+10)
        try:
            record,packet=review_primary(c,pair['refined']['result'],audit,refresher)
        finally:
            audit_client.budget=old_budget
        record['audit_metrics']={'network_requests':audit_client.requests-before[0],
                                'decoded_network_body_bytes':audit_client.network_body_bytes-before[1],
                                'elapsed_seconds':round(time.monotonic()-before[2],4)}
        pair['audit']=record;pairs.append(pair)
        if packet is not None: private.append(packet)
        report['aggregate']=aggregate(pairs,('refined',))
        report['audit_summary']={'statuses':dict(Counter(p['audit']['status'] for p in pairs)),
                                  'authenticated_primary':sum(p['audit'].get('documentary_authenticity')=='verified_document_and_span' for p in pairs),
                                  'english_words_for_review':sum(p['audit'].get('english_review_words',0) for p in pairs),
                                  'french_words_for_review':sum(p['audit'].get('french_review_words',0) for p in pairs),
                                  'network_requests':audit_client.requests,'decoded_network_body_bytes':audit_client.network_body_bytes,
                                  'elapsed_seconds':round(sum(p['audit']['audit_metrics']['elapsed_seconds'] for p in pairs),4),
                                  'semantic_equivalence':None,'false_positives':None,'rights_approved':0}
        write(out,report)
        print(json.dumps({'completed':len(pairs),'selected':len(cases),'case_id':c['id'],
                          'retrieval':pair['refined']['result']['status'],'audit':record['status'],
                          'metrics':pair['refined']['metrics']}),flush=True)
    bad=[p['id'] for p in pairs if p['refined']['result']['status']=='execution_error'
         or p['refined']['result'].get('navigation',{}).get('status')=='input_unavailable']
    report.update(status='candidate_experiment_complete' if not bad else 'candidate_experiment_incomplete',
                  execution_or_input_failures=bad,elapsed_seconds=round(time.monotonic()-start,3),
                  alignment_cache={'hits':engine.direct_cache.hits,'computations':engine.direct_cache.computations},
                  model_calls=0,actual_ai_review_tokens=None,actual_ai_review_cost=None)
    write(out,report)
    print(json.dumps({k:report[k] for k in ('status','aggregate','audit_summary','elapsed_seconds','alignment_cache','execution_or_input_failures')},indent=2))
    print('report_sha256='+sha(out.read_bytes()),flush=True)
    if certificate:
        payload={'schema':'wikidebia-fresh-mia-private-review-1','report_sha256':sha(out.read_bytes()),
                 'inventory_sha256':inv_sha,'cases':private,'auto_publish':False,'rights':'unreviewed'}
        raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
        packet_path.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['openssl','cms','-encrypt','-binary','-aes-256-gcm','-outform','DER',
                        '-recip',str(certificate),'-out',str(packet_path)],input=raw,check=True)
        print(json.dumps({'review_cases':len(private),'packet_sha256':sha(raw),
                          'encrypted_sha256':sha(packet_path.read_bytes()),'auto_publish':False}),flush=True)
    return int(bool(bad))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('discover','retrieve'),required=True)
    p.add_argument('--sample',type=int,default=50)
    p.add_argument('--scan-pages',type=int,default=1200)
    p.add_argument('--inventory',type=Path,required=True)
    p.add_argument('--inventory-sha',type=Path)
    p.add_argument('--out',type=Path,default=ROOT/'reports-live/fresh-mia-holdout.json')
    p.add_argument('--certificate',type=Path)
    p.add_argument('--packet',type=Path)
    a=p.parse_args()
    if not 50<=a.sample<=80 or not 1<=a.scan_pages<=3000 or bool(a.certificate)!=bool(a.packet):
        p.error('invalid bounds or private packet options')
    memory=load_effective_memory(MEMORY);catalog=load_catalog(CATALOG);chapters=load_chapters(CHAPTERS)
    with open_current() as index,tempfile.TemporaryDirectory(prefix='fresh-mia-') as tmp:
        cache=Path(tmp)
        if a.stage=='discover':
            if a.inventory.exists(): raise FileExistsError(a.inventory)
            inv=discover(PilotClient(cache/'discover',budget=400+(a.scan_pages+9)//10+50),index,memory,catalog,chapters,a.sample,a.scan_pages)
            write(a.inventory,inv)
            digest=sha(a.inventory.read_bytes())
            if a.inventory_sha:
                a.inventory_sha.write_text(digest+'\n')
            print(json.dumps({'selection':inv['selection'],'discovery':{k:v for k,v in inv['discovery'].items() if k!='queries'},
                              'inventory_status':inv['status'],'inventory_sha256':digest},indent=2),flush=True)
            return int(len(inv['sample'])!=a.sample)
        if a.inventory_sha is None: p.error('retrieval requires the frozen inventory digest')
        raw=a.inventory.read_bytes()
        if sha(raw)!=a.inventory_sha.read_text().strip(): raise ValueError('inventory_hash_mismatch')
        inv=json.loads(raw);cases=recheck_inventory(inv,index,memory,catalog,chapters,a.sample)
        return retrieve(cases,index,inv,sha(raw),catalog,chapters,cache,a.out,a.certificate,a.packet)

if __name__=='__main__':
    raise SystemExit(main())
