#!/usr/bin/env python3
"""Private review archive: shared document windows, independent packet digests.

Export only review views, never the private SQLite manifests, HTTP cache or
full documents. Original candidate coordinates and reply format are unchanged.
"""
import argparse
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

from alignment_store import encode
from citation_registry import fingerprint
from grouped_review import review_view
from resolver import sha

SCHEMA = 'wikidebia-review-bundle-1'


def build_bundle(pairs, inventory_sha, max_shared_chars=30000):
    """Merge overlapping windows across packets only when document identity
    and overlapping characters agree. Preserve original passage offsets."""
    if max_shared_chars < 1 or not pairs:
        raise ValueError('invalid_bundle_size')
    views = {}
    groups = defaultdict(list)
    entries = []
    occurrences = set()
    before_chars = 0
    before_windows = 0
    for digest, packet in pairs:
        if fingerprint(packet) != digest or digest in views:
            raise ValueError('bundle_packet_digest_mismatch')
        view = deepcopy(review_view(packet))
        views[digest] = view
        ids = [o['case_id'] for source in packet['sources'].values()
               for o in source['occurrences']]
        if len(ids) != len(set(ids)) or occurrences.intersection(ids):
            raise ValueError('bundle_duplicate_occurrence')
        occurrences.update(ids)
        entries.append({'packet_sha256': digest, 'file': 'packets/'+digest+'.json',
                        'occurrences': len(ids), 'view_sha256': fingerprint(view)})
        for pid, passage in view['passages'].items():
            t = packet['targets'][pid]
            a, b = t['char_start'], t['char_end']
            text = passage['text']
            if (b-a != len(text) or len(text) != passage['length']
                    or sha(text.encode()) != t['text_sha256']):
                raise ValueError('bundle_passage_mismatch')
            groups[fingerprint(t['document'])].append((a,b,digest,pid,text))
            before_chars += len(text)
            before_windows += 1

    shared = {}

    def store(members, start, end, text):
        rid = 't'+str(len(shared)+1)
        shared[rid] = text
        for a,b,digest,pid,_ in members:
            passage = views[digest]['passages'][pid]
            # Relative offsets are into this shared string only. Reviewer
            # selection offsets still refer to the sliced original passage.
            views[digest]['passages'][pid] = {
                'document': passage['document'], 'length': passage['length'],
                'text_ref': rid, 'start': a-start, 'end': b-start}

    for windows in groups.values():
        members = []
        start = end = 0
        merged = ''
        for item in sorted(windows, key=lambda x: (x[0],x[1],x[2],x[3])):
            a,b,_,_,text = item
            if members and a <= end and max(end,b)-start <= max_shared_chars:
                overlap = min(end,b)-a
                if merged[a-start:a-start+overlap] != text[:overlap]:
                    raise ValueError('bundle_inconsistent_document_overlap')
                if b > end:
                    merged += text[overlap:]
                    end = b
                members.append(item)
            else:
                if members:
                    store(members,start,end,merged)
                members, start, end, merged = [item],a,b,text
        if members:
            store(members,start,end,merged)

    index = {'schema': SCHEMA, 'inventory_sha256': inventory_sha,
             'packets': entries, 'occurrences': len(occurrences),
             'windows_before_sharing': before_windows,
             'shared_windows': len(shared),
             'english_chars_before_sharing': before_chars,
             'english_chars_after_sharing': sum(map(len,shared.values())),
             'shared_sha256': fingerprint(shared), 'auto_publish': False,
             'instructions': ('Process one or more packets. Reconstruct each original '
                              'passage using texts[text_ref][start:end] (Unicode offsets). '
                              'Return a JSON array of ordinary complete per-packet replies. '
                              'Never change packet IDs, invent passages, or return quotes. '
                              'Review only supplied material. Replies can be imported incrementally.')}
    return index, views, shared


def write_bundle(path, pairs, inventory_sha):
    index, views, shared = build_bundle(pairs, inventory_sha)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
    try:
        with ZipFile(tmp,'w',compression=ZIP_DEFLATED,compresslevel=6) as z:
            z.writestr('index.json',encode(index)+'\n')
            z.writestr('texts.json',encode(shared)+'\n')
            for entry in index['packets']:
                z.writestr(entry['file'],encode(views[entry['packet_sha256']])+'\n')
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return index


def read_packet(path, number):
    """Reconstruct and check a standalone review view without extracting files."""
    with ZipFile(path) as z:
        index = json.loads(z.read('index.json'))
        if index['schema'] != SCHEMA or not 1 <= number <= len(index['packets']):
            raise ValueError('invalid_bundle_packet_number')
        entry = index['packets'][number-1]
        digest = entry['packet_sha256']
        shared = json.loads(z.read('texts.json'))
        if fingerprint(shared) != index['shared_sha256']:
            raise ValueError('bundle_shared_digest_mismatch')
        view = json.loads(z.read('packets/'+digest+'.json'))
        for pid, passage in view['passages'].items():
            text = shared[passage['text_ref']]
            a,b = passage['start'],passage['end']
            if (type(a) is not int or type(b) is not int or not 0 <= a < b <= len(text)
                    or b-a != passage['length']):
                raise ValueError('bundle_invalid_slice')
            view['passages'][pid] = {'document': passage['document'],
                                      'length': b-a,'text': text[a:b]}
        if view['packet_sha256'] != digest or fingerprint(view) != entry['view_sha256']:
            raise ValueError('bundle_view_digest_mismatch')
        return view


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('archive',type=Path)
    p.add_argument('--packet',type=int,required=True,help='1-based packet number')
    args = p.parse_args()
    print(encode(read_packet(args.archive,args.packet)))


if __name__ == '__main__':
    main()
