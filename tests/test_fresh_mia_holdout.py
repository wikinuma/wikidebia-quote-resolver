import unittest
from unittest.mock import Mock
from fresh_mia_holdout import select, identity

class EmptyIndex:
    def ids_for_urls(self,urls): return set()
    def ids_for_title(self,lang,title): return set()

def case(n,work,url=None,page='Test',quote=None,template=None):
    return {'id':'id-'+str(n),'wiki_title':page,'stratum':'explicit_mia',
            'citation_ref':{'quote_sha256':quote or format(n,'064x'),'template_sha256':template or format(n+100,'064x')},
            'metadata':{'work':work,'article':'','authors':'Karl Marx','date':'',
                        'mia_urls':[url or 'https://www.marxists.org/francais/test/'+str(n)+'.htm'],'quote_words':30}}

class FreshHoldoutTests(unittest.TestCase):
    def test_excludes_both_cohorts_and_prefers_novel_works(self):
        idx=EmptyIndex()
        old=[case(1,'Old one'),case(2,'Old two')]
        candidates=[case(3,'New work',quote=old[0]['citation_ref']['quote_sha256']),
                    case(4,'New work',template=old[1]['citation_ref']['template_sha256']),
                    case(5,'Old two'),case(6,'New A'),case(7,'New B'),case(8,'New A')]
        selected,d=select(candidates,{'cases':old},idx,(),(),(set(),set(),set()),2)
        self.assertEqual({c['metadata']['work'] for c in selected},{'New A','New B'})
        self.assertEqual(d['excluded_occurrences']['previous_case_or_fingerprint'],2)
        self.assertEqual(d['selected_by_novelty'],{'no_known_work_signal':2})
        self.assertEqual(d['selected_overlap']['wiki_page'],2)

    def test_same_url_different_label_is_known(self):
        idx=EmptyIndex()
        old=case(1,'Old')
        new=case(2,'Different title',url=old['metadata']['mia_urls'][0])
        selected,_=select([new],{'cases':[old]},idx,(),(),(set(),set(),set()),1)
        self.assertEqual(selected[0]['holdout_group'],'known_work_signal')
        self.assertTrue(selected[0]['holdout_overlap']['source_url'])

    def test_same_root_is_known(self):
        idx=Mock()
        idx.ids_for_urls.side_effect=lambda urls:{1} if any('/1.htm' in x for x in urls) else {2}
        idx.ids_for_title.return_value=set()
        idx.page.side_effect=lambda lang,pid:{'work_root':'One root','title':'Chapter '+str(pid)}
        selected,_=select([case(2,'Other label')],{'cases':[case(1,'Old')]},idx,(),(),(set(),set(),set()),1)
        self.assertTrue(selected[0]['holdout_overlap']['wikirouge_root'])

    def test_exact_quote_dedup_and_order_independence(self):
        idx=EmptyIndex();a=case(1,'A');b=case(2,'B',quote=a['citation_ref']['quote_sha256'])
        c=case(3,'C')
        args=({'cases':[]},idx,(),(),(set(),set(),set()),2)
        x,d=select([a,b,c],*args);y,e=select([c,b,a],*args)
        self.assertEqual(x,y);self.assertEqual(d,e);self.assertEqual(d['eligible_unique_quotes'],2)

    def test_non_mia_rejected(self):
        c=case(1,'A',url='https://example.org/quote')
        with self.assertRaises(ValueError):
            identity(c['metadata'],EmptyIndex())
