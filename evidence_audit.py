"""Independently reread public quotation candidates and audit their evidence.

A matching source substring is not proof of bilingual equivalence, exact
translation boundaries, or license compatibility. Public reports contain
hashes/coordinates and bibliographic facts, never full quotation text.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlsplit, unquote

from alignment import text_and_starts
from benchmark_index import write
from navigation import canonical_mia, raw_parse, extract_citation_ref, error_kind
from pilot_full import PilotClient, extract_blocks, html_revision, soup, wr_title
from resolver import Document, ROOT, WD_API, clean, norm, sha, valid_url, RequestBudgetExceeded

SCHEMAS={'wikidebia-refined-index-1','wikidebia-mia-holdout-1','wikidebia-paired-index-1'}
HEX=re.compile(r'[0-9a-f]{64}')
LABELS=re.compile(r'^\s*(translated(?:\s+by)?|translator|source|first published|published|publication|edition|copyright|copyleft|transcription(?:/html markup)?|proofread(?:ing)?|licen[cs]e)\s*:\s*(.+)$',re.I)
RIGHTS_RE=re.compile(r'\b(?:copyright|copyleft|permission|licen[cs]e|public domain|all rights reserved)\b',re.I)
LICENSE_PATTERNS={
    'GFDL':re.compile(r'GNU Free Documentation Licen[cs]e|\bGFDL\b',re.I),
    'CC-BY-SA':re.compile(r'Creative Commons Attribution[- ]ShareAlike|\bCC BY-SA\b',re.I),
    'CC-BY':re.compile(r'Creative Commons Attribution (?:[1-4]\.\d|International)|\bCC BY\b(?!-)',re.I),
    'CC0':re.compile(r'\bCC0\b|Creative Commons Zero',re.I),
    'public_domain_notice':re.compile(r'\bpublic domain\b',re.I),
    'copyright_notice':re.compile(r'\bcopyright\b|all rights reserved|©',re.I),
}


def valid_digest(s):
    return isinstance(s,str) and HEX.fullmatch(s) is not None


STATE_MIRROR_TITLE = 'The State: A Lecture Delivered at the Sverdlov University'
STATE_MIRROR_PATH = '/texts/en/' + STATE_MIRROR_TITLE.replace(' ', '_')
STATE_MIRROR_LABELS = ('11 july 1919', 'lenin collected works', '4th english edition',
                       'progress publishers', '1972', 'volume 29', '470 488')


def documented_state_mirror(url):
    p = urlsplit(url)
    return (valid_url(url) and p.hostname == 'wikirouge.net'
            and not p.query and not p.fragment
            and unquote(p.path) == STATE_MIRROR_PATH)


def document_provider(url,lang):
    if not valid_url(url):
        raise ValueError('unexpected_document_url')
    if canonical_mia(url):
        if lang=='en' and '/francais/' in url:
            raise ValueError('french_target_url')
        return 'mia'
    if (lang == 'en' and documented_state_mirror(url)) or wr_title(url,lang):
        return 'wikirouge'
    raise ValueError('unexpected_document_provider')


def publication_metadata(body,provider):
    """Extract labelled bibliography and explicit rights *signals*, not legal
    conclusions. Avoid publishing arbitrary body text or long license prose.
    """
    s=soup(body)
    out={'fields':{},'license_signals':[],'rights_notice_sha256':[],
         'rights_status':'unreviewed','reuse_authorized':False}
    labels={}
    for tag in s.find_all(['p','li','td','div'],limit=3000):
        # Container nodes can include an entire work. Do not use them.
        if tag.find(['p','li','td','div'],recursive=True):
            continue
        # BR-separated MIA bibliographic header lines must remain separate.
        lines=tag.get_text('\n',strip=True).splitlines()
        if len(lines)>100:
            continue
        for i,line in enumerate(lines):
            line=clean(line)
            if line.endswith(':') and i+1<len(lines):
                line+=' '+clean(lines[i+1])
            m=LABELS.fullmatch(line)
            if m and len(m[2])<=240:
                key=norm(m[1])
                if key in ('copyright','copyleft','licence','license'):
                    out['rights_notice_sha256'].append(sha(line.encode()))
                else:
                    labels.setdefault(key,set()).add(m[2])
            if len(line)<=1000 and RIGHTS_RE.search(line):
                out['license_signals'].extend(name for name,rx in LICENSE_PATTERNS.items() if rx.search(line))
    # Explicit link text often labels the declared license.
    for a in s.find_all('a',href=True):
        label=clean(a.get_text(' ',strip=True))
        if len(label)<=120:
            out['license_signals'].extend(name for name,rx in LICENSE_PATTERNS.items() if rx.search(label))
    out['fields']={k:sorted(v) for k,v in sorted(labels.items())}
    out['license_signals']=sorted(set(out['license_signals']))
    out['rights_notice_sha256']=sorted(set(out['rights_notice_sha256']))
    out['scope']='page_level_labels_and_license_signals_not_legal_review'
    return out


def read_span(doc,entry):
    """Check original character coordinates or reconstruct exact block spans."""
    text,starts=text_and_starts(doc)
    lo,hi=entry.get('char_start'),entry.get('char_end')
    if lo is None and hi is None:
        i,n=entry.get('block'),entry.get('span')
        if type(i) is not int or type(n) is not int or not 0<=i<len(starts) or not 1<=n<=len(starts)-i:
            raise ValueError('invalid_block_coordinates')
        lo,hi=starts[i],starts[i+n-1]+len(doc.blocks[i+n-1])
    if type(lo) is not int or type(hi) is not int or not 0<=lo<hi<=len(text):
        raise ValueError('invalid_character_coordinates')
    return lo,hi,text[lo:hi]


def span_receipt(doc,entry):
    expected=entry.get('text_sha256')
    if not valid_digest(expected) or not valid_digest(entry.get('sha256')):
        raise ValueError('missing_evidence_digest')
    lo,hi,text=read_span(doc,entry)
    passage=sha(text.encode())
    revision_same=entry.get('revision') is None or entry['revision']==doc.revision
    document_same=entry['sha256']==doc.digest
    exact=passage==expected
    status='verified_document_and_span' if exact and document_same and revision_same else (
        'span_matches_changed_document' if exact and revision_same else
        'revision_mismatch' if not revision_same else 'span_mismatch')
    return {'url':doc.url,'recorded_document_sha256':entry['sha256'],'document_sha256':doc.digest,
            'revision':doc.revision,'char_start':lo,'char_end':hi,'expected_text_sha256':expected,
            'actual_text_sha256':passage,'text_words':len(norm(text).split()),
            'document_matches':document_same,'span_matches':exact,'revision_matches':revision_same,
            'status':status,'bilingual_equivalence':'unreviewed','boundary_verified':False,
            'rights':'unreviewed','auto_publish':False}


class Auditor:
    def __init__(self,client):
        self.client=client
        self.docs={}
        self.publication={}

    def document(self,url,lang):
        key=(lang,url)
        if key not in self.docs:
            try:
                provider=document_provider(url,lang)
                body,final=self.client.get(url)
                if final!=url or document_provider(final,lang)!=provider:
                    raise ValueError('document_url_changed')
                s=soup(body)
                mirror = lang == 'en' and documented_state_mirror(final)
                if mirror:
                    header = norm(s.get_text(' ', strip=True))
                    if any(label not in header for label in STATE_MIRROR_LABELS):
                        raise ValueError('mirror_edition_metadata_unconfirmed')
                doc=Document(final,extract_blocks(body,provider=='wikirouge'),sha(body),
                             (STATE_MIRROR_TITLE if mirror else wr_title(final,lang) or ''),html_revision(s) if provider=='wikirouge' else None)
                if not doc.blocks:
                    raise ValueError('empty_document')
                self.publication[key]={'url':final,'lang':lang,'document_sha256':doc.digest,
                                       **publication_metadata(body,provider)}
                self.docs[key]=doc
            except Exception as exc:
                failure={'status':'document_unavailable','error':error_kind(exc),'url':url}
                if isinstance(exc, RequestBudgetExceeded):
                    return failure
                self.docs[key]=failure
        return self.docs[key]

    def span(self,entry,lang):
        try:
            doc=self.document(entry['url'],lang)
            if isinstance(doc,dict):
                return doc.copy()
            return span_receipt(doc,entry)
        except Exception as exc:
            return {'status':'span_audit_error','url':entry.get('url'),'error':error_kind(exc)}

    def input(self,case):
        ref=case['citation_ref']
        try:
            title,rev,raw=raw_parse(self.client,WD_API,oldid=ref['revision'])
            if title.replace('_',' ')!=case['wiki_title'].replace('_',' '):
                raise ValueError('citation_page_mismatch')
            meta=extract_citation_ref(raw,ref,include_text=True)
            if not meta['mia_urls'] or meta['mia_urls']!=case['metadata']['mia_urls']:
                raise ValueError('input_mia_link_mismatch')
            return {'status':'verified_pinned_citation','revision':rev,'quote_sha256':meta['quote_sha256'],
                    'template_sha256':meta['template_sha256'],'quote_words':meta['quote_words'],
                    'mia_urls':meta['mia_urls']},meta['text']
        except Exception as exc:
            return {'status':'input_unavailable_or_changed','error':error_kind(exc)},None

    def case(self,case,result):
        i,quote=self.input(case)
        source=result.get('source',{})
        candidates=source.get('occurrences',[])+source.get('variants',[])
        source_receipts=[]
        for c in candidates:
            x=self.span(c,'fr')
            # On normalized contiguous evidence, independently verify the
            # source quotation's normalized words, not its typography.
            if quote is not None and x.get('span_matches') and c.get('match')=='normalized_contiguous_tokens':
                doc=self.document(c['url'],'fr')
                _,_,fragment=read_span(doc,c)
                x['normalized_french_quote_matches']=norm(fragment)==norm(quote)
            source_receipts.append(x)
        aligned=result.get('section_alignment',{}).get('candidates',[])
        lexical=result.get('candidates',[])
        primary=result.get('review_packet',{}).get('candidates',[]) or (aligned or lexical)[:1]
        targets=[]
        seen=set()
        for candidate in primary+aligned+lexical:
            identity=(candidate.get('url'),candidate.get('sha256'),candidate.get('text_sha256'),
                      candidate.get('char_start'),candidate.get('char_end'),candidate.get('block'),candidate.get('span'))
            if identity in seen:
                continue
            seen.add(identity)
            receipt=self.span(candidate,'en')
            receipt['initial_candidate']=candidate in primary
            targets.append(receipt)
        return {'id':case['id'],'input':i,'source':source_receipts,'targets':targets,
                'retrieval_status':result.get('status'),'bilingual_equivalence':'unreviewed',
                'exact_english_quote':'unreviewed','rights':'unreviewed','auto_publish':False}


def summarize(rows):
    verified='verified_document_and_span'
    count=lambda seq:dict(Counter(x['status'] for x in seq))
    return {'cases':len(rows),'input_statuses':count(r['input'] for r in rows),
            'source_span_statuses':count(s for r in rows for s in r['source']),
            'target_span_statuses':count(t for r in rows for t in r['targets']),
            'cases_with_strict_primary_span':sum(any(t.get('initial_candidate') and t['status']==verified for t in r['targets']) for r in rows),
            'cases_with_strict_primary_and_source':sum(r['input']['status']=='verified_pinned_citation' and
                any(s['status']==verified for s in r['source']) and
                any(t.get('initial_candidate') and t['status']==verified for t in r['targets']) for r in rows),
            'cases_with_normalized_exact_french_source':sum(any(s.get('normalized_french_quote_matches') is True and s['status']==verified for s in r['source']) for r in rows),
            'verified_bilingual_equivalence':None,'verified_exact_english_quotes':None,'verified_reuse_rights':None}


def load_report(path,expected_sha,arm):
    data=Path(path).read_bytes()
    if not valid_digest(expected_sha) or sha(data)!=expected_sha:
        raise ValueError('report_sha256_mismatch')
    report=json.loads(data)
    if report.get('schema') not in SCHEMAS or report.get('scope')!='explicit_mia_only' or report.get('status') not in (
        'candidate_comparison_complete','development_comparison_complete','holdout_candidate_comparison_complete'):
        raise ValueError('report_scope_or_status_mismatch')
    cases=report['sample']; pairs=report['pairs']
    if not 1<=len(cases)<=80 or len(cases)!=len(pairs) or len({c['id'] for c in cases})!=len(cases):
        raise ValueError('invalid_report_cohort')
    for c,p in zip(cases,pairs):
        urls=c['metadata']['mia_urls']
        if c['id']!=p['id'] or not urls or any(canonical_mia(u)!=u for u in urls) or arm not in p:
            raise ValueError('invalid_report_case')
        if p[arm]['result']['id']!=c['id']:
            raise ValueError('invalid_result_identity')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--sha256',required=True)
    p.add_argument('--arm',choices=['legacy','indexed','refined'],default='refined')
    p.add_argument('--out',type=Path,default=ROOT/'reports-live'/'evidence-audit.json')
    p.add_argument('--budget',type=int,default=400)
    a=p.parse_args()
    if not 1<=a.budget<=1000:
        p.error('bounded positive budget required')
    original=load_report(a.report,a.sha256,a.arm)
    with tempfile.TemporaryDirectory(prefix='evidence-audit-') as tmp:
        client=PilotClient(Path(tmp),budget=a.budget)
        audit=Auditor(client); started=time.monotonic()
        rows=[]; result={'schema':'wikidebia-evidence-audit-1','scope':'explicit_mia_only','report_sha256':a.sha256,
                         'arm':a.arm,'status':'running','cases':rows,'auto_publish':False,
                         'model_calls':0,'verification_scope':'fresh public input fingerprints, source and target extracted-text coordinates; not semantic or rights approval'}
        for i,(case,pair) in enumerate(zip(original['sample'],original['pairs'])):
            rows.append(audit.case(case,pair[a.arm]['result']))
            write(a.out,result)
            print(json.dumps({'completed':i+1,'case':case['id'],'input':rows[-1]['input']['status'],
                              'sources':Counter(x['status'] for x in rows[-1]['source']),
                              'targets':Counter(x['status'] for x in rows[-1]['targets'])}),flush=True)
        summary=summarize(rows)
        result.update(status='audit_complete',summary=summary,documents=list(audit.publication.values()),
                      requests=client.requests,decoded_network_body_bytes=client.network_body_bytes,
                      elapsed_seconds=round(time.monotonic()-started,3))
        write(a.out,result)
        print(json.dumps({'summary':summary,'documents':len(audit.publication),
                          'requests':client.requests,'decoded_network_body_bytes':client.network_body_bytes,
                          'elapsed_seconds':result['elapsed_seconds']},indent=2),flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
