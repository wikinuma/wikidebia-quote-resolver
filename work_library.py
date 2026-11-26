"""Persistent bibliography only: no quotation text, offsets or fingerprints.

Entries describe explicitly established bilingual work routes and independently
corroborated chapter hypotheses. All remain documentary leads, not evidence of
a particular quotation or reuse rights. Unknown/conflicting routes fall back.
"""
import argparse
import json
from pathlib import Path
import re
import sqlite3
import time

from bibliography import VERIFIED_AUTHORS, load_catalog
from chapter_routes import load_chapters
from navigation import author_check, canonical_mia, generic_work_name, safe_title
from pilot_full import wr_title
from resolver import ROOT, norm, sha, valid_url

SCHEMA = 1
LIVE_TTL = 30 * 86400
BIB_FIELDS = frozenset({
    'author', 'title', 'date', 'writing_date', 'publication_date', 'publication',
    'edition', 'publisher', 'translator', 'place', 'volume', 'issue', 'isbn',
    'language', 'source', 'mia_url', 'original_title', 'original_language',
})


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def year(values):
    years = {y for v in values if isinstance(v, str)
             for y in re.findall(r'(?<!\d)(?:1[5-9]\d{2}|20\d{2})(?!\d)', v)}
    return next(iter(years)) if len(years) == 1 else None


def labels(meta):
    return sorted({norm(meta.get(k, '')) for k in ('work', 'article')
                   if meta.get(k) and not generic_work_name(meta[k]) and norm(meta[k])})


def identity(meta):
    urls = meta.get('mia_urls', [])
    names = labels(meta)
    author = meta.get('authors', '')
    if (len(urls) != 1 or canonical_mia(urls[0]) != urls[0]
            or not urls[0].startswith('https://www.marxists.org/francais/')
            or not names or not norm(author)):
        return None
    return {'source_url': urls[0], 'titles': names, 'authors': author,
            'cited_titles': {k:meta[k] for k in ('work','article') if meta.get(k)},
            'citation_year': year([meta.get('date', '')])}


def metadata_projection(meta):
    out = {k: meta[k] for k in ('status', 'mia_urls', 'authors', 'revision', 'raw_sha256', 'date', 'dates')
           if k in meta}
    fields = meta.get('fields', {})
    out['fields'] = {k: v for k, v in fields.items() if k in BIB_FIELDS
                     and isinstance(v, list) and all(isinstance(t, str) and len(t) <= 4096 for t in v)}
    return out


def metadata_valid(m, lang):
    urls = m.get('mia_urls', [])
    return (m.get('status') in ('snapshot_metadata', 'raw_text_metadata')
            and isinstance(m.get('authors'), str) and bool(norm(m['authors']))
            and isinstance(urls, list) and len(urls) <= 1
            and all(canonical_mia(u) == u and (('/francais/' in u) == (lang == 'fr')) for u in urls)
            and type(m.get('revision')) is int and m['revision'] > 0)


def route_valid(entry, index_fingerprint):
    i = entry['identity']
    route = entry['route']
    fr, en = (entry['work_metadata'][l] for l in ('fr', 'en'))
    if (entry['index_fingerprint'] != index_fingerprint
            or not metadata_valid(fr, 'fr') or not metadata_valid(en, 'en')
            or author_check(i['authors'], fr['authors']) not in VERIFIED_AUTHORS
            or author_check(fr['authors'], en['authors']) not in VERIFIED_AUTHORS):
        return False
    for l in ('fr', 'en'):
        u, title = route[l+'_url'], route[l+'_title']
        if (not valid_url(u) or not u.startswith('https://wikirouge.net/texts/'+l+'/')
                or not safe_title(title) or generic_work_name(title)
                or wr_title(u, l) != title.replace('_', ' ')):
            return False
    fy, ey = (year(m.get('dates', []) + [m.get('date', '')]) for m in (fr, en))
    if fy and ey and fy != ey:
        return False
    if entry['route_method'] == 'local_wikirouge_snapshot':
        return (digest(index_fingerprint) and route.get('snapshot_sha256') == index_fingerprint
                and route.get('scope') in ('direct_native_link', 'parent_work_only_no_chapter_equivalence')
                and bool(route.get('native_link_directions'))
                and entry['source_evidence']['kind'] == 'unique_index_pair'
                and entry['source_evidence']['source_url'] == i['source_url'])
    return (route.get('reciprocal') is True
            and i['source_url'] in fr['mia_urls']
            and entry['source_evidence']['kind'] == 'reciprocal_live_metadata')


