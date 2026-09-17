#!/usr/bin/env python3
"""Marblehead maintenance split: which utility maintains which side of town.

Usage:
  uv run python3 split.py --town "Marblehead, Massachusetts" [--buffer 150] [--overlay]

CONTEXT (must stay faithful): the Marblehead Municipal Light Department (MMLD)
published a map -- an AxisGIS screenshot with a single hand-drawn straight
black line across the town. Southwest of it is labelled "Verizon", northeast
"MMLD". This is a POLE MAINTENANCE split only -- both parties jointly own all
poles in town. That sentence is carried through into the GeoJSON properties
and the printed summary, and it belongs on any web page built from this data.

The line itself was already digitised (do not re-derive it): the screenshot
was georeferenced by fitting OSM natural=coastline geometry to the dashed town
outline drawn on it, solving a 3-parameter north-up Web Mercator similarity
(scale 0.151400 px per Mercator metre, origin Mercator (-7892648.000,
5241316.000) at the image's top-left, i.e. 4.87 ground m/px at 42.5N; 79% of
coastline vertices land within 4 px of the drawn outline, degrading measurably
beyond +/-3 px, so registration is good to about +/-15 m). The drawn line's
pixels were then extracted with a Hough transform plus a total-least-squares
refit (2171 pixels, rms 1.45 px; ~68 m stroke width on the ground). Its
digitised endpoints (WGS84) are DIGITISED_NW and DIGITISED_SE below.

Reads  data/osm/<slug>/boundary.json   Overpass relation 2373036 (Marblehead
                                        admin_level=8), cached forever
Writes data/split/<slug>/split.geojson   the two maintenance halves + the
                                          extended split line, with provenance
       data/split/<slug>/overlay.png     --overlay only: line over an OSM
                                          basemap at the source screenshot's extent
       data/basemap/osm/15/<x>/<y>.png   --overlay only: cached basemap tiles

Public API for later phases (import, do not re-derive):
  town_ring(slug) -> the stitched boundary as one closed ring of (lon, lat)
  maintainer_of(lon, lat, line, buffer_m=150) -> "MMLD" | "VERIZON" | "UNCERTAIN"
  load_split(slug) -> parsed geometry from the written split.geojson
"""
import argparse
import io
import json
import math
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from PIL import Image, ImageDraw

import geo
import osm
from coverage import DATA, ROOT, slugify
from dedupe import haversine_m

# ---------------------------------------------------------------- provenance (given, not re-derived)
DIGITISED_NW = (-70.875462, 42.504476)
DIGITISED_SE = (-70.860375, 42.488829)

GEOREFERENCE = {
    "method": ("North-up Web Mercator similarity (1 scale + 2 offsets) fit between OSM "
               "natural=coastline geometry and the dashed town outline drawn on the "
               "AxisGIS screenshot."),
    "scale_px_per_mercator_m": 0.151400,
    "origin_mercator_xy_at_image_top_left": [-7892648.000, 5241316.000],
    "ground_m_per_px_at_42_5N": 4.87,
    "coastline_vertices_within_4px_pct": 79,
    "fit_note": "fit degrades measurably beyond +/-3 px; registration is good to about +/-15 m",
}
LINE_EXTRACTION = {
    "method": "Hough transform plus a total-least-squares refit of the drawn line's pixels.",
    "pixels": 2171,
    "rms_px": 1.45,
    "stroke_width_m": 68,
}

# The town's OSM relation is not hardcoded: coverage.geocode() already cached Nominatim's answer
# for this town (data/geocode/<slug>.json), which carries osm_type/osm_id. Reading it from there
# keeps town_ring() usable for any territory instead of only Marblehead. For Marblehead this
# resolves to relation 2373036, admin_level=8.
STITCH_TOLERANCE_M = 25  # generous slack for float round-trip of shared node coords across ways
DEFAULT_BUFFER_M = 150

