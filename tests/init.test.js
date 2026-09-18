'use strict';
// Runs web/app.js against the built data with no map library: list, filters, count, details, deep link must still work.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { makeDocument, El } = require('./fakedom.js');

const OUT = path.join(__dirname, '..', 'out', 'greenpoint-brooklyn-new-york');
const IDS = ['summary', 'dates-label|span', 'territory|select', 'loc-name|span', 'kind|span', 'toolbar', 'filters-toggle|button', 'tab-list|button', 'tab-map|button', 'chips', 'dates-btn|button', 'dates-menu', 'year-min|input', 'year-max|input', 'recent|input', 'other|input', 'years|input', 'years-n|span', 'osm-wrap|label', 'osm|input', 'osm-n|span', 'reset|button', 'sort|select', 'export-btn|button', 'export-menu', 'exp-csv-f|button', 'exp-geo-f|button', 'exp-review|button', 'imp-review|button', 'imp-file|input', 'ws', 'count', 'list', 'mapwrap|section', 'map', 'map-state', 'fit|button', 'resetview|button', 'legend', 'detail|aside', 'lb|dialog', 'lb-cap|span', 'lb-src|a', 'lb-close|button', 'lb-img|img',
  'map-notice', 'chips-eq', 'chips-more', 'g-issues|div|filters', 'g-equip|div|filters', 'g-review|div|filters', 'review-seg|span', 'review-n|span', 'recent-n|span', 'more-btn|button', 'more-menu', 'more-fold', 'active', 'import-dlg|dialog', 'import-body', 'import-close|button', 'about-dlg|dialog', 'about-close|button',
  'notice-btn|button', 'filters', 'more-n|span', 'more-done|button', 'list-rail|button', 'about-counts'];

function boot(hash = '', width = 1440, extra = {}) {
  const document = makeDocument(IDS.map(s => s.split('|')));
  const storage = Object.assign({}, extra.storage);
  const localStorage = { getItem: k => storage[k] ?? null, setItem: (k, v) => { storage[k] = String(v); } };
  const window = { innerWidth: width, addEventListener() {}, PP: require('../web/predicates.js'), location: { hash, pathname: '/', search: '' } };
  const setUrl = url => { const i = url.indexOf('#'); window.location.hash = i >= 0 ? url.slice(i) : ''; };
  const history = { replaceState: (s, t, url) => setUrl(url), pushState: (s, t, url) => setUrl(url) };
  const win = {}; new Function('window', fs.readFileSync(path.join(OUT, 'data.js'), 'utf8'))(win);
  window.POLE_DATA = win.POLE_DATA;
  const app = fs.readFileSync(path.join(OUT, 'app.js'), 'utf8');
  const URLStub = extra.URL || { createObjectURL: () => 'blob:' };
  const BlobStub = extra.Blob || class {};
  new Function('window', 'document', 'localStorage', 'history', 'location', 'CSS', 'URL', 'Blob', 'console', 'fetch', app)(
    window, document, localStorage, history, window.location, { escape: s => s }, URLStub, BlobStub, console, extra.fetch);
  return { window, document, PP: window.PolePass, storage };
}

test('initializes without maplibregl: fallback shown, list and count rendered, nothing selected', () => {
  const { document, PP, window } = boot();
  assert.match(document.getElementById('map-state').innerHTML, /Map unavailable/);
  assert.equal(document.getElementById('map-state').hidden, false);
  assert.equal(document.getElementById('ws').classList.contains('map-failed'), true, 'the list reclaims the space');
  assert.equal(document.getElementById('legend').hidden, true);
  const util = window.POLE_DATA.records.filter(r => r.util).length;
  assert.match(document.getElementById('count').innerHTML, new RegExp(`of <span class="mono">${util}</span>`));
  assert.equal(document.getElementById('list').querySelectorAll('.row').length, Math.min(20, util));
  assert.equal(PP.state.selected, null, 'no record opens by default; the map is the first view');
  assert.equal(document.getElementById('ws').classList.contains('has-detail'), false);
});

test('filters change list, count, and filtered export together', () => {
  const { document, PP, window } = boot();
  const lean = window.POLE_DATA.records.filter(r => r.util && (r.lean === 'moderate' || r.lean === 'severe')).length;
  const chip = document.getElementById('chips').querySelectorAll('.chip').find(c => c.dataset.f === 'lean');
  chip.click();
  assert.equal(PP.filtered.length, lean);
  assert.match(document.getElementById('count').innerHTML, new RegExp(`of <span class="mono">${lean}</span>`));
  assert.equal(document.getElementById('list').querySelectorAll('.row').length, Math.min(20, lean));
  assert.ok(PP.filtered.every(r => r.lean === 'moderate' || r.lean === 'severe'));
});