def project_route(nav, index_fingerprint):
    i = identity(nav.get('input', {}))
    if not i or nav.get('status') not in ('mia_route_candidate', 'wikirouge_route_candidate'):
        return None
    route = nav.get('route', {})
    meta = nav.get('work_metadata', {})
    if not all(k in route for k in ('fr_title','en_title','fr_url','en_url')) or not all(l in meta for l in ('fr','en')):
        return None
    method = nav.get('route_method')
    if not method:
        return None
    observed_titles = {norm(route['fr_title'])}
    if method == 'local_wikirouge_snapshot':
        if not digest(index_fingerprint):
            return None
        attempts = [a for a in nav.get('attempts', [])
                    if a.get('method') == method and a.get('status') == 'candidate'
                    and a.get('snapshot_sha256') == index_fingerprint]
        if len(attempts) != 1 or len(attempts[0].get('candidates', [])) != 1:
            return None
        candidate = attempts[0]['candidates'][0]
        if candidate.get('reasons'):
            return None
        source_page = candidate['source_page']
        if i['source_url'] not in source_page['mia_urls'] and i['source_url'] not in meta['fr']['mia_urls']:
            return None
        observed_titles.add(norm(source_page['title']))
        evidence = {'kind':'unique_index_pair','source_url':i['source_url'],
                    'source_page_title':source_page['title'],'source_page_revision':source_page['revision'],
                    'snapshot_sha256':index_fingerprint}
    else:
        evidence = {'kind':'reciprocal_live_metadata','source_url':i['source_url']}
    if not set(i['titles']) & observed_titles:
        return None
    entry = {'identity':i,'index_fingerprint':index_fingerprint,'route_method':method,
             'route':{k:route[k] for k in ('fr_title','en_title','fr_url','en_url','reciprocal',
                                          'native_link_directions','scope','snapshot_sha256') if k in route},
             'selected_fr_title':nav.get('selected_fr_title',route['fr_title']),
             'work_metadata':{l:metadata_projection(meta[l]) for l in ('fr','en')},
             'source_evidence':evidence}
    return entry if route_valid(entry,index_fingerprint) else None


def same_identity(a,b):
    return (a['source_url'] == b['source_url'] and a['titles'] == b['titles']
            and author_check(a['authors'],b['authors']) in VERIFIED_AUTHORS
            and (not a['citation_year'] or not b['citation_year'] or a['citation_year']==b['citation_year']))


