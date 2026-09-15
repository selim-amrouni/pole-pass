#!/usr/bin/env python3
"""Step 2: for each pole-like map feature, fetch its detections, pick the frames
where the pole appears largest, and download those thumbnails.

Usage:
  uv run python3 fetch.py --town "Greenpoint, Brooklyn, New York" [--limit 20] [--frames 2]

Reads  data/coverage/<slug>/map_features.jsonl   (from coverage.py)
Writes data/fetch/<slug>/features/<fid>.json      raw Graph API map_feature (+detections)
       data/fetch/<slug>/images/<iid>.json        raw Graph API image metadata
       data/fetch/<slug>/obs/<fid>.json           observation rows for that feature
       data/fetch/<slug>/observations.jsonl       all rows, regenerated each run
       data/thumbs/<iid>.jpg                      thumbnail (original res for panos, 2048 otherwise)

Graph API is called per entity id only. Every call is cached; reruns hit nothing.
"""
import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import median

import mvt
from coverage import DATA, ROOT, load_env, slugify

GRAPH = "https://graph.mapillary.com"
USER_AGENT = "pole-condition-pass/0.1 (fetch)"
FEATURE_FIELDS = "id,object_value,geometry,aligned_direction,first_seen_at,last_seen_at,detections.id,detections.value,detections.geometry,detections.image"
IMAGE_FIELDS = "id,captured_at,compass_angle,geometry,computed_geometry,height,width,is_pano,sequence,creator,thumb_2048_url,thumb_original_url"
DEFAULT_VALUES = ["object--support--utility-pole"]


def http(url, headers=None, retries=5, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                raw = r.read()
                return raw if binary else json.loads(raw)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:200]
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"HTTP {e.code} {url.split('?')[0]}: {body}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"network: {e}")


def graph(entity_id, fields, token):
    return http(f"{GRAPH}/{entity_id}?{urllib.parse.urlencode({'fields': fields})}",
                headers={"Authorization": f"OAuth {token}"})


def cached(path, fetch):
    if path.exists():
        return json.loads(path.read_text())
    obj = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))
    return obj


