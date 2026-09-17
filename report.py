#!/usr/bin/env python3
"""Step 6: render the static web bundle for one territory.

Usage:
  uv run python3 report.py --town "Greenpoint, Brooklyn, New York" [--contact mailto:you@example.com] [--example gree-00062]

Reads  data/poles/<slug>/{poles.jsonl,summary.json}
       data/coverage/<slug>/summary.json
       data/validate/<slug>/precision.json          (optional; only a real file produces validation results)
       data/crops/<detection_id>.jpg                 (per-frame crops)
       web/{index.html,style.css,app.js,predicates.js}
Writes out/<slug>/index.html, style.css, app.js, predicates.js
       out/<slug>/data.js                            window.POLE_DATA = {meta, records}
       out/<slug>/poles.geojson, poles.csv           all records, ODbL
       out/<slug>/crops/<pole_id>.jpg                photo shown per record (640 px)
       out/<slug>/frames/<image_id>.jpg              every assessed photo (560 px), for in-app comparison

Copy on the page lives in web/index.html. Counts on the page are computed in the
browser from data.js with web/predicates.js, not written here.
"""
import argparse
import csv
from collections import Counter
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from coverage import DATA, ROOT, slugify

WEB = ROOT / "web"
OUT = ROOT / "out"
CROP_PX, FRAME_PX = 640, 560
ATTRIBUTION = ("Imagery and detections © Mapillary contributors, CC BY-SA 4.0. Derived data ODbL. "
               "Basemap © OpenStreetMap contributors.")
OSM_ATTRIBUTION = "OSM pole comparison © OpenStreetMap contributors, ODbL."
DEFAULT_CONTACT = "mailto:selim.amrouni@gmail.com"
# Example record opened on first load, keyed by the detection id of its shown photo (the crop file name), because pole ids
# are renumbered by every dedupe run. Each chosen after viewing the crop: whole pole, clear, visible equipment, no flag.
DEFAULT_EXAMPLE = {"greenpoint-brooklyn-new-york": "det:619927453431602",  # wood pole with a terminal box and comm cables
                   "reading-massachusetts": "det:1387761053001946",        # crossarm, streetlight arm, comm lines, terminal box
                   "hardwick-vermont": "det:227228208854685"}              # pole with crossarm against woods, videolog frame


