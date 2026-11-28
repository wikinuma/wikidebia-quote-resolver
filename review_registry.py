"""Revalidate hash-bound bilingual review decisions against the frozen cohorts.

No text in public output. Exact substrings are reviewer-selected, not
generated translations. Rights and editorial formatting remain unapproved.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import tempfile
from evidence_audit import Auditor,load_report
from review_export import materialize,select_primary
from pilot_full import PilotClient
from resolver import ROOT,sha
from benchmark_index import write

ACCEPT={'complete_equivalent','boundary_adjustment','omission_adjustment'}
DECISIONS=ACCEPT|{'transcription_issue','edition_variant','partial','wrong_passage'}


def verify_fragments(text,decision):
    fragments=decision['fragments']
    if decision['decision'] not in DECISIONS or bool(fragments)!=(decision['decision'] in ACCEPT):
        raise ValueError('invalid_review_decision')
    if decision['decision']=='complete_equivalent' and (len(fragments)!=1 or fragments[0][:2]!=[0,len(text)]):
        raise ValueError('complete_decision_must_cover_entire_candidate')
    last=0; out=[]
    for lo,hi,digest in fragments:
        if type(lo) is not int or type(hi) is not int or not 0<=last<=lo<hi<=len(text):
            raise ValueError('invalid_fragment_coordinates')
        if sha(text[lo:hi].encode())!=digest:
            raise ValueError('reviewed_fragment_hash_mismatch')
        out.append({'relative_start':lo,'relative_end':hi,'text_sha256':digest})
        last=hi
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--development',type=Path,required=True)
    p.add_argument('--holdout',type=Path,required=True)
    p.add_argument('--chapter',type=Path,required=True)
    p.add_argument('--review',type=Path,default=ROOT/'first-candidate-review.json')
    p.add_argument('--out',type=Path,default=ROOT/'reports-live/review-registry.json')
    a=p.parse_args()
    rev=json.loads(a.review.read_text(encoding='utf-8'))
    if rev['schema']!='wikidebia-first-candidate-review-1' or rev.get('auto_publish') is not False:
        raise ValueError('review_schema_mismatch')
    chapters=a.chapter.read_bytes()
    if sha(chapters)!=rev['chapter_report_sha256']:
        raise ValueError('chapter_report_sha256_mismatch')
    chapter=json.loads(chapters)
    if chapter['reports']!=rev['reports']:
        raise ValueError('chapter_report_scope_mismatch')
    corrections={r['id']:r for r in chapter['cases']}
    decisions={r['case_id']:r for r in rev['entries']}
    if len(decisions)!=70 or len(decisions)!=len(rev['entries']):
        raise ValueError('review_count_mismatch')
    reports=[('development',load_report(a.development,rev['reports'][0],'refined')),
             ('holdout',load_report(a.holdout,rev['reports'][1],'refined'))]
    rows=[]; seen=set()
    with tempfile.TemporaryDirectory(prefix='review-registry-') as tmp:
        client=PilotClient(Path(tmp),budget=300); auditor=Auditor(client)
        for group,report in reports:
            for case,pair in zip(report['sample'],report['pairs']):
                cid=case['id']; result=pair['refined']['result']
                if cid in seen: raise ValueError('duplicate_case_id')
                seen.add(cid)
                correction=corrections.get(cid)
                target,origin=select_primary(result,correction)
                row={'case_id':cid,'cohort':group,'citation_ref':case['citation_ref'],
                     'rights':'unreviewed','auto_publish':False}
                if target is None:
                    if cid in decisions: raise ValueError('review_without_candidate')
                    row['status']='no_primary_candidate'; rows.append(row); continue
                if cid not in decisions: raise ValueError('candidate_without_review')
                record=materialize(auditor,case,result,correction)
                if record['status']!='ready':
                    row.update(status='review_evidence_unavailable',error=record.get('error'))
                else:
                    decision=decisions[cid]
                    fragments=verify_fragments(record['en_candidate'],decision)
                    receipt=record['en_receipt']
                    row.update(status='review_revalidated',decision=decision['decision'],note=decision['note'],
                               candidate_kind=record['candidate_kind'],origin=origin,
                               target_url=receipt['url'],document_sha256=receipt['document_sha256'],
                               target_text_sha256=receipt['actual_text_sha256'],target_revision=receipt['revision'],
                               char_start=receipt['char_start'],char_end=receipt['char_end'],
                               fragments=[{**f,'char_start':receipt['char_start']+f['relative_start'],
                                           'char_end':receipt['char_start']+f['relative_end']} for f in fragments],
                               editorial_formatting_unreviewed=True)
                rows.append(row)
        if set(decisions)-seen or len(rows)!=100: raise ValueError('incomplete_review_cohort')
        counts=dict(Counter(r.get('decision',r['status']) for r in rows))
        kinds={k:dict(Counter(r['decision'] for r in rows if r.get('candidate_kind')==k))
               for k in ('aligned','lexical')}
        summary={'inputs':len(rows),'decisions':counts,'by_candidate_kind':kinds,
                 'reviewed_fragments_revalidated':sum(bool(r.get('fragments')) for r in rows),
                 'fragment_count':sum(len(r.get('fragments',[])) for r in rows),
                 'requests':client.requests,'reuse_rights_approved':0}
        write(a.out,{'schema':'wikidebia-revalidated-review-registry-1','review_sha256':sha(a.review.read_bytes()),
                     'reports':rev['reports'],'chapter_report_sha256':rev['chapter_report_sha256'],
                     'summary':summary,'cases':rows,'auto_publish':False})
        print(json.dumps(summary,indent=2),flush=True)
        return int(counts.get('review_evidence_unavailable',0)>0)

if __name__=='__main__':
    raise SystemExit(main())
