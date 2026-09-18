# Marblehead MA — double-pole pass for MMLD (unlisted)

Goal: an unlisted Marblehead page whose primary output is candidate DOUBLE POLES
(old pole left standing beside its replacement). ~4,000 jointly-owned poles,
public MMLD backlog of ~100 doubles. Capture dates are evidence for the MA
removal deadline, so dates matter as much as locations.

Reused, not rebuilt: `mvt.py` (tile decode), `coverage.py` (geocode, tile cache,
`points_in_bbox`, slug/DATA), `osm.py` (Overpass fetch-once-cache-forever,
`nearest_within` grid), `dedupe.py` (`haversine_m`, `cluster`), and for phase 2/3
`fetch.py` / `classify.py` / `locate.py` / `tilt.py` / `report.py` unchanged at the
core. New code is additive: `geo.py`, `split.py`, `roadcover.py`.

## Phase 0 — maintenance split  (in progress)
- [x] Extract the hand-drawn line from the screenshot programmatically
      (Hough + total-least-squares refit, rms 1.45 px over 2171 pixels). The town
      outline turned out to be DASHED, not solid, which is why naive connected
      components found nothing; the split line is the only long solid stroke.
- [x] Georeference the screenshot -> WGS84. Fitted a 3-parameter north-up Web
      Mercator similarity by matching OSM `natural=coastline` to the dashed town
      outline drawn on the screenshot: scale 0.151400 px per mercator metre,
      origin (-7892648.000, 5241316.000), 4.87 ground m/px. 79% of coastline
      vertices land within 4 px of ink; fit degrades past +-3 px, so registration
      is good to ~+-15 m. Cross-checked independently against 9 OSM landmarks
      (Village School, Gerry Playground, Winter Island, Chandler Hovey, Devereux
      Beach, Audubon Trail, two street junctions, Forest River Park) - every one
      lands on its feature. The drawn stroke is ~68 m wide on the ground, which
      is the real argument for the 150 m buffer.
      Endpoints: NW -70.875462, 42.504476   SE -70.860374, 42.488828
- [ ] Town boundary from OSM relation 2373036 at full resolution (Nominatim's
      simplified 84-vertex polygon is too coarse; it also includes ~57 km2 of
      water, which is the legal boundary and must be stated, not silently clipped)
- [x] `geo.py`: point-in-polygon, densify, point-segment distance, half-plane
      clip, line/ring intersections, Mercator + local-frame helpers. Stdlib only.
      `osm.fetch_overpass` refactored to take a query string so it is reusable
      as the one cache-forever Overpass fetcher. 38 python tests green.
- [x] `split.py`: extended endpoints NW 42.508521,-70.879362 (only 552 m past
      the drawn end - the town line meets the shore right there) and
      SE 42.433279,-70.806813 (7.6 km out, because the legal boundary is offshore).
      2.13 km as drawn, 10.27 km boundary to boundary. Halves 57.70 / 12.02 km2,
      both mostly open water and NOT comparable - road centreline per half is the
      denominator that means anything. Removed a fabricated 11.6 km2 land-area
      constant a subagent had introduced and the "83% of each half is water" line
      derived from it; that claim was also just wrong, the split is lopsided.
- [x] 150 m buffer -> `maintainer = UNCERTAIN`, checked before the side test
- [x] Overlay PNG at the screenshot's exact extent:
      `data/split/marblehead-massachusetts/overlay.png`. Verizon side holds
      Lafayette/Humphrey/Tedesco/Clifton, MMLD side holds Village School, Gerry,
      the old town and the Neck - matches the Light Department's arrows.
- [x] Note in code, GeoJSON properties and overlay footer: maintenance split
      only; both parties jointly own all poles in town. Page copy still to do.

## Phase 1 — coverage check, then STOP
- [x] `coverage.py --town "Marblehead, Massachusetts"`: 21,146 images, 79
      sequences, 11,465 map features, 2,393 utility-pole + 722 pole + 1,212
      street-light, captures 2011-10-21 -> 2026-08-28. bbox is 131 km2 and mostly
      ocean, so every bbox density is meaningless - hence roadcover.py.
- [x] `roadcover.py` - all of it, plus a coverage-by-vintage cut I added because
      it turned out to decide the project.
- [x] VERDICT: UNUSABLE - 11.6% of in-town road centreline has an image within
      20 m. 377 of 490 named streets have none at all.
