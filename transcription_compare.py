"""Compare explicitly documented transcriptions without rewriting either text.

The current route is deliberately restricted to one known pair of transcriptions
of the same bibliographic edition. Matching non-whitespace characters is a
documentary comparison, not evidence that the French quotation is equivalent
or that either transcription is free of errors. Preserve both exact passages.
"""
from collections import Counter
from urllib.parse import quote

from alignment import text_and_starts
from citation_registry import check_target
from evidence_audit import STATE_MIRROR_TITLE
from review_context import exact_target

MIA_STATE_URL = 'https://www.marxists.org/archive/lenin/works/1919/jul/11.htm'
STATE_MIRROR_URL = 'https://wikirouge.net/texts/en/' + quote(STATE_MIRROR_TITLE.replace(' ', '_'))
DOCUMENTED_PAIRS = {MIA_STATE_URL: STATE_MIRROR_URL}
MAX_DOCUMENT_CHARS = 2_000_000


def compact(text):
    """Keep non-whitespace characters verbatim and their original offsets."""
    offsets = [i for i,c in enumerate(text) if not c.isspace()]
    return ''.join(text[i] for i in offsets), offsets


def boundaries(text, offsets):
    return {i for i in range(1,len(offsets)) if offsets[i]-offsets[i-1] > 1}


def whole_edges(text, lo, hi):
    """Avoid matching a substring starting or ending inside a word."""
    return not ((lo > 0 and text[lo-1].isalnum() and text[lo].isalnum())
                or (hi < len(text) and text[hi-1].isalnum() and text[hi].isalnum()))


def compare_spacing(original, target, alternate, *, max_passage_chars=15000):
    """Find a unique literal match ignoring whitespace only.

    No case-folding, punctuation normalisation, edit-distance or generated
    corrections. Accept a candidate comparison only when the other text
    preserves every existing internal space boundary and introduces at least
    one new boundary between letters. This does not prove the insertion
    correct, even for a documented pair.
    """
    if max_passage_chars < 1:
        raise ValueError('invalid_transcription_limit')
    if (original.url not in DOCUMENTED_PAIRS or
            DOCUMENTED_PAIRS[original.url] != alternate.url):
        raise ValueError('undocumented_transcription_pair')
    check_target(original,target)
    source, _ = text_and_starts(original)
    other, _ = text_and_starts(alternate)
    lo, hi = target['char_start'], target['char_end']
    raw = source[lo:hi]
    if (len(source) > MAX_DOCUMENT_CHARS or len(other) > MAX_DOCUMENT_CHARS
            or len(raw) > max_passage_chars):
        return {'status':'comparison_limit'}
    if raw != raw.strip() or not whole_edges(source,lo,hi):
        return {'status':'source_boundary_uncertain'}
    key, original_offsets = compact(raw)
    if len(key) < 40:
        return {'status':'reference_too_short'}
    haystack, offsets = compact(other)
    pos = haystack.find(key)
    if pos < 0:
        return {'status':'no_literal_spacing_match'}
    if haystack.find(key,pos+1) >= 0:
        return {'status':'ambiguous_literal_spacing_match'}
    a,b = offsets[pos], offsets[pos+len(key)-1]+1
    if b-a > max_passage_chars:
        return {'status':'comparison_limit'}
    if not whole_edges(other,a,b):
        return {'status':'alternate_boundary_uncertain'}
    actual = other[a:b]
    alt_key, alt_offsets = compact(actual)
    if alt_key != key:
        raise ValueError('transcription_comparison_identity_mismatch')
    old = boundaries(raw, original_offsets)
    new = boundaries(actual, alt_offsets)
    added = new-old
    if not old <= new or any(not(key[i-1].isalpha() and key[i].isalpha()) for i in added):
        return {'status':'mixed_spacing_differences'}
    if not added:
        return {'status':'no_added_word_boundaries'}
    return {'status':'spacing_variant_requires_review',
            'added_word_boundaries':len(added),
            'original_target':target,
            'alternate_target':exact_target(alternate,a,b),
            'semantic_equivalence':None,'rights':'unreviewed','auto_publish':False}


def compare_documented(target, load_document):
    """Fetch only the explicitly registered pair via the verified loader."""
    url = target['document']['url']
    if url not in DOCUMENTED_PAIRS:
        return {'status':'no_documented_pair'}
    original = load_document(url,'en')
    if isinstance(original,dict):
        return {'status':'original_unavailable'}
    alternate = load_document(DOCUMENTED_PAIRS[url],'en')
    if isinstance(alternate,dict):
        return {'status':'alternate_unavailable'}
    return compare_spacing(original,target,alternate)


def summarize_transcriptions(targets, load_document):
    """Aggregate-only, deduplicated diagnostic for CI."""
    seen = set()
    counts = Counter()
    added = 0
    for target in targets:
        if target['document']['url'] not in DOCUMENTED_PAIRS:
            continue
        key = target['candidate_key']
        if key in seen:
            continue
        seen.add(key)
        result = compare_documented(target,load_document)
        counts[result['status']] += 1
        added += result.get('added_word_boundaries',0)
    return {'distinct_registered_targets':len(seen),'statuses':dict(counts),
            'added_word_boundaries':added,'semantic_equivalence':None,
            'rights_approved':0,'auto_publish':False}
