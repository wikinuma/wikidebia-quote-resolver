"""Read-only domain census of literal Citation invocations in mainspace."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
import mwparserfromhell
from citation_policy import link_policy
from resolver import Client, ROOT, WD_API, RequestBudgetExceeded


def utc():
    return datetime.now(timezone.utc).isoformat()


def api(client, **params):
    result = client.api(WD_API, maxlag=5, **params)
    if result.get('warnings'):
        raise ValueError('api_warning')
    return result


def iterate(client, params, keys):
    cursor = {}
    seen = set()
    for _ in range(1000):
        result = api(client, **params, **cursor)
        yield result['query']
        cursor = result.get('continue', {})
        if not cursor:
            return
        key = json.dumps(cursor, sort_keys=True)
        if not set(cursor) <= {'continue', *keys} or key in seen:
            raise ValueError('invalid_or_repeated_continuation')
        seen.add(key)
    raise ValueError('continuation_limit')


def name(value):
    value = ' '.join(str(value).replace('_', ' ').strip().split())
    if value.startswith(':'):
        value = value[1:].strip()
        if ':' not in value:
            return None
    if ':' in value:
        prefix, rest = value.split(':', 1)
        if prefix.casefold() not in ('modèle', 'template'):
            return None
        value = rest.strip()
    return value[:1].upper() + value[1:]


def aliases(client):
    root = api(client, action='query', titles='Modèle:Citation',
               redirects=1)['query']['pages']
    if len(root) != 1 or root[0].get('ns') != 10 or root[0].get('missing'):
        raise ValueError('citation_template_not_found')
    pending = ['Modèle:Citation', root[0]['title']]
    titles = set()
    while pending:
        title = pending.pop()
        if title in titles:
            continue
        if len(titles) >= 100:
            raise ValueError('template_alias_limit')
        titles.add(title)
        for query in iterate(client, dict(action='query', titles=title, prop='redirects',
                         rdnamespace=10, rdlimit=500), {'rdcontinue'}):
            pages = query['pages']
            if len(pages) != 1 or pages[0].get('ns') != 10 or pages[0].get('missing'):
                raise ValueError('invalid_template_alias')
            for row in pages[0].get('redirects', []):
                if row.get('ns') != 10 or not isinstance(row.get('title'), str):
                    raise ValueError('invalid_redirect')
                pending.append(row['title'])
    return sorted(titles)


def embedded_pages(client, titles):
    ids = set()
    for title in titles:
        for query in iterate(client, dict(action='query', list='embeddedin',
                         eititle=title, einamespace=0, eilimit=500), {'eicontinue'}):
            for row in query['embeddedin']:
                pid = row.get('pageid')
                if row.get('ns') != 0 or type(pid) is not int or pid <= 0:
                    raise ValueError('invalid_transclusion_page')
                ids.add(pid)
    return sorted(ids)


def revision_batches(client, ids, issues):
    try:
        pages = api(client, action='query', pageids='|'.join(map(str, ids)),
                    prop='revisions', rvprop='ids|content', rvslots='main')['query']['pages']
        if len(pages) != len(ids) or {p.get('pageid') for p in pages} != set(ids):
            raise ValueError('incomplete_revision_batch')
        checked = []
        for p in pages:
            revs = p.get('revisions', [])
            if p.get('ns') != 0 or len(revs) != 1:
                raise ValueError('missing_page_revision')
            rev = revs[0]
            raw = rev.get('slots', {}).get('main', {}).get('content')
            if type(rev.get('revid')) is not int or rev['revid'] <= 0 or not isinstance(raw, str):
                raise ValueError('missing_revision_content')
            checked.append(raw)
    except RequestBudgetExceeded:
        issues['request_budget_exhausted'] += len(ids)
        return
    except Exception as exc:
        if len(ids) > 1 and isinstance(exc, ValueError):
            mid = len(ids) // 2
            yield from revision_batches(client, ids[:mid], issues)
            yield from revision_batches(client, ids[mid:], issues)
        else:
            issues[type(exc).__name__] += len(ids)
        return
    yield from checked


def count(client, batch_size=30):
    start, t0 = utc(), monotonic()
    issues = Counter()
    routes, reasons, domains, candidates, pages_by_domain = (Counter() for _ in range(5))
    inspected = unmatched = total = 0
    titles, ids = [], []
    try:
        titles = aliases(client)
        ids = embedded_pages(client, titles)
    except Exception as exc:
        issues['enumeration_' + type(exc).__name__] += 1
    if not issues and ids:
        names = {name(t) for t in titles}
        for i in range(0, len(ids), batch_size):
            for raw in revision_batches(client, ids[i:i+batch_size], issues):
                inspected += 1
                page_domains = set()
                hits = 0
                try:
                    for t in mwparserfromhell.parse(raw).filter_templates(recursive=True):
                        if name(t.name) not in names:
                            continue
                        hits += 1
                        total += 1
                        policy = link_policy(t)
                        routes[policy['route']] += 1
                        reasons[policy['reason']] += 1
                        domains.update(policy['domains'])
                        page_domains.update(policy['domains'])
                        if 'candidate' in policy:
                            candidates[policy['candidate']] += 1
                except Exception as exc:
                    issues['parse_' + type(exc).__name__] += 1
                unmatched += int(hits == 0)
                pages_by_domain.update(page_domains)
            if issues['request_budget_exhausted']:
                break
    complete = bool(ids and inspected == len(ids) and not issues and sum(routes.values()) == total and total)
    return {
        'schema': 'wikidebia-citation-domain-census-1',
        'status': 'complete_for_enumerated_raw_scope' if complete else 'incomplete',
        'scope': 'main namespace; raw Citation and template aliases on embeddedin pages; non-atomic',
        'started_utc': start, 'finished_utc': utc(), 'elapsed_seconds': round(monotonic()-t0, 2),
        'templates_and_aliases': len(titles), 'pages_enumerated': len(ids), 'pages_inspected': inspected,
        'pages_without_literal_template': unmatched, 'citation_occurrences': total,
        'routes': dict(routes), 'reasons': dict(reasons),
        'domain_occurrences': dict(domains.most_common()), 'domain_pages': dict(pages_by_domain.most_common()),
        'inactive_provider_candidates': dict(candidates.most_common()), 'issues': dict(issues),
        'wiki_requests': client.requests, 'decoded_bytes': client.network_body_bytes,
        'hosted_model_calls': 0, 'cited_url_requests': 0, 'auto_publish': False,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, default=ROOT/'reports-live/citation-domains.json')
    p.add_argument('--budget', type=int, default=1200)
    p.add_argument('--batch-size', type=int, default=30)
    a = p.parse_args()
    if not 1 <= a.batch_size <= 50 or not 1 <= a.budget <= 5000:
        p.error('invalid bounds')
    with TemporaryDirectory(prefix='citation-census-') as tmp:
        report = count(Client(Path(tmp), budget=a.budget, delay=.25), a.batch_size)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return int(report['status'] != 'complete_for_enumerated_raw_scope')


if __name__ == '__main__':
    raise SystemExit(main())
