#!/usr/bin/env python3
"""Carve one district out of a town and make it a first-class area the rest of the pipeline can run on.

Usage:
  uv run python3 district.py --town "Newton, Massachusetts" --village "Newton Lower Falls"
  uv run python3 district.py --town "..." --village A --village B --since-years 3 [--name "..."]

Why this exists. Newton has 16,130 recently-photographed utility poles, and classifying every one of
them costs an order of magnitude more than any territory run so far. A district keeps the standard
area page exactly as it is and just makes the area smaller, rather than inventing a cheaper page.

Why a Voronoi cell. Newton's thirteen villages are real places people navigate by, but they have no
legal boundaries and OSM carries them only as POINT nodes -- there is no published polygon to clip
to. So the district is every point closer to this village's node than to any other village's node:
one rule, computed from OSM data, reproducible, and honest about being a partition rather than a
surveyed line. That has to be said on the page; it is not the city's boundary.

Reads  data/osm/<town>/places.json         OSM place nodes (cached; fetched here on a miss)
       data/coverage/<town>/*              coverage.py's enumeration of the whole town
Writes data/district/<district>/district.geojson   the cell polygon, its provenance and vintage rule
       data/coverage/<district>/{summary.json,images.jsonl,map_features.jsonl}   the town's rows,
                                                    clipped to the cell (and features to --since)

After this, every downstream step runs on the district exactly as it would on a town:
  roadcover.py -> fetch.py --in-town -> classify.py -> locate.py -> doubles.py -> dedupe.py
  -> tilt.py --calibrate -> osm.py -> report.py
`ring()` below is what makes that work: modules ask for an area's boundary and get the district's
polygon when one exists, or the OSM town relation when it does not.
"""
import argparse
import json
import math
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import geo
import osm
import split
from coverage import DATA, POLE_VALUES, ROOT, ms_to_date as ms_date, slugify
from dedupe import haversine_m

PLACE_TAGS = "suburb|neighbourhood|village|quarter"
UTILITY_VALUE = "object--support--utility-pole"


# ---------------------------------------------------------------- the area contract
def path_for(slug):
    return DATA / "district" / slug / "district.geojson"


def load(slug):
    """The district definition, or None when `slug` is an ordinary town."""
    p = path_for(slug)
    return json.loads(p.read_text()) if p.exists() else None


def ring(slug):
    """The boundary any pipeline step should clip to: the district's cell, else the OSM town ring.

    This is the one function that lets a district be passed to --town anywhere in the pipeline.
    Districts have no OSM relation of their own, so split.town_ring would fail for them."""
    d = load(slug)
    if d:
        return [tuple(p) for p in d["geometry"]["coordinates"][0]]
    return split.town_ring(slug)


def since_ms(slug):
    """The district's imagery cutoff in epoch ms, or None. Recorded with the area rather than passed
    on every command line, so a rerun months later reproduces the same set instead of quietly
    widening it."""
    d = load(slug)
    return (d or {}).get("properties", {}).get("since_ms")


# ---------------------------------------------------------------- the cell
def place_nodes(town_slug, bbox):
    w, s, e, n = bbox
    box = f"({s},{w},{n},{e})"
    q = (f'[out:json][timeout:180];('
         f'node["place"~"{PLACE_TAGS}"]{box};);out tags center;')
    raw = osm.fetch_overpass(q, DATA / "osm" / town_slug / "places.json")
    return [(el["tags"]["name"], el["lon"], el["lat"]) for el in raw.get("elements", [])
            if el.get("type") == "node" and el.get("tags", {}).get("name")]


def bisector(centre, other):
    """Two points on the perpendicular bisector of centre->other, plus which side keeps `centre`.

    Returns (a, b, keep_left) ready for geo.clip_ring_to_halfplane. Built in a local metric frame so
    the perpendicular is a true right angle on the ground rather than in degrees, which would skew
    with latitude."""
    lat0 = (centre[1] + other[1]) / 2
    fwd, inv = geo.local_frame(lat0)
    cx, cy = fwd(*centre)
    ox, oy = fwd(*other)
    mx, my = (cx + ox) / 2, (cy + oy) / 2
    dx, dy = ox - cx, oy - cy
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    span = 60000.0  # 60 km: longer than any town, so the line always spans the ring
    a = inv(mx - uy * span, my + ux * span)
    b = inv(mx + uy * span, my - ux * span)
    keep_left = geo.signed_dist_to_line_m(centre[0], centre[1], a, b) > 0
    return a, b, keep_left


