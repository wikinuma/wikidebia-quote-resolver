"""Incremental bibliographic index updates, read-only public MediaWiki API.

The original verified ZIP never changes. A complete delta records only touched
metadata, explicit tombstones, bounded recentchanges evidence and checkpoints.
Derived native pairs are rebuilt locally; no full-text corpus is persisted.
"""
from collections import Counter, defaultdict
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unicodedata
from urllib.parse import quote, urlsplit

from navigation import canonical_mia, mia_urls, norm, plain, templates
from pilot_full import PilotClient
from resolver import API, sha
from wikirouge_index import DEFAULT, LocalIndex, open_snapshot, title_key

SCHEMA = 'wikidebia-wikirouge-delta-1'
LANGS = ('fr', 'en')
CATEGORIES = ('Text', 'Texte', 'Subpage', 'Sous-page')
TEMPLATES = {'text', 'texte', 'subpage', 'sous page'}
HISTORY = DEFAULT.parent/'deltas/manifest.json'
MAX_EVENTS = 1000
MAX_TOUCHED = 200
BATCH = 5


def stamp(s):
    if not isinstance(s, str):
        raise ValueError('invalid_timestamp')
    d = datetime.fromisoformat(s.replace('Z', '+00:00'))
    if d.tzinfo is None or d.utcoffset() != timezone.utc.utcoffset(d):
        raise ValueError('timestamp_not_utc')
    return d


def utc_second(d):
    return d.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def exact_title(s):
    return ' '.join(unicodedata.normalize('NFC', s.replace('_', ' ')).split())


def url_info(value):
    try:
        p = urlsplit(value)
        if (p.scheme not in ('http','https') or not p.hostname or p.username
                or p.password or any(c.isspace() or ord(c)<32 for c in value)):
            return None, None
        host = p.hostname.lower()
        key = None
        if host in ('www.marxists.org','marxists.org') and p.port in (None,80,443):
            key = 'marxists.org'+(p.path or '/')+('?' + p.query if p.query else '')
        return host,key
    except ValueError:
        return None,None


def read_api(client, lang, **params):
    raw, final = client.get(API[lang], {'format':'json','formatversion':2,**params})
    p,q = urlsplit(final),urlsplit(API[lang])
    if (p.scheme,p.netloc,p.path)!=(q.scheme,q.netloc,q.path):
        raise ValueError('unexpected_api_endpoint')
    data=json.loads(raw)
    if 'error' in data or 'warnings' in data:
        raise ValueError('mediawiki_error_or_warning')
    return data


def base_checkpoints(db):
    source = json.loads(db.execute("SELECT value FROM info WHERE key='source_started_at'").fetchone()[0])
    # Round down: include the entire second in which the original export began.
    start = utc_second(stamp(source))
    return {lang:start for lang in LANGS}


def recent(client,lang,start,limit=MAX_EVENTS):
    # An oldest retained event older than the checkpoint is conservative
    # evidence that the public recentchanges retention window reaches it.
    oldest=read_api(client,lang,action='query',list='recentchanges',
                    rcdir='newer',rclimit=1,rcprop='timestamp',curtimestamp=1)
    end=oldest['curtimestamp']
    if stamp(end)<stamp(start):
        raise ValueError('checkpoint_in_future')
    first=oldest['query']['recentchanges']
    if not first or stamp(first[0]['timestamp'])>stamp(start):
        raise ValueError('recentchanges_coverage_not_established')
    params=dict(action='query',list='recentchanges',rcdir='newer',
                rcstart=start,rcend=end,rctype='edit|new|log',
                rcprop='title|ids|timestamp|flags|loginfo',rclimit=500)
    events=[]; continuations=set(); more={}
    while True:
        data=read_api(client,lang,**{**params,**more})
        rows=data['query']['recentchanges']
        if not isinstance(rows,list):
            raise ValueError('invalid_recentchanges')
        for r in rows:
            if not stamp(start)<=stamp(r['timestamp'])<=stamp(end):
                raise ValueError('event_outside_window')
            if type(r.get('rcid')) is not int or r['rcid']<=0 or type(r.get('ns')) is not int or not isinstance(r.get('title'),str):
                raise ValueError('hidden_or_invalid_event')
            rec={k:r[k] for k in ('rcid','timestamp','type','ns','title')}
            for k in ('pageid','revid','old_revid','logid','logtype','logaction'):
                if k in r: rec[k]=r[k]
            if r.get('logtype')=='move':
                move=r.get('logparams',{})
                if type(move.get('target_ns')) is not int or not isinstance(move.get('target_title'),str):
                    raise ValueError('unresolved_move_target')
                rec['target_ns']=move['target_ns'];rec['target_title']=move['target_title']
            events.append(rec)
        if len(events)>limit:
            raise ValueError('recentchanges_event_budget_exceeded')
        more=data.get('continue',{})
        if not more: break
        key=json.dumps(more,sort_keys=True)
        if key in continuations or len(continuations)>=20:
            raise ValueError('recentchanges_continuation_incomplete')
        continuations.add(key)
    if len({r['rcid'] for r in events})!=len(events):
        raise ValueError('duplicate_recentchange')
    # Template/module changes can alter category membership and interlanguage
    # links without a revision on the text itself. Do not silently advance.
    if any(r['ns'] in (10,828) or r.get('target_ns') in (10,828) for r in events):
        raise ValueError('template_or_module_change_requires_impact_review')
    return end,events,first[0]['timestamp']


