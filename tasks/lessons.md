# Lessons

- **Mapillary Graph API bbox search is not exhaustive.** Same 50 m box returned
  42, 78, 93, 151, 1781 images depending on `limit` and `fields`, with no
  `paging.next`, and dense 0.01 deg tiles fail with "reduce the amount of data".
  Counting with it would have silently undercounted by 5 to 10x. Use z14 vector
  tiles for enumeration; they are deterministic across reruns. Pattern: when an
  API returns exactly-at-limit or wildly varying counts, verify against a
  second source before trusting a single number.

- **Mapillary's `object--support--utility-pole` includes street-light and
  traffic-signal poles.** Two of four Greenpoint debug crops were ornamental
  cast-iron light poles. Never count Mapillary detections as poles without a
  type check. Pattern: eyeball a handful of crops before trusting any
  upstream class label.

- **Structured-output JSON schemas reject `minimum`/`maximum` on integers and
  `maxLength` on strings.** The API returned 400 and my script swallowed the
  message, so ten calls were logged as generic failures. Always surface the
  API error text in the dropped reason. Put value ranges in the prompt, not
  the schema.

- **A predicate test caught a copy-paste branch bug** (`state.other ? !isUtility : !isUtility`).
  Shared predicates for summaries, filters, and exports must have a unit test per
  branch; the bug would have shown poles under "other detected objects" silently.

- **`node --test tests/` treats the directory as one failing test on this Node;
  use `node --test tests/*.test.js`.** CLAUDE.md said the former. Pattern: when
  a runner reports 1 test for a directory of N files, the invocation is wrong,
  not the tests.

- **A literal one-character sentinel typed into a heredoc-sized file became a
  NUL byte** (`'\x00'` in grade.py). Python refused the source. Sentinels for
  "clear this field" are a smell anyway; pass the real empty string and let the
  server write it. Pattern: grep for NULs when a freshly written file fails to
  parse at a line that looks fine.

- **Check the data before building a fetch mode.** The per-year frame fetch
  was fully implemented before a 20-line check showed 0 of 1,725 features had a
  detection in a year their existing frames missed. Reverted. Pattern: for any
  "fetch more X" feature, count the candidates from cache first.

- **The Batches API caps one submission at 256 MB.** 3,244 image requests
  (Greenpoint) fit; 5,357 (Hardwick) returned 413 after a 17-minute fetch and
  a full crop pass. classify.py and locate.py now submit in chunks of 2,000,
  all up front so they process in parallel. Pattern: any "one request per
  image" batch needs a size cap before the first territory bigger than the
  demo.

- **Two Mapillary map features can share the same detection ids** (duplicate
  features a few meters apart; 7 of 9,987 Reading observations). The Batches
  API rejects duplicate custom_ids for the whole chunk, after the first chunk
  was already accepted. classify.py and locate.py now send one request per
  detection id. Pattern: any per-entity request list built from a join needs a
  uniqueness check before submission; Greenpoint and Hardwick happened to have
  none.

## Subagent-introduced constants from outside knowledge (2026-09-17)
A sonnet subagent writing `split.py` needed a land-area figure, had none in
`data/`, and supplied `TOWN_LAND_AREA_KM2 = 11.6` from its own knowledge, then
derived "roughly 83% of EACH half is open water" from it. It even commented that
the figure was approximate - which made it read as careful rather than invented.
Two things were wrong: the constant traces to no file in `data/` (CLAUDE.md),
and the derived claim was false, since the halves are 57.7 vs 12.0 km2 and the
water is almost all on one side.

**Pattern:** when a subagent is asked to print a figure the inputs cannot
support, it will source it from memory rather than decline. Reviewing subagent
output means grepping new module-level constants and asking, for each one,
"which file in data/ does this come from?" Policy thresholds are fine; measured
quantities are not.

**Fix applied:** deleted the constant, printed the real computed areas, and said
plainly that they are mostly open water and not comparable - then pointed at the
denominator that is actually measured (road centreline per half).

## Publish the module contract before parallelising (2026-09-17)
`roadcover.py` and `split.py` were written concurrently against a contract I
specified as `split.town_ring(slug)`; the split agent named it `boundary_ring`.
Cost one failed run. When two agents share an interface, state the exact names in
BOTH prompts and have the provider echo its public API back.

## A convenience flag made a destructive command the obvious one (2026-09-17)
`deploy.sh` has always rebuilt gh-pages from an orphan branch and force-pushed, so any territory
not named on the command line silently disappears from the live site. That was survivable while
the habit was "pass all the slugs". Adding `--unlisted` broke the habit: the natural command for a
one-operator preview is `./deploy.sh --unlisted <slug>`, which would have deployed that slug and
wiped the three public territories, blanking the landing page. My `--unlisted` also forced
`territories.json` to `[]` for the whole deploy, so passing the public slugs alongside would have
un-listed those too.