def haversine_m(lon1, lat1, lon2, lat2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def polygon_bbox(polys):
    xs = [x for p in polys for x, _ in p]
    ys = [y for p in polys for _, y in p]
    return (min(xs), min(ys), max(xs), max(ys))


def process_feature(feat, slug, token, values, frames, thumbs_dir):
    """Return observation rows for one map feature (cached per feature)."""
    fid = str(feat["id"])
    obs_path = DATA / "fetch" / slug / "obs" / f"{fid}.json"
    if obs_path.exists():
        return json.loads(obs_path.read_text()), False

    mf = cached(DATA / "fetch" / slug / "features" / f"{fid}.json",
                lambda: graph(fid, FEATURE_FIELDS, token))
    dets = [d for d in (mf.get("detections") or {}).get("data", []) if d.get("value") in values and d.get("geometry")]

    # rank detections by apparent size (normalized bbox area); keep the largest `frames`
    ranked = []
    for d in dets:
        polys = mvt.decode_detection(d["geometry"])
        if not polys:
            continue
        x0, y0, x1, y1 = polygon_bbox(polys)
        ranked.append(((x1 - x0) * (y1 - y0), d, polys, (x0, y0, x1, y1)))
    ranked.sort(key=lambda r: r[0], reverse=True)

    rows = []
    for area, d, polys, bbox in ranked[:frames]:
        iid = str(d["image"]["id"])
        im = cached(DATA / "fetch" / slug / "images" / f"{iid}.json",
                    lambda: graph(iid, IMAGE_FIELDS, token))
        # panos: original resolution, since a pole is a thin sliver of the frame; flat: 2048
        url = im.get("thumb_original_url") if im.get("is_pano") else im.get("thumb_2048_url")
        url = url or im.get("thumb_2048_url") or im.get("thumb_original_url")
        thumb = thumbs_dir / f"{iid}.jpg"
        if not thumb.exists() and url:
            thumb.parent.mkdir(parents=True, exist_ok=True)
            thumb.write_bytes(http(url, binary=True))
        ilon, ilat = (im.get("computed_geometry") or im.get("geometry"))["coordinates"]
        flon, flat = mf["geometry"]["coordinates"]
        rows.append({
            "feature_id": fid, "value": mf.get("object_value"), "lon": flon, "lat": flat,
            "aligned_direction": mf.get("aligned_direction"),
            "first_seen_at": mf.get("first_seen_at"), "last_seen_at": mf.get("last_seen_at"),
            "detection_id": str(d["id"]), "image_id": iid,
            "image_lon": ilon, "image_lat": ilat, "distance_m": round(haversine_m(flon, flat, ilon, ilat), 1),
            "captured_at": im.get("captured_at"), "compass_angle": im.get("compass_angle"),
            "is_pano": bool(im.get("is_pano")), "width": im.get("width"), "height": im.get("height"),
            "sequence": im.get("sequence"), "creator": (im.get("creator") or {}).get("username"),
            "bbox_norm": [round(v, 5) for v in bbox], "polygon_norm": [[(round(x, 5), round(y, 5)) for x, y in p] for p in polys],
            "n_pole_detections": len(dets), "thumb": str(thumb.relative_to(ROOT)) if thumb.exists() else None,
            "mapillary_url": f"https://www.mapillary.com/app/?pKey={iid}&focus=photo",
        })
    obs_path.parent.mkdir(parents=True, exist_ok=True)
    obs_path.write_text(json.dumps(rows))
    return rows, True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--town", required=True)
    ap.add_argument("--values", nargs="+", default=DEFAULT_VALUES, help="map feature object values to include")
    ap.add_argument("--frames", type=int, default=2, help="frames per feature, largest apparent pole first")
    ap.add_argument("--limit", type=int, help="only the first N features (iteration)")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    slug = slugify(args.town)
    token = load_env().get("MAPILLARY_TOKEN", "")
    src = DATA / "coverage" / slug / "map_features.jsonl"
    if not src.exists():
        sys.exit(f"run coverage.py first, missing {src}")
    feats = [json.loads(l) for l in src.open()]
    feats = [f for f in feats if f["value"] in args.values]
    feats.sort(key=lambda f: f["id"])  # deterministic order so --limit is stable
    if args.limit:
        feats = feats[:args.limit]
    thumbs_dir = DATA / "thumbs"
    print(f"{slug}: {len(feats)} features with values {args.values}, {args.frames} frames each")

    all_rows, fetched, errors = [], 0, []
    t0 = time.time()
    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(process_feature, f, slug, token, set(args.values), args.frames, thumbs_dir): f for f in feats}
        for k, fut in enumerate(as_completed(futs), 1):
            try:
                rows, was_fetched = fut.result()
                all_rows.extend(rows)
                fetched += was_fetched
            except Exception as e:  # keep going, report at the end
                errors.append((futs[fut]["id"], str(e)))
            if k % 25 == 0 or k == len(feats):
                print(f"\r  {k}/{len(feats)} features, {len(all_rows)} observations, {fetched} newly fetched, {len(errors)} errors, {time.time()-t0:.0f}s", end="", flush=True)
    print()

    out = DATA / "fetch" / slug / "observations.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        for r in sorted(all_rows, key=lambda r: (r["feature_id"], r["image_id"])):
            fh.write(json.dumps(r) + "\n")

    # summary
    with_obs = {r["feature_id"] for r in all_rows}
    heights_px = [ (r["bbox_norm"][3] - r["bbox_norm"][1]) * (r["height"] or 0) for r in all_rows if r["height"]]
    panos = sum(1 for r in all_rows if r["is_pano"])
    thumbs = [r["thumb"] for r in all_rows if r["thumb"]]
    mb = sum((ROOT / t).stat().st_size for t in set(thumbs)) / 1e6
    print(f"features with >=1 observation  {len(with_obs)}/{len(feats)}")
    print(f"observations                   {len(all_rows)}   ({panos} from panos)")
    print(f"pole height in source pixels   median {median(heights_px):.0f}px, "
          f"{sum(1 for h in heights_px if h < 60)} under 60px" if heights_px else "no observations")
    print(f"distance image->pole           median {median(r['distance_m'] for r in all_rows):.1f} m" if all_rows else "")
    print(f"thumbnails                     {len(set(thumbs))} files, {mb:.0f} MB")
    print(f"output                         {out.relative_to(ROOT)}")
    if errors:
        print(f"errors ({len(errors)}), first 5:")
        for fid, e in errors[:5]:
            print(f"  {fid}: {e}")


if __name__ == "__main__":
    main()
