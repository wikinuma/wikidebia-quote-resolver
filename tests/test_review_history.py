import unittest
from copy import deepcopy
from review_history import ReviewedWindows, source_identity


class WindowCoverageTests(unittest.TestCase):
    def setUp(self):
        self.source={'source_key':'source','occurrences':[{'case_id':'a','ref':{'revision':1}}],
                     'source_statuses':['exact'],'candidates':['p1','p2']}
        self.packet={'targets':{
            'p1':{'document':{'url':'one','body_sha256':'a'},'char_start':5,'char_end':10},
            'p2':{'document':{'url':'one','body_sha256':'a'},'char_start':25,'char_end':30}}}
        self.history=ReviewedWindows.__new__(ReviewedWindows)
        from collections import defaultdict
        self.history.by_source=defaultdict(list)
        from review_history import windows
        self.doc=windows(self.packet,self.source)[0][0]
        self.key=source_identity(self.source)

    def test_containment_requires_one_completed_review(self):
        h=self.history
        h.by_source[self.key]=[('one',[(self.doc,0,15)]),('two',[(self.doc,20,40)])]
        self.assertIsNone(h.covered_by(self.packet,self.source))
        h.by_source[self.key].append(('both',[(self.doc,0,15),(self.doc,20,40)]))
        self.assertEqual(h.covered_by(self.packet,self.source),'both')
        new=deepcopy(self.packet)
        new['targets']['p2']['char_end']=41
        self.assertIsNone(h.covered_by(new,self.source))

    def test_source_status_or_revision_change_is_new_evidence(self):
        self.history.by_source[self.key]=[('prior',[(self.doc,0,100)])]
        for s in ({**self.source,'source_statuses':['variant']},
                  {**self.source,'source_key':'changed'},
                  {**self.source,'occurrences':[{'case_id':'a','ref':{'revision':2}}]}):
            self.assertIsNone(self.history.covered_by(self.packet,s))
