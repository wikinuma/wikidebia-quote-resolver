"""Read-only metadata projection of a preparation campaign; no text export."""
import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

PENDING = {'insufficient_evidence', 'retry_documentary'}


def encode(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def fingerprint(x):
    return sha256(encode(x)).hexdigest()


def code(x):
    return x if isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9_.=:-]{1,100}', x) else None


def label(x):
    return x[:300] if isinstance(x, str) else None


def url(x):
    if not isinstance(x, str):
        return None
    p = urlsplit(x)
    if (p.scheme == 'https' and p.hostname in {'www.marxists.org', 'marxists.org', 'wikirouge.net'}
            and p.netloc == p.hostname and not p.query and not p.fragment and p.path.startswith('/')):
        return x
    return None


def fields(d, names, transform=code):
    return {k: v for k in names.split() if (v := transform(d.get(k))) is not None}


def span(d):
    doc = d.get('document', d)
    out = fields(doc, 'url', url)
    for k in ('sha256', 'body_sha256', 'blocks_sha256'):
        if isinstance(doc.get(k), str) and re.fullmatch('[0-9a-f]{64}', doc[k]):
            out[k] = doc[k]
    for k in ('revision',):
        if type(doc.get(k)) is int:
            out[k] = doc[k]
    for k in ('char_start', 'char_end'):
        if type(d.get(k)) is int:
            out[k] = d[k]
    if isinstance(d.get('text_sha256'), str) and re.fullmatch('[0-9a-f]{64}', d['text_sha256']):
        out['text_sha256'] = d['text_sha256']
    return out


def triage_summary(rows):
    """Compact observed-state counts; never infer an unavailable translation
    from a failed or bounded search. Keep the latest event distinct from the
    last retrieval, because a later review can supersede retrieval state.
    """
    grouped = Counter()
    for row in rows:
        events = row['events']
        latest = events[-1] if events else {}
        last = next((e['retrieval'] for e in reversed(events) if 'retrieval' in e), {})
        biblio = next((b for e in reversed(events)
                       for b in (e.get('review', {}).get('bibliography'),
                                 e.get('retrieval', {}).get('bibliography')) if b), {})
        reason = code(latest.get('reason'))
        decision = code(latest.get('decision'))
        status = code(last.get('status'))
        source = code(last.get('source_status'))
        if row['state'] == 'retry_documentary':
            signal = 'documentary_retry'
        elif reason == 'already_reviewed_without_new_evidence':
            signal = 'unchanged_review_evidence'
        elif (decision in {'uncertain', 'no_equivalent'}
              or reason == 'review_abstention_not_exhaustive'):
            signal = 'review_abstention'
        elif source in {'source_unconfirmed_in_bounded_scope', 'ambiguous_exact_source'}:
            signal = 'source_not_confirmed'
        elif status in {'no_work_route', 'bibliographic_lead_only',
                        'alternative_catalog_route_unconfirmed'}:
            signal = 'documentary_route_unresolved'
        elif status in {'catalog_source_document_unconfirmed', 'source_needs_review'}:
            signal = 'source_not_confirmed'
        elif status and status.endswith('_unavailable'):
            signal = 'documentary_unavailable'
        elif status and status.endswith('_no_candidate'):
            signal = 'no_ranked_candidate'
        else:
            signal = 'other_or_unclassified'
        grouped[(row['mia_url'], label(biblio.get('authors')),
                 label(biblio.get('work') or biblio.get('article')),
                 row['state'], signal, status, source, reason, decision)] += 1
    keys = ('mia_url', 'authors', 'work', 'state', 'signal',
            'last_retrieval_status', 'last_source_status', 'latest_reason',
            'latest_decision')
    result = [{**dict(zip(keys, k)), 'count':n}
              for k,n in sorted(grouped.items(), key=lambda pair:repr(pair[0]))]
    by_signal = Counter()
    for row in result:
        by_signal[row['signal']] += row['count']
    return {'scope':'observed_statuses_not_proven_causes_or_exhaustive_search',
            'unresolved_occurrences':sum(by_signal.values()),
            'by_signal':dict(sorted(by_signal.items())), 'groups':result}


