"""Targeted development comparison from verified prior reports.

Select the affected cases before retrieving anything. The new engine gets
only the pinned French input, never the expected English URL, prior
semantic decision, or prior passage. Unaffected cases are not rerun.
"""
import argparse,json,time,subprocess
from collections import Counter
from pathlib import Path
from auto_retrieve import Retrieval
from benchmark_index import one_case,write
from bibliography import load_catalog
from chapter_routes import load_chapters
from embedding import LocalEncoder
from evidence_audit import Auditor
from fresh_mia_holdout import review_primary
from novel_url_benchmark import urls,comparison
from pilot_full import PilotClient
from resolver import ROOT,norm,sha
from revision_receipt import RevisionRefresher
from sentence_index import SentenceVectorIndex
from toc_structure import AugmentedTocMatcher
from wikirouge_incremental import open_current

PINS={
    'integrated':'92d8d79ee43ec2ed5b5604e5175bbc3b8d9deb96fa206ea0720aae5ac941c7ca',
    'old_toc':'cbca2c457e033bf3af0a0cdeaa8a7b9dcbc3e84d5326c5e2b35489b040085e8b',
    'new_toc':'68b2e31ec93a1771dfd155fa3d3310d68f04d058c506f5a22be8b0846ea1f7ea',
}

def read(path,pin):
    raw=path.read_bytes()
    if sha(raw)!=pin:raise ValueError('input_hash_mismatch')
    r=json.loads(raw)
    if r.get('auto_publish') is not False:raise ValueError('unsafe_input')
    return r

def supported(report):
    out={}
    for r in report['novel']:
        if (r['status']=='candidate' and r.get('outside_first_15_toc_links')
                and r.get('content_probe',{}).get('status')=='sampled_content_support'):
            if r['source_url'] in out:raise ValueError('duplicate_source_url')
            out[r['source_url']]=r['pair']['en']['url']
    return out

