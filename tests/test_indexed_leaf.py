import sqlite3
import unittest
from unittest.mock import Mock,patch
from indexed_leaf import indexed_leaf_route
from wikirouge_index import LocalIndex,title_key
from resolver import Document,sha
from alignment import exact_occurrences
from auto_retrieve import Retrieval

FR='https://www.marxists.org/francais/test/chapter.htm'
EN='https://www.marxists.org/archive/test/chapter.htm'
WRFR='https://wikirouge.net/texts/fr/Work/Chapitre'
WREN='https://wikirouge.net/texts/en/Work/Chapter'
META={'authors':'Karl Marx','work':'Work','article':'','mia_urls':[FR]}


def fixture():
    db=sqlite3.connect(':memory:')
    db.row_factory=sqlite3.Row
    db.executescript('''
    CREATE TABLE pages(lang,id,title,title_key,search_key,revision,url,work_root,status);
    CREATE TABLE fields(lang,id,template_no,ordinal,field,value);
    CREATE TABLE source_urls(lang,id,mia_key);
    CREATE TABLE pairs(fr_id,en_id,evidence,fr_degree,en_degree,metadata_ambiguous);
    ''')
    for lang,pid,title,url,link,rev in [('fr',1,'Work/Chapitre',WRFR,FR,101),('en',2,'Work/Chapter',WREN,EN,102)]:
        db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?,?,?)',
                   (lang,pid,title,title,title_key(title),rev,url,'Work','metadata'))
        db.executemany('INSERT INTO fields VALUES(?,?,?,?,?,?)',
                       [(lang,pid,0,0,'author','Karl Marx'),(lang,pid,0,0,'mia_url',link)])
        db.execute('INSERT INTO source_urls VALUES(?,?,?)',(lang,pid,link.removeprefix('https://www.')))
    db.execute('INSERT INTO pairs VALUES(1,2,?,1,1,0)',('en',))
    return LocalIndex(db,'snapshot')


def source(url=FR,revision=None):
    return {'status':'exact_in_examined_documents','occurrences':[{'url':url,'revision':revision}]}


class LeafRoutingTests(unittest.TestCase):
    def setUp(self):
        self.index=fixture()

    def tearDown(self):
        self.index.db.close()

    def test_unique_native_leaf(self):
        r=indexed_leaf_route(self.index,META,source())
        self.assertEqual(r['status'],'candidate')
        self.assertEqual(r['route']['en_mia_url'],EN)
        self.assertEqual(r['route']['directions'],['en'])
        self.assertFalse(r['route']['auto_publish'])

    def test_exact_wikirouge_revision(self):
        self.assertEqual(indexed_leaf_route(self.index,META,source(WRFR,101))['status'],'candidate')
        self.assertIsNone(indexed_leaf_route(self.index,META,source(WRFR,999))['route'])

    def test_no_title_only_or_parent_fallback(self):
        self.assertIsNone(indexed_leaf_route(self.index,META,source(FR.replace('chapter','different')))['route'])
        self.index.db.execute('UPDATE pages SET work_root=title')
        self.assertIsNone(indexed_leaf_route(self.index,META,source())['route'])

    def test_descendant_and_ambiguous_pair_rejected(self):
        self.index.db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?,?,?)',
                              ('en',3,'Work/Chapter/Sub','Work/Chapter/Sub','work/chapter/sub',103,'u','Work','metadata'))
        self.assertIsNone(indexed_leaf_route(self.index,META,source())['route'])
        self.index.db.execute('DELETE FROM pages WHERE id=3')
        self.index.db.execute('UPDATE pairs SET en_degree=2')
        self.assertIsNone(indexed_leaf_route(self.index,META,source())['route'])

    def test_author_conflict_and_non_mia_rejected(self):
        self.assertIsNone(indexed_leaf_route(self.index,{**META,'authors':'Different Author'},source())['route'])
        self.assertIsNone(indexed_leaf_route(self.index,{**META,'mia_urls':[]},source())['route'])

    def test_ambiguous_source_rejected(self):
        s=source();s['occurrences']*=2
        self.assertIsNone(indexed_leaf_route(self.index,META,s)['route'])

    def test_real_retrieval_only_after_valid_alignment_and_fallback(self):
        q='Une longue citation exacte de cette section.'
        fr=Document(FR,[q,' '.join(['La richesse du pays augmente.']*12)],'f')
        en=Document(EN,['A long exact quotation from this section.',' '.join(['The wealth of the country increases.']*12)],'e')
        hits,_=exact_occurrences(q,fr)
        s={'status':'exact_in_examined_documents','occurrences':hits}
        r=Retrieval(Mock(),16,local_index=self.index,refined=True,indexed_leaf=True)
        r.source_documents={(FR,'f'):fr}
        row={'navigation':{'input':META}}
        with patch('auto_retrieve.mia_doc',return_value=(en,b'<p>chapter</p>')) as load:
            self.assertTrue(r.try_indexed_leaf(q,row,s))
            self.assertEqual(row['target_scope'],'indexed_single_leaf')
            self.assertFalse(row['section_alignment']['candidates'][0]['boundary_verified'])
            self.assertTrue(r.try_indexed_leaf(q,row,s))
            self.assertEqual(load.call_count,1)
        r.leaf_documents.clear()
        with patch('auto_retrieve.mia_doc',side_effect=ValueError('unavailable')):
            r.client.get.side_effect=ValueError('unavailable')
            self.assertFalse(r.try_indexed_leaf(q,{'navigation':{'input':META}},s))
