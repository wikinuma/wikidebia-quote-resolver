import unittest
from holdout_mia import select_holdout
from benchmark_index import pinned_record

class HoldoutTests(unittest.TestCase):
    def record(self,i,work,url,title=None):
        return {'wiki_title':title or 'Page '+str(i),'revision':i+1,'template_index':0,
                'template_sha256':'t'+str(i),'quote_sha256':'q'+str(i),'authors':'Author',
                'work':work,'article':'','date':'','mia_urls':[url],'quote_words':20}

    def test_frozen_quotes_and_templates_never_reused(self):
        u='https://www.marxists.org/francais/a.htm'
        a=self.record(1,'Old',u)
        b=self.record(2,'Different','https://www.marxists.org/francais/b.htm')
        duplicate_quote={**b,'quote_sha256':a['quote_sha256']}
        duplicate_template={**b,'template_sha256':a['template_sha256']}
        chosen,diag=select_holdout([a,b,duplicate_quote,duplicate_template],[pinned_record(a)],
                                   set(),set(),set(),(),50)
        self.assertEqual([x['id'] for x in chosen],[pinned_record(b)['id']])
        self.assertEqual(diag['by_novelty'],{'unseen_page_work_url':1})

    def test_novel_documents_prioritized_and_overlap_reported(self):
        u='https://www.marxists.org/francais/a.htm'
        a=self.record(1,'Old',u)
        b=self.record(2,'Old',u)
        c=self.record(3,'Fresh','https://www.marxists.org/francais/b.htm')
        rows=[a,b,c]
        chosen,diag=select_holdout(rows,[pinned_record(a)],set(),set(),set(),(),1)
        self.assertEqual(chosen[0]['id'],pinned_record(c)['id'])
        chosen,diag=select_holdout(rows,[pinned_record(a)],set(),set(),set(),(),50)
        self.assertEqual(len(chosen),2)
        self.assertEqual(diag['by_novelty'],{'unseen_page_work_url':1,'familiar_document_or_page':1})
        self.assertEqual(diag['known_url_overlap'],1)

    def test_catalog_document_is_not_an_unseen_work(self):
        u='https://www.marxists.org/francais/a.htm'
        a=self.record(1,'Alias',u)
        catalog=[{'source_url':u,'source_title':'Canonical','source_titles':['Alias']}]
        c,diag=select_holdout([a],[],set(),set(),set(),catalog,1)
        self.assertTrue(c[0]['holdout_overlap']['known_source_url'])
        self.assertTrue(c[0]['holdout_overlap']['known_work_label'])
        self.assertEqual(c[0]['holdout_group'],'familiar_document_or_page')
