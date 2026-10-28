import json
import tempfile
import unittest
from pathlib import Path

from backend.app import LogSync, export_snapshot
from backend.engine import Engine
from backend.quota import parse_quota
from test_engine import context, usage


def quota(ts='2026-10-26T06:03:22Z', used=64, minutes=10080, limit='codex', reset=1793513402, secondary=None):
    return {'type': 'event_msg', 'timestamp': ts, 'payload': {'type': 'token_count', 'rate_limits': {
        'limit_id': limit, 'primary': {'used_percent': used, 'window_minutes': minutes, 'resets_at': reset},
        'secondary': secondary, 'credits': {'balance': 'private-do-not-export'}}}}


class QuotaTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root / 'sessions').mkdir(); (self.root / 'archived_sessions').mkdir()
        self.engine = Engine(self.root, self.root / 'index.sqlite')

    def write(self, events, name='sessions/a.jsonl'):
        path = self.root / name; path.write_text(''.join(json.dumps(x) + '\n' for x in events)); return path

    def query(self, **changes):
        return self.engine.query({'start': '2026-10-26T14:00', 'end': '2026-10-26T15:00',
            'timezone': 'Asia/Shanghai', 'granularity': '1h', **changes})['quota']

    def test_restore_is_bracketed_not_assigned_exact_reset_time(self):
        self.write([quota(), quota('2026-10-26T06:19:09Z', 0, reset=1793600336)])
        self.engine.refresh(); event = self.query()['windows'][0]['events'][0]
        self.assertEqual(event['kind'], 'full_recovery')
        self.assertEqual(event['remaining_before'], 36); self.assertEqual(event['remaining_after'], 100)
        self.assertIn('06:03:22', event['lower_timestamp']); self.assertIn('06:19:09', event['upper_timestamp'])
        self.assertTrue(event['scheduled_reset_changed'])

    def test_tiny_bounce_is_not_recovery_and_windows_never_mix(self):
        self.write([quota(used=55), quota('2026-10-26T06:04:00Z', 54),
                    quota('2026-10-26T06:05:00Z', 0, limit='other'),
                    quota('2026-10-26T06:06:00Z', 0, minutes=300),
                    quota('2026-10-26T06:07:00Z', 56, secondary={'used_percent': 0, 'window_minutes': 300})])
        self.engine.refresh(); windows = self.query()['windows']
        self.assertEqual(len(windows), 4); self.assertFalse(any(w['events'] for w in windows))

    def test_append_changes_generation_and_snapshot_without_token_record(self):
        path = self.write([quota()]); self.engine.refresh(); sync = LogSync(self.engine); before = sync.snapshot()
        with path.open('a') as f: f.write(json.dumps(quota('2026-10-26T06:19:09Z', 0)) + '\n')
        sync.scan(); after = sync.snapshot()
        self.assertNotEqual(before['queries']['index_state']['rows'][0]['generation'], after['queries']['index_state']['rows'][0]['generation'])
        self.assertEqual(len(after['queries']['quota']['rows']), 2)
        self.assertNotIn('private-do-not-export', json.dumps(after))

    def test_duplicate_partial_rewrite_removal_and_old_index_backfill(self):
        path = self.write([quota()]); self.write([quota()], 'archived_sessions/copy.jsonl')
        self.engine.refresh(); self.assertEqual(len(self.engine.quota_rows), 1)
        with path.open('a') as f: f.write(json.dumps(quota('2026-10-26T06:19:09Z', 0)))
        self.engine.refresh(); self.assertEqual(len(self.engine.quota_rows), 1)
        with path.open('a') as f: f.write('\n')
        self.engine.refresh(); self.assertEqual(len(self.engine.quota_rows), 2)
        with self.engine.connect() as c:
            for file, state in c.execute('SELECT path,state FROM files').fetchall():
                old = json.loads(state); old.pop('parser_version'); c.execute('UPDATE files SET state=? WHERE path=?', (json.dumps(old), file))
            c.execute('DELETE FROM quota_occurrences')
        self.engine.refresh(); self.assertEqual(len(self.engine.quota_rows), 2)
        path.write_text(json.dumps(quota('2026-10-26T06:10:00Z', used=12)) + '\n'); self.engine.refresh()
        self.assertEqual({r['used_percent'] for r in self.engine.quota_rows}, {12, 64})
        path.unlink(); self.engine.refresh(); self.assertEqual(len(self.engine.quota_rows), 1)

    def test_range_context_exclusive_end_and_session_filters(self):
        self.write([context(), usage(), quota('2026-10-26T05:59:00Z'), quota('2026-10-26T06:19:09Z', 0), quota('2026-10-26T07:00:00Z', 5)])
        self.engine.refresh(); a = self.query(); b = self.query(sessions={'mode': 'explicit', 'ids': []}, include_compaction=False)
        self.assertEqual(a, b); window = a['windows'][0]
        self.assertEqual(len(window['points']), 1); self.assertIsNotNone(window['context_before'])
        self.assertEqual(len(window['events']), 1)
        empty = self.query(start='2026-10-26T13:00', end='2026-10-26T13:30')
        self.assertEqual(empty['windows'], [])

    def test_conflicting_same_timestamp_excluded(self):
        self.write([quota(), quota(used=0)]); self.engine.refresh()
        self.assertEqual(self.engine.status['quota_conflicts'], 1); self.assertFalse(self.query()['windows'])

    def test_parser_rejects_invalid_fields_and_preserves_missing_deadline(self):
        for used in (-1, 101, float('nan'), True, '64'):
            self.assertEqual(parse_quota(quota(used=used)), [])
        self.assertEqual(parse_quota(quota(minutes=0)), [])
        self.assertEqual(parse_quota(quota(ts='invalid')), [])
        self.assertEqual(parse_quota(quota(ts='2026-10-26T06:00:00')), [])
        self.assertIsNone(parse_quota(quota(reset=None))[0]['resets_at'])

    def test_quota_event_does_not_break_compaction_attribution(self):
        self.write([context(), usage(), quota(), {'type': 'compacted', 'payload': {}}]); self.engine.refresh()
        self.assertTrue(self.engine.rows[0]['compaction'])


if __name__ == '__main__': unittest.main()
