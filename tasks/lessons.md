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
