"""A bounded live metadata-read probe of existing FR/EN snapshot pages.

No synthetic edit is made to any wiki. Re-read a small number of pages with
known author and MIA URL and compare key bibliographic facts at unchanged
revisions. Never publish the source text or mutate the retained index.
"""
import json
from collections import Counter
from pathlib import Path
import tempfile
from datetime import datetime,timezone
from wikirouge_incremental import open_current,field_map,fetch_pages,metadata_row,utc_second
from navigation import canonical_mia
from pilot_full import PilotClient

def main():
    with open_current() as i,tempfile.TemporaryDirectory() as tmp:
        mapping=field_map(i.db);c=PilotClient(Path(tmp),budget=16)
        results=[]
        for lang in ('fr','en'):
            ids=[r[0] for r in i.db.execute(
                """SELECT DISTINCT p.id FROM pages p JOIN source_urls u USING(lang,id)
                   WHERE p.lang=? AND p.status='metadata' AND u.mia_key IS NOT NULL
                   AND EXISTS (SELECT 1 FROM fields f WHERE f.lang=p.lang AND f.id=p.id AND f.field='author' AND f.value!='')
                   ORDER BY p.id LIMIT 2""",(lang,))]
            pages=fetch_pages(c,lang,ids,[])
            for pid,p in pages.items():
                old=i.page(lang,pid)
                row=metadata_row(lang,p,utc_second(datetime.now(timezone.utc)),mapping)
                if row['kind']!='upsert':
                    results.append({'lang':lang,'id':pid,'status':row['reason']});continue
                urls={v for f in row['fields'] if f[4]=='mia_url' and (v:=canonical_mia(f[-1]))}
                authors={f[-1] for f in row['fields'] if f[4]=='author' and f[-1]}
                old_links={(r[0],r[1]) for r in i.db.execute('SELECT target_lang,target_title FROM native_links WHERE lang=? AND id=?',(lang,pid))}
                checks={'revision':row['page'][5]==old['revision'],'title':row['page'][2]==old['title'],
                        'author':authors==({old['authors']} if old['authors'] else set()),
                        'mia_urls':urls==set(old['mia_urls']),
                        'links':{(r[2],r[3]) for r in row['native_links']}==old_links,
                        'metadata_status':row['page'][9]=='metadata'}
                results.append({'lang':lang,'id':pid,'status':'matches' if all(checks.values()) else 'difference',
                                'checks':checks,'old_revision':old['revision'],'observed_revision':row['page'][5],
                                'observed_revision_sha256':row['revision_sha256']})
        print(json.dumps({'schema':'wikidebia-index-live-probe-1','results':results,
                          'summary':dict(Counter(r['status'] for r in results)),
                          'requests':c.requests,'decoded_network_body_bytes':c.network_body_bytes,'model_calls':0},indent=2))
        return int(len(results)!=4 or any(r['status']!='matches' for r in results))

if __name__=='__main__':
    raise SystemExit(main())
