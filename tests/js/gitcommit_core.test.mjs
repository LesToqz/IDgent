import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const C = require('../../idgent/gitcommit_web/commit_core.js');

process.env.TZ = 'America/New_York';

test('commit dates use viewer days and survive daylight-saving day lengths', () => {
  const now = new Date('2026-03-09T01:00:00-04:00');
  assert.equal(C.dateLabel(new Date('2026-03-09T00:58:00-04:00') / 1000, now), 'Today at 12:58 AM');
  assert.equal(C.dateLabel(new Date('2026-03-08T00:58:00-05:00') / 1000, now), 'Yesterday at 12:58 AM');
  assert.equal(C.dateLabel(new Date('2026-03-07T20:58:00-05:00') / 1000, now), 'Mar 7, 2026 at 8:58 PM');
  assert.equal(C.fullDate(Date.parse('2026-10-19T20:58:00+05:30') / 1000, 330), '2026-10-19 20:58:00 +05:30');
  assert.equal(C.fullDate(0, -240), '1969-12-31 20:00:00 -04:00');
});

test('intra-line ranges trim common prefix and suffix using Python character offsets', () => {
  assert.deepEqual(C.changedRange('return False', 'return True'), [[7, 11], [7, 10]]);
  assert.deepEqual(C.changedRange('😀 old café', '😀 new café'), [[2, 5], [2, 5]]);
  assert.deepEqual(C.changedRange('same', 'same'), [[4, 4], [4, 4]]);
  assert.deepEqual(C.changedRange('', 'new'), [[0, 0], [0, 3]]);
  const lines = [{t: '-', text: 'x = 1'}, {t: '-', text: 'y = 2'}, {t: '+', text: 'x = 10'}, {t: '+', text: 'y = 20'}];
  assert.deepEqual(C.pairs(lines).get(2), [5, 6]);
});

test('hunk gaps and context map numbers across insertions and removals', () => {
  const hunks = [
    {old_start: 10, old_lines: 6, new_start: 10, new_lines: 8},
    {old_start: 100, old_lines: 8, new_start: 102, new_lines: 6},
  ];
  assert.deepEqual(C.gaps(hunks), [
    {before: 0, start: 1, end: 9, offset: 0},
    {before: 1, start: 18, end: 101, offset: -2},
    {before: 2, start: 108, end: null, offset: 0},
  ]);
  assert.equal(25 + C.oldOffset(hunks[0]), 23);
  assert.deepEqual(C.gaps([]), []);
  assert.deepEqual(C.gaps([{old_start: 0, old_lines: 0, new_start: 1, new_lines: 3}]),
    [{before: 1, start: 4, end: null, offset: -3}]);
});

test('context requests use 20-line steps and fill gaps of at most 40', () => {
  assert.deepEqual(C.requestRange({start: 1, end: 40}, 'up'), [1, 40]);
  assert.deepEqual(C.requestRange({start: 1, end: 41}, 'up'), [22, 41]);
  assert.deepEqual(C.requestRange({start: 1, end: 41}, 'down'), [1, 20]);
  assert.deepEqual(C.requestRange({start: 108, end: null}, 'down'), [108, 127]);
});
