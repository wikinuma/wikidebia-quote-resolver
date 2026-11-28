"""Audit mapped chapters on two frozen MIA cohorts, independently re-read
their actual public source/target documents, and replay reviewed quotations.
Only candidates in the verified chapter are offered by the targeted path.
"""
import argparse
import json
from pathlib import Path
import tempfile
from collections import Counter
from evidence_audit import Auditor, load_report, read_span
from chapter_routes import load_chapters, chapter_route, chapter_urls, retrieve_chapter
from refinement import DirectAlignmentCache, review_packet, select_source_hit, select_hit_metadata
from resolver import ROOT, norm
from pilot_full import PilotClient, redact_text
from benchmark_index import write
from navigation import error_kind


def primary(row):
    return (row.get('review_packet',{}).get('candidates') or
            row.get('section_alignment',{}).get('candidates') or row.get('candidates',[]))[:1]


def review_receipt(case,result,decision,auditor):
    """A reviewed record is never accepted by title or case ID alone.
    Semantic judgment is explicit and separate from document integrity."""
    ref=case['citation_ref']
    if (case['id']!=decision['case_id'] or ref['revision']!=decision['wiki_revision'] or
        ref['quote_sha256']!=decision['quote_sha256'] or
        decision.get('decision')!='equivalent_complete_passage'):
        return {'status':'review_input_mismatch'}
    inp,quote=auditor.input(case)
    if inp['status']!='verified_pinned_citation':
        return {'status':'review_input_unavailable'}
    src=[s for s in result.get('source',{}).get('occurrences',[]) if s.get('url')==decision['fr_url']
         and s.get('text_sha256')==decision['fr_span_sha256']]
    targets=primary(result)
    if len(src)!=1 or len(targets)!=1 or targets[0].get('url')!=decision['en_url'] or targets[0].get('text_sha256')!=decision['en_span_sha256']:
        return {'status':'review_evidence_mismatch'}
    fr=auditor.span(src[0],'fr'); en=auditor.span(targets[0],'en')
    if fr['status']!='verified_document_and_span' or en['status']!='verified_document_and_span':
        return {'status':'review_evidence_changed','fr':fr,'en':en}
    doc=auditor.document(src[0]['url'],'fr')
    _,_,text=read_span(doc,src[0])
    if norm(text)!=norm(quote):
        return {'status':'review_source_quote_mismatch'}
    return {'status':'reviewed_exact_equivalence_revalidated','decision':decision,'fr':fr,'en':en,
            'rights':'unreviewed','auto_publish':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--development',type=Path,required=True)
    p.add_argument('--holdout',type=Path,required=True)
    p.add_argument('--development-sha',required=True)
    p.add_argument('--holdout-sha',required=True)
    p.add_argument('--out',type=Path,default=ROOT/'reports-live/chapter-quality.json')
    args=p.parse_args()
    cohorts=[('development',load_report(args.development,args.development_sha,'refined')),
             ('holdout',load_report(args.holdout,args.holdout_sha,'refined'))]
    entries=load_chapters(ROOT/'chapter-catalog.json')
    reviews=json.loads((ROOT/'reviewed-quotations.json').read_text(encoding='utf-8'))
    if reviews.get('schema')!='wikidebia-reviewed-quotation-1':
        raise ValueError('invalid_review_schema')
    decisions={e['case_id']:e for e in reviews['entries']}
    rows=[]; receipts=[]
    with tempfile.TemporaryDirectory(prefix='chapter-audit-') as tmp:
        client=PilotClient(Path(tmp),budget=150); auditor=Auditor(client); align=DirectAlignmentCache()
        for group,data in cohorts:
            for c,pair in zip(data['sample'],data['pairs']):
                result=pair['refined']['result']
                if c['id'] in decisions:
                    receipts.append({'id':c['id'],**review_receipt(c,result,decisions[c['id']],auditor)})
                src=result.get('source',{})
                route=chapter_route(c['metadata'],src,entries)
                if route is None: continue
                row={'cohort':group,'id':c['id'],'chapter':route,'status':'pending','old_primary':primary(result),
                     'old_wrong_chapter':bool(primary(result) and primary(result)[0]['url'] not in chapter_urls(route))}
                rows.append(row)
                try:
                    inp,quote=auditor.input(c)
                    if inp['status']!='verified_pinned_citation': raise ValueError('input_unavailable')
                    source,kind=select_hit_metadata(src,route['fr_url'])
                    if source is None: raise ValueError('source_not_selected')
                    fr=auditor.document(route['fr_url'],'fr')
                    en=[auditor.document(url,'en') for url in chapter_urls(route)]
                    if isinstance(fr,dict) or any(isinstance(d,dict) for d in en): raise ValueError('document_unavailable')
                    _,_,actual=read_span(fr,source)
                    if auditor.span(source,'fr')['status']!='verified_document_and_span':
                        raise ValueError('source_fingerprint_mismatch')
                    if kind=='exact_normalized_source' and norm(actual)!=norm(quote):
                        raise ValueError('source_quote_mismatch')
                    # Keep all competing metadata; restore only the authenticated selected span.
                    key='occurrences' if kind=='exact_normalized_source' else 'variants'
                    current={**src,key:[{**h,'text':actual} if h is source else h for h in src[key]]}
                    if select_source_hit(current,[fr],route['fr_url'])[0] is None:
                        raise ValueError('source_variant_evidence_mismatch')
                    row['source_evidence_kind']=kind
                    updated=retrieve_chapter(quote,current,[fr],en,route,align)
                    updated['review_packet']=review_packet(updated)
                    row.update(status=updated['status'],new=redact_text(updated))
                    row['new_primary_in_chapter']=bool(primary(row['new']) and primary(row['new'])[0]['url'] in chapter_urls(route))
                    if c['id'] in decisions:
                        row['reviewed_span_retained_as_primary']=bool(primary(row['new']) and
                            primary(row['new'])[0]['text_sha256']==decisions[c['id']]['en_span_sha256'])
                except Exception as exc:
                    row.update(status='chapter_audit_error',error=error_kind(exc))
                print(json.dumps({'id':c['id'],'status':row['status'],'old_wrong_chapter':row['old_wrong_chapter'],
                                  'new_primary_in_chapter':row.get('new_primary_in_chapter',False)}),flush=True)
        summary={'mapped_cases':len(rows),'old_primary_outside_reviewed_chapter':sum(r['old_wrong_chapter'] for r in rows),
                 'new_primary_in_reviewed_chapter':sum(r.get('new_primary_in_chapter',False) for r in rows),
                 'wrong_chapter_cases_with_new_in_chapter_candidate':sum(r['old_wrong_chapter'] and r.get('new_primary_in_chapter',False) for r in rows),
                 'statuses':dict(Counter(r['status'] for r in rows)),
                 'explicit_reviewed_cases':len(receipts),
                 'reviewed_equivalences_revalidated':sum(r['status']=='reviewed_exact_equivalence_revalidated' for r in receipts),
                 'semantic_accuracy_of_other_cases':None,'reuse_rights_approved':0,'requests':client.requests,
                 'alignment_cache_hits':align.hits}
        report={'schema':'wikidebia-chapter-quality-1','scope':'targeted chapter correction on two development/familiar-work cohorts',
                'reports':[args.development_sha,args.holdout_sha],'summary':summary,'cases':rows,'review_receipts':receipts,
                'model_calls_during_run':0,'auto_publish':False}
        write(args.out,report); print(json.dumps(summary,indent=2),flush=True)
        return int(any(r['status']=='chapter_audit_error' for r in rows) or any(r['status']!='reviewed_exact_equivalence_revalidated' for r in receipts) or not receipts)


if __name__=='__main__':
    raise SystemExit(main())
