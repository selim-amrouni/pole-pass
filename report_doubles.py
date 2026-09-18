#!/usr/bin/env python3
"""Phase 3: render the candidate-double-poles page for one territory (Marblehead).

Usage:
  uv run python3 report_doubles.py --town "Marblehead, Massachusetts" [--contact mailto:you@example.com]

This is a separate bundle from the main Pole Pass product (report.py): it publishes candidate
double poles rather than the pole explorer, so it is listed on the landing page with its own
headline count ("candidate double poles") instead of a pole-record count. It reuses report.py's
small helpers
(resized, ms_date, ATTRIBUTION, DEFAULT_CONTACT, render) rather than reimplementing them, but
does not import or modify doubles.py, split.py, roadcover.py, classify.py, or report.py's own
main(). All the double-pole screening/classification work already happened in doubles.py; this
script only reads its output and lays out a page.

Reads  data/doubles/<slug>/candidates.jsonl        (doubles.py)
       data/doubles/<slug>/summary.json            (doubles.py)
       data/doubles/crops/<pair_id>.jpg             (doubles.py; per-pair crop, cached globally)
       data/roadcover/<slug>/summary.json           (roadcover.py; coverage per maintenance half)
       data/split/<slug>/split.geojson              (split.py; maintenance line + half polygons)
       data/coverage/<slug>/summary.json            (coverage.py; bbox, imagery fetch timestamp)
       web/{doubles.html,doubles.css,doubles.js}
Writes out/<slug>/index.html, doubles.css, doubles.js
       out/<slug>/data.js                            window.DOUBLES_DATA = {meta, records}
       out/<slug>/crops/<pair_id>.jpg                 640px thumbnail per pair with a crop
       out/<slug>/candidates.csv, candidates.geojson  all screened pairs, model fields only
       out/<slug>/summary.json                        small bundle summary (deploy.sh checks for this)

Counts, filtering, sorting, and the map are computed in the browser from data.js by doubles.js,
mirroring how report.py leaves counts to predicates.js -- nothing here is baked into the HTML
beyond the page shell and a handful of per-run strings (location, generated date, attribution).
"""
import argparse
import csv
import hashlib
import json
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone

import report
from coverage import DATA, ROOT, slugify

WEB = ROOT / "web"
OUT = ROOT / "out"
CROP_PX = 640

VALIDATION_NOTICE = ("Experimental results. These double-pole calls have not been checked against "
                      "a human-graded sample -- there is no precision table for this page yet, unlike "
                      "the main Pole Pass product.")
VALIDATION_SHORT = "Experimental · not verified"


def load_split_summary(slug):
    """{"line": [[lon,lat],[lon,lat]], "mmld_ring": [...], "verizon_ring": [...], "buffer_m", "note",
    "source"} read directly from split.geojson. Parsed here rather than via split.load_split() to
    avoid pulling in split.py's Overpass/PIL machinery for what is just reading three features
    back out of a GeoJSON file split.py already wrote."""
    path = DATA / "split" / slug / "split.geojson"
    if not path.exists():
        sys.exit(f"run split.py first, missing {path}")
    fc = json.loads(path.read_text())
    polys, line, buffer_m, note, source = {}, None, None, None, None
    for f in fc["features"]:
        geom, props = f["geometry"], f["properties"]
        if geom["type"] == "Polygon":
            polys[props["maintainer"]] = geom["coordinates"][0]
        elif geom["type"] == "LineString":
            line = geom["coordinates"]
        buffer_m = props.get("buffer_m", buffer_m)
        note = props.get("note", note)
        source = props.get("source", source)
    if not line or "MMLD" not in polys or "VERIZON" not in polys:
        sys.exit(f"{path} is missing the split line or a maintenance-half polygon")
    return {"line": line, "mmld_ring": polys["MMLD"], "verizon_ring": polys["VERIZON"],
            "buffer_m": buffer_m, "note": note, "source": source}


