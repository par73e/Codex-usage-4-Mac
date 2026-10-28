import test from 'node:test';
import assert from 'node:assert/strict';
import {quotaPosition, quotaView, quotaMarks, quotaEventBand, retainQuotaRows, windowLabel} from '../web/src/content/dashboard/quota-view.js';
const bins = [{start_ms:100,end_ms:200},{start_ms:200,end_ms:500},{start_ms:500,end_ms:600}];
const point=(time,remaining=50)=>({timestamp_ms:time,remaining_percent:remaining,snapshot_id:String(time)});
test('exact snapshots share equal-width token bin edges, including partial bins',()=>{
  assert.equal(quotaPosition(100,bins),0);assert.equal(quotaPosition(200,bins),1/3);
  assert.equal(quotaPosition(350,bins),.5);assert.equal(quotaPosition(600,bins),1);
});
test('shared zoom preserves real timestamps, context and exclusive end',()=>{
  const w={context_before:point(50),points:[point(150),point(250),point(550)],events:[{lower_ms:150,upper_ms:250},{lower_ms:550,upper_ms:600}]};
  const v=quotaView(w,bins,[1,1]);assert.deepEqual(v.points,[point(250)]);assert.equal(v.context.timestamp_ms,150);
  assert.equal(v.start,200);assert.equal(v.end,500);assert.equal(v.events.length,1);
});
test('absence of snapshots is not converted into zero',()=>{
  const v=quotaView({context_before:point(50),points:[],events:[]},bins,[0,2]);
  assert.equal(v.points.length,0);assert.equal(v.context.remaining_percent,50);
});
test('hover thinning retains extrema and both recovery observations',()=>{
  const points=Array.from({length:1000},(_,i)=>point(100+i/10,50));points[100].remaining_percent=0;points[200].remaining_percent=100;
  const marks=quotaMarks(points,bins,50,[{lower_ms:points[321].timestamp_ms,upper_ms:points[322].timestamp_ms}]);
  for(const i of [0,100,200,321,322,999])assert.ok(marks.includes(points[i]));
  assert.ok(marks.length<50);
});
test('quota labels use recorded window lengths instead of primary assumptions',()=>{
  assert.equal(windowLabel(10080),'7 天窗口');assert.equal(windowLabel(300),'5 小时窗口');assert.equal(windowLabel(90),'90 分钟窗口');
});
test('subpixel recovery intervals retain exact duration and a separate visible cue',()=>{
  const band=quotaEventBand({lower_ms:250,upper_ms:250.01},bins,1000);
  assert.ok(band.actualWidth<1);assert.equal(band.narrow,true);assert.equal(band.cueWidth,8);
  assert.ok(band.cueStart<=band.end && band.cueStart+band.cueWidth>=band.end);
});
test('recovery cues stay inside the plot while wide intervals retain their scale',()=>{
  for(const event of [{lower_ms:99,upper_ms:100.01},{lower_ms:599.98,upper_ms:599.99}]){
    const band=quotaEventBand(event,bins,300);assert.ok(band.cueStart>=0);assert.ok(band.cueStart+band.cueWidth<=300);
  }
  const band=quotaEventBand({lower_ms:100,upper_ms:200},bins,300);
  assert.equal(band.actualWidth,100);assert.equal(band.narrow,false);
});
test('polling identical quota records preserves row and collection identity for hover',()=>{
  const before=[{id:'restore',lower_ms:100,upper_ms:200,remaining_after:100}];
  assert.equal(retainQuotaRows(before,JSON.parse(JSON.stringify(before)),'id'),before);
});
test('appending new snapshots retains the hovered historical row',()=>{
  const before=[point(100),point(200)];
  const next=retainQuotaRows(before,[...JSON.parse(JSON.stringify(before)),point(300)],'snapshot_id');
  assert.equal(next[0],before[0]);assert.equal(next[1],before[1]);assert.equal(next.length,3);
});
test('changed, removed and reordered records do not retain stale evidence',()=>{
  const before=[point(100),point(200)];
  const changed=retainQuotaRows(before,[point(100,75)],'snapshot_id');
  assert.notEqual(changed[0],before[0]);assert.equal(changed[0].remaining_percent,75);assert.equal(changed.length,1);
  const reordered=retainQuotaRows(before,[point(200),point(100)],'snapshot_id');
  assert.equal(reordered[0],before[1]);assert.equal(reordered[1],before[0]);
  const old=[{id:'one',value:1,removed:2}];
  assert.equal(retainQuotaRows(old,[{id:'one',value:1}],'id')[0].removed,undefined);
});
