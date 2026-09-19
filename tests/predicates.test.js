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

test('a double pole is a condition flag, and it comes from the pair not from this record\'s own fields', () => {
  const dbl = { pair_id: 'MH-abc12345', confidence: 0.75, reason: 'old pole beside its replacement' };
  assert.deepEqual(PP.conditionFlags(rec({ dbl })), ['double']);
  assert.equal(PP.isDoublePole(rec({ dbl })), true);
  // No dbl key at all means the double-pole pass never ran for this town, which is NOT the same as
  // "checked and found none". Either way it is not a flag on this record.
  assert.deepEqual(PP.conditionFlags(rec({})), []);
  assert.equal(PP.isDoublePole(rec({})), false);
  assert.equal(PP.isDoublePole(rec({ dbl: {} })), false, 'a dbl object without a pair id is not a double');
  assert.deepEqual(PP.conditionFlags(rec({ dbl, lean: 'severe' })), ['double', 'lean']);
  assert.equal(PP.FILTERS.double(rec({ dbl })), true);
  assert.equal(PP.FILTERS.double(rec({})), false);
  assert.ok(PP.reviewableFlags(rec({ dbl })).includes('double'), 'a double pole is reviewable like any other flag');
  assert.equal(PP.flagSupport(rec({ dbl }), 'double'), null, 'no per-frame support: the evidence is the pair photo');
});

test('slight lean is a watch item, one tier below a condition issue', () => {
  assert.deepEqual(PP.warningFlags(rec({ lean: 'slight' })), ['lean_slight']);
  assert.equal(PP.hasConditionIssue(rec({ lean: 'slight' })), false);
  assert.deepEqual(PP.warningFlags(rec({ lean: 'moderate' })), [], 'moderate is an issue, not a watch item');
  assert.deepEqual(PP.warningFlags(rec({ lean: 'none' })), []);
  assert.deepEqual(PP.applyFilters([rec({ id: 'a', lean: 'slight' }), rec({ id: 'b', lean: 'severe' })], { flag: 'lean_slight' }).map(r => r.id), ['a']);
  assert.equal(PP.summary([rec({ lean: 'slight' }), rec({ lean: 'slight', util: false }), rec({ lean: 'moderate' })]).warnings, 1);
  const sorted = [rec({ id: 'w', lean: 'slight' }), rec({ id: 'n' }), rec({ id: 'i', lean: 'severe' })].sort(PP.SORTS.flags_desc).map(r => r.id);
  assert.deepEqual(sorted, ['i', 'w', 'n'], 'issues first, then watch items');
});

test('notInOsm: utility record with no OSM pole within the 15 m headline radius; absent osm field counts as not in OSM', () => {
  const near = rec({ id: 'near', osm: 6.2 }), edge = rec({ id: 'edge', osm: 15 }), far = rec({ id: 'far', osm: 22 }), none = rec({ id: 'none', osm: null }), unchecked = rec({ id: 'u' });
  assert.equal(PP.notInOsm(near), false); assert.equal(PP.notInOsm(edge), false);
  assert.equal(PP.notInOsm(far), true); assert.equal(PP.notInOsm(none), true); assert.equal(PP.notInOsm(unchecked), true);
  assert.equal(PP.notInOsm(rec({ util: false, osm: null })), false, 'other objects are never counted');
  assert.deepEqual(PP.applyFilters([near, far, none], { flag: 'all', osm: true }).map(r => r.id), ['far', 'none']);
  assert.deepEqual(PP.applyFilters([near, far, none], { flag: 'all' }).map(r => r.id), ['near', 'far', 'none'], 'off by default');
  assert.equal(PP.summary([near, far, none, rec({ util: false, osm: null })]).notInOsm, 2);
});

test('spansYears needs two distinct photo years; the filter keeps only those records', () => {
  const two = rec({ id: 'two', frames: [{ year: 2019 }, { year: 2019 }, { year: 2024 }] }), one = rec({ id: 'one', frames: [{ year: 2024 }, { year: null }] });
  assert.deepEqual(PP.frameYears(two), [2019, 2024]);
  assert.equal(PP.spansYears(two), true); assert.equal(PP.spansYears(one), false); assert.equal(PP.spansYears(rec({})), false);
  assert.deepEqual(PP.applyFilters([two, one], { flag: 'all', years: true }).map(r => r.id), ['two']);
  assert.equal(PP.summary([two, one, rec({ util: false, frames: [{ year: 1 }, { year: 2 }] })]).spansYears, 1);
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
  let warnMismatch = 0;
  for (const r of D.records) if (JSON.stringify(PP.warningFlags(r)) !== JSON.stringify(r.warn)) warnMismatch++;
  assert.equal(warnMismatch, 0, 'warning_flags() in dedupe.py and warningFlags() agree');
  const s = PP.summary(D.records);
  assert.equal(s.records, D.records.length);
  assert.equal(s.utility + s.other, s.records);
});

