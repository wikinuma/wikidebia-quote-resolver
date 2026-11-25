import importlib.util
import unittest
from unittest.mock import Mock,patch
from navigation import (canonical_mia,mia_urls,safe_title,extract_citation,text_metadata,
                        reverse_lookup,title_lookup,reciprocal_roots,discover_case,evaluate_route,generic_work_name,rendered_authors,author_check,
                        API,WR)

HAS_PARSER=bool(importlib.util.find_spec('mwparserfromhell'))


class NavigationTests(unittest.TestCase):
    def test_canonical_mia(self):
        self.assertEqual(canonical_mia('http://marxists.org/archive/a.htm#part'),'https://www.marxists.org/archive/a.htm')
        for url in ('https://marxists.org.evil.test/a','https://name@marxists.org/a','https://marxists.org/a?x=1','https://marxists.org/../a','https://marxists.org/a|b'):
            self.assertIsNone(canonical_mia(url))

    def test_url_extraction(self):
        self.assertEqual(mia_urls('[http://marxists.org/a.htm A] [https://www.marxists.org/a.htm#b B] https://evil.test/a'),['https://www.marxists.org/a.htm'])

    def test_query_title_validation(self):
        self.assertTrue(safe_title('La lutte des classes'))
        self.assertFalse(safe_title('X]]|?Other'))
        self.assertFalse(safe_title('X::Y'))

    @unittest.skipUnless(HAS_PARSER,'navigation parser optional')
    def test_raw_nested_citation(self):
        raw="{{Argument|citations={{Citation|citation=Voici une phrase ''assez longue'' et utile.|auteurs=[[Karl Marx]]|ouvrage=[[Un livre]]|lien=[http://marxists.org/a.htm page]}}}}"
        r=extract_citation(raw,'une phrase assez longue')
        self.assertEqual(r['authors'],'Karl Marx'); self.assertEqual(r['work'],'Un livre')
        self.assertEqual(r['mia_urls'],['https://www.marxists.org/a.htm'])
        self.assertNotIn('Voici une phrase',str(r))

    @unittest.skipUnless(HAS_PARSER,'navigation parser optional')
    def test_raw_ambiguous_quote_refuses(self):
        raw='{{Citation|citation=une phrase assez longue ici}}{{Citation|citation=une phrase assez longue ailleurs}}'
        with self.assertRaisesRegex(ValueError,'raw_quote_selector_matches=2'): extract_citation(raw,'phrase assez longue')

    @unittest.skipUnless(HAS_PARSER,'navigation parser optional')
    def test_raw_text_fields(self):
        r=text_metadata('{{Text|Author(s)=Karl Marx|Date of writing=1848|MIA URL=http://marxists.org/abc.htm|Interlanguages=fr:Un livre}}')
        self.assertEqual(r['authors'],'Karl Marx'); self.assertEqual(r['mia_urls'],['https://www.marxists.org/abc.htm'])
        self.assertTrue(r['interlanguages_parameter_present'])

    def test_reverse_lookup_requires_returned_exact_property(self):
        c=Mock(); c.api.return_value={'query':{'results':{'A':{'namespace':0,'fulltext':'A','printouts':{'MIA URL':['http://marxists.org/a.htm']}},'B':{'namespace':0,'fulltext':'B','printouts':{'MIA URL':['https://marxists.org/b.htm']}}}}}
        r=reverse_lookup(c,'https://www.marxists.org/a.htm')
        self.assertEqual([x['title'] for x in r['matches']],['A']); self.assertIn('[[MIA URL::',c.api.call_args.kwargs['query'])

    def test_no_arbitrary_title_search_hit(self):
        c=Mock(); c.api.side_effect=[{'query':{'pages':[{'ns':0,'title':'Livre','missing':True}]}},{'query':{'search':[{'ns':0,'title':'Autre livre'}]}}]
        self.assertEqual(title_lookup(c,'Livre')['titles'],[])

    def test_reciprocal_interlanguage(self):
        fr=WR+'fr/Livre'; en=WR+'en/Book'; c=Mock()
        c.get.side_effect=[(f'<a class="interlanguage-link-target" href="{en}">EN</a>'.encode(),fr),(f'<a class="interlanguage-link-target" href="{fr}">FR</a>'.encode(),en)]
        self.assertTrue(reciprocal_roots(c,'Livre/01')['reciprocal'])
        c.get.side_effect=[(f'<a class="interlanguage-link-target" href="{en}">EN</a>'.encode(),fr),(b'<p>Missing reciprocal</p>',en)]
        with self.assertRaisesRegex(ValueError,'nonreciprocal'): reciprocal_roots(c,'Livre')

    @unittest.skipUnless(HAS_PARSER,'navigation parser optional')
    def test_navigation_uses_citation_not_expected_url(self):
        c=Mock(); c.get.return_value=(b'<p>Public work</p>','https://www.marxists.org/en.htm')
        raw='{{Citation|citation=une phrase assez longue ici|auteurs=Karl Marx|ouvrage=Livre|lien=https://marxists.org/fr.htm}}'
        case={'id':'fixture','wiki_title':'Argument','selector':'phrase assez longue','work_fr':'WRONG GOLD','expected_en_mia':'https://www.marxists.org/WRONG.htm'}
        with patch('navigation.raw_parse',return_value=('Argument',5,raw)), \
             patch('navigation.reverse_lookup',return_value={'matches':[{'title':'Livre'}],'truncated':False}), \
             patch('navigation.reciprocal_roots',return_value={'fr_title':'Livre','en_title':'Book','reciprocal':True}), \
             patch('navigation.wr_meta',side_effect=[{'status':'raw_text_metadata','authors':'Karl Marx','mia_urls':['https://www.marxists.org/fr.htm']},{'status':'raw_text_metadata','authors':'Karl Marx','mia_urls':['https://www.marxists.org/en.htm']}]):
            row=discover_case(c,case)
        self.assertEqual(row['status'],'mia_route_candidate'); self.assertFalse(row['auto_publish'])
        self.assertFalse(evaluate_route(row,case)['target_exact_url_match'])
        c.get.assert_called_once_with('https://www.marxists.org/en.htm')

    def test_generic_archive_and_rendered_authors(self):
        self.assertTrue(generic_work_name('Marxists.org'))
        self.assertFalse(generic_work_name('Le Capital'))
        html='<table><tr><th>Auteur·e(s)</th><td><a>Friedrich Engels</a></td></tr></table>'
        self.assertEqual(rendered_authors(html),'Friedrich Engels')

    @unittest.skipUnless(HAS_PARSER,'navigation parser optional')
    def test_help_page_cannot_be_a_work(self):
        c=Mock()
        raw='{{Citation|citation=une phrase assez longue ici|ouvrage=Marxists.org|article=Une lettre}}'
        with patch('navigation.raw_parse',return_value=('Argument',5,raw)), \
             patch('navigation.title_lookup',return_value={'titles':['Une lettre'],'method':'exact_api_title_or_redirect'}) as lookup, \
             patch('navigation.reciprocal_roots',return_value={'fr_title':'Une lettre','en_title':'A letter','reciprocal':True}), \
             patch('navigation.wr_meta',side_effect=[{'status':'missing_or_ambiguous_text_template','authors':'','mia_urls':[]},{'status':'raw_text_metadata','authors':'','mia_urls':[]}]):
            row=discover_case(c,{'id':'x','wiki_title':'Argument','selector':'phrase assez longue'})
        lookup.assert_called_once_with(c,'Une lettre')
        self.assertEqual(row['status'],'work_metadata_unconfirmed')
        c.get.assert_not_called()

    @unittest.skipUnless(HAS_PARSER,'navigation parser optional')
    def test_internal_quote_sink_not_exposed(self):
        c=Mock(); quotes={}
        raw='{{Citation|citation=une phrase assez longue ici|ouvrage=Marxists.org}}'
        with patch('navigation.raw_parse',return_value=('Argument',5,raw)):
            row=discover_case(c,{'id':'x','wiki_title':'Argument','selector':'phrase assez longue'},quote_sink=quotes)
        self.assertEqual(quotes['x'],'une phrase assez longue ici')
        self.assertNotIn('text',row['input'])
        self.assertEqual(row['status'],'work_not_resolved')

    def test_documented_full_name_alias(self):
        self.assertEqual(author_check('Léon Trotsky','Léon Trotski'),'same_documented_author_alias')
        self.assertEqual(author_check('Léon Trotski','Leon Trotsky'),'same_documented_author_alias')
        self.assertEqual(author_check('Léon Trotsky','Ivan Trotski'),'author_tokens_differ')
        self.assertEqual(author_check('Léon Trotsky','Karl Marx'),'author_tokens_differ')
        self.assertEqual(author_check('Léon Trotsky','Léon Trotski, Karl Marx'),'partial_author_overlap')
        self.assertEqual(author_check('','Léon Trotski'),'metadata_missing')
        self.assertEqual(author_check('Lénine','Lenin'),'same_documented_author_alias')
        self.assertEqual(author_check('Lénine','Karl Marx'),'author_tokens_differ')
