import test from 'node:test';
import assert from 'node:assert/strict';
import { localMinute, presetRange, rangeError, rememberRange, restoreHistory, validMinute, moveEndpointDay } from '../web/src/content/dashboard/date-range.js';

const now = Date.parse('2026-10-26T02:36:40Z');
const fixed = { start:'2026-10-23T01:37', end:'2026-10-26T12:04', timezone:'Asia/Shanghai' };
test('arbitrary minutes and leap years validate without browser timezone coercion', () => {
  assert.equal(rangeError(fixed), '');
  assert.equal(validMinute('2024-02-29T23:59'), true);
  for (const value of ['2026-02-29T01:30','2026-04-31T00:00','2026-01-01T24:00','2026-01-01T12:60','0000-01-01T00:00']) assert.equal(validMinute(value), false);
});
test('empty, reversed and same-minute ranges are rejected', () => {
  for (const value of [{...fixed,start:''},{...fixed,end:fixed.start},{...fixed,start:fixed.end,end:fixed.start},{...fixed,timezone:'wrong/zone'}]) assert.notEqual(rangeError(value), '');
});
test('selected zone controls wall-clock fields and year rollover', () => {
  assert.equal(localMinute(new Date('2026-12-31T16:05:00Z'),'Asia/Shanghai'),'2027-01-01T00:05');
  assert.equal(localMinute(new Date(now),'UTC'),'2026-10-26T02:36');
});
test('last six hours uses exact duration with exclusive current-minute end', () => {
  assert.deepEqual(presetRange('last6h','Asia/Shanghai',now), {start:'2026-10-26T04:37',end:'2026-10-26T10:37',timezone:'Asia/Shanghai'});
});
test('relative window moves both ends together as now advances', () => {
  const first = presetRange('last1h','UTC',now), next = presetRange('last1h','UTC',now+120000);
  assert.equal(Date.parse(next.start+':00Z')-Date.parse(first.start+':00Z'),120000);
  assert.equal(Date.parse(next.end+':00Z')-Date.parse(first.end+':00Z'),120000);
});
test('today and yesterday follow selected-zone calendar boundaries', () => {
  const at = Date.parse('2026-01-01T01:37:45Z');
  assert.deepEqual(presetRange('yesterday','Asia/Shanghai',at), {start:'2025-12-31T00:00',end:'2026-01-01T00:00',timezone:'Asia/Shanghai'});
  assert.equal(presetRange('today','Asia/Shanghai',at).start,'2026-01-01T00:00');
});
test('today at 23:59 includes the last minute without moving the start to tomorrow', () => {
  const result = presetRange('today','Asia/Shanghai',Date.parse('2026-10-26T15:59:40Z'));
  assert.equal(result.start,'2026-10-26T00:00');assert.equal(result.end,'2026-10-27T00:00');
});
test('history retains exact minutes and zone, deduplicates, and limits to five', () => {
  let history=rememberRange([],fixed);history=rememberRange(history,{...fixed});assert.equal(history.length,1);
  for(let i=1;i<7;i++)history=rememberRange(history,{...fixed,start:`2026-10-${String(i).padStart(2,'0')}T01:37`});
  assert.equal(history.length,5);assert.equal(history[0].start,'2026-10-06T01:37');assert.equal(history[0].end,fixed.end);
});
test('relative history deduplicates by preset and timezone rather than stale timestamps', () => {
  const one={...fixed,followNow:true,relativeKey:'last6h'};
  assert.equal(rememberRange([one],{...one,start:'2026-10-24T01:37'}).length,1);
  assert.equal(rememberRange([one],{...one,timezone:'UTC'}).length,2);
});
test('invalid stored history is ignored', () => {
  assert.deepEqual(restoreHistory(null),[]);
  assert.deepEqual(restoreHistory([null,{},fixed,{...fixed,relativeKey:'unknown'}]),[fixed]);
});
test('dragging either endpoint preserves its hours and arbitrary minutes', () => {
  const range=[fixed.start,fixed.end];
  assert.deepEqual(moveEndpointDay(range,1,'2026-10-25'),[fixed.start,'2026-10-25T12:04']);
  assert.deepEqual(moveEndpointDay(range,0,'2026-10-22'),['2026-10-22T01:37',fixed.end]);
  assert.deepEqual(range,[fixed.start,fixed.end]);
});
test('dragging across a month or year preserves the other endpoint', () => {
  assert.deepEqual(moveEndpointDay([fixed.start,fixed.end],1,'2027-01-03'),[fixed.start,'2027-01-03T12:04']);
});
test('endpoints cannot cross or land on invalid dates', () => {
  assert.equal(moveEndpointDay([fixed.start,fixed.end],1,'2026-10-22'),null);
  assert.equal(moveEndpointDay([fixed.start,fixed.end],0,'2026-10-27'),null);
  assert.equal(moveEndpointDay([fixed.start,fixed.end],1,'2026-02-30'),null);
});
test('same-day endpoints remain independently movable when times are ordered', () => {
  assert.deepEqual(moveEndpointDay([fixed.start,fixed.end],1,'2026-10-23'),[fixed.start,'2026-10-23T12:04']);
});
