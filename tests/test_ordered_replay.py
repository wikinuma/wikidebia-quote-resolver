import unittest
import tempfile
from unittest.mock import patch
from requests.exceptions import ReadTimeout
from resolver import Client,RequestBudgetExceeded
from pilot_full import PilotClient,recorded_events,replay_mismatches
from navigation import error_kind

URL='https://www.marxists.org/archive/test.htm'

class OrderedReplayTests(unittest.TestCase):
    def test_typed_network_failure_and_memoized_repeat(self):
        with tempfile.TemporaryDirectory() as d:
            c=PilotClient(d,delay=0)
            with patch.object(Client,'get',side_effect=ReadTimeout('timed out')):
                kinds=[]
                for _ in range(2):
                    with self.assertRaises(Exception) as e: c.get(URL)
                    kinds.append(error_kind(e.exception))
            self.assertEqual(kinds,['ReadTimeout','RuntimeError'])
            r=PilotClient(d,offline=True,expected_events=recorded_events(c.events))
            replay=[]
            for _ in range(2):
                with self.assertRaises(Exception) as e: r.get(URL)
                replay.append(error_kind(e.exception))
            self.assertEqual(kinds,replay)
            self.assertEqual(replay_mismatches(c.events,r.events),[])
            self.assertEqual(r.requests,0)

    def test_budget_then_success_is_replayable(self):
        import base64,json,time
        from pathlib import Path
        from resolver import sha
        with tempfile.TemporaryDirectory() as d:
            c=PilotClient(d,budget=0,delay=0)
            with self.assertRaises(RequestBudgetExceeded): c.get(URL)
            b=b'<p>Text</p>'
            Path(d,sha(URL.encode())+'.json').write_text(json.dumps({'body':base64.b64encode(b).decode(),
                'url':URL,'request_url':URL,'sha256':sha(b),'at':time.time()}))
            c.budget=1
            self.assertEqual(c.get(URL),(b,URL))
            r=PilotClient(d,offline=True,expected_events=recorded_events(c.events))
            with self.assertRaises(RequestBudgetExceeded): r.get(URL)
            self.assertEqual(r.get(URL),(b,URL))
            self.assertEqual(replay_mismatches(c.events,r.events),[])

    def test_detects_order_missing_and_conflicting_bytes(self):
        one={'request_url':URL,'status':'ok','sha256':'a'*64,'final_url':URL}
        two={**one,'request_url':URL+'?x=1'}
        self.assertTrue(replay_mismatches([one,two],[two,one]))
        self.assertTrue(replay_mismatches([one,two],[one]))
        with self.assertRaises(ValueError):
            recorded_events([one,{**one,'sha256':'b'*64}])
