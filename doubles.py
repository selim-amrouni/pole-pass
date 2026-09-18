#!/usr/bin/env python3
"""Step (Marblehead double-pole pass): screen close pairs of utility-pole map features and let
the model decide which are two real poles rather than one pole Mapillary detected twice.

Usage:
  uv run python3 doubles.py --town "Marblehead, Massachusetts" --estimate
  uv run python3 doubles.py --town "Marblehead, Massachusetts" [--radius 6] [--frames 2]
  uv run python3 doubles.py --town "..." --mode direct --limit 10          # sync, for eyeballing
  uv run python3 doubles.py --town "..." --resume <batch_id>               # pick up a submitted batch

THE CENTRAL DIFFICULTY: Mapillary emits 1.2-1.4 map features per real pole (measured: Reading
1.38, Greenpoint 1.34, Hardwick 1.24 -- see dedupe.py, which merges within 8 m for exactly this
reason). A geometric screen for "two pole features within a few meters" therefore returns mostly
ONE POLE DETECTED TWICE, not a real double pole (an old pole left standing beside its
replacement). Distance alone cannot tell the two cases apart -- only a model looking at a frame
that actually contains both detections can. That separation is the point of this module; the
screen below is deliberately cheap and over-inclusive, and every pair it finds survives into the
output even when nothing further can be done with it.

Reads  data/coverage/<slug>/map_features.jsonl       (coverage.py; kept: object--support--utility-pole, in-town)
       data/fetch/<slug>/features/<fid>.json          (fetch.py; ALREADY CACHED -- never fetched here)
       data/osm/<slug>/roads.json                     (roadcover.py; for street naming)
       data/split/<slug>/split.geojson                (split.py; via split.load_split)
Writes data/doubles/<slug>/pairs.jsonl                every pair from the geometric screen, regenerated
       data/doubles/<slug>/results/<pair_id>.json     per-pair model result (the cache)
       data/doubles/<slug>/batches/<batch_id>.json    submitted batch manifest
       data/doubles/<slug>/candidates.jsonl           pairs joined with results, regenerated
       data/doubles/<slug>/summary.json
       data/doubles/crops/<pair_id>.jpg (+.json)      the two-pole crop actually sent (global, like data/crops/)

Mapillary image metadata/thumbnails are fetched through fetch.py's own cache (data/fetch/<slug>/images/,
data/thumbs/) on a cache miss -- doubles.py just never fetches a FEATURE's own detections/geometry
itself, since fetch.py already cached that and may still be filling in.
"""
import argparse
import base64
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image

import classify  # Job/run_batch/run_direct land here; see wait_for_job_contract(). classify.py is not modified.
import district
import fetch
import geo
import mvt
import roadcover
import split
from coverage import DATA, ROOT, load_env, slugify
from dedupe import haversine_m

UTILITY_VALUE = "object--support--utility-pole"
DEFAULT_RADIUS_M = 6.0
DEFAULT_FRAMES = 2
SCHEMA_VERSION = 3  # 3: other_pole_purpose, after Marblehead called a pole beside a streetlight a double (2026-09-18)
CROSS_STREET_MAX_M = 120.0
WORKERS = 8

