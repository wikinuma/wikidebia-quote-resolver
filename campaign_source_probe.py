"""Offline inspection of pinned French input and saved source evidence.

This is diagnostic, not a new retrieval engine or a receipt format.
A selected MIA URL is inspected, or all unresolved French quotes are
exported in compact quote-only mode. The optional transfer
contains French quote text and is confined to the designated private relay.
No document is fetched from the web, no campaign state is modified.
"""
import argparse
from collections import Counter
import fcntl
import json
import os
from pathlib import Path
import sqlite3

from auto_retrieve import collect_mia
from campaign_diagnostic import split, span
from chapter_routes import chapter_route, load_chapters
from citation_registry import source_identity
from grouped_review import pinned_loader
from navigation import extract_citation_ref, canonical_mia
from pilot_full import PilotClient
from prepare_citations import load_inventory
from relay_diagnostic import transfer, make_api, token
from resolver import ROOT

RELAY = 'wikidebates/wikidebia-mia-exchange'
PENDING = {'insufficient_evidence', 'retry_documentary'}


def prior_result(db, cid):
    events = db.execute('SELECT state,evidence FROM campaign_events_v1 '
                        'WHERE occurrence=? ORDER BY seq DESC', (cid,))
    first = next(events, None)
    if first is None or first[0] not in PENDING:
        return None
    state = first[0]
    result = json.loads(first[1]).get('result')
    if result is not None:
        return state, result
    for _, raw in events:
        result = json.loads(raw).get('result')
        if result is not None:
            return state, result
    return state, {}


def probe_rows(db, inventory, url, load_raw, chapters):
    rows, expected = [], {}
    for c in sorted(inventory['cases'], key=lambda x:x['id']):
        if c['mia_url'] != url:
            continue
        prior = prior_result(db, c['id'])
        if prior is None:
            continue
        state, r = prior
        source = r.get('source', {})
        nav = r.get('navigation', {}).get('input', {})
        row = {'id':c['id'], 'wiki_title':c['wiki_title'],
               'quote_sha256':c['citation_ref']['quote_sha256'],
               'source_key':c['source_key'], 'state':state,
               'retrieval_status':r.get('status'), 'source_status':source.get('status'),
               'quote_words':nav.get('quote_words'),
               'source_diagnostics':[],
               'source_hits':[]}
        for d in source.get('source_evidence_diagnostics', []):
            keys = ('method','status','truncated','seed_hits','alignments','cells',
                    'qualifying_hits','fragments','fragment_words')
            row['source_diagnostics'].append({k:d[k] for k in keys if k in d})
        for h in source.get('occurrences', []) + source.get('variants', []):
            row['source_hits'].append({**span(h),
                **{k:h[k] for k in ('match','similarity','token_edits','query_words',
                    'source_words','boundary_ambiguous','normalized_sha256','gap_words')
                    if k in h}})
        for s in source.get('stages', []):
            for doc in s.get('pages', []):
                if doc.get('url') and doc.get('sha256'):
                    expected.setdefault(doc['url'], set()).add(doc['sha256'])
        route = chapter_route(nav, source, chapters)
        if route:
            row['eligible_chapter'] = {'fr_url':route['fr_url'], 'en_url':route['en_url']}
        # Text only for cases with an unresolved French passage, and only
        # after verifying the exact pinned template and source identity.
        if r.get('status') == 'source_needs_review':
            try:
                ref = c['citation_ref']
                raw = load_raw(c['wiki_title'], ref['revision'])
                if source_identity(raw, ref)['source_key'] != c['source_key']:
                    raise ValueError('source_identity_mismatch')
                quote = extract_citation_ref(raw, ref, include_text=True)['text']
                if len(quote) > 20000:
                    raise ValueError('source_probe_quote_length_limit')
                row['fr'] = quote
            except Exception as exc:
                row['probe_error'] = (str(exc) if str(exc) in
                    {'offline_cache_miss','invalid_offline_cache_entry',
                     'source_identity_mismatch','source_probe_quote_length_limit'}
                    else type(exc).__name__)
        rows.append(row)
    return rows, expected


def quote_rows(db, inventory, url, load_raw):
    """Read only the latest state and the pinned French citation."""
    rows = []
    for c in sorted(inventory['cases'], key=lambda x: (x['mia_url'], x['id'])):
        if url is not None and c['mia_url'] != url:
            continue
        event = db.execute('SELECT state FROM campaign_events_v1 '
                           'WHERE occurrence=? ORDER BY seq DESC LIMIT 1',
                           (c['id'],)).fetchone()
        if event is None or event[0] not in PENDING:
            continue
        ref = c['citation_ref']
        row = {'id':c['id'], 'wiki_title':c['wiki_title'],
               'mia_url':c['mia_url'], 'state':event[0],
               'source_key':c['source_key'], 'citation_ref':ref}
        try:
            raw = load_raw(c['wiki_title'], ref['revision'])
            if source_identity(raw, ref)['source_key'] != c['source_key']:
                raise ValueError('source_identity_mismatch')
            rec = extract_citation_ref(raw, ref, include_text=True)
            quote = rec['text']
            if not quote or len(quote) > 20000:
                raise ValueError('source_probe_quote_length_limit')
            row['fr'] = quote
            for key in ('authors', 'work', 'article', 'date'):
                if key in rec:
                    row[key] = rec[key]
        except Exception as exc:
            row['probe_error'] = (str(exc) if str(exc) in
                {'offline_cache_miss','invalid_offline_cache_entry',
                 'source_identity_mismatch','source_probe_quote_length_limit'}
                else type(exc).__name__)
        rows.append(row)
    return rows


