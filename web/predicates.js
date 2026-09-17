// Shared predicates for Pole Pass. One definition for summaries, badges, filters, sorting, exports.
// Works in the browser (window.PP) and in node (module.exports). Keep in sync with dedupe.py.
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.PP = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  const isUtility = r => r.util === true;
  const possibleLean = r => r.lean === 'moderate' || r.lean === 'severe';
  const crossarmDamage = r => r.xarm === 'damaged';
  const vegetationContact = r => r.veg === 'touching';
  const transformerVisible = r => r.xfmr === true;
  const attachments3 = r => isUtility(r) && Number.isInteger(r.att) && r.att >= 3;
  // "Possible condition issue": any of the three condition flags. Attachments and transformers are not condition issues.
  const conditionFlags = r => [possibleLean(r) && 'lean', crossarmDamage(r) && 'crossarm', vegetationContact(r) && 'vegetation'].filter(Boolean);
  const hasConditionIssue = r => conditionFlags(r).length > 0;
  // "Watch item": one tier below a condition issue. Slight lean only, for now. Mirrors warning_flags() in dedupe.py.
  const leanWarning = r => r.lean === 'slight';
  const warningFlags = r => [leanWarning(r) && 'lean_slight'].filter(Boolean);
  const hasWarning = r => warningFlags(r).length > 0;
  // Distinct capture years among the assessed photos, ascending. Two or more: the record can be compared across years.
  const frameYears = r => [...new Set((r.frames || []).map(f => f.year).filter(y => y != null))].sort((a, b) => a - b);
  const spansYears = r => frameYears(r).length >= 2;
  // All three condition fields unreadable: the model could not assess condition from any photo.
  const conditionUnclear = r => r.lean === 'unclear' && r.xarm === 'unclear' && r.veg === 'unclear';
  // r.osm: distance in m to the nearest OpenStreetMap pole node within 25 m (osm.py), null when none, absent when OSM was not checked.
  // "Not in OSM" uses the 15 m headline radius: a utility record with no OSM pole within it. Mirrors osm.py HEADLINE_M.
  const OSM_HEADLINE_M = 15;
  const notInOsm = r => isUtility(r) && !(Number.isFinite(r.osm) && r.osm <= OSM_HEADLINE_M);

  // ---- dates. The representative date of a record is the capture date of the photo shown (r.shown.ts):
  // the newest assessed photo where the pole is readable, else the largest. Ages are computed from that
  // capture date at view time, never from the dataset build date. Unknown stays unknown.
  const RECENT_MONTHS = 24;
  const MS_MONTH = 365.25 / 12 * 86400000;
  const monthsSince = (ts, now) => Number.isFinite(ts) && ts > 0 ? Math.floor((now - ts) / MS_MONTH) : null;
  const isRecent = (r, now) => { const m = monthsSince(r.shown && r.shown.ts, now); return m != null && m >= 0 && m < RECENT_MONTHS; };
  function ageLabel(ts, now) {
    const m = monthsSince(ts, now);
    if (m == null) return 'date unknown';
    if (m < 1) return 'this month';
    if (m < 12) return `${m} mo ago`;
    const y = Math.floor(m / 12), rest = m % 12;
    return `${y} yr${rest ? ` ${rest} mo` : ''} ago`;
  }

  // ---- reviewable flags and per-photo support. The flag keys with review buttons; att3 and xfmr are equipment
  // observations, kept reviewable because they were reviewable before and saved decisions reference them.
  const reviewableFlags = r => isUtility(r) ? [...conditionFlags(r), ...warningFlags(r), attachments3(r) && 'att3', transformerVisible(r) && 'xfmr'].filter(Boolean) : [];
  const FRAME_SUPPORTS = {
    lean: f => f.lean === 'moderate' || f.lean === 'severe',
    lean_slight: f => f.lean === 'slight',
    crossarm: f => f.xarm === 'damaged',
    vegetation: f => f.veg === 'touching',
    att3: f => Number.isInteger(f.att) && f.att >= 3,
    xfmr: f => f.xfmr === true,
  };
  const FRAME_UNCLEAR = {
    lean: f => f.lean === 'unclear' || f.lean == null, lean_slight: f => f.lean === 'unclear' || f.lean == null,
    crossarm: f => f.xarm === 'unclear' || f.xarm == null, vegetation: f => f.veg === 'unclear' || f.veg == null,
    att3: f => !Number.isInteger(f.att), xfmr: f => f.xfmr == null,
  };
  // Which dated photos support a combined flag, and what the latest assessed photo shows. Photo evidence only:
  // a flag absent from a newer photo is not a resolved condition; angle, distance, and image quality differ.
  function flagSupport(r, key) {
    const frames = r.frames || [], sup = FRAME_SUPPORTS[key], unc = FRAME_UNCLEAR[key];
    if (!sup) return null;
    const supporting = frames.filter(sup);
    const dated = frames.filter(f => Number.isFinite(f.ts));
    const latest = dated.length ? dated.reduce((a, b) => (b.ts > a.ts ? b : a)) : null;
    const latestStatus = !latest ? null : sup(latest) ? 'supports' : unc(latest) ? 'unclear' : 'absent';
    return { n: frames.length, supporting, drives: new Set(supporting.map(f => f.seq).filter(Boolean)).size, latest, latestStatus };
  }

  // ---- review decisions (browser-local). rv = { flags: {key: 'supported'|'not_supported'|'cannot_tell'|null}, note, updated? }
  const REVIEW_VALUES = ['supported', 'not_supported', 'cannot_tell'];
  const decided = (rv, k) => !!(rv && rv.flags && REVIEW_VALUES.includes(rv.flags[k]));
  // 'reviewed' only when every reviewable flag has a decision; notes alone never count. null: nothing to review.
  function reviewState(r, rv) {
    const keys = reviewableFlags(r);
    if (!keys.length) return null;
    const n = keys.filter(k => decided(rv, k)).length;
    return n === keys.length ? 'reviewed' : n ? 'partial' : 'unreviewed';
  }
  // Overall verdict for a row: every decided flag supported -> 'ok'; any not supported -> 'no'; else 'ct'.
  function reviewVerdict(rv) {
    const vals = rv && rv.flags ? Object.values(rv.flags).filter(v => REVIEW_VALUES.includes(v)) : [];
    if (!vals.length) return null;
    if (vals.every(x => x === 'supported')) return 'ok';
    if (vals.some(x => x === 'not_supported')) return 'no';
    return 'ct';
  }
  function reviewProgress(records, reviews) {
    const out = { reviewable: 0, reviewed: 0, partial: 0 };
    records.forEach(r => { const s = reviewState(r, reviews && reviews[r.id]); if (!s) return; out.reviewable++; if (s === 'reviewed') out.reviewed++; else if (s === 'partial') out.partial++; });
    return out;
  }
  // Import: classify each incoming decision against the local one. Never overwrites silently; the caller applies
  // 'new' by default and 'conflict' only on explicit request. Returns { errors: [..], items: [{id, kind, local, incoming}] }.
  function mergeReviews(local, incoming, byId) {
    const errors = [], items = [];
    if (!incoming || typeof incoming !== 'object' || !incoming.decisions || typeof incoming.decisions !== 'object' || Array.isArray(incoming.decisions)) {
      return { errors: ['Not a Pole Pass review file: expected an object with "decisions".'], items };
    }
    Object.entries(incoming.decisions).forEach(([id, rv]) => {
      if (!rv || typeof rv !== 'object' || (rv.flags && typeof rv.flags !== 'object')) { errors.push(`${id}: malformed decision`); return; }
      const flags = rv.flags || {};
      const bad = Object.entries(flags).find(([k, v]) => !FRAME_SUPPORTS[k] || (v != null && !REVIEW_VALUES.includes(v)));
      if (bad) { errors.push(`${id}: unknown flag or value "${bad[0]}: ${bad[1]}"`); return; }
      if (rv.note != null && typeof rv.note !== 'string') { errors.push(`${id}: note is not text`); return; }
      const clean = { flags, note: rv.note || '', updated: typeof rv.updated === 'string' ? rv.updated : null };
      if (!byId[id]) { items.push({ id, kind: 'unknown', incoming: clean, local: null }); return; }
      const mine = local[id];
      if (!mine || !reviewVerdict(mine) && !(mine.note || '')) { items.push({ id, kind: 'new', incoming: clean, local: mine || null }); return; }
      const same = JSON.stringify({ f: Object.fromEntries(Object.entries(mine.flags || {}).filter(([, v]) => v)), n: mine.note || '' })
        === JSON.stringify({ f: Object.fromEntries(Object.entries(flags).filter(([, v]) => v)), n: clean.note });
      items.push({ id, kind: same ? 'same' : 'conflict', incoming: clean, local: mine, localNewer: !!(mine.updated && clean.updated && mine.updated > clean.updated) });
    });
    return { errors, items };
  }

  const FILTERS = {
    all: r => true,
    lean: possibleLean,
    lean_slight: leanWarning,
    xarm: crossarmDamage,
    veg: vegetationContact,
    att3: attachments3,
    xfmr: transformerVisible,
  };

  // Population for the main workflow: utility-pole records. Other detected objects only when asked for.
  // Filters combine with AND: one flag chip, the date range or the 24-month recent switch, the review status,
  // and the extra checkboxes. state.reviews (id -> decision) and state.now are supplied by the page.
  function applyFilters(records, state) {
    const now = state.now || Date.now();
    return records.filter(r => {
      if (state.other ? isUtility(r) : !isUtility(r)) return false;
      if (!FILTERS[state.flag || 'all'](r)) return false;
      if (state.yearMin != null && (r.shown.year == null || r.shown.year < state.yearMin)) return false;
      if (state.yearMax != null && (r.shown.year == null || r.shown.year > state.yearMax)) return false;
      if (state.recent === true && !isRecent(r, now)) return false;
      if (typeof state.recent === 'number' && (r.shown.year == null || r.shown.year < state.recent)) return false;  // older year form
      if (state.years && !spansYears(r)) return false;
      if (state.osm && !notInOsm(r)) return false;
      if (state.review && state.review !== 'all') {
        const s = reviewState(r, state.reviews && state.reviews[r.id]);
        if (state.review === 'reviewed' ? s !== 'reviewed' : !(s === 'unreviewed' || s === 'partial')) return false;
      }
      return true;
    });
  }

  function summary(records, now) {
    const util = records.filter(isUtility);
    const years = util.map(r => r.shown.year).filter(y => y != null);
    return {
      records: records.length,
      utility: util.length,
      other: records.length - util.length,
      conditionIssues: util.filter(hasConditionIssue).length,
      warnings: util.filter(hasWarning).length,
      spansYears: util.filter(spansYears).length,
      notInOsm: util.filter(notInOsm).length,
      attachments3: util.filter(attachments3).length,
      transformer: util.filter(transformerVisible).length,
      lean: util.filter(possibleLean).length,
      crossarm: util.filter(crossarmDamage).length,
      vegetation: util.filter(vegetationContact).length,
      recent: util.filter(r => isRecent(r, now || Date.now())).length,
      undated: util.length - years.length,
      yearMin: years.length ? Math.min(...years) : null,
      yearMax: years.length ? Math.max(...years) : null,
    };
  }

  const SORTS = {
    date_desc: (a, b) => (b.shown.ts || 0) - (a.shown.ts || 0),
    date_asc: (a, b) => (a.shown.ts || 0) - (b.shown.ts || 0),
    att_desc: (a, b) => (b.att ?? -1) - (a.att ?? -1) || (b.shown.ts || 0) - (a.shown.ts || 0),
    flags_desc: (a, b) => conditionFlags(b).length - conditionFlags(a).length || warningFlags(b).length - warningFlags(a).length || (b.shown.ts || 0) - (a.shown.ts || 0),
  };

  return { isUtility, possibleLean, crossarmDamage, vegetationContact, transformerVisible, attachments3,
           conditionFlags, hasConditionIssue, leanWarning, warningFlags, hasWarning, conditionUnclear, frameYears, spansYears, notInOsm, OSM_HEADLINE_M,
           RECENT_MONTHS, monthsSince, isRecent, ageLabel, reviewableFlags, flagSupport, REVIEW_VALUES, reviewState, reviewVerdict, reviewProgress, mergeReviews,
           FILTERS, applyFilters, summary, SORTS };
});
