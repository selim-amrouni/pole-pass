/* Pole Pass front end. Data, list, filters, details, review, and exports initialize first; the map is
   attempted afterwards and degrades to a one-line notice with a retry. One map instance moves between
   the main map area and the mini slot inside an open record. Review decisions live in this browser only. */
(function () {
  'use strict';
  const D = window.POLE_DATA;
  const PP = window.PP;
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // ---------- labels ----------
  const L = {
    type: { wood_utility: 'Wood utility pole', concrete_or_steel_utility: 'Concrete or steel utility pole', street_light: 'Street light pole', traffic_signal: 'Traffic signal pole', push_brace: 'Push brace (support pole)', other: 'Other object', unclear: 'Pole type unclear' },
    lean: { none: 'No lean visible', slight: 'Slight lean', moderate: 'Possible lean (moderate)', severe: 'Possible lean (severe)', unclear: 'Lean: cannot tell' },
    xarm: { none_visible: 'No crossarm visible', intact: 'Crossarm looks intact', damaged: 'Possible crossarm damage', unclear: 'Crossarm: cannot tell' },
    veg: { none: 'No vegetation contact seen', near: 'Vegetation nearby', touching: 'Possible vegetation contact', unclear: 'Vegetation: cannot tell' },
    mat: { wood: 'Wood', concrete: 'Concrete', steel: 'Steel', fiberglass: 'Fiberglass', unclear: 'Material: cannot tell' },
    flag: { double: 'Possible double pole', lean: 'Possible lean', crossarm: 'Possible crossarm damage', vegetation: 'Possible vegetation contact', lean_slight: 'Slight lean', att3: '3+ estimated attachments', xfmr: 'Transformer visible' },
    review: { supported: 'Yes', not_supported: 'No', cannot_tell: "Can't tell" },
    reviewLong: { supported: 'Photo supports the flag', not_supported: 'Photo does not support the flag', cannot_tell: 'Cannot tell from the photo' },
    kind: { city: 'Urban', suburb: 'Suburban', backcountry: 'Rural' },
  };
  const attLabel = r => !PP.isUtility(r) ? 'Attachments not assessed' : Number.isInteger(r.att) ? `${r.att} estimated attachment${r.att === 1 ? '' : 's'}` : 'Attachments: cannot tell';
  const xfmrLabel = r => r.xfmr === true ? 'Transformer visible' : 'No transformer visible';
  const dateLabel = d => d && d.date ? d.date : 'Date unknown';
  const flagCls = k => k === 'att3' ? 'att' : k === 'xfmr' ? 'neutral' : k === 'lean_slight' ? 'warn' : 'issue';

  // ---------- state ----------
  const state = { flag: 'all', yearMin: null, yearMax: null, recent: false, review: 'all', other: false, years: false, osm: false, sort: 'date_desc', page: 1,
    selected: null, viewing: null, compare: false, colorMode: 'condition', tab: 'list', example: false, outline: true, markers: true, badges: true, now: Date.now(), reviews: null };
  const PAGE = 20;
  const byId = Object.fromEntries(D.records.map(r => [r.id, r]));
  const yearsAll = D.records.filter(PP.isUtility).map(r => r.shown.year).filter(y => y != null);
  const Y0 = yearsAll.length ? Math.min(...yearsAll) : null, Y1 = yearsAll.length ? Math.max(...yearsAll) : null;
  let filtered = [], mapApi = null;

  // ---------- review decisions (local to this browser, scoped to the dataset version) ----------
  const RKEY = `polepass-review:${D.meta.slug}:${D.meta.version}`;
  function loadReview() { try { return JSON.parse(localStorage.getItem(RKEY) || '{}'); } catch (e) { return {}; } }
  function saveReview(obj) { try { localStorage.setItem(RKEY, JSON.stringify(obj)); } catch (e) { /* storage unavailable */ } }
  let review = loadReview();
  state.reviews = review;
  const touch = rv => { rv.updated = new Date().toISOString(); return rv; };
  const reviewStatusText = r => { const s = PP.reviewState(r, review[r.id]); if (!s) return ''; const v = PP.reviewVerdict(review[r.id]); return s === 'reviewed' ? ({ ok: 'Reviewed: supported', no: 'Reviewed: not supported', ct: "Reviewed: can't tell" }[v]) : s === 'partial' ? 'Partly reviewed' : ''; };
  const reviewGlyph = r => { const s = PP.reviewState(r, review[r.id]); return s === 'reviewed' ? '✓' : s === 'partial' ? '◐' : s ? '○' : ''; };

  // ---------- header summary ----------
  function renderSummary() {
    const s = PP.summary(D.records, state.now);
    const dates = s.yearMin == null ? 'photo dates unknown' : s.yearMin === s.yearMax ? `photos ${s.yearMin}` : `photos ${s.yearMin} to ${s.yearMax}`;
    $('summary').innerHTML = `<b class="mono">${s.utility}</b> poles · <b class="mono">${s.conditionIssues}</b> possible condition issues · ${esc(dates)}`;
    $('summary').title = `${s.utility} pole records from the model, ${s.other} other detected objects. ${s.conditionIssues} with a possible double pole, lean, crossarm damage, or vegetation contact. ${s.warnings} with a slight lean (watch items, listed under More filters).`;
    $('dates-label').textContent = s.yearMin == null ? '?' : `${s.yearMin}–${s.yearMax}`;
  }

  // ---------- filters ----------
  const CHIPS = { chips: [['all', 'All poles', ''], ['double', 'Double pole', 'issue'], ['lean', 'Lean', 'issue'], ['xarm', 'Crossarm', 'issue'], ['veg', 'Vegetation', 'issue']],
                  'chips-eq': [['att3', '3+ attachments', 'att'], ['xfmr', 'Transformer', 'neutral']],
                  'chips-more': [['lean_slight', 'Slight lean (watch item)', 'warn']] };
  const FLAG_LABEL = { all: 'All poles', double: 'Possible double pole (old pole left beside its replacement)', lean: 'Possible lean', xarm: 'Possible crossarm damage', veg: 'Possible vegetation contact', att3: '3+ attachments', xfmr: 'Transformer visible', lean_slight: 'Slight lean (watch item)' };
  function renderChips() {
    const base = D.records.filter(r => state.other ? !PP.isUtility(r) : PP.isUtility(r));
    Object.entries(CHIPS).forEach(([id, list]) => { $(id).innerHTML = list.map(([k, label, cls]) => `<button class="chip ${cls}" data-f="${k}" aria-pressed="${state.flag === k}" title="${esc(FLAG_LABEL[k])}">${esc(label)}<span class="n">${base.filter(PP.FILTERS[k]).length}</span></button>`).join(''); });
    $('years-n').textContent = String(base.filter(PP.spansYears).length);
    $('recent-n').textContent = String(base.filter(r => PP.isRecent(r, state.now)).length);
    if (D.meta.osm) $('osm-n').textContent = String(base.filter(PP.notInOsm).length);
    const p = PP.reviewProgress(base, review);
    $('review-n').textContent = p.reviewable ? `${p.reviewed} of ${p.reviewable} reviewed` : '';
    document.querySelectorAll('#review-seg button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.rs === state.review)));
  }
  function readYearInputs() {
    const a = parseInt($('year-min').value, 10), b = parseInt($('year-max').value, 10);
    state.yearMin = Number.isFinite(a) && (Y0 == null || a > Y0) ? a : null;
    state.yearMax = Number.isFinite(b) && (Y1 == null || b < Y1) ? b : null;
    $('dates-label').textContent = state.recent ? `last ${PP.RECENT_MONTHS} mo` : state.yearMin == null && state.yearMax == null ? `${Y0 ?? '?'}–${Y1 ?? '?'}` : `${state.yearMin ?? Y0}–${state.yearMax ?? Y1}`;
  }
  function resetFilters() {
    Object.assign(state, { flag: 'all', yearMin: null, yearMax: null, recent: false, review: 'all', other: false, years: false, osm: false, page: 1 });
    $('year-min').value = Y0 ?? ''; $('year-max').value = Y1 ?? ''; ['recent', 'other', 'years', 'osm'].forEach(id => { $(id).checked = false; });
    readYearInputs(); refresh();
  }
  function activeFilters() {
    const out = [];
    if (state.flag !== 'all') out.push(FLAG_LABEL[state.flag] || state.flag);
    if (state.recent) out.push(`photo within ${PP.RECENT_MONTHS} months`);
    else if (state.yearMin != null || state.yearMax != null) out.push(`photo ${state.yearMin ?? Y0} to ${state.yearMax ?? Y1}`);
    if (state.review !== 'all') out.push(state.review === 'reviewed' ? 'reviewed' : 'not yet reviewed');
    if (state.years) out.push('photographed in 2+ years');
    if (state.osm) out.push('no OSM pole within 15 m');
    if (state.other) out.push('other detected objects');
    return out;
  }

  // ---------- list ----------
  function refresh(keepPage) {
    if (!keepPage) state.page = 1;
    filtered = PP.applyFilters(D.records, state).sort(PP.SORTS[state.sort]);
    renderChips(); renderList();
    if (mapApi) mapApi.setData(filtered);
    updateDetailNav();
  }
  function flagChips(r) {
    const out = PP.conditionFlags(r).map(f => `<span class="flag issue">${L.flag[f]}</span>`);
    PP.warningFlags(r).forEach(f => out.push(`<span class="flag warn">${L.flag[f]}</span>`));
    if (PP.attachments3(r)) out.push(`<span class="flag att">3+ attachments</span>`);
    if (PP.transformerVisible(r)) out.push(`<span class="flag neutral">Transformer</span>`);
    if (!out.length) out.push(PP.conditionUnclear(r) ? `<span class="flag dim">Cannot tell from photos</span>` : `<span class="flag dim">No model flag</span>`);
    return out.join('');
  }
  function rowHtml(r) {
    const util = PP.isUtility(r);
    const img = r.shown.img ? `<img src="${esc(r.shown.img)}" alt="" loading="lazy">` : `<span class="ph">No photo</span>`;
    const l1 = util ? flagChips(r) : `<span class="flag dim">${esc(L.type[r.type] || 'Other object')}</span>`;
    const st = util ? PP.reviewState(r, review[r.id]) : null;
    const right = util ? (st && st !== 'unreviewed' ? `<span class="status ${st}"><span aria-hidden="true">${reviewGlyph(r)}</span> ${esc(reviewStatusText(r))}</span>` : '') : `<span class="status">Not a utility pole</span>`;
    return `<button class="row" role="option" data-id="${esc(r.id)}" aria-selected="${state.selected === r.id}">${img}
      <span><span class="l1"><span>${l1}</span></span>
      <span class="l2"><span class="d">${esc(dateLabel(r.shown))}<span class="age"> · ${esc(PP.ageLabel(r.shown.ts, state.now))}</span></span>${right}<span class="pid">${esc(r.id)}</span></span></span></button>`;
  }
  function renderList() {
    const total = filtered.length, shown = Math.min(total, state.page * PAGE);
    const act = activeFilters();
    $('count').innerHTML = `<span><span class="mono">${shown}</span> of <span class="mono">${total}</span> ${state.other ? 'objects' : 'poles'}</span>${act.length ? '<button id="reset2">Reset filters</button>' : ''}`;
    const wasHidden = $('active').hidden; $('active').hidden = !act.length;
    if (wasHidden !== $('active').hidden && state.sizeWs) state.sizeWs();  // the line above the workspace changes the height available to it
    $('active').innerHTML = act.length ? `<span>Showing: ${act.map(esc).join(' · ')} · <span class="mono">${total}</span> ${state.other ? 'objects' : 'poles'}</span><button class="btn sm" id="reset4">Reset</button>` : '';
    if (!total) { $('list').innerHTML = `<div class="empty">No ${state.other ? 'objects' : 'poles'} match these filters. <button class="btn sm" id="reset3">Reset filters</button></div>`; return; }
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
    if (mapApi) { mapApi.toMini(); mapApi.select(r, opts.fromMap); }
    if (opts.focus !== false) { const h = $('detail').querySelector('.detail-h button'); if (h) h.focus(); }
    return true;
  }
  function close(keepSelection) {
    if (!keepSelection) { state.selected = null; history.replaceState(null, '', location.pathname + location.search); document.querySelectorAll('.row').forEach(el => el.setAttribute('aria-selected', 'false')); }
    state.example = false;
    $('ws').classList.remove('has-detail'); $('detail').hidden = true; $('detail').innerHTML = '';
    if (mapApi) { mapApi.toMain(); if (!keepSelection) mapApi.select(null); }
    const row = document.querySelector(keepSelection && state.selected ? `.row[data-id="${CSS.escape(state.selected)}"]` : '.row'); if (row) row.focus();
  }
  function goTo(j) {
    if (j < 0 || j >= filtered.length) return false;
    if (j >= state.page * PAGE) { state.page = Math.ceil((j + 1) / PAGE); renderList(); }
    select(filtered[j].id);
    const row = document.querySelector(`.row[data-id="${CSS.escape(filtered[j].id)}"]`); if (row) row.scrollIntoView({ block: 'nearest' });
    return true;
  }
  function step(delta) { const i = filtered.findIndex(r => r.id === state.selected); if (i < 0) return; goTo(i + delta); }
  // next record in the current list, after the open one, whose flags are not all decided
  function nextUnreviewed() {
    const i = filtered.findIndex(r => r.id === state.selected);
    const isOpen = r => { const s = PP.reviewState(r, review[r.id]); return s === 'unreviewed' || s === 'partial'; };
    const j = filtered.findIndex((r, k) => k > i && isOpen(r));
    if (j >= 0) return goTo(j);
    const k = filtered.findIndex((r, q) => q < i && isOpen(r));
    if (k >= 0) return goTo(k);
    const n = $('nextun'); if (n) { n.textContent = 'None left in this list'; n.disabled = true; }
    return false;
  }
  function updateDetailNav() {
    const i = filtered.findIndex(r => r.id === state.selected);
    const p = $('prev'), n = $('next'); if (!p || !n) return;
    p.disabled = i <= 0; n.disabled = i < 0 || i >= filtered.length - 1;
    const pos = $('pos'); if (pos) pos.textContent = i >= 0 ? `${i + 1} of ${filtered.length}` : 'Not in current list';
  }
  function frameObs(f) {
    return [L.lean[f.lean] || f.lean, L.xarm[f.xarm] || f.xarm, L.veg[f.veg] || f.veg, f.xfmr ? 'Transformer visible' : 'No transformer visible', Number.isInteger(f.att) ? `${f.att} estimated attachment${f.att === 1 ? '' : 's'}` : 'Attachments: cannot tell'];
  }
  // A double pole is judged from one photo showing BOTH poles, not from this record's own frames, so
  // it has no per-frame support line. Its evidence is the pair: the reason, the estimated gap, and a
  // link to the frame both poles appear in. Rendered here so the flag is as inspectable as the rest.
  function doubleEvidenceHtml(r) {
    const d = r.dbl; if (!d) return '';
    const where = [d.street, d.cross_street].filter(Boolean).join(' \u00d7 ');
    const gap = Number.isFinite(d.separation_m) ? `${d.separation_m.toFixed(1)} m apart (estimated)` : 'gap not estimated';
    const cut = d.cut_short === 'yes' ? ' · one pole cut short' : '';
    const who = d.maintainer && d.maintainer !== 'UNCERTAIN' ? ` · ${esc(d.maintainer)} maintains this side (approximate)` : '';
    const link = d.url ? ` <a class="lnk" href="${esc(d.url)}" target="_blank" rel="noopener">open source photo</a>` : '';
    // The claim is about TWO poles, so show the frame that contains both with both of them outlined.
    // One outlined pole would prove nothing about a pair.
    const pic = d.crop && d.boxes && d.boxes.length === 2 ? `
      <figure class="dblpair">
        <div class="wrap">
          <img src="${esc(d.crop)}" alt="Photo showing both poles of candidate double ${esc(d.pair_id)}, each outlined">
          <svg viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true">
            ${d.boxes.map((b, i) => `<rect class="dbl b${i}" x="${(b[0] * 1000).toFixed(1)}" y="${(b[1] * 1000).toFixed(1)}" width="${((b[2] - b[0]) * 1000).toFixed(1)}" height="${((b[3] - b[1]) * 1000).toFixed(1)}" vector-effect="non-scaling-stroke"/>`).join('')}
          </svg>
        </div>
        <figcaption class="muted">Both poles of the pair, outlined. Photo © Mapillary contributors, CC BY-SA 4.0.${link}</figcaption>
      </figure>` : '';
    return `<div class="ev"><span>${esc(d.reason || '')}</span><span class="muted">${esc(gap)}${cut}${esc(where ? ` · ${where}` : '')}${who} · pair ${esc(d.pair_id)}</span></div>${pic}`;
  }
  // photo-evidence line for one flag: how many photos support it, when, and what the latest assessed photo shows
  function evidenceHtml(r, k) {
    if (k === 'double') return doubleEvidenceHtml(r);
    const s = PP.flagSupport(r, k); if (!s) return '';
    const dates = [...new Set(s.supporting.map(f => f.date).filter(Boolean))].sort();
    const n = `${s.supporting.length} of ${s.n} photo${s.n === 1 ? '' : 's'}${s.drives > 1 ? ` · ${s.drives} drives` : s.n > 1 && s.drives === 1 ? ' · 1 drive' : ''}`;
    const latest = s.latest ? `Latest assessed photo (${esc(dateLabel(s.latest))}): ${s.latestStatus === 'supports' ? 'shows it' : s.latestStatus === 'unclear' ? 'cannot tell' : 'does not show it'}` : 'No dated photo';
    const idx = f => r.frames.indexOf(f);
    const links = s.supporting.filter(f => f.img).map(f => `<button class="lnk" data-i="${idx(f)}">${esc(f.date || '?')}</button>`).join(', ');
    return `<div class="ev"><span>${esc(n)}${dates.length && !links ? ` (${esc(dates.join(', '))})` : ''}${links ? `: ${links}` : ''}</span><span class="muted">${latest}</span></div>`;
  }
  function mapsLinks(r) {
    const q = `${r.lat.toFixed(6)},${r.lon.toFixed(6)}`;
    const pin = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(q)}`;
    const pano = `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${encodeURIComponent(q)}`;
    return `<span class="maps"><a class="lnk" href="${pin}" target="_blank" rel="noopener" title="Open this location in Google Maps">Google Maps ↗</a>` +
           `<a class="lnk sv" href="${pano}" target="_blank" rel="noopener" title="Google Street View: a different provider on a different date, so it is an independent check on what is standing here now">Street View ↗</a></span>`;
  }

  function renderDetail() {
    const r = byId[state.selected]; if (!r) return;
    const f = r.frames[state.viewing] || null;
    const util = PP.isUtility(r);
    const flags = PP.reviewableFlags(r);
    const rv = review[r.id] || { flags: {}, note: '' };
    const frameFlags = f ? [(f.lean === 'moderate' || f.lean === 'severe') && L.flag.lean, f.xarm === 'damaged' && L.flag.crossarm, f.veg === 'touching' && L.flag.vegetation, f.lean === 'slight' && L.flag.lean_slight].filter(Boolean) : [];
    const m = f && f.marks;
    const SW = f && f.size ? f.size[0] : 1000, SH = f && f.size ? f.size[1] : 1000, R = Math.max(9, Math.round(Math.min(SW, SH) / 28));
    const X = p => p[0] * SW, Y = p => p[1] * SH, px = p => `${X(p).toFixed(1)},${Y(p).toFixed(1)}`;
    const circle = (p, cls, label) => `<circle class="mk ${cls}" cx="${X(p)}" cy="${Y(p)}" r="${R}" vector-effect="non-scaling-stroke"/>${label ? `<text x="${X(p)}" y="${Y(p) + R * 0.38}" text-anchor="middle" font-size="${R * 1.1}">${esc(label)}</text>` : ''}`;
    const square = (p, cls) => `<rect class="mk ${cls}" x="${X(p) - R}" y="${Y(p) - R}" width="${2 * R}" height="${2 * R}" vector-effect="non-scaling-stroke"/>`;
    const diamond = (p, cls) => `<polygon class="mk ${cls}" points="${X(p)},${Y(p) - R * 1.3} ${X(p) + R * 1.3},${Y(p)} ${X(p)},${Y(p) + R * 1.3} ${X(p) - R * 1.3},${Y(p)}" vector-effect="non-scaling-stroke"/>`;
    const tri = (p, cls) => `<polygon class="mk ${cls}" points="${X(p)},${Y(p) - R * 1.3} ${X(p) + R * 1.2},${Y(p) + R} ${X(p) - R * 1.2},${Y(p) + R}" vector-effect="non-scaling-stroke"/>`;
    let svg = '';
    if (f && f.poly && f.poly.length && state.outline) svg += f.poly.map(ring => `<polygon class="halo" points="${ring.map(p => px(p)).join(' ')}"/><polygon class="line" points="${ring.map(p => px(p)).join(' ')}"/>`).join('');
    // On a double-pole record, outlining one pole says nothing about a pair. When this frame shows
    // both of the pair's detections, box them both so the claim is visible in the main photo, not
    // only in the small pair figure.
    if (f && f.dblboxes && state.outline) {
      svg += f.dblboxes.map((b, i) => {
        const x = b[0] * SW, y = b[1] * SH, w = (b[2] - b[0]) * SW, h = (b[3] - b[1]) * SH;
        return `<rect class="dblbox b${i}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" vector-effect="non-scaling-stroke"/>`;
      }).join('');
    }
    if (m && state.markers) {
      if (m.top && m.base) svg += `<line class="axis" x1="${X(m.top)}" y1="${Y(m.top)}" x2="${X(m.base)}" y2="${Y(m.base)}" vector-effect="non-scaling-stroke"/>`;
      m.att.forEach((a, i) => { if (a.p) svg += circle(a.p, 'att', String(i + 1)); });
      if (m.xfmr) svg += square(m.xfmr, 'xfmr'); if (m.xarm) svg += tri(m.xarm, 'issue'); if (m.veg) svg += diamond(m.veg, 'issue');
    }
    const overlay = svg ? `<svg class="ov" viewBox="0 0 ${SW} ${SH}" preserveAspectRatio="none" aria-hidden="true">${svg}</svg>` : '';
    const badges = frameFlags.length && state.badges ? `<div class="badges" aria-hidden="true">${frameFlags.map(x => `<span class="flag ${x === L.flag.lean_slight ? 'warn' : 'issue'}">${esc(x)}</span>`).join('')}</div>` : '';
    const photo = f && f.img ? `<div class="imgwrap"><img id="dimg" src="${esc(f.img)}" alt="Photo of pole ${esc(r.id)} taken ${esc(dateLabel(f))}">${overlay}</div>${badges}`
      : `<div class="photo-missing">Photo unavailable.${f && f.url ? ` <a href="${esc(f.url)}" target="_blank" rel="noopener">Open source photo</a>` : ''}</div>`;
    const ovbar = `<div class="ovbar" role="group" aria-label="Photo annotations">
        <button class="o" id="tg-outline" aria-pressed="${state.outline}" ${f && ((f.poly && f.poly.length) || f.dblboxes) ? '' : 'disabled'}><i></i>Outline</button>
        <button class="m" id="tg-markers" aria-pressed="${state.markers}" ${m ? '' : 'disabled'}><i></i>Markers</button>
        <button class="b" id="tg-badges" aria-pressed="${state.badges}" ${frameFlags.length ? '' : 'disabled'}><i></i>Flags</button></div>`;
    const G = { dblbox: '<rect x="1" y="2" width="4.5" height="10"/><rect x="8.5" y="2" width="4.5" height="10"/>', outline: '<rect x="4.5" y="1" width="5" height="12" rx="1"/>', axis: '<line x1="7" y1="1" x2="7" y2="13"/>', att: '<circle cx="7" cy="7" r="5.5"/>', xfmr: '<rect x="2" y="2" width="10" height="10"/>', xarm: '<polygon points="7,1.5 12.5,12 1.5,12"/>', veg: '<polygon points="7,1 13,7 7,13 1,7"/>' };
    const keyItems = [
      f && f.poly && f.poly.length && state.outline && ['outline', 'Mapillary outline'],
      f && f.dblboxes && state.outline && ['dblbox', 'Both poles of the candidate double'],
      m && state.markers && m.top && m.base && ['axis', 'Pole axis, model estimate'],
      m && state.markers && m.att.length && ['att', `Attachment 1${m.att.length > 1 ? `–${m.att.length}` : ''}`],
      m && state.markers && m.xfmr && ['xfmr', 'Transformer'],
      m && state.markers && m.xarm && ['xarm', 'Crossarm damage'],
      m && state.markers && m.veg && ['veg', 'Vegetation contact'],
    ].filter(Boolean);
    const key = keyItems.length ? `<div class="key" aria-label="Photo annotation key"><span class="kt">Model observations</span>${keyItems.map(([k, label]) => `<span><svg viewBox="0 0 14 14" class="g ${k}" aria-hidden="true">${G[k]}</svg>${esc(label)}</span>`).join('')}</div>` : '';
    const yrs = PP.frameYears(r);
    const strip = r.frames.length > 1 ? `<div class="strip"><span class="lbl">${r.frames.length} photos<br>${yrs.length > 1 ? `${yrs[0]}–${yrs[yrs.length - 1]}` : `${r.seq} drive${r.seq === 1 ? '' : 's'}`}</span>
        <div class="thumbs">${r.frames.map((x, i) => `<button data-i="${i}" aria-pressed="${i === state.viewing}" aria-label="View photo from ${esc(dateLabel(x))}">${x.img ? `<img src="${esc(x.img)}" alt="">` : `<span class="ph"></span>`}<span class="c">${esc(x.date || '?')}</span></button>`).join('')}</div>
        <button class="btn sm cmp" id="cmp" aria-pressed="${state.compare}">Compare</button></div>${state.compare ? compareHtml(r) : ''}` : '';
    const latest = r.latest && r.latest.ts && (!f || r.latest.ts > (f.ts || 0)) ? `<a href="${esc(r.latest.url)}" target="_blank" rel="noopener">Latest available photo ${esc(dateLabel(r.latest))}${r.latest.classified ? '' : ' (not assessed)'} ↗</a>` : '';
    const flagLabel = k => k === 'lean' || k === 'lean_slight' ? L.lean[r.lean] : k === 'crossarm' ? L.xarm[r.xarm] : k === 'vegetation' ? L.veg[r.veg] : k === 'att3' ? attLabel(r) : xfmrLabel(r);
    const why = flags.length ? flags.map(k => `<div class="why-it"><span class="flag ${flagCls(k)}">${esc(flagLabel(k))}</span>${evidenceHtml(r, k)}${k === 'vegetation' ? '<div class="hint">Judged from overlap in the photo; a branch behind or in front of the pole can read as touching it.</div>' : ''}</div>`).join('')
      : `<div class="why-it"><span class="flag dim">${PP.conditionUnclear(r) ? 'Condition could not be assessed from the photos' : util ? 'No model flag. Listed as part of the inventory; not inspected.' : 'Not a utility pole'}</span></div>`;
    const attrs = [
      `${esc(L.type[r.type] || r.type)} · ${esc(L.mat[r.material] || r.material)}`,
      !PP.possibleLean(r) && !PP.leanWarning(r) && (L.lean[r.lean] || r.lean),
      !PP.crossarmDamage(r) && (L.xarm[r.xarm] || r.xarm),
      !PP.vegetationContact(r) && (L.veg[r.veg] || r.veg),
      !PP.transformerVisible(r) && xfmrLabel(r),
      !PP.attachments3(r) && attLabel(r),
    ].filter(Boolean).map(t => `<div class="it"><span>${t}</span></div>`).join('');
    const agree = r.n > 1 ? `<p class="hint">Combined from ${r.n} photos${r.seq > 1 ? ` across ${r.seq} drives` : ' from one drive'}; each field takes the most common value. Photos from one drive are not independent views.</p>` : '';
    const reviewSec = util && flags.length ? `<div class="sec review"><h3>Your review</h3><p class="q">Does the photo support the flag? This records what the photos show, not field condition.</p>
            ${flags.map(k => `<div class="it"><span>${esc(L.flag[k])}</span><span class="seg" role="group" aria-label="Does the photo support ${esc(L.flag[k])}?">${['supported', 'not_supported', 'cannot_tell'].map(v => `<button class="${v === 'supported' ? 'yes' : ''}" data-rf="${k}" data-rv="${v}" aria-pressed="${rv.flags[k] === v}" title="${L.reviewLong[v]}">${L.review[v]}</button>`).join('')}</span></div>`).join('')}
            <textarea id="rnote" maxlength="500" placeholder="Note (optional)" aria-label="Review note">${esc(rv.note || '')}</textarea>
            <p class="hint">Saved in this browser only. <button class="btn sm" id="rreset">Clear</button> <button class="btn sm" id="nextun">Next unreviewed</button></p></div>` : '';
    $('detail').innerHTML = `
      <div class="detail-h"><button class="btn sm" id="back" aria-label="Back to list">← List</button><span class="id">${esc(r.id)}</span><span class="pos" id="pos"></span>
        <div class="nav"><button class="btn sm" id="prev" aria-label="Previous pole">Prev</button><button class="btn sm" id="next" aria-label="Next pole">Next</button><a class="btn sm sv" id="streetview" href="${`https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${encodeURIComponent(r.lat.toFixed(6) + ',' + r.lon.toFixed(6))}`}" target="_blank" rel="noopener" title="Google Street View here — a different provider on a different date, so it is an independent check on what is standing now">Street View ↗</a><button class="btn sm" id="share">Copy link</button><button class="btn sm" id="close" aria-label="Close details">Close</button></div></div>
      ${state.example ? `<div class="example-tag">Example record. Pick any pole from the list or map.</div>` : ''}
      <div class="dbody">
        <div class="dphoto">
          <div class="stage">${photo}${f && f.img ? ovbar + key : ''}</div>
          <div class="cap"><span><b>${esc(dateLabel(f))}</b> <span class="muted">${esc(PP.ageLabel(f && f.ts, state.now))}</span>${f && f.pano ? ' · 360°' : ''}${f && f.shown ? (r.shown.newest ? ' · newest readable' : ' · clearest available') : ''}</span>
            ${f && f.url ? `<a href="${esc(f.url)}" target="_blank" rel="noopener">Source ↗</a>` : ''}${f && f.img ? `<a href="#" id="enlarge">Enlarge</a>` : ''}${f && f.by ? `<span class="muted small">by ${esc(f.by)}</span>` : ''}${latest}</div>
          ${strip}
        </div>
        <div class="dtext">
          <div class="sec"><h3>Why this record is listed</h3><div class="why">${why}</div>${agree}</div>
          ${reviewSec}
          <div class="sec"><h3>Other visible attributes</h3><div class="fl">${attrs}</div>${f ? `<p class="hint">This photo: ${frameObs(f).map(esc).join(' · ')}.</p>` : ''}</div>
          <div class="sec"><h3>Location</h3><div class="mini" id="mini"></div>
            <div class="kv">${r.lat.toFixed(5)}, ${r.lon.toFixed(5)} <span>· ${r.nfeat} detection${r.nfeat === 1 ? '' : 's'} · estimate</span> · <button class="btn sm" id="fullmap">Full map</button> ${mapsLinks(r)}</div></div>
          <details class="sec tech"><summary>Technical details</summary>
            ${util ? tiltHtml(r) : ''}
            ${D.meta.osm && util ? `<p class="small">${Number.isFinite(r.osm) ? `Nearest OpenStreetMap pole ${r.osm.toFixed(0)} m away` : 'No OpenStreetMap pole within 25 m'}. OSM is volunteer mapping, not the utility's inventory.</p>` : ''}
            <p class="small muted">Raw model output per photo. Self-rating is uncalibrated. Notes are free text and may overstate.</p>
            <table><thead><tr><th>Photo</th><th>Pole px</th><th>Type</th><th>Lean</th><th>Tilt</th><th>Crossarm</th><th>Veg.</th><th>Xfmr</th><th>Att.</th><th>Self-rating</th><th>Note</th></tr></thead>
            <tbody>${r.frames.map(x => `<tr><td class="mono">${esc(x.date || '?')}${x.pano ? ' 360°' : ''}</td><td class="mono">${x.px ?? ''}</td><td>${esc(x.type)}</td><td>${esc(x.lean)}</td><td class="mono">${Number.isFinite(x.tilt) ? `${x.tilt}°` : ''}</td><td>${esc(x.xarm)}</td><td>${esc(x.veg)}</td><td>${x.xfmr ? 'yes' : 'no'}</td><td class="mono">${x.att ?? ''}</td><td class="mono">${x.conf ?? ''}</td><td>${esc(x.note)}</td></tr>`).join('')}</tbody></table>
            <p class="small muted">Mapillary features: <span class="mono">${r.features.map(esc).join(', ')}</span> · grouped within ${esc(D.meta.method.radius_m)} m · demo id, not an asset id.</p></details>
        </div>
      </div>`;
    $('detail').hidden = false;
    updateDetailNav();
    if (mapApi) mapApi.toMini();
  }
  // Apparent tilt per photo (from the Mapillary outline) against photo date. A property of each photo, not a measured lean.
  function tiltHtml(r) {
    const pts = r.frames.map((x, i) => ({ i, ts: x.ts, t: Math.abs(x.tilt), lean: x.lean, pano: !!x.pano })).filter(p => Number.isFinite(p.t) && p.ts);
    if (!pts.length) return '';
    const cal = D.meta.tilt;
    const noise = cal ? `${cal.kind === 'flat' ? 'Flat photos' : 'Photos'} the model called straight read up to ${cal.none_p90}° (90th percentile, ${cal.none_n} photos); 360° photos read noisier.` : '';
    const hint = `<p class="hint">Angle of the Mapillary outline from vertical in each photo. Camera roll, perspective, and a lean toward or away from the camera all distort it. ${noise} It is not a measurement of the pole, and a series of photos does not measure deterioration.</p>`;
    if (pts.length === 1) return `<div class="tilt"><h4>Apparent tilt in photo</h4><div class="it"><span><b class="mono">${pts[0].t.toFixed(1)}°</b> from vertical, photo from ${esc(dateLabel(r.frames[pts[0].i]))}</span></div>${hint}</div>`;
    const W = 360, H = 128, L = 30, R = 10, T = 10, B = 32;
    const ts0 = Math.min(...pts.map(p => p.ts)), ts1 = Math.max(...pts.map(p => p.ts));
    const ymax = Math.max(15, cal ? cal.none_p90 + 5 : 0, Math.ceil(Math.max(...pts.map(p => p.t)) / 5) * 5 + 5);
    const X = ts => ts1 === ts0 ? L + (W - L - R) / 2 : L + (ts - ts0) / (ts1 - ts0) * (W - L - R);
    const Y = t => T + (1 - t / ymax) * (H - T - B);
    const iso = p => new Date(p.ts).toISOString();
    const sameMonth = new Set(pts.map(p => r.frames[p.i].date)).size === 1, sameDay = sameMonth && new Set(pts.map(p => iso(p).slice(0, 10))).size === 1;
    const lbl = p => sameDay ? iso(p).slice(11, 16) : sameMonth ? iso(p).slice(0, 10) : (r.frames[p.i].date || '?');
    const months = [...pts.reduce((mm, p) => { const d = lbl(p); mm.set(d, Math.min(mm.get(d) ?? Infinity, p.ts)); return mm; }, new Map())].sort((a, b) => a[1] - b[1]);
    const MINGAP = sameDay ? 40 : sameMonth ? 66 : 48, rows = [[], []];
    months.forEach(([label, ts]) => { const x = Math.min(W - R - 22, Math.max(L + 22, X(ts))); const row = rows.find(rw => !rw.length || x - rw[rw.length - 1].x >= MINGAP); if (row) row.push({ x, label }); });
    const xt = rows.map((row, k) => row.map(({ x, label }) => `<text x="${x.toFixed(1)}" y="${H - 16 + k * 10}" text-anchor="middle">${esc(label)}</text>`).join('')).join('')
      + (sameDay ? `<text x="${W - R}" y="${T - 2}" text-anchor="end">${esc(iso(pts[0]).slice(0, 10))} · times UTC</text>` : '');
    const yt = [0, 10, 20, 30].filter(v => v <= ymax).map(v => `<line class="grid" x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"/><text x="${L - 4}" y="${(Y(v) + 3).toFixed(1)}" text-anchor="end">${v}°</text>`).join('');
    const band = cal ? `<rect class="band" x="${L}" y="${Y(cal.none_p90).toFixed(1)}" width="${W - L - R}" height="${(Y(0) - Y(cal.none_p90)).toFixed(1)}"/>` : '';
    const dots = pts.sort((a, b) => a.ts - b.ts).map(p => `<circle class="pt ${p.lean === 'moderate' || p.lean === 'severe' ? 'issue' : p.lean === 'slight' ? 'warn' : ''}${p.pano ? ' pano' : ''}${p.i === state.viewing ? ' cur' : ''}" data-i="${p.i}" tabindex="0" role="button" aria-label="View photo from ${esc(dateLabel(r.frames[p.i]))}, ${p.t.toFixed(1)} degrees in photo" cx="${X(p.ts).toFixed(1)}" cy="${Y(p.t).toFixed(1)}" r="5"><title>${esc(dateLabel(r.frames[p.i]))}: ${p.t.toFixed(1)}° in photo</title></circle>`).join('');
    return `<div class="tilt"><h4>Apparent tilt in photos · ${pts.length} of ${r.n}</h4>
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Apparent tilt per photo over time">${band}${yt}${xt}${dots}</svg>
      <p class="hint">${cal ? `Shaded: range of photos the model called straight. ` : ''}Dot color follows that photo's lean call; dashed dots are 360° photos. Click a dot to view the photo.</p>${hint}</div>`;
  }
  function compareHtml(r) {
    const a = r.frames[state.viewing];
    const b = r.frames.reduce((best, x, i) => i !== state.viewing && x.img && (!best || Math.abs((x.ts || 0) - (a.ts || 0)) > Math.abs((best.ts || 0) - (a.ts || 0))) ? x : best, null);
    if (!a || !b || !a.img) return `<p class="hint" style="padding:0 12px 8px">Only one photo has an in-app image; others are available through their source links.</p>`;
    const fig = x => `<figure><img src="${esc(x.img)}" alt="Photo taken ${esc(dateLabel(x))}"><figcaption><b>${esc(dateLabel(x))}</b> · ${esc(frameObs(x)[0])}, ${esc(frameObs(x)[4])}</figcaption></figure>`;
    return `<div class="compare">${fig(a)}${fig(b)}</div><p class="hint" style="padding:0 12px 8px;margin:0">Two photos of the same record. Differences are not confirmed changes; angle, distance, and camera differ.</p>`;
  }

  // ---------- map ----------
  // States: loading (until the style loads), empty (no results), failed (library blocked, no WebGL, or a
  // construction error) with a retry that re-injects the script once. Tile errors show a notice; the map stays.
  const MAP_JS = 'https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js';
  let mapAttempt = 0, mapInstance = null;
  function mapState(kind, text, retry) {
    const el = $('map-state'); if (!el) return;
    el.hidden = !kind; el.className = `map-state ${kind || ''}`;
    el.innerHTML = kind ? `<div><span>${esc(text)}</span>${retry ? ' <button class="btn sm" id="map-retry">Retry</button>' : ''}</div>` : '';
    $('ws').classList.toggle('map-failed', kind === 'failed');
    // when the map is out, the list takes the whole workspace and the notice sits above it
    const n = $('map-notice'); if (n) { n.hidden = kind !== 'failed'; n.innerHTML = kind === 'failed' ? `<span>${esc(text)}</span>${retry ? ' <button class="btn sm" id="map-retry2">Retry</button>' : ''}` : ''; }
    const mini = $('mini'); if (mini && kind === 'failed') mini.hidden = true;
  }
  function webglOk() { try { const c = document.createElement('canvas'); return !!(c.getContext('webgl2') || c.getContext('webgl') || c.getContext('experimental-webgl')); } catch (e) { return false; } }
  function initMap() {
    const box = $('map');
    const fail = (why, retry) => {
      mapState('failed', why, retry); $('legend').hidden = true; $('fit').hidden = true; $('resetview').hidden = true;
      return { failed: true, setData() {}, select() {}, fit() {}, reset() {}, recolor() {}, resize() {}, destroy() {},
        toMini() { const mini = $('mini'); if (mini) mini.hidden = true; }, toMain() {} };
    };
    if (mapInstance) { try { mapInstance.remove(); } catch (e) { /* already gone */ } mapInstance = null; }
    box.innerHTML = '';
    if (typeof window.maplibregl === 'undefined') return fail(window.__maplibreFailed ? 'The map library could not be loaded (blocked or offline). The list, photos, reviews, and exports still work.' : 'The map library did not load.', true);
    if (!webglOk()) return fail('This browser has no WebGL, which the map needs. The list, photos, reviews, and exports still work.', false);
    let map;
    try {
      map = new maplibregl.Map({ container: 'map', center: D.meta.center, zoom: 14.5, attributionControl: true,
        style: { version: 8, sources: { osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256, attribution: '© OpenStreetMap contributors' } },
                 layers: [{ id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-saturation': -0.6, 'raster-opacity': 0.9 } }] } });
    } catch (e) { return fail('The map could not start. The list, photos, reviews, and exports still work.', true); }
    mapInstance = map;
    mapState('loading', 'Loading map…');
    $('legend').hidden = false; $('fit').hidden = false; $('resetview').hidden = false;
    // warning-only poles keep a white fill with an amber ring so the few issue markers stay visible among the many watch items
    const category = r => !PP.isUtility(r) ? 'other' : state.colorMode === 'attachments' ? (Number.isInteger(r.att) ? (r.att >= 3 ? 'a3' : r.att >= 1 ? 'a1' : 'a0') : 'unclear') : PP.hasConditionIssue(r) ? 'issue' : PP.hasWarning(r) ? 'warn' : PP.conditionUnclear(r) ? 'unclear' : 'none';
    const toFC = rows => ({ type: 'FeatureCollection', features: rows.map(r => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [r.lon, r.lat] }, properties: { id: r.id, cat: category(r) } })) });
    const COLORS = { issue: '#c2410c', warn: '#ffffff', none: '#ffffff', unclear: '#e3e3df', other: '#bcbcb7', a3: '#2c6e6b', a1: '#9ccbc9', a0: '#ffffff' };
    const WARN_STROKE = '#9a6a00';
    let ready = false, pendingSel = null, mode = 'main', tileWarned = false;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-left');
    map.on('error', e => { if (ready && !tileWarned && e && e.error) { tileWarned = true; mapState('notice', 'Some map tiles did not load. Pole markers are unaffected.'); setTimeout(() => { if ($('map-state').classList.contains('notice')) mapState(null); }, 6000); } });
    map.on('load', () => {
      ready = true;
      map.addSource('poles', { type: 'geojson', data: toFC(filtered) });
      map.addSource('sel', { type: 'geojson', data: toFC([]) });
      map.addLayer({ id: 'poles', type: 'circle', source: 'poles', paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 13, 3.5, 16, 6, 18, 9], 'circle-color': ['match', ['get', 'cat'], ...Object.entries(COLORS).flat(), '#ffffff'], 'circle-stroke-color': ['match', ['get', 'cat'], 'warn', WARN_STROKE, '#1c1c1a'], 'circle-stroke-width': ['match', ['get', 'cat'], 'warn', 2.2, 1.2], 'circle-opacity': ['match', ['get', 'cat'], 'other', 0.6, 1] } });
      map.addLayer({ id: 'sel', type: 'circle', source: 'sel', paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 13, 9, 18, 16], 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#1b5e8a', 'circle-stroke-width': 3 } });
      map.on('click', 'poles', e => select(e.features[0].properties.id, { fromMap: true, focus: false }));
      map.on('mouseenter', 'poles', () => map.getCanvas().style.cursor = 'pointer');
      map.on('mouseleave', 'poles', () => map.getCanvas().style.cursor = '');
      if (pendingSel) api.select(pendingSel);
      api.setData(filtered);
      renderLegend();
    });
    const api = {
      setData(rows) { if (!ready) return; map.getSource('poles').setData(toFC(rows)); mapState(rows.length ? null : 'empty', rows.length ? '' : 'No poles match the current filters.'); },
      select(r, fromMap) {
        if (!ready) { pendingSel = r; return; }
        map.getSource('sel').setData(toFC(r ? [r] : []));
        if (r) map.easeTo({ center: [r.lon, r.lat], zoom: mode === 'mini' ? 17 : Math.max(map.getZoom(), 16.5), duration: fromMap ? 0 : 300 });
      },
      fit(rows) { if (!ready || !rows.length) return; const lons = rows.map(r => r.lon), lats = rows.map(r => r.lat); map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: 40, duration: 400, maxZoom: 17 }); },
      reset() { if (ready) map.fitBounds(D.meta.bbox, { padding: 20, duration: 400 }); },
      recolor() { if (ready) { map.getSource('poles').setData(toFC(filtered)); renderLegend(); } },
      resize() { map.resize(); },
      destroy() { try { map.remove(); } catch (e) { /* ignore */ } mapInstance = null; },
      // move the one map instance into the record's Location slot and back
      toMini() { const mini = $('mini'); if (!mini || mini.contains(box)) return; mini.hidden = false; mini.appendChild(box); mode = 'mini'; map.resize(); const r = byId[state.selected]; if (r && ready) map.jumpTo({ center: [r.lon, r.lat], zoom: 17 }); },
      toMain() { if (mode !== 'mini') return; $('mapwrap').insertBefore(box, $('mapwrap').firstChild); mode = 'main'; map.resize(); },
    };
    function renderLegend() {
      const cond = `<div><i style="background:#c2410c"></i>Possible condition issue</div><div><i class="warn"></i>Slight lean, watch item</div><div><i style="background:#fff"></i>No model flag</div><div><i style="background:#e3e3df"></i>Cannot tell from photos</div>`;
      const att = `<div><i style="background:#2c6e6b"></i>3 or more attachments</div><div><i style="background:#9ccbc9"></i>1 to 2 attachments</div><div><i style="background:#fff"></i>No attachments seen</div><div><i style="background:#e3e3df"></i>Cannot tell</div>`;
      $('legend').innerHTML = `<label>Color by <select id="cmode"><option value="condition"${state.colorMode === 'condition' ? ' selected' : ''}>condition flags</option><option value="attachments"${state.colorMode === 'attachments' ? ' selected' : ''}>attachment estimate</option></select></label>
        ${state.colorMode === 'condition' ? cond : att}${state.other ? '<div><i style="background:#bcbcb7"></i>Other detected object</div>' : ''}<div><i style="border-color:#1b5e8a;border-width:3px;background:none"></i>Selected</div>`;
      $('cmode').addEventListener('change', e => { state.colorMode = e.target.value; api.recolor(); });
    }
    return api;
  }
  function retryMap() {
    if (mapAttempt++ > 0) { mapState('failed', 'The map still could not load. The list, photos, reviews, and exports work without it.', false); return; }
    const start = () => { mapApi = initMap(); if (state.selected && !mapApi.failed) { mapApi.toMini(); mapApi.select(byId[state.selected]); } };
    if (typeof window.maplibregl !== 'undefined') { start(); return; }
    mapState('loading', 'Loading the map library…');
    const s = document.createElement('script'); s.src = MAP_JS; s.onload = start; s.onerror = () => { window.__maplibreFailed = true; start(); };
    document.body.appendChild(s);
  }

  // ---------- exports ----------
  const REVIEW_KEYS = ['lean', 'crossarm', 'vegetation', 'lean_slight', 'att3', 'xfmr'];
  const CSV_COLS = ['id', 'lat', 'lon', 'is_utility_pole', 'pole_type', 'model_flags', 'model_watch', 'lean', 'crossarm', 'vegetation', 'transformer', 'attachments_estimate', 'photos_assessed', 'capture_sequences', 'photo_shown_date', 'latest_available_photo_date', 'source_photo_url',
    'review_status', ...REVIEW_KEYS.map(k => `review_${k}`), 'review_note', 'review_updated'];
  const reviewCols = r => { const rv = review[r.id] || {}; const st = PP.reviewState(r, rv); return [st || '', ...REVIEW_KEYS.map(k => PP.reviewableFlags(r).includes(k) ? (rv.flags && rv.flags[k]) || '' : ''), rv.note || '', rv.updated || '']; };
  function csvRow(r) {
    const v = [r.id, r.lat, r.lon, PP.isUtility(r), r.type, PP.conditionFlags(r).join(';'), PP.warningFlags(r).join(';'), r.lean, r.xarm, r.veg, r.xfmr, Number.isInteger(r.att) ? r.att : '', r.n, r.seq, r.shown.date || '', r.latest && r.latest.date || '', r.shown.url, ...reviewCols(r)];
    return v.map(x => { const s = String(x ?? ''); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; }).join(',');
  }
  function download(name, text, type) { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; document.body.appendChild(a); a.click(); a.remove(); }
  function exportCsv(rows) { download(`pole-pass-${D.meta.slug}-filtered.csv`, [CSV_COLS.join(','), ...rows.map(csvRow), '', D.meta.attribution, 'review_* columns: decisions from this browser; blank = not reviewed. Photo interpretation, not field verification.'].join('\n'), 'text/csv'); }
  function exportGeo(rows) {
    const fc = { type: 'FeatureCollection', license: 'ODbL 1.0', attribution: D.meta.attribution, dataset_version: D.meta.version,
      review_note: 'review_* properties are decisions from this browser; null = not reviewed. Photo interpretation, not field verification.',
      features: rows.map(r => { const rc = reviewCols(r); const rv = review[r.id] || {}; return { type: 'Feature', geometry: { type: 'Point', coordinates: [r.lon, r.lat] }, properties: { id: r.id, is_utility_pole: PP.isUtility(r), pole_type: r.type, model_flags: PP.conditionFlags(r), model_watch: PP.warningFlags(r), lean: r.lean, crossarm: r.xarm, vegetation: r.veg, transformer: r.xfmr, attachments_estimate: r.att, photos_assessed: r.n, photo_shown_date: r.shown.date, source_photo_url: r.shown.url,
        review_status: rc[0] || null, ...Object.fromEntries(REVIEW_KEYS.map((k, i) => [`review_${k}`, rc[i + 1] || null])), review_note: rv.note || null, review_updated: rv.updated || null } }; }) };
    download(`pole-pass-${D.meta.slug}-filtered.geojson`, JSON.stringify(fc), 'application/geo+json');
  }
  function exportReview() { download(`pole-pass-${D.meta.slug}-review.json`, JSON.stringify({ dataset_version: D.meta.version, slug: D.meta.slug, exported: new Date().toISOString(), scope: 'Local decisions from one browser. Photo interpretation, not field verification. Not shared, not independently validated.', decisions: review }, null, 2), 'application/json'); }
  // Import: validate, classify against local decisions, preview, apply only what the user chose.
  let importPlan = null;
  function importReviewFile(file) {
    const rd = new FileReader();
    rd.onload = () => {
      let data; try { data = JSON.parse(rd.result); } catch (e) { return showImport({ errors: ['Not valid JSON.'], items: [] }, null); }
      showImport(PP.mergeReviews(review, data, byId), data);
    };
    rd.readAsText(file);
  }
  function showImport(plan, data) {
    importPlan = plan;
    const by = k => plan.items.filter(i => i.kind === k);
    const versionNote = data && data.dataset_version && data.dataset_version !== D.meta.version ? `<p class="notice">This file was exported from dataset <span class="mono">${esc(data.dataset_version)}</span>; this page is <span class="mono">${esc(D.meta.version)}</span>. Record ids are renumbered when the data is rebuilt, so decisions may land on different poles.</p>` : '';
    const list = (k, title) => by(k).length ? `<details><summary>${title}: ${by(k).length}</summary><ul class="small">${by(k).slice(0, 200).map(i => `<li><span class="mono">${esc(i.id)}</span>${i.local ? ` · here: ${esc(fmtRv(i.local))}` : ''} · file: ${esc(fmtRv(i.incoming))}${i.localNewer ? ' · your decision is newer' : ''}</li>`).join('')}</ul></details>` : '';
    const fmtRv = rv => Object.entries(rv.flags || {}).filter(([, v]) => v).map(([k, v]) => `${k}=${L.review[v] || v}`).join(', ') + (rv.note ? ` (note)` : '');
    const conflicts = by('conflict').length, unknown = by('unknown').length;
    $('import-body').innerHTML = plan.errors.length && !plan.items.length ? `<p class="notice">${plan.errors.map(esc).join('<br>')}</p>`
      : `${versionNote}${plan.errors.length ? `<p class="notice">${plan.errors.length} entries skipped as malformed.</p>` : ''}
         <p>${by('new').length} new decision${by('new').length === 1 ? '' : 's'} will be added. ${by('same').length} already match. ${conflicts} conflict with a decision already in this browser. ${unknown ? `${unknown} refer to ids not in this dataset and are skipped.` : ''}</p>
         ${list('new', 'New')}${list('conflict', 'Conflicts')}${list('unknown', 'Unknown ids')}
         ${conflicts ? `<label class="chk"><input type="checkbox" id="imp-replace"> Replace my ${conflicts} conflicting decision${conflicts === 1 ? '' : 's'} with the file's</label>` : ''}
         ${versionNote ? `<label class="chk"><input type="checkbox" id="imp-version"> I understand the dataset versions differ</label>` : ''}
         <p><button class="btn primary" id="imp-apply" ${by('new').length || conflicts ? '' : 'disabled'}>Apply</button> <button class="btn" id="imp-cancel">Cancel</button></p>`;
    openDialog('import-dlg', $('imp-review'));
  }
  function applyImport() {
    if (!importPlan) return;
    const replace = $('imp-replace') && $('imp-replace').checked, needVersion = $('imp-version');
    if (needVersion && !needVersion.checked) { needVersion.focus(); return; }
    let n = 0;
    importPlan.items.forEach(i => { if (i.kind === 'new' || (i.kind === 'conflict' && replace)) { review[i.id] = { flags: i.incoming.flags, note: i.incoming.note, updated: i.incoming.updated || new Date().toISOString() }; n++; } });
    saveReview(review); state.reviews = review; importPlan = null;
    $('import-dlg').close();
    refresh(true); if (state.selected) renderDetail();
    $('count').insertAdjacentHTML('afterbegin', `<span class="flash">${n} decision${n === 1 ? '' : 's'} imported.</span>`);
    setTimeout(() => { const f = $('count').querySelector('.flash'); if (f) f.remove(); }, 4000);
  }

  // ---------- dialogs ----------
  let dialogOpener = null;
  function openDialog(id, opener) {
    const d = $(id); if (!d || d.open) return;
    dialogOpener = opener || document.activeElement;
    d.showModal();
    const h = d.querySelector('h2, .bar'); if (h && h.focus) { h.setAttribute('tabindex', '-1'); h.focus(); }
    d.addEventListener('close', () => { if (dialogOpener && dialogOpener.focus) dialogOpener.focus(); dialogOpener = null; }, { once: true });
  }
  function openAbout(opener) { openDialog('about-dlg', opener); if (location.hash === '#about') history.replaceState(null, '', location.pathname + location.search + (state.selected ? `#pole=${encodeURIComponent(state.selected)}` : '')); }

  // ---------- wiring ----------
  function toggleMenu(btnId, menuId, open) {
    const m = $(menuId), b = $(btnId); const o = open == null ? m.hidden : open; m.hidden = !o; b.setAttribute('aria-expanded', String(o));
  }
  const MENUS = [['dates-btn', 'dates-menu'], ['more-btn', 'more-menu'], ['export-btn', 'export-menu']];
  const closeMenus = except => MENUS.forEach(([b, m]) => { if (b !== except) toggleMenu(b, m, false); });
  // Below 1560 px the review group, and below 1300 px the equipment group too, live inside "More filters" so the toolbar never wraps.
  function foldToolbar() {
    const w = window.innerWidth, fold = $('more-fold');
    [['g-review', 1560], ['g-equip', 1300]].forEach(([id, min]) => { const g = $(id); if (!g) return; const narrow = w < min; if (narrow && g.parentNode !== fold) fold.appendChild(g); else if (!narrow && g.parentNode === fold) $('toolbar').insertBefore(g, $('toolbar').querySelector('.spacer')); });
  }
  function wire() {
    document.addEventListener('click', e => {
      const chip = e.target.closest('.chip'); if (chip && chip.dataset.f) { state.flag = state.flag === chip.dataset.f && chip.dataset.f !== 'all' ? 'all' : chip.dataset.f; refresh(); return; }
      const rs = e.target.closest('#review-seg button'); if (rs) { state.review = rs.dataset.rs; refresh(); return; }
      const ab = e.target.closest('a[href="#about"]'); if (ab) { e.preventDefault(); openAbout(ab); return; }
      if (!e.target.closest('.menu')) closeMenus();
    });
    ['year-min', 'year-max'].forEach(id => $(id).addEventListener('change', () => { state.recent = false; $('recent').checked = false; readYearInputs(); refresh(); }));
    $('recent').addEventListener('change', e => { state.recent = e.target.checked; readYearInputs(); refresh(); });
    $('sort').addEventListener('change', e => { state.sort = e.target.value; refresh(); });
    $('reset').addEventListener('click', () => { resetFilters(); closeMenus(); });
    $('other').addEventListener('change', e => { state.other = e.target.checked; state.flag = 'all'; refresh(); if (mapApi) mapApi.recolor(); });
    $('years').addEventListener('change', e => { state.years = e.target.checked; refresh(); });
    $('osm').addEventListener('change', e => { state.osm = e.target.checked; refresh(); });
    MENUS.forEach(([b, m]) => $(b).addEventListener('click', () => { closeMenus(b); toggleMenu(b, m); }));
    $('count').addEventListener('click', e => { if (e.target.id === 'reset2') resetFilters(); });
    $('active').addEventListener('click', e => { if (e.target.id === 'reset4') resetFilters(); });
    $('list').addEventListener('click', e => {
      const row = e.target.closest('.row'); if (row) { select(row.dataset.id); return; }
      if (e.target.id === 'more') { state.page++; renderList(); return; }
      if (e.target.id === 'reset3') resetFilters();
    });
    $('list').addEventListener('keydown', e => {
      const rows = [...$('list').querySelectorAll('.row')]; const i = rows.indexOf(document.activeElement); if (i < 0) return;
      if (e.key === 'ArrowDown' && rows[i + 1]) { e.preventDefault(); rows[i + 1].focus(); } if (e.key === 'ArrowUp' && rows[i - 1]) { e.preventDefault(); rows[i - 1].focus(); }
    });
    $('detail').addEventListener('click', e => {
      const pt = e.target.closest && e.target.closest('.tilt .pt'); if (pt) { state.viewing = +pt.dataset.i; state.compare = false; renderDetail(); return; }
      const t = e.target.closest('button, a#enlarge'); if (!t) return;
      if (t.id === 'back' || t.id === 'close') { close(); return; }
      if (t.id === 'fullmap') { close(true); return; }
      if (t.id === 'prev') { step(-1); return; } if (t.id === 'next') { step(1); return; }
      if (t.id === 'nextun') { nextUnreviewed(); return; }
      if (t.id === 'share') { const url = location.origin + location.pathname + `#pole=${encodeURIComponent(state.selected)}`; if (navigator.clipboard) navigator.clipboard.writeText(url).then(() => { t.textContent = 'Copied'; setTimeout(() => { t.textContent = 'Copy link'; }, 1500); }); return; }
      if (t.id === 'enlarge') { e.preventDefault(); const f = byId[state.selected].frames[state.viewing]; $('lb-img').src = f.img; $('lb-img').alt = `Photo of pole ${state.selected} taken ${dateLabel(f)}`; $('lb-cap').textContent = `${state.selected} · ${dateLabel(f)}`; $('lb-src').href = f.url; openDialog('lb', t); return; }
      if (t.id === 'cmp') { state.compare = !state.compare; renderDetail(); $('cmp').focus(); return; }
      if (t.id === 'rreset') { delete review[state.selected]; saveReview(review); renderDetail(); refreshRowStatus(); renderChips(); return; }
      const k = { 'tg-outline': 'outline', 'tg-markers': 'markers', 'tg-badges': 'badges' }[t.id];
      if (k) { state[k] = !state[k]; renderDetail(); $(t.id).focus(); return; }
      if (t.dataset.i != null) { state.viewing = +t.dataset.i; state.compare = false; renderDetail(); const n = $('detail').querySelector(`[data-i="${state.viewing}"]`); if (n) n.focus(); return; }
      if (t.dataset.rf) { const rv = review[state.selected] || { flags: {}, note: '' }; rv.flags[t.dataset.rf] = rv.flags[t.dataset.rf] === t.dataset.rv ? null : t.dataset.rv; review[state.selected] = touch(rv); saveReview(review); renderDetail(); refreshRowStatus(); renderChips(); $('detail').querySelector(`[data-rf="${t.dataset.rf}"][data-rv="${t.dataset.rv}"]`).focus(); }
    });
    $('detail').addEventListener('keydown', e => {
      const pt = e.target.closest && e.target.closest('.tilt .pt'); if (!pt || (e.key !== 'Enter' && e.key !== ' ')) return;
      e.preventDefault(); state.viewing = +pt.dataset.i; state.compare = false; renderDetail(); const n = $('detail').querySelector(`.tilt .pt[data-i="${state.viewing}"]`); if (n && n.focus) n.focus();
    });
    $('detail').addEventListener('input', e => { if (e.target.id === 'rnote') { const rv = review[state.selected] || { flags: {}, note: '' }; rv.note = e.target.value; review[state.selected] = touch(rv); saveReview(review); } });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && state.selected && !document.querySelector('dialog[open]')) close(); });
    $('lb-close').addEventListener('click', () => $('lb').close());
    $('about-close').addEventListener('click', () => $('about-dlg').close());
    $('import-close').addEventListener('click', () => $('import-dlg').close());
    $('import-dlg').addEventListener('click', e => { if (e.target.id === 'imp-apply') applyImport(); else if (e.target.id === 'imp-cancel') $('import-dlg').close(); });
    $('exp-csv-f').addEventListener('click', () => exportCsv(filtered)); $('exp-geo-f').addEventListener('click', () => exportGeo(filtered)); $('exp-review').addEventListener('click', exportReview);
    $('imp-review').addEventListener('click', () => { closeMenus(); $('imp-file').value = ''; $('imp-file').click(); });
    $('imp-file').addEventListener('change', e => { const f = e.target.files && e.target.files[0]; if (f) importReviewFile(f); });
    $('fit').addEventListener('click', () => mapApi && mapApi.fit(filtered)); $('resetview').addEventListener('click', () => mapApi && mapApi.reset());
    $('mapwrap').addEventListener('click', e => { if (e.target.id === 'map-retry') retryMap(); });
    $('map-notice').addEventListener('click', e => { if (e.target.id === 'map-retry2') retryMap(); });
    $('filters-toggle').addEventListener('click', () => { const open = $('toolbar').classList.toggle('open'); $('filters-toggle').setAttribute('aria-expanded', String(open)); });
    document.querySelectorAll('.mobilebar [role=tab]').forEach(b => b.addEventListener('click', () => { state.tab = b.dataset.tab; $('ws').dataset.tab = state.tab; document.querySelectorAll('.mobilebar [role=tab]').forEach(x => x.setAttribute('aria-selected', String(x === b))); if (mapApi) mapApi.resize(); }));
    const sizeWs = () => {
      foldToolbar();
      if (window.innerWidth >= 900) { const top = $('ws').offsetTop, foot = document.querySelector('.foot'); $('ws').style.height = Math.max(480, window.innerHeight - top - (foot ? foot.offsetHeight : 0)) + 'px'; } else { $('ws').style.height = ''; }
      if (mapApi) mapApi.resize();
    };
    window.addEventListener('resize', sizeWs); sizeWs(); state.sizeWs = sizeWs;
    window.addEventListener('hashchange', () => { if (location.hash === '#about') { openAbout(null); return; } const id = parseHash(); if (id && id !== state.selected) select(id, { silent: true }); });
  }
  function refreshRowStatus() { document.querySelectorAll('.row').forEach(el => { const r = byId[el.dataset.id]; if (r) el.outerHTML = rowHtml(r); }); document.querySelectorAll('.row').forEach(el => el.setAttribute('aria-selected', String(el.dataset.id === state.selected))); }
  function parseHash() { const m = /[#&]pole=([^&]+)/.exec(location.hash); return m ? decodeURIComponent(m[1]) : null; }

  // ---------- territories ----------
  // The deployed site holds one bundle per territory under /<slug>/ and a territories.json at the root.
  // A local preview has neither, so the selector stays hidden and the plain name shows.
  function initTerritories() {
    if (typeof fetch !== 'function') return;
    fetch('../territories.json', { cache: 'no-cache' }).then(r => r.ok ? r.json() : null).then(list => {
      if (!Array.isArray(list) || !list.some(t => t.slug === D.meta.slug)) return;
      const me = list.find(t => t.slug === D.meta.slug);
      const sel = $('territory');
      sel.innerHTML = list.map(t => `<option value="${esc(t.slug)}"${t.slug === D.meta.slug ? ' selected' : ''}>${esc(t.name)}</option>`).join('');
      sel.hidden = false; $('loc-name').hidden = true;
      if (me.kind) { $('kind').textContent = L.kind[me.kind] || me.kind; $('kind').className = `kind ${esc(me.kind)}`; $('kind').hidden = false; }
      sel.addEventListener('change', e => { location.href = `../${encodeURIComponent(e.target.value)}/`; });
    }).catch(() => { /* no territory list: single-territory page */ });
  }

  // ---------- init ----------
  function init() {
    ['year-min', 'year-max'].forEach(id => { $(id).min = Y0 ?? ''; $(id).max = Y1 ?? ''; });
    $('year-min').value = Y0 ?? ''; $('year-max').value = Y1 ?? '';
    renderSummary();
    if (D.meta.osm) $('osm-wrap').hidden = false;
    wire();
    readYearInputs();
    refresh();
    const id = parseHash();
    if (id) {
      if (!select(id, { silent: true, focus: false })) $('count').insertAdjacentHTML('afterend', `<div class="empty">No record with id <span class="mono">${esc(id)}</span> in this dataset.</div>`);
    }  // no record opens by default: the first view is the list beside the map
    mapApi = initMap();
    if (state.selected && !mapApi.failed) { mapApi.toMini(); mapApi.select(byId[state.selected]); }
    if (location.hash === '#about') openAbout(null);
    initTerritories();
  }
  try { init(); } catch (e) { $('count').textContent = 'The page failed to initialize.'; console.error(e); }
  window.PolePass = { state, select, close, refresh, nextUnreviewed, retryMap, get filtered() { return filtered; }, get review() { return review; } };
})();
