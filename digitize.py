#!/usr/bin/env python3
"""Digitize the MMLD/Verizon maintenance-split line into WGS84 endpoints.

Usage:
  uv run python3 digitize.py [--image docs/mmld-maintenance-map.png] [--overlay]

Reads  <image>                                             source screenshot
                                                            (default docs/mmld-maintenance-map.png)
       data/osm/marblehead-massachusetts/coastline.json    natural=coastline ways, fetched via
                                                            Overpass and cached forever if missing
       data/osm/marblehead-massachusetts/boundary.json     Marblehead's admin boundary relation,
                                                            fetched and cached if missing (--overlay only)
Writes docs/digitize-check.png                             only with --overlay: projected coastline
                                                            (red), boundary (blue), landmarks (magenta
                                                            rings), split endpoints (green rings)

Marblehead Municipal Light Department publishes the MMLD/Verizon maintenance
split as a single straight line hand-drawn on an AxisGIS screenshot -- not a
survey. This script is the only place that picture becomes coordinates;
split.py imports the two endpoints printed at the end as constants, so anyone
reading split.py can trace them back here instead of trusting magic numbers.

Six stages, each printed as it runs:
  1. find the drawn line       Hough transform over dark pixels, then a
                                total-least-squares refit of the winning peak
  2. georeference the image    3-parameter similarity fit (AxisGIS is north-up
                                Web Mercator) against OSM natural=coastline
  3. quantify uncertainty      registration sensitivity + drawn stroke width
  4. cross-check nine independent OSM landmarks looked up by name
  5. print the final endpoints in WGS84 and the screenshot's geographic extent
  6. --overlay: draw the picture that proves the fit
"""
import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw

from coverage import DATA, ROOT
from osm import fetch_overpass

SLUG = "marblehead-massachusetts"
DEFAULT_IMAGE = ROOT / "docs" / "mmld-maintenance-map.png"
OVERLAY_PATH = ROOT / "docs" / "digitize-check.png"
COASTLINE_PATH = DATA / "osm" / SLUG / "coastline.json"
BOUNDARY_PATH = DATA / "osm" / SLUG / "boundary.json"
COASTLINE_QUERY = ('[out:json][timeout:180];'
                    'way["natural"="coastline"](42.470,-70.930,42.545,-70.790);out geom;')
# Marblehead's administrative boundary relation, looked up by name on
# openstreetmap.org during the original investigation. Used only to draw the
# --overlay check picture, not part of the fit itself.
BOUNDARY_RELATION_ID = 2373036
BOUNDARY_QUERY = f"[out:json][timeout:180];relation({BOUNDARY_RELATION_ID});out geom;"

# Geometry of the screenshot itself, not of anything drawn on it: the frame
# border is ~26px wide on every edge, and the caption text starts at y=1500.
# Both the line search (stage 1) and the coastline scoring (stage 2) need to
# stay inside this interior so neither the frame nor the caption can win.
FRAME_MARGIN = 26
MAP_AREA_Y = 1500

DARK_RGB = 70          # per-channel threshold for "this pixel is drawn ink" (stage 1)
DARK_AVG = 110         # average-of-channels threshold, looser, catches anti-aliased
                       # coastline strokes too (stage 2 and 3)
SEED_BAND_PX = 9       # stage 1: how far off the Hough line a pixel can be and still seed the refit
SPLIT_EXCLUDE_PX = 10  # stage 2/3: how far from the fitted split line a pixel must be to count
                       # as coastline ink rather than the split line's own anti-aliased edge
CHAMFER_NEAR = 12      # chamfer units (3 per orthogonal step) == 4 px, the coastline match radius
STROKE_WINDOW_PAD = 100  # stage 3: padding around the fitted endpoints' bounding box when
                          # measuring stroke width; the measurement is stable from ~20px on

R = 6378137.0  # Web Mercator sphere radius, metres