test('empty result shows the empty state with a reset', () => {
  const { document, PP } = boot();
  document.getElementById('year-min').value = '2026'; document.getElementById('year-min').dispatch('change');
  assert.equal(PP.filtered.length, 0);
  assert.match(document.getElementById('list').innerHTML, /No poles match these filters/);
  document.getElementById('reset').click();
  assert.ok(PP.filtered.length > 0);
});

test('deep link selects the record; unknown id is reported', () => {
  const a = boot('#pole=gree-00309');
  assert.equal(a.PP.state.selected, 'gree-00309');
  assert.doesNotMatch(a.document.getElementById('detail').innerHTML, /Example record/);
  const b = boot('#pole=nope-1');
  assert.equal(b.PP.state.selected, null);
  assert.match(b.document.getElementById('list').parent.innerHTML + b.document.body.querySelectorAll('.empty').map(e => e.textContent).join(''), /No record with id/);
});

test('review decisions persist locally under the dataset version', () => {
  // record ids are renumbered by every dedupe run, so find the crossarm-flagged pole in the built data
  const win = {}; new Function('window', fs.readFileSync(path.join(OUT, 'data.js'), 'utf8'))(win);
  const target = win.POLE_DATA.records.find(r => r.util && r.xarm === 'damaged');
  assert.ok(target, 'the built data has a utility pole with crossarm damage');
  const { document, PP, storage, window } = boot(`#pole=${target.id}`);
  const btn = document.getElementById('detail').querySelectorAll('button').find(b => b.dataset.rf === 'crossarm' && b.dataset.rv === 'supported');
  assert.ok(btn, 'crossarm flag has review buttons');
  btn.click();
  const key = Object.keys(storage)[0];
  assert.match(key, new RegExp(window.POLE_DATA.meta.version));
  assert.equal(JSON.parse(storage[key])[target.id].flags.crossarm, 'supported');
  assert.match(document.getElementById('detail').innerHTML, /Saved in this browser only/);
});

test('prev/next move within the filtered list and single-photo records say so', () => {
  const { PP, window, document } = boot();
  const first = PP.filtered[0].id; PP.select(first);
  document.getElementById('detail').querySelectorAll('button').find(b => b.id === 'next').click();
  assert.equal(PP.state.selected, PP.filtered[1].id);
  const single = window.POLE_DATA.records.find(r => r.util && r.n === 1);
  PP.select(single.id);
  assert.match(document.getElementById('detail').innerHTML, /1 of 1 photo\b/);
});

test('multi-year filter keeps records photographed in 2+ years; compare pairs the farthest photos in time', () => {
  const { document, PP, window } = boot();
  const n = window.POLE_DATA.records.filter(r => r.util && new Set(r.frames.map(f => f.year).filter(y => y != null)).size >= 2).length;
  const cb = document.getElementById('years'); cb.checked = true; cb.dispatch('change');
  assert.equal(PP.filtered.length, n);
  assert.equal(String(document.getElementById('years-n').textContent), String(n));
  if (!n) return;
  PP.select(PP.filtered[0].id);
  const detail = document.getElementById('detail');
  assert.match(detail.innerHTML, /Apparent tilt in photos/);
  assert.match(detail.innerHTML, /\d{4}–\d{4}<\/span>/, 'strip label shows the year span');
  detail.querySelectorAll('button').find(b => b.id === 'cmp').click();
  const caps = [...detail.innerHTML.matchAll(/<figcaption><b>(\d{4})-\d{2}<\/b>/g)].map(m => m[1]);
  assert.equal(caps.length, 2);
  assert.notEqual(caps[0], caps[1], 'the two compared photos come from different years');
  document.getElementById('reset').click();
  assert.equal(cb.checked, false); assert.ok(PP.filtered.length > n);
});

test('photo key lists only the glyphs drawn on the current frame and follows the toggles', () => {
  const { document, PP, window } = boot();
  const r = window.POLE_DATA.records.find(x => x.util && x.frames.some(f => f.img && f.marks && f.marks.top && f.marks.base && f.marks.att.length));
  PP.select(r.id);
  const detail = document.getElementById('detail');
  assert.match(detail.innerHTML, /class="keyrow"[^]*Mapillary outline[^]*Pole axis[^]*Attachment 1/);
  detail.querySelectorAll('button').find(b => b.id === 'tg-markers').click();
  assert.doesNotMatch(detail.innerHTML, /Pole axis/);
  assert.match(detail.innerHTML, /Mapillary outline/);
  detail.querySelectorAll('button').find(b => b.id === 'tg-outline').click();
  assert.doesNotMatch(detail.innerHTML, /class="keyrow"/);
});