class WorkLibrary:
    def __init__(self,path,clock=time.time):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path,timeout=30)
        self.clock=clock
        self._alignment_store=None
        self.stats={'work_hits':0,'work_misses':0,'work_learned':0,'chapter_hits':0,'chapter_misses':0,'chapter_learned':0}
        with self.db:
            v=self.db.execute('PRAGMA user_version').fetchone()[0]
            if v not in (0,SCHEMA):
                self.db.close()
                raise ValueError('unsupported_work_library_schema')
            self.db.execute('CREATE TABLE IF NOT EXISTS works (source_url TEXT NOT NULL, route_key TEXT PRIMARY KEY, entry TEXT NOT NULL, learned REAL NOT NULL)')
            self.db.execute('CREATE INDEX IF NOT EXISTS works_source ON works(source_url)')
            self.db.execute('CREATE TABLE IF NOT EXISTS chapters (fr_url TEXT NOT NULL, route_key TEXT PRIMARY KEY, entry TEXT NOT NULL)')
            self.db.execute('CREATE INDEX IF NOT EXISTS chapters_source ON chapters(fr_url)')
            self.db.execute('PRAGMA user_version=1')

    def __enter__(self):
        return self

    def __exit__(self,*exc):
        self.db.close()

    @property
    def alignment_store(self):
        if self._alignment_store is None:
            from alignment_store import AlignmentStore
            self._alignment_store=AlignmentStore(self.db)
        return self._alignment_store

    def learn(self,nav,index_fingerprint=None):
        entry=project_route(nav,index_fingerprint)
        if entry is None:
            return False
        raw=json_text(entry)
        with self.db:
            key=sha(raw.encode())
            existed=self.db.execute('SELECT 1 FROM works WHERE route_key=?',(key,)).fetchone()
            self.db.execute('INSERT OR REPLACE INTO works VALUES (?,?,?,?)',(entry['identity']['source_url'],key,raw,self.clock()))
        self.stats['work_learned']+=int(not existed)
        return True

    def lookup(self,meta,index_fingerprint=None):
        i=identity(meta)
        matches=[]
        if i:
            rows=self.db.execute('SELECT route_key,entry,learned FROM works WHERE source_url=?',(i['source_url'],))
            for key,raw,learned in rows:
                entry=json.loads(raw)
                if sha(raw.encode())!=key:
                    raise ValueError('work_library_integrity_mismatch')
                if (0 <= self.clock()-learned <= LIVE_TTL and same_identity(i,entry['identity'])
                        and route_valid(entry,index_fingerprint)):
                    matches.append(entry)
        # Several contradictory target routes are never silently resolved.
        # Same mapping with updated metadata is also conservative: re-discover.
        if len(matches)!=1:
            self.stats['work_misses']+=1
            return None
        self.stats['work_hits']+=1
        e=matches[0]
        return {'route':e['route'],'work_metadata':e['work_metadata'],
                'route_method':e['route_method'],'selected_fr_title':e['selected_fr_title'],
                'status':'wikirouge_route_candidate',
                'author_check':author_check(meta['authors'],e['work_metadata']['fr']['authors']),
                'work_library_evidence':e['source_evidence']}

    def remember_chapter(self,fr_root,en_root,pair,toc_receipts,probe,matcher_id):
        """Store only chapter-level URLs, headings and document evidence."""
        if probe.get('status')!='sampled_content_support' or not digest(matcher_id):
            return False
        e={'fr_root':fr_root,'en_root':en_root,
           'pair':{l:{k:pair[l][k] for k in ('url','heading','position','ordinary_position') if k in pair[l]}
                   for l in ('fr','en')},
           'toc_receipts':[{k:r[k] for k in ('requested_url','url','sha256')} for r in toc_receipts],
           'document_hashes':{l:probe[l+'_document_sha256'] for l in ('fr','en')},
           'matcher_id':matcher_id}
        if not chapter_valid(e):
            return False
        raw=json_text(e)
        with self.db:
            cur=self.db.execute('INSERT OR IGNORE INTO chapters VALUES (?,?,?)',
                                (e['pair']['fr']['url'],sha(raw.encode()),raw))
        self.stats['chapter_learned']+=cur.rowcount
        return True

    def chapter(self,fr_root,en_root,fr_url,matcher_id,fr_digest):
        matches=[]
        for key,raw in self.db.execute('SELECT route_key,entry FROM chapters WHERE fr_url=?',(fr_url,)):
            e=json.loads(raw)
            if sha(raw.encode())!=key or not chapter_valid(e):
                raise ValueError('chapter_library_integrity_mismatch')
            if (e['fr_root']==fr_root and e['en_root']==en_root and e['matcher_id']==matcher_id
                    and e['document_hashes']['fr']==fr_digest):
                matches.append(e)
        # This is a hypothesis only; caller must revalidate both TOCs and target hash.
        return matches[0] if len(matches)==1 else None

    def export(self):
        works=[]
        for key,raw,learned in self.db.execute('SELECT route_key,entry,learned FROM works ORDER BY route_key'):
            if sha(raw.encode())!=key:
                raise ValueError('work_library_integrity_mismatch')
            works.append({'learned_unix':learned,**json.loads(raw)})
        chapters=[]
        for key,raw in self.db.execute('SELECT route_key,entry FROM chapters ORDER BY route_key'):
            if sha(raw.encode())!=key:
                raise ValueError('chapter_library_integrity_mismatch')
            chapters.append(json.loads(raw))
        return {'schema':'wikidebia-work-library-1','works':works,'chapters':chapters,
                'manual_bibliographic_catalog':list(load_catalog(ROOT/'bibliographic-catalog.json')),
                'manual_chapter_catalog':list(load_chapters(ROOT/'chapter-catalog.json')),
                'research_notes':json.loads((ROOT/'data/works/research.json').read_text())['entries'],
                'rights':'unreviewed','auto_publish':False}


def chapter_valid(e):
    for lang in ('fr','en'):
        root=e[lang+'_root']; leaf=e['pair'][lang]['url']
        if (not all(isinstance(u,str) and canonical_mia(u)==u for u in (root,leaf))
                or root==leaf or any(('/francais/' in u)!=(lang=='fr') for u in (root,leaf))
                or not digest(e['document_hashes'][lang])):
            return False
        r=e['toc_receipts'][0 if lang=='fr' else 1]
        if r['requested_url']!=root or canonical_mia(r['url']) is None or not digest(r['sha256']):
            return False
    return digest(e['matcher_id']) and len(e['toc_receipts'])==2

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True)
    p.add_argument('--export',type=Path,required=True)
    a=p.parse_args()
    with WorkLibrary(a.database) as lib:
        data=lib.export()
    a.export.parent.mkdir(parents=True,exist_ok=True)
    tmp=a.export.with_suffix(a.export.suffix+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    tmp.replace(a.export)
    print(json.dumps({k:len(data[k]) for k in ('works','chapters','manual_bibliographic_catalog','manual_chapter_catalog','research_notes')}))


if __name__=='__main__':
    main()