SOURCE = "Marblehead Municipal Light Department published maintenance map (AxisGIS screenshot), traced by hand"
NOTE = "Maintenance split only. Both MMLD and Verizon jointly own all poles in town."
ATTRIBUTION = ("Boundary: OpenStreetMap contributors, ODbL. "
               "Split line: traced by hand from a Marblehead Municipal Light Department "
               "published maintenance map (AxisGIS screenshot).")

# Marblehead's OSM admin boundary runs out over the water (Massachusetts town lines do this), so
# its area is several times the town's land area and the two halves' areas are NOT comparable:
# the split line's southeast extension runs kilometres offshore, so the northeast half swallows
# most of the open water. Deliberately no land-area figure here -- this script fetches no
# coastline polygon, so any land/water breakdown would be an outside number this project cannot
# trace to a file in data/. The denominator that actually means something for poles is public
# road centreline length per half, which roadcover.py measures.

# ---------------------------------------------------------------- overlay basemap
OSM_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
BASEMAP_ZOOM = 15
BASEMAP_USER_AGENT = "pole-condition-pass/0.1 (maintenance split overlay)"
MAX_OVERLAY_TILES = 80
OVERLAY_EXTENT = (-70.900863, 42.468431, -70.830968, 42.534048)  # west, south, east, north; matches the source screenshot


# ================================================================== boundary stitching
def stitch_ring(ways, tol_m=STITCH_TOLERANCE_M):
    """Join way fragments -- each [(lon,lat), ...], arbitrary order and orientation -- into one closed ring.

    A real admin boundary's "outer" member ways are a set of fragments that meet end to end but
    are not necessarily sorted or consistently wound. Grow one ring greedily from the first
    fragment: at each step attach whichever remaining fragment (forward or reversed) touches
    either open end of the ring within tol_m, until none remain. Raises instead of returning a
    ring that didn't actually close -- callers must not silently fall back to a bbox.
    """
    remaining = [list(w) for w in ways if len(w) >= 2]
    if not remaining:
        raise ValueError("no way fragments to stitch")

    def close(p, q):
        return haversine_m(*p, *q) <= tol_m

    ring = remaining.pop(0)
    while remaining:
        for i, w in enumerate(remaining):
            if close(ring[-1], w[0]):
                ring.extend(w[1:])
            elif close(ring[-1], w[-1]):
                ring.extend(list(reversed(w))[1:])
            elif close(ring[0], w[-1]):
                ring[0:0] = w[:-1]
            elif close(ring[0], w[0]):
                ring[0:0] = list(reversed(w))[:-1]
            else:
                continue
            remaining.pop(i)
            break
        else:
            raise ValueError(f"could not attach {len(remaining)} of {len(ways)} way fragment(s) "
                              f"to the ring; endpoints do not match within {tol_m} m")
    if not close(ring[0], ring[-1]):
        raise ValueError(f"stitched path does not close: ends are {haversine_m(*ring[0], *ring[-1]):.1f} m apart")
    ring[-1] = ring[0]  # snap exact closure (floats may differ by fractions of tol_m)
    return ring


def town_relation_id(slug):
    """The town's OSM relation id, from the Nominatim answer coverage.py already cached."""
    path = DATA / "geocode" / f"{slug}.json"
    if not path.exists():
        sys.exit(f"missing {path}; run coverage.py --town ... first so the boundary can be resolved")
    hit = json.loads(path.read_text())
    if hit.get("osm_type") != "relation":
        sys.exit(f"{slug} geocoded to an OSM {hit.get('osm_type')}, not a relation; no boundary polygon to split")
    return int(hit["osm_id"])