const MS_MONTH = 365.25 / 12 * 86400000;

test('monthsSince, isRecent, and ageLabel derive age from a photo capture date at view time', () => {
  const now = Date.UTC(2026, 5, 15);
  assert.equal(PP.monthsSince(null, now), null);
  assert.equal(PP.monthsSince(0, now), null);
  assert.equal(PP.monthsSince(now, now), 0);
  assert.equal(PP.monthsSince(now - 5.4 * MS_MONTH, now), 5);
  assert.equal(PP.ageLabel(null, now), 'date unknown');
  assert.equal(PP.ageLabel(0, now), 'date unknown');
  assert.equal(PP.ageLabel(now - 10 * 86400000, now), 'this month');
  assert.equal(PP.ageLabel(now - 5.4 * MS_MONTH, now), '5 mo ago');
  assert.equal(PP.ageLabel(now - 12.4 * MS_MONTH, now), '1 yr ago');
  assert.equal(PP.ageLabel(now - 21.4 * MS_MONTH, now), '1 yr 9 mo ago');
  assert.equal(PP.RECENT_MONTHS, 24);
  assert.equal(PP.isRecent(rec({ shown: { ts: now } }), now), true);
  assert.equal(PP.isRecent(rec({ shown: { ts: now - 23.9 * MS_MONTH } }), now), true);
  assert.equal(PP.isRecent(rec({ shown: { ts: now - 24.4 * MS_MONTH } }), now), false, '24 months and older is not recent');
  assert.equal(PP.isRecent(rec({ shown: { ts: null } }), now), false, 'date unknown is not recent');
});

test('flagSupport reports which photos back a flag, distinct drives, and what the latest photo shows', () => {
  const r = rec({ frames: [{ lean: 'severe', ts: 100, seq: 1 }, { lean: 'none', ts: 200, seq: 1 }, { lean: 'moderate', ts: 300, seq: 2 }] });
  const s = PP.flagSupport(r, 'lean');
  assert.equal(s.n, 3);
  assert.equal(s.supporting.length, 2);
  assert.equal(s.drives, 2, 'distinct frame.seq among supporting frames');
  assert.equal(s.latest.ts, 300);
  assert.equal(s.latestStatus, 'supports');

  const absentLatest = rec({ frames: [{ lean: 'severe', ts: 100, seq: 1 }, { lean: 'none', ts: 200, seq: 1 }] });
  assert.equal(PP.flagSupport(absentLatest, 'lean').latestStatus, 'absent');

  const unclearLatest = rec({ frames: [{ lean: 'severe', ts: 100, seq: 1 }, { lean: 'unclear', ts: 200, seq: 1 }] });
  assert.equal(PP.flagSupport(unclearLatest, 'lean').latestStatus, 'unclear');

  const noDated = rec({ frames: [{ lean: 'severe', seq: 1 }] });
  assert.equal(PP.flagSupport(noDated, 'lean').latest, null, 'no dated frame means no latest');
  assert.equal(PP.flagSupport(noDated, 'lean').latestStatus, null);

  assert.equal(PP.flagSupport(rec({}), 'nope'), null, 'unknown key');

  const veg = rec({ frames: [{ veg: 'touching', ts: 1, seq: 1 }, { veg: 'near', ts: 2, seq: 2 }] });
  assert.equal(PP.flagSupport(veg, 'vegetation').supporting.length, 1);
  const att = rec({ frames: [{ att: 3, ts: 1, seq: 1 }, { att: 2, ts: 2, seq: 1 }] });
  assert.equal(PP.flagSupport(att, 'att3').supporting.length, 1);
});

test('reviewableFlags lists condition, watch, and equipment flags for utility poles only', () => {
  assert.deepEqual(PP.reviewableFlags(rec({ util: false, lean: 'severe' })), []);
  assert.deepEqual(PP.reviewableFlags(rec({ lean: 'slight' })), ['lean_slight']);
  assert.deepEqual(PP.reviewableFlags(rec({ att: 3 })), ['att3']);
  assert.deepEqual(PP.reviewableFlags(rec({ xfmr: true })), ['xfmr']);
  assert.deepEqual(PP.reviewableFlags(rec({ lean: 'severe', xarm: 'damaged', veg: 'touching', att: 5, xfmr: true })), ['lean', 'crossarm', 'vegetation', 'att3', 'xfmr']);
});

