/* Pole Pass front end. Runs without the map: data, list, filters, details, and exports
   initialize first; the map is attempted afterwards and falls back to a message. */
(function () {
  'use strict';
  const D = window.POLE_DATA;
  const PP = window.PP;
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // ---------- labels (internal enums stay out of the UI) ----------
  const L = {
    type: { wood_utility: 'Wood utility pole', concrete_or_steel_utility: 'Concrete or steel utility pole', street_light: 'Street light pole', traffic_signal: 'Traffic signal pole', other: 'Other object', unclear: 'Pole type unclear' },
    lean: { none: 'No lean visible', slight: 'Slight lean', moderate: 'Possible lean (moderate)', severe: 'Possible lean (severe)', unclear: 'Cannot tell' },
    xarm: { none_visible: 'No crossarm visible', intact: 'Crossarm looks intact', damaged: 'Possible crossarm damage', unclear: 'Cannot tell' },
    veg: { none: 'No vegetation contact visible', near: 'Vegetation nearby', touching: 'Possible vegetation contact', unclear: 'Cannot tell' },
    mat: { wood: 'Wood', concrete: 'Concrete', steel: 'Steel', fiberglass: 'Fiberglass', unclear: 'Cannot tell' },
    flag: { lean: 'Possible lean', crossarm: 'Possible crossarm damage', vegetation: 'Possible vegetation contact', att3: '3+ estimated attachments', xfmr: 'Transformer visible' },
    review: { supported: 'Flag supported', not_supported: 'Flag not supported', cannot_tell: 'Cannot tell from photos' },
  };
  const attLabel = r => !PP.isUtility(r) ? 'Not assessed' : Number.isInteger(r.att) ? `${r.att} estimated attachment${r.att === 1 ? '' : 's'}` : 'Cannot tell';
  const xfmrLabel = r => r.xfmr === true ? 'Transformer visible' : r.xfmr === false ? 'No transformer visible' : 'Cannot tell';
  const dateLabel = d => d && d.date ? d.date : 'Date unknown';

  // ---------- state ----------
  const state = { flag: 'all', yearMin: null, yearMax: null, recent: null, other: false, sort: 'date_desc', page: 1, selected: null, viewing: null, compare: false, colorMode: 'condition', tab: 'list', example: false };
  const PAGE = 50;
  const byId = Object.fromEntries(D.records.map(r => [r.id, r]));
  const yearsAll = D.records.filter(PP.isUtility).map(r => r.shown.year).filter(y => y != null);
  const Y0 = yearsAll.length ? Math.min(...yearsAll) : null, Y1 = yearsAll.length ? Math.max(...yearsAll) : null;
  const THIS_YEAR = new Date(D.meta.generated).getUTCFullYear();
  let filtered = [], mapApi = null;

  // ---------- review decisions, local to this browser, scoped to the dataset version ----------
  const RKEY = `polepass-review:${D.meta.slug}:${D.meta.version}`;
  function loadReview() { try { return JSON.parse(localStorage.getItem(RKEY) || '{}'); } catch (e) { return {}; } }
  function saveReview(obj) { try { localStorage.setItem(RKEY, JSON.stringify(obj)); } catch (e) { /* storage unavailable */ } }
  let review = loadReview();
  const reviewStatus = r => {
    const v = review[r.id]; if (!v || !v.flags) return null;
    const vals = Object.values(v.flags).filter(Boolean); if (!vals.length) return null;
    if (vals.every(x => x === 'supported')) return 'ok'; if (vals.some(x => x === 'not_supported')) return 'no'; return 'ct';
  };
  const reviewStatusLabel = r => ({ ok: 'Flag supported', no: 'Flag not supported', ct: 'Cannot tell from photos' }[reviewStatus(r)] || 'Not reviewed');

  // ---------- summary ----------
  function renderSummary() {
    const s = PP.summary(D.records);
    const dates = s.yearMin == null ? 'Unknown' : s.yearMin === s.yearMax ? String(s.yearMin) : `${s.yearMin} to ${s.yearMax}`;
    $('summary').innerHTML = [
      tile(s.utility, 'Poles identified', 'Model estimate'),
      tile(`${s.conditionIssues} of ${s.utility}`, 'Possible condition issues', 'Possible lean, crossarm damage, or vegetation contact'),
      tile(`${s.attachments3} of ${s.utility}`, '3+ estimated attachments', 'Visible non-electric attachments'),
      tile(dates, 'Source photo dates', s.undated ? `Photos shown for the ${s.utility} poles. ${s.undated} without a date.` : `Photos shown for the ${s.utility} poles`),
    ].join('');
  }
  const tile = (v, l, q) => `<div class="tile"><div class="v">${esc(v)}</div><div class="l">${esc(l)}</div>${q ? `<div class="q">${esc(q)}</div>` : ''}</div>`;

  // ---------- filters ----------
  const CHIPS = [['all', 'All poles', ''], ['lean', 'Possible lean', 'issue'], ['xarm', 'Possible crossarm damage', 'issue'], ['veg', 'Possible vegetation contact', 'issue'], ['att3', '3+ estimated attachments', ''], ['xfmr', 'Transformer visible', '']];
  function renderChips() {
    const base = D.records.filter(r => state.other ? !PP.isUtility(r) : PP.isUtility(r));
    $('chips').innerHTML = CHIPS.map(([k, label, cls]) => {
      const n = base.filter(PP.FILTERS[k]).length;
      return `<button class="chip ${cls}" data-f="${k}" aria-pressed="${state.flag === k}">${esc(label)}<span class="n">${n}</span></button>`;
    }).join('');
  }
  function readYearInputs() {
    const a = parseInt($('year-min').value, 10), b = parseInt($('year-max').value, 10);
    state.yearMin = Number.isFinite(a) && (Y0 == null || a > Y0) ? a : null;
    state.yearMax = Number.isFinite(b) && (Y1 == null || b < Y1) ? b : null;
  }
  function resetFilters() {
    Object.assign(state, { flag: 'all', yearMin: null, yearMax: null, recent: null, other: false, page: 1 });
    $('year-min').value = Y0 ?? ''; $('year-max').value = Y1 ?? ''; $('recent').setAttribute('aria-pressed', 'false'); $('other').checked = false;
    refresh();
  }

  // ---------- list ----------
  function refresh(keepPage) {
    if (!keepPage) state.page = 1;
    filtered = PP.applyFilters(D.records, state).sort(PP.SORTS[state.sort]);
    renderChips();
    renderList();
    if (mapApi) mapApi.setData(filtered);
    updateDetailNav();
  }
  function rowHtml(r) {
    const flags = PP.conditionFlags(r).map(f => `<span class="flag issue">${L.flag[f]}</span>`);
    if (PP.attachments3(r)) flags.push(`<span class="flag att">3+ attachments</span>`);
    if (PP.transformerVisible(r)) flags.push(`<span class="flag">Transformer</span>`);
    if (!flags.length) flags.push(PP.conditionUnclear(r) ? `<span class="flag dim">Cannot tell</span>` : `<span class="flag dim">No model flag</span>`);
    const st = reviewStatus(r);
    const img = r.shown.img ? `<img src="${esc(r.shown.img)}" alt="" loading="lazy">` : `<span class="ph">No photo</span>`;
    return `<button class="row" role="option" data-id="${esc(r.id)}" aria-selected="${state.selected === r.id}">${img}
      <span><span class="pid">${esc(r.id)}</span><span class="t">${PP.isUtility(r) ? esc(L.type[r.type] || r.type) : esc(L.type[r.type] || 'Other object')}</span><br><span class="s">${flags.join('')}</span></span>
      <span class="r"><span class="d">${esc(dateLabel(r.shown))}</span><br>${esc(attLabel(r).replace(' estimated', ''))}<br><span class="status ${st || ''}">${esc(reviewStatusLabel(r))}</span></span></button>`;
  }
  function renderList() {
    const total = filtered.length, shown = Math.min(total, state.page * PAGE);
    $('count').innerHTML = `Showing <span class="mono">${shown}</span> of <span class="mono">${total}</span> matching ${state.other ? 'objects' : 'poles'}`;
    if (!total) { $('list').innerHTML = `<div class="empty">No poles match these filters. <button class="btn sm" id="reset2">Reset filters</button></div>`; return; }
    $('list').innerHTML = filtered.slice(0, shown).map(rowHtml).join('') + (shown < total ? `<div class="more"><button class="btn sm" id="more">Show more (${total - shown} left)</button></div>` : '');
  }

  // ---------- detail ----------
  function select(id, opts = {}) {
    const r = byId[id]; if (!r) return false;
    state.selected = id; state.viewing = r.frames.findIndex(f => f.shown); if (state.viewing < 0) state.viewing = r.frames.length - 1;
    state.compare = false; state.example = !!opts.example;
    if (!opts.silent) history.replaceState(null, '', `#pole=${encodeURIComponent(id)}`);
    document.querySelectorAll('.row').forEach(el => el.setAttribute('aria-selected', String(el.dataset.id === id)));
    renderDetail();
    $('ws').classList.add('has-detail');
    if (mapApi) mapApi.select(r, opts.fromMap);
    if (opts.focus !== false) { const h = $('detail').querySelector('.detail-h button'); if (h) h.focus(); }
    return true;
  }
  function close() {
    state.selected = null; state.example = false;
    $('ws').classList.remove('has-detail'); $('detail').hidden = true; $('detail').innerHTML = '';
    history.replaceState(null, '', location.pathname + location.search);
    document.querySelectorAll('.row').forEach(el => el.setAttribute('aria-selected', 'false'));
    if (mapApi) mapApi.select(null);
    const row = document.querySelector('.row'); if (row) row.focus();
  }
  function step(delta) {
    const i = filtered.findIndex(r => r.id === state.selected); if (i < 0) return;
    const j = i + delta; if (j < 0 || j >= filtered.length) return;
    if (j >= state.page * PAGE) { state.page = Math.ceil((j + 1) / PAGE); renderList(); }
    select(filtered[j].id);
    const row = document.querySelector(`.row[data-id="${CSS.escape(filtered[j].id)}"]`); if (row) row.scrollIntoView({ block: 'nearest' });
  }
  function updateDetailNav() {
    const i = filtered.findIndex(r => r.id === state.selected);
    const p = $('prev'), n = $('next'); if (!p || !n) return;
    p.disabled = i <= 0; n.disabled = i < 0 || i >= filtered.length - 1;
    const pos = $('pos'); if (pos) pos.textContent = i >= 0 ? `${i + 1} of ${filtered.length}` : 'Not in current list';
  }
  function agreeText(r, key) {
    const v = r.votes[key]; if (!v) return '';
    if (r.n === 1) return 'Single photo';
    const win = Object.entries(v).sort((a, b) => b[1] - a[1])[0];
    return `${win[1]} of ${r.n} photos`;
  }
  function frameObs(f) {
    return [L.lean[f.lean] || f.lean, L.xarm[f.xarm] || f.xarm, L.veg[f.veg] || f.veg, f.xfmr ? 'Transformer visible' : 'No transformer visible', Number.isInteger(f.att) ? `${f.att} estimated attachment${f.att === 1 ? '' : 's'}` : 'Attachments: cannot tell'];
  }
  function renderDetail() {
    const r = byId[state.selected]; if (!r) return;
    const f = r.frames[state.viewing] || null;
    const util = PP.isUtility(r);
    const flags = [...PP.conditionFlags(r), PP.attachments3(r) && 'att3', PP.transformerVisible(r) && 'xfmr'].filter(Boolean);
    const rv = review[r.id] || { flags: {}, note: '' };
    const photo = f && f.img ? `<img id="dimg" src="${esc(f.img)}" alt="Photo of ${esc(r.id)} taken ${esc(dateLabel(f))}">`
      : `<div class="photo-missing">Photo unavailable.${f && f.url ? ` <a href="${esc(f.url)}" target="_blank" rel="noopener">Open source photo</a>` : ''}</div>`;
    const others = r.frames.length > 1 ? `<div class="sec"><h3>Other photos</h3><p class="small muted" style="margin:0 0 6px">${r.frames.length} photos from ${r.seq} capture sequence${r.seq === 1 ? '' : 's'}. Photos from one drive are related, not independent.</p>
      <div class="thumbs">${r.frames.map((x, i) => `<button data-i="${i}" aria-pressed="${i === state.viewing}" aria-label="View photo from ${esc(dateLabel(x))}">${x.img ? `<img src="${esc(x.img)}" alt="">` : `<span class="ph" style="width:80px;height:80px;display:grid;place-items:center;font-size:11px">No image</span>`}<span class="c">${esc(x.date || '?')}</span></button>`).join('')}</div>
      <p style="margin:8px 0 0"><button class="btn sm" id="cmp" aria-pressed="${state.compare}">Compare photos</button></p>
      ${state.compare ? compareHtml(r) : ''}</div>` : '';
    const latest = r.latest && r.latest.ts && (!f || r.latest.ts > (f.ts || 0)) ? `<div class="k">Latest available photo</div><div>${esc(dateLabel(r.latest))}${r.latest.classified ? '' : ', not assessed (pole too small in frame)'}${r.latest.url ? ` · <a href="${esc(r.latest.url)}" target="_blank" rel="noopener">Open source photo</a>` : ''}</div>` : '';
    $('detail').innerHTML = `
      <div class="detail-h"><button class="btn sm" id="back" aria-label="Back to list">← Back to list</button><span class="id">${esc(r.id)}</span>
        <div class="nav"><span class="small muted" id="pos"></span><button class="btn sm" id="prev" aria-label="Previous pole">Prev</button><button class="btn sm" id="next" aria-label="Next pole">Next</button><button class="btn sm" id="close" aria-label="Close details">Close</button></div></div>
      ${state.example ? `<div class="example-tag">Selected example. Pick any pole from the list or map, or close this to return to the overview.</div>` : ''}
      <div class="photo">${photo}
        <div class="cap"><span><span class="muted">Photo taken</span> <b>${esc(dateLabel(f))}</b>${f && f.pano ? ' · 360° photo' : ''}${f && f.shown ? (r.shown.newest ? ' · newest readable photo' : ' · clearest photo; newer photos too small') : ''}</span>
          ${f && f.url ? `<a href="${esc(f.url)}" target="_blank" rel="noopener">Open source photo</a>` : ''}${f && f.img ? `<button class="btn sm" id="enlarge">Enlarge</button>` : ''}${f && f.by ? `<span class="muted small">by ${esc(f.by)} (Mapillary)</span>` : ''}</div></div>
      ${f ? `<div class="sec"><h3>This photo's observation</h3><div class="small">${frameObs(f).map(esc).join(' · ')}</div>${f.note ? `<div class="small muted" style="margin-top:4px">Model note for this photo: ${esc(f.note)}</div>` : ''}</div>` : ''}
      <div class="sec"><h3>Model assessment${r.n > 1 ? ` (combined across ${r.n} photos)` : ''}</h3>
        <div class="kv">
          <div class="k">Type</div><div>${esc(L.type[r.type] || r.type)} <span class="agree">${esc(agreeText(r, 'type'))}</span></div>
          <div class="k">Lean</div><div>${esc(L.lean[r.lean] || r.lean)} <span class="agree">${esc(agreeText(r, 'lean'))}</span></div>
          <div class="k">Crossarm</div><div>${esc(L.xarm[r.xarm] || r.xarm)} <span class="agree">${esc(agreeText(r, 'xarm'))}</span></div>
          <div class="k">Vegetation</div><div>${esc(L.veg[r.veg] || r.veg)} <span class="agree">${esc(agreeText(r, 'veg'))}</span></div>
          <div class="k">Transformer</div><div>${esc(xfmrLabel(r))} <span class="agree">${esc(agreeText(r, 'xfmr'))}</span></div>
          <div class="k">Attachments</div><div>${esc(attLabel(r))} <span class="agree">${esc(agreeText(r, 'att'))}</span></div>
          <div class="k">Material</div><div>${esc(L.mat[r.material] || r.material)}</div>
          ${latest}
          <div class="k">Location</div><div class="mono small">${r.lat.toFixed(5)}, ${r.lon.toFixed(5)} <span class="agree">estimated from ${r.nfeat} detection${r.nfeat === 1 ? '' : 's'}</span></div>
        </div>
        <p class="small muted" style="margin:6px 0 0">Model agreement across photos: the number of photos whose result matches the combined value. ${r.n === 1 ? 'Only one photo was assessed.' : ''} An attachment is a visible non-electric item on the pole (cable bundle, box, riser, antenna). It says nothing about ownership.</p></div>
      ${others}
      ${util && flags.length ? `<div class="sec review"><h3>Your review of the model flags</h3>
        ${flags.map(k => `<div><div class="small"><b>${esc(L.flag[k])}</b></div><div class="opt" role="group" aria-label="Review ${esc(L.flag[k])}">${['supported', 'not_supported', 'cannot_tell'].map(v => `<button class="btn sm" data-rf="${k}" data-rv="${v}" aria-pressed="${rv.flags[k] === v}">${L.review[v]}</button>`).join('')}</div></div>`).join('')}
        <label class="small" for="rnote">Note (optional)</label><textarea id="rnote" maxlength="500">${esc(rv.note || '')}</textarea>
        <p class="scope">Saved in this browser only, for dataset version ${esc(D.meta.version)}. Not shared through links. Judges whether the photo supports the flag, not whether the pole is safe. <button class="btn sm" id="rreset">Reset review</button></p></div>` : ''}
      <details class="tech sec"><summary>Technical details</summary>
        <p class="small muted">Raw model outputs per photo. Self-rating is the model's own 0 to 1 confidence and is not calibrated. Notes are free text from the model and may overstate what a photo shows.</p>
        <table><thead><tr><th>Photo</th><th>Pole px</th><th>Type</th><th>Lean</th><th>Crossarm</th><th>Veg.</th><th>Xfmr</th><th>Att.</th><th>Self-rating</th><th>Note</th></tr></thead>
        <tbody>${r.frames.map(x => `<tr><td class="mono">${esc(x.date || '?')}${x.pano ? ' 360°' : ''}</td><td class="mono">${x.px ?? ''}</td><td>${esc(x.type)}</td><td>${esc(x.lean)}</td><td>${esc(x.xarm)}</td><td>${esc(x.veg)}</td><td>${x.xfmr ? 'yes' : 'no'}</td><td class="mono">${x.att ?? ''}</td><td class="mono">${x.conf ?? ''}</td><td>${esc(x.note)}</td></tr>`).join('')}</tbody></table>
        <p class="small muted">Mapillary feature ids: <span class="mono">${r.features.map(esc).join(', ')}</span>. Record id is a demo identifier, not a utility asset id. Grouping radius ${esc(D.meta.method.radius_m)} m; grouped detections can merge distinct objects or leave duplicates.</p></details>`;
    $('detail').hidden = false;
    updateDetailNav();
  }
  function compareHtml(r) {
    const a = r.frames[state.viewing], b = r.frames.find((x, i) => i !== state.viewing && x.img) || null;
    if (!a || !b || !a.img) return `<p class="small muted">Only one photo has an in-app image. Others are available through their source links above.</p>`;
    const fig = x => `<figure><img src="${esc(x.img)}" alt="Photo taken ${esc(dateLabel(x))}"><figcaption><b>${esc(dateLabel(x))}</b> · ${esc(frameObs(x).slice(0, 1)[0])}, ${esc(frameObs(x)[4])}</figcaption></figure>`;
    return `<div class="compare" style="margin-top:8px">${fig(a)}${fig(b)}</div><p class="small muted" style="margin:4px 0 0">Two photos of the same record. Differences are not confirmed changes; angle, distance, and camera differ.</p>`;
  }

  // ---------- map ----------
  function initMap() {
    const box = $('map');
    const fail = msg => { box.innerHTML = `<div class="map-fallback"><div>${esc(msg)}</div></div>`; $('legend').hidden = true; $('fit').hidden = true; $('resetview').hidden = true; };
    if (typeof window.maplibregl === 'undefined') { fail('The map could not load. You can still review photos and export the list.'); return null; }
    let map;
    try {
      map = new maplibregl.Map({ container: 'map', center: D.meta.center, zoom: 14.5, attributionControl: true,
        style: { version: 8, sources: { osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256, attribution: '© OpenStreetMap contributors' } },
                 layers: [{ id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-saturation': -0.6, 'raster-opacity': 0.9 } }] } });
    } catch (e) { fail('The map could not load. You can still review photos and export the list.'); return null; }
    const toFC = rows => ({ type: 'FeatureCollection', features: rows.map(r => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [r.lon, r.lat] }, properties: { id: r.id, cat: category(r) } })) });
    const category = r => !PP.isUtility(r) ? 'other' : state.colorMode === 'attachments' ? (Number.isInteger(r.att) ? (r.att >= 3 ? 'a3' : r.att >= 1 ? 'a1' : 'a0') : 'unclear')
      : PP.hasConditionIssue(r) ? 'issue' : PP.conditionUnclear(r) ? 'unclear' : 'none';
    const COLORS = { issue: '#c2410c', none: '#ffffff', unclear: '#e3e3df', other: '#bcbcb7', a3: '#2c6e6b', a1: '#9ccbc9', a0: '#ffffff' };
    let ready = false, pendingSel = null;
    map.on('error', e => { if (!ready && e && e.error && /style|source|Failed to fetch/i.test(String(e.error.message || e.error))) fail('The map could not load. You can still review photos and export the list.'); });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-left');
    map.on('load', () => {
      ready = true;
      map.addSource('poles', { type: 'geojson', data: toFC(filtered) });
      map.addSource('sel', { type: 'geojson', data: toFC([]) });
      map.addLayer({ id: 'poles', type: 'circle', source: 'poles', paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 13, 3.5, 16, 6, 18, 9], 'circle-color': ['match', ['get', 'cat'], ...Object.entries(COLORS).flat(), '#ffffff'], 'circle-stroke-color': '#1c1c1a', 'circle-stroke-width': 1.2, 'circle-opacity': ['match', ['get', 'cat'], 'other', 0.6, 1] } });
      map.addLayer({ id: 'sel', type: 'circle', source: 'sel', paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 13, 9, 18, 16], 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#1b5e8a', 'circle-stroke-width': 3 } });
      map.on('click', 'poles', e => { const id = e.features[0].properties.id; select(id, { fromMap: true, focus: false }); });
      map.on('mouseenter', 'poles', () => map.getCanvas().style.cursor = 'pointer');
      map.on('mouseleave', 'poles', () => map.getCanvas().style.cursor = '');
      if (pendingSel) api.select(pendingSel);
      renderLegend();
    });
    const api = {
      setData(rows) { if (ready) map.getSource('poles').setData(toFC(rows)); },
      select(r, fromMap) {
        if (!ready) { pendingSel = r; return; }
        map.getSource('sel').setData(toFC(r ? [r] : []));
        if (r && !fromMap) {
          // keep the marker beside the details: pad on the side the detail pane occupies
          const wide = window.innerWidth >= 900;
          map.easeTo({ center: [r.lon, r.lat], zoom: Math.max(map.getZoom(), 16.5), padding: wide ? { right: 0 } : { bottom: 0 }, duration: 400 });
        }
      },
      fit(rows) { if (!ready || !rows.length) return; const lons = rows.map(r => r.lon), lats = rows.map(r => r.lat); map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: 40, duration: 400, maxZoom: 17 }); },
      reset() { if (ready) map.fitBounds(D.meta.bbox, { padding: 20, duration: 400 }); },
      recolor() { if (ready) { map.getSource('poles').setData(toFC(filtered)); renderLegend(); } },
      resize() { map.resize(); },
    };
    function renderLegend() {
      const cond = `<div><i style="background:#c2410c"></i>Possible condition issue</div><div><i style="background:#fff"></i>No model flag</div><div><i style="background:#e3e3df"></i>Cannot tell from photos</div>`;
      const att = `<div><i style="background:#2c6e6b"></i>3 or more attachments</div><div><i style="background:#9ccbc9"></i>1 to 2 attachments</div><div><i style="background:#fff"></i>No attachments seen</div><div><i style="background:#e3e3df"></i>Cannot tell</div>`;
      $('legend').innerHTML = `<label>Color by <select id="cmode"><option value="condition"${state.colorMode === 'condition' ? ' selected' : ''}>condition flags</option><option value="attachments"${state.colorMode === 'attachments' ? ' selected' : ''}>attachment estimate</option></select></label>
        ${state.colorMode === 'condition' ? cond : att}${state.other ? '<div><i style="background:#bcbcb7"></i>Other detected object</div>' : ''}<div><i style="border-color:#1b5e8a;border-width:3px;background:none"></i>Selected</div>`;
      $('cmode').addEventListener('change', e => { state.colorMode = e.target.value; api.recolor(); });
    }
    return api;
  }

  // ---------- exports ----------
  const CSV_COLS = ['id', 'lat', 'lon', 'is_utility_pole', 'pole_type', 'model_flags', 'lean', 'crossarm', 'vegetation', 'transformer', 'attachments_estimate', 'photos_assessed', 'capture_sequences', 'photo_shown_date', 'latest_available_photo_date', 'source_photo_url', 'review_status'];
  function csvRow(r) {
    const v = [r.id, r.lat, r.lon, PP.isUtility(r), r.type, PP.conditionFlags(r).join(';'), r.lean, r.xarm, r.veg, r.xfmr, Number.isInteger(r.att) ? r.att : '', r.n, r.seq, r.shown.date || '', r.latest && r.latest.date || '', r.shown.url, reviewStatusLabel(r)];
    return v.map(x => { const s = String(x ?? ''); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; }).join(',');
  }
  function download(name, text, type) {
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; document.body.appendChild(a); a.click(); a.remove();
  }
  function exportCsv(rows) { download(`pole-pass-${D.meta.slug}-filtered.csv`, [CSV_COLS.join(','), ...rows.map(csvRow), '', D.meta.attribution].join('\n'), 'text/csv'); }
  function exportGeo(rows) {
    const fc = { type: 'FeatureCollection', license: 'ODbL 1.0', attribution: D.meta.attribution, dataset_version: D.meta.version,
      features: rows.map(r => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [r.lon, r.lat] }, properties: { id: r.id, is_utility_pole: PP.isUtility(r), pole_type: r.type, model_flags: PP.conditionFlags(r), lean: r.lean, crossarm: r.xarm, vegetation: r.veg, transformer: r.xfmr, attachments_estimate: r.att, photos_assessed: r.n, photo_shown_date: r.shown.date, source_photo_url: r.shown.url } })) };
    download(`pole-pass-${D.meta.slug}-filtered.geojson`, JSON.stringify(fc), 'application/geo+json');
  }
  function exportReview() {
    download(`pole-pass-${D.meta.slug}-review.json`, JSON.stringify({ dataset_version: D.meta.version, exported: new Date().toISOString(), scope: 'Local decisions from one browser. Not shared, not independently validated.', decisions: review }, null, 2), 'application/json');
  }

  // ---------- wiring ----------
  function wire() {
    $('chips').addEventListener('click', e => { const b = e.target.closest('.chip'); if (!b) return; state.flag = b.dataset.f; refresh(); });
    ['year-min', 'year-max'].forEach(id => $(id).addEventListener('change', () => { readYearInputs(); refresh(); }));
    $('recent').title = `Photo taken ${THIS_YEAR - 4} or later`;
    $('recent').addEventListener('click', () => { state.recent = state.recent ? null : THIS_YEAR - 4; $('recent').setAttribute('aria-pressed', String(!!state.recent)); refresh(); });
    $('sort').addEventListener('change', e => { state.sort = e.target.value; refresh(); });
    $('reset').addEventListener('click', resetFilters);
    $('other').addEventListener('change', e => { state.other = e.target.checked; state.flag = 'all'; refresh(); if (mapApi) mapApi.recolor(); });
    $('list').addEventListener('click', e => {
      const row = e.target.closest('.row'); if (row) { select(row.dataset.id); return; }
      if (e.target.id === 'more') { state.page++; renderList(); return; }
      if (e.target.id === 'reset2') resetFilters();
    });
    $('list').addEventListener('keydown', e => {
      const rows = [...$('list').querySelectorAll('.row')]; const i = rows.indexOf(document.activeElement); if (i < 0) return;
      if (e.key === 'ArrowDown' && rows[i + 1]) { e.preventDefault(); rows[i + 1].focus(); } if (e.key === 'ArrowUp' && rows[i - 1]) { e.preventDefault(); rows[i - 1].focus(); }
    });
    $('detail').addEventListener('click', e => {
      const t = e.target.closest('button'); if (!t) return;
      if (t.id === 'back' || t.id === 'close') { close(); return; }
      if (t.id === 'prev') { step(-1); return; } if (t.id === 'next') { step(1); return; }
      if (t.id === 'enlarge') { const f = byId[state.selected].frames[state.viewing]; $('lb-img').src = f.img; $('lb-img').alt = `Photo taken ${dateLabel(f)}`; $('lb-cap').textContent = `${state.selected} · Photo taken ${dateLabel(f)}`; $('lb-src').href = f.url; $('lb').showModal(); return; }
      if (t.id === 'cmp') { state.compare = !state.compare; renderDetail(); $('cmp').focus(); return; }
      if (t.id === 'rreset') { delete review[state.selected]; saveReview(review); renderDetail(); refreshRowStatus(); return; }
      if (t.dataset.i != null) { state.viewing = +t.dataset.i; state.compare = false; renderDetail(); $('detail').querySelector(`[data-i="${state.viewing}"]`).focus(); return; }
      if (t.dataset.rf) { const rv = review[state.selected] || { flags: {}, note: '' }; rv.flags[t.dataset.rf] = rv.flags[t.dataset.rf] === t.dataset.rv ? null : t.dataset.rv; review[state.selected] = rv; saveReview(review); renderDetail(); refreshRowStatus(); $('detail').querySelector(`[data-rf="${t.dataset.rf}"][data-rv="${t.dataset.rv}"]`).focus(); }
    });
    $('detail').addEventListener('input', e => { if (e.target.id === 'rnote') { const rv = review[state.selected] || { flags: {}, note: '' }; rv.note = e.target.value; review[state.selected] = rv; saveReview(review); } });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && state.selected && !$('lb').open) close(); });
    $('lb-close').addEventListener('click', () => $('lb').close());
    $('export-btn').addEventListener('click', () => { const m = $('export-menu'); m.hidden = !m.hidden; $('export-btn').setAttribute('aria-expanded', String(!m.hidden)); });
    document.addEventListener('click', e => { if (!e.target.closest('.menu')) { $('export-menu').hidden = true; $('export-btn').setAttribute('aria-expanded', 'false'); } });
    $('exp-csv-f').addEventListener('click', () => exportCsv(filtered)); $('exp-geo-f').addEventListener('click', () => exportGeo(filtered)); $('exp-review').addEventListener('click', exportReview);
    $('fit').addEventListener('click', () => mapApi && mapApi.fit(filtered)); $('resetview').addEventListener('click', () => mapApi && mapApi.reset());
    $('filters-toggle').addEventListener('click', () => { const open = $('toolbar').classList.toggle('open'); $('filters-toggle').setAttribute('aria-expanded', String(open)); });
    const sizeWs = () => { if (window.innerWidth >= 900) { $('ws').style.height = Math.max(520, window.innerHeight - $('toolbar').offsetHeight) + 'px'; } else { $('ws').style.height = ''; } if (mapApi) mapApi.resize(); };
    window.addEventListener('resize', sizeWs); sizeWs();
    document.querySelectorAll('.tabs [role=tab]').forEach(b => b.addEventListener('click', () => { state.tab = b.dataset.tab; $('ws').dataset.tab = state.tab; document.querySelectorAll('.tabs [role=tab]').forEach(x => x.setAttribute('aria-selected', String(x === b))); if (mapApi) mapApi.resize(); }));
    window.addEventListener('hashchange', () => { const id = parseHash(); if (id && id !== state.selected) select(id, { silent: true }); });
  }
  function refreshRowStatus() { document.querySelectorAll('.row').forEach(el => { const r = byId[el.dataset.id]; const s = el.querySelector('.status'); if (r && s) { s.className = `status ${reviewStatus(r) || ''}`; s.textContent = reviewStatusLabel(r); } }); }
  function parseHash() { const m = /[#&]pole=([^&]+)/.exec(location.hash); return m ? decodeURIComponent(m[1]) : null; }

  // ---------- init ----------
  function init() {
    $('year-min').min = Y0 ?? ''; $('year-min').max = Y1 ?? ''; $('year-max').min = Y0 ?? ''; $('year-max').max = Y1 ?? '';
    $('year-min').value = Y0 ?? ''; $('year-max').value = Y1 ?? '';
    $('year-min').placeholder = Y0 ?? '?'; $('year-max').placeholder = Y1 ?? '?';
    renderSummary();
    wire();
    refresh();
    const id = parseHash();
    if (id) {
      if (!select(id, { silent: true, focus: false })) { $('count').insertAdjacentHTML('afterend', `<div class="empty">No record with id <span class="mono">${esc(id)}</span> in this dataset.</div>`); }
    } else if (D.meta.example_id && byId[D.meta.example_id] && window.innerWidth >= 900) {
      select(D.meta.example_id, { example: true, silent: true, focus: false });
    }
    mapApi = initMap();
    if (mapApi && state.selected) mapApi.select(byId[state.selected]);
  }
  try { init(); } catch (e) { $('count').textContent = 'The page failed to initialize.'; console.error(e); }
  window.PolePass = { state, select, close, refresh, get filtered() { return filtered; } };
})();
