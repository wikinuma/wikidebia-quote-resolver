"""Conservative refresh of a Wikirouge receipt when only HTML bytes drift.

The old strict receipt is never reclassified. A fresh receipt requires the
same positive revision, same URL and exact span, plus an independent public
MediaWiki parse of that pinned revision containing a unique identical span.
No MIA (non-revisioned) document can use this path.
"""
from collections import Counter
import argparse
import json
from pathlib import Path
import tempfile
import time
import unicodedata
from urllib.parse import urlsplit

from alignment import text_and_starts
from evidence_audit import Auditor, read_span, span_receipt, valid_digest
from navigation import error_kind
from pilot_full import PilotClient, extract_blocks, wr_title
from resolver import API, ROOT, Document, sha, RequestBudgetExceeded


def title_key(value):
    return unicodedata.normalize('NFC', value.replace('_',' '))


class RevisionRefresher:
    def __init__(self, client):
        self.client = client
        self.parsed = {}

    def pinned(self, url, rev):
        key = (url, rev)
        if key not in self.parsed:
            try:
                title = wr_title(url, 'en')
                if not title or type(rev) is not int or rev <= 0:
                    raise ValueError('invalid_revision_url')
                body, final = self.client.get(API['en'], {
                    'format':'json','formatversion':2,'action':'parse',
                    'oldid':rev,'prop':'text|revid'})
                endpoint, expected = urlsplit(final), urlsplit(API['en'])
                if (endpoint.scheme,endpoint.netloc,endpoint.path) != (expected.scheme,expected.netloc,expected.path):
                    raise ValueError('unexpected_api_endpoint')
                result = json.loads(body)['parse']
                if result['revid'] != rev or title_key(result['title']) != title_key(title):
                    raise ValueError('pinned_revision_identity_mismatch')
                raw = result['text']
                if isinstance(raw, dict):
                    raw = raw['*']
                blocks = extract_blocks(raw.encode('utf-8'), True)
                if not blocks:
                    raise ValueError('empty_pinned_revision')
                document = Document(url,blocks,sha(raw.encode()),title,rev)
                text,_ = text_and_starts(document)
                self.parsed[key] = (text,sha(body),None)
            except Exception as exc:
                if isinstance(exc, RequestBudgetExceeded):
                    return (None,None,error_kind(exc))
                self.parsed[key] = (None,None,error_kind(exc))
        return self.parsed[key]

    def refresh(self, doc, entry):
        old = span_receipt(doc, entry)
        out = {'schema':'wikidebia-revision-span-refresh-1',
               'original_receipt':old,'status':'not_verified',
               'bilingual_equivalence':'unreviewed','boundary_verified':False,
               'rights':'unreviewed','auto_publish':False}
        rev = entry.get('revision')
        if (not wr_title(doc.url,'en') or doc.url != entry.get('url')
                or type(rev) is not int or rev <= 0 or doc.revision != rev
                or old['status'] not in {'span_matches_changed_document','verified_document_and_span'}):
            return {**out,'reason':'not_same_revision_and_exact_span'}
        lo,hi,fragment = read_span(doc,entry)
        # Require a nonempty substantive passage, not a coincidental short word.
        if len(fragment.strip()) < 80:
            return {**out,'reason':'span_too_short'}
        pinned,api_hash,error = self.pinned(doc.url,rev)
        if pinned is None:
            return {**out,'reason':'pinned_revision_unavailable','error':error}
        pos = pinned.find(fragment)
        if pos < 0 or pinned.find(fragment,pos+1) >= 0:
            return {**out,'reason':'pinned_span_missing_or_ambiguous',
                    'pinned_response_sha256':api_hash,'pinned_extracted_text_sha256':sha(pinned.encode())}
        if not valid_digest(api_hash):
            raise ValueError('invalid_api_receipt')
        refreshed = {**entry,'sha256':doc.digest,'revision':rev,
                     'char_start':lo,'char_end':hi}
        new = span_receipt(doc,refreshed)
        if new['status'] != 'verified_document_and_span':
            raise ValueError('fresh_receipt_failed')
        return {**out,'status':'verified_same_revision_span',
                'fresh_receipt':new,'pinned_response_sha256':api_hash,
                'pinned_extracted_text_sha256':sha(pinned.encode()),
                'pinned_char_start':pos,'pinned_char_end':pos+len(fragment),
                'pinned_text_sha256':sha(fragment.encode()),
                'original_html_matches':old['document_matches']}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture',type=Path,default=ROOT/'experiments/mia-nonreproduced-receipts.json')
    p.add_argument('--out',type=Path,default=ROOT/'reports-live/mia-revision-refresh.json')
    a=p.parse_args()
    fixture=json.loads(a.fixture.read_bytes())
    entries=fixture['entries']
    if fixture['schema']!='wikidebia-receipt-diagnostic-input-1' or not 1<=len(entries)<=30:
        raise ValueError('invalid_fixture')
    start=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='mia-revision-refresh-') as tmp:
        client=PilotClient(Path(tmp),budget=20)
        audit=Auditor(client); refresher=RevisionRefresher(client)
        rows=[]
        for e in entries:
            doc=audit.document(e['url'],'en')
            if isinstance(doc,dict):
                result={'status':'not_verified','reason':'document_unavailable','rights':'unreviewed','auto_publish':False}
            else:
                try:
                    result=refresher.refresh(doc,e)
                except ValueError:
                    result={'status':'not_verified','reason':'invalid_receipt','rights':'unreviewed','auto_publish':False}
            rows.append({'case_id':e['case_id'],'url':e['url'],'expected_text_sha256':e['text_sha256'],**result})
        report={'schema':'wikidebia-mia-revision-refresh-report-1',
                'fixture_sha256':sha(a.fixture.read_bytes()),
                'summary':{'entries':len(rows),'statuses':dict(Counter(r['status'] for r in rows)),
                           'reasons':dict(Counter(r.get('reason','verified') for r in rows)),
                           'network_requests':client.requests,'decoded_network_body_bytes':client.network_body_bytes,
                           'elapsed_seconds':round(time.monotonic()-start,3),
                           'model_calls':0,'new_equivalences':0,'rights_approved':0},
                'cases':rows,'auto_publish':False}
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps(report['summary'],indent=2))
    for row in rows:
        print('REFRESH_RECEIPT='+json.dumps(row,sort_keys=True,separators=(',',':')))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