test('reviewState tracks decision progress per record; a note alone is not a decision', () => {
  assert.equal(PP.reviewState(rec({ util: false }), null), null, 'nothing to review');
  assert.equal(PP.reviewState(rec({ lean: 'severe' }), null), 'unreviewed');
  const both = rec({ lean: 'severe', xfmr: true });
  assert.equal(PP.reviewState(both, { flags: { lean: 'supported' } }), 'partial');
  assert.equal(PP.reviewState(both, { flags: { lean: 'supported', xfmr: 'not_supported' } }), 'reviewed');
  assert.equal(PP.reviewState(rec({ lean: 'severe' }), { flags: {}, note: 'looks fine' }), 'unreviewed', 'a note alone is not a decision');
  assert.equal(PP.reviewState(rec({ lean: 'severe' }), { flags: { lean: 'supported' } }), 'reviewed', 'old-shape entry without updated still works');
});

test('reviewVerdict summarizes decided flags: all supported is ok, any not_supported is no, else cannot tell', () => {
  assert.equal(PP.reviewVerdict(null), null);
  assert.equal(PP.reviewVerdict({ flags: {} }), null);
  assert.equal(PP.reviewVerdict({ flags: { lean: 'supported', xfmr: 'supported' } }), 'ok');
  assert.equal(PP.reviewVerdict({ flags: { lean: 'supported', xfmr: 'not_supported' } }), 'no');
  assert.equal(PP.reviewVerdict({ flags: { lean: 'supported', xfmr: 'cannot_tell' } }), 'ct');
});

test('reviewProgress counts reviewable, reviewed, and partial records', () => {
  const records = [rec({ id: 'a', lean: 'severe' }), rec({ id: 'b', xfmr: true }), rec({ id: 'c', util: false })];
  const reviews = { a: { flags: { lean: 'supported' } } };
  const p = PP.reviewProgress(records, reviews);
  assert.deepEqual(p, { reviewable: 2, reviewed: 1, partial: 0 });
});

test('applyFilters review status: unreviewed includes partial, reviewed requires every flag decided', () => {
  const a = rec({ id: 'a', lean: 'severe' });
  const b = rec({ id: 'b', lean: 'severe', xfmr: true });
  const c = rec({ id: 'c', lean: 'severe' });
  const reviews = { b: { flags: { lean: 'supported' } }, c: { flags: { lean: 'supported' } } };
  const rows = [a, b, c];
  assert.deepEqual(PP.applyFilters(rows, { flag: 'all', review: 'unreviewed', reviews }).map(r => r.id), ['a', 'b']);
  assert.deepEqual(PP.applyFilters(rows, { flag: 'all', review: 'reviewed', reviews }).map(r => r.id), ['c']);
  assert.deepEqual(PP.applyFilters(rows, { flag: 'all', review: 'all', reviews }).map(r => r.id), ['a', 'b', 'c']);
});

test('applyFilters recent switch uses state.now against the 24-month window; the numeric year form still works', () => {
  const now = Date.UTC(2026, 5, 15);
  const rows = [rec({ id: 'r', shown: { year: 2026, ts: now - 10 * MS_MONTH } }), rec({ id: 'o', shown: { year: 2020, ts: now - 30 * MS_MONTH } })];
  assert.deepEqual(PP.applyFilters(rows, { flag: 'all', recent: true, now }).map(r => r.id), ['r']);
  assert.deepEqual(PP.applyFilters(rows, { flag: 'all', recent: 2022 }).map(r => r.id), ['r'], 'older numeric year form still works');
});

test('summary.recent counts utility records within the recent window at the given time', () => {
  const now = Date.UTC(2026, 5, 15);
  const rows = [rec({ shown: { year: 2026, ts: now - 10 * MS_MONTH } }), rec({ shown: { year: 2020, ts: now - 30 * MS_MONTH } }), rec({ util: false, shown: { year: 2026, ts: now } })];
  assert.equal(PP.summary(rows, now).recent, 1);
});

