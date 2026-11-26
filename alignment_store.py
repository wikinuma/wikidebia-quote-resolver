"""Reconstructible document alignment cache, not a semantic decision registry."""
from functools import lru_cache
import json
from pathlib import Path
import sys
import unicodedata

from resolver import sha


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


@lru_cache(maxsize=1)
def algorithm():
    # Include normalization/anchor definitions as well as the alignment code.
    # Conservative invalidation on any change in either implementation.
    root = Path(__file__).resolve().parent
    return sha(encode({
        'schema': 'alignment-store-1',
        'alignment': sha((root/'alignment.py').read_bytes()),
        'resolver': sha((root/'resolver.py').read_bytes()),
        'python': list(sys.version_info[:2]),
        'unicode': unicodedata.unidata_version,
    }).encode())


def document_identity(doc):
    return {
        'url': doc.url, 'body_sha256': doc.digest, 'revision': doc.revision,
        # Keep block boundaries, not just the concatenation: segmentation depends on them.
        'blocks_sha256': sha(encode(doc.blocks).encode()),
    }


def alignment_key(fr, en, kind, max_cells):
    return sha(encode({'algorithm': algorithm(), 'fr': document_identity(fr),
                       'en': document_identity(en), 'kind': kind,
                       'max_cells': max_cells}).encode())


class AlignmentStore:
    """A cache table in the existing WorkLibrary DB. No passage text stored.

    Exact identity and integrity are mandatory. Changed inputs are cache misses;
    corrupted entries are discarded and recomputed. Never establish that the
    documents are parallel or that any quote is equivalent.
    """
    def __init__(self, db):
        self.db = db
        self.stats = {'alignment_disk_hits': 0, 'alignment_disk_misses': 0,
                      'alignment_disk_writes': 0, 'alignment_disk_invalid': 0}
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS document_alignments_v1 '
                            '(cache_key TEXT PRIMARY KEY, result TEXT NOT NULL, '
                            'result_sha256 TEXT NOT NULL)')

    def load(self, key):
        row = self.db.execute(
            'SELECT result, result_sha256 FROM document_alignments_v1 WHERE cache_key=?',
            (key,)).fetchone()
        if row is not None:
            raw, fingerprint = row
            try:
                if sha(raw.encode()) != fingerprint:
                    raise ValueError('alignment_cache_corrupt')
                value = json.loads(raw)
                if (value.get('status') != 'aligned_candidates_only'
                        or not isinstance(value.get('links'), list) or not value['links']):
                    raise ValueError('alignment_cache_invalid')
                self.stats['alignment_disk_hits'] += 1
                return value
            except (ValueError, TypeError, AttributeError):
                with self.db:
                    self.db.execute('DELETE FROM document_alignments_v1 WHERE cache_key=?',
                                    (key,))
                self.stats['alignment_disk_invalid'] += 1
        self.stats['alignment_disk_misses'] += 1
        return None

    def save(self, key, result):
        # Only successful deterministic calculations. No failures/negative
        # conclusions and no passage/citation/semantic labels.
        if result.get('status') != 'aligned_candidates_only':
            return
        raw = encode(result)
        with self.db:
            cur = self.db.execute(
                'INSERT OR IGNORE INTO document_alignments_v1 VALUES (?,?,?)',
                (key, raw, sha(raw.encode())))
        self.stats['alignment_disk_writes'] += cur.rowcount