test('territory selector appears only when a territories.json lists this bundle', async () => {
  const list = [{ slug: 'greenpoint-brooklyn-new-york', name: 'Greenpoint, Brooklyn', kind: 'city' }, { slug: 'elsewhere', name: 'Elsewhere', kind: 'backcountry' }];
  // the HTML ships both elements hidden; the fake DOM does not read attributes, so mirror that here
  const hide = b => { b.document.getElementById('territory').hidden = true; b.document.getElementById('kind').hidden = true; return b; };
  const withList = hide(boot('', 1440, { fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve(list) }) }));
  await new Promise(r => setTimeout(r, 0));
  const sel = withList.document.getElementById('territory');
  assert.equal(sel.hidden, false); assert.match(sel.innerHTML, /Elsewhere/); assert.match(sel.innerHTML, /selected>Greenpoint/);
  assert.equal(withList.document.getElementById('kind').textContent, 'Urban', 'kind is shown as a plain label');
  const without = hide(boot('', 1440, { fetch: () => Promise.resolve({ ok: false }) }));
  await new Promise(r => setTimeout(r, 0));
  assert.equal(without.document.getElementById('territory').hidden, true);
  assert.equal(without.document.getElementById('kind').hidden, true);
});

test('review filter narrows to reviewable, unreviewed records; reviewed is empty with no decisions', () => {
  const { document, PP, window } = boot();
  PP.state.review = 'unreviewed';
  PP.refresh();
  const expected = window.POLE_DATA.records.filter(r => window.PP.isUtility(r) && window.PP.reviewableFlags(r).length > 0);
  assert.equal(PP.filtered.length, expected.length);
  assert.ok(PP.filtered.every(r => window.PP.reviewableFlags(r).length > 0));
  assert.equal(document.getElementById('review-n').textContent, `0 / ${expected.length} with something to review`);

  PP.state.review = 'reviewed';
  PP.refresh();
  assert.equal(PP.filtered.length, 0);
});

test('nextUnreviewed moves to a later record whose flags are not all decided', () => {
  const { document, PP, window } = boot();
  PP.state.review = 'unreviewed';
  PP.refresh();
  const first = PP.filtered[0];
  PP.select(first.id);
  const flags = window.PP.reviewableFlags(first);
  assert.ok(flags.length, 'the first unreviewed record has reviewable flags');
  const detail = document.getElementById('detail');
  flags.forEach(k => { detail.querySelectorAll('button').find(b => b.dataset.rf === k && b.dataset.rv === 'supported').click(); });
  assert.equal(window.PP.reviewState(first, PP.review[first.id]), 'reviewed');
  const totalReviewable = window.POLE_DATA.records.filter(r => r.util && window.PP.reviewableFlags(r).length > 0).length;
  assert.equal(document.getElementById('review-n').textContent, `1 / ${totalReviewable} with something to review`);
  PP.nextUnreviewed();
  assert.notEqual(PP.state.selected, first.id);
  const next = window.POLE_DATA.records.find(r => r.id === PP.state.selected);
  assert.notEqual(window.PP.reviewState(next, PP.review[next.id]), 'reviewed');
});

test('an old-shape saved review (no "updated" field) still renders a review status and a pressed button', () => {
  const win = {}; new Function('window', fs.readFileSync(path.join(OUT, 'data.js'), 'utf8'))(win);
  const D = win.POLE_DATA;
  const target = D.records.find(r => r.util && (r.lean === 'moderate' || r.lean === 'severe'));
  const key = `polepass-review:${D.meta.slug}:${D.meta.version}`;
  const storage = { [key]: JSON.stringify({ [target.id]: { flags: { lean: 'supported' }, note: 'x' } }) };
  const { document, PP } = boot('', 1440, { storage });
  PP.state.flag = 'lean';
  PP.refresh();
  const row = document.getElementById('list').querySelectorAll('.row').find(el => el.dataset.id === target.id);
  assert.ok(row, 'the target pole renders within the lean-filtered list');
  // the row's own innerHTML is not populated by the fake DOM's tolerant parser (only the container that
  // received the raw string keeps it); check the list container's markup instead.
  assert.match(document.getElementById('list').innerHTML, /Reviewed: supported/);
  PP.select(target.id);
  const btn = document.getElementById('detail').querySelectorAll('button').find(b => b.dataset.rf === 'lean' && b.dataset.rv === 'supported');
  assert.equal(btn.getAttribute('aria-pressed'), 'true');
});

