import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from backend.app import LogSync, Server
from backend.engine import Engine
from test_engine import context, usage


class LiveSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'sessions').mkdir()
        self.path = self.root / 'sessions/one.jsonl'
        self.path.write_text(json.dumps(context()) + '\n' + json.dumps(usage()) + '\n')
        self.engine = Engine(self.root, self.root / 'index.sqlite')
        self.engine.refresh()
        self.sync = LogSync(self.engine, interval=0.02)
        self.addCleanup(self.sync.stop)

    def wait_for(self, predicate):
        until = time.monotonic() + 2
        while not predicate() and time.monotonic() < until:
            time.sleep(0.01)
        self.assertTrue(predicate())

    def append(self, value):
        with self.path.open('a') as stream:
            stream.write(json.dumps(value) + '\n')

    def test_background_append_updates_snapshot_without_build(self):
        old = self.sync.snapshot()
        with patch('backend.app.build_dashboard', side_effect=AssertionError('Must not build during sync')):
            self.sync.start()
            self.append(usage('r2', tokens=200))
            self.wait_for(lambda: self.engine.status['record_count'] == 2)
            new = self.sync.snapshot()
            self.assertNotEqual(old['queries']['index_state']['rows'][0]['generation'], new['queries']['index_state']['rows'][0]['generation'])
            self.assertEqual(len(new['queries']['usage']['rows']), 2)
            self.assertTrue(self.sync.status()['auto_sync'])
            self.sync.scan()
            self.assertIs(new, self.sync.snapshot())  # unchanged rows reuse the full snapshot

    def test_partial_tail_not_published_until_complete(self):
        self.sync.start()
        raw = json.dumps(usage('r2'))
        with self.path.open('a') as stream:
            stream.write(raw[:-1])
        self.wait_for(lambda: self.sync.checked_at is not None)
        self.assertEqual(self.engine.status['record_count'], 1)
        with self.path.open('a') as stream:
            stream.write('}\n')
        self.wait_for(lambda: self.engine.status['record_count'] == 2)

    def test_failure_keeps_data_and_next_scan_recovers(self):
        before = self.sync.snapshot()
        with patch.object(self.engine, 'refresh', side_effect=OSError('temporary failure')):
            self.assertEqual(self.sync.scan()['refresh_error'], 'temporary failure')
        self.assertIs(before, self.sync.snapshot())
        self.append(usage('r2'))
        self.assertIsNone(self.sync.scan()['refresh_error'])
        self.assertEqual(len(self.sync.snapshot()['queries']['usage']['rows']), 2)

    def test_manual_and_background_scans_do_not_duplicate(self):
        self.sync.start()
        self.append(usage('r2'))
        workers = [threading.Thread(target=self.sync.scan) for _ in range(4)]
        for worker in workers: worker.start()
        for worker in workers: worker.join()
        self.assertEqual(self.engine.status['record_count'], 2)

    def test_removing_all_logs_publishes_empty_snapshot(self):
        self.path.unlink()
        self.sync.scan()
        self.assertEqual(self.sync.snapshot()['queries']['usage']['rows'], [])
        self.assertEqual(self.engine.status['file_count'], 0)

    def test_http_auto_snapshot_and_manual_sync(self):
        server = Server(('127.0.0.1', 0), self.engine, interval=0.02)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start(); server.sync.start()
        def stop():
            server.sync.stop(); server.shutdown(); server.server_close(); thread.join()
        self.addCleanup(stop)
        base = 'http://127.0.0.1:' + str(server.server_address[1])
        def request(path, data=None, headers=None):
            return urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=headers or {}), timeout=2)
        self.append(usage('r2'))
        self.wait_for(lambda: self.engine.status['record_count'] == 2)
        with request('/api/v1/snapshot') as response:
            snapshot = json.load(response)
        self.assertTrue(snapshot['localUsage'])
        self.assertEqual(len(snapshot['queries']['usage']['rows']), 2)
        from test_quota import quota
        self.append(quota()); self.append(quota('2026-10-26T06:19:09Z', 0))
        self.wait_for(lambda: self.engine.status['quota_record_count'] == 2)
        body = {'start': '2026-10-26T14:00', 'end': '2026-10-26T15:00', 'timezone': 'Asia/Shanghai', 'sessions': {'mode': 'explicit', 'ids': []}}
        with request('/api/v1/query', json.dumps(body).encode()) as response:
            queried = json.load(response)
        self.assertFalse(queried['models'])
        self.assertEqual(queried['quota']['windows'][0]['events'][0]['remaining_after'], 100)
        with request('/api/v1/snapshot') as response:
            self.assertEqual(len(json.load(response)['queries']['quota']['rows']), 2)
        with request('/api/v1/index/refresh', b'{}') as response:
            self.assertTrue(json.load(response)['auto_sync'])
        with self.assertRaises(urllib.error.HTTPError) as denied:
            request('/api/v1/index/refresh', b'{}', {'Origin': 'https://example.com'})
        self.assertEqual(denied.exception.code, 403)


if __name__ == '__main__':
    unittest.main()
