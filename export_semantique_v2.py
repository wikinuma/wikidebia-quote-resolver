#!/usr/bin/env python3
"""Public Wikirouge bibliography export: SMW, native language links, external links.

No credentials, full texts, raw templates, or wiki writes. Resumable and sequential.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import gzip
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import quote, urlsplit
import requests

SCHEMA='wikirouge-semantic-metadata-2'
CATS={'fr':('Texte','Text','Sous-page','Subpage'),'en':('Text','Subpage')}
FIELDS={'fr':{'author':'Auteur','date':'Date écriture','collection':'Recueil','availability':'Indisponible','publication':'Publication','mia_url':'MIA URL'},
        'en':{'author':'Author','date':'Date of writing','collection':'Collection','availability':'Nonavailable','publication':'Publication','mia_url':'MIA URL'}}
EXPECTED={
    ('fr','Manifeste du parti communiste'):'/francais/marx/works/1847/00/kmfe18470000.htm',
    ('fr','Anti-Dühring'):'/francais/engels/works/1878/06/fe18780611.htm',
    ('en','Manifesto of the Communist Party'):'/archive/marx/works/1848/communist-manifesto/',
    ('en','Anti-Dühring'):'/archive/marx/works/1877/anti-duhring/index.htm',
    ('en','The Questions of Wendelin Thomas'):'/archive/trotsky/1937/07/thomas.htm'}
PROBES={lang:[t for l,t in EXPECTED if l==lang]+[sub] for lang,sub in (
    ('fr','Anti-Dühring/03/Partie III. Socialisme'),('en','Anti-Dühring/03/Part III. Socialism'))}
HOSTS={'marxists.org','www.marxists.org','marxists.info','www.marxists.info'}
PAGE_PROPS='info|revisions|langlinks|extlinks'
MAX_RESPONSE=16*1024*1024

class ExportError(RuntimeError): pass

def title_key(s): return ' '.join(s.replace('_',' ').split())
def now(): return datetime.now(timezone.utc).isoformat()
def json_key(value): return json.dumps(value,ensure_ascii=False,sort_keys=True)

def mia_url(value):
    if isinstance(value,dict): value=value.get('url') or value.get('*') or value.get('fullurl') or value.get('value') or value.get('fulltext')
    if not isinstance(value,str) or any(c.isspace() or ord(c)<32 for c in value): return None
    try:
        p=urlsplit(value)
        if p.scheme in ('https','http') and p.hostname in HOSTS and p.username is None and p.password is None and p.port in (None,80,443):
            return value
    except ValueError: pass
    return None

def mia_urls(values): return sorted({u for v in values if (u:=mia_url(v))})

class Client:
    def __init__(self,delay=2,budget=6000):
        self.delay=delay; self.budget=budget; self.calls=0; self.bytes=0; self.last=0
        self.session=requests.Session()
        self.session.trust_env=False
        self.session.headers['User-Agent']='WikidebiaMetadataOnly/2.0 (public bibliographic metadata; sequential; read only)'
    def close(self): self.session.close()
    def api(self,lang,**params):
        if lang not in CATS: raise ExportError('invalid_language')
        action=params.get('action','query')
        # Only these three request shapes are permitted. No login or content API.
        common={'action','continue'}
        if action=='ask':
            if set(params)-{'action','query'} or not isinstance(params.get('query'),str):
                raise ExportError('forbidden_semantic_request')
        elif action=='query' and params.get('list')=='categorymembers':
            if set(params)-common-{'list','cmtitle','cmnamespace','cmtype','cmlimit','cmcontinue'}:
                raise ExportError('forbidden_inventory_request')
        elif action=='query' and params.get('prop')==PAGE_PROPS and params.get('rvprop')=='ids|timestamp':
            if set(params)-common-{'prop','rvprop','titles','pageids','lllang','lllimit','ellimit','llcontinue','elcontinue','rvcontinue'}:
                raise ExportError('forbidden_page_request')
        else: raise ExportError('forbidden_action_or_content_request')
        data={'action':'query','format':'json','formatversion':2,'maxlag':5,**params}
        endpoint=f'https://wikirouge.net/texts/{lang}/w/api.php'
        for attempt in range(4):
            if self.calls>=self.budget: raise ExportError('request_budget_exhausted; relancer pour reprendre')
            time.sleep(max(0,self.delay-(time.monotonic()-self.last)))
            self.last=time.monotonic(); self.calls+=1
            pause=30*2**attempt
            try:
                # POST for long semantic queries is read-only; avoid URL-length limits.
                is_post=action=='ask'
                with self.session.request('POST' if is_post else 'GET',endpoint,
                        **({'data':data} if is_post else {'params':data}),timeout=(15,90),stream=True,allow_redirects=False) as response:
                    if response.status_code in (429,502,503,504):
                        retry=response.headers.get('Retry-After','')
                        if retry.isdigit(): pause=max(pause,int(retry))
                    elif response.status_code!=200:
                        raise ExportError(f'HTTP {response.status_code} ({lang}, {action})')
                    else:
                        buf=bytearray()
                        for part in response.iter_content(65536):
                            buf.extend(part); self.bytes+=len(part)
                            if len(buf)>MAX_RESPONSE: raise ExportError('response_too_large')
                        try: obj=json.loads(buf)
                        except ValueError: raise ExportError('invalid_json_response') from None
                        if not isinstance(obj,dict): raise ExportError('invalid_api_response')
                        if 'error' in obj:
                            code=str(obj['error'].get('code','unknown'))
                            if code not in ('maxlag','ratelimited','readonly'): raise ExportError(f'MediaWiki {code} ({lang}, {action})')
                        elif obj.get('warnings'):
                            raise ExportError(f'API warning ({lang}, {action}); try smaller --batch')
                        else: return obj
            except requests.exceptions.SSLError: raise ExportError('TLS verification failed') from None
            except requests.exceptions.RequestException: pass
            if attempt==3 or pause>600: break
            print(f'{lang}: serveur indisponible; pause {pause}s',file=sys.stderr,flush=True)
            time.sleep(pause)
        raise ExportError('server_unavailable_after_retries')

def continuation(obj,seen,allowed,limit=40):
    nxt=obj.get('continue',{})
    if not isinstance(nxt,dict) or set(nxt)-allowed: raise ExportError('invalid_continuation')
    if nxt:
        key=json_key(nxt)
        if key in seen or len(seen)>=limit: raise ExportError('repeated_or_excessive_continuation')
        seen.add(key)
    return nxt

def pageinfo(client,lang,*,titles=None,ids=None):
    if (titles is None)==(ids is None): raise ValueError('one selector required')
    selector={'titles':'|'.join(titles)} if titles is not None else {'pageids':'|'.join(map(str,ids))}
    pages={}; cont={}; seen=set()
    while True:
        obj=client.api(lang,prop=PAGE_PROPS,rvprop='ids|timestamp',lllang='en' if lang=='fr' else 'fr',
                       lllimit=500,ellimit=500,**selector,**cont)
        rows=obj.get('query',{}).get('pages')
        if not isinstance(rows,list): raise ExportError('invalid_pageinfo')
        for p in rows:
            if p.get('ns')!=0 or 'missing' in p or 'invalid' in p or type(p.get('pageid')) is not int: continue
            pid=p['pageid']; title=p['title']
            if not isinstance(title,str): raise ExportError('invalid_title')
            r=pages.setdefault(pid,{'language':lang,'title':title,'pageid':pid,
                     'revision':None,'revision_timestamp':None,'lastrevid':p.get('lastrevid'),
                     'redirect':bool(p.get('redirect',False)),'native_langlinks':[],
                     'external_mia_urls':[],'url':f'https://wikirouge.net/texts/{lang}/'+quote(title.replace(' ','_'),safe='/()'),
                     'work_root_candidate':title.split('/',1)[0]})
            if r['title']!=title or (r['lastrevid'] is not None and p.get('lastrevid',r['lastrevid'])!=r['lastrevid']):
                raise ExportError('page_changed_during_batch; retry')
            revs=p.get('revisions',[])
            for rev in revs:
                if 'slots' in rev or 'content' in rev or '*' in rev: raise ExportError('unexpected_revision_content')
                rid=rev.get('revid')
                if type(rid) is not int or (r['revision'] is not None and r['revision']!=rid):
                    raise ExportError('invalid_or_changed_revision')
                r['revision']=rid; r['revision_timestamp']=rev.get('timestamp')
            for link in p.get('langlinks',[]):
                item={'language':link.get('lang'),'title':link.get('title',link.get('*'))}
                if item['language'] in ('fr','en') and isinstance(item['title'],str) and item not in r['native_langlinks']: r['native_langlinks'].append(item)
            r['external_mia_urls']=sorted(set(r['external_mia_urls'])|set(mia_urls(p.get('extlinks',[]))))
        cont=continuation(obj,seen,{'continue','llcontinue','elcontinue','rvcontinue'})
        if not cont: break
    if ids is not None and set(pages)!=set(ids): raise ExportError('inventory_changed_or_missing_page; no silent omission')
    for r in pages.values():
        if r['revision'] is None or (r['lastrevid'] is not None and r['lastrevid']!=r['revision']):
            raise ExportError('revision_not_confirmed')
        r.pop('lastrevid',None)
        r['native_langlinks'].sort(key=lambda x:(x['language'],x['title']))
    return sorted(pages.values(),key=lambda r:r['pageid'])

def ask(client,lang,titles):
    safe=[t for t in titles if t and '::' not in t and not any(c in t for c in '[]|{}<>\n\r')]
    if not safe: return {}
    allowed={title_key(t) for t in safe}
    q=' OR '.join('[['+t+']]' for t in safe)+'|'+ '|'.join('?'+name for name in FIELDS[lang].values())+'|limit='+str(len(safe))
    out={}; offset=0; seen=set()
    while True:
        obj=client.api(lang,action='ask',query=q+'|offset='+str(offset))
        rows=obj.get('query',{}).get('results')
        if not isinstance(rows,(dict,list)): raise ExportError('invalid_smw_results')
        for r in rows.values() if isinstance(rows,dict) else rows:
            if not isinstance(r,dict) or r.get('namespace')!=0 or r.get('exists') in (False,'0',0): continue
            key=title_key(r.get('fulltext',''))
            if key not in allowed: continue
            p=r.get('printouts',{})
            if not isinstance(p,dict): raise ExportError('invalid_smw_printouts')
            values={label:p.get(name,p.get(name.replace(' ','_'),[])) for label,name in FIELDS[lang].items()}
            if not all(isinstance(v,list) for v in values.values()): raise ExportError('invalid_smw_values')
            if key in out and out[key]!=values: raise ExportError('conflicting_smw_rows')
            out[key]=values
        nxt=obj.get('query-continue-offset')
        if nxt is None: break
        if type(nxt) is not int or nxt<=offset or nxt in seen or len(seen)>=10: raise ExportError('invalid_smw_continuation')
        seen.add(nxt); offset=nxt
    return out

def combine(pages,properties):
    for r in pages:
        values=properties.get(title_key(r['title']))
        r['semantic_properties']=values or {}
        indexed=mia_urls((values or {}).get('mia_url',[]))
        external=r['external_mia_urls']
        union=sorted(set(indexed)|set(external))
        r['mia_urls']=union
        r['mia_link_evidence']=[{'url':u,'indexed_mia_property':u in indexed,'external_link':u in external} for u in union]
        r['mia_url_status']=('indexed_property_with_other_candidates' if set(external)-set(indexed) else 'indexed_property') if indexed else (
            'multiple_external_link_candidates' if len(external)>1 else 'single_external_link_candidate' if external else 'not_found')
        r['status']='semantic_metadata' if values and any(values.values()) else 'no_semantic_values'
        r['issues']=[] if values is not None else ['subject_not_returned_by_smw']
        if len(union)>1: r['issues'].append('multiple_mia_urls_need_review')
    return pages

def batch_records(client,lang,*,titles=None,ids=None):
    pages=pageinfo(client,lang,titles=titles,ids=ids)
    return combine(pages,ask(client,lang,[p['title'] for p in pages]))

def mapping(rows):
    by={(r['language'],title_key(r['title'])):r for r in rows}; pairs={}; unmatched=[]
    for r in rows:
        lang=r['language']; other='fr' if lang=='en' else 'en'
        for ll in r['native_langlinks']:
            if ll['language']!=other: continue
            target=by.get((other,title_key(ll['title'])))
            if target is None:
                unmatched.append({'language':lang,'title':r['title'],'target_language':other,'target_title':ll['title']})
                continue
            f,e=(r,target) if lang=='fr' else (target,r)
            key=(f['title'],e['title'])
            pair=pairs.setdefault(key,{'fr_title':f['title'],'en_title':e['title'],'fr_revision':f['revision'],'en_revision':e['revision'],
                'fr_mia_urls':f['mia_urls'],'en_mia_urls':e['mia_urls'],'evidence':[],
                'status':'native_langlink_candidate_not_quote_verified'})
            if lang not in pair['evidence']: pair['evidence'].append(lang)
    fc=Counter(a for a,b in pairs); ec=Counter(b for a,b in pairs)
    for (a,b),p in pairs.items():
        p['evidence'].sort()
        p['reciprocal']=p['evidence']==['en','fr']
        p['ambiguous_in_export']=fc[a]>1 or ec[b]>1
    return [pairs[k] for k in sorted(pairs)],unmatched

def probe(client):
    rows=[]
    for lang,titles in PROBES.items(): rows+=batch_records(client,lang,titles=titles)
    by={(r['language'],r['title']):r for r in rows}; problems=[]
    for lang,titles in PROBES.items():
        for t in titles:
            if (lang,t) not in by: problems.append({'language':lang,'title':t,'missing':'page'})
    for key,path in EXPECTED.items():
        r=by.get(key,{})
        if not any(urlsplit(u).path==path for u in r.get('mia_urls',[])): problems.append({'language':key[0],'title':key[1],'missing':'known_mia_url'})
        for field in ('author','date'):
            if not r.get('semantic_properties',{}).get(field): problems.append({'language':key[0],'title':key[1],'missing':field})
    pairs,_=mapping(rows)
    for f,e in (('Manifeste du parti communiste','Manifesto of the Communist Party'),('Anti-Dühring','Anti-Dühring')):
        if not any(p['fr_title']==f and p['en_title']==e for p in pairs): problems.append({'missing':'known_interlanguage','fr_title':f,'en_title':e})
    return rows,problems

def db_open(path):
    db=sqlite3.connect(path,timeout=30)
    db.executescript('''CREATE TABLE IF NOT EXISTS export_schema(version TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pages(lang TEXT,id INTEGER,title TEXT,metadata TEXT,PRIMARY KEY(lang,id));
    CREATE TABLE IF NOT EXISTS categories(lang TEXT,name TEXT,cursor TEXT,done INTEGER,PRIMARY KEY(lang,name));
    CREATE TABLE IF NOT EXISTS members(lang TEXT,name TEXT,id INTEGER,PRIMARY KEY(lang,name,id));''')
    versions=db.execute('SELECT version FROM export_schema').fetchall()
    if not versions:
        if db.execute('SELECT COUNT(*) FROM pages').fetchone()[0]: raise ExportError('unversioned_checkpoint; use separate --out')
        db.execute('INSERT INTO export_schema VALUES(?)',(SCHEMA,))
    elif versions!=[(SCHEMA,)]: raise ExportError('incompatible_checkpoint; use separate --out')
    db.commit()
    return db

def category_batch(client,lang,name,limit=500,**cursor):
    obj=client.api(lang,list='categorymembers',cmtitle='Category:'+name,cmnamespace=0,cmtype='page',cmlimit=limit,**cursor)
    members=obj.get('query',{}).get('categorymembers')
    if not isinstance(members,list): raise ExportError('invalid_category_members')
    for r in members:
        if r.get('ns')!=0 or type(r.get('pageid')) is not int or not isinstance(r.get('title'),str): raise ExportError('invalid_category_member')
    return members,obj

def inventory(client,db):
    for lang,names in CATS.items():
        for name in names:
            with db: db.execute('INSERT OR IGNORE INTO categories VALUES(?,?,?,0)',(lang,name,'{}'))
            raw,done=db.execute('SELECT cursor,done FROM categories WHERE lang=? AND name=?',(lang,name)).fetchone()
            cursor=json.loads(raw); seen=set()
            if cursor: seen.add(json_key(cursor))
            while not done:
                members,obj=category_batch(client,lang,name,**cursor)
                nxt=continuation(obj,seen,{'continue','cmcontinue'},limit=10000)
                with db:
                    for r in members:
                        db.execute('INSERT OR IGNORE INTO pages VALUES(?,?,?,NULL)',(lang,r['pageid'],r['title']))
                        db.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(lang,name,r['pageid']))
                    done=not bool(nxt)
                    db.execute('UPDATE categories SET cursor=?,done=? WHERE lang=? AND name=?',(json_key(nxt),int(done),lang,name))
                cursor=nxt
                print(f'{lang} / {name}: '+str(db.execute('SELECT count(*) FROM members WHERE lang=? AND name=?',(lang,name)).fetchone()[0])+' pages recensees',flush=True)
        if not db.execute('SELECT COUNT(*) FROM members WHERE lang=? AND name IN (?,?)',(lang,'Texte','Text')).fetchone()[0]:
            raise ExportError(f'empty_text_inventory ({lang})')

def harvest(client,db,batch):
    for lang in CATS:
        total=db.execute('SELECT COUNT(*) FROM pages WHERE lang=?',(lang,)).fetchone()[0]
        while True:
            ids=[x[0] for x in db.execute('SELECT id FROM pages WHERE lang=? AND metadata IS NULL ORDER BY id LIMIT ?',(lang,batch))]
            if not ids: break
            rows=batch_records(client,lang,ids=ids)
            roots=[r for r in rows if '/' not in r['title'] and not r['redirect']]
            if len(roots)>=5 and not any(r['status']=='semantic_metadata' for r in roots):
                raise ExportError(f'zero_semantic_values_in_root_batch ({lang}); try smaller --batch')
            with db:
                for r in rows: db.execute('UPDATE pages SET metadata=?,title=? WHERE lang=? AND id=?',(json.dumps(r,ensure_ascii=False),r['title'],lang,r['pageid']))
            done=db.execute('SELECT COUNT(*) FROM pages WHERE lang=? AND metadata IS NOT NULL',(lang,)).fetchone()[0]
            print(f'{lang}: {done}/{total}; {client.calls} requetes; {client.bytes/1048576:.1f} Mio de reponses',flush=True)

def write_report(path,rows,client,mode,complete,**extra):
    pairs,unmatched=mapping(rows); stats={}
    for lang in CATS:
        group=[r for r in rows if r['language']==lang]
        stats[lang]={'pages':len(group),'statuses':dict(Counter(r['status'] for r in group)),
            'mia_urls_present':sum(bool(r['mia_urls']) for r in group),
            'mia_url_statuses':dict(Counter(r['mia_url_status'] for r in group)),
            'author_present':sum(bool(r['semantic_properties'].get('author')) for r in group),
            'date_present':sum(bool(r['semantic_properties'].get('date')) for r in group),
            'publication_present':sum(bool(r['semantic_properties'].get('publication')) for r in group),
            'native_links_present':sum(bool(r['native_langlinks']) for r in group)}
    info={'schema':SCHEMA,'mode':mode,'generated_at':now(),'export_complete_for_declared_scope':complete,
          'scope':'public category inventory, indexed semantic properties, native language links and MIA external-link candidates; not a simultaneous snapshot; no article content or raw template fields',
          'stats':stats,'mapping_candidates_count':len(pairs),
          'ambiguous_mapping_candidates_count':sum(p['ambiguous_in_export'] for p in pairs),
          'unmatched_langlinks_count':len(unmatched),
          'requests_this_invocation':client.calls,'bytes_this_invocation':client.bytes,**extra}
    tmp=path.with_name(path.name+'.tmp'); opener=gzip.open if path.suffix=='.gz' else open
    with opener(tmp,'wt',encoding='utf-8') as f:
        json.dump({**info,'mapping_candidates':pairs,'unmatched_langlinks':unmatched,'pages':rows},f,ensure_ascii=False,indent=None if path.suffix=='.gz' else 2)
        f.write('\n')
    os.replace(tmp,path)
    print(json.dumps(info,ensure_ascii=False,indent=2),flush=True); print('Fichier :',path.resolve(),flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True); mode.add_argument('--probe',action='store_true'); mode.add_argument('--full',action='store_true')
    p.add_argument('--out',type=Path,default=Path('export-semantique-v2'))
    p.add_argument('--delay',type=float,default=2); p.add_argument('--batch',type=int,default=25); p.add_argument('--budget',type=int,default=6000)
    a=p.parse_args()
    if not 2<=a.delay<=60 or not 1<=a.batch<=50 or not 1<=a.budget<=10000: p.error('invalid limits')
    os.umask(0o077); a.out.mkdir(mode=0o700,parents=True,exist_ok=True)
    with (a.out/'export.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise ExportError('another export is already running in this directory') from None
        client=Client(a.delay,a.budget)
        try:
            rows,problems=probe(client)
            write_report(a.out/'wikirouge-semantic-probe.json',rows,client,'probe',not problems,probe_problems=problems)
            if problems:
                print('Sondage insuffisant : ne pas lancer la collecte complete.',file=sys.stderr); return 2
            if a.probe: return 0
            db=db_open(a.out/'checkpoint-semantic-v2.sqlite3')
            try:
                inventory(client,db); harvest(client,db,a.batch)
                rows=[json.loads(x[0]) for x in db.execute('SELECT metadata FROM pages WHERE metadata IS NOT NULL ORDER BY lang,title,id')]
                pending=db.execute('SELECT COUNT(*) FROM pages WHERE metadata IS NULL').fetchone()[0]
                cats=[{'language':l,'category':n,'done':bool(d),'pages':count} for l,n,d,count in db.execute('SELECT c.lang,c.name,c.done,count(m.id) FROM categories c LEFT JOIN members m ON c.lang=m.lang AND c.name=m.name GROUP BY c.lang,c.name ORDER BY c.lang,c.name')]
                complete=not pending and len(cats)==sum(map(len,CATS.values())) and all(c['done'] for c in cats)
                write_report(a.out/'wikirouge-semantic-metadata.json.gz',rows,client,'full',complete,categories=cats,pending=pending,
                             missing_semantic_values_count=sum(r['status']=='no_semantic_values' for r in rows))
                return 0 if complete else 2
            finally: db.close()
        finally: client.close()

if __name__=='__main__':
    try: raise SystemExit(main())
    except KeyboardInterrupt:
        print('Interrompu. Reprendre avec la meme commande.',file=sys.stderr); raise SystemExit(130)
    except (ExportError,KeyError,ValueError,OSError,sqlite3.Error) as e:
        print('Export arrete: '+str(e),file=sys.stderr); raise SystemExit(1)