def project(state, url, expected_inventory, *, quote_only=False):
    inventory = load_inventory(state/'inventory.json')
    if inventory['manifest_sha256'] != expected_inventory:
        raise ValueError('unexpected_inventory')
    if url is not None and not any(c['mia_url']==url for c in inventory['cases']):
        raise ValueError('url_not_in_inventory')
    client = PilotClient(state/'http', budget=0, offline=True)
    chapters = [] if quote_only else load_chapters(ROOT/'chapter-catalog.json')
    dbpath = (state/'works.sqlite3').resolve().as_uri()+'?mode=ro'
    with sqlite3.connect(dbpath,uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if quote_only:
            rows = quote_rows(db, inventory, url, pinned_loader(client))
        else:
            rows, expected = probe_rows(db, inventory, url, pinned_loader(client), chapters)
    if quote_only:
        if client.requests:
            raise RuntimeError('offline_probe_made_network_requests')
        return {'schema':'wikidebia-source-probe-1', 'mode':'quote-only',
                'inventory_sha256':expected_inventory, 'source_url':url,
                'auto_publish':False, 'rights':'unreviewed',
                'states':dict(Counter(r['state'] for r in rows)),
                'source_quotes':sum('fr' in r for r in rows),
                'probe_errors':dict(Counter(r['probe_error'] for r in rows if 'probe_error' in r)),
                'eligible_chapters':0, 'cached_source_documents':0,
                'cache_only':True, 'network_requests':client.requests,
                'cache_hits':client.hits, 'rows':rows, 'scopes':{}}
    # Also inspect the headings of already cached MIA leaves, not their text.
    docs, info = collect_mia(client, url, 128)
    scopes = {}
    for d in docs:
        scopes[d.url] = {'url':d.url, 'sha256':d.digest, 'title':d.title,
                         'headings':list(d.headings),
                         'matches_prior_digest':d.digest in expected.get(d.url,set())}
    if client.requests:
        raise RuntimeError('offline_probe_made_network_requests')
    return {'schema':'wikidebia-source-probe-1', 'inventory_sha256':expected_inventory,
            'source_url':url, 'auto_publish':False, 'rights':'unreviewed',
            'states':dict(Counter(r['state'] for r in rows)),
            'retrieval_statuses':dict(Counter(r['retrieval_status'] or 'unknown' for r in rows)),
            'source_quotes':sum('fr' in r for r in rows),
            'probe_errors':dict(Counter(r['probe_error'] for r in rows if 'probe_error' in r)),
            'eligible_chapters':sum('eligible_chapter' in r for r in rows),
            'cache_only':True, 'network_requests':client.requests,
            'cache_hits':client.hits, 'cached_source_documents':len(docs),
            'cache_issue_kinds':dict(Counter(i.get('error') or i.get('kind') or 'unknown' for i in info.get('issues',[]))),
            'rows':rows, 'scopes':scopes}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--inventory-sha256',required=True)
    p.add_argument('--mia-url')
    p.add_argument('--quote-only',action='store_true',
                   help='Export pinned French text only for unresolved cases; omit URL for all.')
    p.add_argument('--upload-private',action='store_true')
    a = p.parse_args()
    url=None
    if a.mia_url is not None:
        url=canonical_mia(a.mia_url)
        if url is None or url!=a.mia_url or not url.startswith('https://www.marxists.org/francais/'):
            p.error('expected exact canonical French MIA URL')
    elif not a.quote_only:
        p.error('--mia-url is required unless --quote-only is selected')
    os.umask(0o077)
    with (a.state/'campaign.lock').open('rb') as lock:
        fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
        manifest, files = split(project(a.state,url,a.inventory_sha256,quote_only=a.quote_only))
    digest = manifest['diagnostic_sha256']
    directory = a.out/digest
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    if directory.is_symlink() or directory.stat().st_mode & 0o077:
        raise ValueError('insecure_source_probe_directory')
    for name,data in files.items():
        path = directory/name
        if path.is_symlink():
            raise ValueError('unexpected_symlink')
        if path.exists() and path.read_bytes()!=data:
            raise ValueError('existing_probe_mismatch')
        if not path.exists():
            with path.open('xb') as f:
                f.write(data)
    print(json.dumps({'directory':str(directory),'diagnostic_sha256':digest,
                      'states':manifest['states'],'source_quotes':manifest['source_quotes'],
                      'probe_errors':manifest['probe_errors'],
                      'eligible_chapters':manifest['eligible_chapters'],
                      'cached_source_documents':manifest['cached_source_documents'],
                      'network_requests':manifest['network_requests']},sort_keys=True))
    if a.upload_private:
        print(json.dumps(transfer(make_api(RELAY,token(RELAY)),RELAY,digest,files,
                                  prefix='source-probes'),sort_keys=True))


if __name__=='__main__':
    main()