def selected(integrated,old,new):
    a,b=supported(old),supported(new)
    if any(b.get(k)!=v for k,v in a.items()):raise ValueError('old_routes_not_preserved_in_probe')
    delta={k:v for k,v in b.items() if k not in a}
    if len(a)!=5 or len(b)!=8 or len(delta)!=3:raise ValueError('unexpected_route_cohort')
    pairs={p['id']:p for p in integrated['pairs']}
    if len(pairs)!=50 or len(integrated['sample'])!=50:raise ValueError('unexpected_citation_cohort')
    cases=[c for c in integrated['sample'] if len(urls(c))==1 and urls(c)[0] in delta]
    if len(cases)!=10:raise ValueError('unexpected_delta_cohort')
    return sorted(cases,key=lambda c:(urls(c),c['id'])),pairs,delta

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--integrated',type=Path,required=True)
    p.add_argument('--old-toc',type=Path,required=True)
    p.add_argument('--new-toc',type=Path,required=True)
    p.add_argument('--model-dir',type=Path,required=True)
    p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--out',type=Path,default=ROOT/'reports-live/toc-delta.json')
    p.add_argument('--certificate',type=Path)
    p.add_argument('--packet',type=Path)
    a=p.parse_args()
    if bool(a.certificate)!=bool(a.packet):p.error('paired private packet options required')
    reports={k:read(getattr(a,k),v) for k,v in PINS.items()}
    cases,previous,delta=selected(reports['integrated'],reports['old_toc'],reports['new_toc'])
    a.cache.mkdir(parents=True,exist_ok=True)
    enc=LocalEncoder(a.model_dir,a.cache/'vectors.sqlite3')
    client=PilotClient(a.cache/'retrieval',budget=len(cases)*32)
    matcher=AugmentedTocMatcher(client,enc);sem=SentenceVectorIndex(enc)
    ac=PilotClient(a.cache/'audit',budget=len(cases)*12)
    auditor=Auditor(ac);refresher=RevisionRefresher(ac)
    rows=[];private=[];start=time.monotonic()
    report={'schema':'wikidebia-toc-delta-1','status':'running','input_sha256':PINS,
            'selected':len(cases),'affected_urls':len(delta),'cohort_total':50,
            'model_id':enc.model_id,'rows':rows,'hosted_model_calls':0,
            'semantic_equivalence':None,'rights':'unreviewed','auto_publish':False}
    write(a.out,report)
    try:
        with open_current() as index:
            engine=Retrieval(client,16,catalog=load_catalog(ROOT/'bibliographic-catalog.json'),
                             local_index=index,refined=True,chapter_catalog=load_chapters(ROOT/'chapter-catalog.json'),
                             semantic_index=sem,toc_matcher=matcher,hybrid=True)
            for case in cases:
                old=previous[case['id']]['integrated_audit']
                result=one_case(engine,case,32);row=result['result']
                prior=ac.budget;ac.budget=min(prior,ac.requests+12)
                try:receipt,packet=review_primary(case,row,auditor,refresher)
                finally:ac.budget=prior
                route=row.get('toc_route_experiment',{})
                new_route=route.get('status')=='early_chapter_candidate' and route.get('matcher_variant')=='structure_on_abstention'
                target=receipt.get('target',{}).get('url')
                reached=bool(new_route and receipt['status']=='ready' and target==delta[urls(case)[0]])
                entry={'id':case['id'],'citation_ref':case['citation_ref'],'source_url':urls(case)[0],
                       'expected_chapter_for_evaluation':delta[urls(case)[0]],
                       'prior_result':old,'new_result':result,'new_audit':receipt,
                       'comparison':comparison(old,receipt),'new_route':new_route,
                       'authenticated_expected_chapter':reached}
                rows.append(entry)
                if reached and packet is not None:
                    private.append({'id':case['id'],'quote_sha256':case['citation_ref']['quote_sha256'],
                                    'fr_quote':packet['fr_quote'],'en_candidate':packet['en_candidate'],
                                    'candidate_sha256':receipt['target']['actual_text_sha256']})
                write(a.out,report)
                print(json.dumps({'id':case['id'],'route':route.get('status'),'authenticated_expected_chapter':reached,
                                  'previous':old['status'],'new':receipt['status']}),flush=True)
        errors=[r['id'] for r in rows if r['new_result']['result']['status']=='execution_error']
        report.update(status='complete' if not errors else 'incomplete',execution_errors=errors,
                      summary={'authenticated_new_expected_chapter':sum(r['authenticated_expected_chapter'] for r in rows),
                               'previous_ready':sum(r['prior_result']['status']=='ready' for r in rows),
                               'new_ready':sum(r['new_audit']['status']=='ready' for r in rows),
                               'old_review_words':sum(r['prior_result'].get('english_review_words',0) for r in rows),
                               'new_review_words':sum(r['new_audit'].get('english_review_words',0) for r in rows),
                               'routes':dict(Counter(r['new_result']['result'].get('toc_route_experiment',{}).get('status','not_attempted') for r in rows)),
                               'retrieval_requests':client.requests,'retrieval_decoded_bytes':client.network_body_bytes,
                               'audit_requests':ac.requests,'audit_decoded_bytes':ac.network_body_bytes,
                               'elapsed_seconds':round(time.monotonic()-start,3),'encoder_stats':enc.stats.copy(),
                               'comparison':'targeted fresh-cache subset vs frozen earlier full-cohort result; not equal-scope performance comparison'})
        write(a.out,report)
        print(json.dumps(report['summary'],indent=2))
        report_sha=sha(a.out.read_bytes())
        print('report_sha256='+report_sha)
        if a.certificate:
            data=json.dumps({'schema':'wikidebia-toc-delta-private-review-1','report_sha256':report_sha,
                             'cases':private,'rights':'unreviewed','auto_publish':False},
                            ensure_ascii=False,separators=(',',':')).encode()
            a.packet.parent.mkdir(parents=True,exist_ok=True)
            subprocess.run(['openssl','cms','-encrypt','-binary','-aes-256-gcm','-outform','DER',
                            '-recip',str(a.certificate),'-out',str(a.packet)],input=data,check=True)
            print(json.dumps({'packet_cases':len(private),'packet_sha256':sha(data),
                              'encrypted_sha256':sha(a.packet.read_bytes())}))
        return int(bool(errors))
    finally:enc.close()

if __name__=='__main__':raise SystemExit(main())
