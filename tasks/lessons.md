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
