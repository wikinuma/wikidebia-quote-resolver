from copy import deepcopy
import json
import unittest

from citation_registry import fingerprint
from public_quote_index import COMPLETE, ENDPOINTS, ROOTS, hashes, page_record, scan, validate_snapshot
from resolver import RequestBudgetExceeded


class Fake:
    def __init__(self, lang='fr'):
        self.lang = lang
        self.requests = self.network_body_bytes = 0
        self.calls = []
        self.root = ROOTS[lang]
        self.alias = self.root.split(':')[0] + ':Cite'
        field = 'citation' if lang == 'fr' else 'quote'
        model = self.root.split(':')[1]
        self.pages = {
            1: (101, 'First', '{{Argument|x={{'+model+'|'+field+'=A test passage.}}}}'),
            2: (102, 'Second', '{{Cite|'+field+'=A test passage.}}'),
            3: (103, 'Wrapper', '{{Wrapper}}'),
        }
        self.broken = False

    def api(self, endpoint, **kw):
        assert endpoint == ENDPOINTS[self.lang]
        self.requests += 1
        self.calls.append(kw)
        if kw.get('redirects'):
            return {'query': {'pages': [{'ns':10,'title':self.root}]}}
        if kw.get('prop') == 'redirects':
            rows = [{'ns':10,'title':self.alias}] if kw['titles']==self.root else []
            return {'query': {'pages':[{'ns':10,'title':kw['titles'],'redirects':rows}]}}
        if kw.get('list') == 'embeddedin':
            if kw['eititle'] == self.alias:
                return {'query': {'embeddedin':[{'pageid':i,'ns':0} for i in (2,3) if i in self.pages]}}
            if 'eicontinue' not in kw:
                return {'query': {'embeddedin':[{'pageid':1,'ns':0}]},
                        'continue': {'continue':'-||','eicontinue':'next'}}
            return {'query': {'embeddedin':[{'pageid':i,'ns':0} for i in self.pages if i != 1]}}
        assert kw.get('prop') == 'revisions'
        if self.broken:
            raise RequestBudgetExceeded('test')
        out = []
        for i in map(int, kw['pageids'].split('|')):
            rev,title,raw = self.pages[i]
            r = {'revid':rev}
            if 'content' in kw['rvprop']:
                r['slots'] = {'main':{'content':raw}}
            out.append({'pageid':i,'title':title,'ns':0,'revisions':[r]})
        return {'query': {'pages':out}}


