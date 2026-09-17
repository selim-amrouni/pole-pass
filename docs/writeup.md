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

## Tables

<!-- filled from data/ after the runs; see the sections below -->

## What changed the results

- **Push braces.** Every severe-lean record in Hardwick was a push brace: a support pole set at an
  angle against a straight pole, which Mapillary detects as a utility pole and the first schema
  read as a severely leaning pole. The classifier now has a `push_brace` type; braces are listed
  under other detected objects and never flagged. For the two territories classified before that,
  only the photos the first pass had called moderate or severe were resent (`classify.py
  --redo-lean moderate,severe`, direct mode); the superseded results are kept as `<id>.v1.json`.
- **Batch size.** One Batches API submission caps at 256 MB, which ~5,000 image requests exceed.
  Submissions go in chunks of 2,000, all at once.
- **Detection outlines.** A Mapillary detection can have several rings; drawing only the first
  showed a fragment. All rings are drawn and the tilt uses the largest.

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
