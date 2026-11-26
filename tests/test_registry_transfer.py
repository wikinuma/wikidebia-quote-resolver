from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from citation_registry import CitationRegistry, fingerprint, source_identity
from grouped_review import REPLY
from prepared_resolution import CANDIDATES, REVIEWED, RETRY, INSUFFICIENT
from prepare_citations import Campaign
from registry_transfer import export_bundle, import_bundle, inventory, read_private_json
from test_citation_registry import RAW, QUOTE, ENGLISH, ref, candidate, document, review
from test_translation_handoff import locked, request
from translation_handoff import prepare_handoff
from work_library import WorkLibrary


class RegistryTransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.a = WorkLibrary(root / 'a.sqlite3')
        self.b = WorkLibrary(root / 'b.sqlite3')
        self.addCleanup(self.a.db.close)
        self.addCleanup(self.b.db.close)
        self.source = CitationRegistry(self.a.db)
        self.dest = CitationRegistry(self.b.db)
        self.doc = document()
        self.rf = ref(RAW, 7)

    def record(self, registry, raw=RAW, doc=None, reviewer='synthetic'):
        doc = doc or self.doc
        rf = ref(raw, 7)
        c = candidate(doc)
        return registry.record(raw, rf, doc, c, {**review(raw, rf, doc, c),
                                                'reviewer': reviewer})

    def transfer(self, bundle):
        return import_bundle(self.dest, bundle, fingerprint(bundle))

    def test_campaign_to_separate_translation_environment(self):
        identity = source_identity(RAW, self.rf)
        case = {'id': 'campaign', 'wiki_title': 'Fixture', 'citation_ref': self.rf,
                'source_key': identity['source_key'], 'mia_url': identity['mia_url']}
        campaign = Campaign(self.a, {'cases': [case], 'pages': [],
                                    'manifest_sha256': 'b'*64})
        calls = []
        def retrieve(case):
            calls.append(case['id'])
            return {'id': case['id'], 'status': 'candidate_review_required',
                    'review_packet': {'candidates': [candidate(self.doc)]}}
        retr = SimpleNamespace(client=SimpleNamespace(events=[]), retrieve=retrieve)
        raw = lambda title, revision: RAW
        audit = SimpleNamespace(document=lambda url, lang: self.doc, publication={})
        self.assertEqual(campaign.search_one(case, retr, raw, audit), CANDIDATES)
        digest, packet = campaign.prepare(raw, audit)
        decisions = []
        for q, src in packet['sources'].items():
            pid = src['candidates'][0]
            length = packet['payload']['passages'][pid]['length']
            decisions.append({'id': q, 'decision': 'complete_equivalent',
                              'candidate': pid, 'ranges': [[0, length]]})
        campaign.apply({'schema': REPLY, 'packet_sha256': digest, 'reviewer': 'synthetic',
                        'decisions': decisions}, raw, audit)
        bundle = export_bundle(self.source)
        self.assertEqual(len(bundle['entries']), 1)
        self.assertNotIn(QUOTE, str(bundle))
        self.assertNotIn(ENGLISH, str(bundle))
        self.assertEqual(self.transfer(bundle)['inserted'], 1)
        self.assertEqual(self.transfer(bundle)['already_present'], 1)
        calls.clear()
        result = prepare_handoff(request(locked()), self.dest, retr, raw, audit,
                                 search_missing=False)
        row = result['rows'][0]
        self.assertEqual(row['state'], REVIEWED)
        self.assertEqual(row['published_passage']['fragments'][0]['text'], ENGLISH)
        self.assertEqual(calls, [])
        self.assertEqual(result['packets'], [])
        self.assertFalse(row['published_passage']['auto_publish'])
        self.assertEqual(row['published_passage']['rights'], 'unreviewed')
        self.doc = document(digest='c'*64)
        stale = prepare_handoff(request(locked()), self.dest, retr, raw, audit,
                                search_missing=False)['rows'][0]
        self.assertEqual(stale['state'], RETRY)
        self.assertNotIn('published_passage', stale)

    def test_scoped_incremental_export(self):
        first = self.record(self.source)
        other_raw = RAW.replace(QUOTE, 'Une citation différente avec assez de mots.')
        other = self.record(self.source, raw=other_raw)
        scoped = export_bundle(self.source, source_keys=[first['source_key']])
        self.assertEqual([r['entry_sha256'] for r in scoped['entries']], [first['entry_sha256']])
        self.assertEqual(export_bundle(self.source, source_keys=[])['entries'], [])
        self.transfer(scoped)
        incremental = export_bundle(self.source, known=inventory(self.dest))
        self.assertEqual([r['entry_sha256'] for r in incremental['entries']], [other['entry_sha256']])
        self.transfer(incremental)
        self.assertEqual(inventory(self.source), inventory(self.dest))
        self.assertEqual(export_bundle(self.source, known=inventory(self.dest))['entries'], [])
        self.assertEqual(self.dest.lookup(other_raw, ref(other_raw, 7))['status'], 'reviewed_receipt_found')

    def test_conflicts_are_preserved_not_overwritten(self):
        self.record(self.dest)
        self.record(self.source, doc=document(digest='c'*64))
        result = self.transfer(export_bundle(self.source))
        self.assertEqual(result['conflicting_sources'], 1)
        self.assertEqual(self.dest.lookup(RAW, self.rf)['status'], 'ambiguous_reviewed_passages')
        self.assertEqual(len(inventory(self.dest)['entry_sha256s']), 2)

    def test_identical_passage_different_reviewer_not_conflict(self):
        self.record(self.dest)
        self.record(self.source, reviewer='another synthetic reviewer')
        result = self.transfer(export_bundle(self.source))
        self.assertEqual(result['conflicting_sources'], 0)
        self.assertEqual(self.dest.lookup(RAW, self.rf)['receipt_count'], 2)

    def test_digest_and_schema_and_forged_identity_rejected(self):
        self.record(self.source)
        original = export_bundle(self.source)
        with self.assertRaises(ValueError):
            import_bundle(self.dest, original, '0'*64)
        changes = [
            lambda e: e.update(rights='authorized'),
            lambda e: e.update(auto_publish=True),
            lambda e: e.update(schema='unknown'),
            lambda e: e['source'].update(source_key='0'*64),
            lambda e: e['source'].update(provider='other'),
            lambda e: e['target'].update(candidate_key='0'*64),
            lambda e: e['target']['document'].update(url='https://example.org/a'),
            lambda e: e['fragments'][0].update(char_end=999999),
            lambda e: e['fragments'][0].update(text_sha256='0'*64),
            lambda e: e['fragments'][0].update(text='not allowed'),
        ]
        for change in changes:
            with self.subTest(change=change):
                b = deepcopy(original)
                row = b['entries'][0]
                change(row['entry'])
                row['entry_sha256'] = fingerprint(row['entry'])
                with self.assertRaises(ValueError):
                    self.transfer(b)
                self.assertEqual(inventory(self.dest)['entry_sha256s'], [])

    def test_transaction_rolls_back_on_existing_corruption(self):
        self.record(self.source)
        self.record(self.source, raw=RAW.replace(QUOTE, 'Autre citation de test.'))
        b = export_bundle(self.source)
        last = b['entries'][-1]
        with self.b.db:
            self.b.db.execute('INSERT INTO prepared_citations_v1 VALUES (?,?,?)',
                              (last['entry_sha256'], last['entry']['source']['source_key'], '{}'))
        with self.assertRaises(ValueError):
            self.transfer(b)
        self.assertEqual(self.b.db.execute('SELECT count(*) FROM prepared_citations_v1').fetchone()[0], 1)

    def test_duplicate_or_unordered_entries_rejected(self):
        self.record(self.source)
        b = export_bundle(self.source)
        b['entries'].append(deepcopy(b['entries'][0]))
        with self.assertRaises(ValueError):
            self.transfer(b)
        self.assertEqual(inventory(self.dest)['entry_sha256s'], [])

    def test_json_rejects_duplicate_keys_and_noncanonical_bytes(self):
        path = Path(self.tmp.name) / 'transfer.json'
        for raw in ('{"a":1,"a":2}\n', '{ "a": 1 }\n'):
            path.write_text(raw, encoding='utf-8')
            with self.assertRaises(ValueError):
                read_private_json(path)

    def test_source_changes_still_miss(self):
        self.record(self.source)
        self.transfer(export_bundle(self.source))
        changed = RAW.replace('1900', '1901')
        self.assertEqual(self.dest.lookup(changed, ref(changed, 7))['status'], 'miss')