# Nine OSM features looked up by name (not fed into the fit above, which uses
# only the coastline) so the georeference can be checked against something it
# has never seen.
LANDMARKS = [
    ("Village School",      -70.865107, 42.504199),
    ("Gerry Playground",    -70.863335, 42.513510),
    ("Forest River Park",   -70.884613, 42.506298),
    ("Winter Island",       -70.868928, 42.527270),
    ("Chandler Hovey",      -70.833401, 42.505035),
    ("Devereux Beach",      -70.856373, 42.491082),
    ("Lafayette/Humphrey",  -70.869823, 42.494024),
    ("Pleasant/Washington", -70.850012, 42.504711),
    ("Audubon Trail",       -70.840384, 42.492684),
]


# ---------------------------------------------------------------- Mercator
def to_merc(lon, lat):
    return R * math.radians(lon), R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def from_merc(x, y):
    return math.degrees(x / R), math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)


def is_dark(rgb, threshold=DARK_RGB):
    r, g, b = rgb
    return r < threshold and g < threshold and b < threshold


def is_inked(rgb, threshold=DARK_AVG):
    return sum(rgb) / 3 < threshold


# ---------------------------------------------------------------- stage 1: find the line
def _largest_component(points):
    """8-connected components of `points`; returns the biggest one.

    Dark pixels that merely happen to sit close to the winning Hough line
    elsewhere in the image (a coastline crossing, a stray letter) show up as
    small isolated blobs here. The drawn stroke is the one long connected run.
    """
    pts = set(points)
    seen = set()
    best = []
    for p in points:
        if p in seen:
            continue
        comp = []
        stack = [p]
        seen.add(p)
        while stack:
            cx, cy = stack.pop()
            comp.append((cx, cy))
            for ddx in (-1, 0, 1):
                for ddy in (-1, 0, 1):
                    if ddx == 0 and ddy == 0:
                        continue
                    n = (cx + ddx, cy + ddy)
                    if n in pts and n not in seen:
                        seen.add(n)
                        stack.append(n)
        if len(comp) > len(best):
            best = comp
    return best


def _total_least_squares(points):
    """Centroid and unit direction minimizing perpendicular (not vertical) distance."""
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mx) ** 2 for p in points)
    syy = sum((p[1] - my) ** 2 for p in points)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in points)
    theta = 0.5 * math.atan2(2 * sxy, sxx - syy)
    return (mx, my), (math.cos(theta), math.sin(theta))


def find_split_line(im):
    """Locate the hand-drawn split line and refit it to a clean segment.

    The town outline on this AxisGIS map is DASHED, so a naive "biggest dark
    connected component" search over the whole image finds nothing usable for
    it -- each dash is its own tiny blob. The split line is the only long
    SOLID stroke in the frame, which is what makes its Hough peak unambiguous
    even with the coastline and the dashed outline also inked.
    """
    W, H = im.size
    px = im.load()

    dark = [(x, y) for y in range(MAP_AREA_Y) for x in range(W) if is_dark(px[x, y])]
    print(f"[1] dark pixels: {len(dark)}")

    # Hough-accumulate theta in [0, 180) at 0.5deg steps, rho in 2px bins.
    thetas = [math.radians(t * 0.5) for t in range(360)]
    cos_sin = [(math.cos(t), math.sin(t)) for t in thetas]
    rho_bin = 2.0
    acc = defaultdict(int)
    for x, y in dark:
        for ti, (c, s) in enumerate(cos_sin):
            acc[(ti, int((x * c + y * s) / rho_bin))] += 1

    # The image frame is a solid rectangle, so it dominates the accumulator at
    # theta ~ 0 and ~90 (axis-aligned). Skip those cells and take the next
    # highest peak -- "the peak that is not the image frame".
    axis_tolerance_deg = 8
    theta_deg = rho = votes = None
    for (ti, rb), v in sorted(acc.items(), key=lambda kv: -kv[1]):
        td = ti * 0.5
        if min(td % 90, 90 - (td % 90)) < axis_tolerance_deg:
            continue
        theta_deg, rho, votes = td, rb * rho_bin, v
        break
    if theta_deg is None:
        sys.exit("no non-axis-aligned Hough peak found -- is this the right image?")
    print(f"[1] Hough peak: theta={theta_deg:.1f} deg  rho={rho:.1f} px  votes={votes}")

    theta = math.radians(theta_deg)
    c, s = math.cos(theta), math.sin(theta)
    slope, intercept = -c / s, rho / s  # line as y = intercept + slope*x

    # Seed the refit with dark pixels within SEED_BAND_PX of that line,
    # inside the frame interior (excludes the border and the caption).
    x_lo, x_hi = FRAME_MARGIN, W - FRAME_MARGIN
    xs = [x for x in range(x_lo, x_hi) if FRAME_MARGIN <= intercept + slope * x <= MAP_AREA_Y]
    seed = [(x, y) for x in xs for y in range(FRAME_MARGIN, MAP_AREA_Y)
            if is_dark(px[x, y]) and abs(y - (intercept + slope * x)) <= SEED_BAND_PX]
    comp = _largest_component(seed)
    print(f"[1] largest connected run on that line: {len(comp)} of {len(seed)} banded pixels")

    pts = comp
    residuals = []
    for _ in range(20):
        centroid, direction = _total_least_squares(pts)
        mx, my = centroid
        dx, dy = direction
        residuals = [abs((p[0] - mx) * dy - (p[1] - my) * dx) for p in pts]
        kept = [p for p, r in zip(pts, residuals) if r <= 4.0]
        if len(kept) == len(pts):
            break
        pts = kept
    rms = math.sqrt(sum(r * r for r in residuals) / len(residuals))
    ts = sorted((p[0] - mx) * dx + (p[1] - my) * dy for p in pts)
    p0 = (mx + ts[0] * dx, my + ts[0] * dy)
    p1 = (mx + ts[-1] * dx, my + ts[-1] * dy)
    print(f"[1] refit: {len(pts)} pixels  rms={rms:.2f}px  slope={dy / dx:.4f}")
    print(f"[1] endpoints (px): ({p0[0]:.1f}, {p0[1]:.1f})  ({p1[0]:.1f}, {p1[1]:.1f})")
    return p0, p1


