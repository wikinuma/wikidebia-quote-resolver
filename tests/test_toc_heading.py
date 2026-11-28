import importlib.util
import unittest
from toc_heading import heading,entries,align_headings

class TocTests(unittest.TestCase):
    def test_labels_ignore_number_and_generic(self):
        self.assertEqual(heading('47. L’insurrection d’Octobre'),'L’insurrection d’Octobre')
        self.assertEqual(heading('Chapter 46 The October Insurrection'),'The October Insurrection')
        self.assertIsNone(heading('II.'))
        self.assertIsNone(heading('23. Conclusion'))

    def test_only_explicit_same_directory_links(self):
        root='https://www.marxists.org/archive/test/index.htm'
        body=b'<h1>Table of contents</h1><a href="ch1.htm">1. First chapter</a><a href="ch2.htm">2. Second chapter</a><a href="ch3.htm">3. Third chapter</a><a href="https://example.org/x.htm">Other text</a><a href="ch1.htm">Next</a>'
        out=entries(body,root,'en')
        self.assertEqual([r['heading'] for r in out],['First chapter','Second chapter','Third chapter'])
        self.assertEqual(out[0]['url'],root.replace('index','ch1'))

    @unittest.skipUnless(importlib.util.find_spec('numpy'),'optional numpy')
    def test_monotone_alignment_handles_insertions_and_number_shift(self):
        class Encoder:
            def encode(self,texts):
                lookup={'A':[1,0,0,0],'B':[0,1,0,0],'C':[0,0,1,0],'D':[0,0,0,1]}
                return [lookup[t] for t in texts]
        def rows(labels):return [{'url':str(i),'heading':t,'position':i} for i,t in enumerate(labels)]
        r=align_headings(rows('ABC'),rows('ADBC'),Encoder())
        self.assertEqual([(p['fr']['position'],p['en']['position']) for p in r['pairs']],[(0,0),(1,2),(2,3)])
        self.assertTrue(all(not p['auto_publish'] for p in r['pairs']))
        self.assertFalse(align_headings(rows('ABC'),rows('CBA'),Encoder())['pairs'])
        self.assertFalse(align_headings(rows('ABC'),rows('AABC'),Encoder())['pairs'])
