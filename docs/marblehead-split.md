# The Marblehead maintenance split

Marblehead Municipal Light Department and Verizon jointly own roughly 4,000 poles
in Marblehead. **Both parties own every pole in town.** What is split is who
*maintains* them, and the Light Department published that split as a single
straight line hand-drawn across an AxisGIS basemap screenshot: southwest of the
line is labelled Verizon, northeast MMLD.

This note records how that line became a geometry, because a line drawn by hand
on a screenshot is not a survey and the page must not pretend otherwise.

Source image: `docs/mmld-maintenance-map.png`.
Reproduce with: `uv run python3 digitize.py --overlay`.
Consumed by: `split.py`, which carries the two endpoint coordinates as constants.

## Why this was harder than reading two pixels off a picture

The obvious approach — eyeball the endpoints against street labels — is worth
about ±200 m, and the whole point of the exercise is to know how wrong we are.
So the line and the map were recovered separately and then checked against each
other.

**Finding the line.** The first attempt, isolating dark pixels and taking the
largest connected component, returned nothing usable: the largest component in
the whole image was 645 pixels. The reason is that the town outline on an
AxisGIS basemap is *dashed*. That turned out to be a gift rather than an
obstacle — the split line is the only long **solid** stroke in the frame, so a
Hough accumulator over dark pixels peaks on it unambiguously, with the image
border as the only competition. A total-least-squares refit over the 2,171
pixels in that peak, iteratively dropping residuals beyond 4 px, lands at
rms 1.45 px.

**Finding the map.** AxisGIS renders north-up Web Mercator, so the transform
from pixels to the world has three unknowns: one scale and two offsets. Rather
than guess them from labels, they were fitted: project OSM `natural=coastline`
geometry through a candidate transform and score it by the fraction of coastline
vertices that land within 4 px of an inked pixel, with the split line, the
caption and the frame excluded from the ink so they cannot attract the fit. A
chamfer distance transform makes each vertex one array lookup, which keeps a
coarse-to-fine search over the three parameters down to about 35 seconds.

Best fit:

| | |
|---|---|
| scale | 0.151400 px per Mercator metre |
| origin (image top-left) | Mercator (−7892648.000, 5241316.000) |
| ground resolution | 4.87 m per pixel at 42.5°N |
| fit | 76–79% of coastline vertices within 4 px of ink |
| screenshot extent | west −70.900863, south 42.468431, east −70.830968, north 42.534048 |

## The endpoints

| | longitude | latitude |
|---|---|---|
| NW (shoreline southwest of Marblehead Village School) | −70.875462 | 42.504476 |
| SE (southeast coast, west of Devereux Beach) | −70.860375 | 42.488829 |

`split.py` extends this segment to the town boundary at both ends before
splitting the town polygon.

## How wrong is it

Three separate error sources, none of which cancel:

- **Registration.** The fit score degrades measurably beyond ±3 px of shift in
  either axis, so the screenshot is pinned to the world to about **±15 m**.
- **The stroke itself.** The drawn line's 95th-percentile half-width is 7 px.
  At 4.87 m/px that is a mark roughly **68 m wide on the ground** — a line you
  could park a row of houses inside.
- **Where the line ends.** A hand-drawn stroke has no defined terminus; the
  extracted endpoints are the extremes of the ink, which is a choice, not a
  measurement.

Taken together the line's position is good to something like ±50 m, and its
*intent* — which side of a street a given pole falls on — is not recoverable at
all near the line. That is the argument for the **150 m uncertainty buffer**:
any pole within 150 m of the line is labelled `UNCERTAIN` rather than assigned
to a side. The buffer is not a hedge, it is roughly twice the width of the mark
being interpreted.

## Independent check

The fit above uses only the coastline. As a check that does not share that
input, nine OSM features were looked up by name and projected through the
transform: Marblehead Village School, Gerry Playground, Forest River Park,
Winter Island, Chandler Hovey Park, Devereux Beach, the Audubon trail, and the
Lafayette/Humphrey and Pleasant/Washington junctions. Every one lands on the
feature the basemap labels, with scatter consistent with label-placement offset
rather than a systematic shift. `docs/digitize-check.png` is that picture.

The two endpoints also land where the Light Department's own description puts
them: the NW end on the shoreline about 850 m west of Marblehead Village School,
the SE end on the southeast coast a few hundred metres southwest of Devereux
Beach, between Devereux Ave and Atlantic Ave.

## What this geometry is not

- It is **not a boundary of ownership**. MMLD and Verizon jointly own all poles
  in town. This line allocates maintenance only.
- It is **not authoritative**. It is traced from a published picture. If the
  Light Department has the real geometry, theirs replaces this immediately.
- It is **not precise near itself**. Within 150 m of the line the honest answer
  is `UNCERTAIN`, and the page says so rather than picking a side.

Boundary geometry: © OpenStreetMap contributors, ODbL. Maintenance split traced
from the Marblehead Municipal Light Department's published maintenance map.
