import json
import unittest
from pathlib import Path

from review_memory import load_effective_memory
from work_queue import build_queue


class WorkQueueTests(unittest.TestCase):
    def setUp(self):
        self.memory = load_effective_memory()
        self.research = json.loads(Path('data/mia/work-research.json').read_text())

    def test_real_queue(self):
        q = build_queue(self.memory, self.research)
        self.assertEqual(q['summary']['unresolved'], 62)
        self.assertEqual(q['summary']['excluded_accepted'], 38)
        self.assertEqual(q['summary']['cases_with_research_note'], 11)
        self.assertFalse(q['auto_publish'])
        self.assertEqual(len({c['case_id'] for g in q['groups'] for c in g['cases']}),62)
        self.assertEqual(q['groups'][0]['count'],8)
        self.assertEqual(q['groups'][0]['research']['status'],'wrong_work_excluded')

    def test_order_and_no_loss(self):
        q = build_queue(self.memory, self.research)
        self.memory['cases'].reverse()
        self.assertEqual(q, build_queue(self.memory, self.research))

    def test_notes_require_exact_identity(self):
        self.research['entries'][0]['article'] += ' (another work)'
        self.assertEqual(build_queue(self.memory,self.research)['summary']['cases_with_research_note'],3)

    def test_duplicate_note_rejected(self):
        self.research['entries'].append(self.research['entries'][0])
        with self.assertRaises(ValueError):
            build_queue(self.memory,self.research)
