#!/usr/bin/env python3
"""Run the whole pipeline for one territory, skipping steps whose outputs exist.

  uv run python3 run.py --town "Norwich, Connecticut" [--frames 3] [--contact mailto:...] [--skip-classify]

Steps: coverage -> fetch -> classify (batch, waits) -> dedupe -> report.
validate.py is deliberately manual: sample, grade by hand, score, then rerun report.
"""
import argparse
import subprocess
import sys

from coverage import DATA, slugify


def step(name, cmd, done):
    if done:
        print(f"[skip] {name}: output exists")
        return
    print(f"[run ] {name}: {' '.join(cmd)}")
    r = subprocess.run([sys.executable] + cmd)
    if r.returncode != 0:
        sys.exit(f"{name} failed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--town", required=True)
    ap.add_argument("--frames", type=int, default=3)
    ap.add_argument("--contact", default="")
    ap.add_argument("--skip-classify", action="store_true", help="stop before spending on the API")
    ap.add_argument("--force", action="store_true", help="rerun every step (caches still prevent refetching)")
    a = ap.parse_args()
    slug = slugify(a.town)
    f = lambda p: (DATA / p).exists() and not a.force
    step("coverage", ["coverage.py", "--town", a.town], f(f"coverage/{slug}/summary.json"))
    step("fetch", ["fetch.py", "--town", a.town, "--frames", str(a.frames)], f(f"fetch/{slug}/observations.jsonl"))
    if a.skip_classify:
        subprocess.run([sys.executable, "classify.py", "--town", a.town, "--estimate"])
        print("stopped before classification (--skip-classify)")
        return
    step("classify", ["classify.py", "--town", a.town], False)  # its own per-detection cache handles reruns
    step("dedupe", ["dedupe.py", "--town", a.town], False)
    step("report", ["report.py", "--town", a.town, "--contact", a.contact], False)


if __name__ == "__main__":
    main()
