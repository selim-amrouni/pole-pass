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
# Loud on failure: this script is usually run with its output piped, and a pipeline without
# `pipefail` in the CALLER's shell reports the exit code of `tail`, not of this script. A deploy
# that died mid-push once read as a success because of exactly that.
trap 'status=$?; [ $status -ne 0 ] && { echo; echo "*** DEPLOY FAILED (exit $status) - gh-pages may be unchanged; check: git ls-remote --heads origin gh-pages"; } >&2' EXIT
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
gitc() { git -C "$tmp" -c user.name="$(git -C "$root" config user.name)" -c user.email="$(git -C "$root" config user.email)" "$@"; }
# HTTP/1.1 is forced because GitHub fails large pushes under HTTP/2 with "RPC failed; HTTP 400".
# The temp repo is created fresh each run, so it inherits nothing from the working repo's config.
gitpush() { git -C "$tmp" -c http.postBuffer=524288000 -c http.version=HTTP/1.1 push "$@"; }

# Uploaded one territory per commit to a STAGING ref, then gh-pages is moved in a single ref
# update at the end. Two separate problems force this shape:
#   size  - gh-pages is an orphan branch rebuilt from scratch every deploy, so every byte is a new
#           object. The whole site is ~600 MB of JPEGs and one push of that reliably died with
#           "the remote end hung up unexpectedly". Several ~150 MB pushes go through.
#   order - but pushing those chunks straight at gh-pages publishes a landing page whose territory
#           links 404 until the last chunk lands. Staging keeps the live branch untouched until
#           every object is already on the server; the final push moves the ref and transfers
#           nothing, so the site switches over atomically with no half-built window.
staging="refs/heads/deploy-staging"
stamp="$(date -u +%Y-%m-%dT%H:%MZ)"
for slug in "$@"; do
  git -C "$tmp" add -A -- "$slug"
  gitc commit -q -m "deploy $stamp: $slug"
  echo "uploading $slug ($(du -sh "$tmp/$slug" | cut -f1))..."
  gitpush -f "$remote" "HEAD:$staging"
done
git -C "$tmp" add -A
gitc commit -q -m "deploy $stamp: site shell"
echo "uploading site shell..."
gitpush -f "$remote" "HEAD:$staging"

# Nothing new to transfer here: every object is already on the server from the staged pushes.
echo "switching gh-pages over..."
gitpush -f "$remote" "HEAD:refs/heads/gh-pages"
gitpush "$remote" --delete "$staging" >/dev/null 2>&1 || true
echo "deployed $* to gh-pages ($(du -sh "$tmp" | cut -f1))"
