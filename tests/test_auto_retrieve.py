import unittest
from unittest.mock import Mock,patch
from auto_retrieve import mia_toc_links,collect_mia,Retrieval
from resolver import Document,sha


class AutoRetrievalTests(unittest.TestCase):
    def test_directory_index_only_direct_links(self):
        root='https://www.marxists.org/archive/work/'
        body=b'<a href="ch01.htm">Chapter</a><a href="index.htm">Index</a><a href="../other.htm">Other work</a><a href="sub/a.htm">Subdirectory</a><a href="https://evil.test/x.htm">Bad</a>'
        self.assertEqual(mia_toc_links(body,root),[{'url':root+'ch01.htm','route':'directory_index_link'}])

    def test_old_french_work_stem_and_explicit_preface(self):
        root='https://www.marxists.org/francais/a/work001.htm'
        body='<a href="work001a.htm">Chapitre</a><a href="else.htm">Autre texte</a><a href="/francais/a/1883/p.htm">Préface de 1883</a>'.encode()
        self.assertEqual([x['url'] for x in mia_toc_links(body,root)],
                         ['https://www.marxists.org/francais/a/work001a.htm','https://www.marxists.org/francais/a/1883/p.htm'])

    def test_bounded_pages(self):
        c=Mock(); root='https://www.marxists.org/archive/work/'
        c.get.return_value=(b'<a href="ch01.htm">One</a><a href="ch02.htm">Two</a>',root)
        docs,info=collect_mia(c,root,1)
        self.assertEqual(c.get.call_count,1); self.assertIn({'kind':'page_limit'},info['issues'])
        self.assertEqual(len(docs),1)

    def test_expected_answers_not_passed_to_discovery(self):
        c=Mock(); r=Retrieval(c,8)
        case={'id':'x','wiki_title':'Argument','selector':'une phrase utile','expected_en_mia':'https://www.marxists.org/gold.htm','work_fr':'Gold'}
        with patch('auto_retrieve.discover_case',return_value={'status':'work_not_resolved'}) as d:
            row=r.retrieve(case)
        self.assertEqual(d.call_args.args[1],{'id':'x','wiki_title':'Argument','selector':'une phrase utile'})
        self.assertFalse(row['candidates']); self.assertFalse(row['auto_publish'])

    def test_no_target_fetch_if_source_unconfirmed(self):
        r=Retrieval(Mock(),8)
        def nav(client,case,quote_sink=None):
            quote_sink['x']='une phrase utile ici'
            return {'status':'mia_route_candidate','author_check':'metadata_missing','mia_target':{'url_accessible':True,'final_url':'https://www.marxists.org/en.htm'}}
        with patch('auto_retrieve.discover_case',side_effect=nav),patch.object(r,'source',return_value={'status':'source_unconfirmed_in_bounded_scope'}) as s,patch.object(r,'work') as w:
            row=r.retrieve({'id':'x','wiki_title':'Argument','selector':'une phrase utile'})
        self.assertEqual(row['status'],'source_needs_review')
        w.assert_not_called()
