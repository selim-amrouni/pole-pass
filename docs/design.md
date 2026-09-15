# Pole Pass — Design Doc

Pole condition and joint-use pass from open street imagery, packaged as a
rerunnable tool. Dev territory: Greenpoint, Brooklyn. First real target:
Norwich Public Utilities, CT (GM Jeff Brining, started Jan 2026).

## Problem

A municipal electric utility with 10k to 50k customers owns tens of thousands
of wooden poles and knows less about them than it would like. Ground-line
inspections run on a roughly ten-year cycle and are outsourced. Joint-use
attachments (telecom, cable, fiber) are rented per pole per year, and small
munis rarely audit them, so unbilled attachments and overloaded poles go
unnoticed. Storm hardening and vegetation programs are prioritized from
whatever the crews happen to have seen. Nobody at a muni this size has a budget
line for "look at every pole from the street", and the vendors who do it charge
per pole.

Meanwhile Mapillary already holds a photo of most of those poles. Norwich has
378k street-level images and 19,085 Mapillary-detected utility poles inside its
bbox, refreshed every year since 2020. Greenpoint has 105k images and 1,725
poles in 6 km2. That imagery is free, licensed for derived work, and nobody at
the muni has looked at it.

Why now for Kyber: the outreach model is a free AI or data project as the
opener. Norwich and Reading both have new GMs since January 2026. A report
that says "here is what the public record already shows about your poles" is a
better first touch than a template email, and a rerunnable tool means the
second muni costs an afternoon, not a month.

## Solution

A public webapp anyone can open, showing Greenpoint end to end: a one-page
summary on top, an interactive map and a worklist underneath, a precision
table that says how much to trust it, and one call to action: "want this for
your utility? ask and I will run it". Every point links to the source photo.
Every number traces to a file in `data/`.

Greenpoint is the only territory in the public app. Other territories are run
on request by the operator with the same pipeline and delivered privately.
The pipeline stays territory-agnostic; the webapp is one build of it.

Budget rule: this is unfunded. Static hosting at zero cost, the cheapest model
that clears the precision bar, batch pricing, tight crops, and a hard cap on
API spend per run printed before anything is sent.

The narrowest wedge is two findings, chosen because they are the easiest to
verify in a single photo and the most directly actionable:

1. **Joint-use attachments.** Count of non-electric attachments per pole.
   Feeds a "poles with 3+ attachments" list the muni can check against its
   joint-use billing. Verifiable by eye in seconds.
2. **Visible condition flags.** Lean, crossarm damage, vegetation contact,
   transformer present. Feeds a "look at these first" list for the next
   inspection or storm-hardening cycle. Higher false-positive risk, so it
   ships with its own precision row and a severity threshold.

Everything else (material, change over time, inventory gaps) is either a
column that costs nothing extra or a deferred stretch.

## What is useful to a utility, and what is not

Worth showing:
- **Pole count seen vs their own count.** They know how many poles they bill
  joint-use on. A number from an independent source is interesting on its own.
- **Attachment histogram and the top of the tail.** "412 poles carry four or
  more attachments" is a joint-use audit shortlist, and a revenue conversation.
- **Condition shortlist with photos.** Not "replace this pole", but "these 80
  poles show a visible lean or a broken crossarm in a 2025 photo, here is the
  photo". The photo does the persuading.
- **Vegetation contact clusters.** Contact tends to cluster by street. A map
  of clusters is a vegetation-management planning input.
- **Coverage map and capture dates.** Which streets were photographed when.
  This is also the honest disclosure of what the pass cannot see.
- **Precision table from a hand-graded sample.** The single most credible
  thing in the document. A utility engineer will not trust a flag without it.
- **OSM inventory gap (stretch).** Poles we see that OSM lacks. Cheap to
  compute, striking if large, and a natural "want the same diff against your
  GIS?" follow-up.

Not worth showing, or actively harmful:
- Any structural conclusion. A photo cannot see rot, ground-line decay, or
  loading. Language stays at "visible in imagery".
- Per-pole replace or repair recommendations.
- Precision claims without the hand-graded sample behind them.
- A model-confidence column dressed up as accuracy. Disagreement rate across
  frames and the graded precision are the honest reliability signals.
- Anything that implies the pass replaces the NESC inspection cycle.

## Success Criteria

- Greenpoint end to end from cache in under 10 minutes with zero API calls.
- On a 50-pole hand-graded sample: precision of at least 0.9 for
  pole_present, at least 0.8 for attachment_count within plus or minus one,
  at least 0.7 for each condition flag at its "high" severity threshold.
  Flags that miss 0.7 ship as "experimental" or are cut from the summary.
