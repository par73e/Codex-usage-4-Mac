import test from 'node:test';
import assert from 'node:assert/strict';
import {buildChartView, MAX_PLOT_BUCKETS} from '../web/src/content/dashboard/chart-view.js';
import {quotaPosition} from '../web/src/content/dashboard/quota-view.js';
import {barChartSpec,projectBarOptions} from '../web/src/charting/bar-family.js';

function fixture(count) {
  const base=Date.parse('2026-07-10T00:00:00Z'),hour=3600000;
  const bins=Array.from({length:count},(_,i)=>({id:String(i),start_ms:base+i*hour,end_ms:base+(i+1)*hour,
    start:new Date(base+i*hour).toISOString(),end:new Date(base+(i+1)*hour).toISOString(),label:`07/10 ${i%24}:00`,partial:false}));
  const buckets=bins.map((bin,i)=>({id:bin.id,tokens:i%7?110:0,responses:i%7?1:0,efforts:i%7?{high:110}:{},cached:i%7?70:0,uncached:i%7?30:0,cache_unknown:0,output:i%7?10:0}));
  return {bins,models:[{name:'a',tokens:buckets.reduce((n,b)=>n+b.tokens,0),buckets}],quota:{windows:[]},evidenceRows:[{response_id:'evidence'}]};
}
test('multi-month hourly overview has a bounded number of drawn buckets',()=>{
  const source=fixture(2616),view=buildChartView(source,[0,2615]);
  assert.ok(view.bins.length<=MAX_PLOT_BUCKETS);assert.equal(view.overviewStride,24);
  assert.equal(view.bins[0].start,source.bins[0].start);assert.equal(view.bins.at(-1).end,source.bins.at(-1).end);
  assert.equal(view.quota,source.quota);assert.equal(view.evidenceRows,source.evidenceRows);
});
test('overview conserves tokens, responses, cache composition and every effort',()=>{
  const source=fixture(2631),before=JSON.stringify(source),view=buildChartView(source,[0,2630]);
  for(const field of ['tokens','responses','cached','uncached','cache_unknown','output']){
    assert.equal(view.models[0].buckets.reduce((n,b)=>n+b[field],0),source.models[0].buckets.reduce((n,b)=>n+b[field],0));
  }
  for(const b of view.models[0].buckets){assert.equal(b.tokens,Object.values(b.efforts).reduce((a,v)=>a+v,0));assert.equal(b.tokens,b.cached+b.uncached+b.cache_unknown+b.output);}
  assert.equal(view.models[0].tokens,source.models[0].tokens);assert.equal(JSON.stringify(source),before);
});
test('zooming into a short range restores exact original hourly buckets',()=>{
  const source=fixture(2616),view=buildChartView(source,[2500,2550]);
  assert.equal(view.overviewStride,1);assert.equal(view.bins.length,51);
  assert.equal(view.bins[0],source.bins[2500]);assert.equal(view.models[0].buckets.at(-1),source.models[0].buckets[2550]);
  assert.equal(view.models[0].tokens,source.models[0].tokens);
});
test('grouped token bins and exact quota timestamps share plot edges',()=>{
  const source=fixture(2616),view=buildChartView(source,[0,2615]);
  assert.equal(quotaPosition(source.bins[24].start_ms,view.bins),1/view.bins.length);
  assert.equal(quotaPosition(source.bins.at(-1).end_ms,view.bins),1);
});
test('daily or short ranges retain original records without merging',()=>{
  const source=fixture(109),view=buildChartView(source,[0,108]);
  assert.equal(view.overviewStride,1);assert.equal(view.bins[99],source.bins[99]);assert.equal(view.models[0].buckets[99],source.models[0].buckets[99]);
});
test('full aggregate range labels survive chart serialization',()=>{
  const spec=barChartSpec({category:'period',value:'total',tooltipLabelField:'range'});
  assert.equal(projectBarOptions(JSON.parse(JSON.stringify(spec.barOptions))).tooltipLabelField,'range');
});
test('all-model overview conserves exact and interval-estimated token sums',()=>{
  const source=fixture(2616);
  source.total_buckets=source.bins.map((bin,i)=>({id:bin.id,tokens:i+20,recorded:i,estimated:20,responses:1}));
  source.legacy_intervals=[{interval_id:'old'}];
  const view=buildChartView(source,[0,2615]);
  for(const field of ['tokens','recorded','estimated','responses']){
    assert.equal(view.total_buckets.reduce((sum,b)=>sum+b[field],0),source.total_buckets.reduce((sum,b)=>sum+b[field],0));
  }
  assert.equal(view.legacy_intervals,source.legacy_intervals);
  for(const b of view.total_buckets)assert.equal(b.tokens,b.recorded+b.estimated);
  assert.equal(view.total_buckets.length,view.bins.length);
  const zoomed=buildChartView(source,[20,22]);assert.equal(zoomed.total_buckets.length,3);assert.equal(zoomed.total_buckets[0].tokens,40);
});
