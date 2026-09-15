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
- [ ] CTA contact link (needs user's choice)
- [x] GitHub repo https://github.com/selim-amrouni/pole-pass (public, MIT) + Pages at https://selim-amrouni.github.io/pole-pass/ via deploy.sh
- [x] 16 issues filed for known problems and deferred work
- [ ] Stretch: Overpass diff vs OSM power=pole
- [ ] Writeup with every number traced to data/

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