- Norwich bundle produced from the same code with only the bbox changed.
- Measured API cost per pole recorded from the Greenpoint run, so the
  Norwich cost is a computed number, not a guess.
- Outreach: a reply from the Norwich GM. A 30-minute call is the win.

## Architecture

- **Stack**: Python 3 stdlib for everything up to the classifier, `mvt.py`
  (own MVT decoder) for tile enumeration, Pillow for cropping,
  Anthropic Messages API via the `anthropic` SDK for classification, MapLibre
  GL JS with OSM raster tiles for the map, plain HTML/JS for the webapp. No
  framework, no database, no server.
- **Hosting**: static site on GitHub Pages or Cloudflare Pages, both free.
  The app loads a precomputed `poles.geojson` and thumbnails. The "request a
  report" CTA is a mailto link or a free form; no backend to run or pay for.
- **Why boring**: rerunnable by one person on a laptop in six months, and
  hostable for free forever. Files in `data/` are the database.

### Classification approach: hybrid, cheapest first

Three layers, each doing only what it is good at:

1. **Mapillary's detections (free, already real CV).** The
   `object--support--utility-pole` features are output of Mapillary's
   segmentation model. They give pole presence, location, and per-image
   pixel polygons via the Graph API `detections` field. We do not re-detect
   poles. This is also what makes tight crops possible.
2. **Deterministic geometry (free, explainable).** Lean angle from the
   principal axis of the detection polygon, pure Python. Crop box from the
   polygon bounds plus margin. These are numbers a utility engineer can
   check, and they cross-check the model's lean_severity.
3. **Vision-language model, only for the semantic fields.** Attachment count,
   crossarm condition, vegetation contact, transformer present, material.
   There is no pretrained open model for these, and training one is out of
   scope by constraint. A VLM is zero-shot on all of them. Classical
   OpenCV-style pipelines cannot answer "is the crossarm broken".

Model choice is measured, not assumed: Sonnet 5 runs the first pass. Once the
graded sample exists, rerun it with Haiku 4.5 and keep Haiku for any field
where its precision holds, since it halves the cost. Batches API halves the price of every run and there is no
latency requirement.

### Data model

- `Image`: id, lon, lat, captured_at, sequence_id, compass_angle, is_pano.
  From vector tiles. Thumbnail URL fetched per id from the Graph API on demand.
- `MapFeature`: Mapillary's own object detection, id, value
  (`object--support--utility-pole` etc.), lon, lat, first_seen_at,
  last_seen_at, and the list of image ids it was detected in.
- `Observation`: one (feature, detection, image) triple sent to the
  classifier, plus the returned JSON: pole_present, pole_type
  (wood_utility, concrete_or_steel_utility, street_light, traffic_signal,
  other), material, lean_severity, crossarm_condition, transformer_present,
  vegetation_contact, attachment_count, confidence, notes. Cached keyed by
  detection id. pole_type exists because Mapillary's utility-pole class
  includes ornamental street-light and traffic-signal poles in NYC; those
  are excluded from the pole counts and the joint-use finding.
- `Pole`: a cluster of observations within a few meters. Majority vote per
  field, disagreement rate per field, evidence image list, severity score,
  best photo id.
- `Validation`: sampled pole ids, hand-graded labels, precision per flag.

### Components

1. `coverage.py` (done): bbox to image and feature rows from z14 tiles.
2. `fetch.py`: for each pole-like feature, resolve the images it appears in
   and download thumbnails (1024px) to `data/thumbs/`. Graph API per id only.
3. `classify.py`: crop around the detection, send to Claude with a strict
   JSON schema, batch, cache, retry once on bad JSON then drop the row.
4. `dedupe.py`: cluster observations into poles, majority vote, disagreement
   rate, severity score.
5. `validate.py`: sample 50 poles, write the grading CSV, read it back,
   compute the precision table.
6. `report.py`: render `out/<slug>/index.html` (GM summary, map, worklist,
   precision table, coverage, attribution) plus `poles.geojson` and
   `worklist.csv`. Self-contained, opens from disk.
7. `run.py`: runs 1 to 6 in order for a `--town` or `--bbox`, skipping steps
   whose outputs exist.

### External dependencies

- Mapillary vector tiles (enumeration) and Graph API (per-entity: image
  thumbnails, detections). Client token in `.env`.
- Nominatim for town to bbox. One call per town, cached.
- Anthropic API for classification. Key in `.env`.
- Overpass API for the OSM `power=pole` diff (stretch).
- Licenses: Mapillary imagery CC BY-SA 4.0, derived data ODbL, OSM ODbL.
  Attribution string on every output file and in the report footer.