def build_records(rows, out_dir):
    """candidates.jsonl rows -> the smaller record shape the page actually needs, with a resized
    thumbnail made per pair that has a crop (resized() skips ones already on disk)."""
    records = []
    for r in rows:
        crop_img = None
        if r.get("crop") and report.resized(r["crop"], out_dir / "crops" / f"{r['pair_id']}.jpg", CROP_PX):
            crop_img = f"crops/{r['pair_id']}.jpg"
        records.append({
            "id": r["pair_id"], "lon": r["lon"], "lat": r["lat"], "distance_m": r["distance_m"],
            "maintainer": r["maintainer"], "street": r.get("street"), "street_distance_m": r.get("street_distance_m"),
            "cross_street": r.get("cross_street"), "cross_street_distance_m": r.get("cross_street_distance_m"),
            "status": r["status"], "n_shared_frames": r.get("n_shared_frames", 0),
            "capture_first": report.ms_date(r.get("capture_first")), "capture_last": report.ms_date(r.get("capture_last")),
            "url": r.get("chosen_mapillary_url"), "crop": crop_img, "result": r.get("result"),
        })
    return records


def write_exports(records, out_dir, attribution):
    """candidates.csv / candidates.geojson: every screened pair, model fields only -- no reviewer
    decisions (those live in the browser; the page's own Export CSV button adds them at export
    time, mirroring poles.csv/poles.geojson vs. app.js's filtered exports in the main product).

    NEVER a street address: a pole sits in the public right of way, but an address attaches a
    double-pole or condition flag to a named person's house for a public-infrastructure item that
    isn't theirs. Only the nearest NAMED ROAD CENTRELINE (street/cross_street, from doubles.py) is
    ever written here or shown on the page -- do not add a reverse geocode.
    """
    cols = ["pair_id", "lat", "lon", "distance_m", "maintainer", "street", "street_distance_m", "cross_street",
            "cross_street_distance_m", "status", "capture_first", "capture_last", "n_shared_frames",
            "chosen_mapillary_url", "two_poles_visible", "likely_duplicate_detection", "is_double_pole",
            "equipment_transferred", "old_pole_lower_attachments_only", "either_pole_cut_short",
            "either_pole_leaning", "poles_at_different_depths", "separation_estimate_m", "confidence", "reason"]
    with (out_dir / "candidates.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in records:
            res = r["result"] or {}
            w.writerow([r["id"], r["lat"], r["lon"], r["distance_m"], r["maintainer"], r["street"] or "",
                        r["street_distance_m"] if r["street_distance_m"] is not None else "", r["cross_street"] or "",
                        r["cross_street_distance_m"] if r["cross_street_distance_m"] is not None else "", r["status"],
                        r["capture_first"] or "", r["capture_last"] or "", r["n_shared_frames"], r["url"] or "",
                        res.get("two_poles_visible", ""), res.get("likely_duplicate_detection", ""), res.get("is_double_pole", ""),
                        res.get("equipment_transferred", ""), res.get("old_pole_lower_attachments_only", ""),
                        res.get("either_pole_cut_short", ""), res.get("either_pole_leaning", ""),
                        res.get("poles_at_different_depths", ""), res.get("separation_estimate_m", ""),
                        res.get("confidence", ""), res.get("reason", "")])
        w.writerow([])
        w.writerow([attribution])
        w.writerow(["Candidate double poles from public Mapillary imagery, ungraded model calls. "
                     "Every row needs checking against MMLD's own records before dispatch."])

    fc = {"type": "FeatureCollection", "license": "ODbL 1.0", "attribution": attribution,
          "note": "Candidate double poles from public Mapillary imagery, ungraded model calls. "
                  "Every row needs checking against MMLD's own records before dispatch.",
          "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                        "properties": {"pair_id": r["id"], "distance_m": r["distance_m"], "maintainer": r["maintainer"],
                                       "street": r["street"], "cross_street": r["cross_street"], "status": r["status"],
                                       "capture_first": r["capture_first"], "capture_last": r["capture_last"],
                                       "chosen_mapillary_url": r["url"], **(r["result"] or {})}}
                       for r in records]}
    (out_dir / "candidates.geojson").write_text(json.dumps(fc))


