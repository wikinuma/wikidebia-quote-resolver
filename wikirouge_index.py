"""Read-only Wikirouge snapshot: bibliographic leads, never quotation proof."""
from __future__ import annotations
from contextlib import contextmanager, closing
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import unicodedata
import zipfile

from navigation import canonical_mia, author_check, safe_title, generic_work_name

SCHEMA = 'wikirouge-bibliographic-index-1'
MATCHED = {'same_normalized_author_tokens', 'same_documented_author_alias'}
DEFAULT = Path(__file__).resolve().parent / 'data' / 'wikirouge' / 'manifest.json'
LIMIT = 64


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def title_key(value):
    return ' '.join(unicodedata.normalize('NFC', value.replace('_', ' ')).split()).replace('\u2019', "'").replace('\u02bc', "'").casefold()


@contextmanager
def open_snapshot(manifest=DEFAULT):
    """Extract only the pinned database member; no scripts from the ZIP run."""
    manifest = Path(manifest)
    m = json.loads(manifest.read_text(encoding='utf-8'))
    if m['schema'] != 'wikidebia-wikirouge-snapshot-manifest-1' or m['database']['schema'] != SCHEMA:
        raise ValueError('unsupported_snapshot')
    filename = m['archive']['filename']
    if Path(filename).name != filename:
        raise ValueError('invalid_archive_name')
    archive = manifest.parent / filename
    if archive.stat().st_size != m['archive']['bytes'] or digest(archive) != m['archive']['sha256']:
        raise ValueError('archive_integrity_mismatch')
    with tempfile.TemporaryDirectory(prefix='wikirouge-index-') as tmp:
        path = Path(tmp) / 'index.sqlite3'
        with zipfile.ZipFile(archive) as z:
            member = m['database']['member']
            entries = [i for i in z.infolist() if i.filename == member]
            if len(entries) != 1 or entries[0].file_size != m['database']['bytes'] or not 0 < entries[0].file_size <= 100_000_000:
                raise ValueError('invalid_database_member')
            with z.open(entries[0]) as src, path.open('wb') as dst:
                shutil.copyfileobj(src, dst)
        if digest(path) != m['database']['sha256']:
            raise ValueError('database_integrity_mismatch')
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
            db.row_factory = sqlite3.Row
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('database_integrity_check')
            if json.loads(db.execute("SELECT value FROM info WHERE key='schema'").fetchone()[0]) != SCHEMA:
                raise ValueError('database_schema_mismatch')
            counts = dict(db.execute('SELECT lang,count(*) FROM pages GROUP BY lang'))
            if counts != {k:m['counts']['pages'][k] for k in ('fr','en')} or db.execute('SELECT count(*) FROM pairs').fetchone()[0] != m['counts']['pairs']:
                raise ValueError('database_counts_mismatch')
            yield LocalIndex(db, m['database']['sha256'])


def simple_year(dates):
    if len(dates) == 1 and re.fullmatch(r'\d{4}(?:-\d{2}(?:-\d{2})?)?', dates[0]):
        return dates[0][:4]
    return None