- [x] STOPPED and reported. No image processing, no page.

## Phase 2 — detection  (go-ahead given)
- [x] `fetch.py --in-town` added. The bbox held 2,393 utility-pole features but only
      1,023 are in Marblehead; without the clip we would have paid to classify ~1,370
      Salem poles and published them on a Marblehead page. 3,069 observations, 0 errors.
- [x] `split.town_ring` de-hardcoded: resolves the OSM relation from the Nominatim
      answer coverage.py already cached, so it works for any territory.
- [x] `classify.py` runner generalised to a `Job` (schema, id field, request builder)
      so the doubles pass reuses the Batches machinery instead of growing a second one.
      Verified byte-identical `--estimate` output on all three cached territories.
- [x] Per-pole pipeline: classify 2,182 frames ($2.76, 0 dropped) -> dedupe 609 records
      (582 utility, 214 with a condition flag) -> tilt --calibrate -> osm (552 of 582
      absent from OSM at 15 m). `locate.py` deliberately skipped: it only feeds the
      annotation overlay on the pole explorer, which is not this page's deliverable.
- [x] `doubles.py`: pair screen on the FEATURE layer (not dedupe's pole records, which
      merge at 8 m and would erase exactly the pairs we hunt). 372 pairs within 6 m;
      pair ids are sha1 of the two feature ids, so they survive reruns.
      Status: 261 ok, 57 no_shared_frame, 54 too_small — none dropped.
- [x] Third failure mode found by inspecting crops before spending, and added to the
      prompt (schema v2): Mapillary triangulates feature positions and depth along the
      camera ray is poorly constrained, so two poles 20-40 m apart down the same street
      collapse to within a metre of each other. Measured: only 15% of ok pairs have both
      detections at the same apparent depth. The prompt now teaches the ground-line cue
      (same depth = bases at the same height in frame) rather than pole height, because
      an old pole cut down to a stub is genuinely short while standing right beside its
      replacement — that is a real double and a height filter would delete it.
- [x] Doubles classification: 261 requests, $0.835, 0 dropped.
      75 likely duplicate detections (29%, matching the 1.2-1.4 features-per-pole prior),
      153 poles_at_different_depths (59% — the largest single category, and it would have
      had nowhere honest to go without schema v2), 27 called real doubles
      (MMLD 21, Verizon 6; 9 under 1 m, 9 at 1-3 m, 9 at 3-6 m; photos 2023-08-19..2025-11-07).

## Phase 3 — unlisted page
- [x] Unlisted deploy route: `deploy.sh --unlisted <slug>` deploys the bundle without
      adding it to territories.json and appends `Disallow: /<slug>/` to a new
      `web/landing/robots.txt`. Verified: listed mode unchanged, and Marblehead cannot
      be deployed as a listed page without someone editing territories.json by hand.
- [x] `report_doubles.py` + `web/doubles.{html,js,css}` -> `out/marblehead-massachusetts/`
      (11.3 MB; noindex meta, no address ever rendered, maintainer blank inside the 150 m
      buffer, filters by maintainer/confidence/duplicate/depth, split line on the map,
      browser-local review decisions exported in the CSV, attribution intact).
      Added a Gap column the spec did not ask for: the model's visual separation estimate,
      because the spot check found every disputed call in the widest third and a real
      double is usually under 3 m. Shows the visual estimate, not the map separation,
      which is unreliable for exactly the triangulation reason above.
      Fixed two misleading lines in the generated email summary: the 111 unassessable pairs
      were described as "awaiting classification" (they are not pending — no photo shows
      both poles, or they are too small to read), and the per-maintainer counts printed the
      screened-pair split (MMLD 335) directly under "27 called a real double", which invites
      reading 335 as MMLD's double count.
- [x] Spot-checked 4 of the 27 positives and both rejection classes by eye ->
      `data/doubles/<slug>/spotcheck.json`, carried onto the page with its limits stated
      first. 3 of 4 positives agreed; the one disputed call is the widest (5.44 m, wood
      next to steel, plausibly a separate streetlight standard). Both rejection classes
      correct. NOT a validation: same system judging its own output, n=4, not random.

## Review

### Phases 2 and 3 closed
27 candidate doubles from 372 screened pairs, $3.60 of model spend in total. The number
landed inside the 15-25 range predicted from the 1.35 features-per-pole prior before any
money was spent, which is mild evidence the pipeline is behaving.

