import json
import unittest
from unittest.mock import Mock,patch
from wikirouge_incremental import (
    affected,apply_delta,base_checkpoints,exact_title,field_map,metadata_row,
    open_updated,rebuild_pairs,recent,fetch_batch,SCHEMA
)
from wikirouge_index import open_snapshot

START='2026-11-25T18:11:44Z'
END='2026-11-26T10:00:00Z'


class IncrementalTests(unittest.TestCase):
    def test_empty_rebuild_reproduces_original_pairs_and_links(self):
        with open_snapshot() as base,open_updated() as updated:
            old=[tuple(r) for r in base.db.execute('SELECT * FROM pairs ORDER BY fr_id,en_id')]
            rebuild_pairs(updated.db)
            self.assertEqual(old,[tuple(r) for r in updated.db.execute('SELECT * FROM pairs ORDER BY fr_id,en_id')])
            self.assertEqual([tuple(r) for r in base.db.execute('SELECT * FROM native_links ORDER BY lang,id,target_lang,target_title')],
                             [tuple(r) for r in updated.db.execute('SELECT * FROM native_links ORDER BY lang,id,target_lang,target_title')])
            self.assertEqual(updated.checkpoints,{'fr':START,'en':START})

    def test_delete_preserves_other_edges_and_ambiguity(self):
        with open_updated() as i:
            pid=i.db.execute('SELECT fr_id FROM pairs ORDER BY fr_id LIMIT 1').fetchone()[0]
            d={'schema':SCHEMA,'status':'complete','base_snapshot_sha256':i.base_fingerprint,
               'previous_sha256':i.fingerprint,'from':i.checkpoints,'until':{'fr':END,'en':END},
               'updates':[{'lang':'fr','id':pid,'kind':'tombstone','reason':'page_missing'}],'auto_publish':False}
            apply_delta(i.db,d,i.base_fingerprint,i.fingerprint,i.checkpoints)
            self.assertIsNone(i.db.execute('SELECT id FROM pages WHERE lang=? AND id=?',('fr',pid)).fetchone())
            self.assertEqual(i.db.execute('SELECT count(*) FROM pairs WHERE fr_id=?',(pid,)).fetchone()[0],0)
            self.assertEqual(i.db.execute('SELECT count(*) FROM native_links WHERE target_lang=? AND target_id=?',('fr',pid)).fetchone()[0],0)
            self.assertFalse(i.db.execute('PRAGMA foreign_key_check').fetchall())

    def test_invalid_delta_rolls_back(self):
        with open_updated() as i:
            count=i.db.execute('SELECT count(*) FROM pages').fetchone()[0]
            d={'schema':SCHEMA,'status':'complete','base_snapshot_sha256':i.base_fingerprint,
               'previous_sha256':i.fingerprint,'from':i.checkpoints,'until':{'fr':END,'en':END},
               'updates':[{'lang':'fr','id':9,'kind':'tombstone'},
                          {'lang':'fr','id':9,'kind':'tombstone'}],'auto_publish':False}
            with self.assertRaises(ValueError):
                apply_delta(i.db,d,i.base_fingerprint,i.fingerprint,i.checkpoints)
            self.assertEqual(i.db.execute('SELECT count(*) FROM pages').fetchone()[0],count)

    def test_changed_metadata_and_native_link(self):
        with open_updated() as i:
            p={'pageid':999999,'ns':0,'title':'Example','lastrevid':9999,
               'revisions':[{'revid':9999,'timestamp':END,'slots':{'main':{'content':'{{Text|Author=Karl Marx|MIA URL=https://www.marxists.org/archive/marx/test.htm|Date of writing=1848}}\nFull text must not be saved.'}}}],
               'categories':[{'title':'Category:Text'}],
               'langlinks':[{'lang':'fr','title':"De l'autorité"}]}
            row=metadata_row('en',p,END,field_map(i.db))
            self.assertEqual(row['kind'],'upsert')
            self.assertIn('Karl Marx',[f[-1] for f in row['fields'] if f[4]=='author'])
            self.assertNotIn('Full text must not be saved.',str(row))
            self.assertEqual(row['native_links'][0][3],"De l'autorité")
            self.assertTrue(any(r[4]=='marxists.org/archive/marx/test.htm' for r in row['source_urls']))
            d={'schema':SCHEMA,'status':'complete','base_snapshot_sha256':i.base_fingerprint,
               'previous_sha256':i.fingerprint,'from':i.checkpoints,'until':{'fr':END,'en':END},
               'updates':[row],'auto_publish':False}
            apply_delta(i.db,d,i.base_fingerprint,i.fingerprint,i.checkpoints)
            self.assertEqual(i.db.execute('SELECT count(*) FROM pairs WHERE en_id=999999').fetchone()[0],1)
            self.assertEqual(i.db.execute('SELECT fr_degree FROM pairs WHERE en_id=999999').fetchone()[0],2)

    def test_retention_gap_and_partial_feed(self):
        with patch('wikirouge_incremental.read_api',return_value={
                'curtimestamp':END,'query':{'recentchanges':[{'timestamp':END}]}}):
            with self.assertRaisesRegex(ValueError,'coverage'):
                recent(Mock(),'en',START)
        event={'rcid':1,'ns':0,'title':'Example','timestamp':END,'type':'edit','pageid':3}
        with patch('wikirouge_incremental.read_api',side_effect=[
                {'curtimestamp':END,'query':{'recentchanges':[{'timestamp':START}]}},
                {'query':{'recentchanges':[event]},'continue':{'rccontinue':'x'}} ,
                {'query':{'recentchanges':[]},'continue':{'rccontinue':'x'}}]):
            with self.assertRaisesRegex(ValueError,'continuation'):
                recent(Mock(),'en',START)

    def test_template_event_is_blocking(self):
        event={'rcid':1,'ns':10,'title':'Template:Text','timestamp':END,'type':'edit','pageid':5}
        with patch('wikirouge_incremental.read_api',side_effect=[
                {'curtimestamp':END,'query':{'recentchanges':[{'timestamp':START}]}},
                {'query':{'recentchanges':[event]}}]):
            with self.assertRaisesRegex(ValueError,'impact_review'):
                recent(Mock(),'en',START)

    def test_move_target_and_deleted_old_id(self):
        with open_updated() as i:
            pid,title=i.db.execute("SELECT id,title FROM pages WHERE lang='fr' LIMIT 1").fetchone()
            e={'ns':0,'title':title,'type':'log','target_ns':0,'target_title':'New title'}
            ids,titles=affected(i.db,'fr',[e])
            self.assertIn(pid,ids)
            self.assertIn('New title',titles)

    def test_missing_id_and_incomplete_batch(self):
        with patch('wikirouge_incremental.read_api',return_value={'query':{'pages':[{'pageid':7,'missing':True}]}}):
            self.assertEqual(list(fetch_batch(Mock(),'fr','pageids',[7])),[{'pageid':7,'missing':True}])
            with self.assertRaisesRegex(ValueError,'not_accounted'):
                fetch_batch(Mock(),'fr','pageids',[7,8])
