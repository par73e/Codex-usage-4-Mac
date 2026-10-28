import json, unittest
import test_engine as fixture
from test_engine import context, usage


def meta(thread='old',ts='2026-07-10T00:00:00Z'):
    return {'type':'session_meta','timestamp':ts,'payload':{'id':thread,'timestamp':ts}}

def count(value,ts,**extra):
    return {'type':'event_msg','timestamp':ts,'payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':value-10,'output_tokens':10,'total_tokens':value}},**extra}}

class LegacyIntervalsTest(unittest.TestCase):
    setUp = fixture.EngineTest.setUp
    write = fixture.EngineTest.write
    def july(self,**kw):
        return self.engine.query({'start':'2026-07-10T00:00','end':'2026-07-11T00:00','timezone':'UTC','granularity':'1h','sessions':{'mode':'all'},**kw})
    def test_intervals_count_differences_and_duplicate_copies_once(self):
        events=[meta(),count(110,'2026-07-10T01:00:00Z'),count(110,'2026-07-10T01:30:00Z'),count(330,'2026-07-10T02:00:00Z')]
        p=self.write(events);self.write(events,'copy.jsonl','archived_sessions');before=p.read_bytes();self.engine.refresh();r=self.july()
        self.assertEqual(r['totals']['total_tokens'],330);self.assertEqual(r['response_count'],0);self.assertEqual(r['models'],[])
        self.assertEqual(len(r['legacy_intervals']),2);self.assertEqual(r['legacy_intervals'][1]['start_ms'],1783647000000)
        self.assertEqual(sum(b['estimated'] for b in r['total_buckets']),330);self.assertEqual(p.read_bytes(),before)
        self.assertEqual(r['options'][0]['id'],'old');self.assertEqual(r['options'][0]['estimated_tokens'],330)
    def test_partial_intervals_and_bins_conserve_integer_totals(self):
        self.write([meta(),count(111,'2026-07-10T02:00:00Z')]);self.engine.refresh()
        a=self.july(end='2026-07-10T01:00');b=self.july(start='2026-07-10T01:00')
        self.assertEqual(a['totals']['estimated_tokens']+b['totals']['estimated_tokens'],111)
        for gran in ['1h','6h','12h','day']:
            r=self.july(granularity=gran);self.assertEqual(r['totals']['total_tokens'],111);self.assertEqual(sum(x['tokens'] for x in r['total_buckets']),111)
    def test_mixed_format_prefix_not_double_counted(self):
        self.write([meta(),count(110,'2026-07-10T01:00:00Z'),context(),usage('new','2026-07-10T02:00:00Z',thread='old'),count(220,'2026-07-10T02:00:00.001Z')]);self.engine.refresh();r=self.july()
        self.assertEqual(r['totals']['total_tokens'],220);self.assertEqual(r['totals']['recorded_tokens'],110);self.assertEqual(r['totals']['estimated_tokens'],110)
        # A cumulative-only copied file cannot reintroduce telemetry after explicit responses.
        self.write([meta(),count(110,'2026-07-10T01:00:00Z'),count(220,'2026-07-10T02:00:00.001Z')],'copy.jsonl');self.engine.refresh()
        self.assertEqual(self.july()['totals']['total_tokens'],220)
    def test_incremental_legacy_and_session_filter(self):
        p=self.write([meta(),count(110,'2026-07-10T01:00:00Z')]);self.engine.refresh();generation=self.engine.generation
        with p.open('a') as f:f.write(json.dumps(count(330,'2026-07-10T02:00:00Z'))+'\n')
        self.engine.refresh();self.assertNotEqual(generation,self.engine.generation);self.assertEqual(self.july()['totals']['total_tokens'],330)
        self.assertEqual(self.july(sessions={'mode':'explicit','ids':[]})['totals']['total_tokens'],0)
        self.assertEqual(self.july(sessions={'mode':'explicit','ids':['old']})['totals']['total_tokens'],330)
        p.unlink();self.engine.refresh();self.assertEqual(self.july()['legacy_intervals'],[])
    def test_conflicts_and_counter_reset_are_explicit(self):
        self.write([meta(),count(110,'2026-07-10T01:00:00Z'),count(210,'2026-07-10T02:00:00Z'),count(120,'2026-07-10T02:00:00Z'),count(330,'2026-07-10T03:00:00Z'),count(110,'2026-07-10T04:00:00Z')]);self.engine.refresh();r=self.july()
        self.assertEqual(self.engine.status['legacy_conflicts'],1);self.assertTrue(r['legacy_intervals'][-1]['counter_reset'])
        self.assertEqual(r['totals']['total_tokens'],440)
        self.assertTrue(r['legacy_intervals'][1]['coarse_timestamp'])
    def test_unknown_start_and_invalid_values(self):
        self.write([{'type':'session_meta','payload':{'id':'old'}},count(110,'2026-07-10T01:00:00Z'),count(80,'bad')]);self.engine.refresh();r=self.july()
        self.assertTrue(r['legacy_intervals'][0]['start_unknown']);self.assertEqual(r['totals']['total_tokens'],110)
    def test_compaction_switch_does_not_claim_legacy_classification(self):
        self.write([meta(),count(110,'2026-07-10T01:00:00Z'),context(),usage('new','2026-07-10T02:00:00Z',thread='old'),{'type':'compacted','payload':{}}]);self.engine.refresh();r=self.july(include_compaction=False)
        self.assertEqual(r['totals']['total_tokens'],110);self.assertEqual(r['totals']['recorded_tokens'],0)

    def test_fixed_timestamp_archive_retains_maximum_without_summing(self):
        self.write([meta(ts='2026-07-10T01:00:00Z'),count(110,'2026-07-10T01:00:00Z'),count(330,'2026-07-10T01:00:00Z'),count(220,'2026-07-10T01:00:00Z')]);self.engine.refresh();r=self.july()
        self.assertEqual(r['totals']['estimated_tokens'],330);self.assertEqual(len(r['legacy_intervals']),1)
        self.assertTrue(r['legacy_intervals'][0]['coarse_timestamp']);self.assertTrue(r['legacy_intervals'][0]['start_unknown'])
        self.assertEqual(self.july(end='2026-07-10T01:00')['totals']['total_tokens'],0)
    def test_child_inherited_counter_is_a_baseline_not_new_usage(self):
        child=meta(ts='2026-07-10T01:00:00Z');child['payload']['parent_thread_id']='parent'
        inherited=count(1000,'2026-07-10T01:00:00.001Z');inherited['payload']['info']['last_token_usage']={'total_tokens':100}
        self.write([child,inherited,count(1110,'2026-07-10T02:00:00Z'),context(),usage('new','2026-07-10T03:00:00Z',thread='old')]);self.engine.refresh();r=self.july()
        self.assertEqual(r['totals']['estimated_tokens'],110);self.assertEqual(r['totals']['recorded_tokens'],110);self.assertEqual(r['totals']['total_tokens'],220)
    def test_fresh_child_counter_still_counts_its_initial_usage(self):
        child=meta();child['payload']['parent_thread_id']='parent'
        first=count(110,'2026-07-10T00:00:00.001Z');first['payload']['info']['last_token_usage']={'total_tokens':110}
        self.write([child,first,count(330,'2026-07-10T00:00:00.001Z')]);self.engine.refresh();r=self.july()
        self.assertEqual(r['totals']['estimated_tokens'],330)