def ms_date(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m") if ms else None


def ms_year(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year if ms else None


def resized(src, dst, px, q=80):
    if dst.exists():
        return True
    if not src or not (ROOT / src).exists():
        return False
    with Image.open(ROOT / src) as im:
        im = im.convert("RGB")
        im.thumbnail((px, px))
        dst.parent.mkdir(parents=True, exist_ok=True)
        im.save(dst, "JPEG", quality=q)
    return True


def load_polygons(slug):
    """detection_id -> polygon rings normalized to the crop box (0..1), from fetch polygon + crop meta."""
    obs_path = DATA / "fetch" / slug / "observations.jsonl"
    out = {}
    if not obs_path.exists():
        return out
    for line in obs_path.open():
        o = json.loads(line)
        meta = DATA / "crops" / f"{o['detection_id']}.json"
        if not meta.exists() or not o.get("polygon_norm"):
            continue
        m = json.loads(meta.read_text())
        W, H = m["source_w"], m["source_h"]
        x0, y0, x1, y1 = m["box"]
        cw, ch = (x1 - x0) or 1, (y1 - y0) or 1
        # every ring: a detection split by an occluder has several, and the first is often not the largest
        out[o["detection_id"]] = {"poly": [[[round((x * W - x0) / cw, 4), round((y * H - y0) / ch, 4)] for x, y in ring] for ring in o["polygon_norm"] if len(ring) >= 3],
                                  "size": m.get("crop_size")}
    return out


def load_marks(slug):
    """detection_id -> model-located positions (normalized to the crop), or {} if the locate pass has not run."""
    p = DATA / "locate" / slug / "locations.jsonl"
    out = {}
    if not p.exists():
        return out
    for line in p.open():
        o = json.loads(line)
        loc = o.get("locations")
        if not loc:
            continue
        pt = lambda q: [round(q["x"], 3), round(q["y"], 3)] if q else None
        out[o["detection_id"]] = {"top": pt(loc.get("pole_top")), "base": pt(loc.get("pole_base")),
                                  "att": [{"p": pt(a), "l": a.get("label", "")} for a in loc.get("attachments", []) if a],
                                  "xfmr": pt(loc.get("transformer")), "xarm": pt(loc.get("crossarm_damage")),
                                  "veg": pt(loc.get("vegetation_contact")), "note": loc.get("notes") or ""}
    return out


def load_osm(slug, utility_ids):
    """(meta, {pole_id: nearest OSM pole distance in m or None}) from osm.py, or (None, {}) when the diff has not run.

    Pole ids are renumbered by every dedupe run, so a match file must cover exactly the current utility ids; otherwise stop."""
    d = DATA / "osm" / slug
    if not (d / "summary.json").exists() or not (d / "matches.jsonl").exists():
        return None, {}
    s = json.loads((d / "summary.json").read_text())
    dist = {m["pole_id"]: m["distance_m"] for m in map(json.loads, (d / "matches.jsonl").open())}
    if set(dist) != set(utility_ids):
        sys.exit(f"{d.relative_to(ROOT)} is stale for the current pole ids (dedupe ran since); rerun osm.py first")
    meta = {"nodes": s["osm_nodes"], "by_tag": s["osm_nodes_by_tag"], "utility": s["detected_utility"], "with": s["detected_with_osm_within_m"],
            "headline_m": s["headline_radius_m"], "not_in_osm": s["detected_without_osm_within_headline"],
            "osm_unmatched": s["osm_without_detected_within_headline"], "fetched_at": (s.get("fetched_at") or "")[:10] or None, "source": str((d / "summary.json").relative_to(ROOT))}
    return meta, dist


def build_records(poles, out_dir, polygons, marks, osm_dist=None):
    rows = []
    for p in poles:
        det = Path(p["best_crop"]).stem if p.get("best_crop") else None  # detection id: stable across dedupe runs, unlike the pole id
        shown_img = f"crops/{det}.jpg" if det and resized(p["best_crop"], out_dir / "crops" / f"{det}.jpg", CROP_PX) else None
        frames = []
        for f in p["frames"]:
            img = f"frames/{f['image_id']}.jpg" if resized(f.get("crop"), out_dir / "frames" / f"{f['image_id']}.jpg", FRAME_PX, 78) else None
            det = Path(f["crop"]).stem if f.get("crop") else None
            mk = marks.get(det)
            if mk:  # markers explain this photo's own flags; the locator was told the assessment and may place items that were only "near"
                mk = dict(mk, veg=mk["veg"] if f["vegetation"] == "touching" else None,
                          xarm=mk["xarm"] if f["crossarm"] == "damaged" else None,
                          xfmr=mk["xfmr"] if f["transformer"] else None)
            pg = polygons.get(det) or {}
            frames.append({"poly": pg.get("poly"), "size": pg.get("size"), "marks": mk,"id": f["image_id"], "date": ms_date(f.get("captured_at")), "ts": f.get("captured_at"), "year": ms_year(f.get("captured_at")),
                           "url": f["url"], "px": f.get("px_h"), "pano": bool(f.get("is_pano")), "seq": f.get("sequence"), "img": img, "by": f.get("creator"),
                           "type": f["pole_type"], "lean": f["lean"], "xarm": f["crossarm"], "veg": f["vegetation"], "xfmr": bool(f["transformer"]),
                           "att": f["attachments"], "conf": f.get("confidence"), "note": f.get("note") or "", "tilt": f.get("tilt"), "shown": bool(f.get("shown"))})
        rows.append({
            "id": p["pole_id"], "lon": p["lon"], "lat": p["lat"], "util": bool(p["is_utility_pole"]), "type": p["pole_type"], "material": p["material"],
            "lean": p["lean_severity"], "xarm": p["crossarm_condition"], "veg": p["vegetation_contact"], "xfmr": bool(p["transformer_present"]),
            "att": p["attachment_count"] if isinstance(p["attachment_count"], int) else None,
            "votes": {"type": p["votes"]["pole_type"], "lean": p["votes"]["lean_severity"], "xarm": p["votes"]["crossarm_condition"],
                      "veg": p["votes"]["vegetation_contact"], "xfmr": p["votes"]["transformer_present"], "att": p["votes"]["attachment_count"]},
            "n": p["n_observations"], "seq": p["n_sequences"], "nfeat": len(p["feature_ids"]), "features": p["feature_ids"],
            "flags": p["condition_flags"], "warn": p.get("warning_flags", []),
            "shown": {"img": shown_img, "date": ms_date(p.get("best_captured_at")), "ts": p.get("best_captured_at"), "year": ms_year(p.get("best_captured_at")),
                      "url": p["best_mapillary_url"], "by": p.get("best_creator"), "px": p.get("best_px_h"), "newest": bool(p.get("best_is_newest"))},
            "latest": {"date": ms_date(p.get("latest_available_at")), "ts": p.get("latest_available_at"), "year": ms_year(p.get("latest_available_at")),
                       "url": p.get("latest_available_url"), "classified": p.get("latest_available_classified")},
            "frames": frames,
            **({"osm": osm_dist.get(p["pole_id"])} if osm_dist is not None and p["is_utility_pole"] else {}),
        })
    return rows


def resolve_example(key, poles):
    """A pole id, or "det:<detection id>" matched against each record's shown photo (its best_crop). None when absent, with a warning."""
    if not key:
        return None
    if key.startswith("det:"):
        det = key[4:]
        for p in poles:
            if p.get("best_crop") and Path(p["best_crop"]).stem == det:
                return p["pole_id"]
        print(f"warning: example {key} not found among shown photos, none will be opened")
        return None
    if key not in {p["pole_id"] for p in poles}:
        print(f"warning: example {key} not in dataset, none will be opened")
        return None
    return key


def bundle_summary(meta, rows, summary, coverage):
    """Small per-bundle stats for the root landing page. Same predicates as the page: dedupe.py condition/warning flags."""
    util = [r for r in rows if r["util"]]
    years = [f["year"] for r in util for f in r["frames"] if f.get("year")]
    multi = sum(1 for r in util if len({f["year"] for f in r["frames"] if f.get("year")}) >= 2)
    osm = meta.get("osm")
    not_in_osm = sum(1 for r in util if not (r.get("osm") is not None and r["osm"] <= osm["headline_m"])) if osm else None  # same rule as predicates.js notInOsm
    # representative date per pole = the photo shown (newest readable assessed photo); one entry per pole, by capture month,
    # so the landing page can compute the share within a window at view time without counting frames twice
    by_month = Counter(r["shown"]["date"] for r in util if r["shown"].get("date"))
    ex = next((r for r in rows if r["id"] == meta.get("example_id")), None)
    exf = next((f for f in ex["frames"] if f.get("shown")), None) if ex else None
    example = None
    if ex and ex["shown"].get("img"):
        mk = (exf or {}).get("marks") or {}
        example = {"id": ex["id"], "crop": ex["shown"]["img"], "date": ex["shown"].get("date"), "ts": ex["shown"].get("ts"), "url": ex["shown"].get("url"), "by": ex["shown"].get("by"),
                   "flags": ex["flags"], "type": ex["type"],
                   "marks": {"top": mk.get("top"), "base": mk.get("base"), "att": [a for a in mk.get("att", []) if a.get("p")][:2], "xfmr": mk.get("xfmr")} if mk else None}
    return {"slug": meta["slug"], "location": meta["location"], "version": meta["version"], "generated": meta["generated"], "example": example,
            "shown_by_month": dict(sorted(by_month.items())), "undated": sum(1 for r in util if not r["shown"].get("date")),
            "counts": {"records": len(rows), "utility": len(util), "condition_issues": sum(1 for r in util if r["flags"]),
                       "warnings": sum(1 for r in util if r["warn"]), "frames_classified": summary["frames_classified"],
                       "photo_year_first": min(years) if years else None, "photo_year_last": max(years) if years else None, "multi_year": multi,
                       "not_in_osm": not_in_osm, "osm_nodes": osm["nodes"] if osm else None},
            "coverage": {"images": coverage["images"], "area_km2": coverage["area_km2"], "capture_first": coverage.get("capture_first"), "capture_last": coverage.get("capture_last")},
            "sources": {"poles": f"data/poles/{meta['slug']}/summary.json", "coverage": f"data/coverage/{meta['slug']}/summary.json", **({"osm": osm["source"]} if osm else {})}}


def write_all_exports(rows, out_dir, attribution):
    osm = lambda r: "" if r.get("osm") is None else r["osm"]
    fc = {"type": "FeatureCollection", "license": "ODbL 1.0", "attribution": attribution,
          "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                        "properties": {"id": r["id"], "is_utility_pole": r["util"], "pole_type": r["type"], "model_flags": r["flags"], "model_watch": r["warn"],
                                       "lean": r["lean"], "crossarm": r["xarm"], "vegetation": r["veg"], "transformer": r["xfmr"],
                                       "attachments_estimate": r["att"], "photos_assessed": r["n"], "capture_sequences": r["seq"],
                                       "photo_shown_date": r["shown"]["date"], "latest_available_photo_date": r["latest"]["date"],
                                       "source_photo_url": r["shown"]["url"], "nearest_osm_pole_m": r.get("osm")}} for r in rows]}
    (out_dir / "poles.geojson").write_text(json.dumps(fc))
    cols = ["id", "lat", "lon", "is_utility_pole", "pole_type", "model_flags", "model_watch", "lean", "crossarm", "vegetation", "transformer",
            "attachments_estimate", "photos_assessed", "capture_sequences", "photo_shown_date", "latest_available_photo_date", "source_photo_url", "nearest_osm_pole_m"]
    with (out_dir / "poles.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            w.writerow([r["id"], r["lat"], r["lon"], r["util"], r["type"], ";".join(r["flags"]), ";".join(r["warn"]), r["lean"], r["xarm"], r["veg"], r["xfmr"],
                        "" if r["att"] is None else r["att"], r["n"], r["seq"], r["shown"]["date"] or "", r["latest"]["date"] or "", r["shown"]["url"], osm(r)])
        w.writerow([])
        w.writerow([attribution])


