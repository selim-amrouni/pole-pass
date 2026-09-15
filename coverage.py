#!/usr/bin/env python3
"""Step 1: how much Mapillary coverage does a town actually have?

Usage:
  python3 coverage.py --town "Reading, Massachusetts" [--town ...]
  python3 coverage.py --town "..." --dry-run          # geocode + tile count only
  python3 coverage.py --bbox MINLON,MINLAT,MAXLON,MAXLAT --name custom

Source: Mapillary vector tiles at zoom 14 (tiles.mapillary.com). The Graph API
bbox search is NOT exhaustive (same box returned 42..1781 images depending on
limit, no pagination cursor), so it is not used here. Raw tiles are cached under
data/tiles/ and reused by fetch.py. Reruns hit no API. Stdlib only.

Outputs per town under data/coverage/<slug>/:
  summary.json          counts, densities, date range, attribution
  images.jsonl          one row per image point inside the bbox
  map_features.jsonl    one row per map feature point inside the bbox
"""
import argparse
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import mvt

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
TILES = "https://tiles.mapillary.com/maps/vtp"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "pole-condition-pass/0.1 (coverage check)"
ZOOM = 14  # image and map-feature points are only present at z14
ATTRIBUTION = ("Imagery and map features: Mapillary, CC BY-SA 4.0. "
               "Geocoding: OpenStreetMap contributors, ODbL.")

# object 'value' strings Mapillary uses for pole-like map features
POLE_VALUES = {
    "object--support--utility-pole",
    "object--support--pole",
    "object--street-light",
}


# ---------------------------------------------------------------- helpers
def load_env(path=ROOT / ".env"):
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def http_get(url, retries=5):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                wait = 2 ** attempt
                print(f"  HTTP {e.code}, retrying in {wait}s: {body}", file=sys.stderr)
                time.sleep(wait)
                continue
            raise SystemExit(f"HTTP {e.code} for {url.split('?')[0]}: {body}")
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise SystemExit(f"network error: {e}")


# ---------------------------------------------------------------- geocode
def geocode(town):
    path = DATA / "geocode" / f"{slugify(town)}.json"
    if path.exists():
        hit = json.loads(path.read_text())
    else:
        q = urllib.parse.urlencode({"q": town, "format": "jsonv2", "limit": 1})
        res = json.loads(http_get(f"{NOMINATIM}?{q}"))
        if not res:
            raise SystemExit(f"Nominatim found nothing for {town!r}")
        hit = res[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(hit))
        time.sleep(1)  # Nominatim usage policy: max 1 req/s
    s, n, w, e = (float(x) for x in hit["boundingbox"])  # minlat, maxlat, minlon, maxlon
    return {"name": hit["display_name"], "osm_type": hit["osm_type"], "osm_id": hit["osm_id"],
            "bbox": (w, s, e, n)}


# ---------------------------------------------------------------- tiles
def bbox_area_km2(bbox):
    w, s, e, n = bbox
    mid_lat = math.radians((s + n) / 2)
    return (e - w) * 111.32 * math.cos(mid_lat) * (n - s) * 110.57


