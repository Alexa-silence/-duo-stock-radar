"""Offline regression checks against the Python actually embedded in monitor.yml."""
import contextlib
import datetime
import io
import json
import os
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


WORKFLOW = (Path(__file__).resolve().parents[1] / '.github/workflows/monitor.yml').read_text()
SOURCE = textwrap.dedent(WORKFLOW.split("python3 <<'PY'\n", 1)[1].split('          PY\n', 1)[0])
PART = 'MK2M4CH/A'
KEY = 'R389|' + PART
REAL_DATETIME = datetime.datetime


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old_cwd = os.getcwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, self.old_cwd)
        self.status = 'unavailable'
        self.missing_store = False
        self.fail_bark = False
        self.pushes = 0
        self.round = 0
        self.all_unknown = False
        self.queries = []

    def run_monitor(self, test_bark=False):
        self.round += 1
        now = REAL_DATETIME(2026, 10, 23, 10, self.round, tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
        class Clock:
            @staticmethod
            def now(tz):
                return now

        def urlopen(request, timeout):
            url = urlparse(request.full_url)
            if url.netloc == 'api.day.app':
                if self.fail_bark:
                    raise RuntimeError('simulated Bark failure')
                self.pushes += 1
                response = io.BytesIO(b'{}')
            else:
                params = parse_qs(url.query)
                store_id = params['store'][0]
                self.queries.append(params)
                parts = {params[f'parts.{i}'][0]: {'pickupDisplay': self.status if store_id == 'R389' and i == 0 else 'unavailable'} for i in range(3)}
                if self.all_unknown:
                    parts = {}
                stores = [{'storeNumber': store_id, 'partsAvailability': parts}]
                if self.missing_store and store_id == 'R389':
                    stores = [{'storeNumber': 'R999', 'partsAvailability': parts}]
                response = io.BytesIO(json.dumps({'body': {'stores': stores}}).encode())
            response.getcode = lambda: 200
            return response

        with patch.dict(os.environ, {'BARK_KEY': 'offline-test', 'TEST_BARK': str(test_bark)}), patch('urllib.request.urlopen', urlopen), patch('datetime.datetime', Clock), contextlib.redirect_stdout(io.StringIO()):
            exec(compile(SOURCE, 'monitor.yml', 'exec'), {})

    def history(self):
        return json.loads(Path('history.json').read_text())

    def test_stock_lifecycle_and_exact_store(self):
        self.run_monitor()
        self.assertEqual(self.history(), [])
        self.status = 'available'
        self.run_monitor()
        first = self.history()[0]['firstSeen']
        self.assertEqual(self.pushes, 1)
        self.run_monitor()
        self.assertEqual(len(self.history()), 1)
        self.assertEqual(self.history()[0]['firstSeen'], first)
        self.assertGreater(self.history()[0]['lastSeen'], first)
        last = self.history()[0]['lastSeen']
        self.assertEqual(self.pushes, 1)
        self.missing_store = True
        self.run_monitor()
        self.assertEqual(self.history()[0]['lastSeen'], last)
        self.assertIsNone(self.history()[0]['endedAt'])
        self.assertTrue(json.loads(Path('state.json').read_text())[KEY])
        self.missing_store = False
        self.status = ''
        self.run_monitor()
        self.assertEqual(self.history()[0]['lastSeen'], last)
        self.assertIsNone(self.history()[0]['endedAt'])
        self.status = 'unavailable'
        self.run_monitor()
        self.assertGreater(self.history()[0]['endedAt'], last)
        self.assertEqual(self.history()[0]['lastSeen'], last)
        self.run_monitor()
        self.assertEqual(len(self.history()), 1)
        self.status = 'available'
        self.run_monitor()
        self.assertEqual(len(self.history()), 2)
        self.assertEqual(self.pushes, 2)
        self.assertEqual(self.history()[1]['storeId'], 'R389')
        self.assertEqual(self.history()[1]['part'], PART)
        self.assertTrue(all(p['pl'] == ['true'] and len([k for k in p if k.startswith('parts.')]) == 3 for p in self.queries))

    def test_bark_failure_keeps_history_and_retries_without_duplicate_event(self):
        self.run_monitor()
        self.status = 'available'
        self.fail_bark = True
        with self.assertRaisesRegex(RuntimeError, 'simulated Bark failure'):
            self.run_monitor()
        self.assertEqual(len(self.history()), 1)
        self.assertFalse(json.loads(Path('state.json').read_text())[KEY])
        snapshot = json.loads(Path('inventory.json').read_text())
        self.assertEqual(next(s for s in snapshot['stores'] if s['id'] == 'R389')['products'][0]['status'], 'available')
        self.fail_bark = False
        self.run_monitor()
        self.assertEqual(self.pushes, 1)
        self.assertEqual(len(self.history()), 1)
        self.assertTrue(json.loads(Path('state.json').read_text())[KEY])

    def test_corrupt_history_is_not_overwritten(self):
        for content in ('{broken', '{}', '[{}]'):
            Path('history.json').write_text(content)
            with self.assertRaises(ValueError):
                self.run_monitor()
            self.assertEqual(Path('history.json').read_text(), content)
        self.assertEqual(self.pushes, 0)

    def test_bark_test_does_not_touch_inventory(self):
        with self.assertRaises(SystemExit):
            self.run_monitor(test_bark=True)
        self.assertEqual(self.pushes, 1)
        self.assertFalse(Path('history.json').exists())
        self.assertFalse(Path('state.json').exists())
        self.assertFalse(Path('inventory.json').exists())

    def test_snapshot_keeps_unknown_separate_from_saved_bark_state(self):
        self.status = 'available'
        self.run_monitor()
        self.missing_store = True
        self.run_monitor()
        snapshot = json.loads(Path('inventory.json').read_text())
        self.assertEqual(len(snapshot['stores']), 8)
        self.assertEqual(next(s for s in snapshot['stores'] if s['id'] == 'R389')['products'][0]['status'], 'unknown')
        self.assertTrue(json.loads(Path('state.json').read_text())[KEY])
        self.all_unknown = True
        with self.assertRaisesRegex(RuntimeError, 'no recognizable'):
            self.run_monitor()
        snapshot = json.loads(Path('inventory.json').read_text())
        self.assertTrue(all(p['status'] == 'unknown' for s in snapshot['stores'] for p in s['products']))
        self.assertIsNone(self.history()[0]['endedAt'])


if __name__ == '__main__':
    unittest.main()