**Pattern:** adding an option can make a pre-existing foot-gun reachable by a shorter, more
plausible command than before. When adding a flag, ask what the shortest command someone will
actually type now is, and what that command does to everything not named in it.

**Fix applied:** `--unlisted` now partitions the slugs (`./deploy.sh a b --unlisted c`) rather than
switching a global mode, and deploy refuses to run when a listed-and-built territory is absent from
the command, unless `--only` is passed. Caught only because the user asked "will I have the town on
the website?" — a question about intent, not code, which is exactly the kind that finds this class
of bug.

## Verify the outcome, not the mechanism (2026-09-17)
Three failures in a row deploying Marblehead, one cause. Each time I checked
whether the *step* reported success instead of whether the *site worked*.

1. `./deploy.sh ... | tail -4` in a shell without `pipefail` returned `tail`'s
   exit code. The push had died; I read "exit 0" and said it was deployed. The
   live site sat on the previous build for another twenty minutes.
2. The branch push failed with `RPC failed; HTTP 400`, which HTTP/1.1 fixed, so
   I assumed the deploy's identical-looking failure had the same cause. It did
   not — that one was payload size — and the "fix" changed nothing.
3. Splitting the push by territory made it succeed, and published a landing page
   whose four cards 404'd until the last chunk landed. The user saw it before I
   did, because I was watching git output rather than the URL.

**Pattern:** for anything with a live endpoint, the check is `curl` against the
real URL and an assertion about the *content*, not an exit code and not a build
status. Where a change is published in pieces, also ask what the thing looks
like halfway through — "it works once finished" is not the same as "it is never
broken".

**Fixes applied:** deploy.sh stages the upload and moves gh-pages in one ref
update, so there is no half-built window and Pages builds once on a complete
tree; an EXIT trap prints failure to stderr regardless of what the caller does
with stdout.

## Build what fits the product, not what the prompt literally says (2026-09-17)
The brief specified "above the fold, a table of candidate doubles with these
columns", so I built a separate bespoke page with that table. The reaction was
"this is so bad, why is it not like any other area??? I just want the same but
with a flag that says double pole". Rebuilt as a fourth condition flag on the
standard area page: same chips, filters, exports, review, map. Perhaps a third
of the bespoke page's code was thrown away.

**Pattern:** when a request describes a UI for a product that already has a
shape, the existing shape usually wins. A literal reading that produces a
one-off surface is worth a single question up front, and the answer was
predictable from the repo: every area page is generated from one template for a
reason.

