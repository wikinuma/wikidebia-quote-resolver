import unittest
from review_memory import proof, lookup, keyed, read_pinned
from tempfile import TemporaryDirectory
from pathlib import Path

class MemoryTests(unittest.TestCase):
    def test_exact_fragment_and_non_publishable_history(self):
        f={'relative_start':0,'relative_end':10,'text_sha256':'b'*64}
        p=proof('complete_equivalent','https://www.marxists.org/archive/test.htm',None,
                'a'*64,'b'*64,20,30,[f])
        self.assertEqual(p['fragments'][0]['char_start'],20)
        self.assertIn('historical',p['proof_status'])

    def test_reject_truncated_complete_and_unaccepted_fragment(self):
        f={'relative_start':0,'relative_end':5,'text_sha256':'b'*64}
        for d in ('complete_equivalent','edition_variant'):
            with self.assertRaises(ValueError):
                proof(d,'https://www.marxists.org/archive/test.htm',None,'a'*64,'b'*64,20,30,[f])

    def test_fingerprint_and_url_guard(self):
        ref={'quote_sha256':'a'*64,'template_sha256':'b'*64}
        meta={'mia_urls':['https://www.marxists.org/francais/test.htm']}
        r={'citation_ref':ref,'metadata':meta,'auto_publish':False}
        m={'cases':[r]}
        self.assertIs(lookup(m,meta,ref),r)
        self.assertIsNone(lookup(m,meta,{**ref,'template_sha256':'c'*64}))
        self.assertIsNone(lookup(m,{'mia_urls':['https://www.marxists.org/francais/other.htm']},ref))
        self.assertIsNone(lookup({'cases':[r,r]},meta,ref))

    def test_duplicate_case(self):
        with self.assertRaises(ValueError):
            keyed([{'id':'a'},{'id':'a'}],'id')

    def test_pin_rejects_modified_bytes(self):
        with TemporaryDirectory() as tmp:
            f=Path(tmp)/'test.json'
            f.write_text('{}')
            with self.assertRaises(ValueError):
                read_pinned(f,'a'*64)