def cell(town_ring, centre, others):
    """The Voronoi cell of `centre` within `town_ring`: the ring clipped by the bisector against
    every other centre. Convex clipping of a non-convex town ring is fine here -- each clip only
    ever removes area, and the town ring bounds the result."""
    poly = list(town_ring)
    for o in others:
        if o[:2] == centre[:2]:
            continue
        a, b, keep_left = bisector(centre, o)
        poly = geo.clip_ring_to_halfplane(poly, a, b, keep_left)
        if not poly:
            return []
    return poly


def union_cells(town_ring, centres, chosen):
    """One ring per chosen village. Kept as separate rings rather than unioned: they tile exactly,
    so drawing or testing them one by one is equivalent and needs no polygon-union code."""
    out = {}
    for name, lon, lat in centres:
        if name in chosen:
            c = cell(town_ring, (lon, lat), [(o[1], o[2]) for o in centres])
            if c:
                out[name] = c
    return out


def in_chosen(lon, lat, centres, chosen, town_ring, town_bbox):
    """Inside the town AND nearest to a chosen village's centre.

    BOTH halves are load-bearing. The nearest-centre test is the cell definition, exact and free of
    clipping error. The town test is what stops the cell leaking across the town line: Newton Lower
    Falls sits on the Wellesley border, and points in Wellesley are nearer to its node than to any
    Wellesley node, so without this the district would quietly publish another town's poles. The
    cell POLYGON is already clipped to the town ring by cell(); this keeps the row filter agreeing
    with it."""
    w, s, e, n = town_bbox
    if not (w <= lon <= e and s <= lat <= n):
        return False
    if not geo.point_in_polygon(lon, lat, [town_ring]):
        return False
    return min(centres, key=lambda c: haversine_m(lon, lat, c[1], c[2]))[0] in chosen