SYSTEM = """You are inspecting a street-level photograph for a small electric utility that is
retiring old utility poles left standing beside their replacements ("double poles"). Massachusetts
has set a per-pole removal deadline, and the utility must find its remaining backlog before it can
clear it.

Each crop was picked because an upstream detector flagged two separate pole map features within a
few meters of each other. THE MOST IMPORTANT THING TO KNOW: that upstream detector counts the same
physical pole more than once fairly often (measured at 1.2 to 1.4 detections per real pole across
several towns), so a large share of these crops show exactly ONE pole, seen from a slightly
different angle or re-detected in a later pass. Reporting likely_duplicate_detection honestly when
that is what the crop shows matters more than finding real doubles -- a false double sends a crew
to a street corner with nothing to remove.

When a crop does show two real poles, this is the pattern to look for: a utility plants the new
pole beside the old one and moves its own electric conductors over first; the telecom companies
(cable, phone, fiber) often take months or years to follow, so the old pole is frequently left
standing with ONLY the lower communication cables still attached and no electric equipment at all.
The old pole is sometimes cut down to a short stub once its lines are gone. Both poles almost
always stand within a few meters of each other, usually under 3 m.

There is a THIRD case, and it is common. The upstream detector places a pole on the map by
triangulating it from photographs, and depth along the camera's line of sight is the hardest thing
for it to pin down. Two poles that are really 20 or 40 m apart -- consecutive poles carrying the
same line straight away from the camera -- can therefore end up recorded within a metre of each
other, and land in this crop together. They are two real poles, but they are NOT a double pole.

Use where each pole meets the GROUND to tell these apart, not how tall each one looks in the frame:
- Two poles standing side by side meet the ground at about the same height in the picture, even if
  one is much shorter than the other.
- A pole further down the street meets the ground HIGHER in the picture, nearer the horizon, and
  looks thinner.
Height alone will mislead you here, because an old pole cut down to a stub is genuinely short while
standing right next to its replacement. That is a double pole, and you should say so.

Answer only from what is visible in this one crop. Do not guess at what a wider or clearer photo
might show; use unclear when the crop does not settle a question.

Definitions:
- two_poles_visible: the crop actually shows two separate pole structures, not one pole seen once.
- likely_duplicate_detection: true when this crop shows what is almost certainly ONE physical pole
  that the upstream detector reported as two nearby map features.
- other_pole_purpose: what the SECOND pole is, judged on what it carries and what it is made of --
  utility (a wooden or composite distribution pole, whether or not it still carries anything),
  street_light (a metal or concrete lighting standard, usually smooth, tapered and carrying a lamp
  arm), traffic_signal, sign_post, other, unclear. Answer this BEFORE is_double_pole and let it
  decide: a distribution pole standing next to a lighting standard is an ordinary street, not a
  double pole, however close together the two are. This is the single most common way this task is
  got wrong, so do not round street_light up to utility because the pair looks like a double.
- is_double_pole: true only when two_poles_visible is true AND other_pole_purpose is utility AND the
  pair reads as an old pole left standing beside its in-service replacement.
- equipment_transferred: whether the OLDER of the two poles has had its electric equipment (main
  conductors, transformer, crossarm hardware) removed -- yes (clearly gone), partial (some removed,
  some remains), no (electric still appears intact), unclear.
- old_pole_lower_attachments_only: yes when the older pole carries only low communication cables
  and nothing else, no when it still carries electric equipment or carries nothing at all, unclear
  otherwise.
- either_pole_cut_short: yes when one of the two poles has visibly been cut down to a stub shorter
  than a normal in-service pole, no, unclear.
- either_pole_leaning: worst apparent lean of either pole against nearby verticals (building edges,
  not the frame edge, since panoramas distort): none, slight, moderate, severe, unclear.
- poles_at_different_depths: true when the two poles are at clearly different distances from the
  camera -- consecutive poles down the same street rather than a pair standing together. Judge it
  from the ground line, as described above.
- separation_estimate_m: your best visual estimate, in meters, of the ground distance between the
  two pole bases. A rough estimate is fine; this is not a measurement.
- confidence: your overall confidence in this record, 0 to 1.
- reason: one short, plain sentence a lineworker could read and immediately understand -- what you
  saw and why you called it that way."""