test('CSV export includes review columns; decided rows show reviewed/partial, undecided rows are blank', () => {
  const win = {}; new Function('window', fs.readFileSync(path.join(OUT, 'data.js'), 'utf8'))(win);
  const D = win.POLE_DATA;
  const PPred = require('../web/predicates.js');
  const target = D.records.find(r => r.util && (r.lean === 'moderate' || r.lean === 'severe'));
  const untouched = D.records.find(r => r.util && r.id !== target.id && PPred.reviewableFlags(r).length > 0);
  const untouchedFlag = PPred.reviewableFlags(untouched)[0];
  const key = `polepass-review:${D.meta.slug}:${D.meta.version}`;
  const storage = { [key]: JSON.stringify({ [target.id]: { flags: { lean: 'supported' }, note: '', updated: new Date().toISOString() } }) };
  let captured = null;
  class FakeBlob { constructor(parts, opts) { this.text = parts.join(''); this.type = opts && opts.type; } }
  const FakeURL = { createObjectURL: b => { captured = b; return 'blob:'; } };
  const { document } = boot('', 1440, { storage, Blob: FakeBlob, URL: FakeURL });
  document.getElementById('exp-csv-f').click();
  assert.ok(captured, 'download() built a Blob and asked for an object URL');
  const lines = captured.text.split('\n');
  const header = lines[0].split(',');
  assert.ok(header.includes('review_status')); assert.ok(header.includes('review_lean')); assert.ok(header.includes('review_note')); assert.ok(header.includes('review_updated'));
  const col = name => header.indexOf(name);
  const row = lines.find(l => l.startsWith(target.id + ','));
  assert.ok(row, 'the decided record is in the export');
  const cells = row.split(',');
  assert.match(cells[col('review_status')], /^(reviewed|partial)$/);
  const otherRow = lines.find(l => l.startsWith(untouched.id + ','));
  const otherCells = otherRow.split(',');
  assert.equal(otherCells[col(`review_${untouchedFlag}`)], '', 'undecided rows leave the per-flag review cell blank, not "No"');
  assert.equal(otherCells[col('review_note')], '');
});

test('the about link opens the about dialog; the close button closes it', () => {
  const { document } = boot();
  const link = document.body.appendChild(new El('a'));
  link.setAttribute('href', '#about');
  link.click();
  assert.equal(document.getElementById('about-dlg').open, true);
  document.getElementById('about-close').click();
  assert.equal(document.getElementById('about-dlg').open, false);
});

test('a deep link with an issue context opens the record inside that filtered set', () => {
  const win = {}; new Function('window', fs.readFileSync(path.join(OUT, 'data.js'), 'utf8'))(win);
  const PPred = require('../web/predicates.js');
  const target = win.POLE_DATA.records.find(r => r.util && r.veg === 'touching');
  const inFilter = win.POLE_DATA.records.filter(r => r.util && r.veg === 'touching');
  const { document, PP } = boot(`#pole=${target.id}&issue=veg`);
  assert.equal(PP.state.selected, target.id);
  assert.equal(PP.state.flag, 'veg', 'the issue context is applied before the first refresh');
  assert.equal(PP.filtered.length, inFilter.length);
  const pos = document.getElementById('pos').textContent;
  assert.equal(pos, `${PP.filtered.findIndex(r => r.id === target.id) + 1} of ${inFilter.length}`,
    'the position counts within the filtered set, not within all poles');
  // The panel leads with the reason the reader arrived, not with whatever is first in the data.
  assert.match(document.querySelector('.primary .ctx').textContent, /Shown for: Possible vegetation contact/);
  assert.equal(PPred.primaryFlag(target, 'veg'), 'vegetation');
  // An unknown issue key is ignored rather than emptying the list.
  const bogus = boot(`#pole=${target.id}&issue=nonsense`);
  assert.equal(bogus.PP.state.flag, 'all');
});

