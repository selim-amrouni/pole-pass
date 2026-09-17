# Pole Pass: what public street photos say about a town's poles

Written 2026-09-17. Every number below names the file under `data/` or `out/` it was read from.
Those directories are not in git; the scripts that produce them are, and they rerun from cache.

## What was built

A pipeline from a town name to a map of its utility poles, each with a model assessment and the
photo it came from. Mapillary's vector tiles give the pole detections and their photos; a vision
model (claude-sonnet-5 through the Batches API) answers a fixed schema per photo; photos of one
pole are merged with a vote per field; the result is a static page per territory, an index page,
CSV and GeoJSON exports, and a comparison with OpenStreetMap. No training, no GPU, stdlib plus
Pillow and the Anthropic SDK.

Three territories were run to show the contrast between kinds of places:

| Territory | Kind | Why |
|---|---|---|
| Greenpoint, Brooklyn | city | dense, panoramic captures, street lights mixed in with poles |
| Hardwick, Vermont | backcountry | a state highway videolog every two years, wooded roadsides |
| Reading, Massachusetts | suburb | a municipal light department on the Public Power pilot list |

## The territories side by side

Sources per row: coverage = `data/coverage/<slug>/summary.json`; poles = `data/poles/<slug>/summary.json`;
bundle = `out/<slug>/summary.json` (written by report.py from the records); tilt =
`data/tilt/<slug>/calibration.json`; osm = `data/osm/<slug>/summary.json`; cost = `costs.py`
(sums the usage stored in every `data/{classify,locate}/<slug>/results/*.json`).
Slugs: `greenpoint-brooklyn-new-york`, `hardwick-vermont`, `reading-massachusetts`.

| | Greenpoint | Hardwick | Reading | source |
|---|---:|---:|---:|---|
| Area, km² | 6.19 | 182.41 | 39.42 | coverage `area_km2` |
| Mapillary images in bbox | 105,211 | 71,371 | 98,589 | coverage `images` |
| Images per km² | 17,002 | 391 | 2,501 | coverage `images_per_km2` |
| Panoramas | 18,372 | 0 | 19,260 | coverage `pano_images` |
| Capture dates | 2014-08 to 2026-09 | 2013-09 to 2022-11 | 2015-03 to 2026-06 | coverage `capture_first`, `capture_last` |
| Utility-pole map features | 1,725 | 2,718 | 3,330 | coverage `features_by_value` |
| Photos assessed | 3,254 | 5,357 | 5,929 | poles `frames_classified` |
| Records | 962 | 1,731 | 1,739 | poles `records` |
| Utility poles | 706 | 1,591 | 1,589 | poles `utility_records` |
| Other detected objects | 256 | 140 | 150 | poles `other_records` |
| Records merged from several features | 236 | 353 | 525 | poles `records_merged_from_multiple_features` |
| Utility poles with a condition issue | 90 | 460 | 500 | poles `utility_with_condition_flag` |
| of which lean (moderate or severe) | 20 | 26 | 32 | poles `utility_flag_counts.lean` |
| of which vegetation touching | 70 | 442 | 474 | poles `utility_flag_counts.vegetation` |
| of which crossarm damaged | 1 | 1 | 0 | poles `utility_flag_counts.crossarm` |
| Severe lean | 10 | 2 | 6 | records with `lean_severity == severe` in `data/poles/<slug>/poles.jsonl` |
| Watch items (slight lean) | 431 | 801 | 462 | poles `utility_with_warning_flag` |
| Transformer visible | 21 | 67 | 55 | poles `utility_transformer` |
| 3+ estimated attachments | 14 | 0 | 12 | poles `utility_3plus_attachments` |
| Push braces | 0 | 2 | 0 | poles `records_by_type.push_brace` |
| Photographed in 2+ years | 24 | 1,148 | 74 | bundle `counts.multi_year` |
| Straight-pole tilt noise, flat photos: median / p90, ° | 2.4 / 7.9 (n=759) | 1.9 / 5.3 (n=2,189) | 2.2 / 5.5 (n=698) | tilt `by_model_lean_call.none.flat` |
| Severe-lean photos: median tilt, ° | 8.9 (n=40) | 9.8 (n=18) | 11.0 (n=15) | tilt `by_model_lean_call.severe.flat` |
| OSM pole nodes in bbox | 0 | 134 | 189 | osm `osm_nodes` |
| Utility poles with an OSM pole within 15 m | 0 | 0 | 119 | osm `detected_with_osm_within_m.15` |
| API spend, USD | 8.73 | 12.28 | 14.87 | costs.py |

