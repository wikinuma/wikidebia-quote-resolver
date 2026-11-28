"""Mechanical boundary-recovery stress test on verified real candidate spans.

Reference spans are existing authenticated English *candidates*, not gold
translations. We artificially clip their word boundaries to measure how well
context policies restore the reference interval. No linguistic labels, texts,
coordinates, identifiers, or document excerpts leave the runner.
"""
from collections import Counter
import re

from alignment import text_and_starts
from review_context import exact_target, expand_target, adaptive_target

SCENARIOS = {
    'left_10': (.10, 0), 'right_10': (0, .10), 'both_10': (.10, .10),
    'left_25': (.25, 0), 'right_25': (0, .25), 'both_25': (.25, .25),
}


def clipped_span(text, lo, hi, left, right):
    """Keep a contiguous, nonempty whole-token interval inside the reference."""
    if not (0 <= lo < hi <= len(text)) or not (0 <= left < 1 and 0 <= right < 1
                                                and left + right < 1):
        raise ValueError('invalid_stress_input')
    words = list(re.finditer(r'\S+', text[lo:hi]))
    if len(words) < 8:
        return None
    a = max(1, int(len(words)*left)) if left else 0
    z = max(1, int(len(words)*right)) if right else 0
    if a+z >= len(words):
        return None
    return (lo+words[a].start() if a else lo,
            lo+words[len(words)-z-1].end() if z else hi)


def recover_reference(doc, reference, quote, scenario, limit=15000):
    """Measure restoration of known coordinates, never translation accuracy."""
    text, _ = text_and_starts(doc)
    lo, hi = reference['char_start'], reference['char_end']
    if exact_target(doc, lo, hi) != reference:
        raise ValueError('reference_evidence_changed')
    clipped = clipped_span(text, lo, hi, *scenario)
    if clipped is None:
        return None
    a, b = clipped
    seed = exact_target(doc, a, b)
    targets = {
        'original': seed,
        'expanded': expand_target(doc, seed, len(quote), limit),
        'adaptive': adaptive_target(doc, seed, quote, limit),
    }
    result = {}
    for mode, target in targets.items():
        start, end = target['char_start'], target['char_end']
        if not 0 <= start <= a < b <= end <= len(text) or end-start > limit:
            raise ValueError('invalid_stress_window')
        missing_left = max(0, start-lo)
        missing_right = max(0, hi-end)
        result[mode] = {
            'trials': 1,
            'fully_restored': int(not (missing_left or missing_right)),
            'missing_left': int(bool(missing_left)),
            'missing_right': int(bool(missing_right)),
            'missing_characters': missing_left+missing_right,
            'window_characters': end-start,
        }
    return result


def measure_boundary_recovery(packets, audit):
    totals = {scenario: {mode: Counter() for mode in
                         ('original', 'expanded', 'adaptive')}
              for scenario in SCENARIOS}
    cases, docs, seen = set(), set(), set()
    skipped_short = 0
    for packet in packets:
        rows = {row['id']: row for work in packet['payload']['groups']
                for chapter in work['chapters'] for row in chapter['citations']}
        for qid, source in packet['sources'].items():
            if len(source['occurrences']) != 1:
                raise ValueError('stress_requires_distinct_occurrences')
            cid = source['occurrences'][0]['case_id']
            for pid in source['candidates']:
                reference = packet['targets'][pid]
                key = (cid, reference['candidate_key'])
                if key in seen:
                    raise ValueError('duplicate_stress_reference')
                seen.add(key)
                url = reference['document']['url']
                doc = audit.document(url, 'en')
                if isinstance(doc, dict):
                    raise ValueError('stress_document_unavailable')
                quote = rows[qid]['fr']
                for label, scenario in SCENARIOS.items():
                    result = recover_reference(doc, reference, quote, scenario)
                    if result is None:
                        skipped_short += 1
                        continue
                    cases.add(cid)
                    docs.add(url)
                    for mode, stats in result.items():
                        totals[label][mode].update(stats)
    return {
        'scope': 'artificial_clipping_of_real_verified_candidate_spans_not_gold_translations',
        'reference_anchors': len(seen),
        'distinct_occurrences_with_trials': len(cases),
        'distinct_documents_with_trials': len(docs),
        'skipped_short_trials': skipped_short,
        'scenarios': {label: {mode: dict(stats) for mode, stats in arms.items()}
                      for label, arms in totals.items()},
        'semantic_equivalence': None,
        'rights_approved': 0,
        'auto_publish': False,
    }
