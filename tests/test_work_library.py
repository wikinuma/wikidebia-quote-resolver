import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from navigation import discover_case
from work_library import WorkLibrary, LIVE_TTL, identity
from resolver import Document, sha
from early_toc import try_early_toc

FR='https://www.marxists.org/francais/test/index.htm'
EN='https://www.marxists.org/archive/test/index.htm'
FLEAF='https://www.marxists.org/francais/test/chapter.htm'
ELEAF='https://www.marxists.org/archive/test/chapter.htm'
WF='https://wikirouge.net/texts/fr/Un_livre'
WE='https://wikirouge.net/texts/en/A_book'
INDEX='a'*64
AUTHOR='Karl Marx'


def meta(url=FR):
    return {'authors':AUTHOR,'work':'Un livre','article':'','date':'1850','mia_urls':[url]}


def fixture(url=FR,indexed=False):
    row={'status':'mia_route_candidate','input':{**meta(url),'quote_sha256':'do-not-store','template_sha256':'never-store','quote_words':10},
         'route_method':'semantic_exact_mia_url','selected_fr_title':'Un livre',
         'route':{'fr_title':'Un livre','en_title':'A book','fr_url':WF,'en_url':WE,
                  'reciprocal':True,'scope':'work_identity_candidate_not_section_or_quote_identity'},
         'work_metadata':{'fr':{'status':'raw_text_metadata','authors':AUTHOR,'mia_urls':[FR],'revision':1,'date':'1850'},
                          'en':{'status':'raw_text_metadata','authors':AUTHOR,'mia_urls':[EN],'revision':2,'date':'1850',
                                'fields':{'publisher':['Example edition'],'translator':['A translator'],'text':['not a metadata field']}}}}
    if indexed:
        row['route_method']='local_wikirouge_snapshot'
        row['route'].update(scope='direct_native_link' if url==FR else 'parent_work_only_no_chapter_equivalence',
                            native_link_directions=['fr','en'],snapshot_sha256=INDEX)
        for m in row['work_metadata'].values(): m['status']='snapshot_metadata'
        row['attempts']=[{'method':'local_wikirouge_snapshot','status':'candidate','snapshot_sha256':INDEX,
                          'candidates':[{'source_page':{'title':'Un livre' if url==FR else 'Un livre/Chapitre',
                                                       'mia_urls':[url],'revision':3},'reasons':[]}]}]
    return row


def chapter_pair():
    pair={'fr':{'url':FLEAF,'position':18,'heading':'Un chapitre'},
          'en':{'url':ELEAF,'position':20,'heading':'A chapter'}}
    tocs=[{'requested_url':u,'url':u,'sha256':sha(u.encode())} for u in (FR,EN)]
    probe={'status':'sampled_content_support','fr_document_sha256':'b'*64,'en_document_sha256':'c'*64}
    return pair,tocs,probe


class WorkLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'works.sqlite3'

    def test_reopen_and_reuse_for_distinct_citation(self):
        with WorkLibrary(self.db) as lib:
            self.assertTrue(lib.learn(fixture()))
        with WorkLibrary(self.db) as lib:
            m={**meta(),'quote_sha256':'another-quote','template_sha256':'different-template','quote_words':75}
            saved=lib.lookup(m)
            self.assertEqual(saved['route']['en_url'],WE)
            self.assertEqual(saved['work_metadata']['en']['fields']['translator'],['A translator'])
            self.assertEqual(lib.stats['work_hits'],1)
            dump=json.dumps(lib.export())
            for secret in ('do-not-store','never-store','another-quote','different-template','not a metadata field'):
                self.assertNotIn(secret,dump)
            self.assertNotIn('quote_sha256',dump)
            self.assertEqual(len(lib.export()['works']),1)

    def test_conflicting_authors_titles_urls_and_years_are_misses(self):
        with WorkLibrary(self.db) as lib:
            self.assertTrue(lib.learn(fixture()))
            for m in ({**meta(),'authors':'Friedrich Engels'}, {**meta(),'work':'Another work'},
                      meta(FLEAF), {**meta(),'date':'1851'}, {**meta(),'mia_urls':[FR,FLEAF]}):
                self.assertIsNone(lib.lookup(m))
            bad=fixture();bad['work_metadata']['en']['authors']='Friedrich Engels'
            self.assertFalse(lib.learn(bad))
            bad=fixture();bad['route']['reciprocal']=False
            self.assertFalse(lib.learn(bad))

    def test_conflicting_target_retained_without_preference(self):
        with WorkLibrary(self.db) as lib:
            self.assertTrue(lib.learn(fixture()))
            other=fixture()
            other['route'].update(en_url='https://wikirouge.net/texts/en/Other_book',en_title='Other book')
            other['work_metadata']['en']['mia_urls']=['https://www.marxists.org/archive/other.htm']
            self.assertTrue(lib.learn(other))
            self.assertEqual(len(lib.export()['works']),2)
            self.assertIsNone(lib.lookup(meta()))

    def test_snapshot_invalidation_and_parent_scope(self):
        with WorkLibrary(self.db) as lib:
            self.assertTrue(lib.learn(fixture(FLEAF,True),INDEX))
            saved=lib.lookup(meta(FLEAF),INDEX)
            self.assertEqual(saved['route']['scope'],'parent_work_only_no_chapter_equivalence')
            self.assertIsNone(lib.lookup(meta(FLEAF),'d'*64))
            self.assertIsNone(lib.lookup(meta(FLEAF)))
            bad=fixture(FLEAF,True)
            bad['attempts'][0]['candidates'][0]['source_page']['mia_urls']=[]
            self.assertFalse(lib.learn(bad,INDEX))

    def test_stale_and_unverified_route_never_suppresses_discovery(self):
        with WorkLibrary(self.db,clock=lambda:1000) as lib:
            self.assertTrue(lib.learn(fixture()))
            bad=fixture();bad['work_metadata']['fr']['mia_urls']=[]
            self.assertFalse(lib.learn(bad))
        with WorkLibrary(self.db,clock=lambda:1001+LIVE_TTL) as lib:
            self.assertIsNone(lib.lookup(meta()))

    def test_navigation_reads_the_new_citation_before_saved_work(self):
        with WorkLibrary(self.db) as lib:
            self.assertTrue(lib.learn(fixture()))
            client=Mock();client.get.return_value=(b'<p>English document</p>',EN)
            case={'id':'new','wiki_title':'Argument','citation_ref':{'revision':20}}
            quotes={}
            with patch('navigation.raw_parse',return_value=('Argument',20,'raw')) as read, \
                 patch('navigation.extract_citation_ref',return_value={**meta(),'text':'different quotation'}) as extract, \
                 patch('navigation.reverse_lookup') as reverse:
                row=discover_case(client,case,quotes,work_library=lib)
            self.assertEqual(row['status'],'mia_route_candidate')
            self.assertTrue(row['work_library_hit'])
            self.assertEqual(quotes,{'new':'different quotation'})
            read.assert_called_once();extract.assert_called_once();reverse.assert_not_called()
            client.get.assert_called_once_with(EN)

    def test_chapters_need_same_documents_roots_and_model(self):
        pair,tocs,probe=chapter_pair()
        with WorkLibrary(self.db) as lib:
            self.assertTrue(lib.remember_chapter(FR,EN,pair,tocs,probe,INDEX))
            self.assertFalse(lib.remember_chapter(FR,EN,pair,tocs,{**probe,'status':'sampled_content_inconclusive'},INDEX))
        with WorkLibrary(self.db) as lib:
            saved=lib.chapter(FR,EN,FLEAF,INDEX,'b'*64)
            self.assertEqual(saved['pair']['en']['url'],ELEAF)
            self.assertIsNone(lib.chapter(FR,EN,FLEAF,'d'*64,'b'*64))
            self.assertIsNone(lib.chapter(FR,EN,FLEAF,INDEX,'d'*64))
            self.assertIsNone(lib.chapter(FR,'https://www.marxists.org/archive/other.htm',FLEAF,INDEX,'b'*64))

    def test_guarded_chapter_reuse_avoids_reencoding_headings_and_probe(self):
        pair,tocs,probe=chapter_pair()
        matcher=Mock();matcher.encoder.model_id=INDEX
        matcher_id=sha(('guarded_chapter_v1|'+type(matcher).__name__+'|'+INDEX).encode())
        fr=Document(FLEAF,['French passage.'],'b'*64)
        en=Document(ELEAF,['English passage.'],'c'*64)
        matcher.toc.side_effect=lambda root,lang:([],tocs[0 if lang=='fr' else 1])
        matcher.document.return_value=en
        row={'navigation':{'input':{'mia_urls':[FLEAF]},'route_method':'local_wikirouge_snapshot',
                           'author_check':'same_normalized_author_tokens',
                           'work_metadata':{'fr':{'authors':AUTHOR,'mia_urls':[FR]},
                                            'en':{'authors':AUTHOR,'mia_urls':[EN]}}}}
        with WorkLibrary(self.db) as lib:
            self.assertTrue(lib.remember_chapter(FR,EN,pair,tocs,probe,matcher_id))
            e=SimpleNamespace(client=SimpleNamespace(budget=32,requests=2),toc_matcher=matcher,max_pages=16,
                              source_documents={FLEAF:fr},direct_cache=Mock(),hybrid=True,work_library=lib)
            e.direct_cache.candidate.return_value={'candidates':[]}
            e.semantic_sidecar=lambda r,q,d:{**r,'semantic_experiment':{'candidates':[{'url':ELEAF}]},
                                              'hybrid_review_queue':{'candidates':[{'url':ELEAF}]}}
            with patch('early_toc.select_source_hit',return_value=(fr,{'evidence_kind':'exact_normalized_source'})), \
                 patch('early_toc.rank',return_value=[{'url':ELEAF}]):
                out,diag=try_early_toc(e,row,'new quote',{})
            self.assertIsNotNone(out)
            self.assertEqual(diag['matcher_variant'],'persisted_revalidated_chapter')
            matcher.match_for_source.assert_not_called();matcher.content_probe.assert_not_called()
            self.assertEqual(lib.stats['chapter_hits'],1)
            # Changed TOC must take the ordinary path, not use the old mapping.
            matcher.toc.side_effect=lambda root,lang:([],{**tocs[0 if lang=='fr' else 1],'sha256':'e'*64})
            matcher.match_for_source.return_value={'pairs':[],'fr_toc':{},'en_toc':{}}
            with patch('early_toc.select_source_hit',return_value=(fr,{'evidence_kind':'exact_normalized_source'})):
                out,diag=try_early_toc(e,row,'another quote',{})
            self.assertIsNone(out)
            matcher.match_for_source.assert_called_once()


if __name__=='__main__':
    unittest.main()
