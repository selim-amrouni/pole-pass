#!/usr/bin/env bash
# Publish one or more out/<slug>/ bundles to the gh-pages branch from a fresh temp repo (no history).
#   ./deploy.sh greenpoint-brooklyn-new-york hardwick-vermont
# Layout on Pages: /<slug>/ per territory, /territories.json (only the slugs deployed, from
# web/territories.json), and a root index.html that forwards to the first slug, keeping #pole= links.
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: $0 <slug> [<slug> ...]"; exit 1; }
root="$(cd "$(dirname "$0")" && pwd)"
for slug in "$@"; do
  [ -f "$root/out/$slug/index.html" ] || { echo "no bundle at out/$slug, run report.py first"; exit 1; }
done
remote="$(git -C "$root" remote get-url origin)"
tmp="$(mktemp -d)/pages"
mkdir -p "$tmp"
for slug in "$@"; do
  mkdir -p "$tmp/$slug"
  cp -R "$root/out/$slug"/. "$tmp/$slug"/
done
python3 - "$root/web/territories.json" "$tmp/territories.json" "$@" <<'PY'
import json, sys
src, dst, *slugs = sys.argv[1:]
known = {t["slug"]: t for t in json.load(open(src))}
missing = [s for s in slugs if s not in known]
if missing:
    sys.exit(f"add to web/territories.json first: {missing}")
json.dump([known[s] for s in slugs], open(dst, "w"), indent=1)
PY
first="$1"
cat > "$tmp/index.html" <<HTML
<!doctype html><meta charset="utf-8"><title>Pole Pass</title>
<meta http-equiv="refresh" content="0; url=$first/">
<script>location.replace("$first/" + location.hash);</script>
<p>Redirecting to <a href="$first/">$first</a>.</p>
HTML
touch "$tmp/.nojekyll"
git -C "$tmp" init -q
git -C "$tmp" checkout -q --orphan gh-pages
git -C "$tmp" add -A
git -C "$tmp" -c user.name="$(git -C "$root" config user.name)" -c user.email="$(git -C "$root" config user.email)" \
  commit -q -m "deploy $* $(date -u +%Y-%m-%dT%H:%MZ)"
git -C "$tmp" -c http.postBuffer=524288000 push -f "$remote" gh-pages
echo "deployed $* to gh-pages ($(du -sh "$tmp" | cut -f1))"