def projection(state, expected=None):
    inventory = json.loads((state / 'inventory.json').read_bytes())
    digest = inventory.pop('manifest_sha256')
    if inventory['schema'] != 'wikidebia-mia-inventory-1' or fingerprint(inventory) != digest:
        raise ValueError('inventory_digest_mismatch')
    if expected and digest != expected:
        raise ValueError('unexpected_inventory')
    cases = {c['id']: c for c in inventory['cases']}
    nested = sorted((c for p in inventory['pages'] for c in p['cases']), key=lambda c:c['id'])
    if len(cases) != len(inventory['cases']) or nested != sorted(cases.values(), key=lambda c:c['id']):
        raise ValueError('inventory_case_mismatch')
    scopes, histories, packets = {}, defaultdict(list), {}
    counts, by_url = Counter(), defaultdict(Counter)

    def scope(info):
        out = {**fields(info, 'provider scope'), **fields(info, 'root', url)}
        if info.get('provider') == 'wikirouge' and 'root' in info and 'root' not in out:
            out['root_title'] = label(info['root'])
        out['pages'] = [span(p) for p in info.get('pages', [])]
        out['issues'] = [{**fields(i, 'kind error status'), **fields(i, 'url', url)} for i in info.get('issues', [])]
        key = fingerprint(out)
        scopes[key] = out
        return key

    def retrieval(e):
        r = e.get('result', {})
        n, s = r.get('navigation', {}), r.get('source', {})
        inp = n.get('input', {})
        out = {**fields(r, 'status navigation_status target_provider target_scope'),
               'bibliography': fields(inp, 'authors work article date', label),
               'navigation': {**fields(n, 'status route_method author_check error'),
                              **fields(n.get('route', {}), 'fr_title en_title', label),
                              **fields(n.get('route', {}), 'fr_url en_url', url),
                              'mia_target': {**fields(n.get('mia_target', {}), 'error status'),
                                             **fields(n.get('mia_target', {}), 'requested_url final_url', url)},
                              'attempts': [fields(a, 'method status error field') for a in n.get('attempts', [])]},
               'source_status': code(s.get('status')),
               'source_hits': [span(p) for p in s.get('occurrences', []) + s.get('variants', [])],
               'source_scopes': [scope(p) for p in s.get('stages', [])],
               'target_scopes': [scope(p) for p in r.get('target_attempts', [])],
               'alignment_status': code(r.get('section_alignment', {}).get('status')),
               'chapter': fields(r.get('chapter_route', r.get('chapter_route_failure', {}).get('route', {})), 'fr_url en_url fr_toc en_toc', url),
               'chapter_error': code(r.get('chapter_route_failure', {}).get('error')),
               'candidates': []}
        chapter = r.get('chapter_route', r.get('chapter_route_failure', {}).get('route', {}))
        if isinstance(chapter.get('en_urls'),list):
            out['chapter']['en_urls'] = [v for u in chapter['en_urls'] if (v := url(u)) is not None]
        queue = r.get('review_packet', {}).get('candidates', []) + r.get('section_alignment', {}).get('candidates', []) + r.get('candidates', [])
        out['candidates'] = list({fingerprint(span(c)):span(c) for c in queue}.values())
        return out

    with sqlite3.connect((state / 'works.sqlite3').resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        for cid, status, raw in db.execute('SELECT occurrence,state,evidence FROM campaign_events_v1 ORDER BY seq'):
            if cid in cases:
                histories[cid].append((status, json.loads(raw)))
        for cid, c in cases.items():
            status = histories[cid][-1][0] if histories[cid] else 'queued'
            counts[status] += 1
            by_url[c['mia_url']][status] += 1
        rows = []
        for cid, c in sorted(cases.items(), key=lambda p:(p[1]['mia_url'], p[0])):
            history = histories[cid]
            if not history or history[-1][0] not in PENDING:
                continue
            row = {'id':cid, 'wiki_title':c['wiki_title'], 'mia_url':url(c['mia_url']),
                   'source_key':c['source_key'], 'quote_sha256':c['citation_ref']['quote_sha256'],
                   'state':history[-1][0], 'events':[]}
            for status, e in history:
                item = {'state':status, **fields(e, 'reason decision context_stage prior_registry_status')}
                if 'request_error_kinds' in e:
                    item['request_errors'] = {k:v for k,v in e['request_error_kinds'].items() if code(k) and type(v) is int}
                if 'review_skip' in e:
                    item['review_skip'] = fields(e['review_skip'], 'status error')
                if 'result' in e:
                    item['retrieval'] = retrieval(e)
                if (packet_id := e.get('packet_sha256')):
                    if packet_id not in packets:
                        found = db.execute('SELECT packet,state,reply FROM campaign_packets_v1 WHERE digest=?', (packet_id,)).fetchone()
                        if not found:
                            raise ValueError('missing_campaign_packet')
                        packet = json.loads(found[0])
                        if fingerprint(packet) != packet_id:
                            raise ValueError('packet_digest_mismatch')
                        reply = json.loads(found[2]) if found[2] else {}
                        decisions = {d['id']:d for d in reply.get('decisions', [])}
                        index = {}
                        for qid, source in packet['sources'].items():
                            windows = sorted((span(packet['targets'][p]) for p in source['candidates']), key=fingerprint)
                            meta = next(({'authors':g['authors'], 'work':g['work'], 'article':ch['article']}
                                         for g in packet['payload']['groups'] for ch in g['chapters']
                                         if any(q['id'] == qid for q in ch['citations'])), {})
                            for occurrence in source['occurrences']:
                                index[occurrence['case_id']] = {'bibliography':fields(meta, 'authors work article', label),
                                    'decision':code(decisions.get(qid, {}).get('decision')),
                                    'windows':windows, 'window_set_sha256':fingerprint(windows)}
                        packets[packet_id] = index
                    item['packet_sha256'] = packet_id
                    if cid in packets[packet_id]:
                        item['review'] = packets[packet_id][cid]
                row['events'].append(item)
            rows.append(row)
    if len(rows) != sum(counts[s] for s in PENDING):
        raise ValueError('diagnostic_count_mismatch')
    groups = Counter()
    for row in rows:
        biblios = [b for e in row['events'] for b in (e.get('retrieval', {}).get('bibliography'), e.get('review', {}).get('bibliography')) if b]
        b = biblios[-1] if biblios else {}
        last = next((e['retrieval'] for e in reversed(row['events']) if 'retrieval' in e), {})
        groups[(b.get('authors'), b.get('work') or b.get('article'), row['state'], last.get('status'))] += 1
    return {'schema':'wikidebia-campaign-diagnostic-1', 'inventory_sha256':digest,
            'groups':[{'authors':a, 'work':w, 'state':s, 'last_retrieval_status':r, 'count':n}
                      for (a,w,s,r),n in sorted(groups.items(), key=lambda p:repr(p[0]))],
            'states':dict(counts), 'by_url':dict(by_url), 'rows':rows, 'scopes':scopes,
            'triage':triage_summary(rows),
            'auto_publish':False, 'rights':'unreviewed'}


def split(report):
    body = dict(report)
    rows, scopes = body.pop('rows'), body.pop('scopes')
    files = {}
    for name, seq in (('cases', rows), ('scopes', [{'id':k, **v} for k,v in sorted(scopes.items())])):
        batch, n = [], 0
        for item in seq:
            if batch and len(encode(batch + [item])) > 70000:
                files[f'{name}-{n:03}.json'] = encode(batch) + b'\n'
                batch, n = [], n+1
            batch.append(item)
        if batch:
            files[f'{name}-{n:03}.json'] = encode(batch) + b'\n'
    body['files'] = {k:{'sha256':sha256(v).hexdigest(), 'bytes':len(v)} for k,v in files.items()}
    body['unresolved_occurrences'] = len(rows)
    body['diagnostic_sha256'] = fingerprint(body)
    files['manifest.json'] = encode(body) + b'\n'
    return body, files


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--inventory-sha256')
    a = p.parse_args()
    import fcntl, os
    os.umask(0o077)
    with (a.state / 'campaign.lock').open('rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        manifest, files = split(projection(a.state, a.inventory_sha256))
    dest = a.out / manifest['diagnostic_sha256']
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, data in files.items():
        path = dest / name
        if path.exists() and path.read_bytes() != data:
            raise ValueError('existing_diagnostic_mismatch')
        if not path.exists():
            with path.open('xb') as f:
                f.write(data)
    print(json.dumps({'directory':str(dest), 'states':manifest['states'],
                      'unresolved_occurrences':manifest['unresolved_occurrences'],
                      'diagnostic_sha256':manifest['diagnostic_sha256'], 'files':len(files)}))


if __name__ == '__main__':
    main()