def affected(db,lang,events):
    ids=set(); titles=set()
    for e in events:
        if e['ns']!=0 and e.get('target_ns')!=0:
            continue
        if e['type'] not in ('edit','new','log'):
            raise ValueError('unknown_event_type')
        pid=e.get('pageid')
        if type(pid) is int and pid>0:
            ids.add(pid)
        elif e['type']!='log':
            raise ValueError('missing_edit_page_id')
        if e['type']=='log':
            # Restrict to relevant content lifecycle logs, but conservatively
            # re-read other main namespace log targets rather than ignore them.
            if e['ns']==0: titles.add(e['title'])
            if e.get('target_ns')==0: titles.add(e['target_title'])
    for title in titles:
        ids.update(r[0] for r in db.execute(
            'SELECT id FROM pages WHERE lang=? AND title_key=?',(lang,exact_title(title))))
    if len(ids)+len(titles)>MAX_TOUCHED:
        raise ValueError('affected_page_budget_exceeded')
    return sorted(ids),sorted(titles)


def fetch_batch(client,lang,kind,values):
    params=dict(action='query',prop='info|revisions|langlinks|categories',
                rvprop='ids|timestamp|sha1|content',rvslots='main',
                lllimit='max',cllimit='max',
                clcategories='|'.join('Category:'+x for x in CATEGORIES))
    params[kind]='|'.join(map(str,values))
    pages={};more={};seen=set()
    while True:
        data=read_api(client,lang,**{**params,**more})
        raw=data['query']['pages']
        if not isinstance(raw,list):
            raise ValueError('invalid_page_batch')
        for p in raw:
            if p.get('invalid') or 'title' not in p or type(p.get('ns')) is not int:
                # A deleted page requested by pageid can be returned without
                # title/ns by MediaWiki. Its positive id is sufficient only
                # when the API explicitly marks it missing.
                if kind!='pageids' or p.get('missing') is not True or type(p.get('pageid')) is not int or p['pageid']<=0:
                    raise ValueError('invalid_page_response')
            key = p.get('pageid') if kind=='pageids' else exact_title(p['title'])
            if key not in pages:
                pages[key]=p
            else:
                existing=pages[key]
                for field in ('pageid','title','ns','missing','redirect','lastrevid','revisions'):
                    if field in p and field in existing and p[field]!=existing[field]:
                        raise ValueError('page_changed_during_continuation')
                    if field in p: existing[field]=p[field]
                for field in ('langlinks','categories'):
                    if field in p:
                        for x in p[field]:
                            if x not in existing.setdefault(field,[]): existing[field].append(x)
        more=data.get('continue',{})
        if not more: break
        key=json.dumps(more,sort_keys=True)
        if key in seen or len(seen)>=20:
            raise ValueError('metadata_continuation_incomplete')
        seen.add(key)
    want=set(values if kind=='pageids' else map(exact_title,values))
    if set(pages)!=want:
        raise ValueError('requested_pages_not_accounted_for')
    return pages.values()


