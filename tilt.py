"""Apparent tilt of a pole in a photo, from the Mapillary detection polygon.

    apparent_tilt(polygon_norm, width, height) -> signed degrees from vertical, or None

The polygon is the outline Mapillary drew around the pole. Its medial axis (the
midpoint of the outline on each of 12 horizontal scanlines) is fitted with a line;
the angle of that line from the image vertical is the apparent tilt. Positive means
the top is to the right of the base in the photo.

This is not a measured lean. Camera roll, perspective, and the coarseness of the
outline all add noise, and a lean toward or away from the camera is invisible.
`--calibrate` measures that noise against the classifier's own lean calls so the
page can state it:

    uv run python3 tilt.py --town "Greenpoint, Brooklyn, New York" --calibrate

reads data/fetch/<slug>/observations.jsonl and data/classify/<slug>/classifications.jsonl
(cache only, no API calls) and writes data/tilt/<slug>/calibration.json.
"""
import argparse
import json
import math
import statistics
import sys
from collections import defaultdict

from coverage import DATA, ROOT, slugify

ROWS = 12          # scanlines across the polygon's height
MIN_PX_H = 20      # polygons shorter than this carry no usable angle
MIN_ROWS = 5       # scanlines that must cross the polygon


def apparent_tilt(polygon_norm, width, height):
    """Signed degrees from vertical for the largest ring, or None if the outline is too small."""
    if not polygon_norm or not width or not height:
        return None
    ring = max(polygon_norm, key=len)
    pts = [(x * width, y * height) for x, y in ring]
    if len(pts) < 3:
        return None
    ys = [y for _, y in pts]
    y0, y1 = min(ys), max(ys)
    if y1 - y0 < MIN_PX_H:
        return None
    n = len(pts)
    rows = []
    for k in range(ROWS):
        y = y0 + (y1 - y0) * (k + 0.5) / ROWS
        xs = []
        for i in range(n):
            (xa, ya), (xb, yb) = pts[i], pts[(i + 1) % n]
            if (ya <= y < yb) or (yb <= y < ya):
                xs.append(xa + (xb - xa) * (y - ya) / (yb - ya))
        if len(xs) >= 2:
            rows.append((y, (min(xs) + max(xs)) / 2))
    if len(rows) < MIN_ROWS:
        return None
    my = sum(y for y, _ in rows) / len(rows)
    mx = sum(x for _, x in rows) / len(rows)
    den = sum((y - my) ** 2 for y, _ in rows)
    if den == 0:
        return None
    slope = sum((y - my) * (x - mx) for y, x in rows) / den  # dx per dy, image y grows downward
    return round(-math.degrees(math.atan(slope)), 1)  # top right of base -> positive


def percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    k = min(len(sorted_vals) - 1, int(round(p * (len(sorted_vals) - 1))))
    return sorted_vals[k]


def calibrate(slug):
    obs_path = DATA / "fetch" / slug / "observations.jsonl"
    cls_path = DATA / "classify" / slug / "classifications.jsonl"
    for p in (obs_path, cls_path):
        if not p.exists():
            sys.exit(f"missing {p}")
    lean = {}
    for line in cls_path.open():
        d = json.loads(line)
        c = d.get("classification") or {}
        if c.get("lean_severity"):
            lean[str(d["detection_id"])] = c["lean_severity"]
    groups = defaultdict(list)
    n_obs = n_tilt = 0
    for line in obs_path.open():
        o = json.loads(line)
        n_obs += 1
        t = apparent_tilt(o.get("polygon_norm"), o.get("width"), o.get("height"))
        if t is None:
            continue
        n_tilt += 1
        call = lean.get(str(o["detection_id"]))
        if call:
            groups[(call, "pano" if o.get("is_pano") else "flat")].append(abs(t))
            groups[(call, "all")].append(abs(t))
    table = {}
    for (call, kind), vals in sorted(groups.items()):
        vals.sort()
        table.setdefault(call, {})[kind] = {"n": len(vals), "median": round(statistics.median(vals), 1),
                                            "p75": round(percentile(vals, 0.75), 1), "p90": round(percentile(vals, 0.9), 1)}
    out = {"slug": slug, "method": f"medial axis of the detection outline, {ROWS} scanlines, least squares; abs degrees from vertical",
           "observations": n_obs, "with_tilt": n_tilt, "by_model_lean_call": table,
           "source": {"observations": str(obs_path.relative_to(ROOT)), "classifications": str(cls_path.relative_to(ROOT))}}
    out_dir = DATA / "tilt" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "calibration.json").write_text(json.dumps(out, indent=2))
    print(f"{n_tilt} of {n_obs} observations have a tilt")
    for call in ("none", "slight", "moderate", "severe", "unclear"):
        for kind in ("flat", "pano", "all"):
            t = table.get(call, {}).get(kind)
            if t:
                print(f"  {call:9s} {kind:5s} n={t['n']:5d}  median {t['median']:5.1f}  p75 {t['p75']:5.1f}  p90 {t['p90']:5.1f}")
    print(f"-> {out_dir.relative_to(ROOT)}/calibration.json")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--town", required=True)
    ap.add_argument("--calibrate", action="store_true", help="write data/tilt/<slug>/calibration.json from cache")
    args = ap.parse_args()
    if not args.calibrate:
        ap.error("nothing to do: pass --calibrate")
    calibrate(slugify(args.town))


if __name__ == "__main__":
    main()
