"""Prepare bounded private bilingual review packets from pinned public sources.

Plaintext is held in memory only. A CI artifact is encrypted to the reviewer's
public certificate; the private key is never supplied to GitHub. This is a
transport mechanism for editorial review, not a translation generator.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import tempfile
from evidence_audit import Auditor, load_report, read_span
from chapter_quality import primary
from pilot_full import PilotClient
from resolver import ROOT, sha
from navigation import error_kind


def select_primary(result, correction=None):
    if correction and correction.get('new_primary_in_chapter') is True:
        candidates=primary(correction['new'])
        if candidates:
            return candidates[0],'reviewed_chapter'
    candidates=primary(result)
    return (candidates[0],'refined') if candidates else (None,None)


def materialize(auditor,case,result,correction=None):
    target,origin=select_primary(result,correction)
    record={'id':case['id'],'origin':origin,'status':'no_primary_candidate',
            'metadata':case['metadata'],'citation_ref':case['citation_ref']}
    if not target:
        return record
    record['status']='review_unavailable'
    try:
        i,quote=auditor.input(case)
        if i['status']!='verified_pinned_citation' or sha(quote.encode())!=case['citation_ref']['quote_sha256']:
            raise ValueError('pinned_input_not_verified')
        receipt=auditor.span(target,'en')
        if receipt['status']!='verified_document_and_span':
            raise ValueError('target_span_not_verified')
        doc=auditor.document(target['url'],'en')
        lo,hi,text=read_span(doc,target)
        if hi-lo>15000 or len(quote)>15000:
            raise ValueError('review_packet_length_limit')
        record.update(status='ready',fr_quote=quote,en_candidate=text,
                      en_receipt=receipt,
                      source_status=result.get('source',{}).get('status'),
                      candidate_kind=('aligned' if target.get('links') else 'lexical'))
    except Exception as exc:
        record['error']=error_kind(exc)
    return record


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--development',type=Path,required=True)
    p.add_argument('--holdout',type=Path,required=True)
    p.add_argument('--development-sha',required=True)
    p.add_argument('--holdout-sha',required=True)
    p.add_argument('--chapter-report',type=Path)
    p.add_argument('--chapter-sha')
    p.add_argument('--certificate',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    datasets=[('development',load_report(args.development,args.development_sha,'refined')),
              ('holdout',load_report(args.holdout,args.holdout_sha,'refined'))]
    corrections={}
    if args.chapter_report:
        data=args.chapter_report.read_bytes()
        if sha(data)!=args.chapter_sha:
            raise ValueError('chapter_report_digest_mismatch')
        chapter=json.loads(data)
        if chapter.get('schema')!='wikidebia-chapter-quality-1' or chapter.get('reports')!=[args.development_sha,args.holdout_sha]:
            raise ValueError('chapter_report_scope_mismatch')
        corrections={r['id']:r for r in chapter['cases']}
    with tempfile.TemporaryDirectory(prefix='private-review-cache-') as tmp:
        client=PilotClient(Path(tmp),budget=300)
        auditor=Auditor(client)
        rows=[]
        for group,data in datasets:
            for case,pair in zip(data['sample'],data['pairs']):
                record=materialize(auditor,case,pair['refined']['result'],corrections.get(case['id']))
                rows.append({'cohort':group,**record})
        summary=dict(Counter(row['status'] for row in rows))
        packet={'schema':'wikidebia-private-review-packet-1',
                'reports':[args.development_sha,args.holdout_sha],
                'chapter_report_sha256':args.chapter_sha,
                'cases':rows,'summary':summary,'requests':client.requests,
                'rights':'unreviewed','auto_publish':False}
        args.out.parent.mkdir(parents=True,exist_ok=True)
        # Authenticated AES-GCM CMS envelope; public X.509 recipient only.
        # Neither plaintext excerpts nor the symmetric content key are logged.
        subprocess.run(['openssl','cms','-encrypt','-binary','-aes-256-gcm',
                        '-outform','DER','-recip',str(args.certificate),'-out',str(args.out)],
                       input=json.dumps(packet,ensure_ascii=False,separators=(',',':')).encode(),check=True)
        print(json.dumps({'cases':len(rows),'summary':summary,'requests':client.requests,
                          'encrypted_bytes':args.out.stat().st_size,'auto_publish':False}),flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