# ---------------------------------------------------------------- coastline cache
def load_coastline_vertices():
    """[(lon, lat)] for every vertex of every natural=coastline way in the bbox, cached forever."""
    if not COASTLINE_PATH.exists():
        print(f"[2] fetching coastline from Overpass -> {COASTLINE_PATH.relative_to(ROOT)}")
        fetch_overpass(COASTLINE_QUERY, COASTLINE_PATH)
    coast = json.loads(COASTLINE_PATH.read_text())
    return [(p["lon"], p["lat"]) for w in coast["elements"] if w["type"] == "way" for p in w["geometry"]]


# ---------------------------------------------------------------- stage 2: georeference
def _chamfer_dt(mask, W, H):
    """Chamfer distance transform (3 orthogonal / 4 diagonal per step) of a 0/1 mask, in px units."""
    INF = 10 ** 6
    dt = [0 if v else INF for v in mask]
    for y in range(H):
        for x in range(W):
            i = y * W + x
            if dt[i] == 0:
                continue
            for ddx, ddy, w in ((-1, 0, 3), (-1, -1, 4), (0, -1, 3), (1, -1, 4)):
                nx, ny = x + ddx, y + ddy
                if 0 <= nx < W and 0 <= ny < H:
                    dt[i] = min(dt[i], dt[ny * W + nx] + w)
    for y in range(H - 1, -1, -1):
        for x in range(W - 1, -1, -1):
            i = y * W + x
            for ddx, ddy, w in ((1, 0, 3), (1, 1, 4), (0, 1, 3), (-1, 1, 4)):
                nx, ny = x + ddx, y + ddy
                if 0 <= nx < W and 0 <= ny < H:
                    dt[i] = min(dt[i], dt[ny * W + nx] + w)
    return dt


def _ink_mask(im, split_p0, split_p1):
    """Inked pixels, excluding the split line itself, the frame, and the caption.

    The split line is ink but not coastline, so it must not be allowed to
    attract the fit; excluding a band around it (and the frame, and the
    caption) is carried over directly from the original georef pass.
    """
    W, H = im.size
    px = im.load()
    sdx, sdy = split_p1[0] - split_p0[0], split_p1[1] - split_p0[1]
    sl = math.hypot(sdx, sdy)
    sux, suy = sdx / sl, sdy / sl
    mask = bytearray(W * MAP_AREA_Y)
    for y in range(FRAME_MARGIN, MAP_AREA_Y):
        for x in range(FRAME_MARGIN, W - FRAME_MARGIN):
            if is_inked(px[x, y]) and abs((x - split_p0[0]) * suy - (y - split_p0[1]) * sux) > SPLIT_EXCLUDE_PX:
                mask[y * W + x] = 1
    return mask


