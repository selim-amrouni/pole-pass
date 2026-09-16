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
   into its utility-pole class. Batch it, cache responses keyed by
   image id, on malformed JSON retry once then drop the row.
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
   slight lean only). Tests: `node --test tests/*.test.js`.

Stretch: diff against OpenStreetMap `power=pole` in the same bbox via Overpass
and report how many detected assets are absent from OSM.

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
- Public webapp is Greenpoint only, hosted on GitHub Pages. Other territories
  run on request.
- Never commit `.env`.

## Target selection
Candidates come from the Public Power Pilot Targets list (Notion, Kyber
workspace). First pass: Reading MA, Groton CT, Norwich CT (Tier 1, P1, not yet
contacted). Density from `coverage.py` decides which one proceeds.