test('mergeReviews validates the import file shape and each decision', () => {
  assert.match(PP.mergeReviews({}, null, {}).errors[0], /expected an object/, 'non-object incoming');
  assert.match(PP.mergeReviews({}, {}, {}).errors[0], /expected an object/, 'missing decisions');
  assert.match(PP.mergeReviews({}, { decisions: [] }, {}).errors[0], /expected an object/, 'decisions must not be an array');
  let r = PP.mergeReviews({}, { decisions: { x: null } }, { x: true });
  assert.match(r.errors[0], /malformed decision/, 'non-object decision');
  r = PP.mergeReviews({}, { decisions: { x: { flags: { bogus: 'supported' } } } }, { x: true });
  assert.match(r.errors[0], /unknown flag or value/, 'bad flag key');
  r = PP.mergeReviews({}, { decisions: { x: { flags: { lean: 'maybe' } } } }, { x: true });
  assert.match(r.errors[0], /unknown flag or value/, 'bad value');
  r = PP.mergeReviews({}, { decisions: { x: { flags: {}, note: 5 } } }, { x: true });
  assert.match(r.errors[0], /note is not text/, 'non-string note');
});

test('mergeReviews classifies each decision as new, same, conflict, or unknown; flags a newer local decision', () => {
  const byId = { a: true, b: true };
  const incoming = { decisions: {
    a: { flags: { lean: 'supported' }, note: '', updated: '2026-01-01T00:00:00Z' },
    b: { flags: { lean: 'supported' }, note: '', updated: '2026-01-01T00:00:00Z' },
    z: { flags: { lean: 'supported' }, note: '', updated: '2026-01-01T00:00:00Z' },
  } };
  const local = { a: { flags: { lean: 'supported' }, note: '', updated: '2026-02-01T00:00:00Z' }, b: { flags: { lean: 'not_supported' }, note: '', updated: '2025-01-01T00:00:00Z' } };
  const { errors, items } = PP.mergeReviews(local, incoming, byId);
  assert.deepEqual(errors, []);
  const byKind = Object.fromEntries(items.map(i => [i.id, i]));
  assert.equal(byKind.a.kind, 'same');
  assert.equal(byKind.b.kind, 'conflict');
  assert.equal(byKind.b.localNewer, false, 'incoming is newer than local here');
  assert.equal(byKind.z.kind, 'unknown');
  const local2 = { b: { flags: { lean: 'not_supported' }, note: '', updated: '2026-03-01T00:00:00Z' } };
  const { items: items2 } = PP.mergeReviews(local2, incoming, byId);
  assert.equal(items2.find(i => i.id === 'b').localNewer, true, 'local decision is newer than the imported one');
  const { items: items3 } = PP.mergeReviews({}, incoming, byId);
  assert.equal(items3.find(i => i.id === 'a').kind, 'new');
});

test('orderFlags leads with the active filter, then falls back to the severity order', () => {
  const dbl = { pair_id: 'MH-abc12345', reason: 'old pole beside its replacement' };
  const many = rec({ dbl, lean: 'moderate', xarm: 'damaged', veg: 'touching', att: 4, xfmr: true });
  // No filter: the documented severity order, conditions before watch items before equipment.
  assert.deepEqual(PP.orderFlags(many, null), ['crossarm', 'double', 'lean', 'vegetation', 'att3', 'xfmr']);
  assert.deepEqual(PP.orderFlags(many, 'all'), ['crossarm', 'double', 'lean', 'vegetation', 'att3', 'xfmr']);
  // The filter the reader arrived through always leads, whatever its severity rank.
  assert.equal(PP.primaryFlag(many, 'veg'), 'vegetation');
  assert.equal(PP.primaryFlag(many, 'double'), 'double');
  assert.equal(PP.primaryFlag(many, 'xfmr'), 'xfmr');
  assert.equal(PP.primaryFlag(many, 'att3'), 'att3');
  // Chip keys and flag keys differ (xarm/crossarm, veg/vegetation); FILTER_FLAG is the one map.
  assert.equal(PP.FILTER_FLAG.xarm, 'crossarm');
  assert.equal(PP.FILTER_FLAG.veg, 'vegetation');
  // The rest keep their relative order behind the lead.
  assert.deepEqual(PP.orderFlags(many, 'veg'), ['vegetation', 'crossarm', 'double', 'lean', 'att3', 'xfmr']);
  // A filter the record does not carry changes nothing.
  assert.deepEqual(PP.orderFlags(rec({ veg: 'touching' }), 'xarm'), ['vegetation']);
  // Watch items never lead ahead of a condition flag.
  assert.equal(PP.primaryFlag(rec({ lean: 'slight', veg: 'touching' }), null), 'vegetation');
  assert.equal(PP.primaryFlag(rec({ lean: 'slight' }), null), 'lean_slight');
  // Nothing flagged, and non-utility records, have no primary finding.
  assert.equal(PP.primaryFlag(rec({}), null), null);
  assert.equal(PP.primaryFlag(rec({ util: false, veg: 'touching' }), 'veg'), null);
});