def town_ring(slug):
    """The town's admin boundary, stitched into one closed outer ring.

    'out geom' embeds each member way's node coordinates directly in the relation response, so no
    separate way-geometry fetch is needed."""
    rel_id = town_relation_id(slug)
    query = f"[out:json][timeout:120];rel({rel_id});out geom;"
    raw = osm.fetch_overpass(query, DATA / "osm" / slug / "boundary.json")
    rels = [el for el in raw.get("elements", []) if el.get("type") == "relation"]
    if not rels:
        sys.exit(f"Overpass returned no relation for id {rel_id}")
    members = rels[0].get("members", [])
    outer_ways = [[(pt["lon"], pt["lat"]) for pt in m["geometry"]]
                  for m in members if m.get("type") == "way" and m.get("role") == "outer" and m.get("geometry")]
    if not outer_ways:
        sys.exit(f"relation {rel_id} has no 'outer' way members with geometry")
    try:
        return stitch_ring(outer_ways)
    except ValueError as e:
        sys.exit(f"failed to stitch town boundary into one ring: {e}")


# ================================================================== the split itself
def extend_to_boundary(a, b, ring):
    """Extend segment a->b to the boundary ring at both ends: the first exit beyond a (away from
    b), and the first exit beyond b (away from a). Returns (exit_near_a, exit_near_b).

    line_polygon_exits gives every crossing of the infinite line with the ring, ordered along
    a->b; a and b sit strictly between two of them (the digitised segment is well inside town),
    so the nearest crossing on each side of the segment is the right extension point even if the
    line grazes a concave stretch of coastline and crosses more than twice overall.
    """
    hits = geo.line_polygon_exits(a, b, ring)
    if len(hits) < 2:
        sys.exit(f"digitised line only crosses the town boundary {len(hits)} time(s); need at least 2 to extend both ends")
    lat0 = (a[1] + b[1]) / 2
    fwd, _ = geo.local_frame(lat0)
    ax, ay = fwd(*a)
    bx, by = fwd(*b)
    dx, dy = bx - ax, by - ay

    def t(p):
        px, py = fwd(*p)
        return dx * (px - ax) + dy * (py - ay)

    t_b = t(b)  # t(a) == 0 by construction
    before = [h for h in hits if t(h) < 0]
    after = [h for h in hits if t(h) > t_b]
    if not before or not after:
        sys.exit("digitised line does not exit the town boundary on both sides of the segment")
    return max(before, key=t), min(after, key=t)


def _ne_probe_point(a, b, dist_m=500.0):
    """A point dist_m along the perpendicular to a->b from its midpoint, on the geographic NE side.

    Used only to ask a clipped half "are you the northeast one?" -- NE/SW is decided by this test,
    never assumed from the line's slope."""
    lat0 = (a[1] + b[1]) / 2
    fwd, inv = geo.local_frame(lat0)
    ax, ay = fwd(*a)
    bx, by = fwd(*b)
    mx, my = (ax + bx) / 2, (ay + by) / 2
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    cand1, cand2 = (-uy, ux), (uy, -ux)  # the two perpendiculars to the heading
    ne = (math.sqrt(0.5), math.sqrt(0.5))
    px, py = cand1 if (cand1[0] * ne[0] + cand1[1] * ne[1]) >= (cand2[0] * ne[0] + cand2[1] * ne[1]) else cand2
    return inv(mx + px * dist_m, my + py * dist_m)


def assign_halves(a, b, ring):
    """Clip ring by the infinite line through a,b into MMLD (NE) and VERIZON (SW) halves, and
    assert the halves reconstruct the whole. The generic, testable core of build_split -- takes
    any line and ring, not just the Marblehead ones.

    Returns (line, mmld_ring, verizon_ring) where `line` is ordered so that, for any point,
    positive signed_dist_to_line_m(point, *line) means MMLD -- see maintainer_of. That ordering
    is derived here from the NE probe point, never hardcoded, so it holds regardless of which way
    a->b happens to run.
    """
    half_left = geo.clip_ring_to_halfplane(ring, a, b, True)
    half_right = geo.clip_ring_to_halfplane(ring, a, b, False)

    probe = _ne_probe_point(a, b)
    in_left = bool(half_left) and geo.point_in_polygon(*probe, [half_left])
    in_right = bool(half_right) and geo.point_in_polygon(*probe, [half_right])
    if in_left == in_right:  # neither, or (shouldn't happen) both
        sys.exit("could not determine which side of the split line is northeast from the probe point")
    ne_is_left = in_left

    line = (a, b) if ne_is_left else (b, a)  # MMLD (NE) is always "left" of line[0]->line[1]
    mmld_ring = half_left if ne_is_left else half_right
    verizon_ring = half_right if ne_is_left else half_left

    whole_area = geo.ring_area_m2(ring)
    split_area = geo.ring_area_m2(mmld_ring) + geo.ring_area_m2(verizon_ring)
    if abs(split_area - whole_area) > 0.01 * whole_area:
        sys.exit(f"clipped halves ({split_area / 1e6:.2f} km2) do not sum to the whole ring "
                  f"({whole_area / 1e6:.2f} km2) within 1%")
    return line, mmld_ring, verizon_ring


