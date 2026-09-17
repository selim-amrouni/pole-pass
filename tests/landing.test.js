'use strict';
// The root landing page: hero example from real bundle data, one card per area, freshness from capture months, deep-link forwarding.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const { makeDocument } = require('./fakedom.js');

function boot() {
  const document = makeDocument([['cards'], ['hero-frame'], ['hero-cap', 'figcaption'], ['attribution', 'p']]);
  const window = {};
  const js = fs.readFileSync(path.join(__dirname, '..', 'web', 'landing', 'landing.js'), 'utf8');
  new Function('window', 'document', 'location', 'fetch', js)(window, document, { hash: '' }, undefined);
  return { PL: window.PoleLanding, document };
}

const NOW = Date.UTC(2026, 8, 17);  // 2026-09-17
const LIST = [
  { slug: 'greenpoint-brooklyn-new-york', name: 'Greenpoint, Brooklyn', kind: 'city',
    stats: { generated: '2026-09-17', version: 'abc12345', counts: { utility: 706, condition_issues: 90, warnings: 431, photo_year_first: 2015, photo_year_last: 2024 },
             shown_by_month: { '2015-06': 10, '2023-07': 40, '2024-12': 50 }, undated: 0,
             example: { id: 'gree-00062', crop: 'crops/gree-00062.jpg', date: '2023-07', url: 'https://www.mapillary.com/app/?pKey=1', by: 'someone', flags: [], marks: { top: [0.5, 0.1], base: [0.5, 0.9], att: [{ p: [0.55, 0.3], l: 'cable' }, { p: [0.45, 0.4], l: 'box' }, { p: [0.5, 0.5], l: 'third' }], xfmr: [0.6, 0.35] } } } },
  { slug: 'hardwick-vermont', name: 'Hardwick, Vermont', kind: 'backcountry', stats: { counts: { utility: 1591, photo_year_first: 2013, photo_year_last: 2022 }, shown_by_month: { '2019-07': 800, '2022-11': 700 } } },
  { slug: 'reading-massachusetts', name: 'Reading, Massachusetts', kind: 'suburb', stats: { counts: { utility: 1589, photo_year_first: 2017, photo_year_last: 2026 }, shown_by_month: { '2017-10': 300, '2025-10': 900, '2026-06': 300 }, undated: 3, example: { id: 'read-00070', crop: 'crops/read-00070.jpg', date: '2025-10', flags: [] } } },
  { slug: 'nowhere', name: 'Nowhere', kind: 'city' },
];

test('freshness counts one representative date per pole within 24 months of now, and says so when none qualify', () => {
  const { PL } = boot();
  const g = PL.freshness(LIST[0].stats, NOW);
  assert.equal(g.share, 50); assert.equal(g.recent, 50); assert.equal(g.total, 100);
  assert.equal(g.text, 'Photos 2015 to 2024 · 50% within 24 months');
  const h = PL.freshness(LIST[1].stats, NOW);
  assert.equal(h.share, 0); assert.equal(h.text, 'Photos 2013 to 2022 · none within 24 months', 'Hardwick has nothing recent and the card says so');
  assert.match(PL.freshness(LIST[2].stats, NOW).text, /80% within 24 months · 3 undated$/);
  assert.equal(PL.freshness(LIST[3].stats, NOW).text, 'Photo dates unknown', 'no histogram: no share is invented');
  assert.equal(PL.freshness({ counts: { photo_year_first: 2020, photo_year_last: 2020 }, shown_by_month: {} }, NOW).text, 'Photos 2020');
});

test('cards show name, kind label, pole-record count, freshness, one Explore button, and no hashes or slight-lean counts', () => {
  const { PL, document } = boot();
  const el = document.getElementById('cards');
  assert.equal(PL.render(LIST, el, NOW), 4);
  const cards = el.querySelectorAll('.card');
  assert.equal(cards.length, 4);
  const html = el.innerHTML;
  assert.ok(html.includes('>Urban<') && html.includes('>Suburban<') && html.includes('>Rural<'), 'kind labels are words, not slugs');
  assert.ok(!/backcountry</.test(html));
  assert.ok(html.includes('>706<') && html.includes('>1,591<') && html.includes('pole records'));
  assert.ok(!html.includes('431') && !html.includes('abc12345') && !html.includes('2026-09-17'), 'no slight-lean count, hash, or build date on cards');
  assert.ok(html.includes('href="greenpoint-brooklyn-new-york/"') && html.includes('Explore Greenpoint'));
  assert.ok(html.includes('greenpoint-brooklyn-new-york/crops/gree-00062.jpg'), 'card photo comes from the bundle');
  assert.ok(PL.cardHtml(LIST[1], NOW).includes('No photo published'), 'no example: placeholder, no invented image');
  assert.ok(PL.cardHtml(LIST[3], NOW).includes('Counts not published'));
});

test('hero uses the preferred area example, draws at most three observations, links the record, and degrades without one', () => {
  const { PL, document } = boot();
  const frame = document.getElementById('hero-frame'), cap = document.getElementById('hero-cap');
  assert.equal(PL.renderHero(LIST, frame, cap), 'reading-massachusetts', 'Reading is preferred when present');
  assert.ok(frame.innerHTML.includes('reading-massachusetts/crops/read-00070.jpg'));
  assert.ok(cap.innerHTML.includes('reading-massachusetts/#pole=read-00070') && cap.innerHTML.includes('photo 2025-10') && cap.innerHTML.includes('Mapillary'));
  assert.ok(!cap.innerHTML.includes('verified'), 'no validation claim');
  // marks: axis + 2 attachments; the third attachment and the transformer are dropped (three observations at most)
  assert.equal(PL.renderHero([LIST[0]], frame, cap), 'greenpoint-brooklyn-new-york');
  assert.equal((frame.innerHTML.match(/class="mk att"/g) || []).length, 2);
  assert.ok(frame.innerHTML.includes('class="axis"') && !frame.innerHTML.includes('class="mk xfmr"'));
  assert.ok(frame.innerHTML.includes('Model observations'));
  assert.equal(PL.renderHero([LIST[1], LIST[3]], frame, cap), null);
  assert.ok(frame.innerHTML.includes('Example photo unavailable'));
});

test('empty or missing list shows an empty state, never placeholder numbers', () => {
  const { PL, document } = boot();
  const el = document.getElementById('cards');
  assert.equal(PL.render([], el, NOW), 0); assert.ok(el.innerHTML.includes('No areas are published yet'));
  assert.equal(PL.render(null, el, NOW), 0); assert.ok(!/\d/.test(el.innerHTML));
  assert.equal(PL.render([{ name: 'no slug' }, null], el, NOW), 0);
});

test('escapes names and forwards #pole= links to the first territory', () => {
  const { PL } = boot();
  assert.ok(PL.cardHtml({ slug: 'x', name: '<b>Town</b>' }, NOW).includes('&lt;b&gt;Town&lt;/b&gt;'));
  assert.equal(PL.forwardPoleLink(LIST, { hash: '#pole=gree-00062' }), 'greenpoint-brooklyn-new-york/#pole=gree-00062');
  assert.equal(PL.forwardPoleLink(LIST, { hash: '' }), null);
  assert.equal(PL.forwardPoleLink([], { hash: '#pole=x' }), null);
});