test('the URL and Copy link carry the issue context; a bare #pole= link still works', () => {
  const win = {}; new Function('window', fs.readFileSync(path.join(OUT, 'data.js'), 'utf8'))(win);
  const target = win.POLE_DATA.records.find(r => r.util && r.veg === 'touching');
  const { window, PP } = boot();
  PP.state.flag = 'veg'; PP.refresh();
  PP.select(target.id);
  assert.equal(window.location.hash, `#pole=${target.id}&issue=veg`);
  PP.state.flag = 'all'; PP.refresh();
  PP.select(target.id);
  assert.equal(window.location.hash, `#pole=${target.id}`, 'no issue filter leaves the hash as it was before');
});

test('a double-pole record leads with the double, not with the transformer line', () => {
  // Regression: flagLabel was a ternary chain with no 'double' branch, so it fell through to the
  // transformer label and every double-pole record announced "No transformer visible".
  const { document, PP, window } = boot();
  const target = window.POLE_DATA.records.find(r => r.util && !r.dbl && !r.xfmr);
  target.dbl = { pair_id: 'TEST-abc12345', reason: 'old pole beside its replacement' };
  PP.refresh();
  PP.select(target.id);
  const lead = document.querySelector('.lead').children[0].textContent;
  assert.equal(lead, 'Possible double pole');
  assert.doesNotMatch(lead, /transformer/i);
  assert.match(document.getElementById('detail').innerHTML, /Possible double pole/);
  delete target.dbl;
});

test('opening a record tucks the list into the rail on a narrow desktop, and the rail brings it back', () => {
  const win = {}; new Function('window', fs.readFileSync(path.join(OUT, 'data.js'), 'utf8'))(win);
  const target = win.POLE_DATA.records.find(r => r.util);

  // Wide: list and record sit side by side, so there is nothing to reopen.
  const wide = boot('', 1440);
  wide.PP.select(target.id);
  assert.equal(wide.document.getElementById('ws').classList.contains('list-open'), true);
  assert.equal(wide.document.getElementById('ws').classList.contains('list-collapsed'), false);
  assert.equal(wide.document.getElementById('list-rail').hidden, true);

  // Narrow: the list would take a third of the screen from the evidence, so it collapses.
  const narrow = boot('', 1200);
  narrow.PP.select(target.id);
  const ws = narrow.document.getElementById('ws');
  assert.equal(ws.classList.contains('list-collapsed'), true);
  assert.equal(narrow.document.getElementById('list-rail').hidden, false, 'the rail is the way back');
  narrow.document.getElementById('list-rail').click();
  assert.equal(ws.classList.contains('list-collapsed'), false);
  assert.equal(narrow.document.getElementById('list-rail').hidden, true);
  // Closing the record always returns to a browse view with the list showing.
  narrow.PP.close();
  assert.equal(ws.classList.contains('has-detail'), false);
  assert.equal(ws.classList.contains('list-open'), true);
});

test('history entries that change only the issue repaint the open record', () => {
  const win = {}; new Function('window', fs.readFileSync(path.join(OUT, 'data.js'), 'utf8'))(win);
  const target = win.POLE_DATA.records.find(r => r.util && r.veg === 'touching');
  const { document, PP, window } = boot(`#pole=${target.id}&issue=veg`);
  assert.match(document.querySelector('.primary .ctx').textContent, /Shown for: Possible vegetation contact/);
  // Back to the same record without the issue: the lead is derived from state.flag, so the panel
  // has to re-render even though the selected record did not change.
  window.location.hash = `#pole=${target.id}`;
  PP.syncFromHash();
  assert.equal(PP.state.flag, 'all');
  assert.doesNotMatch(document.querySelector('.primary .ctx').textContent, /Shown for/);
  assert.equal(document.querySelector('.primary .ctx').textContent, 'Primary finding');
});

test('unfolding restores the filter groups in their markup order, not the order they were folded in', () => {
  // The fold order is review-then-equipment; re-appending in that order put Review before
  // Equipment once both had been folded. The restore position comes from the markup instead.
  // 1600 is wide enough that the width fallback used by the test DOM unfolds both groups again.
  const { document, PP } = boot('', 1600);
  const filters = document.getElementById('filters'), fold = document.getElementById('more-fold');
  const ids = el => el.children.map(c => c.id).filter(Boolean);
  assert.deepEqual(ids(filters), ['g-issues', 'g-equip', 'g-review'], 'markup order');
  ['g-review', 'g-equip'].forEach(id => fold.appendChild(document.getElementById(id)));
  assert.deepEqual(ids(filters), ['g-issues']);
  PP.state.sizeWs();   // re-runs foldToolbar, which unfolds whatever now fits
  assert.deepEqual(ids(filters), ['g-issues', 'g-equip', 'g-review']);
});
