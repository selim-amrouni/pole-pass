#!/usr/bin/env python3
"""Stretch: diff the detected utility poles against OpenStreetMap.

Usage:
  uv run python3 osm.py --town "Greenpoint, Brooklyn, New York"

Reads  data/coverage/<slug>/summary.json   (bounding box)
       data/poles/<slug>/poles.jsonl        (records from dedupe.py)
Writes data/osm/<slug>/overpass.json       raw Overpass response, fetched once and never refreshed
       data/osm/<slug>/matches.jsonl       one row per utility record: nearest OSM pole node within 25 m, or none
       data/osm/<slug>/summary.json        counts at 8, 15, and 25 m; 15 m is the headline

OSM poles are nodes tagged power=pole (poles carrying power lines) or man_made=utility_pole
(poles carrying only telecom lines). A detected pole "is in OSM" when such a node lies within
the radius. Mapillary feature positions are off by several meters, so 8 m is strict, 25 m loose.
OSM completeness varies a lot by town; the diff says how much OSM is missing, not how much the
utility's own GIS is missing. OSM data is ODbL; the attribution goes into every output.
"""
import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

from coverage import DATA, ROOT, USER_AGENT, slugify
from dedupe import haversine_m

OVERPASS = "https://overpass-api.de/api/interpreter"
RADII_M = (8, 15, 25)
HEADLINE_M = 15
CELL_DEG = 0.0005  # ~55 m of latitude; a 3x3 cell neighbourhood covers every radius in RADII_M
OSM_ATTRIBUTION = "OpenStreetMap pole data: OpenStreetMap contributors, ODbL."


def overpass_query(bbox):
    w, s, e, n = bbox
    box = f"({s},{w},{n},{e})"
    return f'[out:json][timeout:180];(node["power"="pole"]{box};node["man_made"="utility_pole"]{box};);out body;'


def fetch_overpass(query, path):
    """Raw Overpass response for an Overpass QL query string, from cache when the file exists.

    Exits on a bad response; writes nothing then. Takes the query itself (not a bbox) so other
    modules can reuse this cache-forever-on-disk fetcher with their own Overpass QL."""
    if path.exists():
        return json.loads(path.read_text())
    body = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(OVERPASS, data=body, headers={"User-Agent": USER_AGENT})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                raw = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")[:300]
            if e.code in (429, 504) and attempt < 3:
                wait = 30 * (attempt + 1)
                print(f"  Overpass HTTP {e.code}, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            sys.exit(f"Overpass HTTP {e.code}: {text}")
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            sys.exit(f"Overpass request failed: {e}")
    if "elements" not in raw:
        sys.exit(f"unexpected Overpass response: {json.dumps(raw)[:300]}")
    raw["_fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    raw["_query"] = query
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw))
    return raw


def osm_nodes(raw):
    """[(osm_id, lon, lat, tag)] for the pole nodes in an Overpass response."""
    out = []
    for el in raw.get("elements", []):
        if el.get("type") != "node" or "lat" not in el:
            continue
        tags = el.get("tags", {})
        tag = "power=pole" if tags.get("power") == "pole" else "man_made=utility_pole" if tags.get("man_made") == "utility_pole" else None
        if tag:
            out.append((el["id"], el["lon"], el["lat"], tag))
    return out


def _cell(lon, lat, k):
    """Grid cell of a point; k = cos(reference latitude) is fixed per call so cell edges are straight lines, not latitude-dependent."""
    return (math.floor(lat / CELL_DEG), math.floor(lon * k / CELL_DEG))