def landing_stats(records, counts):
    """The extra fields web/landing/landing.js needs to render a card for this bundle.

    The landing page drops any territory without `example.crop` and, by default, labels the headline
    number "pole records". This bundle publishes candidate double poles, not pole records, so it
    declares its own `headline`/`headline_label`; landing.js falls back to the pole wording for every
    other bundle. Every figure here is computed from the candidates, never stated by hand.
    """
    # `records` are the page rows, whose capture dates are already formatted "YYYY-MM" by
    # build_records -- not epoch ms. Do not put them through report.ms_date again.
    dated = [r for r in records if r.get("capture_last")]
    by_month = Counter(r["capture_last"] for r in dated)
    years = [int(r["capture_last"][:4]) for r in dated]
    # The card's photo is the highest-confidence real double that actually has a crop on disk: the
    # one record most worth a reader's first look, chosen by the data rather than pinned by hand.
    doubles = [r for r in records if (r.get("result") or {}).get("is_double_pole") and r.get("crop")]
    best = max(doubles, key=lambda r: r["result"]["confidence"], default=None)
    example = None
    if best:
        example = {"id": best["id"], "crop": best["crop"],
                   "date": best.get("capture_last"), "url": best.get("url"),
                   "flags": ["double_pole"], "type": "double_pole",
                   "marks": {}}  # no locate.py pass for this bundle, so no marked observations
    return {
        "counts": {**counts,
                   "headline": counts.get("is_double_pole", 0),
                   "headline_label": "candidate double poles",
                   "photo_year_first": min(years) if years else None,
                   "photo_year_last": max(years) if years else None},
        "shown_by_month": dict(sorted(by_month.items())),
        "undated": len(records) - len(dated),
        "example": example,
    }


