import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from wikirouge_index import open_snapshot
from navigation import discover_case


class SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context=open_snapshot()
        cls.index=cls.context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.context.__exit__(None,None,None)

    def test_native_reverse_link_and_author(self):
        m={'work':"De l'autorité",'authors':'Friedrich Engels','mia_urls':[]}
        result, route=self.index.route(m)
        self.assertEqual(result['status'],'candidate')
        self.assertEqual(route['route']['en_title'],'On Authority')
        self.assertEqual(route['route']['native_link_directions'],['en'])
        self.assertFalse(route['route']['reciprocal'])
        self.assertFalse(result['auto_publish'])

    def test_title_is_not_identity(self):
        m={'work':"De l'autorité",'authors':'Wrong Author','mia_urls':[]}
        result, route=self.index.route(m)
        self.assertIsNone(route)
        self.assertIn('author_unconfirmed_or_conflicting',result['candidates'][0]['reasons'])

    def test_ambiguous_link_retained(self):
        m={'work':'Lettre à Friedrich Engels, 27 avril 1853','authors':'Karl Marx','mia_urls':[]}
        result, route=self.index.route(m)
        self.assertEqual(result['status'],'ambiguous')
        self.assertEqual(len(result['candidates']),2)
        self.assertIsNone(route)

    def test_url_normalization(self):
        m={'work':"De l'autorité",'authors':'Friedrich Engels',
           'mia_urls':['http://marxists.org/francais/marx/works/00/parti/kmpc075.htm']}
        result,route=self.index.route(m)
        self.assertTrue(result['url_page_ids'])
        self.assertEqual(result['status'],'candidate')

    def test_url_title_conflict(self):
        m={'work':'La sainte famille','authors':'Friedrich Engels',
           'mia_urls':['http://marxists.org/francais/marx/works/00/parti/kmpc075.htm']}
        result,route=self.index.route(m)
        self.assertIsNone(route)
        self.assertIn('source_url_title_conflict',result['candidates'][0]['reasons'])

    def test_local_route_bypasses_bibliographic_network(self):
        m={'work':"De l'autorité",'authors':'Friedrich Engels','mia_urls':[],
           'article':'','text':'example'}
        client=Mock()
        client.get.side_effect=RuntimeError('unavailable')
        with patch('navigation.raw_parse',return_value=('Test',1,'raw')), \
             patch('navigation.extract_citation',return_value=m), \
             patch('navigation.reverse_lookup',side_effect=AssertionError('network bibliography')), \
             patch('navigation.title_lookup',side_effect=AssertionError('network bibliography')), \
             patch('navigation.reciprocal_roots',side_effect=AssertionError('network bibliography')):
            result=discover_case(client,{'id':'x','wiki_title':'Test','selector':'example'},quote_sink={},local_index=self.index)
        self.assertEqual(result['status'],'wikirouge_route_candidate')
        self.assertEqual(result['route_method'],'local_wikirouge_snapshot')

    def test_snapshot_connection_is_read_only(self):
        import sqlite3
        with self.assertRaises(sqlite3.OperationalError):
            self.index.db.execute('DELETE FROM pages')
