import unittest
from unittest.mock import patch, Mock
from tempfile import TemporaryDirectory
from resolver import *


def doc(blocks, url='https://www.marxists.org/test'):
    return Document(url,blocks,sha('\n'.join(blocks).encode()))


class ResolverTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(norm('L’œuvre : ÉCOLE, co\u00adopération'), 'l oeuvre ecole cooperation')

    def test_html(self):
        html='<div class="mw-parser-output"><table><tr><td>metadata</td></tr></table><p>Une <em>phrase</em><sup class="reference">[1]</sup>.</p><p>Une seconde.</p></div>'
        self.assertEqual(norm(' '.join(blocks_from(html))),'une phrase une seconde')

    def test_cross_paragraph(self):
        r=locate('le texte commence ici puis continue ailleurs',[doc(['le texte commence ici','puis continue ailleurs','autre paragraphe'])]); self.assertEqual(r[0]['span'],2); self.assertEqual(r[0]['match'],'normalized_exact')

    def test_typography(self):
        self.assertEqual(locate('L’œuvre d’un auteur est publiée.',[doc(["L'oeuvre d´un auteur est publiée."])])[0]['score'],1)

    def test_no_french_match(self):
        r=resolve('le libre développement de chacun',[doc(['texte sans rapport'])],[doc(['the free development of each'])]); self.assertEqual(r['status'],'source_fr_unconfirmed'); self.assertFalse(r['auto_publish']); self.assertTrue(r['en_candidates'])

    def test_no_english_candidate(self):
        r=resolve('le libre développement de chacun',[doc(['le libre développement de chacun'])],[doc(['clouds drift across the sky'])]); self.assertEqual(r['status'],'no_candidate')

    def test_ambiguous_french(self):
        r=locate('le libre développement de chacun',[doc(['le libre développement de chacun','long unrelated intervening text','le libre développement de chacun'])]); self.assertEqual(len(r),2)

    def test_rank_verbatim_and_unapproved(self):
        d=doc(['The weather was cold.','the free development of each','The price of property.']); r=rank('le libre développement de chacun',[d]); self.assertEqual(r[0]['text'],d.blocks[1]); self.assertFalse(r[0]['boundary_verified']); self.assertEqual(r[0]['rights'],'unreviewed')

    def test_hint(self):
        d=doc(['An unrelated concept.','the worst of all possible allies']); r=rank('de tous les alliés possibles',[d],hint='worst of the possible allies'); self.assertEqual(r[0]['text'],d.blocks[1])

    def test_omission(self):
        r=locate('Il explique ceci [...] puis continue autrement',[doc(['Il explique ceci avec beaucoup de détails puis continue autrement'])]); self.assertTrue(r); self.assertEqual(r[0]['match'],'fuzzy')

    def test_wrong_host(self):
        for u in ['http://www.marxists.org/','https://www.marxists.org.evil.test/','https://user@wikirouge.net/','https://wikirouge.net:444/']:
            self.assertFalse(valid_url(u))

    def test_langlinks(self):
        c=Mock(); c.api.return_value={'query':{'pages':[{'title':'Canonical','langlinks':[{'lang':'en','title':'English'}]}]}}; self.assertEqual(work_title(c,'Alias'),('Canonical',['English'])); self.assertEqual(c.api.call_args.kwargs['redirects'],1)

    def test_missing_title_no_arbitrary_selection(self):
        c=Mock(); c.api.side_effect=[{'query':{'pages':[{'title':'Missing','missing':True}]}},{'query':{'search':[{'title':'Similar'}]}},{'query':{'search':[{'title':'Other'}]}}]; fr,en,routes,issues=wikirouge(c,'Missing','une citation assez longue'); self.assertFalse(fr or en or routes); self.assertEqual(issues[0]['candidates'],['Similar','Other'])

    def test_subpages(self):
        c=Mock(); c.api.side_effect=[{'query':{'allpages':[{'title':'Work/1'}]},'continue':{'apcontinue':'Work/2','continue':'-||'}},{'query':{'allpages':[{'title':'Work/2'}]}}]; self.assertEqual(subpages(c,'en','Work',3),(['Work','Work/1','Work/2'],False)); self.assertEqual(c.api.call_args.kwargs['apcontinue'],'Work/2')
        c.api.side_effect=[{'query':{'allpages':[{'title':'Work/1'}]},'continue':{'apcontinue':'Work/2','continue':'-||'}}]; self.assertEqual(subpages(c,'en','Work',2),(['Work','Work/1'],True))

    def test_wiki_quote(self):
        c=Mock(); c.api.return_value={'parse':{'text':'<blockquote>Une citation assez longue.</blockquote><blockquote>Une citation assez longue.</blockquote>','revid':3}}
        with self.assertRaises(RuntimeError): wiki_quote(c,'Page','citation assez longue')
        c.api.return_value={'parse':{'text':'<blockquote>Une citation assez longue.</blockquote>','revid':3}}; self.assertEqual(wiki_quote(c,'Page','citation assez longue'),('Une citation assez longue.',3))

    def test_cache(self):
        with TemporaryDirectory() as temp:
            c=Client(temp,delay=0); url='https://www.marxists.org/test'; body=b'<p>text</p>'; response=Mock(status_code=200); response.iter_content.return_value=[body]; response.__enter__=Mock(return_value=response); response.__exit__=Mock(return_value=False)
            session=Mock(); session.get.return_value=response; session.__enter__=Mock(return_value=session); session.__exit__=Mock(return_value=False)
            with patch('resolver.requests.Session',return_value=session):
                self.assertEqual(c.get(url)[0],body); self.assertEqual(c.get(url)[0],body)
            self.assertEqual(c.requests,1); self.assertEqual(c.hits,1)

    def test_budget(self):
        with TemporaryDirectory() as temp:
            c=Client(temp,budget=0); self.assertRaises(RuntimeError,c.get,'https://www.marxists.org/test')

    def test_api_error(self):
        c=Client.__new__(Client); c.get=Mock(return_value=(b'{"error":{"code":"bad"}}','https://wikirouge.net/')); self.assertRaises(RuntimeError,c.api,API['fr'],action='query')

if __name__=='__main__': unittest.main()
