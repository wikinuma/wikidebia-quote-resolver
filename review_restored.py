"""Prepare a private bilingual review of only the six restored receipts."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import tempfile
import time

from evidence_audit import Auditor, read_span
from pilot_full import PilotClient
from resolver import ROOT, sha
from revision_receipt import RevisionRefresher


def prepare(entries, memory, audit, refresher):
    by_id={r['id']:r for r in memory['cases']}
    rows={}
    for e in entries:
        cid=e['case_id']
        case=by_id[cid]
        if case['latest_decision']!='not_reproduced' or case['auto_publish'] is not False:
            raise ValueError('case_outside_targeted_review')
        if cid not in rows:
            inp,quote=audit.input(case)
            if inp['status']!='verified_pinned_citation' or sha(quote.encode())!=case['citation_ref']['quote_sha256']:
                raise ValueError('pinned_input_unavailable')
            rows[cid]={'id':cid,'citation_ref':case['citation_ref'],'metadata':case['metadata'],
                       'fr_quote':quote,'input_receipt':inp,'candidates':[],
                       'rights':'unreviewed','auto_publish':False}
        row=rows[cid]
        doc=audit.document(e['url'],'en')
        if isinstance(doc,dict):
            raise ValueError('target_unavailable')
        proof=refresher.refresh(doc,e)
        if proof['status']!='verified_same_revision_span':
            raise ValueError('target_not_reproduced')
        _,_,text=read_span(doc,e)
        if sha(text.encode())!=e['text_sha256']:
            raise ValueError('target_hash_mismatch')
        row['candidates'].append({'receipt':proof,'en_candidate':text})
    return list(rows.values())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--memory',type=Path,default=ROOT/'data/mia/memory.json')
    p.add_argument('--fixture',type=Path,default=ROOT/'experiments/mia-nonreproduced-receipts.json')
    p.add_argument('--certificate',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    memory=json.loads(a.memory.read_bytes());fixture=json.loads(a.fixture.read_bytes())
    if (memory['schema']!='wikidebia-mia-review-memory-1' or fixture['schema']!='wikidebia-receipt-diagnostic-input-1'
            or len(fixture['entries'])!=6 or len({e['case_id'] for e in fixture['entries']})!=3):
        raise ValueError('unexpected_scope')
    started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='mia-restored-review-') as tmp:
        client=PilotClient(Path(tmp),budget=20)
        rows=prepare(fixture['entries'],memory,Auditor(client),RevisionRefresher(client))
        packet={'schema':'wikidebia-restored-mia-review-1','memory_sha256':sha(a.memory.read_bytes()),
                'fixture_sha256':sha(a.fixture.read_bytes()),'cases':rows,'auto_publish':False}
        raw=json.dumps(packet,ensure_ascii=False,separators=(',',':')).encode()
        a.out.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['openssl','cms','-encrypt','-binary','-aes-256-gcm','-outform','DER',
                        '-recip',str(a.certificate),'-out',str(a.out)],input=raw,check=True)
        print(json.dumps({'cases':len(rows),'candidates':sum(len(r['candidates']) for r in rows),
                          'packet_sha256':sha(raw),'encrypted_sha256':sha(a.out.read_bytes()),
                          'network_requests':client.requests,'decoded_network_body_bytes':client.network_body_bytes,
                          'elapsed_seconds':round(time.monotonic()-started,3),'model_calls':0,'auto_publish':False},indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