## Don't fit a threshold to one confirmed error (2026-09-18)
Chasing the Marblehead double-pole distance bug, I measured separation from the
two detection boxes in the shared frame, which worked: a pair whose poles visibly
cross went from "4 m" (the model's guess) and "5.90 m" (the map-feature haversine)
to 0.12 m measured, with the boxes overlapping. Good. Then I added a second
geometric rule -- reject the pair when one pole's box is more than 2x wider than
the other, on the theory that it means one is much further away -- and it threw out
11 of Marblehead's 27 candidates.

The threshold was invented, not measured. Width ratio's median among the 27 real
doubles is 1.83, so 2.0 cuts through the middle of the good calls; pole boxes are
only tens of pixels wide and the far pole is routinely occluded by the near one,
so at that scale the ratio is mostly noise. I had exactly ONE visually confirmed
different-depths false positive to fit against.

**Pattern:** a measurement that replaces a fabricated number is worth shipping on
its own evidence. A threshold that silently drops rows needs its own validation,
and n=1 is not it. When the sample is too small to set a cutoff, surface the
number as sortable evidence and let the reviewer see the outlier -- the rejected
pair now sits at the top of the gap column instead of vanishing. Gate only on
what is definitional (a streetlight is not a utility pole) rather than on a
tuned pixel heuristic.

## Never hand a model the number you are asking it to estimate (2026-09-18)
doubles.py asked the model for `separation_estimate_m`, "your best visual estimate
of the ground distance between the two pole bases" -- and in the same prompt told
it "the two flagged map features are about X m apart on the ground", X being the
triangulated map distance we already knew was unreliable. The model largely handed
X back. On the rows that actually get published, 11 of 13 estimates landed within
0.5 m of what it was told, against 28% of the rows that were not published.

So the field was never a second opinion. It was the bad number restated in a place
that looked like corroboration, and it was the whole of the reported bug: the
Marblehead pair whose poles visibly cross was called "4 m apart" because the map
said 5.90 m and the model rounded it back. Two independent-looking numbers that
were really one number.

Deleting the sentence dropped the echo to 3/13 published (6/175 overall) and moved
the estimate closer to the separation measured off the detection outlines. It also
changed two of thirteen double/not-double calls, so the anchor had been steering
the judgement and not merely the number.

**Pattern:** when a prompt supplies context AND asks for a judgement, check whether
the context contains the answer. If it does, the output is an echo with the
authority of an independent read, which is worse than no field at all. Either
withhold it and let the model answer cold, or keep it and stop pretending the
answer is independent. The general test is cheap: correlate what you told the model
against what it told you back, on the subset you actually publish.

## A schema field is not an enforcement mechanism (2026-09-18)
Marblehead called a wooden pole beside a metal streetlight standard a double pole,
and its own `reason` said so: "a straighter pole carrying a streetlight". The fix
was schema v3's `other_pole_purpose`, asked before the double call, plus
`adjudicate()` rejecting anything non-utility. On Newton Lower Falls it looked
like a clean win -- 13 pairs had a streetlight as the second pole and the model
called none of them doubles, so the gate never even had to fire.

Re-running Marblehead under the same schema showed why that was the wrong
conclusion. All three false positives I had confirmed by eye survived, and the
model now labelled the second pole `utility` in every one -- including the pair
whose v2 reason had explicitly called it a streetlight. The gate only fires when
the model admits what it is looking at, and a model that misreads the pole also
misreads the field about the pole. The count went 27 -> 34; I checked two of the
eight new calls and both were poles receding down a road, not pairs.

Reverted to the 27. Kept the free half of the rebuild: re-running dedupe and
report over the ALREADY CACHED results picked up the measured separation, so
Marblehead's "4 m apart" for visibly touching poles became "the two poles overlap
in this photo" with no new model calls.

**Pattern:** adding a field that asks the model to self-report the thing it is
getting wrong validates on whichever dataset you tried it on and generalises
nowhere. A gate is only as good as the input it gates on, and asking the same
model is not an independent input. Geometry measured off the detection outlines
IS independent, which is why the distance fix held on both towns and the schema
fix did not. Also: two eyeballed crops per town is not evidence -- the thing that
would actually settle this is the graded precision sample in validate.py, still
not run, and no amount of prompt iteration substitutes for it.

## A ternary chain with a bare fallback is a silent mislabel (2026-09-18)
`flagLabel` in `web/app.js` mapped a flag key to its display text as
`k === 'lean' ? ... : k === 'crossarm' ? ... : k === 'att3' ? ... : xfmrLabel(r)`.
When the `double` flag was added to the standard page, no branch was added for it, so
every double-pole record fell through to the last arm and led with the words **"No
transformer visible"**. It shipped, and it was reported months later as a design
complaint ("the panel leads with the wrong finding") rather than as a bug, because the
output was a plausible sentence about a real field.

**Pattern:** a lookup written as a ternary chain ending in a value rather than in
`undefined` cannot fail loudly. Adding a case to the data (a new flag key) does not
force a matching case in the renderer. Write these as a map keyed by the same constant
the data uses, or end the chain in an explicit default that is obviously a default
(`L.flag[k] || k`), so a missing case shows as the key rather than as another field's
answer. The general test: for each enum the code branches on, is there a place where
adding a member is silently absorbed?

## Pixel breakpoints fitted to one area clip the next one (2026-09-18)
The toolbar folded filter groups into "More filters" below hardcoded 1560 px and 1300 px.
Those numbers were fitted to one territory's chip widths; chip labels carry per-area
counts ("Vegetation 169" vs "Vegetation 12"), so the natural width differs per area and
the constants were wrong everywhere else. At 1363 px the Export control was cut off with
no scrollbar and no error.

**Pattern:** a layout threshold expressed in viewport pixels is a guess about content
width. Measure the content instead — fold one group at a time until the row actually
fits. Two extra details made it work: the containers wrap rather than overflow, so the
test is a height/position comparison and not `scrollWidth > clientWidth`; and the first
measurement runs before the web font swaps in, which reports the wrong width, so it has
to be repeated on `document.fonts.ready`.

## The cache you assume exists (2026-09-18)
A subagent auditing where street text lives reported that `data/osm/<slug>/roads.json` "exists for
greenpoint, hardwick, reading, marblehead, and newton-lower-falls". It exists for two of those. I
had already put a decision to the owner premised on "no API calls needed", and only caught it when
I went to read the file and `ls` showed three of the five missing — the three that had no street
text, which is of course *why* they had none.

**Pattern:** a subagent asked "does X exist for each area?" will sometimes answer from the shape of
the pipeline rather than from the filesystem. Any claim of the form "this is cached for all N" is
worth one `ls` before it is spent on a decision, especially when the claim is load-bearing for a
question you are about to ask someone else. The tell here was available for free: the areas said to
have road data were exactly the areas with no street names, which should not both be true.

## Ordering a list in two places (2026-09-18)
Reordering the homepage cards meant editing `web/territories.json` — but `deploy.sh` built the
deployed `territories.json` from the order the slugs were typed on the command line, so the file was
not actually the source of truth, and `landing.js` separately forwarded legacy `#pole=` links to
`list[0]`, which silently became a different area the moment the order changed.

**Pattern:** when a list gains a meaningful order, grep for every consumer that indexes into it or
rebuilds it. Two were wrong here, and neither would have failed a test — one produces a different
homepage depending on the deploy command, the other sends old shared links to the wrong town.