DOUBLE_SCHEMA = {
    "type": "object",
    "properties": {
        "two_poles_visible": {"type": "boolean"},
        "likely_duplicate_detection": {"type": "boolean"},
        "other_pole_purpose": {"type": "string",
                               "enum": ["utility", "street_light", "traffic_signal", "sign_post", "other", "unclear"]},
        "is_double_pole": {"type": "boolean"},
        "equipment_transferred": {"type": "string", "enum": ["yes", "partial", "no", "unclear"]},
        "old_pole_lower_attachments_only": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "either_pole_cut_short": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "either_pole_leaning": {"type": "string", "enum": ["none", "slight", "moderate", "severe", "unclear"]},
        "poles_at_different_depths": {"type": "boolean"},
        "separation_estimate_m": {"type": "number"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["two_poles_visible", "likely_duplicate_detection", "other_pole_purpose", "is_double_pole",
                 "equipment_transferred", "old_pole_lower_attachments_only", "either_pole_cut_short",
                 "either_pole_leaning", "poles_at_different_depths", "separation_estimate_m",
                 "confidence", "reason"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- pair id (stable, order-independent)
PINNED_PREFIX = {"marblehead-massachusetts": "MH"}  # its results/ and crops are already keyed MH-; changing it orphans them


def pair_prefix(slug):
    """Short per-town tag for pair ids. data/doubles/crops/ is a single global directory (like
    data/crops/), so without a per-town tag two towns' pairs could collide in it. Marblehead is
    pinned to the tag its cached run already uses."""
    if slug in PINNED_PREFIX:
        return PINNED_PREFIX[slug]
    return (slug.split("-")[0][:3] or "xx").upper()


def make_pair_id(fid_a, fid_b, prefix):
    """Stable across reruns and independent of any numbering -- pole/feature ids renumber or get
    re-clustered elsewhere in this project (see dedupe.py), so the id is derived from the two
    feature ids themselves, sorted, never from list position."""
    lo, hi = sorted([str(fid_a), str(fid_b)])
    return f"{prefix}-" + hashlib.sha1(f"{lo}:{hi}".encode()).hexdigest()[:8]


# ---------------------------------------------------------------- pair screen (grid, not O(n^2))
def pair_screen(points, radius_m):
    """[(id_a, id_b, distance_m), ...] for every pair of points within radius_m, via a grid.

    Same bucketing dedupe.cluster() uses for merging near-duplicate detections -- reused here for
    the opposite purpose: dedupe wants to MERGE close features (they are probably one pole seen
    twice); this wants to keep every close PAIR so a model can tell the merge-worthy ones apart
    from the rare real doubles. points: {id: (lon, lat)}.
    """
    cell = radius_m / 111320.0
    grid = defaultdict(list)
    for pid, (lon, lat) in points.items():
        grid[(int(lon / cell), int(lat / cell))].append(pid)
    pairs = []
    seen = set()
    for (gx, gy), ids in grid.items():
        neighbors = [p for dx in (-1, 0, 1) for dy in (-1, 0, 1) for p in grid.get((gx + dx, gy + dy), [])]
        for a in ids:
            lon_a, lat_a = points[a]
            for b in neighbors:
                if a == b:
                    continue
                key = frozenset((a, b))
                if key in seen:
                    continue
                d = haversine_m(lon_a, lat_a, *points[b])
                if d <= radius_m:
                    seen.add(key)
                    pairs.append((a, b, d))
    return pairs


# ---------------------------------------------------------------- shared frames
def load_feature_detections(slug, fid):
    """detections.data[] (utility-pole only) for a map feature, from fetch.py's own cache.

    Returns None when fetch.py hasn't reached this feature yet -- doubles.py must never call the
    Graph API for a feature's own detections itself (that is fetch.py's job and its cache), so
    this module can run safely while fetch.py is still filling data/fetch/<slug>/features/ in.
    """
    p = DATA / "fetch" / slug / "features" / f"{fid}.json"
    if not p.exists():
        return None
    mf = json.loads(p.read_text())
    return [d for d in (mf.get("detections") or {}).get("data", [])
            if d.get("value") == UTILITY_VALUE and d.get("geometry") and d.get("image")]


def shared_detections(dets_a, dets_b):
    """{image_id: (det_a, det_b)} for images where BOTH features have a detection.

    Pure id-set intersection, no geometry decoding -- computable with zero extra API calls, since
    fetch.py's cached feature JSON already carries every detection's image id.
    """
    by_a = {str(d["image"]["id"]): d for d in dets_a}
    by_b = {str(d["image"]["id"]): d for d in dets_b}
    return {iid: (by_a[iid], by_b[iid]) for iid in set(by_a) & set(by_b)}


def select_frames(dets_a, dets_b, frames_n):
    """("no_shared_frame", []) or ("ok", [{"image_id","area","det_a","det_b","box_a","box_b"}, ...]),
    ranked by the two detections' union bbox area (normalized 0..1 units; bigger = closer/clearer),
    capped to the top frames_n. A pair with no shared frame is a fully valid outcome, never dropped."""
    shared = shared_detections(dets_a, dets_b)
    if not shared:
        return "no_shared_frame", []
    ranked = []
    for iid, (da, db) in shared.items():
        polys_a = mvt.decode_detection(da["geometry"])
        polys_b = mvt.decode_detection(db["geometry"])
        if not polys_a or not polys_b:
            continue
        box_a = fetch.polygon_bbox(polys_a)
        box_b = fetch.polygon_bbox(polys_b)
        union = (min(box_a[0], box_b[0]), min(box_a[1], box_b[1]), max(box_a[2], box_b[2]), max(box_a[3], box_b[3]))
        area = (union[2] - union[0]) * (union[3] - union[1])
        ranked.append({"image_id": iid, "area": area, "det_a": da, "det_b": db, "box_a": box_a, "box_b": box_b})
    if not ranked:
        return "no_shared_frame", []  # every shared image's geometry failed to decode
    ranked.sort(key=lambda r: r["area"], reverse=True)
    # frames_n=None returns the whole ranking. process_pair needs that, because a frame's capture
    # date only arrives with its image metadata, so the date filter cannot be applied until after
    # this ranking exists -- capping here first would discard recent frames sight unseen.
    return "ok", ranked if frames_n is None else ranked[:frames_n]


# ---------------------------------------------------------------- separation measured from the frame
# Why this exists. Two numbers for "how far apart are these poles" already existed and BOTH are bad:
#
#   distance_m            haversine between the two Mapillary map-feature positions. Those positions
#                         are triangulated from photographs, and depth along the camera ray is the
#                         worst-constrained axis, so touching poles can land metres apart.
#   separation_estimate_m the model eyeballing metres off a crop. Measured on the Marblehead run it
#                         clusters on round numbers and overestimates badly: pair MH-dd2a587e, whose
#                         two poles visibly cross each other, was called 4 m (and 5.90 m by the map).
#
# This is a third number and, unlike those two, it is a measurement of the evidence itself. The two
# detection boxes in the shared frame give the poles' apparent width and their apparent separation in
# the same units, and that RATIO is what carries the distance:
#
#     ground separation / pole diameter  ~=  apparent separation / apparent width
#
# so separation ~= widths_apart * POLE_DIAMETER_M. The ratio cancels focal length and image size, and
# it survives equirectangular panoramas, where horizontal pixels are proportional to bearing and both
# quantities scale together. It is only valid while both poles are at similar depth, which is exactly
# the double-pole case -- `depth_ratio` below reports when that assumption breaks rather than hiding it.
POLE_DIAMETER_M = 0.30   # nominal class-4/5 distribution pole at breast height; a stated assumption, not a measurement
SAME_DEPTH_MAX_RATIO = 2.0  # apparent widths differing by more than this mean one pole is much further away


def frame_separation(box_a, box_b):
    """Ground separation of two poles, measured from their detection boxes in one shared frame.

    Boxes are (x0, y0, x1, y1) normalized 0..1. Only x is used for the ratio, so normalizing x by
    width and y by height does not distort it. Returns None when either box is degenerate.
    """
    wa, wb = box_a[2] - box_a[0], box_b[2] - box_b[0]
    if wa <= 0 or wb <= 0:
        return None
    mean_w = (wa + wb) / 2
    dx = abs((box_a[0] + box_a[2]) / 2 - (box_b[0] + box_b[2]) / 2)
    widths_apart = dx / mean_w
    return {
        "widths_apart": round(widths_apart, 2),
        "gap_m": round(widths_apart * POLE_DIAMETER_M, 2),
        # Boxes that overlap horizontally cannot be two poles standing apart; on the Marblehead run
        # this was true of 13/27 called doubles but only 7/159 assessed non-doubles.
        "boxes_overlap": bool(min(box_a[2], box_b[2]) - max(box_a[0], box_b[0]) > 0),
        # >1 means one pole looks wider, i.e. nearer. Past SAME_DEPTH_MAX_RATIO the ratio method's
        # equal-depth assumption has failed and gap_m should not be believed.
        "depth_ratio": round(max(wa, wb) / min(wa, wb), 2),
        "same_depth": bool(max(wa, wb) / min(wa, wb) <= SAME_DEPTH_MAX_RATIO),
        "pole_diameter_assumed_m": POLE_DIAMETER_M,
    }


# ---------------------------------------------------------------- fetch + crop (reuses fetch.py's own caches)
def fetch_image(slug, iid, token):
    """Image metadata + thumbnail path, via fetch.py's exact cache layout and fields -- never a
    second thumbnail cache. Returns (meta_dict, thumb_path_or_None)."""
    im = fetch.cached(DATA / "fetch" / slug / "images" / f"{iid}.json",
                       lambda: fetch.graph(iid, fetch.IMAGE_FIELDS, token))
    thumb = DATA / "thumbs" / f"{iid}.jpg"
    if not thumb.exists():
        url = im.get("thumb_original_url") if im.get("is_pano") else im.get("thumb_2048_url")
        url = url or im.get("thumb_2048_url") or im.get("thumb_original_url")
        if url:
            thumb.parent.mkdir(parents=True, exist_ok=True)
            thumb.write_bytes(fetch.http(url, binary=True))
    return im, thumb if thumb.exists() else None


def make_double_crop(pair_id, thumb_path, box_a, box_b, crops_dir):
    """Crop the union of both detections with context, cap the long side, cache to disk.

    Returns (crop_path, meta_dict) or (None, "too_small_<h>px" / "no_thumbnail")."""
    out = crops_dir / f"{pair_id}.jpg"
    meta_path = out.with_suffix(".json")
    if out.exists() and meta_path.exists():
        return out, json.loads(meta_path.read_text())
    if thumb_path is None:
        return None, "no_thumbnail"
    with Image.open(thumb_path) as im:
        W, H = im.size
        pb_a = (box_a[0] * W, box_a[1] * H, box_a[2] * W, box_a[3] * H)
        pb_b = (box_b[0] * W, box_b[1] * H, box_b[2] * W, box_b[3] * H)
        taller = max(pb_a[3] - pb_a[1], pb_b[3] - pb_b[1])
        if taller < classify.MIN_POLE_H_PX:
            return None, f"too_small_{taller:.0f}px"
        x0 = min(pb_a[0], pb_b[0]); y0 = min(pb_a[1], pb_b[1])
        x1 = max(pb_a[2], pb_b[2]); y1 = max(pb_a[3], pb_b[3])
        pw, ph = x1 - x0, y1 - y0
        # wide margin: enough to see the tops of both poles (crossarms/attachments live there) and
        # a little of what is strung between them; more headroom than footroom, as classify.py does
        margin_x = max(0.4 * pw, 100)
        source_box = (int(max(0, x0 - margin_x)), int(max(0, y0 - 0.3 * ph)),
                      int(min(W, x1 + margin_x)), int(min(H, y1 + 0.1 * ph)))
        crop = im.crop(source_box).convert("RGB")
        crop.thumbnail((classify.MAX_LONG_SIDE, classify.MAX_LONG_SIDE))
        crops_dir.mkdir(parents=True, exist_ok=True)
        crop.save(out, "JPEG", quality=85)
        meta = {"source_box": source_box, "box_a": [round(v) for v in pb_a], "box_b": [round(v) for v in pb_b],
                "source_w": W, "source_h": H, "crop_size": list(crop.size)}
        meta_path.write_text(json.dumps(meta))
        return out, meta


# ---------------------------------------------------------------- street naming (never geocode a defect to an address)
# A pole sits in the public right of way. Reverse-geocoding its position would attach a "leaning
# pole" or "double pole" flag to whatever parcel/address happens to be nearest, i.e. a named
# person's house -- for a public-infrastructure defect that isn't theirs. Nearest NAMED ROAD
# CENTRELINE is the only geographic label this module ever produces. Do not "improve" this into a
# reverse geocode.
def nearest_named_way(lon, lat, ways, exclude_name=None, max_m=None):
    """(name, distance_m) of the nearest way in `ways` with a name (and, optionally, not
    `exclude_name`, and within max_m), else (None, None). ways: roadcover.kept_ways() rows."""
    best_name, best_d = None, None
    for w in ways:
        name = w.get("name")
        if not name or name == exclude_name:
            continue
        coords = w["coords"]
        d = min(geo.seg_point_dist_m(lon, lat, a, b) for a, b in zip(coords, coords[1:]))
        if max_m is not None and d > max_m:
            continue
        if best_d is None or d < best_d:
            best_name, best_d = name, d
    return best_name, best_d


# ---------------------------------------------------------------- per-pair processing (threaded: Graph API calls)
def process_pair(pair, slug, token, frames_n, crops_dir, since_ms=None):
    """Enrich one screened pair with shared-frame selection and a crop, up to (but not including)
    classification. Returns a dict merged into the candidate row; never raises for an ordinary
    "nothing more to do" outcome -- those are statuses, not failures."""
    fid_a, fid_b = pair["feature_ids"]
    dets_a = load_feature_detections(slug, fid_a)
    dets_b = load_feature_detections(slug, fid_b)
    if dets_a is None or dets_b is None:
        return {"status": "feature_not_cached", "capture_first": None, "capture_last": None,
                "chosen_image_id": None, "chosen_mapillary_url": None, "crop": None, "n_shared_frames": 0}

    status, ranked = select_frames(dets_a, dets_b, None)   # None: whole ranking, filtered below
    if status == "no_shared_frame":
        return {"status": status, "capture_first": None, "capture_last": None,
                "chosen_image_id": None, "chosen_mapillary_url": None, "crop": None, "n_shared_frames": 0}

    # Walk the ranking, keeping the best frames that are also recent enough. The date cannot be part
    # of the ranking itself -- it arrives with the image metadata -- and for a double-pole claim the
    # photo date is the evidence, so the largest view of a pair is worthless if it is from 2018.
    # Metadata is cached, so the extra look-ups cost nothing on a rerun.
    captured, kept = [], []
    for k in ranked:
        if len(kept) >= frames_n:
            break
        im, _ = fetch_image(slug, k["image_id"], token)
        k["captured_at"] = im.get("captured_at")
        if since_ms is not None and (k["captured_at"] or 0) < since_ms:
            continue
        kept.append(k)
        if k["captured_at"]:
            captured.append(k["captured_at"])
    if not kept:
        # Shared frames exist, but none since the cutoff: no recent photo shows both poles together.
        return {"status": "no_recent_shared_frame", "capture_first": None, "capture_last": None,
                "chosen_image_id": None, "chosen_mapillary_url": None, "crop": None, "n_shared_frames": 0}
    chosen = kept[0]
    _, thumb = fetch_image(slug, chosen["image_id"], token)
    crop_path, info = make_double_crop(pair["pair_id"], thumb, chosen["box_a"], chosen["box_b"], crops_dir)
    if crop_path is None:
        status = "too_small" if isinstance(info, str) and info.startswith("too_small") else info
        return {"status": status, "capture_first": min(captured) if captured else None,
                "capture_last": max(captured) if captured else None, "chosen_image_id": chosen["image_id"],
                "chosen_mapillary_url": f"https://www.mapillary.com/app/?pKey={chosen['image_id']}&focus=photo",
                "crop": None, "n_shared_frames": len(kept)}

    return {"status": "ok", "capture_first": min(captured) if captured else None,
            "capture_last": max(captured) if captured else None, "chosen_image_id": chosen["image_id"],
            "chosen_mapillary_url": f"https://www.mapillary.com/app/?pKey={chosen['image_id']}&focus=photo",
            "crop": str(crop_path.relative_to(ROOT)), "crop_path": crop_path,
            # measured from the SAME frame the model is shown, so the number and the picture agree
            "separation": frame_separation(chosen["box_a"], chosen["box_b"]),
            "crop_size": tuple(info["crop_size"]), "n_shared_frames": len(kept)}


# ---------------------------------------------------------------- classify (reuses classify.Job, never a second loop)
def build_double_request(item):
    data = base64.standard_b64encode(item["crop_path"].read_bytes()).decode()
    when = time.strftime("%Y-%m", time.gmtime(item["captured_at"] / 1000)) if item.get("captured_at") else "unknown"
    context = (f"Crop from a photo taken {when}. The two flagged map features are about "
               f"{item['distance_m']:.1f} m apart on the ground. Return the JSON record.")
    return {
        "model": classify.MODEL,
        "max_tokens": 500,
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "output_config": {"format": {"type": "json_schema", "schema": DOUBLE_SCHEMA}, "effort": "low"},
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
            {"type": "text", "text": context},
        ]}],
    }


def parse_double_result(msg):
    """(record, usage) or raises ValueError on malformed/refused output. Mirrors classify.parse_result."""
    if msg.stop_reason == "refusal":
        raise ValueError("refusal")
    text = next((b.text for b in msg.content if b.type == "text"), "")
    rec = json.loads(text)
    missing = [k for k in DOUBLE_SCHEMA["required"] if k not in rec]
    if missing:
        raise ValueError(f"missing {missing}")
    usage = {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens,
             "cache_read": getattr(msg.usage, "cache_read_input_tokens", 0) or 0,
             "cache_write": getattr(msg.usage, "cache_creation_input_tokens", 0) or 0}
    return rec, usage


def adjudicate(result, separation):
    """(is_double, rejected_because) -- the verdict the page shows, never result["is_double_pole"] raw.

    The Marblehead run shipped false positives its own output already contradicted: MH-fdad8b7b's
    reason called the second pole "a straighter pole carrying a streetlight" while the badge said
    double, and MH-786f670a paired two poles down the street (7.9 pole-widths apart, one 3.2x wider
    than the other) rather than a pair standing together. Both contradictions are mechanically
    checkable, so they are checked here instead of being left to the model's prose. A rejection keeps
    the row -- it just stops being a candidate, and rejected_because says why in plain words.
    """
    if not result or not result.get("is_double_pole"):
        return False, None
    purpose = result.get("other_pole_purpose")
    if purpose in ("street_light", "traffic_signal", "sign_post"):
        return False, f"the second pole is a {purpose.replace('_', ' ')}, not a utility pole"
    if result.get("poles_at_different_depths"):
        return False, "the two poles are at different distances from the camera, not standing together"
    # Deliberately NOT gated on the frame geometry. depth_ratio looked like it should catch the
    # different-depths case, but measured on Marblehead's 27 calls its median is 1.83, so any
    # threshold tight enough to catch the one confirmed error also throws out a third of the good
    # calls -- pole detection boxes are only tens of pixels wide and the far pole is routinely
    # occluded by the near one, so the ratio is noise at this scale. One confirmed false positive is
    # not enough to fit a threshold on. The geometry rides along on the row as evidence a reviewer
    # can see and sort by; it does not silently drop rows.
    return True, None


def wait_for_job_contract(retries=10, wait_s=30):
    """Block until classify.Job (the shared batch/direct runner) exists. classify.py is being
    developed by another agent in parallel; a plain module-level import can land before that
    lands, so this reloads and retries rather than crashing the whole CLI on an ImportError."""
    import importlib
    for attempt in range(retries):
        importlib.reload(classify)
        if hasattr(classify, "Job"):
            return
        if attempt < retries - 1:
            print(f"classify.Job not available yet, waiting 30s (attempt {attempt + 1}/{retries})...")
            time.sleep(wait_s)
    sys.exit("blocked: classify.Job was never importable after 10 retries (5 minutes) -- "
              "the classify.py runner refactor doesn't appear to be done.")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--town", required=True)
    ap.add_argument("--radius", type=float, default=DEFAULT_RADIUS_M, help="meters; features closer than this screen as a pair")
    ap.add_argument("--frames", type=int, default=DEFAULT_FRAMES, help="shared frames kept per pair, largest union bbox first")
    ap.add_argument("--estimate", action="store_true", help="make crops, print expected cost, send nothing")
    ap.add_argument("--mode", choices=["batch", "direct"], default="batch")
    ap.add_argument("--limit", type=int, help="only the first N uncached pairs")
    ap.add_argument("--resume", help="batch id to collect instead of submitting")
    ap.add_argument("--no-wait", action="store_true", help="submit the batch and exit; collect later with --resume")
    args = ap.parse_args()
    slug = slugify(args.town)

    feats_path = DATA / "coverage" / slug / "map_features.jsonl"
    if not feats_path.exists():
        sys.exit(f"run coverage.py first, missing {feats_path}")
    ring = district.ring(slug)  # the area boundary itself (district cell or town); required
    # The maintenance split is Marblehead-only -- it exists because the Light Department published an
    # MMLD/Verizon boundary. Everywhere else the poles are jointly owned by the electric utility and
    # the telecom, and which of them is next to move its lines is precisely what is not public. So a
    # missing split means no maintainer is claimed for any pair, not that the run cannot proceed.
    try:
        split_data = split.load_split(slug)
    except (FileNotFoundError, SystemExit):
        split_data = None
    roads_path = DATA / "osm" / slug / "roads.json"
    if not roads_path.exists():
        sys.exit(f"run roadcover.py first, missing {roads_path}")
    named_ways = [w for w in roadcover.kept_ways(json.loads(roads_path.read_text())) if w.get("name")]

    feats = [json.loads(l) for l in feats_path.open()]
    feats = [f for f in feats if f["value"] == UTILITY_VALUE]
    feats = [f for f in feats if geo.point_in_polygon(f["lon"], f["lat"], [ring])]
    points = {str(f["id"]): (f["lon"], f["lat"]) for f in feats}
    print(f"{slug}: {len(feats)} in-town utility-pole features")

    screened = pair_screen(points, args.radius)
    prefix = pair_prefix(slug)
    pairs = []
    for a, b, d in screened:
        lo, hi = sorted([a, b])
        lon = (points[a][0] + points[b][0]) / 2
        lat = (points[a][1] + points[b][1]) / 2
        pairs.append({"pair_id": make_pair_id(lo, hi, prefix), "feature_ids": [lo, hi],
                      "distance_m": round(d, 2), "lon": round(lon, 7), "lat": round(lat, 7)})
    pairs.sort(key=lambda p: p["pair_id"])

    out_dir = DATA / "doubles" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "pairs.jsonl").open("w") as fh:
        for p in pairs:
            fh.write(json.dumps(p) + "\n")
    print(f"  {len(screened)} pairs within {args.radius:.0f} m (grid screen, not O(n^2)) -> {out_dir.relative_to(ROOT)}/pairs.jsonl")

    # tag every pair with maintainer + street/cross_street from the midpoint alone -- this needs no
    # frame at all, so it applies uniformly whether or not the pair ever gets a crop
    line, buffer_m = (split_data["line"], split_data["buffer_m"]) if split_data else (None, None)
    for p in pairs:
        p["maintainer"] = split.maintainer_of(p["lon"], p["lat"], line, buffer_m) if line else None
        street, street_d = nearest_named_way(p["lon"], p["lat"], named_ways)
        cross, cross_d = nearest_named_way(p["lon"], p["lat"], named_ways, exclude_name=street, max_m=CROSS_STREET_MAX_M)
        p["street"] = street
        p["street_distance_m"] = round(street_d, 1) if street_d is not None else None
        p["cross_street"] = cross
        p["cross_street_distance_m"] = round(cross_d, 1) if cross_d is not None else None

    token = load_env().get("MAPILLARY_TOKEN", "")
    # A district records its own imagery cutoff, so reruns reproduce the same vintage.
    since_ms = district.since_ms(slug)
    if since_ms:
        print(f"  frames restricted to {datetime.fromtimestamp(since_ms/1000, tz=timezone.utc).date()} onward")
    crops_dir = DATA / "doubles" / "crops"
    results_dir = out_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    enriched, errors = {}, []
    with ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(process_pair, p, slug, token, args.frames, crops_dir, since_ms): p for p in pairs}
        for k, fut in enumerate(as_completed(futs), 1):
            p = futs[fut]
            try:
                enriched[p["pair_id"]] = fut.result()
            except Exception as e:
                errors.append((p["pair_id"], str(e)))
                enriched[p["pair_id"]] = {"status": f"error:{type(e).__name__}", "capture_first": None, "capture_last": None,
                                           "chosen_image_id": None, "chosen_mapillary_url": None, "crop": None, "n_shared_frames": 0}
            if k % 50 == 0 or k == len(pairs):
                print(f"\r  frames+crops {k}/{len(pairs)}", end="", flush=True)
    if pairs:
        print()
    if errors:
        print(f"  {len(errors)} pair(s) errored during frame/crop lookup, first 5: {errors[:5]}")

    by_status = Counter(e["status"] for e in enriched.values())
    print(f"  status: {dict(by_status)}")

    # build the classify todo: "ok" pairs (a crop exists) not already cached
    todo = []
    for p in pairs:
        e = enriched[p["pair_id"]]
        if e["status"] != "ok":
            continue
        if (results_dir / f"{p['pair_id']}.json").exists():
            continue
        todo.append({"pair_id": p["pair_id"], "crop_path": e["crop_path"],
                     "distance_m": p["distance_m"], "captured_at": e.get("capture_last")})
    if args.limit:
        todo = todo[:args.limit]
    cached_n = sum(1 for p in pairs if (results_dir / f"{p['pair_id']}.json").exists())
    print(f"  {cached_n} cached results, {len(todo)} pairs to classify")

    usage = None
    if args.estimate or (not todo and not args.resume):
        if todo:
            sizes = [enriched[t["pair_id"]]["crop_size"] for t in todo]
            est_in = sum(classify.image_tokens(sz) + 220 for sz in sizes)
            est_out = len(todo) * 130
            est = est_in / 1e6 * classify.PRICE_IN + est_out / 1e6 * classify.PRICE_OUT
            print(f"  estimate: ~{est_in:,} input + ~{est_out:,} output tokens -> ${est:.2f} direct, "
                  f"${est * classify.BATCH_DISCOUNT:.2f} batched ({classify.MODEL})")
        else:
            print("  nothing to classify (nothing new, or run again once fetch.py has more features cached)")
    else:
        wait_for_job_contract()
        import anthropic
        key = load_env().get("ANTHROPIC_API_KEY", "")
        if not key:
            sys.exit("ANTHROPIC_API_KEY missing from .env. See .env.example.")
        client = anthropic.Anthropic(api_key=key)
        job = classify.Job(name="doubles", results_dir=results_dir, batches_dir=out_dir / "batches",
                            schema_version=SCHEMA_VERSION, id_field="pair_id",
                            id_of=lambda it: it["pair_id"], build_request=build_double_request, parse=parse_double_result)
        if args.mode == "direct" and not args.resume:
            usage = classify.run_direct(client, job, todo)
        else:
            usage = classify.run_batch(client, job, todo, resume=args.resume, no_wait=args.no_wait)

    write_candidates_and_summary(slug, pairs, enriched, results_dir, out_dir, args, usage)