def nearest_within(points, targets, max_m):
    """For each (id, lon, lat) in points: (id, target_id, distance_m) of the nearest target within max_m, else (id, None, None).

    targets: [(id, lon, lat, ...)]. Grid-bucketed so a town with tens of thousands of nodes stays fast."""
    ref_lat = sum(t[2] for t in targets) / len(targets) if targets else 0.0
    k = max(math.cos(math.radians(ref_lat)), 0.2)
    grid = defaultdict(list)
    for t in targets:
        grid[_cell(t[1], t[2], k)].append(t)
    out = []
    for pid, lon, lat in points:
        ci, cj = _cell(lon, lat, k)
        best, best_d = None, None
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for t in grid.get((ci + di, cj + dj), ()):
                    d = haversine_m(lon, lat, t[1], t[2])
                    if d <= max_m and (best_d is None or d < best_d):
                        best, best_d = t, d
        out.append((pid, best[0] if best else None, round(best_d, 1) if best else None))
    return out


def diff(poles, nodes):
    """Match records to OSM nodes both ways. poles: [(pole_id, lon, lat)]; nodes: [(osm_id, lon, lat, tag)]."""
    fwd = nearest_within(poles, nodes, max(RADII_M))
    rev = nearest_within([(n[0], n[1], n[2]) for n in nodes], [(p[0], p[1], p[2]) for p in poles], HEADLINE_M)
    tag_of = {n[0]: n[3] for n in nodes}
    matches = [{"pole_id": pid, "osm_id": oid, "osm_tag": tag_of.get(oid), "distance_m": d} for pid, oid, d in fwd]
    summary = {
        "osm_nodes": len(nodes),
        "osm_nodes_by_tag": {k: sum(1 for n in nodes if n[3] == k) for k in ("power=pole", "man_made=utility_pole")},
        "detected_utility": len(poles),
        "detected_with_osm_within_m": {str(r): sum(1 for _, oid, d in fwd if oid is not None and d <= r) for r in RADII_M},
        "headline_radius_m": HEADLINE_M,
        "osm_without_detected_within_headline": sum(1 for _, pid, _ in rev if pid is None),
    }
    summary["detected_without_osm_within_headline"] = len(poles) - summary["detected_with_osm_within_m"][str(HEADLINE_M)]
    return matches, summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--town", required=True)
    args = ap.parse_args()
    slug = slugify(args.town)
    cov_path = DATA / "coverage" / slug / "summary.json"
    poles_path = DATA / "poles" / slug / "poles.jsonl"
    for p in (cov_path, poles_path):
        if not p.exists():
            sys.exit(f"missing {p}; run coverage.py and dedupe.py first")
    bbox = json.load(cov_path.open())["bbox"]
    out_dir = DATA / "osm" / slug
    raw = fetch_overpass(overpass_query(bbox), out_dir / "overpass.json")
    nodes = osm_nodes(raw)
    poles = [(r["pole_id"], r["lon"], r["lat"]) for r in map(json.loads, poles_path.open()) if r.get("is_utility_pole")]
    matches, summary = diff(poles, nodes)
    with (out_dir / "matches.jsonl").open("w") as fh:
        for m in matches:
            fh.write(json.dumps(m) + "\n")
    summary.update({"slug": slug, "bbox": bbox, "fetched_at": raw.get("_fetched_at"), "source": OVERPASS, "attribution": OSM_ATTRIBUTION,
                    "files": {"overpass": str((out_dir / "overpass.json").relative_to(ROOT)), "matches": str((out_dir / "matches.jsonl").relative_to(ROOT)),
                              "poles": str(poles_path.relative_to(ROOT)), "coverage": str(cov_path.relative_to(ROOT))}})
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    w = summary["detected_with_osm_within_m"]
    print(f"{slug}: {len(nodes)} OSM pole nodes in the bbox {summary['osm_nodes_by_tag']}; {len(poles)} detected utility poles")
    print(f"  with an OSM pole within 8 / 15 / 25 m: {w['8']} / {w['15']} / {w['25']}  ->  {summary['detected_without_osm_within_headline']} absent from OSM at {HEADLINE_M} m")
    print(f"  OSM poles with no detected pole within {HEADLINE_M} m: {summary['osm_without_detected_within_headline']} (outside the photographed streets, or missed)")
    print(f"-> {out_dir.relative_to(ROOT)}/summary.json")


if __name__ == "__main__":
    main()
