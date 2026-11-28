"""Expand independently verified target spans for human/AI review only.

Never search a new document or fabricate text. All offsets refer to the
existing parser's exact document text; original anchors remain visible.
"""
from collections import defaultdict
import re

from alignment import text_and_starts
from citation_registry import fingerprint, target_identity
from resolver import sha


def exact_target(doc, lo, hi):
    text, _ = text_and_starts(doc)
    if not 0 <= lo < hi <= len(text):
        raise ValueError('invalid_context_coordinates')
    return target_identity(doc, {
        'url': doc.url, 'revision': doc.revision, 'sha256': doc.digest,
        'char_start': lo, 'char_end': hi,
        'text_sha256': sha(text[lo:hi].encode()),
    })


def expand_target(doc, target, quote_chars, limit=15000, *, pad_cap=4000):
    """The input target must already have passed exact document/span checks.
    Add symmetric, length-adaptive context, bounded by the document and cap.
    Prefer paragraph boundaries close to the proposed limits, otherwise
    move inward to whitespace without shortening the original anchor.
    """
    text, _ = text_and_starts(doc)
    start, end = target['char_start'], target['char_end']
    width = end - start
    if width > limit or quote_chars < 1 or pad_cap < 0:
        raise ValueError('invalid_context_length')
    pad = min(pad_cap, max(300, (quote_chars * 3 + 4)//5,
                        quote_chars - width + 200), (limit-width)//2)
    lo, hi = max(0, start-pad), min(len(text), end+pad)
    if lo > 0 and lo < start:
        boundary = text.find('\n\n', lo, min(start, lo+180))
        if boundary >= 0:
            lo = boundary+2
        else:
            while lo < start and not text[lo-1].isspace():
                lo += 1
            while lo < start and text[lo].isspace():
                lo += 1
    if hi < len(text) and hi > end:
        boundary = text.rfind('\n\n', max(end, hi-180), hi)
        if boundary >= end:
            hi = boundary
        else:
            while hi > end and not text[hi].isspace():
                hi -= 1
            while hi > end and text[hi-1].isspace():
                hi -= 1
    lo, hi = min(lo, start), max(hi, end)
    if (lo, hi) == (start, end):
        return target
    return exact_target(doc, lo, hi)


def adaptive_target(doc, target, quote, limit=15000):
    """Conservative review-window heuristic, not a completeness decision.

    Keep the wide baseline for markedly short anchors or explicit omissions.
    Otherwise use a small safety margin, increased for open sentence edges.
    The original exact anchor is always preserved; no new search is done.
    """
    text, _ = text_and_starts(doc)
    start, end = target['char_start'], target['char_end']
    width = end-start
    size = len(quote)
    if width < .8 * size or re.search(r'\.{3,}|\u2026', quote):
        return expand_target(doc, target, size, limit)
    # Boundary signals are weak: even apparently complete seeds get context.
    closed = re.compile(r"""[.!?]["'\u2019\u201d)]*\s*$""")
    left_closed = (start == 0 or text[max(0,start-2):start] == '\n\n'
                   or bool(closed.search(text[max(0,start-32):start])))
    right_closed = (end == len(text) or text[end:end+2] == '\n\n'
                    or bool(closed.search(text[max(start,end-32):end])))
    cap = 120 if left_closed and right_closed else 250
    return expand_target(doc, target, size, limit, pad_cap=cap)


def coalesce_contexts(targets, passages, sources, docs, limit):
    """Share overlapping windows of the same exact document, when their
    union fits the passage cap. Keep each source's original seed coordinates
    relative to the shared window and preserve its candidate order.
    """
    before_chars = sum(p['length'] for p in passages.values())
    before_count = len(targets)
    groups = defaultdict(list)
    for pid, target in targets.items():
        groups[fingerprint(target['document'])].append(pid)
    mapping = {}
    new_targets, new_passages = {}, {}

    def store(members, lo, hi):
        pid = min(members, key=lambda p: int(p[1:]))
        t = targets[pid]
        if (lo, hi) != (t['char_start'], t['char_end']):
            doc = docs[t['document']['url']]
            t = exact_target(doc, lo, hi)
            text, _ = text_and_starts(doc)
            passage = {'document': passages[pid]['document'],
                       'text': text[lo:hi], 'length': hi-lo}
        else:
            passage = passages[pid]
        new_targets[pid] = t
        new_passages[pid] = passage
        mapping.update((member, pid) for member in members)

    for pids in groups.values():
        ordered = sorted(pids, key=lambda p: (targets[p]['char_start'],
                                              targets[p]['char_end'], p))
        members = []
        lo = hi = None
        for pid in ordered:
            a, b = targets[pid]['char_start'], targets[pid]['char_end']
            if members and a < hi and max(hi, b)-lo <= limit:
                members.append(pid)
                hi = max(hi, b)
            else:
                if members:
                    store(members, lo, hi)
                members, lo, hi = [pid], a, b
        if members:
            store(members, lo, hi)
    for source in sources.values():
        order = list(dict.fromkeys(mapping[p] for p in source['candidates']))
        relative = {}
        for pid, ranges in source.pop('seed_hints', {}).items():
            shared = mapping[pid]
            start = new_targets[shared]['char_start']
            dest = relative.setdefault(shared, [])
            for a, b in ranges:
                span = [a-start, b-start]
                if span not in dest:
                    dest.append(span)
        source['candidates'] = order
        source['seed_ranges'] = relative
    targets.clear()
    targets.update(new_targets)
    passages.clear()
    passages.update(new_passages)
    return {'distinct_windows_before_union': before_count,
            'distinct_windows_after_union': len(targets),
            'english_chars_before_union': before_chars,
            'english_chars_after_union': sum(p['length'] for p in passages.values())}
