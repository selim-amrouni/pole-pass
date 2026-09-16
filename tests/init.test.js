'use strict';
// Runs web/app.js against the built data with no map library: list, filters, count, details, deep link must still work.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { makeDocument } = require('./fakedom.js');

const OUT = path.join(__dirname, '..', 'out', 'greenpoint-brooklyn-new-york');
const IDS = ['summary', 'dates-label|span', 'toolbar', 'filters-toggle|button', 'tab-list|button', 'tab-map|button', 'chips', 'dates-btn|button', 'dates-menu', 'year-min|input', 'year-max|input', 'recent|button', 'other|input', 'reset|button', 'sort|select', 'export-btn|button', 'export-menu', 'exp-csv-f|button', 'exp-geo-f|button', 'exp-review|button', 'ws', 'count', 'list', 'mapwrap|section', 'map', 'fit|button', 'resetview|button', 'legend', 'detail|aside', 'lb|dialog', 'lb-cap|span', 'lb-src|a', 'lb-close|button', 'lb-img|img'];

function boot(hash = '', width = 1440) {
  const document = makeDocument(IDS.map(s => s.split('|')));
  const storage = {}; const localStorage = { getItem: k => storage[k] ?? null, setItem: (k, v) => { storage[k] = String(v); } };
  const window = { innerWidth: width, addEventListener() {}, PP: require('../web/predicates.js'), location: { hash, pathname: '/', search: '' } };
  const history = { replaceState: (s, t, url) => { window.location.hash = url.startsWith('#') ? url : ''; } };
  const win = {}; new Function('window', fs.readFileSync(path.join(OUT, 'data.js'), 'utf8'))(win);
  window.POLE_DATA = win.POLE_DATA;
  const app = fs.readFileSync(path.join(OUT, 'app.js'), 'utf8');
  new Function('window', 'document', 'localStorage', 'history', 'location', 'CSS', 'URL', 'Blob', 'console', app)(
    window, document, localStorage, history, window.location, { escape: s => s }, { createObjectURL: () => 'blob:' }, class {}, console);
  return { window, document, PP: window.PolePass, storage };
}

test('initializes without maplibregl: fallback shown, list and count rendered, nothing selected', () => {
  const { document, PP, window } = boot();
  assert.match(document.getElementById('map').innerHTML, /The map could not load/);
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
  const { document, PP, storage, window } = boot('#pole=gree-00309');
  const btn = document.getElementById('detail').querySelectorAll('button').find(b => b.dataset.rf === 'crossarm' && b.dataset.rv === 'supported');
  assert.ok(btn, 'crossarm flag has review buttons');
  btn.click();
  const key = Object.keys(storage)[0];
  assert.match(key, new RegExp(window.POLE_DATA.meta.version));
  assert.equal(JSON.parse(storage[key])['gree-00309'].flags.crossarm, 'supported');
  assert.match(document.getElementById('detail').innerHTML, /Saved in this browser only/);
});

test('prev/next move within the filtered list and single-photo records say so', () => {
  const { PP, window, document } = boot();
  const first = PP.filtered[0].id; PP.select(first);
  document.getElementById('detail').querySelectorAll('button').find(b => b.id === 'next').click();
  assert.equal(PP.state.selected, PP.filtered[1].id);
  const single = window.POLE_DATA.records.find(r => r.util && r.n === 1);
  PP.select(single.id);
  assert.match(document.getElementById('detail').innerHTML, /Single photo/);
});
