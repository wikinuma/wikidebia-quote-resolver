import unittest
from unittest.mock import Mock
from resolver import Document,sha
from evidence_audit import span_receipt
from second_pass import mirror_span,compact,same_document_url,WR_STATE,expand_partial,ranked_candidates,public_row

class SecondPassTests(unittest.TestCase):
    def test_mirror_only_authentic_whitespace_variant(self):
        old=('A long passage with several words joinedtogether in the archive. '
             'The same characters must be preserved in a different transcription.')
        actual=old.replace('joinedtogether','joined together')
        d=Document(WR_STATE,['Unrelated preface.',actual,'Unrelated ending.'],'a'*64)
        c=mirror_span(old,d)
        self.assertIsNotNone(c)
        self.assertEqual(c['text_sha256'],sha(actual.encode()))
        self.assertEqual(span_receipt(d,c)['status'],'verified_document_and_span')
        self.assertEqual(compact(actual),compact(old))
        self.assertIsNone(mirror_span(old,Document(WR_STATE,[actual,actual],'a'*64)))
        self.assertIsNone(mirror_span(old,Document(WR_STATE,[actual.replace('preserved','changed')],'a'*64)))
        self.assertIsNone(mirror_span(old,Document(WR_STATE,['X'+actual],'a'*64)))

    def test_documented_mirror_url_only(self):
        self.assertTrue(same_document_url(WR_STATE,WR_STATE.replace('%3A',':')))
        self.assertFalse(same_document_url(WR_STATE,WR_STATE+'?oldid=1'))
        self.assertFalse(same_document_url(WR_STATE,WR_STATE.replace('wikirouge.net','example.com')))

    def test_context_expansion_preserves_source_text(self):
        d=Document('https://www.marxists.org/archive/a.htm',['Prior paragraph.','Quote.','Following paragraph.'],'a'*64)
        r={'char_start':18,'char_end':24}
        c=expand_partial(d,r)
        self.assertEqual(c['text_sha256'],sha(b'Prior paragraph.\n\nQuote.\n\nFollowing paragraph.'))
        self.assertIsNone(expand_partial(d,r,max_extra_words=1))

    def test_ranked_candidates_deduplicate(self):
        a={'text_sha256':'a'};b={'text_sha256':'b'}
        r={'section_alignment':{'candidates':[a]},'candidates':[a,b]}
        self.assertEqual(list(ranked_candidates(r)),[a,b])

    def test_public_packet_has_no_quote_text(self):
        row={'id':'a','fr_quote':'secret French excerpt','candidates':[{'en_candidate':'secret English excerpt','receipt':{}}]}
        self.assertNotIn('fr_quote',public_row(row))
        self.assertNotIn('en_candidate',public_row(row)['candidates'][0])