def validation_blocks(precision):
    notice = "Experimental results. The model's assessments have not been independently verified."
    section = "<p>No completed validation results are published for this demo.</p>"
    if not precision or not precision.get("flags") or not precision.get("graded_poles"):
        return notice, section, "Experimental · not verified"
    labels = {"utility_pole": "Is a utility pole", "lean_slight_or_worse": "Slight lean or worse (watch item or issue)",
              "lean_moderate_or_worse": "Possible lean (moderate or severe)", "lean_severe": "Possible lean (severe)",
              "crossarm_damaged": "Possible crossarm damage", "vegetation_touching": "Possible vegetation contact", "transformer_present": "Transformer visible",
              "attachments_3plus": "3+ estimated attachments", "attachment_count_within_1": "Attachment estimate within 1"}
    trs = []
    for k, t in precision["flags"].items():
        n = t.get("flagged_and_graded", t.get("graded")); tp = t.get("true_positives", t.get("within_1")); pr = t.get("precision")
        trs.append(f"<tr><td>{labels.get(k, k)}</td><td>{n}</td><td>{tp}</td><td>{'' if pr is None else f'{pr:.2f}'}</td></tr>")
    section = (f"<p>{precision['graded_poles']} of {precision['sampled_poles']} sampled records were reviewed by hand against their photos. "
               f"Precision is the share of model flags the reviewer agreed with. Missed flags were not measured at scale.</p>"
               f"<table><thead><tr><th>Flag</th><th>Reviewed</th><th>Agreed</th><th>Precision</th></tr></thead><tbody>{''.join(trs)}</tbody></table>")
    notice = f"Reviewed sample: {precision['graded_poles']} records checked by hand. See <a href=\"#about\">Validation</a>."
    return notice, section, f"{precision['graded_poles']} records reviewed by hand"


