import hashlib
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.engine import Engine
from backend.legacy_reader import index_coverage
from backend.logfiles import zstd_executable
from test_engine import context, usage
from test_quota import quota


@unittest.skipUnless(zstd_executable(), 'zstd is required for archive fixtures')
class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root/'sessions').mkdir(); (self.root/'archived_sessions').mkdir()
        self.engine = Engine(self.root, self.root/'index.sqlite')

    def archive(self, events, name='sessions/old.jsonl.zst', newline=True):
        text = '\n'.join(json.dumps(e) for e in events) + ('\n' if newline else '')
        data = subprocess.run([zstd_executable(), '-q', '-c'], input=text.encode(), capture_output=True, check=True).stdout
        path = self.root/name; path.write_bytes(data); return path

    def test_compressed_response_and_quota_are_read_without_extraction(self):
        path=self.archive([context(),usage(),quota()]); before=hashlib.sha256(path.read_bytes()).hexdigest()
        self.engine.refresh()
        self.assertEqual(len(self.engine.rows),1); self.assertEqual(self.engine.rows[0]['model'],'gpt-test')
        self.assertEqual(len(self.engine.quota_rows),1); self.assertEqual(self.engine.status['compressed_file_count'],1)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
        self.assertEqual(list((self.root/'sessions').iterdir()),[path])

    def test_plain_and_compressed_copies_are_deduplicated(self):
        events=[context(),usage(),quota()]; self.archive(events)
        (self.root/'archived_sessions/copy.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
        self.engine.refresh(); self.assertEqual(len(self.engine.rows),1); self.assertEqual(len(self.engine.quota_rows),1)

    def test_unchanged_archive_is_not_decoded_again_and_replace_reparses(self):
        self.archive([context(),usage()]); self.engine.refresh()
        with patch('backend.engine.compressed_lines',side_effect=AssertionError('Must reuse unchanged archive')):
            self.engine.refresh()
        self.archive([context(),usage('replacement')]); self.engine.refresh()
        self.assertEqual([r['response_id'] for r in self.engine.rows],['replacement'])

    def test_damaged_frame_keeps_previous_index_then_recovers(self):
        path=self.archive([context(),usage()]); self.engine.refresh()
        self.archive([context(),usage('replacement'),quota()]); path.write_bytes(path.read_bytes()[:-4])
        self.engine.refresh()
        self.assertEqual([r['response_id'] for r in self.engine.rows],['r1'])
        self.assertEqual(len(self.engine.quota_rows),0); self.assertEqual(self.engine.status['scan']['unreadable_files'],1)
        self.archive([context(),usage('recovered'),quota()]); self.engine.refresh()
        self.assertEqual([r['response_id'] for r in self.engine.rows],['recovered'])
        path.unlink(); self.engine.refresh(); self.assertFalse(self.engine.rows); self.assertFalse(self.engine.quota_rows)

    def test_missing_decoder_does_not_publish_partial_data(self):
        self.archive([context(),usage()])
        with patch('backend.logfiles.zstd_executable',return_value=None): self.engine.refresh()
        self.assertFalse(self.engine.rows); self.assertEqual(self.engine.status['scan']['unreadable_files'],1)

    def test_legacy_cumulative_usage_is_disclosed_not_added_repeatedly(self):
        old=quota('2026-07-11T04:00:00Z'); old['payload']['info']={'total_token_usage':{'total_tokens':999999}}
        self.archive([old,old]); self.archive([context(),usage()],name='sessions/modern.jsonl.zst')
        self.engine.refresh()
        self.assertEqual(sum(r['total_tokens'] for r in self.engine.rows),110)
        self.assertEqual(self.engine.status['legacy_only_files'],1)
        self.assertIn('2026-07-11',self.engine.status['legacy_first'])
        self.assertIn('2026-07-11',self.engine.status['quota_first'])

    def test_complete_archive_can_end_without_a_newline(self):
        self.archive([context(),usage()],newline=False); self.engine.refresh()
        self.assertEqual(len(self.engine.rows),1)

    def test_compressed_path_is_available_in_thread_index_coverage(self):
        original=self.root/'sessions/old.jsonl'; self.archive([context(),usage()])
        with sqlite3.connect(self.root/'state_5.sqlite') as c:
            c.execute('CREATE TABLE threads(rollout_path TEXT,tokens_used INTEGER)')
            c.executemany('INSERT INTO threads VALUES(?,?)',[(str(original),100),(str(self.root/'truly-missing.jsonl'),200)])
        coverage=index_coverage(self.root)
        self.assertEqual(coverage['compressed_log_files'],1); self.assertEqual(coverage['missing_log_files'],1)
        self.assertEqual(coverage['missing_logs_with_nonzero_counter'],1)


if __name__=='__main__': unittest.main()
