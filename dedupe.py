#!/usr/bin/env python3
"""Step 4: merge per-frame observations into pole records.

Usage:
  uv run python3 dedupe.py --town "Greenpoint, Brooklyn, New York" [--radius 8]

Reads  data/classify/<slug>/classifications.jsonl
Writes data/poles/<slug>/poles.jsonl      one row per pole record
       data/poles/<slug>/summary.json     counts the report cites

Grouping (estimates, not verified):
  1. Frames that Mapillary attributes to the same map feature merge first.
  2. Features whose points are within --radius meters merge (single linkage on
     a grid). Mapillary sometimes emits two features for one pole; the radius
     can also merge distinct nearby objects or leave duplicates.

Per field: majority vote over classified frames, ties toward the more
conservative value. Exact vote counts are kept per field. Frames from one
drive are correlated, so several frames are not independent assessments.

Condition flag predicate (shared with web/predicates.js, keep in sync):
  lean in {moderate, severe} -> "lean"; crossarm == damaged -> "crossarm";
  vegetation == touching -> "vegetation".
"""
import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from coverage import DATA, ROOT, slugify
from tilt import apparent_tilt

VOTE_FIELDS = ["pole_present", "pole_type", "material", "lean_severity", "crossarm_condition",
               "transformer_present", "vegetation_contact", "attachment_count"]
TIE_ORDER = {  # index 0 wins ties (conservative)
    "lean_severity": ["unclear", "none", "slight", "moderate", "severe"],
    "crossarm_condition": ["unclear", "none_visible", "intact", "damaged"],
    "vegetation_contact": ["unclear", "none", "near", "touching"],
    "pole_type": ["unclear", "other", "push_brace", "traffic_signal", "street_light", "concrete_or_steel_utility", "wood_utility"],
    "material": ["unclear", "wood", "concrete", "steel", "fiberglass"],
}
UTILITY_TYPES = {"wood_utility", "concrete_or_steel_utility"}
READABLE_PX = 300  # newest frame at or above this height is the displayed photo


def condition_flags(fields):
    """The one predicate for 'possible condition issue'. Mirrors web/predicates.js."""
    out = []
    if fields["lean_severity"] in ("moderate", "severe"):
        out.append("lean")
    if fields["crossarm_condition"] == "damaged":
        out.append("crossarm")
    if fields["vegetation_contact"] == "touching":
        out.append("vegetation")
    return out


def warning_flags(fields):
    """Watch items, one tier below a condition issue. Mirrors web/predicates.js warningFlags."""
    return ["lean_slight"] if fields["lean_severity"] == "slight" else []