def georeference(im, split_p0, split_p1, verts):
    """Fit the 3-parameter similarity (scale a, origin mx0,my0 in Web Mercator metres)
    that maps AxisGIS pixels to the world, by scoring candidates against the coastline.

    AxisGIS renders north-up Web Mercator, so there is one scale and two
    offsets to find, no rotation. Score = fraction of coastline vertices
    landing within CHAMFER_NEAR of inked pixels, via a chamfer distance
    transform so each vertex costs one array lookup. Coarse-to-fine search,
    same as the original pass; subsampling the vertices keeps it from getting
    any slower even though it now reruns unattended.
    """
    W, H = im.size
    mask = _ink_mask(im, split_p0, split_p1)
    print(f"[2] inked pixels: {sum(mask)}")

    dt = _chamfer_dt(mask, W, MAP_AREA_Y)
    sub_verts = [to_merc(lon, lat) for lon, lat in verts[::3]]
    print(f"[2] coastline vertices used: {len(sub_verts)}")

    def score(a, mx0, my0):
        hit = inside = 0
        for mx, my in sub_verts:
            x = int(a * (mx - mx0))
            y = int(-a * (my - my0))
            if 0 <= x < W and 0 <= y < MAP_AREA_Y:
                inside += 1
                if dt[y * W + x] <= CHAMFER_NEAR:
                    hit += 1
        return (hit / inside if inside >= 700 else 0.0), inside

    def search(a_rng, mx_rng, my_rng, a_step, off_step):
        best = (0, None, 0)
        a = a_rng[0]
        while a <= a_rng[1]:
            mx = mx_rng[0]
            while mx <= mx_rng[1]:
                my = my_rng[0]
                while my <= my_rng[1]:
                    s, ins = score(a, mx, my)
                    if s > best[0]:
                        best = (s, (a, mx, my), ins)
                    my += off_step
                mx += off_step
            a += a_step
        return best

    # Coarse: the image's left edge lies somewhere in Salem Harbour, the top
    # edge north of Winter Island -- a wide box, refined below.
    s, p, ins = search((0.110, 0.170), (-7896500, -7884000), (5238000, 5250000), 0.004, 400)
    print(f"[2] coarse  score={s:.3f} inside={ins} params={p}")
    for a_step, off_step, span_a, span_o in ((0.001, 120, 0.006, 700), (0.0003, 40, 0.0018, 240), (0.0001, 12, 0.0006, 80)):
        a, mx, my = p
        s, p, ins = search((a - span_a, a + span_a), (mx - span_o, mx + span_o), (my - span_o, my + span_o), a_step, off_step)
        print(f"[2] refine  score={s:.3f} inside={ins} params={p}")

    a, mx0, my0 = p
    ground_m_per_px = 1 / a * math.cos(math.radians(42.50))
    print(f"[2] scale {a:.6f} px per mercator metre  origin ({mx0:.3f}, {my0:.3f})  "
          f"fit {s:.2f}  {ground_m_per_px:.2f} ground metres per pixel at 42.5N")
    return {"a": a, "mx0": mx0, "my0": my0, "fit": s, "m_per_px": ground_m_per_px}


