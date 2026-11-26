from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile
import unittest

from alignment import text_and_starts
from citation_registry import fingerprint
from grouped_review import build_packet,review_view
from resolver import sha
from review_bundle import build_bundle,write_bundle,read_packet
from test_citation_registry import RAW,QUOTE,document,ref


class BundleTests(unittest.TestCase):
    def make(self,doc,cid,lo,hi):
        raw = RAW.replace(QUOTE,QUOTE+' '+cid)
        text,_ = text_and_starts(doc)
        cand = {'url':doc.url,'revision':doc.revision,'sha256':doc.digest,
                'char_start':lo,'char_end':hi,
                'text_sha256':sha(text[lo:hi].encode())}
        case = {'id':cid,'wiki_title':cid,'citation_ref':ref(raw)}
        p = build_packet([(case,{'id':cid,'candidates':[cand]})],
                         lambda title,rev:raw,lambda url,lang:doc)
        return fingerprint(p),p

    def test_overlap_sharing_preserves_exact_independent_views(self):
        d = document(blocks=['Alpha test é beta. Gamma test. Delta test.'])
        a = self.make(d,'1',0,22)
        b = self.make(d,'2',10,35)
        c = self.make(d,'3',0,22)
        pairs = [a,b,c]
        index,views,shared = build_bundle(pairs,'a'*64)
        self.assertEqual(index['occurrences'],3)
        self.assertEqual(index['windows_before_sharing'],3)
        self.assertEqual(index['shared_windows'],1)
        self.assertEqual(index['english_chars_after_sharing'],35)
        self.assertLess(index['english_chars_after_sharing'],
                        index['english_chars_before_sharing'])
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'bundle.zip'
            write_bundle(path,pairs,'a'*64)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            with ZipFile(path) as z:
                self.assertEqual(len(z.namelist()),5)
            for i,(_,packet) in enumerate(pairs,1):
                self.assertEqual(read_packet(path,i),review_view(packet))

    def test_same_text_different_document_is_not_merged(self):
        a = self.make(document(),'1',0,20)
        b = self.make(document(digest='d'*64),'2',0,20)
        index,_,_ = build_bundle([a,b],'a'*64)
        self.assertEqual(index['shared_windows'],2)
        with self.assertRaises(ValueError):
            build_bundle([a,a],'a'*64)

    def test_inconsistent_overlap_rejected(self):
        a = self.make(document(blocks=['A'*40]),'1',0,20)
        b = self.make(document(blocks=['B'*40]),'2',10,30)
        # Document identity includes a parser fingerprint, so independently
        # differing extracted text is not merged even if body hashes agree.
        index,_,_ = build_bundle([a,b],'a'*64)
        self.assertEqual(index['shared_windows'],2)
