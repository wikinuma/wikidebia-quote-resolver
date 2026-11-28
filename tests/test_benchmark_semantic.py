import unittest
from resolver import Document
from benchmark_semantic import evaluate

class SemanticMetricTests(unittest.TestCase):
    def test_omission_penalized_as_extra_text(self):
        d=Document('url',['0123456789'],'digest')
        c={'url':'url','sha256':'digest','char_start':0,'char_end':10,'text':'0123456789'}
        r=evaluate([c],[{'char_start':0,'char_end':3},{'char_start':7,'char_end':10}],d)
        self.assertTrue(r['top1']['full_cover'])
        self.assertEqual(r['top1']['iou'],.6)
        self.assertFalse(r['top1']['exact_single_fragment'])
        self.assertFalse(r['top1']['iou80'])

    def test_wrong_extracted_text_fails_closed(self):
        d=Document('url',['actual text'],'digest')
        c={'url':'url','sha256':'digest','char_start':0,'char_end':6,'text':'invented'}
        with self.assertRaises(ValueError):
            evaluate([c],[{'char_start':0,'char_end':6}],d)
