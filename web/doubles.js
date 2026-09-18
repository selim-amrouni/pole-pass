/* Pole Pass · candidate double poles (unlisted). Table + map over data.js; filtering, sorting, the
   reviewer's confirmed/cleared/not-a-double decisions, and CSV export all happen here in the
   browser, same split of responsibility as the main product's app.js + predicates.js. Review
   decisions live in this browser only (localStorage), keyed by pair_id -- pair ids are content
   hashes of the two feature ids (see doubles.py make_pair_id), so unlike pole ids in the main
   product they do not renumber between runs and need no dataset-version scoping. */
(function () {
  'use strict';
  const D = window.DOUBLES_DATA;
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const byId = Object.fromEntries(D.records.map(r => [r.id, r]));

  // ---------- review decisions (local to this browser) ----------
  const RKEY = `doubles-review:${D.meta.slug}`;
  function loadReview() { try { return JSON.parse(localStorage.getItem(RKEY) || '{}'); } catch (e) { return {}; } }
  function saveReview(obj) { try { localStorage.setItem(RKEY, JSON.stringify(obj)); } catch (e) { /* storage unavailable */ } }
  let review = loadReview();
  const REVIEW_OPTS = [['confirmed', 'Confirmed'], ['cleared', 'Cleared'], ['not_a_double', 'Not a double']];

  // ---------- state ----------
  const state = { maintainer: 'all', minConf: 0, dup: 'all', depth: 'all' };
  let filtered = [], mapApi = null;

  // ---------- shared predicates (assessed-ness, category, sort, filter) ----------
  const isAssessable = status => status === 'ok' || status === 'classified';  // a crop exists / existed; "ok" may still be awaiting classification
  function category(r) {
    if (!isAssessable(r.status)) return 'notassessed';
    if (!r.result) return 'pending';
    return r.result.is_double_pole ? 'double' : 'notdouble';
  }
  function tierOf(r) {
    if (!isAssessable(r.status)) return 2;       // no_shared_frame, too_small, or an upstream error/drop -- never hidden, sinks last
    if (!r.result) return 1;                     // "ok" but classification hasn't reached it yet
    return r.result.is_double_pole ? 0 : 1;       // true doubles first
  }
  function confOf(r) { return r.result ? r.result.confidence : -1; }
  // Default sort: is_double_pole true first, then confidence descending, then not-assessed rows last.
  function cmpDefault(a, b) {
    const ta = tierOf(a), tb = tierOf(b);
    if (ta !== tb) return ta - tb;
    const ca = confOf(a), cb = confOf(b);
    if (ca !== cb) return cb - ca;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  }
  function applyFilters(records) {
    return records.filter(r => {
      if (state.maintainer !== 'all' && r.maintainer !== state.maintainer) return false;
      if (state.minConf > 0 && (!r.result || r.result.confidence < state.minConf)) return false;
      if (state.dup === 'hide' && r.result && r.result.likely_duplicate_detection) return false;
      if (state.dup === 'only' && !(r.result && r.result.likely_duplicate_detection)) return false;
      if (state.depth === 'hide' && r.result && r.result.poles_at_different_depths) return false;
      if (state.depth === 'only' && !(r.result && r.result.poles_at_different_depths)) return false;
      return true;
    });
  }

  const STATUS_REASON = {
    no_shared_frame: 'No photo shows both flagged points together.',
    too_small: 'Poles are too small in the available photo to assess.',
    feature_not_cached: 'Underlying map feature not fetched yet.',
    dropped: 'Model output could not be parsed for this pair.',
  };
  function reasonText(r) {
    if (r.result) return r.result.reason;
    if (isAssessable(r.status)) return 'Awaiting classification.';
    if (r.status && r.status.indexOf('error') === 0) return 'Error while preparing this pair.';
    return STATUS_REASON[r.status] || `Not assessed (${r.status}).`;
  }
  function confidenceText(r) {
    if (r.result) return r.result.confidence.toFixed(2);
    return isAssessable(r.status) ? 'pending' : 'not assessed';
  }

  // ---------- stats ----------
  function renderStats() {
    const c = D.meta.counts, cov = D.meta.coverage;
    const pct = f => f == null ? '?' : `${(f * 100).toFixed(1)}%`;
    const pending = c.total - c.classified;
    $('stats').innerHTML = `
      <div class="stat"><span class="n mono">${c.total}</span><span class="l">Candidate pairs screened</span></div>
      <div class="stat"><span class="n mono">${c.classified}</span><span class="l">Classified so far${pending ? ` (${pending} pending)` : ''}</span></div>
      <div class="stat"><span class="n mono">${c.is_double_pole}</span><span class="l">Model says real double${pending ? ' &middot; of those classified so far' : ''}</span></div>
      <div class="stat mmld"><span class="n mono">${c.by_maintainer.MMLD || 0}</span><span class="l">MMLD candidates</span></div>
      <div class="stat verizon"><span class="n mono">${c.by_maintainer.VERIZON || 0}</span><span class="l">Verizon candidates</span></div>
      <div class="stat"><span class="n mono">${c.by_maintainer.UNCERTAIN || 0}</span><span class="l">Uncertain &middot; within ${esc(String(D.meta.split.buffer_m))} m of the line</span></div>
      <div class="coverage-note">Road coverage within 20 m of a photo: <b>MMLD ${pct(cov.mmld)}</b> &middot; <b>Verizon ${pct(cov.verizon)}</b> &middot; overall <b>${pct(cov.overall)}</b>.
        ${cov.verizon_recent_zero ? 'The Verizon half has <b>no photograph coverage from 2024 onward</b> &mdash; ' : ''}
        A lower candidate count on one side of town reflects thinner photo coverage there, not necessarily fewer double poles: read the counts above next to these percentages, not on their own.</div>`;
  }

  // ---------- table ----------
  function maintainerCell(r) {
    if (r.maintainer === 'MMLD') return `<span class="pill mmld">MMLD</span>`;
    if (r.maintainer === 'VERIZON') return `<span class="pill verizon">Verizon</span>`;
    return `<span class="dash" title="Within ${esc(String(D.meta.split.buffer_m))} m of the traced maintenance line, so the maintainer is not inferred.">&mdash;</span>`;
  }
  // A pole sits in the public right of way, but an address attaches a double-pole flag to a named
  // person's house for a public-infrastructure item that isn't theirs -- so this cell only ever
  // shows the nearest named road (street/cross_street from doubles.py), never a reverse geocode.
  function streetCell(r) {
    const s = r.street ? esc(r.street) : '<span class="muted">Unnamed road</span>';
    const c = r.cross_street ? ` &times; ${esc(r.cross_street)}` : '';
    return `${s}${c}`;
  }
  function thumbCell(r) {
    if (r.crop) return `<a href="${esc(r.url)}" target="_blank" rel="noopener"><img class="thumb" src="${esc(r.crop)}" alt="Crop of candidate ${esc(r.id)}"></a>`;
    if (r.url) return `<a class="lnk" href="${esc(r.url)}" target="_blank" rel="noopener">Open photo</a>`;
    return `<span class="na">No photo</span>`;
  }
  // Google Maps links, for a reviewer who wants to stand somewhere or check the pair against a
  // second, independent set of photographs. Street View matters more than the map pin here: it is
  // imagery from a different provider on a different date, so it is the cheapest way to test whether
  // a double this page found from Mapillary is still standing.
  function mapsCell(r) {
    const q = `${r.lat.toFixed(6)},${r.lon.toFixed(6)}`;
    const pin = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(q)}`;
    const pano = `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${encodeURIComponent(q)}`;
    return `<span class="maps"><a class="lnk" href="${pin}" target="_blank" rel="noopener"
        title="Open this location in Google Maps">Map</a><a class="lnk" href="${pano}" target="_blank"
        rel="noopener" title="Open Google Street View here — imagery from a different provider on a different date, so it is an independent check on whether the pair is still standing">Street View</a></span>`;
  }

  function reviewCell(r) {
    const v = (review[r.id] || {}).decision || '';
    return `<span class="seg" role="group" aria-label="Reviewer decision for ${esc(r.id)}">${REVIEW_OPTS.map(([k, label]) =>
      `<button class="${k}" data-rid="${esc(r.id)}" data-rv="${k}" aria-pressed="${v === k}">${label}</button>`).join('')}</span>`;
  }
  const CAT_PILL = { double: ['double', 'Double'], notdouble: ['notdouble', 'Not a double'], pending: ['', 'Pending'], notassessed: ['notassessed', 'Not assessed'] };
  // The gap shown is the MODEL's visual estimate, not the distance between the two map features.
  // Feature positions are triangulated from photographs and depth along the camera ray is the
  // least-constrained axis, so two poles 40 m apart down the same street can be recorded a metre
  // apart. The map separation is kept in the tooltip so the two can be compared, but it is not the
  // number a reviewer should trust.
  function gapCell(r) {
    const est = r.result && typeof r.result.separation_estimate_m === 'number' ? r.result.separation_estimate_m : null;
    const mapped = typeof r.distance_m === 'number' ? r.distance_m.toFixed(2) + ' m' : '?';
    if (est === null) return `<span class="muted" title="Map separation ${esc(mapped)}. Not assessed, so there is no visual estimate.">&mdash;</span>`;
    const wide = est > 3 ? ' wide' : '';
    return `<span class="gap${wide}" title="Model's visual estimate. Separation between the two map features is ${esc(mapped)}, which is less reliable.">${est.toFixed(1)} m</span>`;
  }

  function rowHtml(r) {
    const [cls, label] = CAT_PILL[category(r)];
    return `<tr data-id="${esc(r.id)}">
      <td class="mono">${esc(r.id)}</td>
      <td class="mono">${r.lat.toFixed(6)}</td>
      <td class="mono">${r.lon.toFixed(6)}</td>
      <td>${streetCell(r)}</td>
      <td class="mono">${gapCell(r)}</td>
      <td>${maintainerCell(r)}</td>
      <td class="mono">${esc(r.capture_first || '?')}</td>
      <td class="mono">${esc(r.capture_last || '?')}</td>
      <td class="mono">${confidenceText(r)}</td>
      <td class="reason"><span class="pill ${cls}">${label}</span> ${esc(reasonText(r))}</td>
      <td>${thumbCell(r)}</td>
      <td>${mapsCell(r)}</td>
      <td>${reviewCell(r)}</td>
    </tr>`;
  }
  function renderCount() {
    $('count').innerHTML = `<span class="mono">${filtered.length}</span> of <span class="mono">${D.records.length}</span> candidates shown`;
  }
  function renderTable() {
    $('tbody').innerHTML = filtered.length ? filtered.map(rowHtml).join('')
      : `<tr><td colspan="13" class="muted" style="padding:16px">No candidates match these filters.</td></tr>`;
  }
  function refresh() {
    filtered = applyFilters(D.records).sort(cmpDefault);
    renderCount();
    renderTable();
    if (mapApi) mapApi.setData(filtered);
  }

  // ---------- row <-> map selection ----------
  function selectRow(id, fromMap) {
    document.querySelectorAll('#tbody tr').forEach(tr => tr.classList.toggle('sel', tr.dataset.id === id));
    if (!fromMap) {
      const tr = document.querySelector(`#tbody tr[data-id="${CSS.escape(id)}"]`);
      if (tr) tr.scrollIntoView({ block: 'nearest' });
    }
    if (mapApi) mapApi.select(byId[id], fromMap);
  }

  // ---------- map ----------
  const CAT_COLOR = { double: '#c2410c', notdouble: '#3f6b2a', pending: '#1b5e8a', notassessed: '#bcbcb7' };
  function webglOk() { try { const c = document.createElement('canvas'); return !!(c.getContext('webgl2') || c.getContext('webgl') || c.getContext('experimental-webgl')); } catch (e) { return false; } }
  function mapState(kind, text) {
    const el = $('map-state'); if (!el) return;
    el.hidden = !kind; el.className = `map-state ${kind || ''}`;
    el.innerHTML = kind ? `<div><span>${esc(text)}</span>${kind === 'failed' ? ' <button class="btn" id="map-retry">Retry</button>' : ''}</div>` : '';
  }
  function renderLegend() {
    $('legend').innerHTML = `
      <div><i style="background:${CAT_COLOR.double}"></i>Model: real double</div>
      <div><i style="background:${CAT_COLOR.notdouble}"></i>Model: not a double</div>
      <div><i style="background:${CAT_COLOR.pending}"></i>Awaiting classification</div>
      <div><i style="background:${CAT_COLOR.notassessed}"></i>Not assessed</div>
      <div><span class="sw" style="background:#2856c6"></span>MMLD half</div>
      <div><span class="sw" style="background:#b5720f"></span>Verizon half</div>`;
  }
  function initMap() {
    const fail = why => { mapState('failed', why); const lg = $('legend'); if (lg) lg.hidden = true; return { failed: true, setData() {}, select() {} }; };
    if (typeof window.maplibregl === 'undefined') return fail(window.__maplibreFailed ? 'The map library could not be loaded (blocked or offline). The table and exports still work.' : 'The map library did not load.');
    if (!webglOk()) return fail('This browser has no WebGL, which the map needs. The table and exports still work.');
    let map;
    try {
      map = new maplibregl.Map({
        container: 'map', center: D.meta.center, zoom: 12.5, attributionControl: true,
        style: { version: 8, sources: { osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256, attribution: '© OpenStreetMap contributors' } },
                 layers: [{ id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-saturation': -0.6, 'raster-opacity': 0.9 } }] },
      });
    } catch (e) { return fail('The map could not start. The table and exports still work.'); }
    mapState('loading', 'Loading map…');
    let ready = false, pendingSel = null;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-left');
    map.on('error', () => { /* tile errors: the map stays usable, no need to surface each one on this internal page */ });
    const toFC = rows => ({ type: 'FeatureCollection', features: rows.map(r => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [r.lon, r.lat] }, properties: { id: r.id, cat: category(r) } })) });
    const halvesFC = { type: 'FeatureCollection', features: [
      { type: 'Feature', properties: { maintainer: 'MMLD' }, geometry: { type: 'Polygon', coordinates: [D.meta.split.mmld_ring] } },
      { type: 'Feature', properties: { maintainer: 'VERIZON' }, geometry: { type: 'Polygon', coordinates: [D.meta.split.verizon_ring] } },
    ] };
    const lineFC = { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: D.meta.split.line } }] };
    map.on('load', () => {
      ready = true;
      map.addSource('halves', { type: 'geojson', data: halvesFC });
      map.addLayer({ id: 'halves-fill', type: 'fill', source: 'halves', paint: { 'fill-color': ['match', ['get', 'maintainer'], 'MMLD', '#2856c6', 'VERIZON', '#b5720f', '#999'], 'fill-opacity': 0.10 } });
      map.addSource('splitline', { type: 'geojson', data: lineFC });
      map.addLayer({ id: 'splitline', type: 'line', source: 'splitline', paint: { 'line-color': '#c2410c', 'line-width': 2, 'line-dasharray': [3, 2] } });
      map.addSource('pts', { type: 'geojson', data: toFC(filtered) });
      map.addSource('sel', { type: 'geojson', data: toFC([]) });
      map.addLayer({ id: 'pts', type: 'circle', source: 'pts', paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 3.5, 15, 6, 18, 9], 'circle-color': ['match', ['get', 'cat'], ...Object.entries(CAT_COLOR).flat(), '#ffffff'], 'circle-stroke-color': '#1c1c1a', 'circle-stroke-width': 1.1 } });
      map.addLayer({ id: 'sel', type: 'circle', source: 'sel', paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 9, 18, 16], 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#1b5e8a', 'circle-stroke-width': 3 } });
      map.on('click', 'pts', e => selectRow(e.features[0].properties.id, true));
      map.on('mouseenter', 'pts', () => map.getCanvas().style.cursor = 'pointer');
      map.on('mouseleave', 'pts', () => map.getCanvas().style.cursor = '');
      if (D.meta.bbox) map.fitBounds(D.meta.bbox, { padding: 24, duration: 0 });
      mapState(null);
      renderLegend();
      if (pendingSel) api.select(pendingSel);
    });
    const api = {
      setData(rows) { if (!ready) return; map.getSource('pts').setData(toFC(rows)); },
      select(r, fromMap) {
        if (!ready) { pendingSel = r; return; }
        map.getSource('sel').setData(toFC(r ? [r] : []));
        if (r) map.easeTo({ center: [r.lon, r.lat], zoom: Math.max(map.getZoom(), 15), duration: fromMap ? 0 : 300 });
      },
    };
    return api;
  }

  // The spot check is shown with its limits stated first, in the same breath as its result. Four
  // crops judged by the same system that produced the calls is not a validation, and presenting the
  // agree/doubt tally without that sentence attached would read as one.
  function spotcheckHtml(m) {
    const sc = m.spotcheck;
    if (!sc || !Array.isArray(sc.sample) || !sc.sample.length) return '';
    const agree = sc.sample.filter(s => s.verdict === 'agree').length;
    return `<p class="spotcheck"><b>A spot check, not a validation.</b> No independently graded sample
      exists for these double-pole calls. ${esc(String(sc.sample.length))} of the crops called a real
      double were re-opened and judged by eye by the same system that made the calls, which agreed
      with ${esc(String(agree))} of them. That is far too small a sample, not randomly drawn, and not
      independent &mdash; treat it as evidence the pipeline is not grossly broken, and nothing more.
      ${sc.pattern_worth_acting_on ? esc(sc.pattern_worth_acting_on) : ''}</p>`;
  }

  // ---------- methods note ----------
  function renderMethods() {
    const m = D.meta;
    $('methods').innerHTML = `
      <h3 id="methods-h">Methods and limits</h3>
      <p>${esc(m.validation.notice)}</p>
      <p>Imagery behind this page spans <span class="mono">${esc(m.coverage.capture_first || '?')}</span> to <span class="mono">${esc(m.coverage.capture_last || '?')}</span>.
        The underlying map-feature and image tiles were fetched ${m.methods.coverage_generated_at ? `<span class="mono">${esc(m.methods.coverage_generated_at)}</span>` : 'at an unrecorded date'}.
        This page was generated <span class="mono">${esc(m.generated)}</span>, dataset <span class="mono">${esc(m.version)}</span>.</p>
      <p>Pairs were screened by finding utility-pole map features within ${esc(String(m.methods.radius_m))} m of each other on the ground &mdash;
        a geometric screen that is deliberately over-inclusive, because the upstream detector reports the same physical pole 1.2 to 1.4 times on
        average. A vision model then looked at up to ${esc(String(m.methods.frames_kept))} shared photo(s) per pair and judged whether it shows one
        pole detected twice, two real poles at different distances down the same street, or a genuine double pole (an old pole left standing beside
        its replacement). None of these calls has been checked yet against a human-graded sample.</p>
      <p>${esc(m.split.note)} Coordinates are averaged detection positions, not surveyed.
        ${m.contact ? `Spot something wrong? <a href="${esc(m.contact)}">Send a correction</a>.` : ''}</p>
      ${spotcheckHtml(m)}`;
  }

  // ---------- export (filtered rows, with reviewer decisions) ----------
  const CSV_COLS = ['pair_id', 'lat', 'lon', 'street', 'cross_street', 'maintainer', 'capture_first', 'capture_last',
    'status', 'confidence', 'likely_duplicate_detection', 'is_double_pole', 'poles_at_different_depths', 'reason',
    'chosen_mapillary_url', 'review'];
  function csvRow(r) {
    const res = r.result;
    const v = [r.id, r.lat, r.lon, r.street || '', r.cross_street || '', r.maintainer, r.capture_first || '', r.capture_last || '',
      r.status, res ? res.confidence : '', res ? res.likely_duplicate_detection : '', res ? res.is_double_pole : '',
      res ? res.poles_at_different_depths : '', reasonText(r), r.url || '', (review[r.id] || {}).decision || ''];
    return v.map(x => { const s = String(x ?? ''); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; }).join(',');
  }
  function download(name, text, type) { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; document.body.appendChild(a); a.click(); a.remove(); }
  function exportCsv() {
    download(`doubles-${D.meta.slug}-filtered.csv`, [CSV_COLS.join(','), ...filtered.map(csvRow), '', D.meta.attribution,
      'Candidate double poles from public imagery, ungraded. review column: decisions made in this browser only, not shared, not independently validated.'].join('\n'), 'text/csv');
  }

  // ---------- wiring ----------
  function resetFilters() {
    Object.assign(state, { maintainer: 'all', minConf: 0, dup: 'all', depth: 'all' });
    $('f-maintainer').value = 'all'; $('f-confidence').value = '0'; $('f-confidence-n').textContent = '0.00';
    $('f-dup').value = 'all'; $('f-depth').value = 'all';
    refresh();
  }
  function wire() {
    $('f-maintainer').addEventListener('change', e => { state.maintainer = e.target.value; refresh(); });
    $('f-confidence').addEventListener('input', e => { state.minConf = parseFloat(e.target.value); $('f-confidence-n').textContent = state.minConf.toFixed(2); refresh(); });
    $('f-dup').addEventListener('change', e => { state.dup = e.target.value; refresh(); });
    $('f-depth').addEventListener('change', e => { state.depth = e.target.value; refresh(); });
    $('reset-filters').addEventListener('click', resetFilters);
    $('export-csv').addEventListener('click', exportCsv);
    $('tbody').addEventListener('click', e => {
      const btn = e.target.closest('button[data-rid]');
      if (btn) {
        const id = btn.dataset.rid, v = btn.dataset.rv;
        const cur = review[id];
        if (cur && cur.decision === v) delete review[id]; else review[id] = { decision: v, updated: new Date().toISOString() };
        saveReview(review);
        const tr = btn.closest('tr'); if (tr) tr.lastElementChild.innerHTML = reviewCell(byId[id]);
        return;
      }
      const tr = e.target.closest('tr[data-id]');
      if (tr) selectRow(tr.dataset.id, false);
    });
    document.addEventListener('click', e => { if (e.target.id === 'map-retry') { mapApi = initMap(); if (mapApi) mapApi.setData(filtered); } });
  }

  // ---------- init ----------
  function init() {
    renderStats();
    renderMethods();
    wire();
    refresh();
    mapApi = initMap();
  }
  try { init(); } catch (e) { $('count').textContent = 'The page failed to initialize.'; console.error(e); }
})();
