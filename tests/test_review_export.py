import unittest
from unittest.mock import Mock
from resolver import Document,sha
from review_export import materialize,select_primary

class ReviewExportTests(unittest.TestCase):
    def test_missing_candidate_does_not_fetch(self):
        a=Mock()
        case={'id':'a','metadata':{},'citation_ref':{}}
        self.assertEqual(materialize(a,case,{})['status'],'no_primary_candidate')
        a.input.assert_not_called()

    def test_only_verified_text_in_private_packet(self):
        quote='Une citation.'
        doc=Document('https://www.marxists.org/archive/a.htm',['A quotation.'],'d')
        case={'id':'a','metadata':{},'citation_ref':{'quote_sha256':sha(quote.encode())}}
        candidate={'url':doc.url,'char_start':0,'char_end':12}
        a=Mock()
        a.input.return_value=({'status':'verified_pinned_citation'},quote)
        a.span.return_value={'status':'verified_document_and_span'}
        a.document.return_value=doc
        r=materialize(a,case,{'candidates':[candidate]})
        self.assertEqual(r['status'],'ready')
        self.assertEqual(r['en_candidate'],'A quotation.')
        a.span.return_value={'status':'span_mismatch'}
        r=materialize(a,case,{'candidates':[candidate]})
        self.assertEqual(r['status'],'review_unavailable')
        self.assertNotIn('fr_quote',r)
        self.assertNotIn('en_candidate',r)

    def test_chapter_correction_selected_only_if_valid(self):
        a={'url':'old'}
        b={'url':'new'}
        r={'candidates':[a]}
        self.assertEqual(select_primary(r,{'new':{'candidates':[b]},'new_primary_in_chapter':True}),(b,'reviewed_chapter'))
        self.assertEqual(select_primary(r,{'new':{'candidates':[b]},'new_primary_in_chapter':False}),(a,'refined'))
