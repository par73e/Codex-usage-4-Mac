import test from 'node:test';
import assert from 'node:assert/strict';
import {totalBreakdown, tokenSeries, orderedTooltipSeries} from '../web/src/content/dashboard/token-series.js';
import {buildChartView} from '../web/src/content/dashboard/chart-view.js';
function fixture(){
  const buckets=[{id:'a',tokens:110,responses:1,efforts:{medium:110},cached:70,uncached:30,output:10,cache_unknown:0},{id:'b',tokens:55,responses:1,efforts:{high:55},cached:0,uncached:0,output:5,cache_unknown:50}];
  return {bins:[{id:'a',start_ms:0,end_ms:100},{id:'b',start_ms:100,end_ms:200}],models:[{name:'one',efforts:{medium:110,high:55},buckets,response_ids:[]},{name:'two',efforts:{medium:110,high:55},buckets,response_ids:[]}],total_buckets:[{id:'a',tokens:240,recorded:220,estimated:20,responses:2},{id:'b',tokens:120,recorded:110,estimated:10,responses:2}],totals:{total_tokens:360,recorded_tokens:330,estimated_tokens:30}};
}
for(const mode of ['effort','composition'])test(`all-model ${mode} stacks conserve totals and keep legacy estimates separate`,()=>{
  const result=fixture(),before=JSON.stringify(result),view=totalBreakdown(result,mode);
  for(const bucket of view.buckets){
    const sum=view.series.reduce((n,s)=>n+(s.key==='estimated'?bucket.estimated:mode==='effort'?bucket.efforts[s.key]||0:bucket[s.key]||0),0);
    assert.equal(sum,bucket.tokens);
  }
  assert.equal(view.efforts.medium,220);assert.equal(view.efforts.high,110);
  assert.equal(view.series.at(-1).key,'estimated');assert.equal(JSON.stringify(result),before);
  const shared=tokenSeries(mode,result.models[0].efforts,result.models[0].buckets);
  for(const s of shared)assert.equal(view.series.find(x=>x.key===s.key).color,s.color);
  assert.equal(orderedTooltipSeries(mode,view.series)[0].key,mode==='effort'?'medium':'cached');
});
test('overview and zoom keep aggregate breakdown aligned with displayed buckets',()=>{
  const result=fixture(),overview=totalBreakdown(buildChartView(result,[0,1],1),'composition');
  assert.equal(overview.buckets.length,1);assert.equal(overview.buckets[0].tokens,360);assert.equal(overview.buckets[0].cached,140);
  const zoom=totalBreakdown(buildChartView(result,[1,1]),'effort');assert.equal(zoom.buckets[0].efforts.high,110);assert.equal(zoom.buckets[0].tokens,120);
});
test('legacy-only and empty scopes keep unknown usage honest',()=>{
  const result=fixture();result.models=[];result.total_buckets=[{tokens:20,recorded:0,estimated:20}];result.totals={total_tokens:20,estimated_tokens:20};
  assert.deepEqual(totalBreakdown(result,'effort').series.map(s=>s.key),['estimated']);
  result.total_buckets=[];result.totals={total_tokens:0,estimated_tokens:0};assert.deepEqual(totalBreakdown(result,'effort').series,[]);
});