test('"Any issue" is exactly the condition-flag population the summary counts', () => {
  const dbl = { pair_id: 'MH-abc12345' };
  // In: any one of the four condition flags.
  assert.equal(PP.FILTERS.any(rec({ dbl })), true);
  assert.equal(PP.FILTERS.any(rec({ lean: 'moderate' })), true);
  assert.equal(PP.FILTERS.any(rec({ lean: 'severe' })), true);
  assert.equal(PP.FILTERS.any(rec({ xarm: 'damaged' })), true);
  assert.equal(PP.FILTERS.any(rec({ veg: 'touching' })), true);
  // Out: watch items and equipment observations are not condition issues.
  assert.equal(PP.FILTERS.any(rec({ lean: 'slight' })), false, 'slight lean alone is a watch item');
  assert.equal(PP.FILTERS.any(rec({ xfmr: true })), false, 'a transformer is not an issue');
  assert.equal(PP.FILTERS.any(rec({ att: 7 })), false, '3+ attachments is not an issue');
  assert.equal(PP.FILTERS.any(rec({ veg: 'near' })), false);
  assert.equal(PP.FILTERS.any(rec({})), false);
  // It is the same predicate the dataset summary reports, so the two can never disagree.
  const rows = [rec({ id: 'a', veg: 'touching' }), rec({ id: 'b', lean: 'slight' }), rec({ id: 'c', xfmr: true, att: 5 }),
                rec({ id: 'd', xarm: 'damaged', lean: 'slight' }), rec({ id: 'e' }), rec({ id: 'f', util: false, veg: 'touching' })];
  assert.equal(rows.filter(PP.isUtility).filter(PP.FILTERS.any).length, PP.summary(rows).conditionIssues);
  // No FILTER_FLAG entry: "any" names no single finding, so the panel falls back to severity order.
  assert.equal(PP.FILTER_FLAG.any, undefined);
  assert.equal(PP.primaryFlag(rec({ veg: 'touching', xarm: 'damaged' }), 'any'), 'crossarm');
});

test('search matches id, street text and typed coordinates, and composes with the filters', () => {
  const r = rec({ id: 'read-01552', st: 'Main Street', lat: 42.5405, lon: -71.1041 });
  assert.equal(PP.matchesQuery(r, ''), true, 'an empty query filters nothing out');
  assert.equal(PP.matchesQuery(r, '   '), true);
  assert.equal(PP.matchesQuery(r, 'read-01552'), true);
  assert.equal(PP.matchesQuery(r, '01552'), true, 'partial id');
  assert.equal(PP.matchesQuery(r, 'READ'), true, 'case-insensitive');
  assert.equal(PP.matchesQuery(r, 'main'), true, 'street text');
  assert.equal(PP.matchesQuery(r, 'Main Street'), true);
  assert.equal(PP.matchesQuery(r, '42.5405, -71.1041'), true, 'typed coordinates');
  assert.equal(PP.matchesQuery(r, '42.5405 -71.1041'), true, 'space-separated coordinates');
  assert.equal(PP.matchesQuery(r, '40.0, -71.1'), false, 'a distant coordinate does not match');
  assert.equal(PP.matchesQuery(r, 'elm'), false);
  // An intersection from the double-pole pass is searchable too.
  assert.equal(PP.matchesQuery(rec({ id: 'marb-1', dbl: { pair_id: 'p', street: 'Harbor Avenue', cross_street: 'Nanepashemet Street' } }), 'nanepashemet'), true);
  // A record with no street text at all is still findable by id and never throws.
  assert.equal(PP.matchesQuery(rec({ id: 'hard-00001' }), 'hard-00001'), true);
  assert.equal(PP.matchesQuery(rec({ id: 'hard-00001' }), 'main'), false);
  // applyFilters ANDs the query with everything else.
  const rows = [rec({ id: 'read-1', st: 'Main Street', veg: 'touching' }), rec({ id: 'read-2', st: 'Main Street' }),
                rec({ id: 'read-3', st: 'Elm Street', veg: 'touching' })];
  const f = st => PP.applyFilters(rows, Object.assign({ flag: 'all', q: '', now: Date.now() }, st)).map(r2 => r2.id);
  assert.deepEqual(f({ q: 'main' }), ['read-1', 'read-2']);
  assert.deepEqual(f({ q: 'main', flag: 'any' }), ['read-1']);
  assert.deepEqual(f({ flag: 'any' }), ['read-1', 'read-3']);
});
