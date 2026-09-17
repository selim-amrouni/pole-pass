# Pole condition pass — plan

Decision: candidate towns come from the Public Power Pilot Targets list, and
coverage.py density decides among them. Picking purely on density was rejected
because a suburban demo town is a worse opener with the munis we want to talk to.

First three candidates (Tier 1, P1 call first, Not contacted, compact territory):
- Reading, MA (Reading Municipal Light Department, ~30k customers)
- Groton, CT (Groton Utilities, ~32k customers)
- Norwich, CT (Norwich Public Utilities)

## Steps
- [x] Scaffold: CLAUDE.md, .gitignore, .env.example, data/, tasks/
- [x] coverage.py (stdlib only, Nominatim geocode + Mapillary z14 vector tiles via mvt.py, cached)
- [x] Token in .env, coverage run on Greenpoint + the three towns (2026-09-15)
- [x] Review coverage output, pick town: iterate on Greenpoint, then Norwich CT
- [x] /kickoff design doc at docs/design.md (GM-first, attachments + condition as co-leads, rerunnable static bundle)
- [x] fetch.py (Graph API per id, largest-apparent-pole frame selection, 20-feature test OK, polygons verified on crops)
- [x] Decisions: Pillow yes, Sonnet 5 via Batches, GitHub Pages, Greenpoint-only public webapp
- [x] classify.py written (Sonnet 5, structured JSON schema, Batches API, per-detection cache, --estimate)
- [x] Key in .env; 10 direct classifications eyeballed: severe-lean calls verified real on a visibly tilted pole
- [x] Refetched with 3 frames: 5,175 observations; skip threshold 150px tall / 20px wide keeps 75% of features
- [x] Full batch: 3,244/3,244 succeeded, $3.88 actual (msgbatch_01X5uhZUgAnSJYhKUk5YDdGm)
- [x] dedupe.py (feature merge + 8 m cluster, majority vote, disagreement rate, severity score; tested on 10)
- [x] validate.py (stratified sample CSV, precision table); grading pending full results
- [ ] Grade 50-pole sample by hand, run --score
- [x] report.py -> out/<slug>/ (index.html, data.js, poles.geojson, worklist.csv, crops/) rendered on test data
- [x] run.py orchestrator
- [x] Dedupe on full data: 865 poles, 620 utility, 183 merged from multiple features
- [x] Report rendered on full data (29.8 MB bundle, 865 crops)
- [ ] Open in browser, iterate on the page
- [x] CTA contact link: report.py DEFAULT_CONTACT is mailto:selim.amrouni@gmail.com in both deployed bundles (confirmed 2026-09-16)
- [x] GitHub repo https://github.com/selim-amrouni/pole-pass (public, MIT) + Pages at https://selim-amrouni.github.io/pole-pass/ via deploy.sh
- [x] 16 issues filed for known problems and deferred work
- [x] Stretch: Overpass diff vs OSM power=pole (osm.py, PR #24)
- [ ] Writeup with every number traced to data/

## Page rework (spec received 2026-09-15)
Approach: move the page out of the report.py f-string into web/ (index.html template,
style.css, app.js, predicates.js). report.py fills the template and writes data.js with
records + meta. Counts computed in JS from data via shared predicates, tested with node.

- [x] dedupe.py: exact per-field votes, per-frame details (note, values), condition_flags
      predicate replaces severity_score, latest_available_at from all frames incl. unclassified
- [x] report.py: template fill, data.js {meta, records}, publish per-frame crops for compare,
      no contact button when unconfigured, validation only from a real file
- [x] web/predicates.js: shared flag predicates + summary counts (browser + node)
- [x] web/app.js: state (filters, sort, page, selection, color mode), list, map w/ fallback,
      detail pane (frames, compare, agreement w/ counts, review decisions in localStorage
      keyed by dataset version, deep link #pole=), exports (filtered/all/review), mobile tabs
- [x] web/style.css: flat, one sans (IBM Plex Sans), mono for ids/numbers, one accent, orange
      only for possible issues, focus rings, reduced motion, 3 breakpoints
- [x] copy: exact strings from spec; About / How it works / Technical details / Limitations /
      Validation / Try another area; byline Selim
- [x] initial example record chosen after viewing its image (config in report.py)
- [x] tests: node tests for predicates + init without maplibregl
- [x] verify: 1440 / 1024 / 390 layouts by reading rendered DOM structure, deep link, empty state
- [x] no deploy; local preview only

## Review
### Step 3-4 Greenpoint classification (2026-09-15, data/poles/greenpoint-brooklyn-new-york/summary.json)
- 3,244 observations classified, $3.88, zero malformed responses (schema enforced by API)
- 865 poles; 620 utility, 158 street lights, 35 traffic signals, 32 other, 20 unclear
- flags among utility poles: lean severe 7, moderate 18, slight 363; crossarm damaged 1; vegetation touching 58; transformers 14
- attachments: max 3, only 13 poles at 3+. Suspiciously low for Brooklyn; check in grading.
- disagreement: lean 0.21, attachments 0.20, pole_type 0.09

### Step 1 coverage (2026-09-15, data/coverage/*/summary.json)
| town | km2 | images | img/km2 | utility-pole feats | last capture |
|---|---|---|---|---|---|
| Greenpoint, Brooklyn NY | 6.2 | 105,211 | 17,002 | 1,725 | 2026-09-09 |
| Reading MA | 39.4 | 98,589 | 2,501 | 3,330 | 2026-06-24 |
| Groton CT | 158.0 | 482,057 | 3,051 | 29,076 | 2026-08-23 |
| Norwich CT | 108.5 | 378,430 | 3,489 | 19,085 | 2026-09-02 |

Density gate: passed everywhere. Project continues.
Lesson: Graph API bbox search silently truncates; switched to vector tiles.

## Hook pass (spec approved 2026-09-16, branch feature/hook-demo)
Plan: ~/.claude/plans/polymorphic-discovering-fountain.md
- [x] A. Warning tier: slight lean = watch (amber), moderate/severe + crossarm + vegetation = issue (orange)
- [x] B. grade.py local grading page for data/validate/<slug>/sample.csv (blind by default)
- [x] C. tilt.py apparent tilt per photo from the detection polygon, calibration.json, per-pole chart over time
- [x] D. "Photographed in more than one year" filter + cross-year compare (23 utility poles). fetch --years dropped: verified 0 of 1725 features have a detection year their top-3 frames miss, so no new fetch or classify was needed
- [x] E. Issues #18 (coverage-gap layer) and #19 (GIS match) filed; README/CLAUDE.md updated
- [x] Code-reviewer findings addressed (static exports, strict summary key, flat calibration + pano dots, y-range, keyboard dots, grade.py value/origin/json guards, python tests)
- [~] Hand grading scrapped 2026-09-16 (user is not a pole expert; crossarm etc. too hard to label). 3 of 50 rows partially graded, not scored. Page keeps its "Experimental, not verified" notice. grade.py stays for a future expert grader
- [ ] User reviews the local page, then PR feature/hook-demo -> main and ./deploy.sh

### Hook pass review (2026-09-16)
- Warning tier: 363 of 620 utility poles are watch items (slight lean); 83 have a condition issue. Unchanged counts, new split.
- Tilt calibration (data/tilt/greenpoint-brooklyn-new-york/calibration.json): abs degrees from vertical by model lean call, all photos:
  none median 2.7 / p90 9.1 (n=1153); slight 4.5 / 12.1; moderate 8.4 / 15.0; severe 9.2 / 16.0. Panos are noisier than flat photos.
- Multi-year records: 23 utility poles have assessed photos in 2+ distinct years (max 2 years each, spans 1 to 8 years). None in 3+.
- No API calls made in this pass. Classifier batch: none.

## Second territory: Hardwick, Vermont (chosen 2026-09-16)
Coverage of five rural candidates (data/coverage/*/summary.json): Hardwick 2,718 utility-pole
features, 71,371 images, 391/km2, photos 2013 to 2022 (8 years); Boonville 378 feats at 9 img/km2;
Chester MA 225; Lake Placid 117; Tupper Lake 119. Hardwick picked; newest photos 2022 is the caveat.
- [x] Territory selector in the header + city/backcountry tag, deploy.sh multi-bundle (branch feature/territories)
- [x] run.py Hardwick: fetch 8,137 photos (2.6 GB), classify 5,357 in 3 batches $5.56, locate 3 batches $6.15, 0 dropped; tilt calibrated; report 222 MB
- [x] Hardwick page reviewed (outline multi-ring bug found and fixed), both territories deployed
- [x] Merge feature/territories

### Hardwick review (2026-09-16, data/poles/hardwick-vermont/summary.json, data/tilt/hardwick-vermont/calibration.json)
- 1,672 records, 1,534 utility. Flags: vegetation 432, lean 35, crossarm 1; watch (slight lean) 777; transformer 61; 3+ attachments 0.
- 444 utility poles photographed in 3+ distinct years (VTrans highway videolog, 2013 to 2022, every two years).
- Tilt calibration, flat photos: none median 1.9 / p90 5.2 (n=2,182); slight 3.8; moderate 7.1; severe 31.3 (n=42). Cleaner than Greenpoint (highway camera, no panos).
- Vegetation "touching" in the woods means canopy overlap in the photo; copy now says so.
- Severe lean: 3 records; hard-00011 is a push brace (issue #22). The other two need a look.
- Total API spend for Hardwick: $11.71.

## Third pass (plan approved 2026-09-16): OSM diff, push brace, landing page, Reading MA, writeup
Plan: ~/.claude/plans/resilient-roaming-stallman.md. Four branches, PR each into main.
- [x] 1. fix/push-brace (PR #23): push_brace pole_type (schema 2), classify --redo-lean moderate,severe in direct mode for both territories, dedupe tie order, page copy, test; close #22 with before/after severe counts
- [x] 2. feature/osm-diff (PR #24): osm.py (Overpass power=pole + man_made=utility_pole, cached under data/osm/<slug>/), nearest-OSM-node per utility record, page filter "Not in OpenStreetMap", exports column, attribution; close #13
- [x] 3. feature/landing (PR #25): web/landing/ root page fed by territories.json with per-bundle summary.json stats, deploy.sh copies it, suburb kind, About copy templated by location
- [ ] 4. feature/reading: run.py gains locate/tilt/osm steps, Reading MA run (~$15), review, deploy all three
- [ ] 5. docs/writeup.md, every number with its data/ path

### Push-brace rerun, Hardwick (2026-09-17, data/poles/hardwick-vermont/summary.json, logs/hardwick-redo-lean.log)
- 255 photos resent (all moderate/severe first-pass calls), 0 dropped, $0.58 direct (costs.py: classify current direct).
- Utility records 1,534 -> 1,532; lean flags 35 -> 26; severe utility records 3 -> 2, and they are different poles:
  hard-00180 (9 photos, 4 severe, marker tape on the pole, outline tilt to 10 deg: real) and hard-01250 (two poles leaning on each other, maybe decommissioned: worth a look).
- The three former severe records: hard-01160 is now push_brace; hard-00011 and hard-01066 are wood_utility with lean unclear (the model was told not to read the brace as a lean), so no flag.
- push_brace records: 2 (hard-00589, hard-01160). Tie between push_brace and wood_utility resolves to push_brace (TIE_ORDER, conservative).
- Tilt calibration, flat: none median 1.9 / p90 5.3 (n=2,189, unchanged); severe median 9.8 / p90 17.0 (n=18, was 31.3 / n=42 with the braces).

### Push-brace rerun, Greenpoint (2026-09-17, data/poles/greenpoint-brooklyn-new-york/summary.json, logs/greenpoint-redo-lean.log)
- 240 photos resent, 0 dropped, $0.64 direct. No push braces found (city). Utility records 620 -> 619 (gree-00362 is now a street light).
- Lean flags 25 -> 17; records with any issue 83 -> 76; watch items 363 -> 373 (some moderate calls became slight on the resend, model variance).
- Severe utility records 8 -> 10: seven of the eight stayed severe, gree-00362 left as a street light, and gree-00001 (14 photos, 6 severe, outline tilt to 12 deg), gree-00024, gree-00222 (1 photo, 18.6 deg) joined.
  The severe set is not stable frame to frame; the flag is a reason to look, not a finding, and the page says so.
- Tilt calibration, flat: none median 2.4 / p90 7.9 (n=759); severe median 8.9 / p90 13.3 (n=40).
- Both territories: OSM has 0 pole nodes in the Greenpoint bbox and 134 in Hardwick's, none within 15 m of a detected pole (data/osm/<slug>/summary.json).

### Reading, Massachusetts (2026-09-17, data/poles/reading-massachusetts/summary.json, logs/reading-run2.log)
- Fetch 9,987 photos (3,330 features, 3 frames), 4,057 skipped as too small; 5,923 distinct detections classified in 3 batches ($7.01), located in 3 batches ($7.87), 0 dropped. Total $14.87 (costs.py).
- 1,519 records, 1,380 utility. Flags: vegetation 431, lean 31, crossarm 0; watch 403; transformer 47; 3+ attachments 9; no push braces. 83 poles photographed in 2+ years.
- Severe lean 6 (read-00075, 00700, 00707, 00979, 01073, 01234), outline tilts 6 to 12 deg, notes consistent; read-01073 "broken with wires holding it up".
- Tilt calibration, flat: none median 2.2 / p90 5.5 (n=698); severe 11.0 / p90 16.1 (n=15).
- OSM: 189 pole nodes in the bbox (164 power=pole, 25 man_made=utility_pole); 96 of 1,380 detected poles have one within 15 m; 1,284 absent.
- Example record read-00070 chosen after viewing the crop (whole pole, crossarm, streetlight arm, comm lines, terminal box, no flags).
- Batch API rejected chunk 2 for duplicate custom_ids (7 detections shared by two features); fixed, lesson recorded. First chunk resumed with --resume.
