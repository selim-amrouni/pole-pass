/* Pole Pass landing page. Renders the hero example and one card per area from territories.json (written by deploy.sh:
   name and kind from web/territories.json, stats from each bundle's summary.json). No count lives in the HTML.
   Exposes window.PoleLanding = { render, renderHero, freshness, cardHtml, forwardPoleLink } so tests can drive it without fetch. */
(function () {
  'use strict';
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = n => Number.isFinite(n) ? n.toLocaleString('en-US') : '';
  const KIND = { city: 'Urban', suburb: 'Suburban', backcountry: 'Rural' };
  const RECENT_MONTHS = 24;
  const HERO_SLUG = 'reading-massachusetts';

  // Share of poles whose representative photo (the one shown on the area page) was taken within the window,
  // from the per-month histogram of those dates; computed at view time from `now`, never from the build date.
  function freshness(stats, now) {
    const c = stats && stats.counts || {}, hist = stats && stats.shown_by_month || null;
    const range = c.photo_year_first && c.photo_year_last ? (c.photo_year_first === c.photo_year_last ? `Photos ${c.photo_year_first}` : `Photos ${c.photo_year_first} to ${c.photo_year_last}`) : 'Photo dates unknown';
    if (!hist) return { range, share: null, recent: null, total: null, text: range };
    const d = new Date(now), cutoff = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() - RECENT_MONTHS + 1, 1)).toISOString().slice(0, 7);
    let total = 0, recent = 0;
    Object.entries(hist).forEach(([ym, n]) => { total += n; if (ym >= cutoff) recent += n; });
    const undated = stats.undated || 0;
    const share = total ? Math.round(100 * recent / total) : null;
    const text = !total ? range : recent ? `${range} · ${share}% within ${RECENT_MONTHS} months` : `${range} · none within ${RECENT_MONTHS} months`;
    return { range, share, recent, total, undated, text: undated ? `${text} · ${num(undated)} undated` : text };
  }

  function cardHtml(t, now, best) {
    const s = t.stats || {}, c = s.counts || {};
    const kind = KIND[t.kind] ? `<span class="kind ${esc(t.kind)}">${esc(KIND[t.kind])}</span>` : '';
    const badge = best ? `<span class="badge" title="Largest share of photos taken within ${RECENT_MONTHS} months">Best imagery</span>` : '';
    const pic = s.example && s.example.crop ? `<img src="${esc(`${encodeURIComponent(t.slug)}/${s.example.crop}`)}" alt="Street photo of a pole in ${esc(t.name || t.slug)}, ${esc(s.example.date || 'date unknown')}" loading="lazy" onerror="this.parentNode.classList.add('nophoto');this.remove()"><span class="ph" hidden>Photo unavailable</span>`
      : '<span class="ph">No photo published</span>';
    const fr = freshness(s, now);
    return `<article class="card" data-slug="${esc(t.slug)}">
      <div class="pic">${pic}</div>
      <div class="body">
        <div class="top"><span class="name">${esc(t.name || t.slug)}</span>${kind}</div>
        ${Number.isFinite(c.utility) ? `<div class="n"><b>${num(c.utility)}</b> pole records</div>` : '<div class="n muted">Counts not published</div>'}
        <div class="fresh">${esc(fr.text)}${badge}</div>
        <a class="btn primary" href="${encodeURIComponent(t.slug)}/">Explore ${esc((t.name || t.slug).split(',')[0])}</a>
      </div></article>`;
  }

  // The single "Best imagery" badge goes to the area with the largest share of recent photos,
  // computed here from the same freshness() the cards print. Never hand-assigned, so it follows the
  // data instead of going stale. No badge when nothing has any recent imagery to be best at.
  function bestImagery(list, now) {
    let best = null, top = 0, tied = false;
    list.forEach(t => {
      const sh = freshness(t.stats || {}, now).share;
      if (!Number.isFinite(sh) || sh <= 0) return;
      if (sh > top) { top = sh; best = t.slug; tied = false; } else if (sh === top) { tied = true; }
    });
    return tied ? null : best;   // a tie would claim a difference the numbers do not show
  }
  function render(list, el, now) {
    const ok = Array.isArray(list) ? list.filter(t => t && typeof t.slug === 'string' && t.slug) : [];
    if (!ok.length) { el.innerHTML = '<div class="empty">No areas are published yet.</div>'; return 0; }
    const at = now || Date.now(), best = bestImagery(ok, at);
    el.innerHTML = ok.map(t => cardHtml(t, at, t.slug === best)).join('');
    return ok.length;
  }

  // Hero: one real photo from the example record of the preferred area, with at most three model observations drawn on it.
  function renderHero(list, frame, cap) {
    const ok = Array.isArray(list) ? list.filter(t => t && t.slug && t.stats && t.stats.example && t.stats.example.crop) : [];
    const t = ok.find(x => x.slug === HERO_SLUG) || ok[0];
    if (!t) { frame.innerHTML = '<div class="ph">Example photo unavailable.</div>'; cap.textContent = ''; return null; }
    const ex = t.stats.example, m = ex.marks || {};
    const src = `${encodeURIComponent(t.slug)}/${ex.crop}`;
    const W = 1000, H = 1250, X = p => (p[0] * W).toFixed(1), Y = p => (p[1] * H).toFixed(1), R = 22;
    // At most three marks, most telling first: the pole axis, then the transformer, then whatever
    // attachments still fit. The transformer used to come last and was dropped whenever the model
    // had found two attachments -- losing the single most legible thing in the picture, and with it
    // the caption line that says the model found one.
    let svg = '', keys = [], marks = 0;
    if (m.top && m.base) { svg += `<line class="axis" x1="${X(m.top)}" y1="${Y(m.top)}" x2="${X(m.base)}" y2="${Y(m.base)}"/>`; keys.push(['axis', 'Pole axis']); marks++; }
    if (m.xfmr) { svg += `<rect class="mk xfmr" x="${X(m.xfmr) - R}" y="${Y(m.xfmr) - R}" width="${2 * R}" height="${2 * R}"/>`; keys.push(['xfmr', 'Transformer']); marks++; }
    const atts = (m.att || []).filter(a => a && a.p).slice(0, Math.max(0, 3 - marks));
    atts.forEach((a, i) => { svg += `<circle class="mk att" cx="${X(a.p)}" cy="${Y(a.p)}" r="${R}"/><text x="${X(a.p)}" y="${(+Y(a.p) + 8).toFixed(1)}" text-anchor="middle" font-size="24">${i + 1}</text>`; marks++; });
    if (atts.length) keys.push(['att', `Attachment${atts.length > 1 ? 's' : ''}: ${atts.map(a => a.l).filter(Boolean).join(', ') || 'communication'}`]);
    const G = { axis: '<line x1="7" y1="1" x2="7" y2="13"/>', att: '<circle cx="7" cy="7" r="5.5"/>', xfmr: '<rect x="2" y="2" width="10" height="10"/>' };
    const key = keys.length ? `<div class="key"><span class="kt">Model observations</span>${keys.map(([k, l]) => `<span><svg viewBox="0 0 14 14" class="g ${k}" aria-hidden="true">${G[k]}</svg>${esc(l)}</span>`).join('')}</div>` : '';
    frame.innerHTML = `<img src="${esc(src)}" alt="Street photo of pole ${esc(ex.id)} in ${esc(t.name)}, taken ${esc(ex.date || 'date unknown')}, with the model's marked observations" onerror="this.parentNode.innerHTML='<div class=ph>Example photo unavailable.</div>'">${svg ? `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">${svg}</svg>` : ''}${key}`;
    const flagNote = ex.flags && ex.flags.length ? 'Model flags on this record have not been verified.' : 'No condition flagged on this record.';
    // Say what the reader can see in the picture. The old caption led with the absence of a flag,
    // which reads as "nothing found" on the one example the homepage gets to show.
    // Count what the model found, not how many markers fitted: `atts` is clipped by the three-mark
    // render budget above, and the sentence is a claim about the model rather than about the figure.
    const nAtt = (m.att || []).filter(a => a && a.p).length;
    const nFlag = (ex.flags || []).length;
    const shown = [m.xfmr && 'a transformer',
                   nAtt && `${nAtt} attachment${nAtt > 1 ? 's' : ''}`,
                   nFlag && `${nFlag} possible condition flag${nFlag > 1 ? 's' : ''}`].filter(Boolean);
    const phrase = shown.length < 2 ? shown.join('') : `${shown.slice(0, -1).join(', ')} and ${shown[shown.length - 1]}`;
    const found = shown.length ? `Model identified ${phrase} on this pole. ` : '';
    cap.innerHTML = `${esc(found)}${esc(t.name)} · photo ${esc(ex.date || 'date unknown')}${ex.by ? ` by ${esc(ex.by)}` : ''} · <a href="${encodeURIComponent(t.slug)}/#pole=${encodeURIComponent(ex.id)}">Open this record</a><br><span class="small">Markers are model observations, not measurements. ${flagNote} Photo © Mapillary contributors, CC BY-SA 4.0${ex.url ? ` · <a href="${esc(ex.url)}" target="_blank" rel="noopener">source</a>` : ''}.</span>`;
    return t.slug;
  }

  // Old root links carried #pole=<id>; keep them working. Record ids are "<first 4 of slug>-NNNNN"
  // (dedupe.py), so send the link to the area it actually names rather than to whichever card
  // happens to be first -- that assumption broke the moment the cards were reordered.
  function forwardPoleLink(list, loc) {
    const m = /[#&]pole=([^&]*)/.exec(loc.hash || '');
    if (!m || !Array.isArray(list) || !list.length) return null;
    let id = m[1];
    try { id = decodeURIComponent(id); } catch (e) { /* malformed escape: match on the raw text */ }
    const prefix = id.slice(0, 4).toLowerCase();
    const t = list.find(x => x && typeof x.slug === 'string' && x.slug.slice(0, 4).toLowerCase() === prefix) || list[0];
    return `${encodeURIComponent(t.slug)}/${loc.hash}`;
  }

  function init() {
    const el = document.getElementById('cards');
    if (!el || typeof fetch !== 'function') return;
    fetch('territories.json', { cache: 'no-cache' }).then(r => r.ok ? r.json() : null).then(list => {
      const to = forwardPoleLink(list, location);
      if (to) { location.replace(to); return; }
      renderHero(list, document.getElementById('hero-frame'), document.getElementById('hero-cap'));
      render(list, el, Date.now());
    }).catch(() => { render([], el); renderHero([], document.getElementById('hero-frame'), document.getElementById('hero-cap')); });
  }

  if (typeof window !== 'undefined') { window.PoleLanding = { render, renderHero, freshness, cardHtml, bestImagery, forwardPoleLink, RECENT_MONTHS }; if (typeof document !== 'undefined' && document.getElementById) init(); }
})();
