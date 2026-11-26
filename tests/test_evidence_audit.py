import unittest
from unittest.mock import Mock,patch
from resolver import Document,sha
from evidence_audit import span_receipt,publication_metadata,Auditor,summarize,document_provider


class EvidenceAuditTests(unittest.TestCase):
    def setUp(self):
        self.doc=Document('https://www.marxists.org/archive/test/a.htm',
                          ['First paragraph.','Second paragraph.'],'a'*64)
        self.entry={'url':self.doc.url,'sha256':self.doc.digest,'text_sha256':sha(b'Second paragraph.'),
                    'block':1,'span':1}

    def test_reconstruct_block_coordinates_and_hash(self):
        r=span_receipt(self.doc,self.entry)
        self.assertEqual(r['status'],'verified_document_and_span')
        self.assertEqual(r['char_start'],18)
        self.assertFalse(r['boundary_verified'])
        self.assertEqual(r['bilingual_equivalence'],'unreviewed')
        self.assertEqual(r['rights'],'unreviewed')

    def test_changed_document_not_strict_verification(self):
        r=span_receipt(self.doc,{**self.entry,'sha256':'b'*64})
        self.assertEqual(r['status'],'span_matches_changed_document')
        self.assertFalse(r['document_matches'])
        r=span_receipt(self.doc,{**self.entry,'text_sha256':'b'*64})
        self.assertEqual(r['status'],'span_mismatch')

    def test_revisions_and_coordinates(self):
        self.assertEqual(span_receipt(self.doc,{**self.entry,'revision':12})['status'],'revision_mismatch')
        with self.assertRaises(ValueError):
            span_receipt(self.doc,{**self.entry,'char_start':-1,'char_end':3})

    def test_license_notice_does_not_authorize_reuse(self):
        html=b'<p>Translated: George Hanna<br>Source: Progress Publishers, 1972<br>Copyleft: Archive. GNU Free Documentation License</p><p>Copyright: a different edition</p>'
        m=publication_metadata(html,'mia')
        self.assertIn('GFDL',m['license_signals'])
        self.assertIn('copyright_notice',m['license_signals'])
        self.assertEqual(m['fields']['translated'],['George Hanna'])
        self.assertFalse(m['reuse_authorized'])

    def test_reject_unrelated_hosts_and_language(self):
        for u in ('https://example.com/a','https://www.marxists.org/francais/a.htm',
                  'https://wikirouge.net/texts/fr/Test'):
            with self.assertRaises(ValueError):
                document_provider(u,'en')

    def test_public_documents_fetched_once_per_language_url(self):
        c=Mock()
        c.get.return_value=(b'<p>Text</p>',self.doc.url)
        a=Auditor(c)
        self.assertIs(a.document(self.doc.url,'en'),a.document(self.doc.url,'en'))
        c.get.assert_called_once_with(self.doc.url)

    def test_summary_does_not_equate_span_integrity_and_translation(self):
        row={'input':{'status':'verified_pinned_citation'},'source':[{'status':'verified_document_and_span'}],
             'targets':[{'status':'verified_document_and_span','initial_candidate':True}]}
        s=summarize([row])
        self.assertEqual(s['cases_with_strict_primary_and_source'],1)
        self.assertIsNone(s['verified_exact_english_quotes'])
        self.assertIsNone(s['verified_reuse_rights'])

    def test_only_documented_state_mirror_with_edition_labels(self):
        from evidence_audit import STATE_MIRROR_TITLE
        from urllib.parse import quote
        url = 'https://wikirouge.net/texts/en/' + quote(STATE_MIRROR_TITLE.replace(' ', '_'))
        self.assertEqual(document_provider(url, 'en'), 'wikirouge')
        for u in (url+'?x=1', url+'#section', url+'x',
                  'https://wikirouge.net/texts/en/Other%3A_Title'):
            with self.assertRaises(ValueError):
                document_provider(u, 'en')
        c = Mock()
        c.get.return_value = (b'<p>Text</p>', url)
        self.assertEqual(Auditor(c).document(url, 'en')['status'], 'document_unavailable')
        c.get.return_value = (b'<p>11 July 1919 Lenin Collected Works 4th English Edition Progress Publishers 1972 Volume 29 470-488</p><p>Text</p>', url)
        self.assertEqual(Auditor(c).document(url, 'en').title, STATE_MIRROR_TITLE)
