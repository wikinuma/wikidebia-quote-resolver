import unittest
from review_registry import verify_fragments
from resolver import sha

class ReviewRegistryTests(unittest.TestCase):
    def test_exact_substrings_not_generated_text(self):
        text='Start middle end'
        r=verify_fragments(text,{'decision':'omission_adjustment','fragments':[[0,5,sha(b'Start')],[13,16,sha(b'end')]]})
        self.assertEqual(len(r),2)
    def test_refuse_changed_fragment(self):
        with self.assertRaises(ValueError):
            verify_fragments('changed',{'decision':'boundary_adjustment','fragments':[[0,7,sha(b'correct')]]})
    def test_rejected_candidate_has_no_reusable_span(self):
        with self.assertRaises(ValueError):
            verify_fragments('abc',{'decision':'wrong_passage','fragments':[[0,3,sha(b'abc')]]})
    def test_complete_decision_cannot_silently_trim(self):
        with self.assertRaises(ValueError):
            verify_fragments('abc',{'decision':'complete_equivalent','fragments':[[0,2,sha(b'ab')]]})
