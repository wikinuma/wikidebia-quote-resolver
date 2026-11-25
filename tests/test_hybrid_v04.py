import unittest
from unittest.mock import Mock
from pilot_full import (WR, API, wr_hybrid_work, wr_section_pairs, Document,
                        source_diagnostics, replay_results)
from resolver import subpages


class HybridTests(unittest.TestCase):
    def fixture(self, hidden=False, truncated=False, reciprocal=True, extra=False):
        fr=WR+'fr/Oeuvre'; en=WR+'en/Work'; leaf=en+'/05/Competition'
        frleaf=fr+'/04/La_concurrence'
        other=en+'/06/Other'
        def page(text,links=(),rev=2):
            return ('<div class="mw-parser-output"><p>'+text+'</p>'+''.join('<a href="'+u+'">x</a>' for u in links)+'</div><li id="t-permalink"><a href="?oldid='+str(rev)+'">permalink</a></li>').encode()
        bodies={fr:page('Table.',[frleaf])+f'<a class="interlanguage-link-target" href="{en}">EN</a>'.encode(),
                en:page('Table.',[leaf]+([other] if extra else []))+ (f'<a class="interlanguage-link-target" href="{fr}">FR</a>'.encode() if reciprocal else b''),
                frleaf:page('La concurrence est une lutte.'),leaf:page('Competition is a struggle.'),other:page('Other.')}
        c=Mock(); c.get.side_effect=lambda u:(bodies[u],u)
        def api(ep,**kw):
            lang='fr' if ep==API['fr'] else 'en'
            if kw['action']=='parse':
                return {'parse':{'title':kw['page'],'text':'<p>Hidden prose.</p>','revid':9}}
            assert kw['list']=='allpages'
            titles=['Oeuvre/04/La concurrence'] if lang=='fr' else ['Work/05/Competition']+(['Work/99/Hidden'] if hidden else [])
            r={'query':{'allpages':[{'title':t} for t in titles]}}
            if truncated and lang=='en': r['continue']={'apcontinue':'Work/99','continue':'-||'}
            return r
        c.api.side_effect=api
        return c

    def test_inventory_reuses_all_public_pages(self):
        c=self.fixture(); docs,info,links=wr_hybrid_work(c,{'wr_fr':'Oeuvre'},40,{'native_langlink':{'fr_title':'Oeuvre','en_titles':[]}})
        self.assertTrue(info['scope_complete'],info); self.assertEqual(c.api.call_count,2)
        self.assertEqual([len(docs[l]) for l in ('fr','en')],[2,2]); self.assertTrue(info['routes'][0]['reciprocal'])
        pairs=wr_section_pairs('engels-condition',docs,links)
        self.assertEqual([(f.title,e.title) for f,e,_ in pairs],[('Oeuvre/04/La concurrence','Work/05/Competition')])

    def test_unlinked_inventory_page_fetched(self):
        c=self.fixture(hidden=True); docs,info,_=wr_hybrid_work(c,{'wr_fr':'Oeuvre'},40,{})
        self.assertTrue(info['scope_complete'],info); self.assertEqual(c.api.call_count,3)
        self.assertIn('Work/99/Hidden',[d.title for d in docs['en']]); self.assertEqual(docs['en'][-1].revision,9)

    def test_missing_reciprocal_link_refuses(self):
        c=self.fixture(reciprocal=False); _,info,_=wr_hybrid_work(c,{'wr_fr':'Oeuvre'},40,{})
        self.assertFalse(info['scope_complete']); c.api.assert_not_called()
        self.assertIn('missing_or_ambiguous_reciprocal_root_link',[i['kind'] for i in info['issues']])

    def test_native_conflict_refuses(self):
        c=self.fixture(); _,info,_=wr_hybrid_work(c,{'wr_fr':'Oeuvre'},40,{'native_langlink':{'fr_title':'Oeuvre','en_titles':['Other']}})
        self.assertFalse(info['scope_complete']); c.api.assert_not_called()
        self.assertIn('native_and_html_root_conflict',[i['kind'] for i in info['issues']])

    def test_truncated_inventory_refuses(self):
        c=self.fixture(truncated=True); _,info,_=wr_hybrid_work(c,{'wr_fr':'Oeuvre'},2,{})
        self.assertFalse(info['scope_complete']); self.assertIn('page_limit',[i['kind'] for i in info['issues']])

    def test_html_extra_outside_inventory_refuses(self):
        c=self.fixture(extra=True); _,info,_=wr_hybrid_work(c,{'wr_fr':'Oeuvre'},40,{})
        self.assertFalse(info['scope_complete']); self.assertIn('html_titles_outside_api_inventory',[i['kind'] for i in info['issues']])

    def test_root_to_toc_is_not_section_pair(self):
        f=Document(WR+'fr/Oeuvre',['Text'],'f','Oeuvre'); e=Document(WR+'en/Work',['Index'],'e','Work'); s=Document(WR+'en/Work/01/Chapter',['Text'],'s','Work/01/Chapter')
        self.assertEqual(wr_section_pairs('manifesto',{'fr':[f],'en':[e,s]},{f.url:[e.url]}),[])

    def test_diagnostic_long_paragraph_not_full_match(self):
        d=Document('https://www.marxists.org/test',['word '*100+'la classe exploitee et opprimee ne peut plus se liberer '+ 'word '*100],'x')
        r=source_diagnostics('La classe exploitée et opprimée [...] ne peut se libérer','la classe exploitée et opprimée',[d])
        self.assertTrue(r['has_ellipsis']); self.assertEqual(r['selector_hit_count'],1)
        self.assertIn('la classe exploitee et opprimee',r['selector_hits'][0]['text']); self.assertFalse(r['selector_hits'][0]['boundary_verified'])
        self.assertLess(len(r['selector_hits'][0]['text'].split()),40)

    def test_repeated_api_continuation_refuses(self):
        c=Mock(); c.api.return_value={'query':{'allpages':[]},'continue':{'apcontinue':'Work/1','continue':'-||'}}
        with self.assertRaisesRegex(ValueError,'repeated_api_continuation'):
            subpages(c,'en','Work',40)

    def test_api_inventory_wrong_prefix_refuses(self):
        c=Mock(); c.api.return_value={'query':{'allpages':[{'title':'Other/1'}]}}
        with self.assertRaisesRegex(ValueError,'invalid_or_duplicate_prefix_title'):
            subpages(c,'en','Work',40)

    def test_replay_compares_candidates_not_timing(self):
        r={'cases':[{'id':'a','ranking_seconds':1,'methods':{'lexical':{'candidates':[1]}}}]}
        s={'cases':[{'id':'a','ranking_seconds':2,'methods':{'lexical':{'candidates':[1]}}}]}
        self.assertEqual(replay_results(r),replay_results(s)); s['cases'][0]['methods']['lexical']['candidates']=[2]
        self.assertNotEqual(replay_results(r),replay_results(s))

if __name__=='__main__': unittest.main()
