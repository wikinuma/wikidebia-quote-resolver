import unittest,importlib.util
from unittest.mock import Mock
from toc_structure import entries_v2,bracketed_pairs
from benchmark_toc_heading import propose

class StructureTests(unittest.TestCase):
    def test_ignore_internal_sections_and_preserve_real_file_links(self):
        root='https://www.marxists.org/archive/test/index.htm'
        body=b'<a href="ch1.htm">1. First chapter</a><a href="ch1.htm#one">Internal section one</a><a href="ch1.htm#two">Internal section two</a><a href="ch2.htm">2. Second chapter</a><a href="https://example.org/test.htm">External file</a><a href="../other.htm">Other work</a>'
        r=entries_v2(body,root,'en')
        self.assertEqual([p['heading'] for p in r],['First chapter','Second chapter'])
        self.assertEqual([p['ordinary_position'] for p in r],[0,1])

    def test_short_legacy_root_with_table_marker(self):
        root='https://www.marxists.org/francais/test/er00t.htm'
        body=b'<table><tr><td>Sommaire</td></tr></table><a href="er1.htm">I. Premier chapitre</a><a href="er2.htm">II. Second chapitre</a>'
        self.assertEqual(len(entries_v2(body,root,'fr')),2)
        self.assertEqual(entries_v2(body.replace(b'Sommaire',b'Texte'),root,'fr'),[])

    def test_different_unanchored_labels_are_still_ambiguous(self):
        root='https://www.marxists.org/archive/test/index.htm'
        body=b'<a href="ch1.htm">First chapter</a><a href="ch1.htm">Another chapter</a><a href="ch2.htm">Second chapter</a>'
        r=entries_v2(body,root,'en')
        self.assertEqual(len(r),1)
        self.assertEqual(r[0]['position'],1)

    @unittest.skipUnless(importlib.util.find_spec('numpy'),'optional numpy')
    def test_brackets_only_equal_contiguous_short_gaps(self):
        def rows(prefix,n):
            return [{'url':prefix+str(i),'position':i,'heading':'Chapter '+str(i)} for i in range(n)]
        f,e=rows('f',6),rows('e',7)
        encoder=Mock();encoder.encode.side_effect=lambda t:[[1.,0.] for _ in t]
        left={'fr':f[1],'en':e[2]};right={'fr':f[4],'en':e[5]}
        r=bracketed_pairs(f,e,[left,right],encoder)
        self.assertEqual([(p['fr']['position'],p['en']['position']) for p in r],[(2,3),(3,4)])
        self.assertTrue(all(p['requires_content_probe'] and not p['auto_publish'] for p in r))
        self.assertEqual(bracketed_pairs(f,e,[left,{'fr':f[4],'en':e[6]}],encoder),[])
        self.assertEqual(bracketed_pairs([x for x in f if x['position']!=2],e,[left,right],encoder),[])
        self.assertEqual(bracketed_pairs(f,e,[left],encoder),[])

    def test_bracketed_pair_is_not_reported_without_corroboration(self):
        m=Mock();pair={'fr':{'url':'f'},'en':{'url':'e','position':20,'ordinary_position':None},
                       'requires_content_probe':True}
        m.match.return_value={'pairs':[pair],'fr_count':10,'en_count':10,'anchors':4,'fr_toc':{},'en_toc':{}}
        m.content_probe.return_value={'status':'sampled_content_inconclusive'}
        r=propose(m,('froot','eroot'),'f')
        self.assertEqual(r['status'],'uncorroborated_bracketed_hypothesis')
        self.assertNotIn('pair',r)
        m.content_probe.return_value={'status':'sampled_content_support'}
        r=propose(m,('froot','eroot'),'f')
        self.assertEqual(r['status'],'candidate')
        self.assertTrue(r['outside_first_15_toc_links'])
