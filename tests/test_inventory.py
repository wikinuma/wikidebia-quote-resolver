import importlib.util
import unittest
from unittest.mock import Mock,patch
from navigation import citation_records,extract_citation_ref,discover_case
from inventory import choose_sample,discover_inventory
from auto_retrieve import automatic_selector,Retrieval
from resolver import norm

HAS_PARSER=bool(importlib.util.find_spec('mwparserfromhell'))

class InventoryTests(unittest.TestCase):
    def test_selector_from_input_only(self):
        quote='Une phrase répétée deux fois. Une phrase répétée mais finalement différente.'
        s=automatic_selector(quote)
        self.assertGreaterEqual(len(s.split()),4)
        self.assertEqual((' '+norm(quote)+' ').count(' '+s+' '),1)

    @unittest.skipUnless(HAS_PARSER,'navigation parser optional')
    def test_template_reference_disambiguates_selector(self):
        raw='{{Citation|citation=une phrase assez longue ici|ouvrage=Livre A}}{{Citation|citation=une phrase assez longue ailleurs|ouvrage=Livre B}}'
        rows,issues=citation_records(raw)
        self.assertEqual(len(rows),2); self.assertFalse(issues)
        self.assertEqual(rows[1]['template_index'],1); self.assertNotIn('text',rows[1])
        self.assertEqual(extract_citation_ref(raw,rows[1],True)['text'],'une phrase assez longue ailleurs')
        with self.assertRaisesRegex(ValueError,'fingerprint_mismatch'):
            extract_citation_ref(raw,{**rows[1],'template_index':0})
        with self.assertRaisesRegex(ValueError,'index_not_found'):
            extract_citation_ref(raw,{**rows[1],'template_index':3})

    @unittest.skipUnless(HAS_PARSER,'navigation parser optional')
    def test_exact_revision_and_title_required(self):
        raw='{{Citation|citation=une phrase assez longue ici|ouvrage=Marxists.org}}'
        rows,_=citation_records(raw); ref={**rows[0],'revision':5}
        c=Mock(); quotes={}
        with patch('navigation.raw_parse',return_value=('Argument',5,raw)) as p:
            row=discover_case(c,{'id':'x','wiki_title':'Argument','citation_ref':ref},quote_sink=quotes)
        self.assertEqual(row['status'],'work_not_resolved')
        self.assertEqual(quotes['x'],'une phrase assez longue ici')
        p.assert_called_once_with(c,'https://fr.wikidebates.org/w/api.php',oldid=5)
        with patch('navigation.raw_parse',return_value=('Wrong title',5,raw)):
            row=discover_case(c,{'id':'x','wiki_title':'Argument','citation_ref':ref})
        self.assertEqual(row['error'],'citation_page_mismatch')

    def test_retrieval_passes_reference_not_gold(self):
        r=Retrieval(Mock(),8)
        ref={'revision':5,'template_index':1,'template_sha256':'f','quote_sha256':'q'}
        with patch('auto_retrieve.discover_case',return_value={'status':'work_not_resolved'}) as d:
            row=r.retrieve({'id':'x','wiki_title':'Argument','citation_ref':ref,'expected_en_mia':'gold'})
        self.assertEqual(d.call_args.args[1],{'id':'x','wiki_title':'Argument','citation_ref':ref})
        self.assertFalse(row['candidates'])

    def test_selection_is_stable_and_distinct_by_work(self):
        rows=[{'wiki_title':str(i),'template_sha256':str(i),'quote_sha256':str(i),'work':w}
              for i,w in enumerate(['Livre A','Livre A','Livre B','Livre C'])]
        a=choose_sample(rows,3); b=choose_sample(list(reversed(rows)),3)
        self.assertEqual(a,b)
        self.assertEqual(len({r['work'] for r in a}),3)

    @unittest.skipUnless(HAS_PARSER,'navigation parser optional')
    def test_inventory_excludes_development_titles_and_works(self):
        raw='{{Citation|citation=une phrase suffisamment longue et exacte pour le test|ouvrage=Livre A|lien=https://marxists.org/a.htm}}{{Citation|citation=une autre phrase suffisamment longue pour notre test|ouvrage=Livre B|lien=https://marxists.org/b.htm}}'
        c=Mock()
        with patch('inventory.link_pages',return_value=({'Dev':{'https://marxists.org/a.htm'},'New':{'https://marxists.org/b.htm'}},[])), \
             patch('inventory.raw_parse',return_value=('New',5,raw)) as p:
            r=discover_inventory(c,{'dev'},{'livre a'},10,3)
        self.assertEqual(len(r['selected']),1)
        self.assertEqual(r['selected'][0]['metadata']['work'],'Livre B')
        self.assertEqual(r['selected'][0]['citation_ref']['revision'],5)
        self.assertNotIn('selector',r['selected'][0])
        self.assertNotIn('une autre phrase',str(r))
        p.assert_called_once_with(c,'https://fr.wikidebates.org/w/api.php',title='New')
