"""Frozen evaluation inputs only; never import from operational retrieval."""
import json
from resolver import ROOT

FIXTURES = ROOT / 'data/mia/benchmarks/20261126'


def check_hash(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('invalid_digest')


def load_prior_cohort():
    data = json.loads((FIXTURES / 'prior-cohort.json').read_bytes())
    rows = data['cases']
    if (data['schema'] != 'wikidebia-frozen-prior-cohort-1'
            or len(rows) != 100
            or len({r['id'] for r in rows}) != 100
            or len({r['citation_ref']['quote_sha256'] for r in rows}) != 100):
        raise ValueError('prior_cohort_mismatch')
    for r in rows:
        for k in ('quote_sha256', 'template_sha256'):
            check_hash(r['citation_ref'][k])
        if set(r) != {'id', 'wiki_title', 'citation_ref', 'metadata'}:
            raise ValueError('unexpected_evaluation_fields')
    return data


def load_semantic_cases():
    prior = {r['id']: r for r in load_prior_cohort()['cases']}
    data = json.loads((FIXTURES / 'semantic-labels.json').read_bytes())
    labels = data['labels']
    if (data['schema'] != 'wikidebia-frozen-semantic-labels-1'
            or len(labels) != 38 or len({r['id'] for r in labels}) != 38):
        raise ValueError('semantic_labels_mismatch')
    out = []
    for r in labels:
        p = r['label']
        for k in ('document_sha256', 'candidate_sha256'):
            check_hash(p[k])
        for f in p['fragments']:
            check_hash(f['text_sha256'])
            if not p['char_start'] <= f['char_start'] < f['char_end'] <= p['char_end']:
                raise ValueError('invalid_label_span')
        if not p['fragments']:
            raise ValueError('empty_label')
        out.append({**prior[r['id']], 'label': p})
    return out
