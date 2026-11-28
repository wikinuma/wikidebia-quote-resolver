"""Conservative novelty gate for already reviewed, sealed English windows.

Skip an occurrence only when one completed wide abstention contained *all*
new windows, with the same pinned French source and exact English document
identities. No text is exported or receipt registered. A changed source
status, new document identity or extended window is new evidence.
"""
from collections import defaultdict
import json

from citation_registry import fingerprint
from grouped_review import PACKET, REPLY, REJECT


SKIPPED = 'already_reviewed_without_new_evidence'


def source_identity(source):
    # Same occurrence, source identity and localization status. A different
    # source revision, page, text, or status is not an old review.
    return fingerprint([source['source_key'], source['occurrences'],
                        sorted(source.get('source_statuses', []))])


def windows(packet, source):
    return [(fingerprint(t['document']), t['char_start'], t['char_end'])
            for t in (packet['targets'][pid] for pid in source['candidates'])]


class ReviewedWindows:
    def __init__(self, db):
        self.by_source = defaultdict(list)
        for digest, body, response in db.execute(
                "SELECT digest,packet,reply FROM campaign_packets_v1 WHERE state='applied' ORDER BY rowid"):
            packet, reply = json.loads(body), json.loads(response)
            if (fingerprint(packet) != digest or packet.get('schema') != PACKET
                    or reply.get('schema') != REPLY or reply.get('packet_sha256') != digest):
                raise ValueError('novelty_historical_packet_mismatch')
            if (packet['payload'].get('progressive_review', {}).get('stage') == 'compact'
                    or 'context_stats' not in packet['payload']):
                # An initial or legacy narrow review is not a wide abstention.
                continue
            for decision in reply['decisions']:
                if (decision['decision'] not in REJECT
                        or decision['candidate'] is not None or decision['ranges'] != []):
                    continue
                source = packet['sources'][decision['id']]
                prior = windows(packet, source)
                if prior:
                    self.by_source[source_identity(source)].append((digest, prior))

    def covered_by(self, packet, source):
        current = windows(packet, source)
        if not current:
            return None
        for digest, prior in self.by_source.get(source_identity(source), []):
            if all(any(d == old_d and old_a <= a < b <= old_b
                       for old_d, old_a, old_b in prior)
                   for d, a, b in current):
                return digest
        return None

    def filter(self, packet):
        # Only a newly built, unsealed packet may be passed here.
        excluded = {qid: digest for qid, source in packet['sources'].items()
                    if (digest := self.covered_by(packet, source)) is not None}
        if not excluded:
            return packet
        payload = packet['payload']
        before = sum(p['length'] for p in payload['passages'].values())
        for qid, digest in excluded.items():
            source = packet['sources'].pop(qid)
            packet['skipped'].extend({'case_id': o['case_id'], 'status': SKIPPED,
                                      'previous_packet_sha256': digest}
                                     for o in source['occurrences'])
        groups = []
        for group in payload['groups']:
            chapters = []
            for chapter in group['chapters']:
                rows = [q for q in chapter['citations'] if q['id'] not in excluded]
                if rows:
                    chapters.append({**chapter, 'citations': rows})
            if chapters:
                groups.append({**group, 'chapters': chapters})
        payload['groups'] = groups
        used = {pid for s in packet['sources'].values() for pid in s['candidates']}
        packet['targets'] = {p: t for p, t in packet['targets'].items() if p in used}
        payload['passages'] = {p: t for p, t in payload['passages'].items() if p in used}
        docs = {p['document'] for p in payload['passages'].values()}
        payload['documents'] = {d: u for d, u in payload['documents'].items() if d in docs}
        after = sum(p['length'] for p in payload['passages'].values())
        # Context/transcription statistics describe the pre-filter build.
        # Keep their original meaning and expose the separate actual saving.
        packet['campaign_novelty'] = {'skipped_occurrences': len(excluded),
                                     'english_chars_before_filter': before,
                                     'english_chars_after_filter': after}
        if 'context_stats' in payload:
            payload['context_stats']['measurement_scope'] = 'before_novelty_filter'
        if 'transcription_comparison' in payload:
            payload['transcription_comparison']['measurement_scope'] = 'before_novelty_filter'
        return packet
