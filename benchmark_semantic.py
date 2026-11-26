"""Evaluate local semantic passage ranking conditionally on the known document.

Historical review labels only determine evaluation targets and the common
document scope. Neither method receives accepted offsets, fragments, English
hints or text. This is NOT end-to-end bibliographic retrieval or a human gold set.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import tempfile
import time

from alignment import text_and_starts
from embedding import LocalEncoder,candidate_windows,semantic_rank
from evidence_audit import Auditor,span_receipt
from pilot_full import PilotClient
from resolver import ROOT,norm,rank,sha
from benchmark_fixtures import load_semantic_cases
from revision_receipt import RevisionRefresher
from sentence_index import SentenceVectorIndex
from window_rank import rank_short_windows

def label(row):
    return row['label']


def revalidate(doc,p,refresher):
    entry={'url':p['url'],'sha256':p['document_sha256'],'revision':p['revision'],
           'char_start':p['char_start'],'char_end':p['char_end'],'text_sha256':p['candidate_sha256']}
    r=span_receipt(doc,entry)
    if r['status']=='span_matches_changed_document' and p['revision'] is not None:
        refresh=refresher.refresh(doc,entry)
        if refresh['status']!='verified_same_revision_span':
            return False
    elif r['status']!='verified_document_and_span':
        return False
    text,_=text_and_starts(doc)
    return all(0<=f['char_start']<f['char_end']<=len(text)
               and sha(text[f['char_start']:f['char_end']].encode())==f['text_sha256']
               for f in p['fragments'])


def lexical(quote,doc):
    text,starts=text_and_starts(doc)
    candidates=rank(quote,[doc])
    if not candidates:
        return rank_short_windows(quote,[doc])['candidates']
    out=[]
    for c in candidates:
        i=c['block'];j=i+c['span']-1
        lo=starts[i];hi=starts[j]+len(doc.blocks[j])
        out.append({**c,'char_start':lo,'char_end':hi,'text':text[lo:hi]})
    return out


def evaluate(candidates,fragments,doc):
    text,_=text_and_starts(doc)
    total=sum(f['char_end']-f['char_start'] for f in fragments)
    rows=[]
    for c in candidates:
        lo,hi=c['char_start'],c['char_end']
        if not 0<=lo<hi<=len(text) or c['url']!=doc.url or c['sha256']!=doc.digest or c['text']!=text[lo:hi]:
            raise ValueError('candidate_not_exact_source_span')
        overlap=sum(max(0,min(hi,f['char_end'])-max(lo,f['char_start'])) for f in fragments)
        iou=overlap/(total+hi-lo-overlap)
        rows.append({'url':doc.url,'char_start':lo,'char_end':hi,'text_sha256':sha(c['text'].encode()),
                     'words':len(norm(c['text']).split()),'coverage':round(overlap/total,6),
                     'precision':round(overlap/(hi-lo),6),'iou':round(iou,6),
                     'full_cover':overlap==total,'iou80':iou>=.8,
                     'exact_single_fragment':len(fragments)==1 and overlap==total==hi-lo})
    return {'top1':rows[0] if rows else None,'top5':rows[:5],
            'cover_rank':next((i+1 for i,r in enumerate(rows[:5]) if r['full_cover']),None),
            'iou80_rank':next((i+1 for i,r in enumerate(rows[:5]) if r['iou80']),None)}


def summarize(rows,arm):
    valid=[r[arm] for r in rows if arm in r]
    return {'evaluated':len(valid),'top1_full_cover':sum(bool(r['top1'] and r['top1']['full_cover']) for r in valid),
            'top5_full_cover':sum(r['cover_rank'] is not None for r in valid),
            'top1_iou80':sum(bool(r['top1'] and r['top1']['iou80']) for r in valid),
            'top5_iou80':sum(r['iou80_rank'] is not None for r in valid),
            'top1_exact_single_fragment':sum(bool(r['top1'] and r['top1']['exact_single_fragment']) for r in valid),
            'top1_words':sum(r['top1']['words'] if r['top1'] else 0 for r in valid),
            'mean_top1_iou':round(sum(r['top1']['iou'] if r['top1'] else 0 for r in valid)/max(1,len(valid)),6)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model-dir',type=Path,required=True)
    p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--out',type=Path,default=ROOT/'reports-live/semantic-conditional.json')
    p.add_argument('--max-exhaustive-windows',type=int,default=1200)
    a=p.parse_args()
    if not 0<=a.max_exhaustive_windows<=3000: p.error('invalid exhaustive budget')
    a.cache.mkdir(parents=True,exist_ok=True)
    a.out.parent.mkdir(parents=True,exist_ok=True)
    cases=load_semantic_cases()
    assert len(cases)==38
    cases.sort(key=lambda c:c['id'])
    rows=[];ready=[];start=time.monotonic()
    client=PilotClient(a.cache/'http',budget=180)
    audit=Auditor(client);refresh=RevisionRefresher(client)
    for c in cases:
        p=label(c);row={'id':c['id'],'status':'label_unavailable','target_url':p['url']}
        rows.append(row)
        try:
            receipt,quote=audit.input(c)
            if receipt['status']!='verified_pinned_citation' or sha(quote.encode())!=c['citation_ref']['quote_sha256']:
                raise ValueError('input_not_revalidated')
            doc=audit.document(p['url'],'en')
            if isinstance(doc,dict) or not revalidate(doc,p,refresh):
                raise ValueError('label_not_revalidated')
            row['status']='ready';ready.append((row,quote,doc,p['fragments']))
        except Exception as e:
            row['reason']=str(e) if str(e) in ('input_not_revalidated','label_not_revalidated') else type(e).__name__
        print(json.dumps({'case_id':c['id'],'status':row['status']}),flush=True)
    source_seconds=time.monotonic()-start
    # Separate caches ensure that baseline window embeddings cannot warm
    # the new sentence index. They are loaded only if the small subset exists.
    enc=LocalEncoder(a.model_dir,a.cache/'pooled.sqlite3')
    idx=SentenceVectorIndex(enc)
    old=None
    remaining=a.max_exhaustive_windows
    old_eligible=0
    timings=Counter();old_inputs=set();sentence_inputs=set()
    def write(status):
        report={'schema':'wikidebia-semantic-conditional-1','status':status,
                'scope':'38 historically accepted MIA-linked cases, known target document only; not independent human gold',
                'selected':len(cases),'revalidated':len(ready),'cases':rows,
                'model_id':enc.model_id,'summary':{arm:summarize(rows,arm) for arm in ('lexical','pooled','exhaustive','pooled_on_exhaustive_subset')},
                'source_requests':client.requests,'source_decoded_bytes':client.network_body_bytes,
                'source_seconds':round(source_seconds,3),'timings_seconds':{k:round(v,3) for k,v in timings.items()},
                'pooled_encoder_stats':enc.stats.copy(),'pooled_document_builds':idx.builds,'pooled_document_hits':idx.hits,
                'exhaustive_encoder_stats':old.stats.copy() if old else None,'exhaustive_budget_remaining':remaining,
                'exhaustive_window_inputs_unique_eligible':len(old_inputs),
                'sentence_inputs_unique_eligible':len(sentence_inputs),
                'exhaustive_count_eligible_cases':old_eligible,
                'local_inference':True,'hosted_model_calls':0,'rights_approved':0,'auto_publish':False}
        raw=json.dumps(report,ensure_ascii=True,sort_keys=True,indent=2)+'\n'
        a.out.write_text(raw)
        return report
    try:
        from alignment import segments
        for row,quote,doc,fragments in ready:
            t=time.monotonic()
            row['lexical']=evaluate(lexical(quote,doc),fragments,doc)
            timings['lexical']+=time.monotonic()-t
            t=time.monotonic();r=idx.rank(quote,[doc]);timings['pooled_cold']+=time.monotonic()-t
            row['pooled_status']=r['status'];row['pooled_windows']=r['window_count']
            row['pooled']=evaluate(r['candidates'],fragments,doc)
            windows,truncated=candidate_windows(quote,[doc],max_windows=6000)
            row['old_window_count']=len(windows) if not truncated else None
            if not truncated and windows:
                old_eligible+=1
                old_inputs.update(sha(c['text'].encode()) for c in windows)
                sentence_inputs.update(sha(s.text.encode()) for s in segments(doc,'sentence'))
            # Predeclared cost-only subset: at most 1200 actual old windows in
            # stable ID order, with no access to rankings/labels for selection.
            if not truncated and windows and len(windows)<=remaining:
                if old is None: old=LocalEncoder(a.model_dir,a.cache/'exhaustive.sqlite3')
                remaining-=len(windows);t=time.monotonic()
                r0=semantic_rank(quote,[doc],old)
                timings['exhaustive']+=time.monotonic()-t
                row['exhaustive']=evaluate(r0['candidates'],fragments,doc)
                row['pooled_on_exhaustive_subset']=row['pooled']
            row['status']='evaluated'
            write('running')
            print(json.dumps({'case_id':row['id'],'status':'evaluated','pooled_top1_iou':(row['pooled']['top1'] or {}).get('iou'),
                              'lexical_top1_iou':(row['lexical']['top1'] or {}).get('iou'),
                              'pooled_windows':row['pooled_windows']}),flush=True)
        # Rebuild in-memory index using the persistent SQLite vector cache.
        warm=SentenceVectorIndex(enc);before=enc.stats.copy()
        for row,quote,doc,fragments in ready:
            t=time.monotonic();r=warm.rank(quote,[doc]);timings['pooled_warm']+=time.monotonic()-t
            if evaluate(r['candidates'],fragments,doc)!=row['pooled']:
                raise ValueError('warm_cache_ranking_changed')
        report=write('complete')
        report['warm_encoder_delta']={k:enc.stats[k]-before[k] for k in before}
        report['elapsed_seconds']=round(time.monotonic()-start,3)
        a.out.write_text(json.dumps(report,ensure_ascii=True,sort_keys=True,indent=2)+'\n')
        print(json.dumps({k:v for k,v in report.items() if k!='cases'},indent=2))
        print('report_sha256='+sha(a.out.read_bytes()))
        return int(not ready)
    finally:
        enc.close()
        if old: old.close()

if __name__=='__main__':raise SystemExit(main())
