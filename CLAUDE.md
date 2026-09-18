# Pole condition pass from open street imagery

## Goal
Given a bounding box for a small utility service territory, produce a GeoJSON of
utility poles with condition flags, plus a static map page that links each flag
to its source photo.

## Data
- Mapillary Graph API at https://graph.mapillary.com. Client token from the
  Mapillary developer dashboard goes in `.env` as `MAPILLARY_TOKEN`.
- Images are CC BY-SA 4.0. Derived data is ODbL. Carry attribution through to
  every output file (GeoJSON properties, map footer, CSVs).
- Enumerate images and map features from the vector tiles at zoom 14
  (tiles.mapillary.com, tilesets mly1_public and mly_map_feature_point),
  decoded by `mvt.py`. The Graph API bbox search is NOT exhaustive (the same
  50 m box returned 42 to 1781 images depending on limit, no pagination
  cursor), so never use it for counting. Use the Graph API only per entity id
  (image thumbnails, detections).
- Raw tiles cache under `data/tiles/`, per-town rows under `data/coverage/`.
- Town bboxes come from Nominatim (OSM geocoder), cached in `data/geocode/`.

## Build order (stop after each step and look at the output)
1. `coverage.py` bbox or town name -> image count, map_feature count, density
   per km2, capture date range. Run on three candidate towns before writing
   anything else. Low density = project stops here, and that is a valid result.
2. `fetch.py` map_features filtered to pole and street furniture object types,
   then nearest images per feature id. Cache everything under `data/`.
3. `classify.py` crop around each detection, send to the Anthropic API with a
   strict JSON schema: pole_present, pole_type, material, lean_severity,
   crossarm_condition, transformer_present, vegetation_contact,
   attachment_count, confidence, notes. pole_type separates real utility
   poles from street-light and traffic-signal poles that Mapillary lumps
   into its utility-pole class, and `push_brace` for the angled support
   poles Mapillary also detects (schema 2; `--redo-lean moderate,severe`
   resends old-schema lean calls, keeping the old result as `<id>.v<schema>.json`).
   Batch it, cache responses keyed by image id, on malformed JSON retry once
   then drop the row.
4. `dedupe.py` cluster detections within a few meters across consecutive
   frames, majority vote per field, keep disagreement rate as a column.
5. `validate.py` sample 50 poles, write a CSV for hand grading, compute
   precision per flag type. Do not skip. The precision table is the only part
   of the writeup anyone will trust. `grade.py` serves the sample CSV as a
   local page and writes grades back in place (blind by default).
6. `tilt.py` apparent tilt per photo from the detection outline (medial axis,
   signed degrees from vertical); `--calibrate` writes
   `data/tilt/<slug>/calibration.json` from the model's own lean calls. It is
   a property of the photo, not a lean measurement; the page says so.
7. `report.py` fills `web/index.html` and copies `web/{style.css,app.js,predicates.js}`
   into `out/<slug>/` with `data.js`, exports, and per-frame images. Page copy
   lives in the template; counts are computed in the browser from data via
   `web/predicates.js`, which mirrors `condition_flags()` and
   `warning_flags()` in dedupe.py. Two tiers: issue (orange) and watch (amber,
   slight lean only). Tests: `node --test tests/*.test.js` and `uv run python3 -m unittest discover -s tests`.
   UI pass of 2026-09-17: About lives in a dialog; filters are grouped (issues,
   equipment, review status, More filters) and AND-combined with one flag chip
   at a time; the representative date per pole is `shown.ts` and "recent" is 24
   months from view time; review decisions are browser-local with `updated`
   timestamps, exported as `review_*` columns and importable with a conflict
   preview; the map has loading / empty / failed states with one retry.
   `out/<slug>/crops/` are named by detection id and pruned on every report run
   (pole ids renumber; `resized()` skips existing files, which once left stale
   thumbnails after the merge-rule change). Pole-id references in code or tests
   go stale after any dedupe change; key by detection id or find by predicate.

7b. `district.py` carves one village out of a town and makes it a first-class area:
   a Voronoi cell of the OSM place nodes, clipped to the town ring, written as
   `data/district/<slug>/district.geojson` plus a clipped copy of the town's
   coverage rows. Newton's villages have no legal boundary and OSM holds them
   only as points, so the cell is a partition, not a surveyed line, and the page
   says so. `district.ring(slug)` is what every step clips to (district cell, else
   the OSM town relation); `district.since_ms(slug)` records the imagery cutoff
   with the area so reruns cannot widen it. The row filter must test the town ring
   AND nearest-centre: Lower Falls sits on the Wellesley line and nearest-centre
   alone annexed 405 poles from the next town.

8. `osm.py` Overpass query for `power=pole` and `man_made=utility_pole` nodes in
   the bbox, cached forever under `data/osm/<slug>/`; nearest node per utility
   record at 8/15/25 m, 15 m is the headline. report.py adds `meta.osm`, a
   per-record `osm` distance, the "Not in OpenStreetMap" filter, and the OSM
   attribution only when the diff exists. Zero OSM poles is a real result
   (Greenpoint has none), not an error.

Deferred (need data or a product decision, not built): a human-reviewed validation
sample across areas and flag types with sample sizes (issue #1); matching against a
utility's own GIS with explicit uncertain matches (#19); a coverage view of where
usable imagery exists and how old it is (#18); change review across dated views
without treating visual differences as confirmed changes; shared review with accounts
only if a real user needs it.

## Constraints
- No model training, no GPU.
- Everything must rerun from cache without hitting either API. Every script
  reads `data/` first and only calls out on a cache miss.
- Every number in the final writeup traces back to a file in `data/`.
- No fabricated or placeholder data, ever. Empty result = empty output.
- Stdlib only unless a dependency is agreed on first. Agreed deps: pillow,
  anthropic. Managed with uv (`uv sync`, run with `uv run python3 x.py`).
- Classifier: `claude-sonnet-5` through the Batches API. Haiku 4.5 is the
  cost fallback per field once precision is graded.
- Public webapp on GitHub Pages: one bundle per territory under `/<slug>/`,
  listed in `web/territories.json` with a kind (city, suburb, or backcountry)
  so the header can brand the contrast. Greenpoint (city), Hardwick VT
  (backcountry), Reading MA (suburb) as of 2026-09-17. The root is a general
  landing page (`web/landing/`) with one card per territory, fed by
  `territories.json` = `web/territories.json` entries + each bundle's
  `out/<slug>/summary.json` (written by report.py). Deploy with
  `./deploy.sh <slug> [<slug> ...]`; `--dry-run` previews. Further
  territories run on request.
- Never commit `.env`.

## Target selection
Candidates come from the Public Power Pilot Targets list (Notion, Kyber
workspace). First pass: Reading MA, Groton CT, Norwich CT (Tier 1, P1, not yet
contacted). Density from `coverage.py` decides which one proceeds.