def fetch_tile(tileset, z, x, y, token):
    """Raw MVT bytes, cached at data/tiles/<tileset>/<z>/<x>/<y>.mvt."""
    path = DATA / "tiles" / tileset / str(z) / str(x) / f"{y}.mvt"
    if path.exists():
        return path.read_bytes()
    if not token:
        raise SystemExit("MAPILLARY_TOKEN missing from .env and tile not cached. See .env.example.")
    raw = http_get(f"{TILES}/{tileset}/2/{z}/{x}/{y}?access_token={urllib.parse.quote(token)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def points_in_bbox(tileset, layer, bbox, token, progress=""):
    """All point features of `layer` whose location falls inside bbox, deduped by id."""
    w, s, e, n = bbox
    found = {}
    tl = mvt.tiles_covering(bbox, ZOOM)
    for k, (z, x, y) in enumerate(tl, 1):
        feats = mvt.decode(fetch_tile(tileset, z, x, y, token), z, x, y).get(layer, [])
        for f in feats:
            if f["type"] != "point" or not f["geom"]:
                continue
            lon, lat = f["geom"][0][0]
            if w <= lon <= e and s <= lat <= n:
                fid = f["props"].get("id", f["id"])
                found[fid] = (lon, lat, f["props"])
        print(f"\r  {progress} tile {k}/{len(tl)}  {layer}s {len(found)}", end="", flush=True)
    print()
    return found


# ---------------------------------------------------------------- main
def ms_to_date(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def write_jsonl(path, rows):
    with path.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def run(name, bbox, token, dry_run):
    slug = slugify(name)
    out_dir = DATA / "coverage" / slug
    area = bbox_area_km2(bbox)
    n_tiles = len(mvt.tiles_covering(bbox, ZOOM))
    print(f"\n== {name}")
    print(f"bbox   {bbox[0]:.4f},{bbox[1]:.4f},{bbox[2]:.4f},{bbox[3]:.4f}   area {area:.1f} km2   z{ZOOM} tiles {n_tiles}")
    if dry_run:
        return None

    images = points_in_bbox("mly1_public", "image", bbox, token, "images  ")
    features = points_in_bbox("mly_map_feature_point", "point", bbox, token, "features")

    caps = sorted(p["captured_at"] for _, _, p in images.values() if p.get("captured_at"))
    years = Counter(ms_to_date(c)[:4] for c in caps)
    pano = sum(1 for _, _, p in images.values() if p.get("is_pano"))
    seqs = len({p.get("sequence_id") for _, _, p in images.values()})
    obj = Counter(p.get("value", "?") for _, _, p in features.values())
    pole_feats = sum(n for v, n in obj.items() if v in POLE_VALUES)

    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "images.jsonl", (
        {"id": i, "lon": round(lon, 7), "lat": round(lat, 7), "captured_at": p.get("captured_at"),
         "is_pano": p.get("is_pano"), "sequence_id": p.get("sequence_id"), "compass_angle": p.get("compass_angle")}
        for i, (lon, lat, p) in images.items()))
    write_jsonl(out_dir / "map_features.jsonl", (
        {"id": i, "lon": round(lon, 7), "lat": round(lat, 7), "value": p.get("value"),
         "first_seen_at": p.get("first_seen_at"), "last_seen_at": p.get("last_seen_at")}
        for i, (lon, lat, p) in features.items()))

    summary = {
        "name": name, "slug": slug, "bbox": bbox, "area_km2": round(area, 2), "z14_tiles": n_tiles,
        "images": len(images), "images_per_km2": round(len(images) / area, 1),
        "pano_images": pano, "sequences": seqs,
        "capture_first": ms_to_date(caps[0]) if caps else None,
        "capture_last": ms_to_date(caps[-1]) if caps else None,
        "captures_by_year": dict(sorted(years.items())),
        "map_features": len(features), "features_per_km2": round(len(features) / area, 1),
        "pole_like_features": pole_feats, "pole_like_per_km2": round(pole_feats / area, 1),
        "features_by_value": dict(obj.most_common()),
        "source": f"Mapillary vector tiles z{ZOOM}: mly1_public/image, mly_map_feature_point/point",
        "attribution": ATTRIBUTION,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    print(f"images         {len(images):>7}   {summary['images_per_km2']:>8} / km2   {pano} pano, {seqs} sequences")
    print(f"captured       {summary['capture_first']} -> {summary['capture_last']}   {summary['captures_by_year']}")
    print(f"map_features   {len(features):>7}   {summary['features_per_km2']:>8} / km2")
    print(f"pole-like      {pole_feats:>7}   {summary['pole_like_per_km2']:>8} / km2   "
          + ", ".join(f"{v.split('--')[-1]}={obj[v]}" for v in sorted(POLE_VALUES) if obj.get(v)))
    print(f"top values     " + ", ".join(f"{v.split('--')[-1]}={n}" for v, n in obj.most_common(6)))
    print(f"files          {out_dir.relative_to(ROOT)}/{{summary.json,images.jsonl,map_features.jsonl}}")
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--town", action="append", default=[], help="place name for Nominatim; repeatable")
    ap.add_argument("--bbox", help="minlon,minlat,maxlon,maxlat")
    ap.add_argument("--name", help="label for --bbox")
    ap.add_argument("--dry-run", action="store_true", help="geocode and count tiles, no Mapillary calls")
    args = ap.parse_args()
    if not args.town and not args.bbox:
        ap.error("give --town or --bbox")

    token = load_env().get("MAPILLARY_TOKEN", "")
    targets = []
    for town in args.town:
        g = geocode(town)
        print(f"geocoded {town!r} -> {g['name']} ({g['osm_type']} {g['osm_id']})")
        targets.append((town, g["bbox"]))
    if args.bbox:
        vals = tuple(float(v) for v in args.bbox.split(","))
        if len(vals) != 4:
            ap.error("--bbox needs four numbers")
        targets.append((args.name or "bbox", vals))

    results = [run(name, bbox, token, args.dry_run) for name, bbox in targets]
    if not args.dry_run and len(results) > 1:
        print("\n== comparison")
        print(f"{'town':30} {'km2':>6} {'images':>8} {'img/km2':>8} {'features':>8} {'poles':>6} {'poles/km2':>9} {'last capture':>12}")
        for s in results:
            print(f"{s['name'][:30]:30} {s['area_km2']:>6} {s['images']:>8} {s['images_per_km2']:>8} "
                  f"{s['map_features']:>8} {s['pole_like_features']:>6} {s['pole_like_per_km2']:>9} {str(s['capture_last']):>12}")


if __name__ == "__main__":
    main()
