#!/usr/bin/env python3
"""Step 6: render the static webapp bundle for one territory.

Usage:
  uv run python3 report.py --town "Greenpoint, Brooklyn, New York" [--contact mailto:you@example.com]

Reads  data/poles/<slug>/{poles.jsonl,summary.json}
       data/coverage/<slug>/summary.json
       data/validate/<slug>/precision.json          (optional; shows "grading in progress" if absent)
       data/crops/<detection_id>.jpg                 (best crop per pole)
Writes out/<slug>/index.html                         self-contained page, MapLibre from CDN
       out/<slug>/data.js                            poles as a JS global (works from file:// and Pages)
       out/<slug>/poles.geojson                      ODbL download
       out/<slug>/worklist.csv                       superintendent list
       out/<slug>/crops/<pole_id>.jpg                640px web crops
"""
import argparse
import csv
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from coverage import DATA, ROOT, slugify

OUT = ROOT / "out"
WEB_CROP = 640
ATTRIBUTION = "Imagery and detections © Mapillary contributors, CC BY-SA 4.0. Derived data ODbL. Basemap © OpenStreetMap contributors."


def ms_date(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%b %Y") if ms else ""


def load(slug):
    pdir = DATA / "poles" / slug
    if not (pdir / "poles.jsonl").exists():
        sys.exit(f"run dedupe.py first, missing {pdir / 'poles.jsonl'}")
    poles = [json.loads(l) for l in (pdir / "poles.jsonl").open()]
    summary = json.loads((pdir / "summary.json").read_text())
    coverage = json.loads((DATA / "coverage" / slug / "summary.json").read_text())
    vpath = DATA / "validate" / slug / "precision.json"
    precision = json.loads(vpath.read_text()) if vpath.exists() else None
    return poles, summary, coverage, precision


def web_rows(poles, slug, out_dir):
    crops_dir = out_dir / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in poles:
        crop_rel = None
        if p.get("best_crop") and (ROOT / p["best_crop"]).exists():
            dst = crops_dir / f"{p['pole_id']}.jpg"
            if not dst.exists():
                with Image.open(ROOT / p["best_crop"]) as im:
                    im = im.convert("RGB")
                    im.thumbnail((WEB_CROP, WEB_CROP))
                    im.save(dst, "JPEG", quality=80)
            crop_rel = f"crops/{p['pole_id']}.jpg"
        rows.append({
            "id": p["pole_id"], "lon": p["lon"], "lat": p["lat"],
            "util": p["is_utility_pole"], "type": p["pole_type"], "mat": p["material"],
            "lean": p["lean_severity"], "xarm": p["crossarm_condition"], "veg": p["vegetation_contact"],
            "xfmr": p["transformer_present"], "att": p["attachment_count"],
            "sev": p["severity_score"], "flags": p["flags"],
            "conf": p["mean_confidence"], "dis": p["disagreement"],
            "n": p["n_observations"], "seq": p["n_sequences"],
            "date": ms_date(p.get("best_captured_at")), "first": ms_date(p.get("capture_first")), "last": ms_date(p.get("capture_last")),
            "url": p["best_mapillary_url"], "by": p.get("best_creator"), "crop": crop_rel,
            "notes": p.get("notes", [])[:1],
        })
    return rows


def write_geojson(rows, path):
    fc = {"type": "FeatureCollection",
          "license": "ODbL 1.0", "attribution": ATTRIBUTION,
          "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                        "properties": {k: v for k, v in r.items() if k not in ("lon", "lat")}} for r in rows]}
    path.write_text(json.dumps(fc))


def write_worklist(rows, path):
    cols = ["pole_id", "lat", "lon", "is_utility_pole", "pole_type", "severity_score", "flags", "attachment_count",
            "lean_severity", "crossarm_condition", "vegetation_contact", "transformer_present",
            "observations", "sequences", "disagreement_lean", "disagreement_attachments", "photo_date", "mapillary_url"]
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in sorted(rows, key=lambda r: (-r["sev"], -r["att"])):
            w.writerow([r["id"], r["lat"], r["lon"], r["util"], r["type"], r["sev"], ";".join(r["flags"]), r["att"],
                        r["lean"], r["xarm"], r["veg"], r["xfmr"], r["n"], r["seq"],
                        r["dis"].get("lean_severity"), r["dis"].get("attachment_count"), r["date"], r["url"]])
        w.writerow([])
        w.writerow([ATTRIBUTION])


def precision_html(precision):
    if not precision:
        return ('<p class="pending">Hand grading of a 50-pole sample is in progress. Until that table exists, '
                'treat every flag on this page as unverified.</p>')
    labels = {"utility_pole": "Is a utility pole", "lean_moderate_or_worse": "Lean, moderate or worse",
              "lean_severe": "Lean, severe", "crossarm_damaged": "Crossarm damaged",
              "vegetation_touching": "Vegetation touching", "transformer_present": "Transformer present",
              "attachments_3plus": "3+ attachments", "attachment_count_within_1": "Attachment count within ±1"}
    trs = []
    for k, t in precision["flags"].items():
        n = t.get("flagged_and_graded", t.get("graded"))
        tp = t.get("true_positives", t.get("within_1"))
        pr = t.get("precision")
        trs.append(f"<tr><td>{labels.get(k, k)}</td><td class=num>{n}</td><td class=num>{tp}</td>"
                   f"<td class=num>{'' if pr is None else f'{pr:.2f}'}</td></tr>")
    return (f'<table class="tbl"><thead><tr><th>Flag</th><th>Graded</th><th>Correct</th><th>Precision</th></tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table>'
            f'<p class="fine">Precision only. {precision["graded_poles"]} poles graded by hand from a stratified sample of {precision["sampled_poles"]}. '
            f'Recall is not measured: a pole the model did not flag was not checked at scale.</p>')


def render(slug, town, rows, summary, coverage, precision, contact):
    util = [r for r in rows if r["util"]]
    n_util = len(util)
    n_att3 = sum(1 for r in util if r["att"] >= 3)
    n_sev = sum(1 for r in util if r["sev"] >= 2)
    n_lean = sum(1 for r in util if r["lean"] in ("moderate", "severe"))
    n_veg = sum(1 for r in util if r["veg"] == "touching")
    n_xarm = sum(1 for r in util if r["xarm"] == "damaged")
    n_xfmr = sum(1 for r in util if r["xfmr"])
    pct = lambda n: f"{100 * n / n_util:.0f}%" if n_util else "–"
    cta = f'<a class="cta" href="{contact}">Want this for your utility? Ask →</a>' if contact else \
          '<span class="cta muted">Request link not configured (report.py --contact)</span>'
    town_short = town.split(",")[0]
    bbox = coverage["bbox"]
    center = [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]
    gen = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pole Pass · {town_short}</title>
<meta name="description" content="Utility pole condition and joint-use attachments in {town_short}, read from public street imagery.">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght,SOFT@9..144,300..900,0..100&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<link href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" rel="stylesheet">
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<style>
:root{{
  --paper:#f3eee4; --paper-2:#eae3d5; --ink:#1b1916; --ink-2:#4a453d; --ink-3:#8a8275; --rule:#d6cdbb;
  --orange:#e0530f; --orange-2:#f3a67a; --teal:#1f6f6b; --ok:#6b8e23; --warn:#c9a227;
  --serif:'Fraunces',Georgia,serif; --mono:'IBM Plex Mono',ui-monospace,Menlo,monospace; --sans:'IBM Plex Sans',system-ui,sans-serif;
}}
*{{box-sizing:border-box}} html,body{{margin:0;height:100%}}
body{{font-family:var(--sans);color:var(--ink);background:var(--paper);font-size:15px;line-height:1.5;
  background-image:radial-gradient(rgba(27,25,22,.045) 1px,transparent 1px);background-size:22px 22px}}
a{{color:var(--teal)}}
.app{{display:grid;grid-template-columns:minmax(380px,44%) 1fr;height:100vh}}
.report{{overflow-y:auto;padding:36px 40px 80px;border-right:1px solid var(--rule);background:linear-gradient(90deg,var(--paper) 0%,var(--paper) 96%,var(--paper-2) 100%)}}
.mapwrap{{position:relative}} #map{{position:absolute;inset:0}}
.eyebrow{{font-family:var(--mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-3);margin:0 0 6px}}
h1{{font-family:var(--serif);font-weight:500;font-size:44px;line-height:1.02;margin:0 0 10px;letter-spacing:-.01em;font-variation-settings:"SOFT" 40,"opsz" 96}}
h1 em{{font-style:italic;color:var(--orange)}}
.lede{{font-size:16px;color:var(--ink-2);max-width:52ch;margin:0 0 28px}}
h2{{font-family:var(--serif);font-weight:500;font-size:22px;margin:38px 0 12px;padding-top:18px;border-top:1px solid var(--rule)}}
.stats{{display:grid;grid-template-columns:repeat(2,1fr);gap:1px;background:var(--rule);border:1px solid var(--rule)}}
.stat{{background:var(--paper);padding:16px 18px}}
.stat .v{{font-family:var(--mono);font-size:34px;font-weight:500;line-height:1;letter-spacing:-.02em}}
.stat .v small{{font-size:15px;color:var(--ink-3);margin-left:6px;letter-spacing:0}}
.stat .l{{font-size:12.5px;color:var(--ink-2);margin-top:8px}}
.stat.hot .v{{color:var(--orange)}} .stat.cool .v{{color:var(--teal)}}
.tbl{{width:100%;border-collapse:collapse;font-size:13.5px}}
.tbl th{{text-align:left;font-family:var(--mono);font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3);font-weight:500;padding:6px 8px;border-bottom:1px solid var(--ink)}}
.tbl td{{padding:7px 8px;border-bottom:1px solid var(--rule)}} .tbl td.num{{font-family:var(--mono);text-align:right}}
.tbl tr:hover td{{background:rgba(224,83,15,.06);cursor:pointer}}
.fine{{font-size:12.5px;color:var(--ink-3)}} .pending{{font-size:14px;color:var(--orange);border-left:3px solid var(--orange);padding:6px 12px;background:rgba(224,83,15,.06)}}
ul.plain{{padding-left:18px;margin:8px 0}} ul.plain li{{margin:4px 0}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 14px}}
.chip{{font-family:var(--mono);font-size:12px;padding:5px 10px;border:1px solid var(--ink);background:transparent;border-radius:999px;cursor:pointer;color:var(--ink)}}
.chip.on{{background:var(--ink);color:var(--paper)}} .chip .n{{color:var(--ink-3)}} .chip.on .n{{color:var(--orange-2)}}
.cta{{display:inline-block;margin-top:14px;padding:14px 20px;background:var(--orange);color:#fff;text-decoration:none;font-family:var(--serif);font-size:20px;border:2px solid var(--ink);box-shadow:4px 4px 0 var(--ink);transition:transform .12s,box-shadow .12s}}
.cta:hover{{transform:translate(-2px,-2px);box-shadow:6px 6px 0 var(--ink)}} .cta.muted{{background:var(--paper-2);color:var(--ink-3);box-shadow:none;border-style:dashed}}
.legend{{position:absolute;left:12px;bottom:28px;background:var(--paper);border:1px solid var(--ink);padding:10px 12px;font-family:var(--mono);font-size:11.5px;line-height:1.7;box-shadow:3px 3px 0 var(--ink)}}
.legend i{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px;vertical-align:-1px;border:1px solid var(--ink)}}
.card{{position:absolute;right:12px;top:12px;width:340px;max-height:calc(100% - 24px);overflow:auto;background:var(--paper);border:1px solid var(--ink);box-shadow:4px 4px 0 var(--ink);display:none}}
.card.show{{display:block;animation:pop .18s ease-out}} @keyframes pop{{from{{transform:translateY(6px);opacity:0}}to{{transform:none;opacity:1}}}}
.card img{{width:100%;display:block;border-bottom:1px solid var(--ink);background:#111}}
.card .body{{padding:12px 14px 14px}} .card .id{{font-family:var(--mono);font-size:11px;color:var(--ink-3)}}
.card h3{{font-family:var(--serif);font-weight:500;font-size:20px;margin:2px 0 8px}}
.kv{{display:grid;grid-template-columns:auto 1fr;gap:3px 12px;font-size:13px}} .kv b{{font-family:var(--mono);font-weight:500;color:var(--ink-3);font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;padding-top:2px}}
.tag{{display:inline-block;font-family:var(--mono);font-size:11px;padding:2px 7px;border:1px solid currentColor;border-radius:3px;margin:0 4px 4px 0}}
.tag.hot{{color:var(--orange)}} .tag.cool{{color:var(--teal)}} .tag.dim{{color:var(--ink-3)}}
.card .x{{position:absolute;top:6px;right:8px;background:var(--paper);border:1px solid var(--ink);width:26px;height:26px;font-family:var(--mono);cursor:pointer}}
.dl{{font-family:var(--mono);font-size:12.5px}} .dl a{{margin-right:14px}}
.reveal{{opacity:0;transform:translateY(8px);animation:up .6s ease-out forwards}} @keyframes up{{to{{opacity:1;transform:none}}}}
@media (max-width:900px){{.app{{grid-template-columns:1fr;height:auto}} .mapwrap{{height:70vh}} .report{{border-right:0;padding:24px 20px 60px}} h1{{font-size:34px}} .card{{width:calc(100% - 24px)}}}}
</style>
</head>
<body>
<div class="app">
<aside class="report">
  <p class="eyebrow reveal">Pole pass · {town_short} · generated {gen}</p>
  <h1 class="reveal" style="animation-delay:.05s">What the street already <em>knows</em> about your poles.</h1>
  <p class="lede reveal" style="animation-delay:.1s">Every utility pole in {town_short} that public street-level imagery can see, with visible condition flags and a count of third-party attachments. No site visit, no drone, no training data. One neighborhood, so anyone can check the work.</p>

  <div class="stats reveal" style="animation-delay:.15s">
    <div class="stat"><div class="v">{n_util:,}</div><div class="l">utility poles seen, after removing {len(rows) - n_util:,} street-light and signal poles the upstream detector lumps in</div></div>
    <div class="stat cool"><div class="v">{n_att3:,}<small>{pct(n_att3)}</small></div><div class="l">carry three or more third-party attachments (telecom, cable, fiber). A joint-use audit shortlist.</div></div>
    <div class="stat hot"><div class="v">{n_sev:,}<small>{pct(n_sev)}</small></div><div class="l">show a high-severity visible condition: severe lean, damaged crossarm, or both</div></div>
    <div class="stat"><div class="v">{coverage['capture_first'][:4]}–{coverage['capture_last'][:4]}</div><div class="l">capture years across {coverage['images']:,} street images and {coverage['pole_like_features']:,} pole-like detections</div></div>
  </div>

  <h2>What this is, and is not</h2>
  <ul class="plain">
    <li>It is a read of <b>public Mapillary imagery</b>: Mapillary's own detector locates poles, a vision model scores each crop, and frames of the same pole vote. Disagreement between frames is shown, not hidden.</li>
    <li>It sees only what a photo sees. <b>No rot, no ground-line decay, no loading.</b> It does not replace an NESC inspection cycle. It tells you where to look first.</li>
    <li>Every point links to its source photo. Every number on this page traces to a file in the pipeline's data folder.</li>
  </ul>

  <h2>How much to trust it</h2>
  {precision_html(precision)}

  <h2>Filter the map</h2>
  <div class="chips" id="chips">
    <button class="chip on" data-f="all">All poles <span class="n">{n_util}</span></button>
    <button class="chip" data-f="att3">3+ attachments <span class="n">{n_att3}</span></button>
    <button class="chip" data-f="lean">Lean mod/severe <span class="n">{n_lean}</span></button>
    <button class="chip" data-f="xarm">Crossarm damaged <span class="n">{n_xarm}</span></button>
    <button class="chip" data-f="veg">Vegetation touching <span class="n">{n_veg}</span></button>
    <button class="chip" data-f="xfmr">Transformer <span class="n">{n_xfmr}</span></button>
    <button class="chip" data-f="nonutil">Not a utility pole <span class="n">{len(rows) - n_util}</span></button>
  </div>

  <h2>Worklist</h2>
  <p class="fine">Top 40 by severity, then attachments. Click a row to fly to it. Full list in the CSV.</p>
  <table class="tbl" id="worklist"><thead><tr><th>Pole</th><th>Flags</th><th>Att.</th><th>Photo</th><th>Agree</th></tr></thead><tbody></tbody></table>
  <p class="dl"><a href="worklist.csv" download>worklist.csv</a><a href="poles.geojson" download>poles.geojson</a></p>

  <h2>Want this for your territory?</h2>
  <p>The pipeline is territory-agnostic. Give me a service area and I will run it and send you the same page, privately, with a hand-graded precision table.</p>
  {cta}

  <h2>Method, briefly</h2>
  <ul class="plain">
    <li>Coverage from Mapillary vector tiles at zoom 14 ({coverage['images']:,} images, {coverage['map_features']:,} detections in the bounding box).</li>
    <li>For each pole detection, the {summary.get('observations_classified', 0):,} frames where the pole appears largest were cropped around Mapillary's own pixel polygon.</li>
    <li>Each crop scored by a vision-language model against a fixed JSON schema. Frames within 8 m merged; majority vote per field; disagreement rate kept.</li>
    <li>Poles under 150 px tall in every frame were not scored ({summary.get('features', 0):,} of {coverage['pole_like_features']:,} pole-like detections were utility-pole class and had a usable frame).</li>
  </ul>
  <p class="fine">{ATTRIBUTION} Built by Kyber.</p>
</aside>
<main class="mapwrap">
  <div id="map"></div>
  <div class="legend"><i style="background:#e0530f"></i>severe<br><i style="background:#f3a67a"></i>moderate<br><i style="background:#1f6f6b"></i>3+ attachments<br><i style="background:#f3eee4"></i>no flag<br><i style="background:#f3eee4;border-style:dashed"></i>not a utility pole</div>
  <div class="card" id="card"><button class="x" onclick="closeCard()">×</button><img id="cimg" alt=""><div class="body" id="cbody"></div></div>
</main>
</div>
<script src="data.js"></script>
<script>
const P = window.POLES;
const map = new maplibregl.Map({{
  container:'map', center:{json.dumps(center)}, zoom:14.6, attributionControl:true,
  style:{{version:8, sources:{{osm:{{type:'raster', tiles:['https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png'], tileSize:256, attribution:'© OpenStreetMap contributors'}}}},
         layers:[{{id:'osm', type:'raster', source:'osm', paint:{{'raster-saturation':-0.85,'raster-brightness-min':0.1,'raster-opacity':0.9}}}}]}}
}});
map.addControl(new maplibregl.NavigationControl({{showCompass:false}}), 'top-left');
const FILTERS = {{
  all: r=>r.util, att3: r=>r.util&&r.att>=3, lean: r=>r.util&&(r.lean==='moderate'||r.lean==='severe'),
  xarm: r=>r.util&&r.xarm==='damaged', veg: r=>r.util&&r.veg==='touching', xfmr: r=>r.util&&r.xfmr, nonutil: r=>!r.util
}};
let active = 'all';
function color(r){{ if(!r.util) return '#f3eee4'; if(r.sev>=2) return '#e0530f'; if(r.sev===1) return '#f3a67a'; if(r.att>=3) return '#1f6f6b'; return '#f3eee4'; }}
function fc(){{ return {{type:'FeatureCollection', features:P.filter(FILTERS[active]).map(r=>({{type:'Feature', geometry:{{type:'Point', coordinates:[r.lon,r.lat]}},
  properties:{{id:r.id, c:color(r), dashed:!r.util, big:(r.sev>=2||r.att>=3)?1:0}}}}))}}; }}
map.on('load', ()=>{{
  map.addSource('poles', {{type:'geojson', data:fc()}});
  map.addLayer({{id:'poles', type:'circle', source:'poles', paint:{{
    'circle-radius':['interpolate',['linear'],['zoom'],13,3,16,['case',['==',['get','big'],1],8,5],18,['case',['==',['get','big'],1],12,7]],
    'circle-color':['get','c'], 'circle-stroke-color':'#1b1916', 'circle-stroke-width':1.2,
    'circle-opacity':['case',['get','dashed'],0.45,0.92]}}}});
  map.on('click','poles', e=>openCard(e.features[0].properties.id));
  map.on('mouseenter','poles', ()=>map.getCanvas().style.cursor='pointer');
  map.on('mouseleave','poles', ()=>map.getCanvas().style.cursor='');
}});
document.getElementById('chips').addEventListener('click', e=>{{
  const b = e.target.closest('.chip'); if(!b) return;
  active = b.dataset.f; document.querySelectorAll('.chip').forEach(c=>c.classList.toggle('on', c===b));
  map.getSource('poles').setData(fc());
}});
const byId = Object.fromEntries(P.map(r=>[r.id,r]));
function tag(t, cls){{ return `<span class="tag ${{cls}}">${{t}}</span>`; }}
function openCard(id){{
  const r = byId[id]; if(!r) return;
  const img = document.getElementById('cimg'); img.src = r.crop||''; img.style.display = r.crop?'block':'none';
  const flags = [];
  if(r.lean==='severe') flags.push(tag('lean: severe','hot')); else if(r.lean==='moderate') flags.push(tag('lean: moderate','hot')); else if(r.lean==='slight') flags.push(tag('lean: slight','dim'));
  if(r.xarm==='damaged') flags.push(tag('crossarm damaged','hot')); else if(r.xarm==='intact') flags.push(tag('crossarm intact','dim'));
  if(r.veg==='touching') flags.push(tag('vegetation touching','hot')); else if(r.veg==='near') flags.push(tag('vegetation near','dim'));
  if(r.xfmr) flags.push(tag('transformer','cool'));
  flags.push(tag(`${{r.att}} attachment${{r.att===1?'':'s'}}`, r.att>=3?'cool':'dim'));
  const agree = Object.entries(r.dis).filter(([k])=>['lean_severity','attachment_count','pole_type'].includes(k)).map(([k,v])=>`${{k.replace('_severity','').replace('_count','').replace('_',' ')}} ${{Math.round((1-v)*100)}}%`).join(' · ');
  document.getElementById('cbody').innerHTML = `
    <div class="id">${{r.id}} · ${{r.lat.toFixed(5)}}, ${{r.lon.toFixed(5)}}</div>
    <h3>${{r.util ? (r.type==='wood_utility'?'Wood utility pole':'Utility pole') : r.type.replace('_',' ')}}</h3>
    <div>${{flags.join('')}}</div>
    <div class="kv" style="margin-top:8px">
      <b>photo</b><span>${{r.date}} · <a href="${{r.url}}" target="_blank" rel="noopener">open on Mapillary ↗</a>${{r.by?` · by ${{r.by}}`:''}}</span>
      <b>frames</b><span>${{r.n}} from ${{r.seq}} sequence${{r.seq===1?'':'s'}}, ${{r.first}}${{r.first!==r.last?' to '+r.last:''}}</span>
      <b>agreement</b><span>${{agree}}</span>
      <b>confidence</b><span>${{r.conf}}</span>
      ${{r.notes&&r.notes[0]?`<b>note</b><span>${{r.notes[0]}}</span>`:''}}
    </div>`;
  document.getElementById('card').classList.add('show');
  map.flyTo({{center:[r.lon,r.lat], zoom:Math.max(map.getZoom(),17), speed:0.8}});
}}
function closeCard(){{ document.getElementById('card').classList.remove('show'); }}
const wl = P.filter(r=>r.util).sort((a,b)=>b.sev-a.sev||b.att-a.att).slice(0,40);
document.querySelector('#worklist tbody').innerHTML = wl.map(r=>`<tr data-id="${{r.id}}"><td class="mono">${{r.id}}</td><td>${{[r.lean==='severe'?'severe lean':r.lean==='moderate'?'moderate lean':null, r.xarm==='damaged'?'crossarm':null, r.veg==='touching'?'vegetation':null, r.xfmr?'transformer':null].filter(Boolean).join(', ')||'—'}}</td><td class="num">${{r.att}}</td><td>${{r.date}}</td><td class="num">${{Math.round((1-r.dis.lean_severity)*100)}}%</td></tr>`).join('');
document.querySelector('#worklist tbody').addEventListener('click', e=>{{ const tr=e.target.closest('tr'); if(tr) openCard(tr.dataset.id); }});
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--town", required=True)
    ap.add_argument("--contact", default="", help="href for the request button, e.g. mailto:you@example.com")
    args = ap.parse_args()
    slug = slugify(args.town)
    poles, summary, coverage, precision = load(slug)
    out_dir = OUT / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = web_rows(poles, slug, out_dir)
    (out_dir / "data.js").write_text("window.POLES=" + json.dumps(rows, separators=(",", ":")) + ";")
    write_geojson(rows, out_dir / "poles.geojson")
    write_worklist(rows, out_dir / "worklist.csv")
    (out_dir / "index.html").write_text(render(slug, args.town, rows, summary, coverage, precision, args.contact))
    size = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file()) / 1e6
    print(f"{len(rows)} poles ({sum(r['util'] for r in rows)} utility) -> {out_dir.relative_to(ROOT)}/  ({size:.1f} MB incl. {len(list((out_dir / 'crops').glob('*.jpg')))} crops)")
    print(f"open {out_dir / 'index.html'}")


if __name__ == "__main__":
    main()