def fetch_pages(client,lang,ids,titles):
    pages={}
    for kind,values in (('pageids',ids),('titles',titles)):
        for i in range(0,len(values),BATCH):
            for p in fetch_batch(client,lang,kind,values[i:i+BATCH]):
                pid=p.get('pageid')
                if type(pid) is not int or pid<=0:
                    if p.get('missing') is True and kind=='titles': continue
                    raise ValueError('missing_page_identity')
                if pid in pages and pages[pid]!=p:
                    # The second query can carry a different set of ancillary
                    # info fields; compare all content-bearing properties.
                    keys=('title','ns','missing','redirect','lastrevid','revisions','langlinks','categories')
                    if any(p.get(k)!=pages[pid].get(k) for k in keys):
                        raise ValueError('page_changed_between_queries')
                pages[pid]=p
    return pages


def field_map(db):
    by=defaultdict(set)
    for lang,param,field in db.execute('SELECT DISTINCT lang,parameter,field FROM fields WHERE parameter IS NOT NULL'):
        by[lang,norm(param)].add(field)
    for lang in LANGS:
        for p in ('author','authors','auteur','auteurs','auteur(s)'):
            by[lang,norm(p)].add('author')
        for p in ('MIA URL',): by[lang,norm(p)].add('mia_url')
        for p in ('Date of writing','Date écriture',"Date d'écriture"):
            by[lang,norm(p)].add('date')
    return by


def metadata_row(lang,p,end,mapping):
    pid=p['pageid']
    base={'lang':lang,'id':pid}
    if p.get('missing') is True:
        return {**base,'kind':'tombstone','reason':'page_missing'}
    if p['ns']!=0 or 'redirect' in p:
        return {**base,'kind':'tombstone','reason':'not_main_text'}
    revs=p.get('revisions',[])
    if len(revs)!=1:
        raise ValueError('missing_latest_revision')
    rev=revs[0]; rid=rev['revid']
    if (type(rid) is not int or rid<=0 or rid!=p.get('lastrevid')
            or stamp(rev['timestamp'])>stamp(end)):
        raise ValueError('page_revision_after_cutoff_or_inconsistent')
    raw=rev['slots']['main']['content']
    if not isinstance(raw,str):
        raise ValueError('missing_revision_content')
    cats=sorted({c['title'].split(':',1)[-1] for c in p.get('categories',[])})
    if not set(cats)&set(CATEGORIES):
        return {**base,'kind':'tombstone','reason':'not_in_text_categories',
                'title':p['title'],'revision':rid,'revision_sha256':sha(raw.encode())}
    title=p['title'];issues=[];fs=[];sources={}
    ts=list(templates(raw,TEMPLATES))
    names=[str(t.name).strip() for t in ts]
    if len(ts)!=1: issues.append('missing_or_multiple_metadata_templates')
    for i,t in enumerate(ts):
        counts=Counter()
        for param in t.params:
            pname=str(param.name).strip()
            mapped=mapping.get((lang,norm(pname)),set())
            if not mapped:
                continue
            if len(mapped)!=1:
                issues.append('ambiguous_metadata_parameter:'+pname[:120])
                continue
            field=next(iter(mapped))
            value=str(param.value).strip()
            if field!='mia_url':
                value=plain(param.value) or param.value.strip_code(keep_template_params=True).strip()
            if len(value)>5000:
                issues.append('oversize_metadata_field:'+field)
                continue
            ordinal=counts[field];counts[field]+=1
            fs.append([lang,pid,i,names[i],field,ordinal,pname,value])
            if field=='mia_url' and value:
                for v in sorted({value,*mia_urls(value)}):
                    host,key=url_info(v);sources[v]=[lang,pid,v,host,key]
        if any(counts[f]>1 for f in ('author','date','mia_url')):
            issues.append('multiple_identity_fields')
    links=[]
    for ll in p.get('langlinks',[]):
        if ll['lang'] not in LANGS or ll['lang']==lang: continue
        t=ll.get('title',ll.get('*'))
        if not isinstance(t,str) or not t: raise ValueError('invalid_langlink')
        links.append([lang,pid,ll['lang'],t,None])
    if len({tuple(r[:4]) for r in links})!=len(links):
        raise ValueError('duplicate_native_link')
    status='metadata' if not issues else 'metadata_ambiguous'
    return {**base,'kind':'upsert','revision_sha256':sha(raw.encode()),'categories':cats,
            'page':[lang,pid,title,exact_title(title),title_key(title),rid,rev['timestamp'],
                    'https://wikirouge.net/texts/'+lang+'/'+quote(title.replace(' ','_'),safe='/()'),
                    title.split('/',1)[0],status,json.dumps(names,ensure_ascii=False),json.dumps(issues,ensure_ascii=False)],
            'fields':fs,'source_urls':list(sources.values()),'native_links':links}


