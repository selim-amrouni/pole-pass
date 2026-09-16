"""Local grading page for the validation sample.

    uv run python3 grade.py --town "Greenpoint, Brooklyn, New York"

Serves data/validate/<slug>/sample.csv at http://127.0.0.1:8766 with the crop, the
other assessed photos of the same record, and the truth fields as buttons. Blind by
default: the model's answers stay hidden until you press "Show model answer".
Every change is written back to sample.csv in place (same columns, help row kept).
Then: validate.py --town ... --score, and report.py to publish the table.

Stdlib only. Localhost only. No API calls.
"""
import argparse
import csv
import json
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from coverage import DATA, ROOT, slugify
from validate import TRUTH_COLS, TRUTH_HELP

CROPS = DATA / "crops"

PAGE = r"""<!doctype html><meta charset="utf-8"><title>Grade sample</title>
<style>
body{margin:0;font:14px/1.45 system-ui,sans-serif;color:#1c1c1a;background:#f4f4f2}
.top{display:flex;gap:14px;align-items:center;padding:8px 16px;background:#fff;border-bottom:1px solid #d8d8d3}
.top b{font-family:ui-monospace,monospace}
.wrap{display:grid;grid-template-columns:minmax(0,1fr) 380px;height:calc(100vh - 46px)}
.photo{background:#111;display:flex;flex-direction:column;min-height:0}
.stage{flex:1;min-height:0;display:flex;align-items:center;justify-content:center}
.stage img{max-width:100%;max-height:100%;object-fit:contain}
.thumbs{display:flex;gap:6px;padding:8px;background:#222;overflow:auto}
.thumbs button{padding:0;border:2px solid transparent;background:none;cursor:pointer}
.thumbs button[aria-pressed=true]{border-color:#8fd3ff}
.thumbs img{width:64px;height:64px;object-fit:cover;display:block}
.thumbs .c{color:#ccc;font-size:11px;display:block;text-align:center;font-family:ui-monospace,monospace}
.form{overflow:auto;padding:12px 16px;background:#fff;border-left:1px solid #d8d8d3}
.f{margin-bottom:12px}.f h4{margin:0 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#6b6b66}
.seg{display:flex;flex-wrap:wrap;gap:4px}
.seg button{border:1px solid #d8d8d3;background:#fff;border-radius:6px;padding:6px 10px;cursor:pointer;font:inherit}
.seg button[aria-pressed=true]{background:#1c1c1a;color:#fff;border-color:#1c1c1a}
.seg button kbd{font-size:10px;color:#999;margin-left:4px}
.seg button[aria-pressed=true] kbd{color:#bbb}
textarea{width:100%;min-height:50px;font:inherit;border:1px solid #d8d8d3;border-radius:6px;padding:6px}
.model{background:#fdf0e8;border:1px solid #efc9b6;border-radius:6px;padding:8px;font-size:12.5px;margin-bottom:12px}
.nav{display:flex;gap:6px;margin-top:8px}.nav button,.top button,.top a{border:1px solid #d8d8d3;background:#fff;border-radius:6px;padding:6px 10px;cursor:pointer;font:inherit;color:inherit;text-decoration:none}
.muted{color:#6b6b66;font-size:12px}.saved{color:#3f6b2a}
</style>
<div class="top"><span>Record <b id="pid"></b></span><span>Progress <b id="prog"></b></span><a id="src" target="_blank" rel="noopener">Mapillary ↗</a>
<button id="reveal" aria-pressed="false">Show model answer</button><span id="status" class="muted"></span></div>
<div class="wrap">
 <div class="photo"><div class="stage"><img id="img" alt=""></div><div class="thumbs" id="thumbs"></div></div>
 <div class="form">
  <div id="model" class="model" hidden></div>
  <div class="f"><h4>Utility pole? <span class="muted">(not a street light or signal pole)</span></h4><div class="seg" data-col="truth_is_utility_pole"></div></div>
  <div class="f"><h4>Lean</h4><div class="seg" data-col="truth_lean"></div></div>
  <div class="f"><h4>Crossarm</h4><div class="seg" data-col="truth_crossarm"></div></div>
  <div class="f"><h4>Vegetation</h4><div class="seg" data-col="truth_vegetation"></div></div>
  <div class="f"><h4>Transformer visible?</h4><div class="seg" data-col="truth_transformer"></div></div>
  <div class="f"><h4>Attachments (non-electric items you can count)</h4><div class="seg" data-col="truth_attachment_count"></div></div>
  <div class="f"><h4>Notes</h4><textarea id="notes"></textarea></div>
  <div class="nav"><button id="prev">← Prev</button><button id="next">Next →</button><button id="skip">Next ungraded</button></div>
  <p class="muted">Keys: lean 1–4 · utility u/n · crossarm q/w/e · vegetation a/s/d · transformer t · attachments [ ] · Enter next · Backspace prev. Leave a field blank if the photos cannot tell.</p>
 </div>
</div>
<script>
const OPTS = {
  truth_is_utility_pole: [['y','Yes','u'],['n','No','n']],
  truth_lean: [['none','None','1'],['slight','Slight','2'],['moderate','Moderate','3'],['severe','Severe','4']],
  truth_crossarm: [['none_visible','None visible','q'],['intact','Intact','w'],['damaged','Damaged','e']],
  truth_vegetation: [['none','None','a'],['near','Near','s'],['touching','Touching','d']],
  truth_transformer: [['y','Yes','t'],['n','No','']],
  truth_attachment_count: ['0','1','2','3','4','5','6','7','8'].map(n => [n, n, '']),
};
let rows = [], frames = {}, i = 0, t;
const $ = id => document.getElementById(id);
const graded = r => TRUTH.some(c => c !== 'grader_notes' && r[c]);
const TRUTH = Object.keys(OPTS).concat(['grader_notes']);
async function load() { const d = await (await fetch('/rows')).json(); rows = d.rows; frames = d.frames; i = Math.max(0, rows.findIndex(r => !graded(r))); if (i < 0) i = 0; render(); }
function render() {
  const r = rows[i]; if (!r) return;
  $('pid').textContent = r.pole_id; $('prog').textContent = `${rows.filter(graded).length} of ${rows.length} graded · row ${i + 1}`;
  $('src').href = r.mapillary_url;
  const fs = frames[r.pole_id] || [];
  const cur = fs.find(f => f.sel) || fs[0];
  $('img').src = cur ? cur.src : ''; $('img').alt = cur ? `Photo ${cur.date}` : 'No photo';
  $('thumbs').innerHTML = fs.map((f, k) => `<button data-k="${k}" aria-pressed="${f === cur}"><img src="${f.src}" alt=""><span class="c">${f.date}${f.sample ? ' ★' : ''}</span></button>`).join('');
  for (const seg of document.querySelectorAll('.seg')) {
    const col = seg.dataset.col;
    seg.innerHTML = OPTS[col].map(([v, label, key]) => `<button data-v="${v}" aria-pressed="${r[col] === v}">${label}${key ? `<kbd>${key}</kbd>` : ''}</button>`).join('');
  }
  if ($('notes').value !== (r.grader_notes || '')) $('notes').value = r.grader_notes || '';
  $('model').hidden = $('reveal').getAttribute('aria-pressed') !== 'true';
  $('model').textContent = `Model: ${r.model_pole_type} · lean ${r.model_lean} · crossarm ${r.model_crossarm} · vegetation ${r.model_vegetation} · transformer ${r.model_transformer} · ${r.model_attachment_count} attachments · self-rating ${r.model_confidence}`;
}
async function set(col, v, toggle = true) {
  const r = rows[i]; r[col] = toggle && r[col] === v ? '' : v; render();
  $('status').textContent = 'saving…';
  const res = await fetch('/save', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ pole_id: r.pole_id, truth: Object.fromEntries(TRUTH.map(c => [c, r[c] || ''])) }) });
  $('status').textContent = res.ok ? 'saved' : 'SAVE FAILED'; $('status').className = res.ok ? 'saved' : '';
}
function go(d) { i = Math.min(rows.length - 1, Math.max(0, i + d)); render(); }
document.addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.dataset.v != null) return set(b.parentElement.dataset.col, b.dataset.v);
  if (b.dataset.k != null) { const fs = frames[rows[i].pole_id]; fs.forEach((f, k) => f.sel = k === +b.dataset.k); return render(); }
  if (b.id === 'prev') go(-1); if (b.id === 'next') go(1);
  if (b.id === 'skip') { const j = rows.findIndex((r, k) => k > i && !graded(r)); if (j >= 0) { i = j; render(); } }
  if (b.id === 'reveal') { b.setAttribute('aria-pressed', String(b.getAttribute('aria-pressed') !== 'true')); render(); }
});
$('notes').addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => set('grader_notes', $('notes').value, false), 500); });
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'TEXTAREA') return;
  if (e.key === 'Enter' || e.key === 'ArrowRight') return go(1);
  if (e.key === 'Backspace' || e.key === 'ArrowLeft') return go(-1);
  const r = rows[i];
  if (e.key === '[' || e.key === ']') { const n = Math.max(0, Math.min(8, (parseInt(r.truth_attachment_count, 10) || 0) + (e.key === ']' ? 1 : -1))); return set('truth_attachment_count', String(n)); }
  for (const col in OPTS) for (const [v, , key] of OPTS[col]) if (key && key === e.key) return set(col, v);
});
load();
</script>"""


