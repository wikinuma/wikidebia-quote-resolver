import unittest
from unittest.mock import Mock,patch
from toc_structure import AugmentedTocMatcher
from toc_heading import TocMatcher

class AugmentationTests(unittest.TestCase):
    def setUp(self):
        self.client=Mock();self.encoder=Mock()
        self.matcher=AugmentedTocMatcher(self.client,self.encoder)
        self.assertIs(self.matcher.structure.client,self.client)
        self.assertIs(self.matcher.structure.encoder,self.encoder)

    def test_existing_match_and_ambiguity_never_replaced(self):
        for n in (1,2):
            old={'pairs':[{'fr':{'url':'f'},'en':{'url':str(i)}} for i in range(n)]}
            with patch.object(TocMatcher,'match',return_value=old),patch.object(self.matcher.structure,'match') as v2:
                r=self.matcher.match_for_source('fr','en','f')
                self.assertEqual(r['pairs'],old['pairs'])
                self.assertEqual(r['matcher_variant'],'original')
                v2.assert_not_called()

    def test_new_parser_only_on_source_abstention(self):
        old={'pairs':[{'fr':{'url':'other'}}]}
        new={'pairs':[{'fr':{'url':'f'},'en':{'url':'e'}}]}
        with patch.object(TocMatcher,'match',return_value=old),patch.object(self.matcher.structure,'match',return_value=new) as v2:
            r=self.matcher.match_for_source('fr','en','f')
            self.assertEqual(r['pairs'],new['pairs'])
            self.assertEqual(r['matcher_variant'],'structure_on_abstention')
            v2.assert_called_once_with('fr','en')

    def test_v2_failure_preserves_ordinary_fallback(self):
        old={'pairs':[]}
        with patch.object(TocMatcher,'match',return_value=old),patch.object(self.matcher.structure,'match',side_effect=RuntimeError('offline')):
            r=self.matcher.match_for_source('fr','en','f')
            self.assertEqual(r['pairs'],[])
            self.assertEqual(r['matcher_variant'],'original_abstention')
            self.assertEqual(r['structure_error'],'RuntimeError')