def rebuild_pairs(db):
    # Preserve exactly the original builder's link resolution and ambiguity rule.
    titles=defaultdict(list);statuses={}
    for lang,pid,key,status in db.execute('SELECT lang,id,title_key,status FROM pages'):
        titles[lang,key].append(pid);statuses[lang,pid]=status
    links=defaultdict(set)
    rows=db.execute('SELECT lang,id,target_lang,target_title FROM native_links').fetchall()
    changes=[]
    for lang,pid,tl,title in rows:
        matches=titles[tl,exact_title(title)]
        tid=matches[0] if len(matches)==1 else None
        changes.append((tid,lang,pid,tl,title))
        if tid is not None and (lang,tl) in (('fr','en'),('en','fr')):
            links[(pid,tid) if lang=='fr' else (tid,pid)].add(lang)
    db.executemany('UPDATE native_links SET target_id=? WHERE lang=? AND id=? AND target_lang=? AND target_title=?',changes)
    fc=Counter(f for f,e in links);ec=Counter(e for f,e in links)
    db.execute('DELETE FROM pairs')
    db.executemany('INSERT INTO pairs VALUES(?,?,?,?,?,?)',
        [(f,e,','.join(sorted(ev)),fc[f],ec[e],
          int(statuses['fr',f]!='metadata' or statuses['en',e]!='metadata'))
         for (f,e),ev in sorted(links.items())])
    for k,v in [('pages',len(statuses)),('pairs',len(links)),
                ('distinct_fr_linked',len(fc)),('distinct_en_linked',len(ec))]:
        db.execute('UPDATE info SET value=? WHERE key=?',(json.dumps(v),k))


def apply_delta(db,delta,base,previous,checkpoints):
    if (delta.get('schema')!=SCHEMA or delta.get('status')!='complete'
            or delta.get('base_snapshot_sha256')!=base or delta.get('previous_sha256')!=previous
            or delta.get('from')!=checkpoints or delta.get('auto_publish') is not False
            or set(delta.get('until',{}))!=set(LANGS)):
        raise ValueError('delta_chain_mismatch')
    for lang in LANGS:
        if stamp(delta['until'][lang])<stamp(checkpoints[lang]):
            raise ValueError('checkpoint_regression')
    seen=set()
    with db:
        for r in delta['updates']:
            lang,pid=r['lang'],r['id']
            if lang not in LANGS or type(pid) is not int or pid<=0 or (lang,pid) in seen:
                raise ValueError('invalid_delta_page')
            seen.add((lang,pid))
            for table in ('fields','source_urls','native_links'):
                db.execute('DELETE FROM '+table+' WHERE lang=? AND id=?',(lang,pid))
            db.execute('DELETE FROM pages WHERE lang=? AND id=?',(lang,pid))
            if r['kind']=='tombstone': continue
            if r['kind']!='upsert' or r['page'][:2]!=[lang,pid]:
                raise ValueError('invalid_upsert')
            db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',r['page'])
            for table,n in (('fields',8),('source_urls',5),('native_links',5)):
                for row in r[table]:
                    if len(row)!=n or row[:2]!=[lang,pid]:
                        raise ValueError('delta_child_identity_mismatch')
                    db.execute('INSERT INTO '+table+' VALUES('+','.join('?' for _ in range(n))+')',row)
        if seen: rebuild_pairs(db)
        if db.execute('PRAGMA foreign_key_check').fetchall() or db.execute('PRAGMA quick_check').fetchone()[0]!='ok':
            raise ValueError('updated_index_integrity_failure')
    return delta['until']


