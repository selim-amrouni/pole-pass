#!/usr/bin/env bash
# Publish one or more out/<slug>/ bundles to the gh-pages branch from a fresh temp repo (no history).
#   ./deploy.sh greenpoint-brooklyn-new-york hardwick-vermont
#   ./deploy.sh --dry-run <slug> ...     build the pages tree in a temp dir, print its path, push nothing
# Layout on Pages: /<slug>/ per territory; /index.html, /landing.css, /landing.js from web/landing/;
# /territories.json = the deployed slugs from web/territories.json (name, kind) plus each bundle's
# summary.json under "stats", which the landing page renders as cards. #pole= links on the root
# are forwarded to the first slug by landing.js.
set -euo pipefail
dry=0
if [ "${1:-}" = "--dry-run" ]; then dry=1; shift; fi
[ $# -ge 1 ] || { echo "usage: $0 [--dry-run] <slug> [<slug> ...]"; exit 1; }
root="$(cd "$(dirname "$0")" && pwd)"
for slug in "$@"; do
  for f in index.html summary.json; do
    [ -f "$root/out/$slug/$f" ] || { echo "no $f at out/$slug, run report.py first"; exit 1; }
  done
done
remote="$(git -C "$root" remote get-url origin)"
tmp="$(mktemp -d)/pages"
mkdir -p "$tmp"
for slug in "$@"; do
  mkdir -p "$tmp/$slug"
  cp -R "$root/out/$slug"/. "$tmp/$slug"/
done
cp "$root/web/landing/index.html" "$root/web/landing/landing.css" "$root/web/landing/landing.js" "$tmp"/
python3 - "$root/web/territories.json" "$root/out" "$tmp/territories.json" "$@" <<'PY'
import json, sys
src, out, dst, *slugs = sys.argv[1:]
known = {t["slug"]: t for t in json.load(open(src))}
missing = [s for s in slugs if s not in known]
if missing:
    sys.exit(f"add to web/territories.json first: {missing}")
rows = [dict(known[s], stats=json.load(open(f"{out}/{s}/summary.json"))) for s in slugs]
json.dump(rows, open(dst, "w"), indent=1)
PY
touch "$tmp/.nojekyll"
if [ "$dry" = 1 ]; then
  echo "dry run: pages tree at $tmp ($(du -sh "$tmp" | cut -f1)); preview with: python3 -m http.server -d $tmp 8765"
  exit 0
fi
git -C "$tmp" init -q
git -C "$tmp" checkout -q --orphan gh-pages
git -C "$tmp" add -A
git -C "$tmp" -c user.name="$(git -C "$root" config user.name)" -c user.email="$(git -C "$root" config user.email)" \
  commit -q -m "deploy $* $(date -u +%Y-%m-%dT%H:%MZ)"
git -C "$tmp" -c http.postBuffer=524288000 push -f "$remote" gh-pages
echo "deployed $* to gh-pages ($(du -sh "$tmp" | cut -f1))"