def haversine_m(lon1, lat1, lon2, lat2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def cluster(points, radius_m):
    cell = radius_m / 111320.0
    grid = defaultdict(list)
    for pid, (lon, lat) in points.items():
        grid[(int(lon / cell), int(lat / cell))].append(pid)
    parent = {pid: pid for pid in points}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (gx, gy), ids in grid.items():
        neighbors = [p for dx in (-1, 0, 1) for dy in (-1, 0, 1) for p in grid.get((gx + dx, gy + dy), [])]
        for a in ids:
            la, pa = points[a]
            for b in neighbors:
                if a < b and haversine_m(la, pa, *points[b]) <= radius_m:
                    parent[find(a)] = find(b)
    roots = {}
    return {pid: roots.setdefault(find(pid), len(roots)) for pid in points}


def vote(values, field):
    c = Counter(values)
    top = max(c.values())
    winners = [v for v, n in c.items() if n == top]
    if len(winners) == 1:
        w = winners[0]
    elif field in TIE_ORDER:
        w = min(winners, key=lambda v: TIE_ORDER[field].index(v) if v in TIE_ORDER[field] else 99)
    elif field == "attachment_count":
        w = min(winners)
    else:
        w = False if False in winners else winners[0]
    return w, {str(k): n for k, n in sorted(c.items(), key=lambda kv: -kv[1])}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--town", required=True)
    ap.add_argument("--radius", type=float, default=8.0, help="meters; features closer than this merge")
    args = ap.parse_args()
    slug = slugify(args.town)
    src = DATA / "classify" / slug / "classifications.jsonl"
    if not src.exists():
        sys.exit(f"run classify.py first, missing {src}")
    rows = [json.loads(l) for l in src.open()]
    classified = [r for r in rows if r.get("classification")]

    by_feat, all_by_feat = defaultdict(list), defaultdict(list)
    for r in rows:
        all_by_feat[r["feature_id"]].append(r)
        if r.get("classification"):
            by_feat[r["feature_id"]].append(r)
    feat_pts = {fid: (rs[0]["lon"], rs[0]["lat"]) for fid, rs in by_feat.items()}
    cid_of = cluster(feat_pts, args.radius)
    groups = defaultdict(list)
    for fid, cid in cid_of.items():
        groups[cid].append(fid)

    poles = []
    for cid, fids in sorted(groups.items()):
        rs = [r for f in fids for r in by_feat[f]]
        every = [r for f in fids for r in all_by_feat[f]]  # includes frames skipped as too small
        cls = [r["classification"] for r in rs]
        fields, votes = {}, {}
        for f in VOTE_FIELDS:
            fields[f], votes[f] = vote([c[f] for c in cls], f)
        lon = sum(r["lon"] for r in rs) / len(rs)
        lat = sum(r["lat"] for r in rs) / len(rs)
        readable = [r for r in rs if (r.get("pole_px_h") or 0) >= READABLE_PX and r.get("captured_at")]
        best = max(readable, key=lambda r: r["captured_at"]) if readable else \
               max(rs, key=lambda r: ((r.get("pole_px_h") or 0), r["classification"]["confidence"]))
        dated = [r for r in every if r.get("captured_at")]
        latest = max(dated, key=lambda r: r["captured_at"]) if dated else None
        frames = sorted(({
            "image_id": r["image_id"], "captured_at": r.get("captured_at"), "url": r["mapillary_url"],
            "px_h": r.get("pole_px_h"), "is_pano": r["is_pano"], "sequence": r["sequence"], "crop": r.get("crop"),
            "creator": r.get("creator"),
            "pole_type": r["classification"]["pole_type"], "lean": r["classification"]["lean_severity"],
            "crossarm": r["classification"]["crossarm_condition"], "vegetation": r["classification"]["vegetation_contact"],
            "transformer": r["classification"]["transformer_present"], "attachments": r["classification"]["attachment_count"],
            "confidence": r["classification"]["confidence"], "note": r["classification"].get("notes") or "",
            "tilt": apparent_tilt(r.get("polygon_norm"), r.get("width"), r.get("height")),
            "shown": r is best,
        } for r in rs), key=lambda f: f["captured_at"] or 0)
        is_utility = bool(fields["pole_present"]) and fields["pole_type"] in UTILITY_TYPES
        poles.append({
            "pole_id": f"{slug[:4]}-{cid:05d}", "lon": round(lon, 7), "lat": round(lat, 7),
            "feature_ids": sorted(fids), "n_observations": len(rs), "n_frames_available": len(every),
            "n_sequences": len({r["sequence"] for r in rs}),
            "is_utility_pole": is_utility, **fields, "votes": votes,
            "condition_flags": condition_flags(fields),
            "warning_flags": warning_flags(fields),
            "mean_confidence": round(sum(c["confidence"] for c in cls) / len(cls), 2),
            "best_image_id": best["image_id"], "best_crop": best.get("crop"), "best_captured_at": best.get("captured_at"),
            "best_mapillary_url": best["mapillary_url"], "best_creator": best.get("creator"),
            "best_px_h": best.get("pole_px_h"), "best_is_newest": bool(readable),
            "latest_available_at": latest["captured_at"] if latest else None,
            "latest_available_url": latest["mapillary_url"] if latest else None,
            "latest_available_classified": bool(latest and latest.get("classification")) if latest else None,
            "capture_first": min(r["captured_at"] for r in rs if r.get("captured_at")),
            "capture_last": max(r["captured_at"] for r in rs if r.get("captured_at")),
            "frames": frames,
        })

    out_dir = DATA / "poles" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "poles.jsonl").open("w") as fh:
        for p in poles:
            fh.write(json.dumps(p) + "\n")

    util = [p for p in poles if p["is_utility_pole"]]
    summary = {
        "slug": slug, "radius_m": args.radius, "readable_px": READABLE_PX,
        "frames_total": len(rows), "frames_classified": len(classified), "frames_skipped": len(rows) - len(classified),
        "features_with_classified_frame": len(by_feat), "records": len(poles), "utility_records": len(util),
        "other_records": len(poles) - len(util), "records_by_type": dict(Counter(p["pole_type"] for p in poles).most_common()),
        "records_merged_from_multiple_features": sum(1 for p in poles if len(p["feature_ids"]) > 1),
        "utility_with_condition_flag": sum(1 for p in util if p["condition_flags"]),
        "utility_flag_counts": dict(Counter(f for p in util for f in p["condition_flags"])),
        "utility_with_warning_flag": sum(1 for p in util if p["warning_flags"]),
        "utility_3plus_attachments": sum(1 for p in util if p["attachment_count"] >= 3),
        "utility_transformer": sum(1 for p in util if p["transformer_present"]),
        "attachment_histogram": {str(k): v for k, v in sorted(Counter(p["attachment_count"] for p in util).items())},
        "single_frame_records": sum(1 for p in poles if p["n_observations"] == 1),
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "attribution": "Imagery and detections: Mapillary, CC BY-SA 4.0. Derived data: ODbL.",
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"{slug}: {len(classified)} frames -> {len(by_feat)} features -> {len(poles)} records "
          f"({summary['records_merged_from_multiple_features']} merged from multiple features); {len(util)} utility")
    print(f"condition flags {summary['utility_flag_counts']}  records with any {summary['utility_with_condition_flag']}  "
          f"3+ att {summary['utility_3plus_attachments']}  transformer {summary['utility_transformer']}  single-frame {summary['single_frame_records']}")
    print(f"files {out_dir.relative_to(ROOT)}/{{poles.jsonl,summary.json}}")


if __name__ == "__main__":
    main()
