'use strict';
// The root landing page renders one card per territory from territories.json and forwards old #pole= links.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { makeDocument } = require('./fakedom.js');

function boot() {
  const document = makeDocument([['cards'], ['attribution', 'p']]);
  const window = {};
  const js = fs.readFileSync(path.join(__dirname, '..', 'web', 'landing', 'landing.js'), 'utf8');
  new Function('window', 'document', 'location', 'fetch', js)(window, document, { hash: '' }, undefined);
  return { PL: window.PoleLanding, document };
}

const LIST = [
  { slug: 'greenpoint-brooklyn-new-york', name: 'Greenpoint, Brooklyn', kind: 'city',
    stats: { generated: '2026-09-16', version: 'abc12345', counts: { utility: 620, condition_issues: 83, warnings: 363, frames_classified: 3244, photo_year_first: 2017, photo_year_last: 2025, multi_year: 23, not_in_osm: 620 } } },
  { slug: 'hardwick-vermont', name: 'Hardwick, Vermont', kind: 'backcountry', stats: { counts: { utility: 1534, not_in_osm: null } } },
  { slug: 'reading-massachusetts', name: 'Reading, Massachusetts', kind: 'suburb' },
];

test('renders one card per territory with its kind and only the stats it has', () => {
  const { PL, document } = boot();
  const el = document.getElementById('cards');
  assert.equal(PL.render(LIST, el), 3);
  const cards = el.querySelectorAll('.card');
  assert.equal(cards.length, 3);
  assert.deepEqual(cards.map(c => c.getAttribute('href')), ['greenpoint-brooklyn-new-york/', 'hardwick-vermont/', 'reading-massachusetts/']);
  assert.ok(el.innerHTML.includes('class="kind city"') && el.innerHTML.includes('class="kind suburb"') && el.innerHTML.includes('class="kind backcountry"'));
  const g = PL.cardHtml(LIST[0]);
  assert.ok(g.includes('>620<') && g.includes('>83<') && g.includes('>363<') && g.includes('3,244') && g.includes('2017–2025') && g.includes('not in OpenStreetMap'));
  assert.ok(g.includes('Built 2026-09-16') && g.includes('abc12345'));
  const h = PL.cardHtml(LIST[1]);
  assert.ok(h.includes('>1,534<') && !h.includes('OpenStreetMap') && !h.includes('photo years') && !h.includes('Built'), 'no OSM diff, no years: those stats are absent, not zero');
  assert.ok(PL.cardHtml(LIST[2]).includes('Counts not published'));
});

test('empty or missing list shows an empty state, never placeholder numbers', () => {
  const { PL, document } = boot();
  const el = document.getElementById('cards');
  assert.equal(PL.render([], el), 0); assert.ok(el.innerHTML.includes('No areas are published yet'));
  assert.equal(PL.render(null, el), 0); assert.ok(!/\d/.test(el.innerHTML));
});

test('escapes names and forwards #pole= links to the first territory', () => {
  const { PL } = boot();
  assert.ok(PL.cardHtml({ slug: 'x', name: '<b>Town</b>' }).includes('&lt;b&gt;Town&lt;/b&gt;'));
  assert.equal(PL.forwardPoleLink(LIST, { hash: '#pole=gree-00062' }), 'greenpoint-brooklyn-new-york/#pole=gree-00062');
  assert.equal(PL.forwardPoleLink(LIST, { hash: '' }), null);
  assert.equal(PL.forwardPoleLink([], { hash: '#pole=x' }), null);
});