The thing that would have gone wrong quietly: the geometric screen is built on Mapillary
feature coordinates, and those are triangulated from photographs, so depth along the camera
ray is barely constrained. Two poles 40 m apart down one street can be recorded a metre
apart. Looking at two crops before spending caught it; measuring all 261 sized it at 59%.
The fix that suggested itself — filter on apparent pole height — would have been actively
harmful, because a double pole's old member is often cut down to a stub and is therefore
genuinely short. The ground line, not the height, is the cue that separates the two, and
that is now what the prompt teaches.

No graded sample, by decision. validate.py/grade.py have never been pointed at the
double-pole schema, and a handful of crops judged by the author is not a precision table.
Grading is hand work and was deprioritised on 2026-09-18. The pages carry "Experimental
results. The model's assessments have not been independently verified", which is what makes
publishing ungraded calls honest -- that notice stays until a real precision number exists.

### Phase 0 closed
The georeference is the part that had to be right, and it is checked twice: a
coastline fit (79% of vertices within 4 px of the drawn outline) and an
independent landmark check (9 OSM features, every one lands on its label). The
150 m buffer is not a hedge - the drawn stroke is ~68 m wide on the ground and
registration is +-15 m, so the buffer is about twice the width of the mark being
interpreted.

Two corrections worth remembering:
- A subagent introduced `TOWN_LAND_AREA_KM2 = 11.6` from outside knowledge and
  derived a water fraction from it. Caught in review, removed. See tasks/lessons.md.
- The `town_ring` / `boundary_ring` name mismatch between two concurrently
  written modules cost a run. Publish the contract before parallelising.

### Phase 1 closed - the answer is no, and the reason is not the one expected
Marblehead has 177.2 km of public road centreline and 8,698 in-town images, but
they are ~25 drives, not a sweep. 11.6% of centreline has an image within 20 m.

The stale-imagery worry was real but pointed the wrong way. The bbox pass showed
75% of images predating 2024, which looked fatal. Clipping to the actual town
polygon showed that every one of those 9,112 photos from 2017-2018 is in a
~850 m strip at lon -70.899..-70.889 - Salem, west of the town line. Marblehead's
OWN imagery starts 2023-08-19. Nothing in town is older than that.

The real blocker is different and worse for the stated goal: the Verizon half has
**zero** imagery after 2023. All 2,955 fresh (Nov 2025) photos are on the MMLD
side. So the one comparison the Light Department would most want - how do the two
maintainers' backlogs compare - cannot be made on current evidence at all.

Cross-check that the road denominator is sane: 177 km of centreline against ~4,000
poles is ~44 m per pole, which is normal distribution spacing. The number is right.

## Newton Lower Falls — shipped 2026-09-18 (PRs #30, #32, both merged to main)

Fifth area live: https://selim-amrouni.github.io/pole-pass/newton-lower-falls-massachusetts/
533 records, 509 utility poles, 13 double-pole candidates on 9 records. $2.55 of model calls.
A DISTRICT, not a town — `district.py` Voronoi cell of OSM village nodes clipped to the Newton
town ring, 3-year imagery cutoff recorded in `data/district/<slug>/district.geojson`.

Rebuild from cache:
    uv run python3 district.py --town "Newton, Massachusetts" --village "Newton Lower Falls" --since-years 3
    uv run python3 roadcover.py --town "Newton Lower Falls, Massachusetts"
    uv run python3 fetch.py --town "Newton Lower Falls, Massachusetts" --in-town --frames 2
    uv run python3 classify.py / locate.py / doubles.py / dedupe.py / tilt.py --calibrate / osm.py / report.py
    ./deploy.sh greenpoint-brooklyn-new-york hardwick-vermont reading-massachusetts \
                marblehead-massachusetts newton-lower-falls-massachusetts
(doubles.py must run BEFORE dedupe.py — dedupe reads candidates.jsonl for the `double` flag.)

### Open, in priority order
(Precision grading is deliberately NOT on this list. Deprioritised 2026-09-18 -- it is hand
work. validate.py/grade.py remain in the repo; the "not independently verified" notice on
every page is the standing substitute and must not be removed while it is true.)

- [ ] **Re-run Marblehead doubles.** Its 27 candidates predate both fixes: they were produced with
      the prompt anchor and pre-v3 schema, and include at least two streetlight false positives
      found by eye (MH-7b5bbf86, MH-fdad8b7b) plus one different-depths call (MH-786f670a). ~$0.30.
      `data/doubles/marblehead-massachusetts/results/` must be moved aside to force it.
