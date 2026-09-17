#!/usr/bin/env bash
# Publish one or more out/<slug>/ bundles to the gh-pages branch from a fresh temp repo (no history).
#   ./deploy.sh greenpoint-brooklyn-new-york hardwick-vermont
#   ./deploy.sh --dry-run <slug> ...     build the pages tree in a temp dir, print its path, push nothing
#   ./deploy.sh <slug> ... --unlisted <slug> ...
#                                        slugs AFTER --unlisted deploy at /<slug>/ without being listed:
#                                        absent from territories.json, absent from the landing page, and
#                                        added to robots.txt as Disallow. For showing a page to one
#                                        operator before anyone else. Pass the normal slugs too, because:
#   NOTE: this script rebuilds gh-pages from scratch and force-pushes. Whatever you do NOT pass
#         disappears from the live site. A slug that is listed in web/territories.json and has a
#         built bundle but is missing from the command is therefore refused, unless --only is given.
# Layout on Pages: /<slug>/ per territory; /index.html, /landing.css, /landing.js from web/landing/;
# /territories.json = the deployed slugs from web/territories.json (name, kind) plus each bundle's
# summary.json under "stats", which the landing page renders as cards. #pole= links on the root
# are forwarded to the first slug by landing.js.
set -euo pipefail
dry=0; only=0
listed=(); unlisted=(); bucket=listed
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry=1;;
    --only)    only=1;;
    --unlisted) bucket=unlisted;;
    -*) echo "unknown option: $arg"; exit 1;;
    *) if [ "$bucket" = listed ]; then listed+=("$arg"); else unlisted+=("$arg"); fi;;
  esac
done
set -- ${listed[@]+"${listed[@]}"} ${unlisted[@]+"${unlisted[@]}"}
[ $# -ge 1 ] || { echo "usage: $0 [--dry-run] [--only] <slug> ... [--unlisted <slug> ...]"; exit 1; }
root="$(cd "$(dirname "$0")" && pwd)"
for slug in "$@"; do
  for f in index.html summary.json; do
    [ -f "$root/out/$slug/$f" ] || { echo "no $f at out/$slug, run report.py first"; exit 1; }
  done
done
# gh-pages is rebuilt from scratch and force-pushed, so anything not passed vanishes from the live
# site. Refuse to silently drop a territory that is both listed and built.
if [ "$only" = 0 ]; then
  dropped="$(python3 "$root/.deploy_dropped.py" "$root/web/territories.json" "$root/out" "$@")"
  if [ -n "$dropped" ]; then
    echo "refusing: these territories are listed and built but absent from this deploy, and would"
    echo "be removed from the live site:  $dropped"
    echo "pass them too, or pass --only if you really mean to publish just what you named."
    exit 1
  fi
fi

remote="$(git -C "$root" remote get-url origin)"
tmp="$(mktemp -d)/pages"
mkdir -p "$tmp"
for slug in "$@"; do
  mkdir -p "$tmp/$slug"
  cp -R "$root/out/$slug"/. "$tmp/$slug"/
done
cp "$root/web/landing/index.html" "$root/web/landing/landing.css" "$root/web/landing/landing.js" "$tmp"/
cp "$root/web/landing/robots.txt" "$tmp"/robots.txt
for slug in ${unlisted[@]+"${unlisted[@]}"}; do echo "Disallow: /$slug/" >> "$tmp/robots.txt"; done
python3 - "$root/web/territories.json" "$root/out" "$tmp/territories.json" ${listed[@]+"${listed[@]}"} <<'PY'
import json, sys
src, out, dst, *slugs = sys.argv[1:]
# Only LISTED slugs reach here. An unlisted bundle is deliberately absent from territories.json, so
# the landing page has no card for it and nothing links to it.
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