def tech_details(summary, coverage, method, tilt_meta=None, osm=None):
    return f"""
<ul>
<li>Coverage: Mapillary vector tiles at zoom 14 for the bounding box {', '.join(f'{v:.4f}' for v in coverage['bbox'])}: {coverage['images']:,} images and {coverage['map_features']:,} map features, of which {coverage['pole_like_features']:,} are pole-like classes. Captures span {coverage['capture_first']} to {coverage['capture_last']} for the whole image pool; the dates shown on records are the dates of the photos actually assessed.</li>
<li>Candidates: the {coverage['features_by_value'].get('object--support--utility-pole', 0):,} map features Mapillary classes as utility poles. Street lights and other classes are not fetched. Mapillary's utility-pole class also includes some street-light and signal poles; the model labels those and the page lists them under other detected objects.</li>
<li>Photos: for each feature, its own detections are ranked by the polygon's area in the frame and the top {method['frames_per_feature']} are fetched ({summary['frames_total']:,} photos). Panoramas at original resolution, other photos at 2048 px.</li>
<li>Crops: the detection polygon's box, widened to 1.5 times the pole width or 0.3 times its height (at least 150 px each side), with 25% headroom above and 5% below, resized to at most 1200 px. Photos where the pole is under {method['min_px_h']} px tall or {method['min_px_w']} px wide are not assessed: {summary['frames_skipped']:,} of {summary['frames_total']:,} photos, leaving {summary['frames_classified']:,} assessed photos on {summary['features_with_classified_frame']:,} features.</li>
<li>Model: {method['model']} through the Batches API, one crop per request, a fixed JSON schema (pole present, pole type, material, lean, crossarm, transformer, vegetation, attachment count, self-rating, note). The self-rating is not calibrated. Notes are free text and are shown only per photo under technical details.</li>
<li>Records: photos of one feature are combined, then features that all lie within {method['radius_m']} m of each other are grouped into one record (no chaining: a hand check of 29 merged Reading records found 7 over-merges, worst in chains of features 25 to 30 m apart, so a merge now needs every pair within the radius) ({summary['records_merged_from_multiple_features']} of {summary['records']} records combine more than one feature). Each field takes the most common value across assessed photos, ties going to the more cautious value; exact vote counts are kept. Photos from one drive are correlated, so agreement across them is not independent verification. {summary['single_frame_records']} records rest on a single photo.</li>
<li>Photo shown: the newest assessed photo where the pole is at least {method['readable_px']} px tall, otherwise the largest. Its capture date is the record's representative date: the date filters, the "within 24 months" switch, ages, and the area's freshness summary all use it, and it can be older than the latest photo available (linked separately). The record's fields combine all assessed photos, which can include older ones than the photo shown; each flag lists the dated photos that show it. The latest available photo, assessed or not, is listed separately.</li>
<li>Positions on photos: a second model pass (same model, one request per assessed photo) was given the crop with a faint labeled grid and the earlier assessment, and asked for the position of the pole top and base, each counted attachment, the transformer, crossarm damage, and vegetation contact. These are approximate model estimates of where something appears in the photo, shown as markers you can hide. They are not measurements and were not verified.</li>
<li>Apparent tilt: for each assessed photo, the angle of Mapillary's pole outline from the image vertical (medial axis of the outline, 12 scanlines, least squares). It is a property of the photo, not a measurement of the pole: camera roll, perspective, and a lean toward or away from the camera all distort it.{f" {'Flat photos' if tilt_meta['kind'] == 'flat' else 'Photos'} the model called straight read a median of {tilt_meta['none_median']}° and up to {tilt_meta['none_p90']}° at the 90th percentile ({tilt_meta['none_n']:,} photos); panoramas read noisier." if tilt_meta else ""}</li>
<li>Push braces: a support pole set at a steep angle against a straight pole is a common Mapillary utility-pole detection and reads as a severe lean. The model labels these as push braces, listed under other detected objects and never flagged for lean. For Greenpoint and Hardwick the label was applied by resending only the photos the first pass had called moderate or severe (schema 2, 2026-09-16); later territories use it throughout.</li>
{f"<li>OpenStreetMap: Overpass returned {osm['nodes']:,} pole nodes (power=pole or man_made=utility_pole) in the same bounding box. {osm['with']['15']:,} of the {osm['utility']:,} utility poles here have one within 15 m ({osm['with']['8']:,} within 8 m, {osm['with']['25']:,} within 25 m), so {osm['not_in_osm']:,} are absent from OSM at 15 m; {osm['osm_unmatched']:,} OSM poles have no detected pole within 15 m (off the photographed streets, or missed). Positions on both sides are approximate.{f" Fetched {osm['fetched_at']}." if osm['fetched_at'] else ""}</li>" if osm else ""}
<li>Possible condition issue: lean moderate or severe, or crossarm damaged, or vegetation touching. Watch item: slight lean, one tier below an issue; it is common ({summary['utility_with_warning_flag']} of {summary['utility_records']} utility poles) and often within the noise of camera angle. Transformers and attachment counts are not condition issues. Attachment count is the number of visible non-electric items the model counted on the pole; it does not identify owners, tenants, or billing status. Coordinates are averaged detection positions, not surveyed.</li>
</ul>"""


