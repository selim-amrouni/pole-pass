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
  function applyFilters(records, state) {
    return records.filter(r => {
      if (state.other ? isUtility(r) : !isUtility(r)) return false;
      if (!FILTERS[state.flag || 'all'](r)) return false;
      if (state.yearMin != null && (r.shown.year == null || r.shown.year < state.yearMin)) return false;
      if (state.yearMax != null && (r.shown.year == null || r.shown.year > state.yearMax)) return false;
      if (state.recent && (r.shown.year == null || r.shown.year < state.recent)) return false;
      if (state.years && !spansYears(r)) return false;
      if (state.osm && !notInOsm(r)) return false;
      return true;
    });
  }

  function summary(records) {
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
           conditionFlags, hasConditionIssue, leanWarning, warningFlags, hasWarning, conditionUnclear, frameYears, spansYears, notInOsm, OSM_HEADLINE_M, FILTERS, applyFilters, summary, SORTS };
});
