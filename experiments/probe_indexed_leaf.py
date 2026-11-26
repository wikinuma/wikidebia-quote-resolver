"""Offline route opportunity assessment on the exact frozen benchmark.

This is not a recovery or quality score. No source downloads or model calls.
The previous candidate URL is used only after generating a new hypothesis.
"""
import json
from collections import Counter
from pathlib import Path
from indexed_leaf import indexed_leaf_route
from wikirouge_incremental import open_current
from resolver import ROOT,sha
from review_export import select_primary

BASE=ROOT/'data/mia/benchmarks/20261126/report.json'
PIN='04b035052345d0ce86b45d52a20344fcd6a4e9ab1af0a1258109011687715e86'


def main():
    raw=BASE.read_bytes()
    if sha(raw)!=PIN: raise ValueError('baseline_hash_mismatch')
    report=json.loads(raw)
    by_id={r['id']:r for r in report['sample']}
    rows=[]
    with open_current() as index:
        for pair in report['pairs']:
            cid=pair['id'];result=pair['refined']['result'];meta=by_id[cid]['metadata']
            if result.get('chapter_route') or result.get('bibliographic_route'):
                rows.append({'id':cid,'status':'existing_catalog_route_not_replaced'})
                continue
            route=indexed_leaf_route(index,meta,result.get('source',{}))
            primary,_=select_primary(result)
            r=route['route']
            rows.append({'id':cid,'status':route['status'],'route':r,'attempts':route['attempts'],
                         'previous_primary_url':primary.get('url') if primary else None,
                         'same_document_url':bool(r and primary and primary['url'] in (r['en_mia_url'],r['en_wr_url']))})
    summary={'cases':len(rows),'statuses':dict(Counter(r['status'] for r in rows)),
             'candidate_routes':sum(r.get('route') is not None for r in rows),
             'different_primary_document':sum(r.get('route') is not None and r.get('previous_primary_url') is not None
                                             and not r['same_document_url'] for r in rows),
             'no_previous_primary':sum(r.get('route') is not None and r.get('previous_primary_url') is None for r in rows),
             'network_requests':0,'model_calls':0,'semantic_equivalences':None}
    out={'schema':'wikidebia-indexed-leaf-opportunities-1','baseline_sha256':PIN,'summary':summary,'cases':rows,'auto_publish':False}
    path=ROOT/'reports-live/indexed-leaf-probe.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out,ensure_ascii=True,sort_keys=True,indent=2)+'\n')
    print(json.dumps(summary,indent=2))
    for row in rows:
        if row.get('route'):
            print('LEAF='+json.dumps(row,ensure_ascii=True,separators=(',',':')))
    return 0

if __name__=='__main__': raise SystemExit(main())