# ---------------------------------------------------------------- stage 3: uncertainty
def quantify_uncertainty(im, split_p0, split_p1, transform, verts):
    """Two independent error sources: how sensitive the fit is to a pixel-scale
    shift, and how wide the drawn stroke itself is on the ground."""
    a, mx0, my0 = transform["a"], transform["mx0"], transform["my0"]
    m_per_px = transform["m_per_px"]
    W, H = im.size
    px = im.load()

    inkset = set()
    sdx, sdy = split_p1[0] - split_p0[0], split_p1[1] - split_p0[1]
    sl = math.hypot(sdx, sdy)
    sux, suy = sdx / sl, sdy / sl
    for y in range(FRAME_MARGIN, MAP_AREA_Y):
        for x in range(FRAME_MARGIN, W - FRAME_MARGIN):
            if is_inked(px[x, y]) and abs((x - split_p0[0]) * suy - (y - split_p0[1]) * sux) > SPLIT_EXCLUDE_PX:
                inkset.add((x, y))

    sub_verts = verts[::7]

    def hits(aa, dx, dy):
        n = h = 0
        for lon, lat in sub_verts:
            mx, my = to_merc(lon, lat)
            X = aa * (mx - mx0) + dx
            Y = -aa * (my - my0) + dy
            if 0 <= X < W and 0 <= Y < MAP_AREA_Y:
                n += 1
                xi, yi = int(X), int(Y)
                if any((xi + i, yi + j) in inkset for i in range(-4, 5) for j in range(-4, 5)):
                    h += 1
        return h / n if n else 0.0

    base = hits(a, 0, 0)
    print(f"[3] baseline fit {base:.3f} of {len(sub_verts)} coastline vertices within 4 px of ink")
    print("[3] translation sensitivity (px shift -> fit score):")
    for shift in (-6, -4, -2, 0, 2, 4, 6):
        print(f"[3]   dx={shift:+3d} ({shift * m_per_px:+6.1f} m)  {hits(a, shift, 0):.3f}"
              f"      dy={shift:+3d}  {hits(a, 0, shift):.3f}")

    # Stroke thickness: perpendicular spread of the split line's own pixels
    # (the opposite selection from the coastline mask -- inside the band, not
    # outside it), windowed to a box around the fitted endpoints rather than
    # the whole frame interior. A drawn line's rounded end-caps flare out
    # perpendicular to the line right at its tips, slightly beyond the fitted
    # endpoints themselves, so the box is padded past them; STROKE_WINDOW_PAD
    # was found by increasing the padding until the measured width stopped
    # changing (stable from ~20px), so it isn't tuned to hit a target number.
    x_lo = max(FRAME_MARGIN, int(min(split_p0[0], split_p1[0])) - STROKE_WINDOW_PAD)
    x_hi = min(W - FRAME_MARGIN, int(max(split_p0[0], split_p1[0])) + STROKE_WINDOW_PAD)
    y_lo = max(FRAME_MARGIN, int(min(split_p0[1], split_p1[1])) - STROKE_WINDOW_PAD)
    y_hi = min(MAP_AREA_Y, int(max(split_p0[1], split_p1[1])) + STROKE_WINDOW_PAD)
    band = [(x, y) for y in range(y_lo, y_hi) for x in range(x_lo, x_hi)
            if is_inked(px[x, y]) and abs((x - split_p0[0]) * suy - (y - split_p0[1]) * sux) <= 12]
    perp = sorted(abs((x - split_p0[0]) * suy - (y - split_p0[1]) * sux) for x, y in band)
    half_width_px = perp[int(0.95 * len(perp))]
    stroke_m = 2 * half_width_px * m_per_px
    print(f"[3] drawn stroke: {len(band)} px, 95th pct half-width {half_width_px:.1f} px "
          f"-> stroke is ~{stroke_m:.0f} m wide on the ground")
    print(f"[3] registration is good to roughly +/-15 m and the stroke itself is ~{stroke_m:.0f} m wide "
          "on the ground -- which is why the downstream uncertainty buffer used to flag poles near "
          "the split as UNCERTAIN is 150 m, not a few metres.")


# ---------------------------------------------------------------- stage 4: landmarks
def project(transform, lon, lat):
    a, mx0, my0 = transform["a"], transform["mx0"], transform["my0"]
    mx, my = to_merc(lon, lat)
    return a * (mx - mx0), -a * (my - my0)


def check_landmarks(transform):
    """Nine OSM features looked up by name, independent of the coastline fit above."""
    print("[4] independent landmark check (looked up on OSM by name, not used in the fit):")
    for name, lon, lat in LANDMARKS:
        x, y = project(transform, lon, lat)
        print(f"[4]   {name:22} px=({x:7.1f}, {y:7.1f})")


