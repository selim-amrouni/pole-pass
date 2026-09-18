#!/usr/bin/env python3
"""Phase 1: is Mapillary coverage good enough to go hunting for double poles?

Usage:
  uv run python3 roadcover.py --town "Marblehead, Massachusetts" [--radius 20] [--step 10]

Reads  data/coverage/<slug>/{images.jsonl,map_features.jsonl,summary.json}  (from coverage.py)
       data/split/<slug>/split.geojson    via split.town_ring / split.load_split / split.maintainer_of
Writes data/osm/<slug>/roads.json          raw Overpass response for public-road ways, cached forever
       data/roadcover/<slug>/summary.json  every number below, plus the verdict and which rule fired
       data/roadcover/<slug>/streets.jsonl one row per named street: length_m, covered_fraction

This exists because a bbox density number is meaningless here: Marblehead's coverage bbox is
131 km2 of which most is ocean. Everything is instead clipped to the actual town polygon
(split.town_ring), and a public road's coverage is measured against its own centreline length,
not the bbox area.

Capture recency matters as much as raw coverage: a double pole (an old pole left standing next
to its replacement) that was cleared last month still shows up as a double in a 2018 photo. A
coverage number with no date context is not good enough to plan a field visit around.

Coverage is checked against PUBLIC_HIGHWAY_CLASSES only -- service drives, tracks, footways and
the like are excluded (see the constant) so pavement no utility pole lines does not inflate the
denominator.
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import district
import geo
import osm
import split
from coverage import DATA, POLE_VALUES, ROOT, slugify

# highway=* values that carry public through-traffic and therefore line with utility poles.
# service is excluded on purpose: driveways and parking-lot aisles would inflate the coverage
# denominator with pavement that photography of the pole network has no reason to reach.
# track/footway/path/cycleway/steps/pedestrian/construction/proposed are excluded for the same
# reason -- none of them are public streets a pole stands beside.
PUBLIC_HIGHWAY_CLASSES = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential", "living_street",
}

BUFFER_M = 150  # maintainer_of's uncertainty band either side of the split line
RECENT_CUTOFF = "2024-01-01"  # MA double-pole removal deadline horizon; older photos may show doubles since cleared
UNUSABLE_COVERAGE = 0.35
THIN_COVERAGE = 0.70
THIN_RECENT_SHARE = 0.25
STALE_MAJORITY = 0.50  # loud warning threshold: over half of in-town images predate RECENT_CUTOFF
HALF_DIFF_FLAG = 0.10  # percentage-point gap between MMLD and Verizon coverage worth calling out
ATTRIBUTION = "Imagery: Mapillary, CC BY-SA 4.0. Roads and boundary: OpenStreetMap contributors, ODbL."


def roads_query(bbox):
    """Overpass QL for every highway=* way in bbox, with full node geometry (out geom;).

    Unfiltered on purpose -- PUBLIC_HIGHWAY_CLASSES is applied in Python (kept_ways) so the
    raw cache holds everything Overpass has, in case the class list changes later."""
    w, s, e, n = bbox
    box = f"({s},{w},{n},{e})"
    return f'[out:json][timeout:180];way["highway"]{box};out geom;'


def kept_ways(raw):
    """[{'id', 'name', 'coords':[(lon,lat),...]}] for public-road ways, from a roads_query response.

    Drops anything not in PUBLIC_HIGHWAY_CLASSES, area=yes ways (mapped as polygons, not
    centrelines), and any way Overpass returned without at least two real coordinates."""
    out = []
    for el in raw.get("elements", []):
        if el.get("type") != "way":
            continue
        tags = el.get("tags", {})
        if tags.get("highway") not in PUBLIC_HIGHWAY_CLASSES or tags.get("area") == "yes":
            continue
        coords = [(n["lon"], n["lat"]) for n in el.get("geometry") or [] if n and "lon" in n]
        if len(coords) < 2:
            continue
        out.append({"id": el["id"], "name": tags.get("name"), "coords": coords})
    return out


def _ring_bbox(ring):
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return min(lons), min(lats), max(lons), max(lats)


def in_town(lon, lat, ring, ring_bbox):
    """geo.point_in_polygon against ring, with a cheap bbox reject first -- Marblehead's boundary
    ring runs far offshore, so most of the coverage bbox is nowhere near it."""
    w, s, e, n = ring_bbox
    if not (w <= lon <= e and s <= lat <= n):
        return False
    return geo.point_in_polygon(lon, lat, [ring])


def samples_for_ways(ways, ring, step_m):
    """Densify every way's FULL geometry, then keep only the samples that land inside ring.

    Densifying before clipping (rather than clipping each way's line first) is what makes every
    surviving sample worth exactly step_m metres of centreline: clip first and the sample at a
    boundary crossing would represent a fractional, unknown length instead."""
    ring_bbox = _ring_bbox(ring)
    out = []
    for w in ways:
        for i, (lon, lat) in enumerate(geo.densify(w["coords"], step_m)):
            if in_town(lon, lat, ring, ring_bbox):
                out.append({"id": f"{w['id']}:{i}", "name": w["name"], "lon": lon, "lat": lat})
    return out


def coverage_share(samples, covered):
    """Fraction of samples that are covered. covered: {sample_id: bool}.

    Each sample is step_m metres of centreline, so counting samples (not ways) gives a
    LENGTH-weighted share: a 1 km street with zero coverage and a 10 m street fully covered
    come out near 1%, not the 50% a per-way average would report."""
    if not samples:
        return 0.0
    return sum(1 for s in samples if covered.get(s["id"], False)) / len(samples)


def street_rows(samples, covered, step_m):
    """One row per distinct way `name` (unnamed ways excluded): length_m and covered_fraction.

    Grouping by name aggregates every OSM way segment that shares it, so a street cut into
    several ways at intersections still reports as a single row."""
    by_name = defaultdict(list)
    for s in samples:
        if s["name"]:
            by_name[s["name"]].append(s)
    rows = []
    for name, ss in by_name.items():
        n = len(ss)
        cov = sum(1 for s in ss if covered.get(s["id"], False))
        rows.append({"name": name, "length_m": round(n * step_m, 1), "n_samples": n, "covered_fraction": round(cov / n, 4)})
    return rows


def streets_with_no_coverage(rows):
    return sorted((r for r in rows if r["covered_fraction"] == 0.0), key=lambda r: -r["length_m"])


def quarter_of(ms):
    d = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def verdict_for(overall_frac, recent_share):
    """(verdict, rule) using the module-constant thresholds, so the printed rule always carries
    the numbers that fired it rather than just the label."""
    if overall_frac < UNUSABLE_COVERAGE:
        return "UNUSABLE", f"overall road coverage {overall_frac:.1%} < {UNUSABLE_COVERAGE:.0%}"
    if overall_frac < THIN_COVERAGE:
        return "THIN", f"overall road coverage {overall_frac:.1%} < {THIN_COVERAGE:.0%}"
    if recent_share < THIN_RECENT_SHARE:
        return "THIN", f"only {recent_share:.1%} of in-town images are from {RECENT_CUTOFF} or later (< {THIN_RECENT_SHARE:.0%})"
    return "GOOD", f"overall road coverage {overall_frac:.1%} >= {THIN_COVERAGE:.0%} and {recent_share:.1%} of images are {RECENT_CUTOFF}+"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--town", required=True)
    ap.add_argument("--radius", type=float, default=20.0, help="metres; an image within this of a road sample covers it")
    ap.add_argument("--step", type=float, default=10.0, help="metres between road-centreline samples")
    args = ap.parse_args()
    slug = slugify(args.town)
    cov_dir = DATA / "coverage" / slug
    for name in ("images.jsonl", "map_features.jsonl", "summary.json"):
        if not (cov_dir / name).exists():
            sys.exit(f"missing {cov_dir / name}; run coverage.py first")
    bbox = json.load((cov_dir / "summary.json").open())["bbox"]

    ring = district.ring(slug)  # the district's cell when slug names one, else the OSM town relation
    # The maintenance split is Marblehead-only: it exists because the Light Department published an
    # MMLD/Verizon boundary. Most towns have a single investor-owned pole owner and no such map, so
    # a missing split is the normal case, not an error -- every per-maintainer number below is then
    # simply not computed. town_ring above is NOT optional: it is the town boundary itself.
    try:
        line = split.load_split(slug)["line"]  # the extended split LineString's two endpoints
    except (FileNotFoundError, SystemExit):
        line = None

    raw = osm.fetch_overpass(roads_query(bbox), DATA / "osm" / slug / "roads.json")
    ways = kept_ways(raw)
    samples = samples_for_ways(ways, ring, args.step)
    for s in samples:
        s["maintainer"] = split.maintainer_of(s["lon"], s["lat"], line, buffer_m=BUFFER_M) if line else None

    images = [json.loads(l) for l in (cov_dir / "images.jsonl").open()]
    features = [json.loads(l) for l in (cov_dir / "map_features.jsonl").open()]
    ring_bbox = _ring_bbox(ring)
    town_images = [im for im in images if in_town(im["lon"], im["lat"], ring, ring_bbox)]
    town_features = [f for f in features if in_town(f["lon"], f["lat"], ring, ring_bbox)]
    for f in town_features:
        f["maintainer"] = split.maintainer_of(f["lon"], f["lat"], line, buffer_m=BUFFER_M) if line else None

    # Coverage matches against EVERY image, not just in-town ones: a photographer standing a
    # few metres outside the (legally generous, offshore-extended) town line can still document
    # a pole on a road just inside it. "images inside the town" below is a separate count.
    sample_pts = [(s["id"], s["lon"], s["lat"]) for s in samples]
    img_targets = [(im["id"], im["lon"], im["lat"]) for im in images]
    nearest_img = osm.nearest_within(sample_pts, img_targets, args.radius) if samples and images else []
    covered = {sid: oid is not None for sid, oid, _ in nearest_img}

    # Reverse direction: which in-town images actually sit near a public road at all.
    town_img_pts = [(im["id"], im["lon"], im["lat"]) for im in town_images]
    nearest_road = osm.nearest_within(town_img_pts, sample_pts, args.radius) if town_img_pts and samples else [(p[0], None, None) for p in town_img_pts]
    img_on_road = {iid: sid is not None for iid, sid, _ in nearest_road}

    overall_frac = coverage_share(samples, covered)
    by_half = {}
    for half in ("MMLD", "VERIZON", "UNCERTAIN") if line else ():
        half_samples = [s for s in samples if s["maintainer"] == half]
        by_half[half] = {
            "n_samples": len(half_samples),
            "length_m": round(len(half_samples) * args.step, 1),
            "covered_fraction": round(coverage_share(half_samples, covered), 4) if half_samples else None,
        }

    # Coverage recomputed against progressively fresher slices of imagery. A double pole is only
    # evidence if the photo is recent enough that the pole plausibly still stands, so "how much
    # road does the RECENT imagery see" decides this project, not the all-vintage number. Reported
    # per half too, because a half with no recent imagery cannot be compared against one that has it.
    by_vintage = {}
    for label, keep in (("all", lambda y: True), ("2023", lambda y: y == 2023),
                        ("2024+", lambda y: y >= 2024), ("2025+", lambda y: y >= 2025)):
        subset = [(im["id"], im["lon"], im["lat"]) for im in images
                  if im.get("captured_at") and keep(datetime.fromtimestamp(im["captured_at"] / 1000, tz=timezone.utc).year)]
        if not subset:
            by_vintage[label] = {"n_images": 0, "covered_fraction": 0.0, "by_maintainer": {}}
            continue
        cov_v = {sid: oid is not None for sid, oid, _ in osm.nearest_within(sample_pts, subset, args.radius)}
        by_vintage[label] = {
            "n_images": len(subset),
            "covered_fraction": round(coverage_share(samples, cov_v), 4),
            "by_maintainer": {h: round(coverage_share([s for s in samples if s["maintainer"] == h], cov_v), 4)
                              for h in (("MMLD", "VERIZON") if line else ()) if any(s["maintainer"] == h for s in samples)},
        }

    rows = street_rows(samples, covered, args.step)
    no_coverage = streets_with_no_coverage(rows)
    total_length_m = round(len(samples) * args.step, 1)

    dated = sorted(im["captured_at"] for im in town_images if im.get("captured_at"))
    by_year = Counter(datetime.fromtimestamp(c / 1000, tz=timezone.utc).year for c in dated)
    by_quarter = Counter(quarter_of(c) for c in dated)
    recent_cutoff_ms = int(datetime.strptime(RECENT_CUTOFF, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    n_recent = sum(1 for c in dated if c >= recent_cutoff_ms)
    recent_share = n_recent / len(dated) if dated else 0.0
    stale_share = 1 - recent_share if dated else 0.0

    # A rolling three-year window from run time, separate from the fixed RECENT_CUTOFF above. A pole
    # photographed in 2019 says nothing about whether a double still stands today, so this is the
    # share that decides whether a town is worth a pass, and it has to move with the calendar.
    three_year_ms = int((datetime.now(tz=timezone.utc) - timedelta(days=3 * 365)).timestamp() * 1000)
    n_3y = sum(1 for c in dated if c >= three_year_ms)
    share_3y = n_3y / len(dated) if dated else 0.0

    town_seqs = {im.get("sequence_id") for im in town_images}
    seqs_on_road = {im.get("sequence_id") for im in town_images if img_on_road.get(im["id"])}

    pole_features = [f for f in town_features if f.get("value") in POLE_VALUES]
    by_value = Counter(f["value"] for f in pole_features)
    by_value_half = defaultdict(Counter)
    for f in pole_features if line else ():
        by_value_half[f["value"]][f["maintainer"]] += 1

    verdict, rule = verdict_for(overall_frac, recent_share)
    half_covs = {h: by_half[h]["covered_fraction"] for h in ("MMLD", "VERIZON")
                 if by_half.get(h, {}).get("covered_fraction") is not None}
    half_diff = abs(half_covs["MMLD"] - half_covs["VERIZON"]) if len(half_covs) == 2 else None

    out_dir = DATA / "roadcover" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "streets.jsonl").open("w") as fh:
        for r in sorted(rows, key=lambda r: -r["length_m"]):
            fh.write(json.dumps(r) + "\n")

    summary = {
        "slug": slug, "bbox": bbox, "radius_m": args.radius, "step_m": args.step,
        "images_in_town": len(town_images), "images_in_town_on_public_road": sum(img_on_road.values()),
        "sequences_in_town": len(town_seqs), "sequences_in_town_on_public_road": len(seqs_on_road),
        "capture_first": dated and datetime.fromtimestamp(dated[0] / 1000, tz=timezone.utc).date().isoformat(),
        "capture_last": dated and datetime.fromtimestamp(dated[-1] / 1000, tz=timezone.utc).date().isoformat(),
        "captures_by_year": dict(sorted((str(k), v) for k, v in by_year.items())),
        "captures_by_quarter": dict(sorted(by_quarter.items())),
        "recent_cutoff": RECENT_CUTOFF, "images_recent": n_recent, "images_recent_share": round(recent_share, 4),
        "images_stale_share": round(stale_share, 4),
        "three_year_cutoff": datetime.fromtimestamp(three_year_ms / 1000, tz=timezone.utc).date().isoformat(),
        "images_last_3y": n_3y, "images_last_3y_share": round(share_3y, 4),
        "road_coverage_overall": round(overall_frac, 4),
        "road_coverage_by_maintainer": by_half,
        "road_coverage_by_vintage": by_vintage,
        "half_coverage_diff": round(half_diff, 4) if half_diff is not None else None,
        "half_coverage_diff_flagged": bool(half_diff is not None and half_diff > HALF_DIFF_FLAG),
        "named_streets_total": len(rows), "total_road_length_m_in_town": total_length_m,
        "streets_with_no_coverage": [{"name": r["name"], "length_m": r["length_m"]} for r in no_coverage],
        "pole_like_features_by_value": dict(by_value),
        "pole_like_features_by_value_and_maintainer": {v: dict(c) for v, c in by_value_half.items()},
        "verdict": verdict, "verdict_rule": rule,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "attribution": ATTRIBUTION,
        "files": {"roads": str((DATA / "osm" / slug / "roads.json").relative_to(ROOT)),
                  "streets": str((out_dir / "streets.jsonl").relative_to(ROOT)),
                  "coverage": str((cov_dir / "summary.json").relative_to(ROOT))},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    print(f"\n== {slug}  ({len(ways)} public-road ways -> {len(samples)} samples @ {args.step:.0f} m, {total_length_m:,.0f} m centreline in town)")
    print(f"images in town        {len(town_images):>7}   on a public road (<= {args.radius:.0f} m) {sum(img_on_road.values()):>7}")
    print(f"sequences in town     {len(town_seqs):>7}   with >=1 image on a public road {len(seqs_on_road):>7}")
    print(f"captured              {summary['capture_first']} -> {summary['capture_last']}")
    print(f"  by year   {summary['captures_by_year']}")
    print(f"  by quarter {summary['captures_by_quarter']}")
    print(f"recent ({RECENT_CUTOFF}+)   {n_recent:>7} / {len(dated)}  = {recent_share:.1%}")
    print(f"last 3 years          {n_3y:>7} / {len(dated)}  = {share_3y:.1%}   (since {summary['three_year_cutoff']})"
          if dated else "last 3 years          no dated in-town imagery")
    print(f"\nroad coverage overall  {overall_frac:.1%}  ({sum(covered.values())}/{len(samples)} samples within {args.radius:.0f} m of an image)")
    for half in ("MMLD", "VERIZON", "UNCERTAIN") if line else ():
        h = by_half[half]
        cov_str = f"{h['covered_fraction']:.1%}" if h["covered_fraction"] is not None else "n/a"
        print(f"  {half:9} {h['n_samples']:>6} samples  {h['length_m']:>9,.0f} m  coverage {cov_str}")
    if not line:
        print("  (no maintenance split for this town: one pole owner, or no published boundary)")
    if half_diff is not None:
        flag = "  <-- differs by more than 10pp, biases any MMLD-vs-Verizon comparison" if half_diff > HALF_DIFF_FLAG else ""
        print(f"  MMLD vs Verizon coverage gap: {half_diff:.1%}{flag}")
    print(f"\nnamed streets: {len(rows)} total, {len(no_coverage)} with zero coverage")
    for r in no_coverage:
        print(f"  {r['name']:30} {r['length_m']:>8,.0f} m")
    print(f"\npole-like map features in town: {sum(by_value.values())}  {dict(by_value)}")
    for v, c in by_value_half.items():
        print(f"  {v}: {dict(c)}")
    print("\ncoverage by imagery vintage (a double pole is only evidence if the photo is recent).")
    print("image counts here are bbox-wide, because a photographer just outside the town line can still")
    print("document a pole just inside it; the coverage percentages are of IN-TOWN road centreline.")
    for label, v in by_vintage.items():
        halves = "  ".join(f"{h} {f:5.1%}" for h, f in v["by_maintainer"].items())
        print(f"  {label:6} {v['n_images']:>6} imgs(bbox)   {v['covered_fraction']:6.1%} of in-town road   {halves}")
    dead = [h for label, v in by_vintage.items() if label != "all" and label != "2023"
            for h, f in v["by_maintainer"].items() if f == 0.0]
    if dead:
        print(f"  NOTE: {', '.join(sorted(set(dead)))} has NO post-2023 imagery at all -- the halves cannot be")
        print("        compared on recent evidence, only on 2023 photos.")

    print(f"\nVERDICT: {verdict}  ({rule})")
    if stale_share > STALE_MAJORITY:
        print("\n" + "!" * 72)
        print(f"! STALE IMAGERY: {stale_share:.1%} of in-town images predate {RECENT_CUTOFF}.")
        print("! A double pole visible in one of these photos may already have been cleared.")
        print("!" * 72)
    print(f"\n-> {out_dir.relative_to(ROOT)}/{{summary.json,streets.jsonl}}")


if __name__ == "__main__":
    main()
