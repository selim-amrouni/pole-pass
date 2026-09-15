#!/usr/bin/env python3
"""Step 4: merge per-frame observations into poles.

Usage:
  uv run python3 dedupe.py --town "Greenpoint, Brooklyn, New York" [--radius 8]

Reads  data/classify/<slug>/classifications.jsonl
Writes data/poles/<slug>/poles.jsonl      one row per pole
       data/poles/<slug>/summary.json     counts that the report and webapp cite

Two-level grouping:
  1. Every observation already carries Mapillary's feature_id, so frames of the
     same feature merge first.
  2. Features within --radius meters of each other merge too, since Mapillary
     sometimes emits two features for one physical pole (different sequences,
     different years). Single-linkage on a grid, no dependencies.

Per field: majority vote over observations, ties broken toward the more
conservative value (lower severity / unclear). disagreement rate per field =
share of observations that differ from the winner, which is the reliability
signal the report shows. Observations classified as street_light,
traffic_signal, or other are kept in the row but do not count as utility poles.
"""
import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from coverage import DATA, ROOT, slugify

VOTE_FIELDS = ["pole_present", "pole_type", "material", "lean_severity", "crossarm_condition",
               "transformer_present", "vegetation_contact", "attachment_count"]
# tie-break order: index 0 wins ties (conservative)
TIE_ORDER = {
    "lean_severity": ["unclear", "none", "slight", "moderate", "severe"],
    "crossarm_condition": ["unclear", "none_visible", "intact", "damaged"],
    "vegetation_contact": ["unclear", "none", "near", "touching"],
    "pole_type": ["unclear", "other", "traffic_signal", "street_light", "concrete_or_steel_utility", "wood_utility"],
    "material": ["unclear", "wood", "concrete", "steel", "fiberglass"],
}
UTILITY_TYPES = {"wood_utility", "concrete_or_steel_utility"}
SEVERITY = {  # points per flag; summed into a 0..n score for map coloring
    "lean_severity": {"moderate": 1, "severe": 2},
    "crossarm_condition": {"damaged": 2},
    "vegetation_contact": {"near": 0, "touching": 1},
}


def haversine_m(lon1, lat1, lon2, lat2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def cluster(points, radius_m):
    """points: {id: (lon, lat)}. Single-linkage within radius via a coarse grid. Returns {id: cluster_idx}."""
    cell = radius_m / 111320.0  # degrees of latitude per cell, roughly radius
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
    return w, round(1 - top / len(values), 2)


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
    obs = [r for r in rows if r.get("classification")]
    dropped = [r for r in rows if not r.get("classification")]

    # level 1: by feature; level 2: features within radius
    by_feat = defaultdict(list)
    for r in obs:
        by_feat[r["feature_id"]].append(r)
    feat_pts = {fid: (rs[0]["lon"], rs[0]["lat"]) for fid, rs in by_feat.items()}
    cid_of = cluster(feat_pts, args.radius)
    groups = defaultdict(list)
    for fid, cid in cid_of.items():
        groups[cid].extend(by_feat[fid])

    poles = []
    for cid, rs in sorted(groups.items()):
        cls = [r["classification"] for r in rs]
        fields, disagreement = {}, {}
        for f in VOTE_FIELDS:
            fields[f], disagreement[f] = vote([c[f] for c in cls], f)
        lon = sum(r["lon"] for r in rs) / len(rs)
        lat = sum(r["lat"] for r in rs) / len(rs)
        best = max(rs, key=lambda r: ((r.get("pole_px_h") or 0), r["classification"]["confidence"]))
        sev = sum(SEVERITY[f].get(fields[f], 0) for f in SEVERITY)
        flags = [f for f in SEVERITY if SEVERITY[f].get(fields[f], 0) > 0]
        is_utility = bool(fields["pole_present"]) and fields["pole_type"] in UTILITY_TYPES
        poles.append({
            "pole_id": f"{slug[:4]}-{cid:05d}", "lon": round(lon, 7), "lat": round(lat, 7),
            "feature_ids": sorted({r["feature_id"] for r in rs}),
            "n_observations": len(rs), "n_sequences": len({r["sequence"] for r in rs}),
            "is_utility_pole": is_utility,
            **fields,
            "disagreement": disagreement,
            "mean_confidence": round(sum(c["confidence"] for c in cls) / len(cls), 2),
            "severity_score": sev, "flags": flags,
            "best_image_id": best["image_id"], "best_crop": best.get("crop"), "best_captured_at": best.get("captured_at"),
            "best_mapillary_url": best["mapillary_url"], "best_creator": best.get("creator"),
            "capture_first": min(r["captured_at"] for r in rs if r.get("captured_at")),
            "capture_last": max(r["captured_at"] for r in rs if r.get("captured_at")),
            "notes": [c["notes"] for c in cls if c.get("notes")][:3],
        })

    out_dir = DATA / "poles" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "poles.jsonl").open("w") as fh:
        for p in poles:
            fh.write(json.dumps(p) + "\n")

    util = [p for p in poles if p["is_utility_pole"]]
    types = Counter(p["pole_type"] for p in poles)
    att = Counter(p["attachment_count"] for p in util)
    summary = {
        "slug": slug, "radius_m": args.radius,
        "observations_classified": len(obs), "observations_dropped": len(dropped),
        "features": len(by_feat), "poles": len(poles), "utility_poles": len(util),
        "poles_by_type": dict(types.most_common()),
        "merged_multi_feature_poles": sum(1 for p in poles if len(p["feature_ids"]) > 1),
        "attachment_histogram": {str(k): v for k, v in sorted(att.items())},
        "utility_poles_3plus_attachments": sum(1 for p in util if p["attachment_count"] >= 3),
        "flags": {f: Counter(p[f] for p in util) for f in SEVERITY},
        "transformers": sum(1 for p in util if p["transformer_present"]),
        "severity_histogram": dict(sorted(Counter(p["severity_score"] for p in util).items())),
        "mean_disagreement": {f: round(sum(p["disagreement"][f] for p in poles) / max(len(poles), 1), 3) for f in VOTE_FIELDS},
        "multi_observation_poles": sum(1 for p in poles if p["n_observations"] > 1),
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "attribution": "Imagery and detections: Mapillary, CC BY-SA 4.0. Derived data: ODbL.",
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=dict))

    print(f"{slug}: {len(obs)} observations -> {len(by_feat)} features -> {len(poles)} poles "
          f"({summary['merged_multi_feature_poles']} merged from multiple features), {len(dropped)} observations dropped")
    print(f"pole types      {dict(types.most_common())}")
    print(f"utility poles   {len(util)}   with 3+ attachments {summary['utility_poles_3plus_attachments']}   transformers {summary['transformers']}")
    for f in SEVERITY:
        print(f"{f:20} {dict(summary['flags'][f])}")
    print(f"attachments     {summary['attachment_histogram']}")
    print(f"disagreement    " + ", ".join(f"{f}={v}" for f, v in summary['mean_disagreement'].items() if f != 'pole_present'))
    print(f"files           {out_dir.relative_to(ROOT)}/{{poles.jsonl,summary.json}}")


if __name__ == "__main__":
    main()
