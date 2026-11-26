"""Materialize only the already-selected first-pass candidates for paired review.
No new search, alternate candidates, plaintext reports, or acceptance.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import tempfile
import time
from evidence_audit import Auditor,read_span,span_receipt
from pilot_full import PilotClient
from resolver import sha
from revision_receipt import RevisionRefresher

PIN='55d890bc4e7bcc92aa8f3ce198c7f5963706f11175d1f9010b5e9603e3c2ce3c'
ARMS=('baseline','integrated')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--certificate',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();raw=a.report.read_bytes()
    if sha(raw)!=PIN: raise ValueError('report_hash_mismatch')
    r=json.loads(raw)
    if (r['schema']!='wikidebia-independent-mia-comparison-1' or r['status']!='complete'
        or r['auto_publish'] is not False or len(r['sample'])!=50 or len(r['pairs'])!=50):
        raise ValueError('report_scope_mismatch')
    by={c['id']:c for c in r['sample']}
    if len(by)!=50 or {p['id'] for p in r['pairs']}!=set(by): raise ValueError('cohort_mismatch')
    packet=[];counts=Counter();start=time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        c=PilotClient(Path(tmp),budget=180)
        audit=Auditor(c);refresh=RevisionRefresher(c)
        for i,pair in enumerate(r['pairs']):
            case=by[pair['id']]
            if not all(pair[arm+'_audit']['status']=='ready' for arm in ARMS):
                counts['neither_or_one_ready']+=1
                continue
            # Randomized-looking, deterministic arm order; review only texts.
            order=ARMS if int(sha(('holdout-review-v1:'+case['id']).encode())[-1],16)%2 else ARMS[::-1]
            out={'id':case['id'],'review_number':i+1,'quote_sha256':case['citation_ref']['quote_sha256']}
            try:
                receipt,quote=audit.input(case)
                if receipt['status']!='verified_pinned_citation' or sha(quote.encode())!=out['quote_sha256']:
                    raise ValueError('input_unavailable')
                if len(quote)>15000: raise ValueError('quote_too_long')
                out['fr_quote']=quote
                for label,arm in zip(('X','Y'),order):
                    original=pair[arm+'_audit']['target']
                    entry={'url':original['url'],'sha256':original['document_sha256'],
                           'revision':original['revision'],'char_start':original['char_start'],
                           'char_end':original['char_end'],'text_sha256':original['actual_text_sha256']}
                    doc=audit.document(entry['url'],'en')
                    if isinstance(doc,dict): raise ValueError('target_unavailable')
                    proof=span_receipt(doc,entry)
                    if proof['status']!='verified_document_and_span':
                        restored=refresh.refresh(doc,entry)
                        if restored['status']!='verified_same_revision_span':
                            raise ValueError('target_not_revalidated')
                    lo,hi,text=read_span(doc,entry)
                    if sha(text.encode())!=entry['text_sha256'] or len(text)>15000:
                        raise ValueError('target_hash_or_length')
                    out[label]={'text':text,'sha256':entry['text_sha256']}
                packet.append(out);counts['paired_ready']+=1
            except Exception as exc:
                counts['review_unavailable']+=1
                print(json.dumps({'case_id':case['id'],'status':'review_unavailable','kind':type(exc).__name__}),flush=True)
        a.out.parent.mkdir(parents=True,exist_ok=True)
        data=json.dumps({'schema':'wikidebia-independent-mia-blind-review-1','report_sha256':PIN,
                         'cases':packet,'counts':dict(counts),'rights':'unreviewed','auto_publish':False},
                        ensure_ascii=False,separators=(',',':')).encode()
        subprocess.run(['openssl','cms','-encrypt','-binary','-aes-256-gcm','-outform','DER',
                        '-recip',str(a.certificate),'-out',str(a.out)],input=data,check=True)
        print(json.dumps({'counts':dict(counts),'packet_sha256':sha(data),
                          'encrypted_sha256':sha(a.out.read_bytes()),'requests':c.requests,
                          'decoded_bytes':c.network_body_bytes,'elapsed_seconds':round(time.monotonic()-start,3),
                          'hosted_model_calls':0,'auto_publish':False},indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