def write_candidates_and_summary(slug, pairs, enriched, results_dir, out_dir, args, usage):
    rows = []
    for p in pairs:
        e = enriched[p["pair_id"]]
        rp = results_dir / f"{p['pair_id']}.json"
        result, dropped = None, None
        if rp.exists():
            cached = json.loads(rp.read_text())
            dropped = cached.get("dropped")
            result = cached.get("result")
        status = e["status"]
        if dropped:
            status = "dropped"
        elif result is not None:
            status = "classified"
        separation = e.get("separation")
        is_double, rejected = adjudicate(result, separation)
        rows.append({**p, "status": status, "capture_first": e["capture_first"], "capture_last": e["capture_last"],
                     "n_shared_frames": e["n_shared_frames"], "chosen_image_id": e["chosen_image_id"],
                     "chosen_mapillary_url": e["chosen_mapillary_url"], "crop": e["crop"],
                     # `separation` is measured from the frame; `is_double` is the adjudicated verdict.
                     # Downstream must read these, not result["is_double_pole"] or the two old distances.
                     "separation": separation, "is_double": is_double, "rejected_because": rejected,
                     "result": result})

    with (out_dir / "candidates.jsonl").open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    classified = [r for r in rows if r["result"]]
    n_dup = sum(1 for r in classified if r["result"]["likely_duplicate_detection"])
    n_model_double = sum(1 for r in classified if r["result"]["is_double_pole"])
    n_double = sum(1 for r in classified if r["is_double"])           # after adjudicate(): the candidate count
    rejections = Counter(r["rejected_because"] for r in classified if r["rejected_because"])
    by_purpose = Counter(r["result"].get("other_pole_purpose") for r in classified)
    confs = sorted(r["result"]["confidence"] for r in classified)
    conf_hist = Counter(min(3, int(c * 4)) for c in confs)  # 0-.25 / .25-.5 / .5-.75 / .75-1
    by_status = Counter(r["status"] for r in rows)
    by_maintainer = Counter(r["maintainer"] for r in rows if r.get("maintainer"))

    cost = classify.cost_usd(usage, batch=(args.mode == "batch")) if usage and usage.get("input") else 0.0
    summary = {
        "slug": slug, "radius_m": args.radius, "frames_kept": args.frames,
        "pairs_screened": len(pairs), "by_status": dict(by_status),
        "classified": len(classified), "likely_duplicate_detection": n_dup,
        "is_double_pole": n_double,                       # adjudicated; this is the candidate count
        "model_said_double": n_model_double,              # before adjudicate(), for the gap to be visible
        "rejected_by_adjudication": dict(rejections),
        "other_pole_purpose": dict(by_purpose),
        "pole_diameter_assumed_m": POLE_DIAMETER_M,
        "confidence_histogram": {"0.00-0.25": conf_hist.get(0, 0), "0.25-0.50": conf_hist.get(1, 0),
                                  "0.50-0.75": conf_hist.get(2, 0), "0.75-1.00": conf_hist.get(3, 0)},
        "cost_usd": round(cost, 4),
        "attribution": "Imagery and detections: Mapillary, CC BY-SA 4.0. Roads and boundary: OpenStreetMap contributors, ODbL. Derived data: ODbL.",
    }
    if by_maintainer:  # only Marblehead has a published maintenance split; elsewhere there is nothing to say
        summary["by_maintainer"] = dict(by_maintainer)
        summary["note"] = split.NOTE
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    print(f"\n{slug}: {len(pairs)} pairs screened, by status {dict(by_status)}")
    if classified:
        print(f"  of {len(classified)} classified: {n_dup} likely duplicate detections, "
              f"{n_model_double} called double by the model -> {n_double} candidates after adjudication")
        for why, n in rejections.most_common():
            print(f"      rejected {n:>4}: {why}")
        print(f"  second pole was: {dict(by_purpose)}")
        print(f"  confidence distribution: {summary['confidence_histogram']}")
    if by_maintainer:
        print(f"  by maintainer: {dict(by_maintainer)}")
    if cost:
        print(f"  cost: ${cost:.3f}")
    print(f"-> {out_dir.relative_to(ROOT)}/{{pairs.jsonl,candidates.jsonl,summary.json}}")


if __name__ == "__main__":
    main()
