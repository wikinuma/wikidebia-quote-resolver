import copy
import unittest
from unittest.mock import patch

from citation_inventory import refresh, inspect_page, validate_previous
from citation_domain_census import name


def raw(text='Un texte', url='https://marxists.org/fr/a.htm'):
    return '{{Citation|citation='+text+'|lien='+url+'|auteurs=Test}}'


class Wiki:
    def __init__(self):
        self.pages={1:('A',101,raw('X')+raw('Y')+raw('Z','https://example.org/a')),
                    2:('B',102,raw('X')),
                    3:('C',103,'{{Cite|citation=Test|lien=https://marxists.org/fr/a.htm}}')}
        self.requests=0
        self.network_body_bytes=0
        self.content_reads=0
        self.fail=False

    def api(self, endpoint, **kw):
        self.requests+=1
        if kw.get('redirects'):
            return {'query':{'pages':[{'ns':10,'title':'Modèle:Citation'}]}}
        if kw.get('prop')=='redirects':
            return {'query':{'pages':[{'ns':10,'title':kw['titles'],
                'redirects':[{'ns':10,'title':'Modèle:Cite'}] if kw['titles']=='Modèle:Citation' else []}]}}
        if kw.get('list')=='embeddedin':
            return {'query':{'embeddedin':[{'ns':0,'pageid':i} for i in self.pages]}}
        if self.fail:
            return {'query':{'pages':[]}}
        byrev='revids' in kw
        values=set(map(int,kw['revids' if byrev else 'pageids'].split('|')))
        out=[]
        for pid,(title,rev,body) in self.pages.items():
            if (rev if byrev else pid) not in values:
                continue
            r={'revid':rev}
            if 'content' in kw['rvprop']:
                r['slots']={'main':{'content':body}}
                self.content_reads+=1
            out.append({'pageid':pid,'ns':0,'title':title,'revisions':[r]})
        return {'query':{'pages':out}}


class InventoryTests(unittest.TestCase):
    def test_all_occurrences_not_just_unique_works_or_long_quotes(self):
        w=Wiki()
        s,m=refresh(w)
        self.assertEqual(s['mia_occurrences'],4)
        self.assertEqual(s['pinned_cases'],3)
        self.assertEqual(s['distinct_source_keys'],2)
        self.assertEqual(s['citation_issues'],{'unsupported_alias':1})
        self.assertEqual(s['routes'],{'mia':4,'ai':1})
        self.assertEqual(w.content_reads,3)
        validate_previous(m)
        self.assertNotIn('citation=X',str(m))

    def test_revision_unchanged_reuse_new_modified_deleted_and_moved(self):
        w=Wiki()
        _,m=refresh(w)
        w.content_reads=0
        s,n=refresh(w,m)
        self.assertEqual(w.content_reads,0)
        self.assertEqual(s['pages_reused'],3)
        self.assertEqual(m,n)
        w.pages[1]=('A',104,raw('Y')+raw('Changed'))
        w.pages[2]=('B moved',102,raw('X'))
        del w.pages[3]
        w.pages[4]=('New',105,raw('New'))
        s,n=refresh(w,m)
        self.assertEqual(w.content_reads,3)
        self.assertEqual(s['new_source_keys'],2)
        self.assertEqual(s['unchanged_source_keys'],2)
        self.assertEqual(s['removed_source_keys'],0)
        self.assertEqual(len(n['cases']),4)
        self.assertIn('B moved',[c['wiki_title'] for c in n['cases']])

    def test_incomplete_scan_never_replaces_manifest(self):
        w=Wiki()
        _,m=refresh(w)
        w.fail=True
        s,n=refresh(w,m)
        self.assertIsNone(n)
        self.assertEqual(s['status'],'incomplete')
        self.assertIsNone(s['removed_source_keys'])

    def test_invalid_manifest_and_algorithm_change(self):
        w=Wiki()
        _,m=refresh(w)
        bad=copy.deepcopy(m)
        bad['cases']=[]
        with self.assertRaises(ValueError):
            refresh(w,bad)
        w.content_reads=0
        with patch('citation_inventory.algorithm',return_value='changed'):
            s,_=refresh(w,m)
        self.assertEqual(s['pages_reused'],0)
        self.assertEqual(w.content_reads,3)

    def test_mia_policy_does_not_use_other_parameters(self):
        text=raw('A','https://example.org')+'{{Citation|citation=B|ouvrage=https://marxists.org/fr/a.htm}}'
        row=inspect_page(text,'A',1,101,{'Citation'})
        self.assertEqual(row['routes'],{'ai':2})
        self.assertFalse(row['cases'])


if __name__=='__main__':
    unittest.main()