def load_sample(path):
    with path.open(newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        rows = [dict(zip(header, r)) for r in reader]
    return header, rows


def write_sample(path, header, rows):
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow([r.get(c, "") for c in header])
    tmp.replace(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--town", required=True)
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    slug = slugify(args.town)
    sample_path = DATA / "validate" / slug / "sample.csv"
    if not sample_path.exists():
        sys.exit(f"no sample at {sample_path}, run validate.py --sample first")
    poles_path = DATA / "poles" / slug / "poles.jsonl"
    poles = {p["pole_id"]: p for p in (json.loads(l) for l in poles_path.open())} if poles_path.exists() else {}
    lock = threading.Lock()

    def frames_for(row):
        p = poles.get(row["pole_id"])
        out = []
        for f in (p["frames"] if p else []):
            crop = f.get("crop")
            if not crop or not (ROOT / crop).exists():
                continue
            date = datetime.fromtimestamp(f["captured_at"] / 1000, tz=timezone.utc).strftime("%Y-%m") if f.get("captured_at") else "?"
            out.append({"src": "/img/" + Path(crop).name, "date": date, "sample": crop == row["crop"], "sel": crop == row["crop"]})
        if not out and row.get("crop") and (ROOT / row["crop"]).exists():
            out.append({"src": "/img/" + Path(row["crop"]).name, "date": "?", "sample": True, "sel": True})
        return out

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
                    header, rows = load_sample(sample_path)
                data = [r for r in rows if not r["pole_id"].startswith("#")]
                return self.send(200, json.dumps({"columns": header, "rows": data, "frames": {r["pole_id"]: frames_for(r) for r in data}}).encode())
            if path.startswith("/img/"):
                name = unquote(path[5:])
                f = CROPS / name
                if "/" in name or ".." in name or not f.is_file():  # crops only, no traversal
                    return self.send(404, b"not found", "text/plain")
                return self.send(200, f.read_bytes(), "image/jpeg")
            self.send(404, b"not found", "text/plain")

        def do_POST(self):
            if urlparse(self.path).path != "/save":
                return self.send(404, b"not found", "text/plain")
            body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0)) or 0) or b"{}")
            truth = body.get("truth") or {}
            if set(truth) - set(TRUTH_COLS):
                return self.send(400, b"unknown column", "text/plain")
            with lock:
                header, rows = load_sample(sample_path)
                hit = [r for r in rows if r["pole_id"] == body.get("pole_id")]
                if not hit:
                    return self.send(404, b"unknown pole_id", "text/plain")
                for c, v in truth.items():
                    hit[0][c] = str(v)
                write_sample(sample_path, header, rows)
            self.send(200, b"{}")

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), H)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"grading {sample_path.relative_to(ROOT)} at {url}  (Ctrl-C to stop)")
    print("help row:", ", ".join(f"{k}={v}" for k, v in TRUTH_HELP.items()))
    if not args.no_open:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
