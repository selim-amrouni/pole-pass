'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const PP = require('../web/predicates.js');

const rec = (o) => Object.assign({ id: 'x', util: true, lean: 'none', xarm: 'none_visible', veg: 'none', xfmr: false, att: 1, shown: { year: 2023, ts: 1 } }, o);

test('condition issue is lean moderate/severe, crossarm damaged, or vegetation touching', () => {
  assert.deepEqual(PP.conditionFlags(rec({ lean: 'slight' })), []);
  assert.deepEqual(PP.conditionFlags(rec({ lean: 'moderate' })), ['lean']);
  assert.deepEqual(PP.conditionFlags(rec({ xarm: 'damaged' })), ['crossarm']);
  assert.deepEqual(PP.conditionFlags(rec({ veg: 'near' })), []);
  assert.deepEqual(PP.conditionFlags(rec({ veg: 'touching', lean: 'severe' })), ['lean', 'vegetation']);
  assert.equal(PP.hasConditionIssue(rec({ att: 5, xfmr: true })), false, 'attachments and transformers are not condition issues');
});

test('3+ attachments requires a utility pole and an integer count', () => {
  assert.equal(PP.attachments3(rec({ att: 3 })), true);
  assert.equal(PP.attachments3(rec({ att: 2 })), false);
  assert.equal(PP.attachments3(rec({ att: null })), false);
  assert.equal(PP.attachments3(rec({ att: 4, util: false })), false);
});

test('unclear stays distinct from none', () => {
  assert.equal(PP.conditionUnclear(rec({ lean: 'unclear', xarm: 'unclear', veg: 'unclear' })), true);
  assert.equal(PP.conditionUnclear(rec({ lean: 'unclear' })), false);
  assert.equal(PP.hasConditionIssue(rec({ lean: 'unclear' })), false);
});

test('filters restrict to utility poles by default and to other objects on request', () => {
  const rows = [rec({ id: 'a' }), rec({ id: 'b', util: false }), rec({ id: 'c', lean: 'severe', shown: { year: 2016, ts: 0 } })];
  assert.deepEqual(PP.applyFilters(rows, { flag: 'all' }).map(r => r.id), ['a', 'c']);
  assert.deepEqual(PP.applyFilters(rows, { flag: 'all', other: true }).map(r => r.id), ['b']);
  assert.deepEqual(PP.applyFilters(rows, { flag: 'lean' }).map(r => r.id), ['c']);
  assert.deepEqual(PP.applyFilters(rows, { flag: 'all', yearMin: 2020 }).map(r => r.id), ['a']);
  assert.deepEqual(PP.applyFilters(rows, { flag: 'all', recent: 2022 }).map(r => r.id), ['a']);
  assert.deepEqual(PP.applyFilters([rec({ shown: { year: null } })], { flag: 'all', yearMin: 2000 }), [], 'undated records drop out of a date range');
});

test('summary counts come from the records and use the utility population', () => {
  const s = PP.summary([rec({ lean: 'moderate' }), rec({ att: 3 }), rec({ util: false, lean: 'severe' }), rec({ shown: { year: null } })]);
  assert.equal(s.utility, 3); assert.equal(s.other, 1); assert.equal(s.conditionIssues, 1); assert.equal(s.attachments3, 1);
  assert.equal(s.undated, 1); assert.equal(s.yearMin, 2023);
});

test('published data.js flags match the shared predicate (dedupe.py and predicates.js agree)', (t) => {
  const p = path.join(__dirname, '..', 'out', 'greenpoint-brooklyn-new-york', 'data.js');
  if (!fs.existsSync(p)) return t.skip('no built bundle');
  const win = {}; new Function('window', fs.readFileSync(p, 'utf8'))(win);
  const D = win.POLE_DATA;
  let mismatch = 0;
  for (const r of D.records) if (JSON.stringify(PP.conditionFlags(r)) !== JSON.stringify(r.flags)) mismatch++;
  assert.equal(mismatch, 0);
  const s = PP.summary(D.records);
  assert.equal(s.records, D.records.length);
  assert.equal(s.utility + s.other, s.records);
});