class LocalIndex:
    def __init__(self, db, fingerprint):
        self.db = db
        self.fingerprint = fingerprint

    def page(self, lang, pid):
        r = self.db.execute('SELECT id,title,revision,url,work_root,status FROM pages WHERE lang=? AND id=?', (lang,pid)).fetchone()
        if r is None:
            raise ValueError('missing_index_page')
        out = dict(r)
        fields = {}
        for field,value in self.db.execute('SELECT field,value FROM fields WHERE lang=? AND id=? ORDER BY template_no,ordinal',(lang,pid)):
            if value.strip():
                fields.setdefault(field,[]).append(value.strip())
        authors = list(dict.fromkeys(fields.get('author',[])))
        out.update(fields=fields, authors=authors[0] if len(authors)==1 else '',
                   mia_urls=sorted({v for raw in fields.get('mia_url',[]) if (v:=canonical_mia(raw))}),
                   dates=list(dict.fromkeys(fields.get('date',[]))))
        return out

    def ids_for_title(self, lang, title):
        if not title or not safe_title(title) or generic_work_name(title):
            return set()
        return {r[0] for r in self.db.execute('SELECT id FROM pages WHERE lang=? AND search_key=? LIMIT ?', (lang,title_key(title),LIMIT+1))}

    def ids_for_urls(self, urls):
        ids=set()
        for raw in urls:
            url=canonical_mia(raw)
            if url:
                key=url.removeprefix('https://www.')
                ids.update(r[0] for r in self.db.execute('SELECT id FROM source_urls WHERE lang=? AND mia_key=? LIMIT ?',('fr',key,LIMIT+1)))
        return ids

    def lookup(self, metadata):
        """Return all bounded alternatives, with explicit abstention reasons."""
        a=self.ids_for_title('fr',metadata.get('article',''))
        w=self.ids_for_title('fr',metadata.get('work',''))
        u=self.ids_for_urls(metadata.get('mia_urls',[]))
        report={'method':'local_wikirouge_snapshot','snapshot_sha256':self.fingerprint,
                'url_page_ids':sorted(u),'article_page_ids':sorted(a),'work_page_ids':sorted(w),
                'status':'not_found','candidates':[],'auto_publish':False,'rights':'unreviewed'}
        if any(len(ids)>LIMIT for ids in (a,w,u)):
            report['status']='truncated'; return report
        chosen=u or a or w
        if not chosen:
            return report
        # URL evidence can point to a chapter; its explicit root may match the
        # cited work. A title-only route requires both authors and a native link.
        for pid in sorted(chosen):
            fr=self.page('fr',pid)
            root_ids=self.ids_for_title('fr',fr['work_root']) if fr['work_root'] else set()
            title_match=bool((a|w)&({pid}|root_ids))
            if u and (a or w) and not title_match:
                report.setdefault('conflicting_title_ids',[]).append(pid)
            direct=self.db.execute('SELECT * FROM pairs WHERE fr_id=? ORDER BY en_id LIMIT ?', (pid,LIMIT+1)).fetchall()
            mapped=fr
            scope='direct_native_link'
            if not direct and fr['work_root'] and fr['work_root']!=fr['title'] and len(root_ids)==1:
                mapped=self.page('fr',next(iter(root_ids)))
                direct=self.db.execute('SELECT * FROM pairs WHERE fr_id=? ORDER BY en_id LIMIT ?', (mapped['id'],LIMIT+1)).fetchall()
                scope='parent_work_only_no_chapter_equivalence'
            if len(direct)>LIMIT:
                report['status']='truncated'; return report
            for edge in direct:
                en=self.page('en',edge['en_id'])
                checks={'citation_fr':author_check(metadata.get('authors',''),mapped['authors']),
                        'fr_en':author_check(mapped['authors'],en['authors'])}
                fy,ey=simple_year(mapped['dates']),simple_year(en['dates'])
                reasons=[]
                if edge['fr_degree']!=1 or edge['en_degree']!=1 or edge['metadata_ambiguous']:
                    reasons.append('ambiguous_native_pair')
                if fr['status']!='metadata' or mapped['status']!='metadata' or en['status']!='metadata':
                    reasons.append('incomplete_metadata')
                if any(v not in MATCHED for v in checks.values()):
                    reasons.append('author_unconfirmed_or_conflicting')
                if fy and ey and fy!=ey:
                    reasons.append('different_writing_years')
                if u and (a or w) and not title_match:
                    reasons.append('source_url_title_conflict')
                if not all(safe_title(p['title']) and not generic_work_name(p['title']) for p in (fr,mapped,en)):
                    reasons.append('invalid_work_title')
                if len(mapped['mia_urls'])>1 or len(en['mia_urls'])>1:
                    reasons.append('multiple_canonical_mia_urls')
                report['candidates'].append({'source_page':fr,'fr':mapped,'en':en,'scope':scope,
                    'directions':edge['evidence'].split(','),'author_checks':checks,
                    'writing_years':[fy,ey],'reasons':reasons})
        if len(chosen)!=1 or len(report['candidates'])>1:
            report['status']='ambiguous'
        elif len(report['candidates'])==1:
            report['status']='candidate' if not report['candidates'][0]['reasons'] else 'unconfirmed'
        return report

    def route(self, metadata):
        result=self.lookup(metadata)
        if result['status']!='candidate':
            return result,None
        c=result['candidates'][0]
        fr,en=c['fr'],c['en']
        # Preserve actual directions. Never fabricate reciprocal confirmation.
        route={'fr_title':fr['title'],'en_title':en['title'],'fr_url':fr['url'],'en_url':en['url'],
               'reciprocal':set(c['directions'])=={'fr','en'},
               'native_link_directions':c['directions'],'scope':c['scope'],
               'snapshot_sha256':self.fingerprint}
        meta={lang:{'status':'snapshot_metadata','mia_urls':p['mia_urls'],'authors':p['authors'],
                    'revision':p['revision'],'dates':p['dates'],'fields':p['fields']}
              for lang,p in (('fr',fr),('en',en))}
        return result,{'route':route,'work_metadata':meta,'selected_fr_title':c['source_page']['title'],
                       'route_method':'local_wikirouge_snapshot','author_check':c['author_checks']['citation_fr'],
                       'status':'wikirouge_route_candidate'}
