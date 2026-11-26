"""Build a redacted, reusable MIA review memory from pinned historical reports.

No text extraction, network, model calls or publication. Historical review
decisions are not current source verification or independent gold labels.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from navigation import canonical_mia
from resolver import valid_url

PINS = {
    'development': '368a2ad617ab504838634dbf328ef4776a25bda9b45ae13d9c7061d6c24bedfd',
    'holdout': '0a1d137fa16b1ff15e897e8d75fe6d37aa89a02adad32f9b5c8f24799f5ed18b',
    'registry': '262a6b5e6c941266fd91a4b5eae78c6c3011ca97aef8a0fbf7ba808aedff8bc2',
    'second': '5dea50b0407103fa08338a1ffa4756c821a92463d04906b7e6f4ca01655937be',
}
ACCEPT = {'complete_equivalent', 'boundary_adjustment', 'omission_adjustment'}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_pinned(path, expected):
    raw = Path(path).read_bytes()
    if digest(raw) != expected:
        raise ValueError('pinned_report_hash_mismatch')
    return json.loads(raw)


def keyed(rows, key):
    result = {r[key]: r for r in rows}
    if len(result) != len(rows):
        raise ValueError('duplicate_case')
    return result


def check_hash(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('invalid_digest')


def proof(decision, url, revision, document_hash, candidate_hash, lo, hi, fragments):
    if not valid_url(url) or type(lo) is not int or type(hi) is not int or not 0 <= lo < hi:
        raise ValueError('invalid_candidate_receipt')
    if revision is not None and (type(revision) is not int or revision <= 0):
        raise ValueError('invalid_revision')
    for h in (document_hash, candidate_hash):
        check_hash(h)
    if bool(fragments) != (decision in ACCEPT):
        raise ValueError('decision_fragment_mismatch')
    verified = []
    prev = 0
    for f in fragments:
        a, b = f['relative_start'], f['relative_end']
        if type(a) is not int or type(b) is not int or not 0 <= prev <= a < b <= hi-lo:
            raise ValueError('invalid_fragment')
        check_hash(f['text_sha256'])
        if f.get('char_start', lo+a) != lo+a or f.get('char_end', lo+b) != lo+b:
            raise ValueError('absolute_fragment_mismatch')
        verified.append({'char_start':lo+a,'char_end':lo+b,'text_sha256':f['text_sha256']})
        prev = b
    if decision == 'complete_equivalent' and verified != [
            {'char_start':lo,'char_end':hi,'text_sha256':candidate_hash}]:
        raise ValueError('complete_candidate_mismatch')
    return {'decision':decision,'url':url,'revision':revision,'document_sha256':document_hash,
            'candidate_sha256':candidate_hash,'char_start':lo,'char_end':hi,'fragments':verified,
            'proof_status':'historical_review_receipt_not_fresh_verification'}


def build(development, holdout, registry, second, ledger):
    cases = keyed(development['sample']+holdout['sample'],'id')
    firsts = keyed(registry['cases'],'case_id')
    seconds = keyed(second['cases'],'id')
    reviews = keyed(ledger['cases'],'case_id')
    if (len(cases) != 100 or set(cases) != set(firsts) or len(seconds) != 29
            or set(seconds) != set(reviews) or not set(seconds) <= set(cases)):
        raise ValueError('cohort_mismatch')
    if ledger['report_sha256'] != PINS['second'] or ledger['previous_registry_sha256'] != PINS['registry']:
        raise ValueError('review_provenance_mismatch')
    rows = []
    for cid, case in sorted(cases.items()):
        ref = case['citation_ref']
        meta = case['metadata']
        if case['stratum'] != 'explicit_mia' or not meta['mia_urls'] or any(canonical_mia(u) != u for u in meta['mia_urls']):
            raise ValueError('non_mia_case')
        check_hash(ref['quote_sha256'])
        check_hash(ref['template_sha256'])
        first = firsts[cid]
        if first['citation_ref'] != ref or first['auto_publish'] is not False or first['rights'] != 'unreviewed':
            raise ValueError('first_review_identity_mismatch')
        decision = first.get('decision',first['status'])
        row = {'id':cid,'wiki_title':case['wiki_title'],'citation_ref':ref,
               'metadata':{k:meta[k] for k in ('authors','work','article','date','mia_urls','quote_words')},
               'first':{'decision':decision},'latest_decision':decision,
               'rights':'unreviewed','auto_publish':False}
        if first['status'] == 'review_revalidated':
            row['first'] = proof(decision,first['target_url'],first['target_revision'],
                                 first['document_sha256'],first['target_text_sha256'],
                                 first['char_start'],first['char_end'],first['fragments'])
        elif first['status'] != 'no_primary_candidate':
            raise ValueError('unexpected_first_status')
        if cid in seconds:
            report, review = seconds[cid], reviews[cid]
            if (report['citation_ref'] != ref or report['prior_decision'] != decision or decision in ACCEPT
                    or review['rights'] != 'unreviewed' or review['auto_publish'] is not False):
                raise ValueError('second_review_identity_mismatch')
            d = review['decision']
            row['latest_decision'] = d
            row['second'] = {'decision':d}
            if review['candidate_sha256'] is not None:
                matches = [c['receipt'] for c in report['candidates']
                           if c['receipt']['actual_text_sha256'] == review['candidate_sha256']]
                if len(matches) != 1 or matches[0]['status'] != 'verified_document_and_span':
                    raise ValueError('second_candidate_not_authenticated')
                r = matches[0]
                row['second'] = proof(d,r['url'],r['revision'],r['document_sha256'],r['actual_text_sha256'],
                                      r['char_start'],r['char_end'],review['fragments'])
            elif review['fragments'] or d in ACCEPT:
                raise ValueError('missing_second_proof')
        rows.append(row)
    counts = dict(Counter(r['latest_decision'] for r in rows))
    accepted = sum(r['latest_decision'] in ACCEPT for r in rows)
    if accepted != 37 or len({r['citation_ref']['quote_sha256'] for r in rows}) != 100:
        raise ValueError('unexpected_review_result')
    return {'schema':'wikidebia-mia-review-memory-1','source_reports':PINS,
            'reviewer':'ChatGPT','scope':'historical model-assisted decisions, not independent human gold',
            'summary':{'cases':len(rows),'historically_accepted':accepted,'unresolved':len(rows)-accepted,
                       'latest_decisions':counts,'rights_approved':0},'cases':rows,'auto_publish':False}


def lookup(memory, metadata, citation_ref):
    """Reuse only exact content AND full template fingerprints and MIA URLs.
    A different page revision is allowed for an identical template; this
    returns a historical decision, never permission to publish.
    """
    q,t = citation_ref['quote_sha256'],citation_ref['template_sha256']
    matches = [r for r in memory['cases'] if r['citation_ref']['quote_sha256']==q
               and r['citation_ref']['template_sha256']==t
               and sorted(r['metadata']['mia_urls'])==sorted(metadata['mia_urls'])]
    return matches[0] if len(matches)==1 else None


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in PINS:
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--ledger',type=Path,default=Path('second-pass-review-ledger.json'))
    p.add_argument('--out',type=Path,default=Path('reports-live/mia-review-memory.json'))
    p.add_argument('--emit-redacted',action='store_true')
    a=p.parse_args()
    data={name:read_pinned(getattr(a,name),pin) for name,pin in PINS.items()}
    memory=build(**data,ledger=json.loads(a.ledger.read_bytes()))
    memory['ledger_sha256']=digest(a.ledger.read_bytes())
    a.out.parent.mkdir(parents=True,exist_ok=True)
    raw=(json.dumps(memory,ensure_ascii=True,sort_keys=True,separators=(',',':'))+'\n').encode()
    a.out.write_bytes(raw)
    print(json.dumps(memory['summary'],sort_keys=True))
    print('memory_sha256='+digest(raw))
    if a.emit_redacted:
        header={k:v for k,v in memory.items() if k!='cases'}
        print('MEMORY_HEADER='+json.dumps(header,ensure_ascii=True,sort_keys=True,separators=(',',':')))
        for row in memory['cases']:
            print('MEMORY_ROW='+json.dumps(row,ensure_ascii=True,sort_keys=True,separators=(',',':')))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