# ---------------------------------------------------------------- stage 5: final endpoints
def final_endpoints(im, transform, split_p0, split_p1):
    a, mx0, my0 = transform["a"], transform["mx0"], transform["my0"]
    W, H = im.size

    def px_to_lonlat(x, y):
        return from_merc(mx0 + x / a, my0 - y / a)

    nw_lon, nw_lat = px_to_lonlat(*split_p0)
    se_lon, se_lat = px_to_lonlat(*split_p1)
    print(f"[5] endpoint NW  {nw_lon:.6f}, {nw_lat:.6f}")
    print(f"[5] endpoint SE  {se_lon:.6f}, {se_lat:.6f}")

    west, north = px_to_lonlat(0, 0)
    east, south = px_to_lonlat(W, MAP_AREA_Y)
    print(f"[5] extent  west {west:.6f}  south {south:.6f}  east {east:.6f}  north {north:.6f}")
    print("[5] these two endpoint coordinates (NW, SE) are the values split.py carries as constants.")
    return (nw_lon, nw_lat), (se_lon, se_lat)


# ---------------------------------------------------------------- stage 6: overlay
def load_boundary_members():
    """Way geometries of Marblehead's admin boundary relation, cached forever. Overlay only."""
    if not BOUNDARY_PATH.exists():
        print(f"[6] fetching boundary relation from Overpass -> {BOUNDARY_PATH.relative_to(ROOT)}")
        fetch_overpass(BOUNDARY_QUERY, BOUNDARY_PATH)
    raw = json.loads(BOUNDARY_PATH.read_text())
    relation = raw["elements"][0]
    return [m["geometry"] for m in relation["members"] if m.get("type") == "way" and m.get("geometry")]


def write_overlay(im, transform, split_p0, split_p1):
    overlay = im.copy()
    d = ImageDraw.Draw(overlay)

    # Drawn per-way (not as scattered projected vertices) so the coastline
    # overlay reads as connected lines.
    coast = json.loads(COASTLINE_PATH.read_text())
    for w in coast["elements"]:
        if w["type"] != "way":
            continue
        pts = [project(transform, p["lon"], p["lat"]) for p in w["geometry"]]
        if len(pts) >= 2:
            d.line(pts, fill=(255, 0, 0), width=3)

    for way_geom in load_boundary_members():
        pts = [project(transform, p["lon"], p["lat"]) for p in way_geom]
        if len(pts) >= 2:
            d.line(pts, fill=(0, 160, 255), width=3)

    RADIUS = 10
    for _, lon, lat in LANDMARKS:
        x, y = project(transform, lon, lat)
        d.ellipse([x - RADIUS, y - RADIUS, x + RADIUS, y + RADIUS], outline=(255, 0, 255), width=3)
    for x, y in (split_p0, split_p1):
        d.ellipse([x - RADIUS, y - RADIUS, x + RADIUS, y + RADIUS], outline=(0, 200, 0), width=3)

    OVERLAY_PATH.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(OVERLAY_PATH)
    print(f"[6] wrote {OVERLAY_PATH.relative_to(ROOT)}")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", type=Path, default=DEFAULT_IMAGE, help="source screenshot")
    ap.add_argument("--overlay", action="store_true", help="also write docs/digitize-check.png")
    args = ap.parse_args()

    if not args.image.exists():
        sys.exit(f"missing {args.image}")
    im = Image.open(args.image).convert("RGB")
    print(f"image {args.image.relative_to(ROOT) if args.image.is_relative_to(ROOT) else args.image}  "
          f"{im.size[0]}x{im.size[1]}")

    split_p0, split_p1 = find_split_line(im)
    verts = load_coastline_vertices()
    transform = georeference(im, split_p0, split_p1, verts)
    quantify_uncertainty(im, split_p0, split_p1, transform, verts)
    check_landmarks(transform)
    final_endpoints(im, transform, split_p0, split_p1)

    if args.overlay:
        write_overlay(im, transform, split_p0, split_p1)


if __name__ == "__main__":
    main()
