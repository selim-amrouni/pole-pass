# Pole Pass

Utility pole condition and joint-use attachments, read from public street-level
imagery. One command per territory, no model training, no GPU, a few dollars of
API cost, and a static page a utility can open.

Live demo (Greenpoint, Brooklyn): https://selim-amrouni.github.io/pole-pass/

## What it does

1. `coverage.py` counts Mapillary images and pole detections in a bounding box
   from zoom-14 vector tiles. Low density means stop.
2. `fetch.py` pulls each pole detection's own frames from the Graph API, keeps
   the ones where the pole appears largest, downloads thumbnails.
3. `classify.py` crops around Mapillary's pixel polygon and scores each crop
   with Claude against a strict JSON schema, through the Batches API.
4. `dedupe.py` merges frames of one pole, votes per field, keeps the
   disagreement rate as a reliability signal.
5. `validate.py` writes a stratified 50-pole CSV for hand grading and computes
   precision per flag. The precision table is the part to trust. `grade.py`
   serves that CSV as a local page (photo, buttons, keyboard) and writes the
   grades back in place; the model's answers stay hidden unless asked for.
6. `tilt.py` measures the apparent tilt of each pole outline in each photo and
   calibrates it against the model's own lean calls (`--calibrate`), so the
   page can say how far from vertical a straight pole reads.
7. `report.py` fills `web/index.html` and copies `web/*.{css,js}` into
   `out/<territory>/` with `data.js`, CSV and GeoJSON exports, and per-photo
   images. `deploy.sh` pushes that folder to GitHub Pages.

Flags come in two tiers. Possible condition issues (lean moderate or severe,
crossarm damaged, vegetation touching) are orange. Watch items (slight lean)
are amber and never counted as issues.

Tests: `node --test tests/*.test.js` covers the shared filter predicates and
initialization without a map library.

`run.py` chains 1 through 6. Everything caches under `data/`; a rerun makes no
API calls.

## Setup

```
uv sync
cp .env.example .env     # add MAPILLARY_TOKEN and ANTHROPIC_API_KEY
uv run python3 run.py --town "Greenpoint, Brooklyn, New York" --skip-classify   # coverage + fetch + cost estimate
uv run python3 run.py --town "Greenpoint, Brooklyn, New York"                   # the whole thing
```

Then grade the sample (`uv run python3 grade.py --town "..."`, or edit
`data/validate/<slug>/sample.csv` by hand), run `validate.py --score`, and
rerun `report.py`. Until a `precision.json` exists the page states that no
validation results are published.

Pass `--contact mailto:...` to `report.py` to show a contact button; without
it the page has none.

## Honest limits

A photo cannot see rot, ground-line decay, or loading. This does not replace an
inspection cycle. It tells you where to look first, with the source photo one
click away. Poles that never appear larger than 150 px in any frame are not
scored, and the page says how many.

## Licenses

Imagery and detections © Mapillary contributors, CC BY-SA 4.0. Basemap ©
OpenStreetMap contributors. Derived data in `out/` is ODbL. Code is MIT.