def bundle_summary(meta, records):
    extra = landing_stats(records, meta["counts"])
    return {"slug": meta["slug"], "location": meta["location"], "generated": meta["generated"], "version": meta["version"],
            "note": "Listed territory. This bundle publishes candidate double poles, not the pole explorer.",
            "coverage": meta["coverage"], **extra,
            "sources": {"candidates": f"data/doubles/{meta['slug']}/candidates.jsonl",
                        "doubles_summary": f"data/doubles/{meta['slug']}/summary.json",
                        "roadcover": f"data/roadcover/{meta['slug']}/summary.json",
                        "split": f"data/split/{meta['slug']}/split.geojson",
                        "coverage": f"data/coverage/{meta['slug']}/summary.json"}}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--town", required=True)
    ap.add_argument("--contact", default=report.DEFAULT_CONTACT, help="href for the contact link; pass an empty string to omit it")
    args = ap.parse_args()
    slug = slugify(args.town)

    cand_path = DATA / "doubles" / slug / "candidates.jsonl"
    if not cand_path.exists():
        sys.exit(f"run doubles.py first, missing {cand_path}")
    rows = [json.loads(l) for l in cand_path.open()]
    dsummary = json.loads((DATA / "doubles" / slug / "summary.json").read_text())
    rc_path = DATA / "roadcover" / slug / "summary.json"
    if not rc_path.exists():
        sys.exit(f"run roadcover.py first, missing {rc_path}")
    rcsummary = json.loads(rc_path.read_text())
    cov_path = DATA / "coverage" / slug / "summary.json"
    if not cov_path.exists():
        sys.exit(f"run coverage.py first, missing {cov_path}")
    csummary = json.loads(cov_path.read_text())
    split_summary = load_split_summary(slug)

    out_dir = OUT / slug
    (out_dir / "crops").mkdir(parents=True, exist_ok=True)

    # drop thumbnails from earlier runs that no current candidate references (resized() skips
    # files already on disk, same lesson report.py learned: prune first, or stale crops linger)
    keep = {r["pair_id"] for r in rows if r.get("crop")}
    for old in (out_dir / "crops").glob("*.jpg"):
        if old.stem not in keep:
            old.unlink()

    records = build_records(rows, out_dir)
    version = hashlib.sha1(json.dumps(records, sort_keys=True).encode()).hexdigest()[:8]

    classified = [r for r in records if r["result"]]
    n_double = sum(1 for r in classified if r["result"]["is_double_pole"])
    n_dup = sum(1 for r in classified if r["result"]["likely_duplicate_detection"])
    n_depth = sum(1 for r in classified if r["result"]["poles_at_different_depths"])
    by_maintainer = Counter(r["maintainer"] for r in records)
    by_status = Counter(r["status"] for r in records)

    bbox = csummary["bbox"]
    center = [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]
    cov_by_maint = rcsummary.get("road_coverage_by_maintainer", {})
    verizon_2024 = ((rcsummary.get("road_coverage_by_vintage") or {}).get("2024+") or {}).get("by_maintainer", {}).get("VERIZON")

    generated = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    sc_path = DATA / "doubles" / slug / "spotcheck.json"
    spotcheck = json.loads(sc_path.read_text()) if sc_path.exists() else None

    meta = {
        "slug": slug, "location": args.town, "generated": generated, "version": version, "contact": args.contact or None,
        "attribution": report.ATTRIBUTION,
        "bbox": [[bbox[0], bbox[1]], [bbox[2], bbox[3]]], "center": center,
        "counts": {"total": len(records), "classified": len(classified), "is_double_pole": n_double,
                   "likely_duplicate_detection": n_dup, "poles_at_different_depths": n_depth,
                   "by_status": dict(by_status), "by_maintainer": dict(by_maintainer)},
        "coverage": {"overall": rcsummary.get("road_coverage_overall"),
                     "mmld": cov_by_maint.get("MMLD", {}).get("covered_fraction"),
                     "verizon": cov_by_maint.get("VERIZON", {}).get("covered_fraction"),
                     "capture_first": rcsummary.get("capture_first"), "capture_last": rcsummary.get("capture_last"),
                     "verizon_recent_zero": verizon_2024 == 0},
        "split": {"line": split_summary["line"], "mmld_ring": split_summary["mmld_ring"],
                  "verizon_ring": split_summary["verizon_ring"], "buffer_m": split_summary["buffer_m"],
                  "note": split_summary["note"], "source": split_summary["source"]},
        "methods": {"radius_m": dsummary.get("radius_m"), "frames_kept": dsummary.get("frames_kept"),
                    "coverage_generated_at": (csummary.get("generated_at") or "")[:10] or None},
        # The spot check is deliberately carried onto the page WITH its own disclaimer rather than
        # left in data/. It is the only human-shaped look anyone has taken at these calls, and a
        # reader deciding whether to send a crew out deserves to know both that it happened and how
        # weak it is. It is not a precision table and must never be rendered as one.
        "spotcheck": spotcheck,
        "validation": {"notice": VALIDATION_NOTICE, "short": VALIDATION_SHORT},
    }
    (out_dir / "data.js").write_text("window.DOUBLES_DATA=" + json.dumps({"meta": meta, "records": records}, separators=(",", ":")) + ";")
    write_exports(records, out_dir, report.ATTRIBUTION)
    (out_dir / "summary.json").write_text(json.dumps(bundle_summary(meta, records), indent=1))

    for f in ("doubles.css", "doubles.js"):
        shutil.copy(WEB / f, out_dir / f)

    contact_nav = f'<a class="btn primary" href="{args.contact}">Contact Selim</a>' if args.contact else ""
    build_hash = hashlib.sha1(b"".join((WEB / f).read_bytes() for f in ("doubles.css", "doubles.js")) + version.encode()).hexdigest()[:8]
    html = report.render((WEB / "doubles.html").read_text(), {
        "LOCATION": args.town, "GENERATED": generated, "VERSION": version, "BUILD": build_hash,
        "VALIDATION_NOTICE": VALIDATION_NOTICE, "VALIDATION_SHORT": VALIDATION_SHORT,
        "ATTRIBUTION": report.ATTRIBUTION, "CONTACT_NAV": contact_nav,
    })
    (out_dir / "index.html").write_text(html)

    size = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file()) / 1e6
    pct = lambda f: f"{f * 100:.1f}%" if f is not None else "?"
    m_pct, v_pct, o_pct = pct(cov_by_maint.get("MMLD", {}).get("covered_fraction")), \
        pct(cov_by_maint.get("VERIZON", {}).get("covered_fraction")), pct(rcsummary.get("road_coverage_overall"))

    print(f"{len(records)} candidate pairs ({by_maintainer.get('MMLD', 0)} MMLD, {by_maintainer.get('VERIZON', 0)} Verizon, "
          f"{by_maintainer.get('UNCERTAIN', 0)} uncertain); {len(classified)} assessable, {n_double} called a real double.")
    print(f"-> {out_dir.relative_to(ROOT)}/  {size:.1f} MB, dataset {version}"
          + ("" if args.contact else "  [no contact configured: contact link omitted]"))
    print(f"open {out_dir / 'index.html'}")

    print()
    print("=" * 72)
    print("Paste into an email:")
    print("=" * 72)
    # The unassessed pairs are NOT pending: 'no_shared_frame' means no single photograph shows both
    # poles and 'too_small' means the poles are unreadable at that range. No amount of waiting
    # produces a verdict for them, and calling them "awaiting classification" would have an operator
    # waiting for numbers that are never coming.
    # Keyed on the absence of a result, not on a status string: doubles.py writes "ok" before the
    # model runs and "classified" after, so a status test silently counted every row once it had.
    unassessed = [r for r in records if not r.get("result")]
    by_status = Counter(r.get("status") for r in unassessed)
    # Maintainer counts must be reported for the DOUBLES, not for every screened pair. Printing the
    # screened counts (MMLD 335, Verizon 33) next to "27 called a real double" invites the reader to
    # take 335 as MMLD's double count, which is wrong by more than an order of magnitude.
    dbl_by_maint = Counter(r["maintainer"] for r in records
                           if (r.get("result") or {}).get("is_double_pole"))

    print(f"{n_double} candidate double poles in {args.town}, from {len(records)} pole pairs screened.")
    print(f"  Candidate doubles by maintainer: MMLD {dbl_by_maint.get('MMLD', 0)}, "
          f"Verizon {dbl_by_maint.get('VERIZON', 0)}, "
          f"not attributable (within 150 m of the traced line) {dbl_by_maint.get('UNCERTAIN', 0)}.")
    print(f"  Those {n_double} came from {len(classified)} pairs a model could assess. The other "
          f"{len(unassessed)} could not be assessed at all and are NOT pending: "
          f"{by_status.get('no_shared_frame', 0)} have no single photo showing both poles, "
          f"{by_status.get('too_small', 0)} are too small in frame to read. They stay in the table, unjudged.")
    print(f"  For scale, all {len(records)} screened pairs split MMLD {by_maintainer.get('MMLD', 0)} / "
          f"Verizon {by_maintainer.get('VERIZON', 0)} / uncertain {by_maintainer.get('UNCERTAIN', 0)} — "
          f"that is the screening workload, not a count of doubles.")
    print(f"Imagery date range: {rcsummary.get('capture_first', '?')} to {rcsummary.get('capture_last', '?')}.")
    print(f"Road coverage within 20 m of a photo: MMLD {m_pct}, Verizon {v_pct}, overall {o_pct}.")
    print()
    print("The three biggest reasons this count could be wrong, in both directions:")
    print("1. Coverage. Only 11.6% of Marblehead's road centreline has a photo within 20 m, and the")
    print("   Verizon half has none after 2023. This is a count of what the camera drove past, so it")
    print("   is an undercount, heavily.")
    print("2. Duplicate detections. The upstream detector reports the same pole 1.2-1.4 times on")
    print("   average, so most candidate pairs are one pole seen twice, or two poles further down the")
    print("   same street. The model sorts those out and its error rate is ungraded, which can move")
    print("   the count either way.")
    print("3. Dates. A capture date says when the photo was taken, not when the pole was set or")
    print("   cleared. A double seen in 2023 may already be gone; one created after November 2025")
    print("   cannot appear at all.")


if __name__ == "__main__":
    main()
