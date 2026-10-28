import unittest,tempfile,json,hashlib
from unittest.mock import patch
from collections import Counter
from pathlib import Path
from datetime import datetime,timezone
from backend.engine import Engine,QueryError,make_bins
from backend.legacy_reader import parse_minute,parse_zone

def context(turn='t1',model='gpt-test',effort='high'):
    return {'type':'turn_context','payload':{'turn_id':turn,'model':model,'effort':effort}}
def usage(rid='r1',ts='2026-10-22T17:30:00Z',turn='t1',thread='chat1',tokens=100,cache=70,output=10):
    return {'type':'token_usage_record','timestamp':ts,'payload':{'response_id':rid,'thread_id':thread,'session_id':'parent','turn_id':turn,'usage':{'input_tokens':tokens,'cached_input_tokens':cache,'output_tokens':output,'reasoning_output_tokens':2,'total_tokens':tokens+output}}}

class EngineTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/'sessions').mkdir();(self.root/'archived_sessions').mkdir()
        self.engine=Engine(self.root,self.root/'index.sqlite')
    def write(self,events,name='one.jsonl',folder='sessions'):
        p=self.root/folder/name;p.write_text(''.join(json.dumps(x)+'\n' for x in events));return p
    def request(self,**kwargs):
        return {'start':'2026-10-23T01:30','end':'2026-10-26T12:00','timezone':'Asia/Shanghai','granularity':'6h','sessions':{'mode':'all','ids':[]},**kwargs}
    def query(self,**kwargs):return self.engine.query(self.request(**kwargs))
    def test_boundaries_and_token_partition(self):
        self.write([context(),usage('before','2026-10-22T17:29:59.999Z'),usage(),usage('last','2026-10-26T03:59:59.999Z',tokens=200),usage('end','2026-10-26T04:00:00Z')]);self.engine.refresh();r=self.query()
        self.assertEqual(r['response_count'],2);self.assertEqual(r['totals']['total_tokens'],320)
        self.assertEqual(r['bins'][0]['start'],'2026-10-23T01:30:00+08:00');self.assertTrue(r['bins'][0]['partial'])
        for b in r['models'][0]['buckets']:self.assertEqual(b['tokens'],sum(b['efforts'].values()));self.assertEqual(b['tokens'],b['cached']+b['uncached']+b['cache_unknown']+b['output'])
    def test_session_eligibility_all_explicit_and_empty(self):
        self.write([context(),usage(thread='a'),usage('r2',thread='b'),usage('old','2026-10-20T00:00:00Z',thread='old')]);self.engine.refresh()
        self.assertEqual({x['id'] for x in self.query()['options']},{'a','b'})
        r=self.query(sessions={'mode':'explicit','ids':['a','old']});self.assertEqual(r['selected_ids'],['a']);self.assertEqual(r['removed_ids'],['old']);self.assertEqual(len(r['options']),2)
        self.assertEqual(self.query(sessions={'mode':'explicit','ids':[]})['models'],[])
    def test_models_and_effort_switching(self):
        self.write([context(),usage(),{'type':'response_item','payload':{'type':'configuration_update','reasoning':{'effort':'low'}}},usage('r2'),context('t2','other','medium'),usage('r3',turn='t2')]);self.engine.refresh();r=self.query();models={m['name']:m for m in r['models']}
        self.assertEqual(models['gpt-test']['efforts'],{'high':110,'low':110});self.assertEqual(models['other']['tokens'],110)
    def test_unknown_is_preserved_not_assigned_latest_context(self):
        self.write([usage(),context('t2','new-model','low')]);self.engine.refresh();r=self.query();self.assertEqual(r['models'][0]['name'],'unknown');self.assertEqual(r['models'][0]['efforts'],{'unknown':110})
    def test_duplicate_across_files_and_same_file(self):
        events=[context(),usage(),usage()];self.write(events);self.write(events,'copy.jsonl','archived_sessions');self.engine.refresh();self.assertEqual(self.query()['response_count'],1)
    def test_conflicting_duplicate_same_file_excluded(self):
        self.write([context(),usage(),usage(tokens=150)]);self.engine.refresh();self.assertEqual(self.query()['response_count'],0);self.assertEqual(self.engine.status['conflicting_responses'],1)
    def test_conflicting_duplicate_across_files_excluded(self):
        self.write([context(),usage()]);self.write([context(),usage(tokens=150)],'copy.jsonl');self.engine.refresh();self.assertEqual(self.query()['response_count'],0)
    def test_compaction_controls_eligibility_and_total(self):
        self.write([context(),usage(thread='compact-only'),{'type':'compacted','payload':{}},usage('normal',thread='normal')]);self.engine.refresh();r=self.query(include_compaction=False)
        self.assertEqual(r['totals']['total_tokens'],110);self.assertEqual([s['id'] for s in r['options']],['normal'])
    def test_compaction_marker_in_next_incremental_scan(self):
        p=self.write([context(),usage()]);self.engine.refresh()
        with p.open('a') as f:f.write(json.dumps({'type':'compacted','payload':{}})+'\n')
        self.engine.refresh();self.assertEqual(self.query(include_compaction=False)['response_count'],0)
    def test_incremental_and_partial_line(self):
        p=self.write([context(),usage()]);self.engine.refresh()
        with p.open('a') as f:f.write(json.dumps(usage('next'))[:-1])
        self.engine.refresh();self.assertEqual(self.query()['response_count'],1)
        with p.open('a') as f:f.write('}\n')
        self.engine.refresh();self.assertEqual(self.query()['response_count'],2);self.engine.refresh();self.assertEqual(self.query()['response_count'],2)
    def test_truncate_replace_and_remove(self):
        p=self.write([context(),usage(),usage('next')]);self.engine.refresh();p.write_text(json.dumps(context())+'\n'+json.dumps(usage('replacement'))+'\n');self.engine.refresh();self.assertEqual(self.query()['response_count'],1)
        p.unlink();self.write([context(),usage('new')],'new.jsonl');self.engine.refresh();self.assertEqual([r['response_id'] for r in self.engine.rows],['new'])
    def test_read_only_source_and_missing_cache_breakdown(self):
        row=usage();row['payload']['usage'].pop('cached_input_tokens');p=self.write([context(),row]);before=hashlib.sha256(p.read_bytes()).hexdigest();self.engine.refresh();r=self.query()
        self.assertEqual(before,hashlib.sha256(p.read_bytes()).hexdigest());self.assertEqual(sum(b['cache_unknown'] for b in r['models'][0]['buckets']),100)
    def test_granularities_and_scales_conserve_totals(self):
        self.write([context(),usage(),usage('next','2026-10-25T18:00:00Z')]);self.engine.refresh()
        for gran in ['1h','6h','12h','day']:
            r=self.query(granularity=gran);self.assertEqual(r['totals']['total_tokens'],220);self.assertEqual(sum(b['tokens'] for b in r['models'][0]['buckets']),220)
    def test_bucket_allocation_scales_with_models_not_responses(self):
        events=[context(),context('t2','other','medium')]
        events += [usage('r'+str(i),turn='t1' if i%2 else 't2') for i in range(200)]
        self.write(events);self.engine.refresh()
        with patch('backend.engine.Counter',wraps=Counter) as counter:
            result=self.query(granularity='1h')
        self.assertEqual(result['response_count'],200)
        self.assertEqual(result['totals']['total_tokens'],22000)
        self.assertLessEqual(counter.call_count,len(result['models'])*(len(result['bins'])+2)+2)
    def test_dst_25_hour_day_and_duplicate_hour(self):
        zone=parse_zone('America/New_York');start=parse_minute('2026-11-01 00:00',zone);end=parse_minute('2026-11-02 00:00',zone)
        bins=make_bins(start,end,zone,'1h');self.assertEqual(len(bins),25);one=[b for b in bins if b['label']=='11/01 01:00'];self.assertEqual(len(one),2);self.assertNotEqual(one[0]['id'],one[1]['id'])
        self.assertEqual((make_bins(start,end,zone,'day')[0]['end_ms']-make_bins(start,end,zone,'day')[0]['start_ms'])/3600000,25)
    def test_invalid_ranges_and_generation(self):
        self.write([context(),usage()]);self.engine.refresh()
        for changes in [{'end':'2026-10-23T01:00'},{'timezone':'bad/zone'},{'granularity':'2h'},{'generation':'old'}]:
            with self.assertRaises(ValueError):self.query(**changes)
    def test_none_not_unknown(self):
        self.write([context(effort='none'),usage(),usage('missing',turn='no-context')]);self.engine.refresh();efforts={k for m in self.query()['models'] for k in m['efforts']};self.assertEqual(efforts,{'none','unknown'})

if __name__=='__main__':unittest.main()
