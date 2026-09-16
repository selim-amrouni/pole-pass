#!/usr/bin/env python3
"""Step 5: hand-graded precision. Do not skip.

Usage:
  uv run python3 validate.py --town "Greenpoint, Brooklyn, New York" --sample 50   # writes the grading CSV
  (grade it in a spreadsheet: fill the truth_* columns, leave blank = not gradable)
  uv run python3 validate.py --town "..." --score                                  # precision table

Reads  data/poles/<slug>/poles.jsonl
Writes data/validate/<slug>/sample.csv        rows to grade, one per pole, with crop path and Mapillary URL
       data/validate/<slug>/precision.json     per-flag precision from the graded CSV
       data/validate/<slug>/precision.md       same, as a table for the report

Sampling is stratified so the flags actually get tested: every pole with a
positive flag (moderate/severe lean, damaged crossarm, touching vegetation,
transformer, 3+ attachments) is eligible in its own stratum, the rest fill
from the unflagged pool. Seeded, so the sample is reproducible.

Precision per flag = graded-true / graded-positive among sampled poles the
model flagged. Recall is not measured (would need grading unflagged poles at
scale); the report says so.
"""
import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

from coverage import DATA, ROOT, slugify

FLAGS = {  # name: (field, predicate on model value)
    "utility_pole": ("is_utility_pole", lambda v: v is True),
    "lean_moderate_or_worse": ("lean_severity", lambda v: v in ("moderate", "severe")),
    "lean_severe": ("lean_severity", lambda v: v == "severe"),
    "crossarm_damaged": ("crossarm_condition", lambda v: v == "damaged"),
    "vegetation_touching": ("vegetation_contact", lambda v: v == "touching"),
    "transformer_present": ("transformer_present", lambda v: v is True),
    "attachments_3plus": ("attachment_count", lambda v: isinstance(v, int) and v >= 3),
    # last on purpose: sample() strata take the first matching flag, so the watch tier only claims poles no issue flag claims
    "lean_slight_or_worse": ("lean_severity", lambda v: v in ("slight", "moderate", "severe")),
}
TRUTH_COLS = ["truth_is_utility_pole", "truth_lean", "truth_crossarm", "truth_vegetation",
              "truth_transformer", "truth_attachment_count", "grader_notes"]
TRUTH_HELP = {
    "truth_is_utility_pole": "y/n",
    "truth_lean": "none/slight/moderate/severe",
    "truth_crossarm": "none_visible/intact/damaged",
    "truth_vegetation": "none/near/touching",
    "truth_transformer": "y/n",
    "truth_attachment_count": "integer",
}


def load_poles(slug):
    p = DATA / "poles" / slug / "poles.jsonl"
    if not p.exists():
        sys.exit(f"run dedupe.py first, missing {p}")
    return [json.loads(l) for l in p.open()]


def sample(slug, n, seed):
    poles = load_poles(slug)
    rng = random.Random(seed)
    strata = defaultdict(list)
    for p in poles:
        hit = [name for name, (f, pred) in FLAGS.items() if name != "utility_pole" and pred(p.get(f))]
        strata[hit[0] if hit else "unflagged"].append(p)
    # round-robin across strata so each flag is represented, then fill from unflagged
    order = [k for k in strata if k != "unflagged"] + ["unflagged"]
    for k in order:
        rng.shuffle(strata[k])
    picked, i = [], 0
    while len(picked) < n and any(strata.values()):
        k = order[i % len(order)]
        if strata[k]:
            picked.append(strata[k].pop())
        i += 1
    out = DATA / "validate" / slug / "sample.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["pole_id", "crop", "mapillary_url", "lat", "lon", "n_observations",
            "model_pole_type", "model_lean", "model_crossarm", "model_vegetation", "model_transformer",
            "model_attachment_count", "model_confidence"] + TRUTH_COLS
    with out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerow(["# how to grade"] + [""] * 12 + [TRUTH_HELP.get(c, "") for c in TRUTH_COLS])
        for p in picked:
            w.writerow([p["pole_id"], p.get("best_crop"), p["best_mapillary_url"], p["lat"], p["lon"], p["n_observations"],
                        p["pole_type"], p["lean_severity"], p["crossarm_condition"], p["vegetation_contact"],
                        p["transformer_present"], p["attachment_count"], p["mean_confidence"]] + [""] * len(TRUTH_COLS))
    strata_count = defaultdict(int)
    for p in picked:
        hit = [name for name, (f, pred) in FLAGS.items() if name != "utility_pole" and pred(p.get(f))]
        strata_count[hit[0] if hit else "unflagged"] += 1
    print(f"wrote {len(picked)} poles -> {out.relative_to(ROOT)}   strata {dict(strata_count)}")
    print("grade the truth_* columns, then run --score")


