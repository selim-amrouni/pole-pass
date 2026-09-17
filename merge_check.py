#!/usr/bin/env python3
"""Local page to check the 8 m feature merge by eye (issue #5): same pole, or two poles merged?

    uv run python3 merge_check.py --town "Reading, Massachusetts"            # sample 30 merged records, open the page
    uv run python3 merge_check.py --town "..." --sample 50                   # bigger sample (adds to an existing one)
    uv run python3 merge_check.py --town "..." --score                       # over-merge rate from the answers

Each merged record (2+ Mapillary features within the dedupe radius) is shown as one column per
source feature with that feature's crops, its distance from the record's position, and three
buttons: same pole / different poles / unsure. Answers go to data/validate/<slug>/merges.csv in
place. Keyboard: 1 same, 2 different, 3 unsure, j/k next/previous.

Stdlib only. Localhost only. No API calls.
"""
import argparse
import csv
import json
import random
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from coverage import DATA, ROOT, slugify
from dedupe import haversine_m

CROPS = DATA / "crops"
COLS = ["pole_id", "n_features", "feature_ids", "max_pairwise_m", "verdict", "notes", "graded_at"]
VERDICTS = {"same", "different", "unsure", ""}


def merged_records(slug):
    """Records combining 2+ features, with per-feature crops and pairwise distances, from the dedupe and classify caches."""
    rows = [json.loads(l) for l in (DATA / "classify" / slug / "classifications.jsonl").open()]
    by_det = {r["detection_id"]: r for r in rows if r.get("classification") and r.get("crop")}
    out = []
    for p in map(json.loads, (DATA / "poles" / slug / "poles.jsonl").open()):
        if len(p["feature_ids"]) < 2:
            continue
        feats = {}
        for f in p["frames"]:
            det = Path(f["crop"]).stem if f.get("crop") else None
            r = by_det.get(det)
            if not r:
                continue
            fe = feats.setdefault(r["feature_id"], {"feature_id": r["feature_id"], "lon": r["lon"], "lat": r["lat"], "crops": []})
            fe["crops"].append({"src": "/img/" + Path(f["crop"]).name, "px": f.get("px_h") or 0,
                                "date": datetime.fromtimestamp(f["captured_at"] / 1000, tz=timezone.utc).strftime("%Y-%m") if f.get("captured_at") else "?",
                                "type": f.get("pole_type"), "lean": f.get("lean")})
        fl = list(feats.values())
        if len(fl) < 2:
            continue
        for fe in fl:
            fe["crops"].sort(key=lambda c: -c["px"])
            fe["offset_m"] = round(haversine_m(fe["lon"], fe["lat"], p["lon"], p["lat"]), 1)
        pair = max(haversine_m(a["lon"], a["lat"], b["lon"], b["lat"]) for i, a in enumerate(fl) for b in fl[i + 1:])
        out.append({"pole_id": p["pole_id"], "util": bool(p["is_utility_pole"]), "type": p["pole_type"], "n_features": len(fl),
                    "feature_ids": ";".join(sorted(fe["feature_id"] for fe in fl)), "max_pairwise_m": round(pair, 1), "features": fl})
    return out


def load_csv(path):
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLS})
    tmp.replace(path)


def sample(records, existing, n, seed=0):
    """Add up to n records not already in the CSV, random with a fixed seed so reruns are reproducible."""
    have = {r["pole_id"] for r in existing}
    pool = [r for r in records if r["pole_id"] not in have]
    random.Random(seed).shuffle(pool)
    return existing + [{"pole_id": r["pole_id"], "n_features": r["n_features"], "feature_ids": r["feature_ids"],
                        "max_pairwise_m": r["max_pairwise_m"], "verdict": "", "notes": "", "graded_at": ""} for r in pool[:n]]


