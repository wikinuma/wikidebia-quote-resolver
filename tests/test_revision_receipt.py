import json
import unittest
from unittest.mock import Mock
from revision_receipt import RevisionRefresher
from resolver import Document, API, sha

class RevisionRefreshTests(unittest.TestCase):
    def setUp(self):
        self.text = 'A sufficiently long passage from a published edition, whose characters must match exactly and without edits.'
        self.url = 'https://wikirouge.net/texts/en/Test'
        self.doc = Document(self.url,[self.text],'b'*64,'Test',12)
        self.entry = {'url':self.url,'revision':12,'sha256':'a'*64,'text_sha256':sha(self.text.encode()),
                      'char_start':0,'char_end':len(self.text)}
        self.client=Mock()
        self.f=RevisionRefresher(self.client)
        self.reply(self.text)

    def reply(self,text,rev=12,title='Test'):
        raw=json.dumps({'parse':{'title':title,'revid':rev,'text':'<div class="mw-parser-output"><p>'+text+'</p></div>'}}).encode()
        self.client.get.return_value=(raw,API['en'])

    def test_refresh_is_independent_and_preserves_old_mismatch(self):
        r=self.f.refresh(self.doc,self.entry)
        self.assertEqual(r['status'],'verified_same_revision_span')
        self.assertEqual(r['original_receipt']['status'],'span_matches_changed_document')
        self.assertFalse(r['original_html_matches'])
        self.assertEqual(r['fresh_receipt']['status'],'verified_document_and_span')
        self.assertFalse(r['auto_publish'])
        self.assertNotIn(self.text,str(r))
        self.f.refresh(self.doc,self.entry)
        self.client.get.assert_called_once()

    def test_missing_and_changed_revision_are_not_accepted(self):
        for rev in (None,13):
            self.assertEqual(self.f.refresh(self.doc,{**self.entry,'revision':rev})['status'],'not_verified')
        self.client.get.assert_not_called()

    def test_changed_span_is_not_accepted(self):
        self.assertEqual(self.f.refresh(self.doc,{**self.entry,'text_sha256':'f'*64})['status'],'not_verified')
        self.client.get.assert_not_called()

    def test_api_wrong_title(self):
        self.reply(self.text,title='Other')
        self.assertEqual(self.f.refresh(self.doc,self.entry)['reason'],'pinned_revision_unavailable')

    def test_api_wrong_revision(self):
        self.reply(self.text,rev=13)
        self.assertEqual(self.f.refresh(self.doc,self.entry)['reason'],'pinned_revision_unavailable')

    def test_duplicate_pinned_passage(self):
        self.reply(self.text+' '+self.text)
        self.assertEqual(self.f.refresh(self.doc,self.entry)['reason'],'pinned_span_missing_or_ambiguous')

    def test_pinned_text_difference(self):
        self.reply(self.text.replace('exactly','approximately'))
        self.assertEqual(self.f.refresh(self.doc,self.entry)['reason'],'pinned_span_missing_or_ambiguous')

    def test_mia_not_allowed_even_if_revision_present(self):
        url='https://www.marxists.org/archive/test.htm'
        self.doc.url=url
        self.assertEqual(self.f.refresh(self.doc,{**self.entry,'url':url})['status'],'not_verified')
        self.client.get.assert_not_called()
