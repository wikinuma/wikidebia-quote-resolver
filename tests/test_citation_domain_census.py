import unittest
import mwparserfromhell
from citation_policy import link_policy
from citation_domain_census import count, iterate, name
from resolver import WD_API


def policy(link):
    return link_policy(mwparserfromhell.parse('{{Citation|citation=test|lien='+link+'}}').filter_templates()[0])


class PolicyTests(unittest.TestCase):
    def test_mia_literal(self):
        for value in ('https://www.marxists.org/fr/a.htm', 'http://marxists.org/fr/a.htm',
                      '[https://www.marxists.org/fr/a.htm source]', '//marxists.org/fr/a.htm',
                      'marxists.org/fr/a.htm'):
            with self.subTest(value=value):
                self.assertEqual(policy(value)['route'], 'mia')
    def test_host_spoofing_and_label(self):
        for value in ('https://marxists.org.evil.test/fr/a.htm',
                      'https://evil.test/marxists.org/fr/a.htm',
                      '[https://evil.test/a https://marxists.org/fr/a.htm]',
                      'https://marxists.org@evil.test/fr/a.htm',
                      'https://marxists.org:444/fr/a.htm',
                      '<!--https://marxists.org/fr/a.htm-->',
                      '{{url|https://marxists.org/fr/a.htm}}',
                      'https://marxists.org/a.htm https://evil.test/b'):
            with self.subTest(value=value):
                self.assertEqual(policy(value)['route'], 'ai')
    def test_inactive_sources(self):
        self.assertEqual(policy('https://fr.wikisource.org/wiki/Texte')['candidate'], 'wikisource')
        self.assertEqual(policy('https://eur-lex.europa.eu/legal-content/FR/TXT/')['route'], 'ai')
        self.assertEqual(policy('')['reason'], 'empty_link')
        self.assertEqual(policy('https://marxists.org/a.htm?x=1')['reason'], 'unsupported_mia_url')
    def test_duplicate_and_wrong_field(self):
        for raw in ('{{Citation|lien=https://marxists.org/a.htm|lien=https://evil.test}}',
                    '{{Citation|ouvrage=https://marxists.org/a.htm}}',
                    '{{Citation|Lien=https://marxists.org/a.htm}}'):
            self.assertEqual(link_policy(mwparserfromhell.parse(raw).filter_templates()[0])['route'], 'ai')


class FakeWiki:
    requests = 0
    network_body_bytes = 0
    def api(self, endpoint, **kw):
        assert endpoint == WD_API
        self.requests += 1
        if kw.get('redirects'):
            return {'query': {'pages': [{'pageid':1,'ns':10,'title':'Modèle:Citation'}]}}
        if kw.get('prop') == 'redirects':
            alias = kw['titles'] == 'Modèle:Citation'
            return {'query': {'pages':[{'pageid':1,'ns':10,'title':kw['titles'],
                        'redirects':[{'ns':10,'title':'Modèle:Cite'}] if alias else []}]}}
        if kw.get('list') == 'embeddedin':
            if kw['eititle'] == 'Modèle:Cite':
                return {'query':{'embeddedin':[{'pageid':1,'ns':0},{'pageid':3,'ns':0}]}}
            if 'eicontinue' not in kw:
                return {'query':{'embeddedin':[{'pageid':1,'ns':0}]},
                        'continue':{'continue':'-||','eicontinue':'next'}}
            return {'query':{'embeddedin':[{'pageid':2,'ns':0}]}}
        assert kw.get('prop') == 'revisions'
        raw = {1:'{{Citation|citation=test|lien=https://marxists.org/a.htm}}{{Citation|citation=abc|ouvrage=https://marxists.org/a.htm}}',
               2:'{{Cite|citation=test|lien=https://fr.wikisource.org/wiki/A}}',
               3:'{{Wrapper|url=https://marxists.org/a.htm}}'}
        return {'query':{'pages':[{'pageid':i,'ns':0,
            'revisions':[{'revid':100+i,'slots':{'main':{'content':raw[i]}}}]}
            for i in map(int,kw['pageids'].split('|'))]}}


class CensusTests(unittest.TestCase):
    def test_complete_deduplicated_alias_aware_census(self):
        result = count(FakeWiki(), batch_size=2)
        self.assertEqual(result['status'], 'complete_for_enumerated_raw_scope')
        self.assertEqual(result['pages_enumerated'], 3)
        self.assertEqual(result['pages_inspected'], 3)
        self.assertEqual(result['pages_without_literal_template'], 1)
        self.assertEqual(result['citation_occurrences'], 3)
        self.assertEqual(result['routes'], {'mia':1,'ai':2})
        self.assertEqual(result['domain_occurrences'], {'marxists.org':1,'fr.wikisource.org':1})
        self.assertNotIn('wiki_title', str(result))
        self.assertNotIn('quote_sha256', str(result))
    def test_missing_page_is_not_complete(self):
        class Missing(FakeWiki):
            def api(self, endpoint, **kw):
                if kw.get('prop') == 'revisions':
                    return {'query':{'pages':[]}}
                return super().api(endpoint, **kw)
        self.assertEqual(count(Missing())['status'], 'incomplete')
    def test_repeated_cursor(self):
        class Repeated:
            def api(self, endpoint, **kw):
                return {'query':{},'continue':{'eicontinue':'same'}}
        with self.assertRaises(ValueError):
            list(iterate(Repeated(), {}, {'eicontinue'}))
    def test_template_namespace(self):
        self.assertEqual(name('Modèle: Citation'), 'Citation')
        self.assertIsNone(name(':Citation'))
        self.assertIsNone(name('User:Citation'))


if __name__ == '__main__':
    unittest.main()