- [ ] Marblehead's `out/` still carries leftovers from the superseded `report_doubles.py`
      (candidates.csv, candidates.geojson, doubles.css, doubles.js). Harmless but confusing.
- [ ] `report_doubles.py` + `web/doubles.*` are dead code — the bespoke page was replaced by the
      `double` flag on the standard page. They write to the SAME `out/<slug>/` path and would
      silently overwrite a standard bundle if anyone ran them. Worth deleting.
- [ ] `district.py` refuses multi-village districts (needs a polygon union, not implemented).
      Waban + Newton Upper Falls were measured and are viable if more Newton coverage is wanted:
      ~$28 for two villages, ~$41 for three.

## Interface redesign — 2026-09-18 (branch `feature/interface-redesign`, not deployed)

General redesign of the area page against a written brief, audited first against the
published Marblehead build with headless Chrome. Data, classification, dedupe, flag
semantics, exports, review storage, record ids and attribution are untouched.

### P0
- [x] **Deep link landed below the fold.** Root cause was not width: `.ws.map-failed`
      forced one grid column, so an open record wrapped to a second row under the whole
      browse list. The audit browser had no WebGL. The fallback is now scoped to browse
      (`.ws.map-failed:not(.has-detail)`) and grid children are placed explicitly.
- [x] **Filter bar clipped Export at 1363 px.** Fitted 1560/1300 breakpoints replaced by
      a measured progressive fold; containers wrap rather than clip as a backstop.
- [x] **No-WebGL fallback looked broken.** Compact notice plus a deliberate multi-column
      result layout; row is thumbnail | findings | meta so the id no longer floats away.
- [x] **Primary finding was wrong, not just mis-ordered.** `flagLabel` had no `double`
      branch and fell through to the transformer label (see lessons.md). Fixed, plus
      `PP.orderFlags()` leads with the active filter and otherwise a documented
      `CONDITION_PRIORITY` severity order (crossarm, double, lean, vegetation).
- [x] **Shared links lost filter context.** `#pole=<id>&issue=<FILTERS key>`, applied
      before the first refresh so the position reads "3 of 27"; Copy link emits both;
      `pushState` + `popstate` so Back returns to a previous view.

### P1 / P2
- [x] More filters is an anchored popover with its own scroll (drawer under 900 px);
      opening it no longer lengthens the page.
- [x] Findings panel reordered: primary finding, location, model findings, review,
      then collapsed "Other visible attributes" and "Technical details".
      "Why this record is listed" -> "Model findings".
- [x] Three annotation buttons + permanent legend -> one Annotations popover with the key.
- [x] Enlarge is a button; About preserves pole and issue state.
- [x] Colour meanings fixed (blue actions, orange conditions, green reviewed only, red
      errors only). Street View moved off the issue colour; the experimental notice is a
      neutral chip and is still permanent. Contact demoted to "Contact / feedback".
- [x] Rows show the absolute date only; "No model flag" -> "No flagged condition".
- [x] Workspace height is CSS-driven (body flex column, viewport-bounded on desktop), so
      no JS height math and nothing sits under the footer.

### Counts wording — deliberate, and the one thing to look at
The header says **"582 poles · 231 poles with at least one flag · imagery from 2023–2025"**,
which is the brief's wording chosen by the owner after the mismatch was pointed out.
231 is poles with at least one **condition** flag; 389 poles carry a flag of some kind.
To stop the two contradicting each other on the same screen, the review denominator reads
"0 / 389 with something to review" rather than "flagged", and the header tooltip plus a new
"What the counts mean" section in About state each population exactly. Making the header
itself precise is one string in `renderSummary()`.

### Verification run
- `node --test tests/*.test.js` 44 pass; `uv run python3 -m unittest discover -s tests` 98 pass.
- All five bundles rebuilt from cache, no API calls; all five boot clean with no console
  errors and no horizontal scroll at 1440.
- Headless Chrome at 2048x1024, 1440x900, 1363x936, 390x844, with and without WebGL:
  deep link above the fold, Export never clipped, list collapse/reopen, Back navigation,
  primary finding follows all seven issue filters generically.
- Before/after screenshots captured from a `main` worktree build of the same area.

### Not done
- [ ] Not deployed. `./deploy.sh` with all five slugs once the screenshots are approved.
