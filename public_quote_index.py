"""Anonymous wiki-wide inventory of literal quotation fingerprints.

Reuse the domain census's paginated alias discovery and batched revisions.
No cited-site requests, model calls, text persistence or wiki writes.
An inventory is non-atomic and is not a publication authorization.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unicodedata

import mwparserfromhell
from alignment_store import encode
from citation_domain_census import aliases, embedded_pages, name, revision_batches
from citation_registry import fingerprint, require_digest
from grouped_review import private_dir, write_private
from registry_transfer import read_private_json
from resolver import Client

SCHEMA = 'wikidebia-public-quote-index-1'
ALGORITHM = 'literal-nfc-space-and-omission-aware-words-1'
COMPLETE = 'complete_for_enumerated_raw_scope'
ENDPOINTS = {lang: f'https://{lang}.wikidebates.org/w/api.php' for lang in ('fr', 'en')}
ROOTS = {'fr': 'Modèle:Citation', 'en': 'Template:Quote'}
PARAMETERS = {'fr': 'citation', 'en': 'quote'}
OMISSION = re.compile(r'\[\s*(?:\.{3,}|…)+\s*\]|\(\s*(?:\.{3,}|…)+\s*\)|\.{3,}|…')
MAX_PAGES = 100000
MAX_BYTES = 64 * 1024 * 1024


def utc():
    return datetime.now(timezone.utc).isoformat()


def normalized(text):
    return ' '.join(unicodedata.normalize('NFC', text).split())


def hashes(fragments):
    form = [normalized(s) for s in fragments]
    if not form or not all(form):
        raise ValueError('empty_quote')
    seq = []
    for i, part in enumerate(p for f in form for p in OMISSION.split(f)):
        if i:
            seq.append('\0')
        seq.extend(re.findall(r"[^\W_]+(?:'[^\W_]+)*",
                              part.casefold().replace('’', "'")))
    return {'literal_sha256': fingerprint(form),
            'words_sha256': fingerprint(seq) if any(w != '\0' for w in seq) else None}


def page_record(row, lang, template_names, *, invalid_reasons=None):
    record = {k: row[k] for k in ('page_id', 'title', 'revision')}
    nodes = mwparserfromhell.parse(row['raw']).filter_templates(recursive=True)
    record['argument_template_present'] = any(name(t.name) == 'Argument' for t in nodes)
    record['quotes'] = []
    record['invalid_occurrences'] = 0
    ordinal = 0
    for t in nodes:
        if name(t.name) not in template_names:
            continue
        ordinal += 1
        params = [str(p.value) for p in t.params if str(p.name).strip() == PARAMETERS[lang]]
        if len(params) != 1:
            record['invalid_occurrences'] += 1
            if invalid_reasons is not None:
                invalid_reasons['missing_quote_field' if not params else 'duplicate_quote_field'] += 1
            continue
        try:
            record['quotes'].append({'ordinal': ordinal, **hashes([params[0]])})
        except ValueError:
            record['invalid_occurrences'] += 1
            if invalid_reasons is not None:
                invalid_reasons['empty_quote_field'] += 1
    return record


def integer(value, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError('invalid_index_integer')


def validate_snapshot(value, expected_sha, lang, *, require_complete=True):
    require_digest(expected_sha)
    if fingerprint(value) != expected_sha:
        raise ValueError('index_digest_mismatch')
    if (not isinstance(value, dict) or set(value) != {
            'schema', 'algorithm', 'unicode_version', 'language', 'status',
            'started_utc', 'finished_utc', 'template_titles', 'pages_enumerated',
            'pages', 'issues'}
            or value['schema'] != SCHEMA or value['algorithm'] != ALGORITHM
            or value['unicode_version'] != unicodedata.unidata_version
            or value['language'] != lang or lang not in ROOTS
            or value['status'] not in {COMPLETE, 'incomplete'}):
        raise ValueError('invalid_index_schema')
    if require_complete and value['status'] != COMPLETE:
        raise ValueError('index_incomplete')
    times = [datetime.fromisoformat(value[k]) for k in ('started_utc', 'finished_utc')]
    if any(t.tzinfo is None for t in times) or times[0] > times[1]:
        raise ValueError('invalid_index_timestamps')
    titles = value['template_titles']
    if (not isinstance(titles, list) or not all(isinstance(t, str) and t for t in titles)
            or titles != sorted(set(titles)) or len(titles) > 100):
        raise ValueError('invalid_index_aliases')
    integer(value['pages_enumerated'], 0)
    if not isinstance(value['issues'], dict):
        raise ValueError('invalid_index_issues')
    for k, n in value['issues'].items():
        if not isinstance(k, str) or not k:
            raise ValueError('invalid_index_issue')
        integer(n, 0)
    pages = value['pages']
    if not isinstance(pages, list) or len(pages) > MAX_PAGES or len(pages) > value['pages_enumerated']:
        raise ValueError('invalid_index_pages')
    ids, page_titles = set(), set()
    previous = 0
    invalid = 0
    for p in pages:
        if not isinstance(p, dict) or set(p) != {
                'page_id', 'title', 'revision', 'argument_template_present',
                'quotes', 'invalid_occurrences'}:
            raise ValueError('invalid_index_page')
        integer(p['page_id'])
        integer(p['revision'])
        integer(p['invalid_occurrences'], 0)
        invalid += p['invalid_occurrences']
        if (p['page_id'] <= previous or not isinstance(p['title'], str)
                or not p['title'].strip() or p['title'] in page_titles
                or type(p['argument_template_present']) is not bool
                or not isinstance(p['quotes'], list)):
            raise ValueError('invalid_index_page_identity')
        previous = p['page_id']
        ids.add(p['page_id'])
        page_titles.add(p['title'])
        last = 0
        for q in p['quotes']:
            if not isinstance(q, dict) or set(q) != {'ordinal', 'literal_sha256', 'words_sha256'}:
                raise ValueError('invalid_index_quote')
            integer(q['ordinal'])
            if q['ordinal'] <= last or q['ordinal'] > len(p['quotes']) + p['invalid_occurrences']:
                raise ValueError('invalid_index_ordinal')
            last = q['ordinal']
            require_digest(q['literal_sha256'])
            if q['words_sha256'] is not None:
                require_digest(q['words_sha256'])
    if value['status'] == COMPLETE and (
            not titles or ROOTS[lang] not in titles or len(ids) != value['pages_enumerated']
            or any(value['issues'].values()) or invalid):
        raise ValueError('contradictory_index_coverage')
    return {p['page_id']: p for p in pages}


def summarize(value, *, reused=0, fetched=0, metadata=0, requests=0, decoded_bytes=0,
              invalid_reasons=None):
    literal, words = defaultdict(set), defaultdict(set)
    for p in value['pages']:
        for q in p['quotes']:
            literal[q['literal_sha256']].add(p['page_id'])
            if q['words_sha256'] is not None:
                words[q['words_sha256']].add(p['page_id'])
    return {'schema': value['schema'], 'language': value['language'],
            'status': value['status'], 'snapshot_sha256': fingerprint(value),
            'pages_enumerated': value['pages_enumerated'], 'pages_inspected': len(value['pages']),
            'argument_pages': sum(p['argument_template_present'] for p in value['pages']),
            'pages_without_literal_template': sum(not p['quotes'] and not p['invalid_occurrences']
                                                  for p in value['pages']),
            'citations': sum(len(p['quotes']) for p in value['pages']),
            'invalid_occurrences': sum(p['invalid_occurrences'] for p in value['pages']),
            'exact_literal_groups_across_pages': sum(len(s) > 1 for s in literal.values()),
            'same_words_groups_across_pages': sum(len(s) > 1 for s in words.values()),
            'pages_reused_same_revision': reused, 'pages_fetched_content': fetched,
            'pages_checked_revision_only': metadata,
            'wiki_requests': requests, 'decoded_bytes': decoded_bytes,
            'issues': value['issues'], 'non_atomic': True, 'hosted_model_calls': 0,
            'all_enumerated_pages_read': (
                bool(value['template_titles']) and ROOTS[value['language']] in value['template_titles']
                and len(value['pages']) == value['pages_enumerated']
                and not any(n for k,n in value['issues'].items() if k != 'invalid_quote_parameter')),
            'all_literal_occurrences_indexable': value['status'] == COMPLETE,
            'invalid_occurrence_reasons': dict(invalid_reasons or {}),
            'cited_url_requests': 0, 'publication_authorized': False}


def scan(client, lang, *, batch_size=30, previous=None, previous_sha=None):
    if lang not in ROOTS or not 1 <= batch_size <= 50:
        raise ValueError('invalid_index_scope')
    if (previous is None) != (previous_sha is None):
        raise ValueError('previous_index_requires_digest')
    # A previous incomplete snapshot is not a proof of absence. However, an
    # individually checked valid page can be reused after its current revision
    # and title are independently read. Re-enumerate and reread every missing
    # or malformed page. Never turn its former errors into valid coverage.
    old = {} if previous is None else validate_snapshot(previous, previous_sha, lang,
                                                        require_complete=False)
    started, issues, invalid_reasons = utc(), Counter(), Counter()
    titles, ids, records = [], [], {}
    reused = fetched = metadata = 0
    endpoint = ENDPOINTS[lang]
    try:
        titles = aliases(client, endpoint=endpoint, template=ROOTS[lang])
        ids = embedded_pages(client, titles, endpoint=endpoint)
        if len(ids) > MAX_PAGES:
            raise ValueError('index_page_limit')
    except Exception as exc:
        issues['enumeration_' + type(exc).__name__] += 1
    if not issues:
        # Aliases and parser identity are part of the cache contract.
        can_reuse = previous is not None and previous['template_titles'] == titles
        names = {name(t) for t in titles}
        for start in range(0, len(ids), batch_size):
            batch = ids[start:start+batch_size]
            pending = batch
            if can_reuse:
                pending = []
                for current in revision_batches(client, batch, issues, endpoint=endpoint,
                                                identity=True, content=False):
                    metadata += 1
                    pid = current['page_id']
                    prior = old.get(pid)
                    if (prior is not None and not prior['invalid_occurrences']
                            and all(prior[k] == current[k] for k in current)):
                        records[pid] = prior
                        reused += 1
                    else:
                        pending.append(pid)
            if pending:
                for row in revision_batches(client, pending, issues, endpoint=endpoint,
                                            identity=True):
                    fetched += 1
                    try:
                        record = page_record(row, lang, names, invalid_reasons=invalid_reasons)
                        records[row['page_id']] = record
                        if record['invalid_occurrences']:
                            issues['invalid_quote_parameter'] += record['invalid_occurrences']
                    except Exception as exc:
                        issues['parse_' + type(exc).__name__] += 1
            if issues['request_budget_exhausted']:
                break
    status = COMPLETE if not any(issues.values()) and len(records) == len(ids) else 'incomplete'
    # Only an exhausted enumeration of a valid template can be complete.
    if not titles or ROOTS[lang] not in titles:
        status = 'incomplete'
    value = {'schema': SCHEMA, 'algorithm': ALGORITHM,
             'unicode_version': unicodedata.unidata_version, 'language': lang, 'status': status,
             'started_utc': started, 'finished_utc': utc(), 'template_titles': titles,
             'pages_enumerated': len(ids), 'pages': [records[k] for k in sorted(records)],
             'issues': {k: v for k, v in sorted(issues.items()) if v}}
    validate_snapshot(value, fingerprint(value), lang, require_complete=False)
    summary = summarize(value, reused=reused, fetched=fetched, metadata=metadata,
                        requests=client.requests, decoded_bytes=client.network_body_bytes,
                        invalid_reasons=invalid_reasons)
    return value, summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--language', required=True, choices=sorted(ROOTS))
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--summary', type=Path)
    p.add_argument('--previous', type=Path)
    p.add_argument('--previous-sha256')
    p.add_argument('--budget', type=int, default=1200)
    p.add_argument('--batch-size', type=int, default=30)
    a = p.parse_args(argv)
    if not 1 <= a.budget <= 5000 or not 1 <= a.batch_size <= 50:
        p.error('invalid bounds')
    if (a.previous is None) != (a.previous_sha256 is None):
        p.error('--previous and --previous-sha256 are required together')
    previous = read_private_json(a.previous) if a.previous is not None else None
    with TemporaryDirectory(prefix='public-quote-census-') as tmp:
        value, summary = scan(Client(Path(tmp), budget=a.budget, delay=.25),
                              a.language, batch_size=a.batch_size,
                              previous=previous, previous_sha=a.previous_sha256)
    if len(encode(value).encode('utf-8')) + 1 > MAX_BYTES:
        raise ValueError('index_byte_limit')
    write_private(private_dir(a.out.parent) / a.out.name, value)
    if a.summary:
        # Aggregate only: no titles, page IDs, quotation hashes or text.
        a.summary.parent.mkdir(parents=True, exist_ok=True)
        a.summary.write_text(json.dumps(summary, sort_keys=True)+'\n', encoding='utf-8')
    print(json.dumps(summary, sort_keys=True), flush=True)
    return int(value['status'] != COMPLETE)


if __name__ == '__main__':
    raise SystemExit(main())