# ---------------------------------------------------------------- build
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--town", required=True, help="the parent town, already run through coverage.py")
    ap.add_argument("--village", action="append", required=True, default=[], help="OSM place name; repeatable")
    ap.add_argument("--name", help="area name for the district (default: villages + the town's state)")
    ap.add_argument("--since-years", type=float, default=3.0,
                    help="keep only pole features seen this recently; 0 disables. A double pole in a "
                         "2018 photo says nothing about what stands there now.")
    args = ap.parse_args()

    town_slug = slugify(args.town)
    cov_town = DATA / "coverage" / town_slug
    if not (cov_town / "summary.json").exists():
        sys.exit(f"run coverage.py --town {args.town!r} first, missing {cov_town / 'summary.json'}")
    town_summary = json.loads((cov_town / "summary.json").read_text())
    bbox = town_summary["bbox"]

    centres = place_nodes(town_slug, bbox)
    known = {c[0] for c in centres}
    missing = [v for v in args.village if v not in known]
    if missing:
        sys.exit(f"no OSM place node named {missing} near {args.town}. Available: {sorted(known)}")
    chosen = set(args.village)

    tring = split.town_ring(town_slug)
    cells = union_cells(tring, centres, chosen)
    if len(cells) != len(chosen):
        sys.exit(f"clipping produced {len(cells)} cells for {len(chosen)} villages; refusing to guess")

    state = args.town.split(",")[-1].strip()
    name = args.name or f"{', '.join(sorted(chosen))}, {state}"
    slug = slugify(name)

    cutoff = None
    if args.since_years:
        cutoff = int((datetime.now(tz=timezone.utc) - timedelta(days=args.since_years * 365)).timestamp() * 1000)

    # One ring per village, but `ring()` must return a single boundary. With one village that is
    # exact; with several, refuse rather than hand back a boundary that is not the real union.
    rings = list(cells.values())
    if len(rings) > 1:
        sys.exit("multi-village districts need a polygon union, which this module deliberately does "
                 "not implement -- run one village, or add the union and its tests first.")

    out = DATA / "district" / slug
    out.mkdir(parents=True, exist_ok=True)
    feature = {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in rings[0]]]},
        "properties": {
            "name": name, "slug": slug, "parent_town": args.town, "parent_slug": town_slug,
            "villages": sorted(chosen),
            "rule": ("Every point closer to this village's OpenStreetMap place node than to any "
                     "other village's node. Newton's villages have no legal boundary and OSM holds "
                     "them only as points, so this is a partition, not a surveyed line."),
            "since_ms": cutoff,
            "since": datetime.fromtimestamp(cutoff / 1000, tz=timezone.utc).date().isoformat() if cutoff else None,
            "since_years": args.since_years or None,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "attribution": "Village points and town boundary: OpenStreetMap contributors, ODbL.",
        },
    }
    path_for(slug).write_text(json.dumps(feature, indent=2))

    # Clip the town's coverage rows into the district's own area, so every later step runs unchanged.
    cov_d = DATA / "coverage" / slug
    cov_d.mkdir(parents=True, exist_ok=True)
    imgs = [json.loads(l) for l in (cov_town / "images.jsonl").open()]
    feats = [json.loads(l) for l in (cov_town / "map_features.jsonl").open()]
    # Images are clipped by area only, never by date: roadcover.py reports coverage BY VINTAGE, and
    # filtering them here would delete the evidence that the district's imagery is recent.
    tbbox = (min(p[0] for p in tring), min(p[1] for p in tring),
             max(p[0] for p in tring), max(p[1] for p in tring))
    keep = lambda lon, lat: in_chosen(lon, lat, centres, chosen, tring, tbbox)
    d_imgs = [im for im in imgs if keep(im["lon"], im["lat"])]
    d_feats_area = [f for f in feats if keep(f["lon"], f["lat"])]
    d_feats = [f for f in d_feats_area
               if cutoff is None or (f.get("last_seen_at") or 0) >= cutoff]

    for fname, rows in (("images.jsonl", d_imgs), ("map_features.jsonl", d_feats)):
        with (cov_d / fname).open("w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    lons = [p[0] for p in rings[0]]; lats = [p[1] for p in rings[0]]
    d_bbox = [min(lons), min(lats), max(lons), max(lats)]
    area_km2 = geo.ring_area_m2(rings[0]) / 1e6
    poles = sum(1 for f in d_feats if f["value"] == UTILITY_VALUE)
    # This file has to be a DROP-IN for coverage.py's own summary.json: every later step, report.py's
    # technical-details block included, reads the district through the same keys it reads a town
    # through. Anything coverage.py writes and a consumer might touch is recomputed here over the
    # clipped rows rather than copied from the parent, so no number silently describes the whole town.
    caps = sorted(im["captured_at"] for im in d_imgs if im.get("captured_at"))
    by_value = Counter(f.get("value") for f in d_feats)
    pole_like = sum(n for v, n in by_value.items() if v in POLE_VALUES)
    summary = {
        **{k: town_summary[k] for k in ("source", "attribution") if k in town_summary},
        "name": name, "slug": slug, "bbox": d_bbox, "area_km2": round(area_km2, 2),
        "images": len(d_imgs), "images_per_km2": round(len(d_imgs) / area_km2, 1) if area_km2 else None,
        "pano_images": sum(1 for im in d_imgs if im.get("is_pano")),
        "sequences": len({im.get("sequence_id") for im in d_imgs}),
        "capture_first": ms_date(caps[0]) if caps else None,
        "capture_last": ms_date(caps[-1]) if caps else None,
        "captures_by_year": dict(sorted(Counter(ms_date(c)[:4] for c in caps).items())),
        "map_features": len(d_feats), "features_per_km2": round(len(d_feats) / area_km2, 1) if area_km2 else None,
        "pole_like_features": pole_like,
        "pole_like_per_km2": round(pole_like / area_km2, 1) if area_km2 else None,
        "features_by_value": dict(by_value.most_common()),
        "utility_pole_features": poles,
        "district_of": args.town, "district_rule": feature["properties"]["rule"],
        "since": feature["properties"]["since"],
        "generated_at": feature["properties"]["generated_at"],
    }
    (cov_d / "summary.json").write_text(json.dumps(summary, indent=2))

    print(f"district {name!r} -> {slug}")
    print(f"  cell        {area_km2:.2f} km2 from {len(rings[0])} vertices, clipped against {len(centres) - 1} other places")
    print(f"  imagery     {len(d_imgs):,} images (area-clipped, all vintages kept for the coverage report)")
    print(f"  features    {len(d_feats_area):,} in the cell -> {len(d_feats):,} seen since "
          f"{feature['properties']['since'] or 'any date'}  ({poles:,} utility poles)")
    print(f"  -> {path_for(slug).relative_to(ROOT)}")
    print(f"  -> {cov_d.relative_to(ROOT)}/{{summary.json,images.jsonl,map_features.jsonl}}")
    print(f"\nnext: uv run python3 roadcover.py --town {name!r}")


if __name__ == "__main__":
    main()
