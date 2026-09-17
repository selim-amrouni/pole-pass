#!/usr/bin/env python3
"""API spend per territory, summed from the cached result files (the only record that survives a rerun).

  uv run python3 costs.py                      # every territory under data/classify and data/locate
  uv run python3 costs.py --town "Hardwick, Vermont"

Each result file stores the usage the API reported and whether it came from a batch (half price).
Files renamed <id>.v1.json by classify.py --redo-lean are superseded results; their cost was still paid,
so they are counted on their own line. Dropped rows have no usage and cost nothing.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

from classify import cost_usd
from coverage import DATA, slugify


def spend(slug):
    rows = []
    for step in ("classify", "locate"):
        d = DATA / step / slug / "results"
        if not d.exists():
            continue
        tot = defaultdict(lambda: {"n": 0, "usd": 0.0})
        for p in d.glob("*.json"):
            r = json.loads(p.read_text())
            if not r.get("usage"):
                continue
            key = (step, "superseded" if p.name.endswith(".v1.json") else "current", "batch" if r.get("batch") else "direct")
            tot[key]["n"] += 1
            tot[key]["usd"] += cost_usd(r["usage"], batch=bool(r.get("batch")))
        rows += [(k, v) for k, v in sorted(tot.items())]
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--town")
    args = ap.parse_args()
    slugs = [slugify(args.town)] if args.town else sorted({p.name for p in (DATA / "classify").iterdir() if p.is_dir()})
    for slug in slugs:
        rows = spend(slug)
        total = sum(v["usd"] for _, v in rows)
        print(f"{slug}: ${total:.2f}")
        for (step, state, mode), v in rows:
            print(f"  {step:9s} {state:10s} {mode:6s} {v['n']:6d} results  ${v['usd']:.2f}")


if __name__ == "__main__":
    main()