def score(rows):
    """Over-merge rate among answered rows; unsure counted separately."""
    answered = [r for r in rows if r.get("verdict") in ("same", "different")]
    diff = sum(1 for r in answered if r["verdict"] == "different")
    return {"sampled": len(rows), "answered": len(answered), "unsure": sum(1 for r in rows if r.get("verdict") == "unsure"),
            "same": len(answered) - diff, "different": diff, "over_merge_rate": round(diff / len(answered), 3) if answered else None,
            "different_ids": [r["pole_id"] for r in answered if r["verdict"] == "different"]}


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Merge check</title>
<style>
body{margin:0;font:14px/1.45 system-ui,sans-serif;background:#f4f4f2;color:#1c1c1a}
header{display:flex;gap:14px;align-items:center;padding:8px 16px;background:#fff;border-bottom:1px solid #d8d8d3;position:sticky;top:0}
.mono{font-family:ui-monospace,Menlo,monospace}.muted{color:#6b6b66}
main{padding:14px 16px}.cols{display:flex;gap:12px;overflow-x:auto}
.col{flex:0 0 300px;background:#fff;border:1px solid #d8d8d3;border-radius:8px;padding:10px}
.col h3{margin:0 0 6px;font-size:13px}.col img{max-width:100%;max-height:330px;width:auto;border-radius:4px;display:block;margin:0 auto 4px;background:#eee}
.btns{display:flex;gap:8px;align-items:center}button{font:inherit;padding:8px 14px;border:1px solid #d8d8d3;border-radius:6px;background:#fff;cursor:pointer}
button[aria-pressed=true]{background:#1c1c1a;color:#fff;border-color:#1c1c1a}
button.same[aria-pressed=true]{background:#3f6b2a;border-color:#3f6b2a}button.different[aria-pressed=true]{background:#c2410c;border-color:#c2410c}
input{font:inherit;padding:6px 8px;border:1px solid #d8d8d3;border-radius:6px;width:260px}
.list{display:flex;flex-wrap:wrap;gap:4px;margin-top:14px}.list button{padding:2px 7px;font-size:12px}
.list .d{background:#fdf0e8}.list .s{background:#e9f1e4}.list .u{background:#fbf3df}
</style></head><body>
<header><b>Merge check</b><span id="pos" class="mono"></span>
<div class="btns"><button class="same" data-v="same">1 · Same pole</button><button class="different" data-v="different">2 · Different poles</button><button data-v="unsure">3 · Unsure</button>
<input id="notes" placeholder="notes (optional)"><button id="prev">k · Prev</button><button id="next">j · Next</button></div>
<span id="score" class="muted"></span></header>
<main>
<div id="head"></div><div class="cols" id="cols"></div>
<p class="muted">Keys: 1 same · 2 different · 3 unsure · j/k next/prev. Answers save on click and go to data/validate/&lt;slug&gt;/merges.csv.</p>
<div class="list" id="list"></div>
</main>
<script>
let rows=[],recs={},i=0;
const $=id=>document.getElementById(id);
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function render(){const r=rows[i],m=recs[r.pole_id];if(!r)return;
 $('pos').textContent=`${i+1} / ${rows.length}  ${r.pole_id}`;
 $('head').innerHTML=`<p><b>${esc(r.pole_id)}</b> · ${m.n_features} features merged, farthest pair <span class="mono">${m.max_pairwise_m} m</span> · record type ${esc(m.type)}${m.util?'':' (not a utility pole)'}. Do the columns show the <b>same physical pole</b>?</p>`;
 $('cols').innerHTML=m.features.map((f,k)=>`<div class="col"><h3>Feature ${k+1} <span class="muted mono">${f.offset_m} m from record</span></h3>${f.crops.slice(0,3).map(c=>`<img src="${esc(c.src)}" loading="lazy" alt=""><div class="muted">${esc(c.date)} · ${c.px} px · ${esc(c.type)} · lean ${esc(c.lean)}</div>`).join('')}</div>`).join('');
 document.querySelectorAll('.btns button[data-v]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.v===r.verdict)));
 $('notes').value=r.notes||'';
 $('list').innerHTML=rows.map((x,k)=>`<button data-k="${k}" class="${x.verdict==='different'?'d':x.verdict==='same'?'s':x.verdict==='unsure'?'u':''}" aria-pressed="${k===i}">${k+1}</button>`).join('');
 const a=rows.filter(x=>x.verdict==='same'||x.verdict==='different').length,d=rows.filter(x=>x.verdict==='different').length;
 $('score').textContent=a?`${d} of ${a} answered are over-merges (${(100*d/a).toFixed(0)}%)`:'';}
async function save(v,notes){const r=rows[i];if(v!==undefined)r.verdict=v;if(notes!==undefined)r.notes=notes;
 const res=await fetch('/save',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({pole_id:r.pole_id,verdict:r.verdict,notes:r.notes})});
 if(!res.ok)alert('save failed: '+await res.text());render();}
function go(k){if(k<0||k>=rows.length)return;i=k;render();window.scrollTo(0,0);}
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
 if(b.dataset.v){save(b.dataset.v).then(()=>go(i+1));}else if(b.id==='next')go(i+1);else if(b.id==='prev')go(i-1);else if(b.dataset.k)go(+b.dataset.k);});
document.addEventListener('keydown',e=>{if(e.target===$('notes'))return;const m={'1':'same','2':'different','3':'unsure'}[e.key];
 if(m){save(m).then(()=>go(i+1));}else if(e.key==='j')go(i+1);else if(e.key==='k')go(i-1);});
$('notes').addEventListener('change',e=>save(undefined,e.target.value));
fetch('/rows').then(r=>r.json()).then(d=>{rows=d.rows;recs=d.records;const first=rows.findIndex(r=>!r.verdict);i=first<0?0:first;render();});
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--town", required=True)
    ap.add_argument("--sample", type=int, default=30, help="records to add to the CSV if it has fewer (0 = keep as is)")
    ap.add_argument("--score", action="store_true", help="print the over-merge rate and exit")
    ap.add_argument("--port", type=int, default=8767)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    slug = slugify(args.town)
    for p in (DATA / "poles" / slug / "poles.jsonl", DATA / "classify" / slug / "classifications.jsonl"):
        if not p.exists():
            sys.exit(f"missing {p}")
    csv_path = DATA / "validate" / slug / "merges.csv"
    rows = load_csv(csv_path)
    if args.score:
        s = score(rows)
        print(json.dumps(s, indent=1))
        print(f"-> {csv_path.relative_to(ROOT)}")
        return
    records = merged_records(slug)
    if args.sample and len(rows) < args.sample:
        rows = sample(records, rows, args.sample - len(rows))
        write_csv(csv_path, rows)
    if not rows:
        sys.exit(f"no merged records in {slug}")
    by_id = {r["pole_id"]: r for r in records}
    lock = threading.Lock()
    allowed_origins = {f"http://127.0.0.1:{args.port}", f"http://localhost:{args.port}"}
    print(f"{len(records)} merged records in {slug}; {len(rows)} in the sample, {sum(1 for r in rows if r['verdict'])} answered -> {csv_path.relative_to(ROOT)}")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                return self.send(200, PAGE.encode(), "text/html; charset=utf-8")
            if path == "/rows":
                with lock:
                    cur = load_csv(csv_path)
                return self.send(200, json.dumps({"rows": cur, "records": {r["pole_id"]: by_id[r["pole_id"]] for r in cur if r["pole_id"] in by_id}}).encode())
            if path.startswith("/img/"):
                name = unquote(path[5:])
                f = CROPS / name
                if "/" in name or ".." in name or not f.is_file():
                    return self.send(404, b"not found", "text/plain")
                return self.send(200, f.read_bytes(), "image/jpeg")
            self.send(404, b"not found", "text/plain")

        def do_POST(self):
            if urlparse(self.path).path != "/save":
                return self.send(404, b"not found", "text/plain")
            origin = self.headers.get("Origin")
            if origin not in allowed_origins and not (origin is None and self.headers.get("Sec-Fetch-Site") == "same-origin"):
                return self.send(403, b"cross-origin write refused", "text/plain")
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0)) or 0) or b"{}")
            except ValueError:
                return self.send(400, b"bad json", "text/plain")
            verdict, notes = body.get("verdict", ""), body.get("notes", "")
            if verdict not in VERDICTS or not isinstance(notes, str) or len(notes) > 2000:
                return self.send(400, b"bad value", "text/plain")
            with lock:
                cur = load_csv(csv_path)
                hit = [r for r in cur if r["pole_id"] == body.get("pole_id")]
                if not hit:
                    return self.send(404, b"unknown pole_id", "text/plain")
                hit[0]["verdict"], hit[0]["notes"] = verdict, notes
                hit[0]["graded_at"] = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if verdict else ""
                write_csv(csv_path, cur)
            self.send(200, b"{}")

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), H)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"serving {url}  (Ctrl-C to stop; answers are saved as you click)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
