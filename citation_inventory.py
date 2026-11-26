"""Full revision-aware inventory of literal MIA-linked Citation templates.

No frequency, author, quote length or sample-size filter. Reuse the existing
census enumeration and strict link policy; only current page revisions are
read, with raw bodies downloaded only when the page revision has changed.
The manifest contains references and hashes, not quotation text.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import mwparserfromhell
from alignment_store import encode
from citation_domain_census import aliases, api, embedded_pages, name
from citation_policy import link_policy
from citation_registry import fingerprint, source_identity
from navigation import citation_record, templates
from resolver import Client, ROOT, RequestBudgetExceeded, sha

SCHEMA = 'wikidebia-mia-inventory-1'


def algorithm():
    files = ('citation_inventory.py', 'citation_domain_census.py',
             'citation_policy.py', 'citation_registry.py', 'navigation.py',
             'resolver.py', 'alignment_store.py')
    return fingerprint({'files': {f: sha((ROOT/f).read_bytes()) for f in files},
                        'parser': mwparserfromhell.__version__})


def inspect_page(raw, title, pid, rev, alias_names):
    cases, issues, routes = [], Counter(), Counter()
    # The resolver indexes only the templates recognized by navigation.
    # Count aliases outside that parser separately rather than claiming they
    # are covered or assigning an incorrect index to an occurrence.
    for t in mwparserfromhell.parse(raw).filter_templates(recursive=True):
        if name(t.name) in alias_names:
            routes[link_policy(t)['route']] += 1
    canonical_mia_count = 0
    for index, t in enumerate(templates(raw, {'citation'})):
        if name(t.name) not in alias_names:
            continue
        policy = link_policy(t)
        if policy['route'] != 'mia':
            continue
        canonical_mia_count += 1
        try:
            record = citation_record(t, index, include_link_policy=True)
            ref = {k: record[k] for k in ('template_index', 'template_sha256', 'quote_sha256')}
            ref['revision'] = rev
            identity = source_identity(raw, ref)
        except (ValueError, KeyError, TypeError) as exc:
            issues['invalid_citation'] += 1
            continue
        cases.append({
            'id': 'inventory-'+fingerprint([pid, rev, index, ref['template_sha256']])[:20],
            'wiki_title': title, 'citation_ref': ref,
            'source_key': identity['source_key'], 'mia_url': identity['mia_url'],
        })
    if canonical_mia_count > routes['mia']:
        raise ValueError('citation_parser_scope_mismatch')
    if canonical_mia_count < routes['mia']:
        issues['unsupported_alias'] += routes['mia']-canonical_mia_count
    if routes['mia'] != len(cases)+sum(issues.values()):
        raise ValueError('citation_count_mismatch')
    return {'pageid': pid, 'title': title, 'revision': rev, 'routes': dict(routes),
            'citation_issues': dict(issues), 'cases': cases}


def get_pages(client, values, *, by_revision=False, content=False):
    field = 'revids' if by_revision else 'pageids'
    rows = api(client, action='query', **{field: '|'.join(map(str, values))},
               prop='revisions', rvprop='ids|content' if content else 'ids',
               **({'rvslots': 'main'} if content else {}))['query']['pages']
    result = {}
    for p in rows:
        if p.get('ns') != 0 or p.get('missing') or not isinstance(p.get('title'), str):
            raise ValueError('page_unavailable')
        revs = p.get('revisions', [])
        if len(revs) != 1 or type(p.get('pageid')) is not int:
            raise ValueError('invalid_revision_row')
        rev = revs[0]
        if type(rev.get('revid')) is not int or rev['revid'] <= 0:
            raise ValueError('invalid_revision_id')
        key = rev['revid'] if by_revision else p['pageid']
        if key in result:
            raise ValueError('duplicate_revision_row')
        raw = rev.get('slots', {}).get('main', {}).get('content')
        if content and not isinstance(raw, str):
            raise ValueError('missing_raw_content')
        result[key] = (p['pageid'], p['title'], rev['revid'], raw)
    if set(result) != set(values):
        raise ValueError('incomplete_revision_response')
    return result


def read_batches(client, values, issues, *, by_revision=False, content=False, size=30):
    def read(batch):
        try:
            yield from get_pages(client, batch, by_revision=by_revision, content=content).values()
        except RequestBudgetExceeded:
            issues['request_budget_exhausted'] += len(batch)
        except Exception:
            # Split only failed batches, not successful ones.
            if len(batch) > 1:
                mid = len(batch)//2
                yield from read(batch[:mid])
                yield from read(batch[mid:])
            else:
                issues['revision_unavailable'] += 1
    for offset in range(0, len(values), size):
        if issues['request_budget_exhausted']:
            return
        yield from read(values[offset:offset+size])


def validate_previous(previous):
    if previous is None:
        return
    body = {k:v for k,v in previous.items() if k != 'manifest_sha256'}
    if previous.get('schema') != SCHEMA or fingerprint(body) != previous.get('manifest_sha256'):
        raise ValueError('invalid_previous_inventory')
    if not isinstance(previous.get('pages'), list):
        raise ValueError('invalid_previous_pages')
    ids = [p['pageid'] for p in previous['pages']]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate_previous_page')
    cases = [c for p in previous['pages'] for c in p['cases']]
    if sorted(cases,key=lambda c:c['id']) != sorted(previous['cases'],key=lambda c:c['id']):
        raise ValueError('inconsistent_previous_cases')


def refresh(client, previous=None, batch_size=30):
    if not 1 <= batch_size <= 50:
        raise ValueError('invalid_batch_size')
    validate_previous(previous)
    titles = aliases(client)
    names = {name(t) for t in titles}
    ids = embedded_pages(client, titles)
    algo = algorithm()
    reusable = bool(previous and previous['algorithm'] == algo and previous['aliases'] == titles)
    old_pages = {p['pageid']:p for p in previous['pages']} if reusable else {}
    issues, stats = Counter(), Counter()
    if not ids:
        issues['empty_enumeration'] += 1
    pages = {}
    changed = {}
    if old_pages:
        for pid,title,rev,_ in read_batches(client,ids,issues,size=batch_size):
            prior = old_pages.get(pid)
            if prior and (prior['revision'], prior['title']) == (rev,title):
                pages[pid] = prior
                stats['pages_reused'] += 1
            else:
                changed[rev] = (pid,title)
        # Bind raw bytes to the revisions just enumerated.
        to_read = sorted(changed)
        rows = read_batches(client,to_read,issues,by_revision=True,content=True,size=batch_size)
    else:
        # A cold scan needs only one pass through current raw pages.
        rows = read_batches(client,ids,issues,content=True,size=batch_size)
    for pid,title,rev,raw in rows:
        if old_pages and changed.get(rev) != (pid,title):
            issues['page_moved_during_scan'] += 1
            continue
        try:
            pages[pid] = inspect_page(raw,title,pid,rev,names)
            stats['pages_parsed'] += 1
        except Exception:
            issues['page_parse_failed'] += 1
    if set(pages) != set(ids):
        issues['pages_not_accounted_for'] = len(set(ids)-set(pages))
    cases = sorted((c for p in pages.values() for c in p['cases']),
                   key=lambda c:(c['mia_url'], c['source_key'], c['id']))
    routes, citation_issues = Counter(), Counter()
    for row in pages.values():
        routes.update(row['routes'])
        citation_issues.update(row['citation_issues'])
    # Source identity, not page position, distinguishes changed quotations.
    old_keys = {c['source_key'] for c in previous['cases']} if previous else set()
    new_keys = {c['source_key'] for c in cases}
    complete = not issues and bool(ids)
    report = {'status':'complete_for_enumerated_raw_scope' if complete else 'incomplete',
              'pages_enumerated':len(ids),'pages_accounted_for':len(pages),
              'pages_reused':stats['pages_reused'],'pages_parsed':stats['pages_parsed'],
              'routes':dict(routes),'mia_occurrences':routes['mia'],
              'pinned_cases':len(cases),'distinct_source_keys':len(new_keys),
              'new_source_keys':len(new_keys-old_keys) if complete else None,
              'removed_source_keys':len(old_keys-new_keys) if complete else None,
              'unchanged_source_keys':len(old_keys&new_keys) if complete else None,
              'citation_issues':dict(citation_issues),'scan_issues':dict(issues),
              'wiki_requests':client.requests,'decoded_bytes':client.network_body_bytes,
              'hosted_model_calls':0,'cited_url_requests':0,'auto_publish':False}
    if not complete:
        return report, None
    manifest = {'schema':SCHEMA,'algorithm':algo,'aliases':titles,
                'scope':'mainspace raw literal Citation links; non-atomic revision scan',
                'pages':[pages[pid] for pid in sorted(pages)],'cases':cases}
    manifest['manifest_sha256'] = fingerprint(manifest)
    return report, manifest


def atomic_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as f:
        tmp=Path(f.name)
        try:
            f.write(encode(value)+'\n')
            f.flush()
            os.fsync(f.fileno())
            f.close()
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inventory',type=Path,required=True)
    p.add_argument('--budget',type=int,default=1200)
    p.add_argument('--batch-size',type=int,default=30)
    a=p.parse_args()
    # Operational state may be kept in ignored cache or outside the checkout.
    path=a.inventory.resolve()
    if path.is_relative_to(ROOT.resolve()) and not path.is_relative_to((ROOT/'.cache').resolve()):
        p.error('inventory must be outside repository or under .cache')
    previous=json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    with TemporaryDirectory(prefix='mia-inventory-http-') as tmp:
        report,manifest=refresh(Client(Path(tmp),budget=a.budget,delay=.25),previous,a.batch_size)
    if manifest is not None:
        atomic_private(path,manifest)
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return int(manifest is None)


if __name__ == '__main__':
    raise SystemExit(main())