def truthy(v):
    return str(v).strip().lower() in ("y", "yes", "true", "1")


def score(slug):
    src = DATA / "validate" / slug / "sample.csv"
    if not src.exists():
        sys.exit(f"no sample at {src}, run --sample first")
    rows = [r for r in csv.DictReader(src.open()) if not r["pole_id"].startswith("#")]
    graded = [r for r in rows if any(r[c].strip() for c in TRUTH_COLS if c != "grader_notes")]
    if not graded:
        sys.exit("nothing graded yet")

    def truth_flag(name, r):
        t = {
            "utility_pole": lambda: truthy(r["truth_is_utility_pole"]) if r["truth_is_utility_pole"].strip() else None,
            "lean_slight_or_worse": lambda: r["truth_lean"].strip() in ("slight", "moderate", "severe") if r["truth_lean"].strip() else None,
            "lean_moderate_or_worse": lambda: r["truth_lean"].strip() in ("moderate", "severe") if r["truth_lean"].strip() else None,
            "lean_severe": lambda: r["truth_lean"].strip() == "severe" if r["truth_lean"].strip() else None,
            "crossarm_damaged": lambda: r["truth_crossarm"].strip() == "damaged" if r["truth_crossarm"].strip() else None,
            "vegetation_touching": lambda: r["truth_vegetation"].strip() == "touching" if r["truth_vegetation"].strip() else None,
            "transformer_present": lambda: truthy(r["truth_transformer"]) if r["truth_transformer"].strip() else None,
            "attachments_3plus": lambda: int(r["truth_attachment_count"]) >= 3 if r["truth_attachment_count"].strip() else None,
        }[name]
        return t()

    def model_flag(name, r):
        f, pred = FLAGS[name]
        v = {"is_utility_pole": r["model_pole_type"] in ("wood_utility", "concrete_or_steel_utility"),
             "lean_severity": r["model_lean"], "crossarm_condition": r["model_crossarm"],
             "vegetation_contact": r["model_vegetation"], "transformer_present": r["model_transformer"] == "True",
             "attachment_count": int(r["model_attachment_count"]) if r["model_attachment_count"].strip() else None}[f]
        return pred(v)

    table = {}
    for name in FLAGS:
        pos = [r for r in graded if model_flag(name, r) and truth_flag(name, r) is not None]
        tp = sum(1 for r in pos if truth_flag(name, r))
        neg = [r for r in graded if not model_flag(name, r) and truth_flag(name, r) is not None]
        fn = sum(1 for r in neg if truth_flag(name, r))
        table[name] = {"flagged_and_graded": len(pos), "true_positives": tp,
                       "precision": round(tp / len(pos), 2) if pos else None,
                       "missed_in_sample": fn, "graded_negatives": len(neg)}
    # attachment count within +-1
    att = [(int(r["model_attachment_count"]), int(r["truth_attachment_count"])) for r in graded
           if r["truth_attachment_count"].strip() and r["model_attachment_count"].strip()]
    within1 = sum(1 for m, t in att if abs(m - t) <= 1)
    table["attachment_count_within_1"] = {"graded": len(att), "within_1": within1,
                                          "precision": round(within1 / len(att), 2) if att else None}
    out = DATA / "validate" / slug
    (out / "precision.json").write_text(json.dumps({"graded_poles": len(graded), "sampled_poles": len(rows), "flags": table}, indent=2))
    lines = ["| flag | flagged and graded | true | precision | missed among graded negatives |", "|---|---|---|---|---|"]
    for name, t in table.items():
        if name == "attachment_count_within_1":
            lines.append(f"| attachment count within ±1 | {t['graded']} | {t['within_1']} | {t['precision']} | n/a |")
        else:
            lines.append(f"| {name} | {t['flagged_and_graded']} | {t['true_positives']} | {t['precision']} | {t['missed_in_sample']}/{t['graded_negatives']} |")
    (out / "precision.md").write_text("\n".join(lines) + f"\n\nGraded {len(graded)} of {len(rows)} sampled poles.\n")
    print("\n".join(lines))
    print(f"\ngraded {len(graded)}/{len(rows)}   -> {out.relative_to(ROOT)}/precision.{{json,md}}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--town", required=True)
    ap.add_argument("--sample", type=int, help="write a grading CSV with N poles")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--score", action="store_true", help="compute precision from the graded CSV")
    args = ap.parse_args()
    slug = slugify(args.town)
    if args.sample:
        sample(slug, args.sample, args.seed)
    elif args.score:
        score(slug)
    else:
        ap.error("give --sample N or --score")


if __name__ == "__main__":
    main()
