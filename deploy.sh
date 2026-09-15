#!/usr/bin/env bash
# Publish out/<slug>/ to the gh-pages branch from a fresh temp repo (no history, nothing deleted).
# Usage: ./deploy.sh greenpoint-brooklyn-new-york
set -euo pipefail
slug="${1:?slug}"
root="$(cd "$(dirname "$0")" && pwd)"
src="$root/out/$slug"
[ -f "$src/index.html" ] || { echo "no bundle at $src, run report.py first"; exit 1; }
remote="$(git -C "$root" remote get-url origin)"
tmp="$(mktemp -d)/pages"
mkdir -p "$tmp"
git -C "$tmp" init -q
git -C "$tmp" checkout -q --orphan gh-pages
cp -R "$src"/. "$tmp"/
touch "$tmp/.nojekyll"
git -C "$tmp" add -A
git -C "$tmp" -c user.name="$(git -C "$root" config user.name)" -c user.email="$(git -C "$root" config user.email)" \
  commit -q -m "deploy $slug $(date -u +%Y-%m-%dT%H:%MZ)"
git -C "$tmp" push -q -f "$remote" gh-pages
echo "deployed $slug to gh-pages ($(du -sh "$tmp" | cut -f1))"
