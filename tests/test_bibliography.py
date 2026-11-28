import unittest
from unittest.mock import Mock,patch
from pathlib import Path
from bibliography import load_catalog,match_catalog,catalog_route
from auto_retrieve import Retrieval,document_alignment
from alignment import exact_occurrences
from resolver import Document,sha
from inventory import link_pages,choose_sample

ROOT=Path(__file__).resolve().parents[1]

class BibliographyTests(unittest.TestCase):
    def setUp(self):
        self.entries=load_catalog(ROOT/'bibliographic-catalog.json')
        self.e=self.entries[0]
        self.meta={'mia_urls':[self.e['source_url']],'article':self.e['source_title'],'authors':'Léon Trotski'}

    def test_exact_bibliographic_match_and_alias(self):
        self.assertEqual(match_catalog(self.meta,self.entries),self.e)
        self.assertFalse(catalog_route(self.meta,self.entries)['auto_publish'])
        self.assertTrue(any(e.get('source_document') for e in self.entries))

    def test_wrong_source_title_or_author_never_matched(self):
        for meta in ({**self.meta,'authors':'Ivan Trotski'}, {**self.meta,'article':'Wrong work'},
                     {**self.meta,'mia_urls':['https://www.marxists.org/francais/other.htm']},
                     {**self.meta,'mia_urls':[]}, {**self.meta,'authors':''}):
            self.assertIsNone(match_catalog(meta,self.entries))

    def test_bibliography_only_no_fetch(self):
        e=self.entries[2]; r=Retrieval(Mock(),8,self.entries)
        row={'navigation':{'input':{'mia_urls':[e['source_url']],'article':e['source_title'],'authors':e['authors']}},'candidates':[]}
        got=r.retrieve_catalog({'id':'x'},row,'citation non reproduite dans les rapports')
        self.assertEqual(got['status'],'bibliographic_lead_only')
        r.client.get.assert_not_called()

    def test_catalog_source_confirmation_required(self):
        r=Retrieval(Mock(),8,self.entries)
        row={'navigation':{'input':self.meta},'candidates':[]}
        with patch.object(r,'source',return_value={'status':'source_unconfirmed_in_bounded_scope'}):
            got=r.retrieve_catalog({'id':'x'},row,'citation non reproduite dans les rapports')
        self.assertEqual(got['status'],'source_needs_review')
        r.client.get.assert_not_called()

    def test_catalog_retrieval_does_not_override_ambiguity(self):
        r=Retrieval(Mock(),8,self.entries)
        with patch('auto_retrieve.discover_case',return_value={'status':'ambiguous_title_search','input':self.meta}),patch.object(r,'retrieve_catalog') as method:
            row=r.retrieve({'id':'x','wiki_title':'Test','selector':'une citation longue'})
        method.assert_not_called(); self.assertEqual(row['status'],'no_work_route')

    def test_direct_document_alignment_without_headings(self):
        q='Les travailleurs sont unis dans cette lutte.'
        f=Document('fr',[q,' '.join(['Une phrase de contexte.']*15)],'f')
        e=Document('en',['The workers are united in this struggle.',' '.join(['A sentence of context.']*15)],'e')
        hits,_=exact_occurrences(q,f); source={'status':'exact_in_examined_documents','occurrences':hits}
        r=document_alignment(q,source,[f],[e],'test_direct_metadata')
        self.assertTrue(r['candidates']); self.assertFalse(r['candidates'][0]['boundary_verified'])
        self.assertFalse(r['auto_publish'])
        self.assertFalse(document_alignment(q,{**source,'status':'variant_review_required'},[f],[e],'test')['candidates'])
        self.assertFalse(document_alignment(q,source,[f],[e,e],'test')['candidates'])

    def test_bounded_continuation_and_deduplication(self):
        c=Mock()
        def api(endpoint,**kwargs):
            if kwargs.get('euprotocol')=='http': return {'query':{'exturlusage':[]}}
            row={'ns':0,'title':'One','url':'https://www.marxists.org/francais/a.htm'}
            if 'eucontinue' not in kwargs: return {'query':{'exturlusage':[row]},'continue':{'eucontinue':'next','continue':'-||'}}
            return {'query':{'exturlusage':[row,{**row,'title':'Two'}]}}
        c.api.side_effect=api
        pages,queries=link_pages(c,max_batches=2)
        self.assertEqual(set(pages),{'One','Two'}); self.assertEqual(len(queries),6)
        self.assertEqual(len(pages['One']),1)

    def test_author_balancing(self):
        rows=[{'wiki_title':str(i),'template_sha256':str(i),'quote_sha256':str(i),'work':str(i),'authors':a}
              for i,a in enumerate(['A','A','A','B','C'])]
        selected=choose_sample(rows,3,True)
        self.assertEqual({r['authors'] for r in selected},{'A','B','C'})


    def test_unpublished_capital_chapter_uses_specific_catalog_target(self):
        fr='https://www.marxists.org/francais/marx/works/1867/Capital-inedit/kmcapI-6-2H.htm'
        en='https://www.marxists.org/archive/marx/works/1864/economic/ch02b.htm'
        meta={'mia_urls':[fr],'work':'Le Capital',
              'article':'Résultats du procès de production immédiat',
              'authors':'Karl Marx'}
        e=match_catalog(meta,self.entries)
        self.assertEqual(e['target_url'],en)
        self.assertEqual(e['source_document'],fr)
        self.assertFalse(e['whole_document_parallel'])
        self.assertIsNone(match_catalog({**meta,'authors':'Friedrich Engels'},self.entries))
        self.assertIsNone(match_catalog({**meta,'mia_urls':[fr+'.other']},self.entries))
        q='La classe prolétarienne veut la révolution sociale.'
        f=Document(fr,[q],'f')
        hits,_=exact_occurrences(q,f)
        source={'status':'exact_in_examined_documents','occurrences':hits}
        row={'navigation':{'input':meta},'candidates':[],
             'auto_publish':False,'rights':'unreviewed'}
        r=Retrieval(Mock(),8,self.entries,refined=True)
        target=Document(en,['The proletarian class wants the social revolution.'],'e')
        def locate(*args,**kwargs):
            r.source_documents={(fr,'f'):f}
            return source
        with patch.object(r,'source',side_effect=locate) as locate_mock, \
             patch('auto_retrieve.mia_doc',return_value=(target,b'<p>synthetic</p>')) as read:
            got=r.retrieve_catalog({'id':'x'},row,q)
        self.assertEqual(locate_mock.call_args.kwargs['preferred_source'],fr)
        read.assert_called_once_with(r.client,en)
        self.assertTrue(got['candidates'])
        self.assertTrue(all(c['url']==en for c in got['candidates']))
        self.assertFalse(got['auto_publish'])
