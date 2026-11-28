import unittest
from work_coverage import group_cases, classify, summarize

FR='https://www.marxists.org/francais/test.htm'
FR2='https://www.marxists.org/francais/other.htm'
EN='https://www.marxists.org/archive/test.htm'


def case(i,url=FR,title='Un livre',author='Karl Marx',q=None):
    return {'id':str(i),'stratum':'explicit_mia',
            'metadata':{'authors':author,'work':title,'article':'','date':'1850','mia_urls':[url]},
            'citation_ref':{'quote_sha256':q or str(i)}}


class NoIndex:
    def route(self,m):
        return {'status':'not_found','candidates':[]},None


class CoverageTests(unittest.TestCase):
    def test_group_by_bibliographic_identity_not_quote(self):
        cases=[case(1),case(2),case(3,q='2'),case(4,FR2),case(5,author='Friedrich Engels')]
        groups=group_cases(cases)
        rows=classify(groups,NoIndex(),(),(),())
        s=summarize(rows,cases)
        self.assertEqual(s['citation_occurrences'],5)
        self.assertEqual(s['distinct_quote_fingerprints'],4)
        self.assertEqual(s['exact_bibliographic_source_groups'],3)
        self.assertEqual(s['groups_with_multiple_distinct_quotes'],1)
        self.assertEqual(rows[0]['citation_occurrences'],3)
        self.assertNotIn('citation_ref',str(rows))
        self.assertNotIn('quote_sha256',str(rows))

    def test_manual_and_chapter_are_leads_not_quote_matches(self):
        cases=[case(1),case(2,FR2)]
        cat=[{'source_url':FR,'source_title':'Un livre','authors':'Karl Marx',
              'target_url':EN,'target_provider':'mia'}]
        ch=[{'fr_url':FR2,'fr_toc':'https://www.marxists.org/francais/index.htm',
             'en_url':EN,'authors':'Karl Marx'}]
        rows=classify(group_cases(cases),NoIndex(),cat,ch,())
        self.assertEqual({r['status'] for r in rows},{'researched_document_route','chapter_leads_only'})
        self.assertTrue(all(r['rights']=='unreviewed' and not r['auto_publish'] for r in rows))

    def test_reject_non_mia_or_duplicate_case(self):
        with self.assertRaises(ValueError):group_cases([case(1),case(1)])
        with self.assertRaises(ValueError):group_cases([case(2,'https://example.com/a')])