@contextmanager
def open_updated(manifest=DEFAULT,deltas=()):
    """Read verified base plus ordered complete deltas into a temporary DB."""
    with open_snapshot(manifest) as source, closing(sqlite3.connect(':memory:')) as db:
        source.db.backup(db);db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        base=previous=source.fingerprint
        checkpoints=base_checkpoints(db)
        for path in deltas:
            raw=Path(path).read_bytes()
            checkpoints=apply_delta(db,json.loads(raw),base,previous,checkpoints)
            previous=sha(raw)
        index=LocalIndex(db,previous)
        index.base_fingerprint=base;index.checkpoints=checkpoints
        yield index


def history_paths(history,manifest=DEFAULT):
    history=Path(history)
    h=json.loads(history.read_bytes())
    base=json.loads(Path(manifest).read_bytes())['database']['sha256']
    if h.get('schema')!='wikidebia-wikirouge-delta-history-1' or h.get('base_snapshot_sha256')!=base:
        raise ValueError('history_base_mismatch')
    paths=[];seen=set()
    for d in h['deltas']:
        name=d['file']
        if not isinstance(name,str) or Path(name).name!=name or name in seen:
            raise ValueError('invalid_history_filename')
        seen.add(name)
        path=history.parent/name
        if sha(path.read_bytes())!=d['sha256']:
            raise ValueError('history_delta_hash_mismatch')
        paths.append(path)
    return paths


@contextmanager
def open_current(manifest=DEFAULT,history=HISTORY):
    with open_updated(manifest,history_paths(history,manifest)) as index:
        yield index


def refresh(index,client):
    started=time.monotonic()
    until={};events={};coverage={};updates=[];mapping=field_map(index.db)
    for lang in LANGS:
        end,rows,oldest=recent(client,lang,index.checkpoints[lang])
        until[lang]=end;events[lang]=rows;coverage[lang]=oldest
        ids,titles=affected(index.db,lang,rows)
        pages=fetch_pages(client,lang,ids,titles)
        for pid,p in sorted(pages.items()):
            updates.append(metadata_row(lang,p,end,mapping))
    return {'schema':SCHEMA,'status':'complete','base_snapshot_sha256':index.base_fingerprint,
            'previous_sha256':index.fingerprint,'from':index.checkpoints,'until':until,
            'oldest_retained_event':coverage,'events':events,'updates':updates,
            'request_receipts':[{'request_url':e['request_url'],'response_sha256':e['sha256'],'bytes':e['bytes']}
                                for e in client.events if e.get('status')=='ok'],
            'summary':{'recentchange_events':sum(map(len,events.values())),
                       'page_updates':len(updates),'kinds':dict(Counter(r['kind'] for r in updates)),
                       'network_requests':client.requests,'decoded_network_body_bytes':client.network_body_bytes,
                       'elapsed_seconds':round(time.monotonic()-started,3),'model_calls':0},
            'scope':'Direct recentchanges; candidate metadata only. Indirect changes outside the feed and asynchronous link/category refresh are not a complete snapshot guarantee.',
            'auto_publish':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,default=DEFAULT)
    p.add_argument('--delta',action='append',type=Path,default=[])
    p.add_argument('--history',type=Path,help='verify and use the committed ordered delta history')
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.out.exists(): raise FileExistsError(a.out)
    if a.history and a.delta: p.error('choose --history or --delta, not both')
    paths=history_paths(a.history,a.manifest) if a.history else a.delta
    with tempfile.TemporaryDirectory(prefix='wikirouge-update-') as tmp, open_updated(a.manifest,paths) as index:
        client=PilotClient(Path(tmp),budget=250)
        delta=refresh(index,client)
        apply_delta(index.db,delta,index.base_fingerprint,index.fingerprint,index.checkpoints)
        delta['summary']['resulting_pages']=index.db.execute('SELECT count(*) FROM pages').fetchone()[0]
        delta['summary']['resulting_pairs']=index.db.execute('SELECT count(*) FROM pairs').fetchone()[0]
    a.out.parent.mkdir(parents=True,exist_ok=True)
    data=(json.dumps(delta,ensure_ascii=True,sort_keys=True,indent=2)+'\n').encode()
    temp=a.out.with_suffix('.tmp')
    temp.write_bytes(data);temp.replace(a.out)
    print(json.dumps({'summary':delta['summary'],'from':delta['from'],'until':delta['until'],
                      'oldest_retained_event':delta['oldest_retained_event'],'delta_sha256':sha(data)},indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
