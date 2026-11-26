"""Evaluate automatic bilingual TOC mapping, with held-out known chapter IDs.

Known catalog root pairs delimit the search only; their expected child URL
is compared after all heading matches have been produced. Novel roots
come solely from the current Wikirouge index and explicit MIA metadata.
No quotation-level success or semantic approval is inferred.
"""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import time
from bibliography import VERIFIED_AUTHORS
from chapter_routes import load_chapters
from embedding import LocalEncoder
from pilot_full import PilotClient
from resolver import ROOT,sha
from review_memory import load_effective_memory
from toc_heading import TocMatcher
from wikirouge_incremental import open_current

PIN='06de13853f1d18b3c16a011a22849efa4cbee3f9b56ba493faf157a31654b286'
BASE=ROOT/'data/mia/benchmarks/20261126/inventory.json'


def novel_sources():
    raw=BASE.read_bytes()
    if sha(raw)!=PIN: raise ValueError('inventory_pin_mismatch')
    inv=json.loads(raw);previous=load_effective_memory(ROOT/'data/mia')['cases']+inv['sample']
    known={u for c in previous for u in c['metadata']['mia_urls']}
    groups=defaultdict(list)
    for case in inv['cases']:
        if case['stratum']!='explicit_mia': raise ValueError('non_mia_inventory')
        urls=case['metadata']['mia_urls']
        if set(urls)&known: continue
        for u in urls: groups[u].append(case)
    return dict(groups)


def roots(index,cases):
    candidates=set()
    for c in cases:
        _,route=index.route(c['metadata'])
        if not route or route.get('author_check') not in VERIFIED_AUTHORS: continue
        f=route['work_metadata']['fr']['mia_urls'];e=route['work_metadata']['en']['mia_urls']
        if len(f)==len(e)==1:
            candidates.add((f[0],e[0]))
    return next(iter(candidates)) if len(candidates)==1 else None


def propose(matcher,root_pair,source_url):
    if root_pair is None:
        return {'status':'no_unique_indexed_root_pair'}
    try:
        r=matcher.match(*root_pair)
        rows=[p for p in r['pairs'] if p['fr']['url']==source_url]
        base={'fr_root':root_pair[0],'en_root':root_pair[1],'fr_count':r['fr_count'],
              'en_count':r['en_count'],'anchors':r['anchors'],'supported_pairs':len(r['pairs']),
              'toc_receipts':[r['fr_toc'],r['en_toc']]}
        if len(rows)!=1: return {**base,'status':'no_unique_supported_chapter'}
        pair=rows[0]
        try: probe=matcher.content_probe(pair)
        except Exception as exc: probe={'status':'document_probe_unavailable','error':type(exc).__name__}
        if pair.get('requires_content_probe') and probe['status']!='sampled_content_support':
            return {**base,'status':'uncorroborated_bracketed_hypothesis','hypothesis':pair,'content_probe':probe}
        pos=pair['en'].get('ordinary_position',pair['en']['position'])
        return {**base,'status':'candidate','pair':pair,'content_probe':probe,
                'outside_first_15_toc_links':pos is None or pos>=15}
    except Exception as exc:
        return {'status':'toc_unavailable','error':type(exc).__name__}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model-dir',type=Path,required=True)
    p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--out',type=Path,default=ROOT/'reports-live/toc-heading-benchmark.json')
    p.add_argument('--structure-v2',action='store_true')
    a=p.parse_args();a.cache.mkdir(parents=True,exist_ok=True);a.out.parent.mkdir(parents=True,exist_ok=True)
    client=PilotClient(a.cache/'http',budget=100)
    enc=LocalEncoder(a.model_dir,a.cache/'vectors.sqlite3')
    if a.structure_v2:
        from toc_structure import StructureMatcher
        matcher=StructureMatcher(client,enc)
    else:matcher=TocMatcher(client,enc)
    started=time.monotonic()
    try:
        labelled=[]
        for c in load_chapters(ROOT/'chapter-catalog.json'):
            result=propose(matcher,(c['fr_toc'],c['en_toc']),c['fr_url'])
            # Expected URL enters only evaluation, never the matcher.
            predicted=result.get('pair',{}).get('en',{}).get('url')
            labelled.append({'source_url':c['fr_url'],'expected_url':c['en_url'],**result,
                             'evaluation':'correct' if predicted==c['en_url'] else 'wrong' if predicted else 'abstained'})
        new=[]
        with open_current() as index:
            for u,cases in sorted(novel_sources().items()):
                new.append({'source_url':u,'inventory_occurrences':len(cases),
                            **propose(matcher,roots(index,cases),u)})
        counts=Counter(r['evaluation'] for r in labelled)
        summary={'labelled':len(labelled),'labelled_outcomes':dict(counts),
                 'labelled_content_supported_correct':sum(r['evaluation']=='correct' and r.get('content_probe',{}).get('status')=='sampled_content_support' for r in labelled),
                 'labelled_content_supported_wrong':sum(r['evaluation']=='wrong' and r.get('content_probe',{}).get('status')=='sampled_content_support' for r in labelled),
                 'novel_urls':len(new),'novel_statuses':dict(Counter(r['status'] for r in new)),
                 'novel_content_supported':sum(r.get('status')=='candidate' and r.get('content_probe',{}).get('status')=='sampled_content_support' for r in new),
                 'structure_v2':a.structure_v2,
                 'bracketed_candidates':sum(r.get('pair',{}).get('method')=='equal_contiguous_bracketed_gap' for r in labelled+new),
                 'novel_outside_first_15':sum(r.get('outside_first_15_toc_links',False) for r in new),
                 'network_requests':client.requests,'decoded_network_body_bytes':client.network_body_bytes,
                 'elapsed_seconds':round(time.monotonic()-started,3),'encoder_stats':enc.stats,
                 'hosted_model_calls':0,'semantic_equivalence':None,'rights_approved':0}
        report={'schema':'wikidebia-toc-heading-benchmark-1','status':'complete','model_id':enc.model_id,
                'labelled':labelled,'novel':new,'summary':summary,'auto_publish':False}
        a.out.write_text(json.dumps(report,ensure_ascii=True,indent=2,sort_keys=True)+'\n')
        print(json.dumps(summary,indent=2))
        for r in labelled+new:
            print('TOC_RESULT='+json.dumps({'source_url':r['source_url'],'target':r.get('pair',{}).get('en',{}).get('url'),
                  'status':r['status'],'evaluation':r.get('evaluation'),'content_probe':r.get('content_probe',{}).get('status'),
                  'outside_first_15':r.get('outside_first_15_toc_links')},ensure_ascii=True))
        print('report_sha256='+sha(a.out.read_bytes()))
        return 0
    finally: enc.close()

if __name__=='__main__':raise SystemExit(main())