### Infrastructure

Local laptop. `data/` is the cache and the source of truth. `out/` is the
deliverable. Nothing is hosted. A muni receives a zip and opens `index.html`.

## Key Flows

1. **Visitor opens the webapp.** Lands on the Greenpoint map with the four
   summary numbers, a "how this works and what it cannot see" panel, and the
   precision table. Filters by flag, clicks a pole, sees the crop, the flags,
   the attachment count, the capture date, and a link to the Mapillary
   image. Clicks "want this for your utility?" which opens an email or form.
2. **Operator runs a territory.** `python3 run.py --town "Norwich, Connecticut"`.
   Coverage prints density and stops if it is below a threshold. Fetch pulls
   thumbnails for pole features. Classify runs with a cost estimate printed
   first and a `--max-poles` cap for the first pass. Dedupe and report
   produce `out/norwich-connecticut/`. Operator opens the map, samples a few
   points, then runs `validate.py --sample`, grades the CSV in a spreadsheet,
   runs `validate.py --score`, and the precision table lands in the report.
3. **GM reads a requested report.** Opens `index.html`. Top: four numbers (poles
   seen, share with 3+ attachments, share with a high-severity condition
   flag, capture date range), one paragraph of what this is and is not, the
   precision table. Scrolls to the map colored by severity. Clicks a point,
   sees the photo, the flags, and a link to the Mapillary image. Forwards to
   the line superintendent.
4. **Superintendent works the list.** Opens `worklist.csv` sorted by severity,
   with lat/lon, flags, attachment count, best photo URL, and disagreement
   rate. Filters to their district. Checks ten poles in the field. Comes back
   with an opinion, which is the conversation Kyber wants.

## MVP Scope

### In
- Greenpoint end to end, published as a public static webapp.
- Norwich and others only on request, run privately with the same code.
- Two findings: attachment_count and condition flags (lean, crossarm,
  vegetation, transformer). Material and confidence as extra columns.
- Static bundle: GM summary, MapLibre map, worklist CSV, GeoJSON, precision
  table, coverage disclosure, attribution.
- Hand-graded 50-pole validation and the precision table.
- Full rerun from cache without API calls.
- Measured cost per pole.

### Out (deferred)
- Change over time across capture years. Needs multi-year alignment per pole
  and Norwich's history is only dense from 2020. Revisit once dedupe is solid.
- OSM inventory gap via Overpass. Cheap, but it is a second finding type with
  its own validation question. Stretch after the precision table exists.
- Street lights and generic poles. Coverage shows thousands of
  `street-light` features. Same pipeline, different prompt. Later.
- Self-serve territory selection in the webapp. Anyone picking a bbox means
  paying for their classification run. Requests go through the operator.
- Accounts, a muni uploading its own GIS, or any backend.
- Any model training or fine-tuning. Not doing it.

## Risks

- **Classifier false positives on condition flags.** Mitigation: majority
  vote across frames, disagreement rate as a column, severity threshold set
  from the graded sample, flags under 0.7 precision demoted to experimental.
- **Mapillary pole detections are noisy or offset.** Mitigation: dedupe
  clusters by location across frames and requires pole_present from the
  classifier before a pole counts.
- **API cost at Norwich scale.** 19k poles times two or three frames is tens
  of thousands of vision calls. Mitigation: measure token usage on Greenpoint,
  cap frames per pole, consider a cheap model for pole_present triage and the
  stronger model only for flagged poles. Print the estimate before running.
- **Imagery age and gaps.** Mitigation: the report shows the coverage map and
  capture dates, and the worklist carries the photo date.
- **Overclaiming.** A muni engineer will dismiss the whole document over one
  wrong structural claim. Mitigation: the language rules above, and the
  precision table on page one.
- **License compliance.** Mitigation: attribution string in every output,
  ODbL notice on GeoJSON and CSV, no redistribution of full-size images, only
  links and thumbnails.

## Decisions (2026-09-15)

- Pillow added as the first dependency, for crops around each detection.
- Sonnet 5 (`claude-sonnet-5`) for the first classification pass, via the
  Batches API. About $6 for Greenpoint. Haiku 4.5 stays as a cost fallback if
  precision holds on the graded sample.
- Hosting on GitHub Pages. Repo must become public or Pages-enabled.

## Open Questions

- Does Norwich's bbox need trimming to the actual NPU service area? The
  Nominatim relation is the town boundary, which is close but not identical.
- What severity threshold goes in the GM summary? Set after grading, not before.
