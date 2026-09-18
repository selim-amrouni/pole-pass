"""Which listed, built territories are missing from a deploy command? Used by deploy.sh to refuse a
force-push that would silently remove them from the live site."""
import json, os, sys

src, out, *passed = sys.argv[1:]
built = [t["slug"] for t in json.load(open(src)) if os.path.exists(os.path.join(out, t["slug"], "index.html"))]
print(" ".join(s for s in built if s not in passed))