def build_split(ring):
    """Extend the digitised Marblehead line to the boundary and split ring with assign_halves."""
    exit_a, exit_b = extend_to_boundary(DIGITISED_NW, DIGITISED_SE, ring)
    return assign_halves(exit_a, exit_b, ring)


def maintainer_of(lon, lat, line, buffer_m=DEFAULT_BUFFER_M):
    """"MMLD", "VERIZON", or "UNCERTAIN" for a point, given the extended split `line` (a, b) as
    written by build_split / load_split.

    Checked BEFORE the side test: any point within buffer_m of the line is UNCERTAIN, regardless
    of which side it falls on."""
    d = geo.signed_dist_to_line_m(lon, lat, line[0], line[1])
    if abs(d) < buffer_m:
        return "UNCERTAIN"
    return "MMLD" if d > 0 else "VERIZON"


def load_split(slug):
    """Parsed split geometry from data/split/<slug>/split.geojson, so later phases never re-derive it.

    Returns {"mmld": ring, "verizon": ring, "line": (a, b), "buffer_m": float}, ring/line as
    (lon, lat) tuples, `line` already in maintainer_of's convention."""
    path = DATA / "split" / slug / "split.geojson"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing; run split.py --town ... first")
    fc = json.loads(path.read_text())
    polys, line, buffer_m = {}, None, None
    for f in fc["features"]:
        geom, props = f["geometry"], f["properties"]
        if geom["type"] == "Polygon":
            polys[props["maintainer"]] = [tuple(pt) for pt in geom["coordinates"][0]]
        elif geom["type"] == "LineString":
            line = tuple(tuple(pt) for pt in geom["coordinates"])
        buffer_m = props.get("buffer_m", buffer_m)
    return {"mmld": polys["MMLD"], "verizon": polys["VERIZON"], "line": line, "buffer_m": buffer_m}


# ================================================================== GeoJSON
def _closed(ring):
    return ring if ring[0] == ring[-1] else ring + [ring[0]]