def render(template, ctx):
    out = template
    for k, v in ctx.items():
        out = out.replace("{{" + k + "}}", v)
    left = [l for l in out.split("{{")[1:]]
    if left:
        sys.exit(f"unfilled template placeholders: {[l.split('}}')[0] for l in left]}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--town", required=True)
    ap.add_argument("--contact", default=DEFAULT_CONTACT, help="href for the contact button; pass an empty string to omit it")
    ap.add_argument("--example", help="record id opened on first load (desktop); default per territory in DEFAULT_EXAMPLE")
    args = ap.parse_args()
    slug = slugify(args.town)
    pdir = DATA / "poles" / slug
    if not (pdir / "poles.jsonl").exists():
        sys.exit(f"run dedupe.py first, missing {pdir / 'poles.jsonl'}")
    poles = [json.loads(l) for l in (pdir / "poles.jsonl").open()]
    summary = json.loads((pdir / "summary.json").read_text())
    coverage = json.loads((DATA / "coverage" / slug / "summary.json").read_text())
    vpath = DATA / "validate" / slug / "precision.json"
    precision = json.loads(vpath.read_text()) if vpath.exists() else None
    tpath = DATA / "tilt" / slug / "calibration.json"
    tilt_meta = None
    if tpath.exists():  # noise floor of the outline tilt, measured against the model's own "none" calls (tilt.py --calibrate)
        cal = json.loads(tpath.read_text())
        none_by_kind = (cal.get("by_model_lean_call") or {}).get("none", {})
        kind = "flat" if none_by_kind.get("flat") else "all"  # flat photos: panoramas read noisier and would widen the band
        none = none_by_kind.get(kind)
        if none:
            tilt_meta = {"none_median": none["median"], "none_p90": none["p90"], "none_n": none["n"], "kind": kind, "source": str(tpath.relative_to(ROOT))}
    out_dir = OUT / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    for sub in ("crops", "frames"):  # drop images from earlier builds that no current record references (ids renumber; crops were once named by pole id)
        keep = {Path(p["best_crop"]).stem for p in poles if p.get("best_crop")} if sub == "crops" else {f["image_id"] for p in poles for f in p["frames"]}
        for old in (out_dir / sub).glob("*.jpg"):
            if old.stem not in keep:
                old.unlink()
    marks = load_marks(slug)
    osm_meta, osm_dist = load_osm(slug, [p["pole_id"] for p in poles if p["is_utility_pole"]])
    attribution = f"{ATTRIBUTION} {OSM_ATTRIBUTION}" if osm_meta else ATTRIBUTION
    rows = build_records(poles, out_dir, load_polygons(slug), marks, osm_dist if osm_meta else None)
    version = hashlib.sha1(json.dumps([{k: v for k, v in r.items() if k not in ("shown", "frames")} for r in rows], sort_keys=True).encode()).hexdigest()[:8]
    example = resolve_example(args.example or DEFAULT_EXAMPLE.get(slug), poles)
    bbox = coverage["bbox"]
    method = {"radius_m": summary["radius_m"], "readable_px": summary["readable_px"], "min_px_h": 150, "min_px_w": 20,
              "frames_per_feature": 3, "model": "claude-sonnet-5"}
    generated = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    meta = {"slug": slug, "location": "Greenpoint, Brooklyn" if slug.startswith("greenpoint") else args.town, "version": version, "generated": generated,
            "contact": args.contact or None, "example_id": example, "bbox": [[bbox[0], bbox[1]], [bbox[2], bbox[3]]],
            "center": [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2], "method": method, "attribution": attribution, "tilt": tilt_meta, "osm": osm_meta,
            "counts": {"records": len(rows), "utility": sum(r["util"] for r in rows), "frames_classified": summary["frames_classified"]}}
    (out_dir / "data.js").write_text("window.POLE_DATA=" + json.dumps({"meta": meta, "records": rows}, separators=(",", ":")) + ";")
    (out_dir / "summary.json").write_text(json.dumps(bundle_summary(meta, rows, summary, coverage), indent=1))  # read by deploy.sh for the landing page
    write_all_exports(rows, out_dir, attribution)
    for f in ("style.css", "app.js", "predicates.js"):
        shutil.copy(WEB / f, out_dir / f)

    notice, vsection, vshort = validation_blocks(precision)
    contact_nav = f'<a class="btn primary big" href="{args.contact}">Contact Selim</a>' if args.contact else ""
    contact_section = ("<h3>Try another area</h3><p>Have an area in mind? Send me the town or service territory. I'll check the available imagery.</p>"
                       f"<p><a class=\"btn primary\" href=\"{args.contact}\">Ask about another area</a></p>") if args.contact else ""
    html = render((WEB / "index.html").read_text(), {
        "LOCATION": meta["location"], "GENERATED": generated, "VERSION": version, "VALIDATION_NOTICE": notice, "VALIDATION_SHORT": vshort,
        "VALIDATION_SECTION": vsection, "TECH_DETAILS": tech_details(summary, coverage, method, tilt_meta, osm_meta),
        "CONTACT_NAV": contact_nav, "CONTACT_SECTION": contact_section, "ATTRIBUTION": attribution,
        "OSM_NOTE": " The OpenStreetMap comparison only says which poles OSM volunteers have mapped; it is not the utility's own records, and OSM pole coverage is sparse in most towns." if osm_meta else "",
        "BUILD": hashlib.sha1(b"".join((WEB / f).read_bytes() for f in ("style.css", "app.js", "predicates.js")) + version.encode()).hexdigest()[:8],
    })
    (out_dir / "index.html").write_text(html)
    size = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file()) / 1e6
    nframes = len(list((out_dir / "frames").glob("*.jpg")))
    print(f"model-located positions for {len(marks)} of {summary['frames_classified']} assessed photos")
    print(f"{len(rows)} records ({meta['counts']['utility']} utility) -> {out_dir.relative_to(ROOT)}/  {size:.0f} MB, {nframes} frame images, version {version}"
          + ("" if args.contact else "  [no contact configured: contact button omitted]"))
    print(f"open {out_dir / 'index.html'}")


if __name__ == "__main__":
    main()
