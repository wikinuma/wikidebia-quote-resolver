import unittest
from rank_fusion import interleave,fused_review_queue
from resolver import Document,sha

def row(i,doc='u'):
    return {'url':doc,'char_start':i,'char_end':i+1,'text_sha256':sha(str(i).encode())}

class FusionTests(unittest.TestCase):
    def test_interleave_deduplicates_exact_provenance_only(self):
        a,b,c,d=row(0),row(1),row(2),row(3)
        self.assertEqual(interleave([a,b,c],[a,d,c],4),[a,b,d,c])
        self.assertEqual(interleave([a],[row(0,'other')]),[a,row(0,'other')])
    def test_block_and_character_span_reconcile(self):
        doc=Document('u',['first','second'],'h',revision=12)
        base={'url':'u','sha256':'h','revision':12,'text':'second'}
        r=fused_review_queue([{**base,'char_start':7,'char_end':13}],[{**base,'block':1,'span':1}],[doc])
        self.assertEqual(len(r['candidates']),1)
        self.assertFalse(r['auto_publish'])
    def test_invalid_source_text_rejected(self):
        doc=Document('u',['first'],'h')
        with self.assertRaises(ValueError):
            fused_review_queue([{'url':'u','sha256':'h','char_start':0,'char_end':5,'text':'other'}],[],[doc])