class PublicIndexTests(unittest.TestCase):
    def test_both_languages_aliases_pagination_no_text_and_separate_pages(self):
        for lang in ('fr','en'):
            client = Fake(lang)
            value, summary = scan(client, lang, batch_size=2)
            self.assertEqual(value['status'], COMPLETE)
            self.assertEqual(summary['pages_inspected'], 3)
            self.assertEqual(summary['citations'], 2)
            self.assertEqual(summary['argument_pages'], 1)
            self.assertEqual(summary['pages_without_literal_template'], 1)
            self.assertEqual(summary['exact_literal_groups_across_pages'], 1)
            self.assertNotIn('A test passage.', json.dumps(value))
            self.assertNotIn('First', json.dumps(summary))
            self.assertEqual(len(validate_snapshot(value, fingerprint(value), lang)), 3)

    def test_incremental_refetch_changed_added_moved_removed_pages(self):
        client = Fake()
        old, _ = scan(client, 'fr')
        client.pages[2] = (202, 'Renamed', '{{Citation|citation=Changed passage}}')
        del client.pages[3]
        client.pages[4] = (204, 'New', '{{Citation|citation=New passage}}')
        client.calls.clear()
        new, summary = scan(client,'fr',previous=old,previous_sha=fingerprint(old))
        self.assertEqual(new['status'], COMPLETE)
        self.assertEqual(summary['pages_reused_same_revision'],1)
        self.assertEqual(summary['pages_fetched_content'],2)
        self.assertEqual(summary['pages_checked_revision_only'],3)
        self.assertEqual([p['page_id'] for p in new['pages']],[1,2,4])
        self.assertEqual(new['pages'][1]['title'],'Renamed')
        self.assertEqual(new['pages'][1]['revision'],202)
        self.assertEqual([c['pageids'] for c in client.calls
                          if 'content' in c.get('rvprop','')],['2|4'])
        self.assertNotIn('Changed passage', json.dumps(new))

    def test_metadata_failure_never_copies_stale_pages(self):
        client = Fake()
        old,_ = scan(client,'fr')
        client.broken = True
        new,summary = scan(client,'fr',previous=old,previous_sha=fingerprint(old))
        self.assertEqual(new['status'],'incomplete')
        self.assertEqual(new['pages'],[])
        self.assertEqual(summary['pages_reused_same_revision'],0)
        with self.assertRaises(ValueError):
            validate_snapshot(new,fingerprint(new),'fr')

    def test_empty_or_duplicate_field_is_explicitly_incomplete(self):
        client = Fake()
        client.pages[1] = (101,'First','{{Citation|citation=x|citation=y}}{{Citation|citation= }}')
        value,summary = scan(client,'fr')
        self.assertEqual(value['status'],'incomplete')
        self.assertEqual(summary['invalid_occurrences'],2)
        self.assertEqual(summary['invalid_occurrence_reasons'],
                         {'duplicate_quote_field':1,'empty_quote_field':1})
        self.assertTrue(summary['all_enumerated_pages_read'])
        self.assertFalse(summary['all_literal_occurrences_indexable'])
        self.assertEqual(summary['citations'],1)
        with self.assertRaises(ValueError):
            validate_snapshot(value,fingerprint(value),'fr')

    def test_incomplete_index_can_refresh_valid_pages_without_hiding_errors(self):
        client = Fake()
        client.pages[1] = (101,'First','{{Citation|ouvrage=Title only}}')
        old, summary = scan(client,'fr')
        self.assertEqual(old['status'],'incomplete')
        self.assertEqual(summary['invalid_occurrence_reasons'],{'missing_quote_field':1})
        refreshed, summary = scan(client,'fr',previous=old,previous_sha=fingerprint(old))
        self.assertEqual(refreshed['status'],'incomplete')
        self.assertEqual(summary['pages_reused_same_revision'],2)
        self.assertEqual(summary['pages_fetched_content'],1)
        self.assertEqual(summary['invalid_occurrence_reasons'],{'missing_quote_field':1})
        client.pages[1] = (201,'First','{{Citation|citation=Fixed}}')
        fixed,summary = scan(client,'fr',previous=refreshed,previous_sha=fingerprint(refreshed))
        self.assertEqual(fixed['status'],COMPLETE)
        self.assertEqual(summary['pages_reused_same_revision'],2)
        self.assertEqual(summary['pages_fetched_content'],1)
        self.assertEqual(summary['invalid_occurrence_reasons'],{})

    def test_missing_page_in_previous_snapshot_is_fetched(self):
        client = Fake()
        old,_ = scan(client,'fr')
        old['status'] = 'incomplete'
        old['pages'].pop(0)
        old['issues'] = {'missing_page_revision':1}
        new,summary = scan(client,'fr',previous=old,previous_sha=fingerprint(old))
        self.assertEqual(new['status'],COMPLETE)
        self.assertEqual(summary['pages_reused_same_revision'],2)
        self.assertEqual(summary['pages_fetched_content'],1)
        self.assertTrue(summary['all_enumerated_pages_read'])

    def test_literal_and_word_variants_keep_omissions(self):
        self.assertEqual(hashes(['A   passage.']),hashes(['A passage.\n']))
        self.assertNotEqual(hashes(['A passage.'])['literal_sha256'],
                            hashes(['a passage'])['literal_sha256'])
        self.assertEqual(hashes(['A passage.'])['words_sha256'],
                         hashes(['a passage'])['words_sha256'])
        self.assertEqual(hashes(['one','two'])['words_sha256'],
                         hashes(['one […] two'])['words_sha256'])
        self.assertNotEqual(hashes(['one','two'])['words_sha256'],
                            hashes(['one two'])['words_sha256'])
        self.assertEqual(hashes(['...'])['words_sha256'],None)

    def test_invalid_snapshot_refused_even_with_recomputed_digest(self):
        old,_ = scan(Fake(),'fr')
        changes = [lambda v: v.update(language='en'),
                   lambda v: v.update(pages_enumerated=99),
                   lambda v: v.update(algorithm='unknown'),
                   lambda v: v['pages'][0]['quotes'][0].update(text='unwanted'),
                   lambda v: v['pages'][1].update(page_id=1),
                   lambda v: v['pages'][0]['quotes'][0].update(ordinal=2),
                   lambda v: v.update(issues={'failed':1})]
        for mutate in changes:
            value = deepcopy(old)
            mutate(value)
            with self.assertRaises(ValueError):
                validate_snapshot(value,fingerprint(value),'fr')
        with self.assertRaises(ValueError):
            validate_snapshot(old,'0'*64,'fr')
        with self.assertRaises(ValueError):
            scan(Fake(),'en',previous=old,previous_sha=fingerprint(old))

    def test_alias_set_change_forces_content_refresh(self):
        client = Fake()
        old,_ = scan(client,'fr')
        client.alias = 'Modèle:Autre'
        _,summary = scan(client,'fr',previous=old,previous_sha=fingerprint(old))
        self.assertEqual(summary['pages_reused_same_revision'],0)
        self.assertEqual(summary['pages_fetched_content'],3)


if __name__ == '__main__':
    unittest.main()