def write_geojson(path, line, mmld_ring, verizon_ring, buffer_m):
    shared = {"note": NOTE, "approximate": True, "buffer_m": buffer_m, "source": SOURCE, "attribution": ATTRIBUTION}
    features = [
        {"type": "Feature", "properties": {**shared, "maintainer": "MMLD"},
         "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in _closed(mmld_ring)]]}},
        {"type": "Feature", "properties": {**shared, "maintainer": "VERIZON"},
         "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in _closed(verizon_ring)]]}},
        {"type": "Feature", "properties": {**shared, "maintainer": "SPLIT"},
         "geometry": {"type": "LineString", "coordinates": [list(line[0]), list(line[1])]}},
    ]
    fc = {
        "type": "FeatureCollection",
        "properties": {
            "digitised_endpoints": {"nw": list(DIGITISED_NW), "se": list(DIGITISED_SE)},
            "georeference": GEOREFERENCE,
            "line_extraction": LINE_EXTRACTION,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        },
        "features": features,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fc, indent=2))


# ================================================================== overlay
def _lonlat_to_px(lon, lat, z):
    """Pixel coordinates in the standard OSM slippy-map tile scheme at zoom z (0,0 = top-left of the world)."""
    x, y = geo.to_merc(lon, lat)
    world = 256 * (2 ** z)
    c = 2 * math.pi * geo.R_MERC  # world circumference in Mercator metres
    return (x + c / 2) / c * world, (c / 2 - y) / c * world


def _fetch_tile(z, x, y):
    path = DATA / "basemap" / "osm" / str(z) / str(x) / f"{y}.png"
    if path.exists():
        return path.read_bytes()
    req = urllib.request.Request(OSM_TILES.format(z=z, x=x, y=y), headers={"User-Agent": BASEMAP_USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
            break
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            sys.exit(f"failed to fetch basemap tile {z}/{x}/{y}: {e}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def render_overlay(path, extent, digitised, extended_a, extended_b, mmld_ring, verizon_ring, buffer_m):
    """Split line over a labelled OSM basemap, cropped to `extent`, for side-by-side comparison
    with the source screenshot. Fetches at most MAX_OVERLAY_TILES z15 tiles, cached forever."""
    west, south, east, north = extent
    x0, y0 = _lonlat_to_px(west, north, BASEMAP_ZOOM)
    x1, y1 = _lonlat_to_px(east, south, BASEMAP_ZOOM)
    tx0, ty0 = int(x0 // 256), int(y0 // 256)
    tx1, ty1 = int(x1 // 256), int(y1 // 256)
    n_tiles = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
    if n_tiles > MAX_OVERLAY_TILES:
        sys.exit(f"overlay extent needs {n_tiles} z{BASEMAP_ZOOM} tiles, over the {MAX_OVERLAY_TILES}-tile cap")

    mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            tile = Image.open(io.BytesIO(_fetch_tile(BASEMAP_ZOOM, tx, ty))).convert("RGB")
            mosaic.paste(tile, ((tx - tx0) * 256, (ty - ty0) * 256))
    crop = mosaic.crop((int(x0 - tx0 * 256), int(y0 - ty0 * 256), int(x1 - tx0 * 256), int(y1 - ty0 * 256))).convert("RGBA")

    def px(lon, lat):
        x, y = _lonlat_to_px(lon, lat, BASEMAP_ZOOM)
        return x - x0, y - y0

    m_per_px = haversine_m(west, (north + south) / 2, east, (north + south) / 2) / crop.width

    overlay = Image.new("RGBA", crop.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay, "RGBA")

    def poly(ring, color):
        if ring:
            d.polygon([px(*p) for p in ring], fill=color)

    poly(mmld_ring, (40, 90, 220, 45))      # faint blue: MMLD (northeast)
    poly(verizon_ring, (230, 140, 30, 45))  # faint orange: VERIZON (southwest)

    buffer_px = max(1, round(buffer_m * 2 / m_per_px))
    d.line([px(*extended_a), px(*extended_b)], fill=(200, 0, 0, 60), width=buffer_px)

    def dashed(p1, p2, color, width=3, dash=10, gap=8):
        (x1, y1), (x2, y2) = p1, p2
        length = math.hypot(x2 - x1, y2 - y1)
        if length == 0:
            return
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        pos = 0.0
        while pos < length:
            end = min(pos + dash, length)
            d.line([(x1 + ux * pos, y1 + uy * pos), (x1 + ux * end, y1 + uy * end)], fill=color, width=width)
            pos += dash + gap

    dashed(px(*extended_a), px(*digitised[0]), (200, 0, 0, 255))
    d.line([px(*digitised[0]), px(*digitised[1])], fill=(200, 0, 0, 255), width=3)
    dashed(px(*digitised[1]), px(*extended_b), (200, 0, 0, 255))

    out = Image.alpha_composite(crop, overlay)
    d2 = ImageDraw.Draw(out, "RGBA")
    lx, ly = 12, 12
    for label, color in (("MMLD (NE)", (40, 90, 220, 255)), ("VERIZON (SW)", (230, 140, 30, 255)),
                          ("digitised split line", (200, 0, 0, 255)), (f"+/-{buffer_m:.0f} m uncertainty", (200, 0, 0, 120))):
        d2.rectangle([lx, ly, lx + 16, ly + 16], fill=color)
        d2.text((lx + 22, ly + 2), label, fill=(0, 0, 0, 255))
        ly += 20
    footer = ("Basemap © OpenStreetMap contributors. Split traced from the MMLD published maintenance map "
              "-- approximate, maintenance only; both parties jointly own all poles in town.")
    d2.rectangle([0, out.height - 18, out.width, out.height], fill=(255, 255, 255, 220))
    d2.text((6, out.height - 16), footer, fill=(0, 0, 0, 255))

    path.parent.mkdir(parents=True, exist_ok=True)
    out.convert("RGB").save(path)


# ================================================================== main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--town", required=True)
    ap.add_argument("--buffer", type=float, default=DEFAULT_BUFFER_M, help="maintainer_of uncertainty buffer, metres")
    ap.add_argument("--overlay", action="store_true", help="also render overlay.png against an OSM basemap")
    args = ap.parse_args()
    slug = slugify(args.town)

    ring = town_ring(slug)
    whole_km2 = geo.ring_area_m2(ring) / 1e6
    line, mmld_ring, verizon_ring = build_split(ring)
    # `line` is ordered for maintainer_of (MMLD is always "left" of line[0]->line[1]), which need
    # not be NW-first; for printing, relabel by whichever extended point is actually nearer DIGITISED_NW
    extended_a, extended_b = line
    if haversine_m(*extended_a, *DIGITISED_NW) > haversine_m(*extended_b, *DIGITISED_NW):
        extended_a, extended_b = extended_b, extended_a

    out_dir = DATA / "split" / slug
    write_geojson(out_dir / "split.geojson", line, mmld_ring, verizon_ring, args.buffer)

    mmld_km2 = geo.ring_area_m2(mmld_ring) / 1e6
    verizon_km2 = geo.ring_area_m2(verizon_ring) / 1e6
    length_km = geo.polyline_length_m([extended_a, extended_b]) / 1000
    digitised_km = geo.polyline_length_m([DIGITISED_NW, DIGITISED_SE]) / 1000

    print(f"digitised endpoints   NW {DIGITISED_NW[1]:.6f},{DIGITISED_NW[0]:.6f}   SE {DIGITISED_SE[1]:.6f},{DIGITISED_SE[0]:.6f}  (lat,lon)")
    print(f"extended endpoints    NW {extended_a[1]:.6f},{extended_a[0]:.6f}   SE {extended_b[1]:.6f},{extended_b[0]:.6f}  (lat,lon)")
    print(f"split line length     {digitised_km:.2f} km as drawn, {length_km:.2f} km extended boundary to boundary")
    print(f"town boundary area (OSM relation {town_relation_id(slug)}): {whole_km2:.2f} km2")
    print(f"  MMLD (northeast) half:    {mmld_km2:.2f} km2")
    print(f"  VERIZON (southwest) half: {verizon_km2:.2f} km2")
    print("  These areas are mostly OPEN WATER and are not comparable to each other: Massachusetts town")
    print("  boundaries run offshore, and the line's southeast extension runs kilometres out to sea, so the")
    print("  northeast half takes nearly all of it. Expected, not an error. For a denominator that means")
    print("  something for poles, use public road centreline length per half from roadcover.py.")
    print(f"uncertainty buffer: {args.buffer:.0f} m")
    print(NOTE)
    print(f"-> {(out_dir / 'split.geojson').relative_to(ROOT)}")

    if args.overlay:
        render_overlay(out_dir / "overlay.png", OVERLAY_EXTENT, (DIGITISED_NW, DIGITISED_SE),
                        extended_a, extended_b, mmld_ring, verizon_ring, args.buffer)
        print(f"-> {(out_dir / 'overlay.png').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
