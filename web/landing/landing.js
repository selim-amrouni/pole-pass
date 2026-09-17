/* Pole Pass landing page. Renders one card per territory from territories.json (written by deploy.sh:
   name and kind from web/territories.json, stats from each bundle's summary.json). No counts live in the HTML.
   Exposes window.PoleLanding = { render, forwardPoleLink } so tests can drive it without fetch. */
(function () {
  'use strict';
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = n => Number.isFinite(n) ? n.toLocaleString('en-US') : '';
  const KIND = { city: 'city', suburb: 'suburb', backcountry: 'backcountry' };

  function stat(label, value, cls) { return value === '' ? '' : `<div><dd class="${cls || ''}">${esc(value)}</dd><dt>${esc(label)}</dt></div>`; }

  function cardHtml(t) {
    const s = t.stats || {}, c = s.counts || {};
    const years = c.photo_year_first && c.photo_year_last ? (c.photo_year_first === c.photo_year_last ? String(c.photo_year_first) : `${c.photo_year_first}–${c.photo_year_last}`) : '';
    const stats = [
      stat('utility poles', num(c.utility)),
      stat('possible condition issues', num(c.condition_issues), 'issue'),
      stat('watch items (slight lean)', num(c.warnings), 'warn'),
      stat('photos assessed', num(c.frames_classified)),
      stat('photo years', years),
      stat('photographed in several years', num(c.multi_year)),
      c.not_in_osm == null ? '' : stat('not in OpenStreetMap', num(c.not_in_osm)),
    ].join('');
    const kind = KIND[t.kind] ? `<span class="kind ${KIND[t.kind]}">${esc(KIND[t.kind])}</span>` : '';
    return `<a class="card" href="${encodeURIComponent(t.slug)}/" data-slug="${esc(t.slug)}">
      <div class="top"><span class="name">${esc(t.name || t.slug)}</span>${kind}</div>
      ${stats ? `<dl class="stats">${stats}</dl>` : '<p class="muted small">Counts not published for this area.</p>'}
      <div class="open">Open the map →</div>
      ${s.generated ? `<div class="muted small gen">Built ${esc(s.generated)}${s.version ? ` · dataset <span class="mono">${esc(s.version)}</span>` : ''}</div>` : ''}
    </a>`;
  }

  function render(list, el) {
    const ok = Array.isArray(list) ? list.filter(t => t && typeof t.slug === 'string' && t.slug) : [];
    if (!ok.length) { el.innerHTML = '<div class="empty">No areas are published yet.</div>'; return 0; }
    el.innerHTML = ok.map(cardHtml).join('');
    return ok.length;
  }

  // Old root links carried #pole=<id> and were forwarded to the first territory; keep that working.
  function forwardPoleLink(list, loc) {
    if (!/[#&]pole=/.test(loc.hash || '') || !Array.isArray(list) || !list.length) return null;
    return `${encodeURIComponent(list[0].slug)}/${loc.hash}`;
  }

  function init() {
    const el = document.getElementById('cards');
    if (!el || typeof fetch !== 'function') return;
    fetch('territories.json', { cache: 'no-cache' }).then(r => r.ok ? r.json() : null).then(list => {
      const to = forwardPoleLink(list, location);
      if (to) { location.replace(to); return; }
      render(list, el);
    }).catch(() => render([], el));
  }

  if (typeof window !== 'undefined') { window.PoleLanding = { render, forwardPoleLink, cardHtml }; if (typeof document !== 'undefined' && document.getElementById) init(); }
})();
