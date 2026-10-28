import test from 'node:test';
import assert from 'node:assert/strict';
import {cacheHitRate,cacheHitTooltipFields} from '../web/src/content/dashboard/token-series.js';
import {barChartSpec,projectBarOptions} from '../web/src/charting/bar-family.js';

test('cache hit rate divides cached input by total tokens including output',()=>{
  assert.equal(cacheHitRate(70,110),7/11);
  assert.equal(cacheHitRate(0,110),0);
  assert.equal(cacheHitRate(70,0),null);
  // An aggregate uses summed counts, not an average of model percentages.
  assert.equal(cacheHitRate(80+0,100+900),0.08);
});
test('composition tooltip inserts a serialized percent field after the total',()=>{
  const spec=barChartSpec({category:'period',value:'total',tooltipFields:[{key:'total',label:'总用量 Token'},...cacheHitTooltipFields('composition'),{key:'cached',label:'输入 · 命中缓存'}]});
  const fields=projectBarOptions(JSON.parse(JSON.stringify(spec.barOptions))).tooltipFields;
  assert.deepEqual(fields.map(f=>f.key),['total','cache_hit_rate','cached']);
  assert.equal(fields[1].format,'percent');assert.equal(fields[2].format,undefined);
  assert.deepEqual(cacheHitTooltipFields('effort'),[]);
});
