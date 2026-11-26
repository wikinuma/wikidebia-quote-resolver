"""Re-score fixed historical candidate lists. No fetching or inference."""
import argparse,json
from pathlib import Path
from rank_fusion import interleave
from resolver import sha
PIN='7d61af8ef16cab83a0e97cc589ca7822f117784d2240b76fd3604a6c83896927'

def summarize(rows):
    return {'cases':len(rows),'top1_iou80':sum(bool(r and r[0]['iou80']) for r in rows),
            'top5_iou80':sum(any(c['iou80'] for c in r) for r in rows),
            'top5_full_cover':sum(any(c['full_cover'] for c in r) for r in rows),
            'top5_exact_single_fragment':sum(any(c['exact_single_fragment'] for c in r) for r in rows),
            'top5_review_words':sum(c['words'] for r in rows for c in r)}

def evaluate(path):
    raw=path.read_bytes()
    if sha(raw)!=PIN:raise ValueError('historical_report_hash_mismatch')
    report=json.loads(raw)
    if report['schema']!='wikidebia-semantic-conditional-1' or report['auto_publish'] is not False:
        raise ValueError('unexpected_report')
    cases=[r for r in report['cases'] if r['status']=='evaluated']
    if len(cases)!=34:raise ValueError('unexpected_evaluated_count')
    lexical=[r['lexical']['top5'] for r in cases];semantic=[r['pooled']['top5'] for r in cases]
    fused=[interleave(s,l,5) for s,l in zip(semantic,lexical)]
    return {'schema':'wikidebia-rank-fusion-benchmark-1','source_sha256':PIN,
            'selected_historical_cases':38,'revalidated':34,
            'summary':{'lexical':summarize(lexical),'semantic':summarize(semantic),'interleaved':summarize(fused)},
            'scope':'Retrospective known-document evaluation of fixed candidate lists; heuristic selected after seeing earlier aggregate results, not independent gold',
            'source_requests':0,'local_model_calls':0,'hosted_model_calls':0,'rights_approved':0,'auto_publish':False}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();r=evaluate(a.report);a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(r,indent=2,sort_keys=True)+'\n')
    print(json.dumps(r,indent=2));print('report_sha256='+sha(a.out.read_bytes()))
if __name__=='__main__':main()
