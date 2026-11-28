"""Real MIA document-pair alignment benchmark. No quotation or text export.

Choose pairs from the existing, pre-reviewed chapter catalog, not from quote
answers. Compare uncached and reopened SQLite calculations on identical
documents. This isolates DP work, not retrieval accuracy or model quota.
"""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from alignment import aligned_candidates, segments
from auto_retrieve import mia_doc, explicit_toc
from chapter_routes import load_chapters
from pilot_full import PilotClient
from refinement import DirectAlignmentCache
from resolver import ROOT, sha
from work_library import WorkLibrary


def measure(cache, fr, en, quotes):
    start = perf_counter()
    results = [aligned_candidates(q,fr,en,'sentence',alignment_cache=cache) for q in quotes]
    return results, round(perf_counter()-start,6)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=ROOT/'reports-live/alignment-store.json')
    p.add_argument('--pairs',type=int,default=3)
    a=p.parse_args()
    if not 1<=a.pairs<=5: p.error('pairs must be 1..5')
    catalog=load_chapters(ROOT/'chapter-catalog.json')
    rows=[]
    with TemporaryDirectory(prefix='alignment-store-') as tmp:
        client=PilotClient(Path(tmp)/'http',budget=2*a.pairs+8)
        db=Path(tmp)/'works.sqlite3'
        for route in catalog[:a.pairs]:
            try:
                fr,fb=mia_doc(client,route['fr_url'])
                en,eb=mia_doc(client,route['en_url'])
                if fr.url!=route['fr_url'] or en.url!=route['en_url'] or explicit_toc(fb,fr.url) or explicit_toc(eb,en.url):
                    raise ValueError('unexpected_document')
                # Actual French sentences as distinct test selectors. This is
                # not a benchmark of real Wikidebats citation equivalence.
                from alignment import exact_occurrences
                qs=[]
                for s in segments(fr,'sentence'):
                    if len(s.text.split())>=8 and s.text not in qs:
                        hits,cut=exact_occurrences(s.text,fr)
                        if len(hits)==1 and not cut: qs.append(s.text)
                    if len(qs)==2: break
                if len(qs)!=2:
                    raise ValueError('insufficient_unique_test_selectors')
                cold=DirectAlignmentCache()
                expected,base_time=measure(cold,fr,en,qs)
                with WorkLibrary(db) as lib:
                    first=DirectAlignmentCache(store=lib.alignment_store)
                    actual,first_time=measure(first,fr,en,qs[:1])
                    assert actual==expected[:1]
                with WorkLibrary(db) as lib:
                    warm=DirectAlignmentCache(store=lib.alignment_store)
                    actual,warm_time=measure(warm,fr,en,qs[1:])
                    equal=actual==expected[1:]
                    disk_hits=lib.alignment_store.stats['alignment_disk_hits']
                    disk_writes=lib.alignment_store.stats['alignment_disk_writes']
                rows.append({'status':'ok' if equal and disk_hits==1 and warm.computations==0 else 'not_reused',
                    'fr_url':fr.url,'en_url':en.url,'selectors':2,
                    'baseline_computations':cold.computations,'first_computations':first.computations,
                    'reopened_computations':warm.computations,'reopened_disk_hits':disk_hits,
                    'reopened_disk_writes':disk_writes,'results_identical':equal,
                    'alignment_cells':expected[0].get('alignment',{}).get('cells'),
                    'baseline_seconds_two_selectors':base_time,'first_seconds':first_time,
                    'reopened_seconds':warm_time})
            except Exception as exc:
                rows.append({'status':'error','fr_url':route['fr_url'],'en_url':route['en_url'],
                             'error':type(exc).__name__})
    report={'schema':'wikidebia-alignment-store-benchmark-1',
            'scope':'real document pairs; distinct unique source sentences, not semantic gold citations',
            'rows':rows,'wiki_or_archive_requests':client.requests,
            'hosted_model_calls':0,'auto_publish':False}
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
    return int(not all(r['status']=='ok' for r in rows))


if __name__=='__main__':
    raise SystemExit(main())
