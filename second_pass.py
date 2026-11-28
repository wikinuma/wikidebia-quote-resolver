"""Targeted second-pass quotation recovery using frozen review decisions.

Review only wrong/partial/transcription cases. Offer at most two *new*
authentic source spans per case. Keep text in a private encrypted packet,
and publish only hashes, coordinates and aggregate counts. No generated
text, semantic auto-acceptance or reuse authorization.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import unquote,urlsplit

from alignment import text_and_starts
from benchmark_index import write
from chapter_quality import primary
from evidence_audit import Auditor,load_report,read_span,span_receipt
from navigation import error_kind
from pilot_full import PilotClient,extract_blocks,html_revision,soup
from resolver import Document,ROOT,norm,sha,valid_url

REPORTS=['368a2ad617ab504838634dbf328ef4776a25bda9b45ae13d9c7061d6c24bedfd',
         '0a1d137fa16b1ff15e897e8d75fe6d37aa89a02adad32f9b5c8f24799f5ed18b']
CHAPTER_SHA='ec70f3aba700147d8da2848195ff3e55379d35c74bcc18e85cb62e5aca8e2de8'
REGISTRY_SHA='262a6b5e6c941266fd91a4b5eae78c6c3011ca97aef8a0fbf7ba808aedff8bc2'
TARGET_DECISIONS={'wrong_passage','partial','transcription_issue'}
MIA_STATE='https://www.marxists.org/archive/lenin/works/1919/jul/11.htm'
WR_STATE='https://wikirouge.net/texts/en/The_State%3A_A_Lecture_Delivered_at_the_Sverdlov_University'
MAX_CANDIDATES=2
MAX_WORDS=800


def load_pinned(path,digest,schema):
    raw=Path(path).read_bytes()
    if sha(raw)!=digest:
        raise ValueError('artifact_sha256_mismatch')
    data=json.loads(raw)
    if data.get('schema')!=schema or data.get('reports')!=REPORTS:
        raise ValueError('artifact_scope_mismatch')
    return data


def compact(text):
    return ''.join(c for c in text if not c.isspace())


def same_document_url(a,b):
    """Only for the explicitly documented mirror, not general URL resolution."""
    if not valid_url(a) or not valid_url(b):
        return False
    x,y=urlsplit(a),urlsplit(b)
    return (x.hostname==y.hostname and not x.query and not y.query and
            not x.fragment and not y.fragment and unquote(x.path)==unquote(y.path))


def mirror_span(old_text,doc):
    """Unique exact non-whitespace match. No punctuation, case or spelling
    repair; the returned text is literally a substring of the mirror.
    Different whitespace may affect meaning, so this remains review-only.
    """
    key=compact(old_text)
    if len(key)<80:
        return None
    text,_=text_and_starts(doc)
    offsets=[i for i,c in enumerate(text) if not c.isspace()]
    joined=''.join(text[i] for i in offsets)
    start=joined.find(key)
    if start<0 or joined.find(key,start+1)>=0:
        return None
    lo,hi=offsets[start],offsets[start+len(key)-1]+1
    if (lo and text[lo-1].isalnum() and text[lo].isalnum()) or (
            hi<len(text) and text[hi-1].isalnum() and text[hi].isalnum()):
        return None
    actual=text[lo:hi]
    if actual==old_text or compact(actual)!=key:
        return None
    return {'url':doc.url,'sha256':doc.digest,'revision':doc.revision,
            'char_start':lo,'char_end':hi,'text_sha256':sha(actual.encode()),
            'text_words':len(norm(actual).split()),'match':'same_nonwhitespace_characters_unique'}


class StateMirror:
    """A narrowly allowlisted mirror of a documented edition. Wikirouge's
    title contains a colon, so do not loosen the generic wiki-title filter.
    """
    def __init__(self,client):
        self.client=client
        self.loaded=False
        self.doc=None
        self.error=None

    def get(self):
        if not self.loaded:
            self.loaded=True
            try:
                body,url=self.client.get(WR_STATE)
                if not same_document_url(url,WR_STATE):
                    raise ValueError('mirror_url_changed')
                s=soup(body)
                header=norm(s.get_text(' ',strip=True))
                required=('11 july 1919','lenin collected works','4th english edition',
                          'progress publishers','1972','volume 29','470 488')
                if any(a not in header for a in required):
                    raise ValueError('mirror_edition_metadata_unconfirmed')
                self.doc=Document(url,extract_blocks(body,True),sha(body),
                    'The State: A Lecture Delivered at the Sverdlov University',html_revision(s))
                if not self.doc.blocks:
                    raise ValueError('mirror_empty_document')
            except Exception as exc:
                self.doc=None
                self.error=error_kind(exc)
        return self.doc


def ranked_candidates(result):
    """Preserve alignments before lexical candidates; remove exact duplicates."""
    seen=set()
    for c in (primary(result)+result.get('section_alignment',{}).get('candidates',[])+result.get('candidates',[])):
        identity=c.get('text_sha256')
        if not identity or identity in seen:
            continue
        seen.add(identity)
        yield c


def verified_candidate(audit,c):
    receipt=audit.span(c,'en')
    if receipt['status']!='verified_document_and_span':
        raise ValueError('target_span_not_verified')
    doc=audit.document(c['url'],'en')
    lo,hi,text=read_span(doc,c)
    return doc,receipt,text


def expand_partial(doc,receipt,max_extra_words=140):
    """Use at most one adjacent paragraph at each edge. Do not construct
    a new passage from disjoint text or expand to a whole chapter."""
    text,starts=text_and_starts(doc)
    lo,hi=receipt['char_start'],receipt['char_end']
    first=max(i for i,s in enumerate(starts) if s<=lo)
    last=max(i for i,s in enumerate(starts) if s<hi)
    begin=starts[max(0,first-1)]
    j=min(len(starts)-1,last+1)
    end=starts[j]+len(doc.blocks[j])
    if begin==lo and end==hi:
        return None
    if len(norm(text[begin:lo]+ ' '+text[hi:end]).split())>max_extra_words:
        return None
    return {'url':doc.url,'sha256':doc.digest,'revision':doc.revision,
            'char_start':begin,'char_end':end,
            'text_sha256':sha(text[begin:end].encode()),'text_words':len(norm(text[begin:end]).split()),
            'match':'verified_adjacent_context'}


def packet_candidate(doc,c,method,original=None):
    r=span_receipt(doc,c)
    if r['status']!='verified_document_and_span':
        raise ValueError('candidate_receipt_mismatch')
    _,_,text=read_span(doc,c)
    if r['text_words']>MAX_WORDS or len(text)>15000:
        raise ValueError('candidate_length_limit')
    return {'method':method,'receipt':r,'en_candidate':text,
            'original_receipt':original,'rights':'unreviewed','auto_publish':False}


def select_case(case,result,old,correction,audit,mirror):
    decision=old.get('decision')
    row={'id':case['id'],'prior_decision':decision,'status':'no_new_candidate',
         'citation_ref':case['citation_ref'],'metadata':case['metadata'],
         'candidates':[],'issues':[]}
    if decision not in TARGET_DECISIONS:
        raise ValueError('unexpected_review_decision')
    if old.get('citation_ref')!=case['citation_ref'] or old['status']!='review_revalidated':
        raise ValueError('review_input_mismatch')
    inp,quote=audit.input(case)
    if inp['status']!='verified_pinned_citation' or sha(quote.encode())!=case['citation_ref']['quote_sha256']:
        return {**row,'status':'input_unavailable'}
    # The previous review registry binds the actual text, not merely the
    # original result's primary (which may predate a chapter correction).
    previous={'url':old['target_url'],'sha256':old['document_sha256'],
              'revision':old.get('target_revision'),'char_start':old['char_start'],
              'char_end':old['char_end'],'text_sha256':old['target_text_sha256']}
    seen={previous['text_sha256']}
    def append(doc,c,method,original=None):
        if len(row['candidates'])>=MAX_CANDIDATES or c['text_sha256'] in seen:
            return
        try:
            candidate=packet_candidate(doc,c,method,original)
        except Exception as exc:
            row['issues'].append({'method':method,'error':error_kind(exc)})
            return
        seen.add(c['text_sha256'])
        row['candidates'].append(candidate)

    if decision=='transcription_issue':
        # Do not ask another reviewer to reread the same malformed string.
        # Seek a published transcription of the same identified edition.
        if previous['url']==MIA_STATE:
            try:
                _,r,text=verified_candidate(audit,previous)
                d=mirror.get()
                if d is None:
                    row['issues'].append({'method':'documented_edition_mirror','error':mirror.error})
                else:
                    c=mirror_span(text,d)
                    if c:
                        append(d,c,'documented_edition_mirror',r)
                    else:
                        row['issues'].append({'method':'documented_edition_mirror','error':'no_unique_changed_whitespace_match'})
            except Exception as exc:
                row['issues'].append({'method':'documented_edition_mirror','error':error_kind(exc)})
    else:
        corrected=bool(correction and correction.get('new_primary_in_chapter') is True)
        chosen=correction['new'] if corrected else result
        if decision=='partial' and not corrected:
            try:
                d,r,_=verified_candidate(audit,previous)
                c=expand_partial(d,r)
                if c:
                    append(d,c,'adjacent_context',r)
            except Exception as exc:
                row['issues'].append({'method':'adjacent_context','error':error_kind(exc)})
        for c in ranked_candidates(chosen):
            if len(row['candidates'])>=MAX_CANDIDATES:
                break
            if c['text_sha256'] in seen:
                continue
            if corrected and c['url']!=correction['chapter']['en_url']:
                raise ValueError('corrected_candidate_outside_chapter')
            try:
                d,_,_=verified_candidate(audit,c)
                append(d,c,'corrected_chapter' if corrected else 'deferred_candidate')
            except Exception as exc:
                row['issues'].append({'method':'deferred_candidate','error':error_kind(exc)})
        if decision=='partial' and corrected and len(row['candidates'])<MAX_CANDIDATES:
            try:
                d,r,_=verified_candidate(audit,previous)
                if d.url==correction['chapter']['en_url']:
                    c=expand_partial(d,r)
                    if c:
                        append(d,c,'adjacent_context',r)
            except Exception as exc:
                row['issues'].append({'method':'adjacent_context','error':error_kind(exc)})
    if row['candidates']:
        row.update(status='ready',fr_quote=quote)
    return row


def public_row(row):
    # Explicitly remove text-bearing fields, not just fields named "text".
    return {**{k:v for k,v in row.items() if k not in ('fr_quote','candidates')},
            'candidates':[{k:v for k,v in c.items() if k!='en_candidate'} for c in row['candidates']]}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--development',type=Path,required=True)
    p.add_argument('--holdout',type=Path,required=True)
    p.add_argument('--chapter',type=Path,required=True)
    p.add_argument('--registry',type=Path,required=True)
    p.add_argument('--certificate',type=Path,required=True)
    p.add_argument('--out',type=Path,default=ROOT/'reports-live/second-pass.json')
    p.add_argument('--packet',type=Path,required=True)
    a=p.parse_args()
    reports=[load_report(a.development,REPORTS[0],'refined'),load_report(a.holdout,REPORTS[1],'refined')]
    chapter=load_pinned(a.chapter,CHAPTER_SHA,'wikidebia-chapter-quality-1')
    registry=load_pinned(a.registry,REGISTRY_SHA,'wikidebia-revalidated-review-registry-1')
    corrections={c['id']:c for c in chapter['cases']}
    reviews={c['case_id']:c for c in registry['cases']}
    if len(reviews)!=100 or len(registry['cases'])!=100 or len(corrections)!=len(chapter['cases']):
        raise ValueError('incomplete_or_duplicate_frozen_registry')
    rows=[];seen=set()
    with tempfile.TemporaryDirectory(prefix='mia-second-pass-') as tmp:
        client=PilotClient(Path(tmp),budget=200)
        audit=Auditor(client);mirror=StateMirror(client)
        for report in reports:
            for case,pair in zip(report['sample'],report['pairs']):
                cid=case['id']
                if cid in seen: raise ValueError('duplicate_case_id')
                seen.add(cid)
                old=reviews[cid]
                if old.get('decision') not in TARGET_DECISIONS: continue
                rows.append(select_case(case,pair['refined']['result'],old,corrections.get(cid),audit,mirror))
                write(a.out,{'schema':'wikidebia-second-pass-1','status':'running','cases':[public_row(r) for r in rows]})
        if seen!=set(reviews) or len(rows)!=29:
            raise ValueError('unexpected_target_cohort')
        summary={'targeted_cases':len(rows),'case_statuses':dict(Counter(r['status'] for r in rows)),
                 'by_prior_decision':{d:dict(Counter(r['status'] for r in rows if r['prior_decision']==d)) for d in sorted(TARGET_DECISIONS)},
                 'new_candidates':sum(len(r['candidates']) for r in rows),
                 'candidate_methods':dict(Counter(c['method'] for r in rows for c in r['candidates'])),
                 'initial_english_words':sum(r['candidates'][0]['receipt']['text_words'] for r in rows if r['candidates']),
                 'all_english_words':sum(c['receipt']['text_words'] for r in rows for c in r['candidates']),
                 'network_requests':client.requests,'network_body_bytes':client.network_body_bytes,
                 'model_calls':0,'new_exact_equivalences':None,'reuse_rights_approved':0}
        common={'schema':'wikidebia-second-pass-1','reports':REPORTS,'registry_sha256':REGISTRY_SHA,
                'chapter_sha256':CHAPTER_SHA,'summary':summary,'auto_publish':False,'rights':'unreviewed'}
        write(a.out,{**common,'status':'candidates_prepared','cases':[public_row(r) for r in rows]})
        a.packet.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['openssl','cms','-encrypt','-binary','-aes-256-gcm',
                        '-outform','DER','-recip',str(a.certificate),'-out',str(a.packet)],
                       input=json.dumps({**common,'cases':rows},ensure_ascii=False,separators=(',',':')).encode(),
                       check=True)
        print(json.dumps(summary,indent=2),flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
