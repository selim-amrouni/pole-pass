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