Watch items are common (slight lean on a third to a half of the poles) and the page never
counts them as issues. Vegetation dominates the issues in Hardwick and Reading for the reason
given below. Reading is the only territory where OSM has mapped some of the same poles: 119 of
1,589 within 15 m (50 within 8 m, 180 within 25 m; osm `detected_with_osm_within_m`).

Severe lean by hand (notes and outline tilt in `data/poles/<slug>/poles.jsonl`; photos not
independently inspected): Hardwick's two remaining severe records look real (hard-00180 carries
marker tape and reads 10° in the outline; hard-01250 is two poles leaning on each other, maybe
decommissioned). Reading's six all have outline tilts of 6° to 12° and consistent notes;
read-01073 is described as "severely leaning/broken with wires holding it up". Greenpoint's ten
include curved and bent poles against building lines, and the set changed on a resend (8 to
10), so a single severe call is a reason to look, not a finding.

## What changed the results

- **Push braces.** All three severe-lean records in Hardwick's first pass involved a push brace: a
  support pole set at an angle against a straight pole, which Mapillary detects as a utility pole
  and the first schema read as a severely leaning pole. The classifier now has a `push_brace` type; braces are listed
  under other detected objects and never flagged. For the two territories classified before that,
  only the photos the first pass had called moderate or severe were resent (`classify.py
  --redo-lean moderate,severe`, direct mode); the superseded results are kept as `<id>.v1.json`.
- **Batch size.** One Batches API submission caps at 256 MB, which ~5,000 image requests exceed.
  Submissions go in chunks of 2,000, all at once.
- **Detection outlines.** A Mapillary detection can have several rings; drawing only the first
  showed a fragment. All rings are drawn and the tilt uses the largest.
- **Feature merge.** Features within 8 m used to merge by single linkage, which chained poles 25
  to 30 m apart into one record through intermediates. A hand check of 30 merged Reading records
  (`merge_check.py`, `data/validate/reading-massachusetts/merges.csv`) found 7 over-merges in 29
  answered, worst in those chains and in pairs 4 to 8 m apart on opposite sides of a street. The
  merge now needs every pair within 8 m (complete linkage). Record counts rose: Greenpoint 865 to
  962, Hardwick 1,672 to 1,731, Reading 1,519 to 1,739. The street-side case at 4 to 8 m is not
  fixed by a radius; a photo-based test (do the two features' outlines overlap in shared photos?)
  matched 22 of 29 hand verdicts but would add 22% more records, so it was not adopted.

## What is not verified

- **Precision is ungraded.** `validate.py` writes a 50-pole sample and `grade.py` serves it, but no
  expert has graded it. The page says so on every territory. Until that table exists, every count
  here is a model estimate, and the severe-lean check by hand (below) is the only spot check.
- **Positions.** The model-located markers on photos and the Mapillary feature positions on the
  map were not checked against ground truth.
- **Vegetation in the woods.** "Touching" is judged from overlap in the photo; in Hardwick a
  branch behind the pole reads as touching it. The count is an upper bound.
- **OSM as a baseline.** OpenStreetMap has almost no pole data in these towns, so "not in OSM" is
  nearly every pole. It says nothing about the utility's own GIS.
