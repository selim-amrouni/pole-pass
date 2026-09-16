#!/usr/bin/env python3
"""Step 3b: ask the model where things are in each assessed crop.

Usage:
  uv run python3 locate.py --town "Greenpoint, Brooklyn, New York" --limit 4 --mode direct
  uv run python3 locate.py --town "..."                      # Batches API, half price
  uv run python3 locate.py --town "..." --resume <batch_id>

Reads  data/classify/<slug>/classifications.jsonl   (only rows with a classification)
       data/crops/<detection_id>.jpg                 (the same crop the classifier saw)
Writes data/locate/<slug>/results/<detection_id>.json
       data/locate/<slug>/locations.jsonl

Positions are normalized to the crop (x, y in 0..1, origin top left). They are
model estimates of where something appears in the photo, not measurements.
"""
import argparse
import base64
import io
import json
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

from coverage import DATA, ROOT, load_env, slugify
from classify import MODEL, PRICE_IN, PRICE_OUT, BATCH_DISCOUNT, cost_usd

SYSTEM = """You are marking positions in a street-level photo crop that contains one utility pole.
Coordinates are fractions of the image width and height, 0 to 1, origin at the top left. A faint grid
with labels 0.1 to 0.9 is drawn on the image to help you read positions; it is not part of the scene.
An earlier assessment of this same crop is given. Locate the items it reported. If you cannot find
one, leave it out and say so in notes. Do not add items the earlier assessment did not report,
except attachments if you clearly see more of them.

- pole_top, pole_base: the visible top and bottom of the main pole. If the top or bottom is cut off, use the edge where it leaves the frame.
- attachments: one point per distinct NON-electric attachment on the pole: communication cable bundles where they meet the pole, terminal or splice boxes, risers, antennas, cameras. Not the electric conductors, not the streetlight arm, not signs. Give each a short label (2 to 4 words).
- transformer: the center of a can-shaped distribution transformer on the pole, or null.
- crossarm_damage: a point on damaged, broken, or hanging crossarm hardware, or null.
- vegetation_contact: a point where branches or vines touch the conductors or pole top, or null.
- notes: one short sentence only if something would mislead a reader, otherwise empty."""

PT = {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}}, "required": ["x", "y"], "additionalProperties": False}
LPT = {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "label": {"type": "string"}}, "required": ["x", "y", "label"], "additionalProperties": False}
SCHEMA = {
    "type": "object",
    "properties": {
        "pole_top": {"anyOf": [PT, {"type": "null"}]},
        "pole_base": {"anyOf": [PT, {"type": "null"}]},
        "attachments": {"type": "array", "items": LPT},
        "transformer": {"anyOf": [PT, {"type": "null"}]},
        "crossarm_damage": {"anyOf": [PT, {"type": "null"}]},
        "vegetation_contact": {"anyOf": [PT, {"type": "null"}]},
        "notes": {"type": "string"},
    },
    "required": ["pole_top", "pole_base", "attachments", "transformer", "crossarm_damage", "vegetation_contact", "notes"],
    "additionalProperties": False,
}


def grid_image(crop_path):
    """The crop with a faint labeled 0.1 grid, as JPEG bytes. Geometry unchanged."""
    im = Image.open(crop_path).convert("RGB")
    W, H = im.size
    d = ImageDraw.Draw(im, "RGBA")
    for i in range(1, 10):
        x, y = W * i / 10, H * i / 10
        d.line([(x, 0), (x, H)], fill=(255, 255, 0, 90), width=1)
        d.line([(0, y), (W, y)], fill=(255, 255, 0, 90), width=1)
        d.text((x + 2, 2), f"{i/10:.1f}", fill=(255, 255, 0, 220))
        d.text((2, y + 2), f"{i/10:.1f}", fill=(255, 255, 0, 220))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def context_text(c):
    if not c:
        return "No earlier assessment available."
    return (f"Earlier assessment of this crop: pole type {c['pole_type']}; attachments counted {c['attachment_count']}; "
            f"transformer {'present' if c['transformer_present'] else 'not seen'}; crossarm {c['crossarm_condition']}; "
            f"vegetation {c['vegetation_contact']}; lean {c['lean_severity']}.")


def build_request(crop_path, classification=None):
    data = base64.standard_b64encode(grid_image(crop_path)).decode()
    return {
        "model": MODEL, "max_tokens": 700,
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "low"},
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
            {"type": "text", "text": context_text(classification) + " Return the JSON record of positions for this crop."}]}],
    }


def parse(msg):
    if msg.stop_reason == "refusal":
        raise ValueError("refusal")
    text = next((b.text for b in msg.content if b.type == "text"), "")
    rec = json.loads(text)
    # keep points inside the image
    def clamp(p):
        return None if p is None else {**p, "x": min(1, max(0, p["x"])), "y": min(1, max(0, p["y"]))}
    for k in ("pole_top", "pole_base", "transformer", "crossarm_damage", "vegetation_contact"):
        rec[k] = clamp(rec.get(k))
    rec["attachments"] = [clamp(p) for p in rec.get("attachments", [])]
    usage = {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens,
             "cache_read": getattr(msg.usage, "cache_read_input_tokens", 0) or 0,
             "cache_write": getattr(msg.usage, "cache_creation_input_tokens", 0) or 0}
    return rec, usage


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--town", required=True)
    ap.add_argument("--mode", choices=["batch", "direct"], default="batch")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--resume")
    ap.add_argument("--no-wait", action="store_true")
    ap.add_argument("--chunk", type=int, default=2000, help="requests per batch submission")
    args = ap.parse_args()
    slug = slugify(args.town)
    src = DATA / "classify" / slug / "classifications.jsonl"
    rows = [json.loads(l) for l in src.open()]
    rows = [r for r in rows if r.get("classification") and r.get("crop") and (ROOT / r["crop"]).exists()]
    res_dir = DATA / "locate" / slug / "results"
    res_dir.mkdir(parents=True, exist_ok=True)
    todo = [r for r in rows if not (res_dir / f"{r['detection_id']}.json").exists()]
    if args.limit:
        todo = todo[:args.limit]
    cached = sum(1 for r in rows if (res_dir / f"{r['detection_id']}.json").exists())
    print(f"{slug}: {len(rows)} assessed crops, {cached} cached, {len(todo)} to send")
    if todo or args.resume:
        import anthropic
        key = load_env().get("ANTHROPIC_API_KEY", "")
        if not key:
            sys.exit("ANTHROPIC_API_KEY missing from .env")
        client = anthropic.Anthropic(api_key=key)
        total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        if args.mode == "direct" and not args.resume:
            for k, r in enumerate(todo, 1):
                did = r["detection_id"]
                try:
                    rec, usage = parse(client.messages.create(**build_request(ROOT / r["crop"], r["classification"])))
                    for kk in total: total[kk] += usage[kk]
                    (res_dir / f"{did}.json").write_text(json.dumps({"detection_id": did, "model": MODEL, "result": rec, "usage": usage}))
                    print(f"  {k}/{len(todo)} {did} attachments={len(rec['attachments'])} xfmr={bool(rec['transformer'])} xarm={bool(rec['crossarm_damage'])} veg={bool(rec['vegetation_contact'])}")
                except Exception as e:
                    (res_dir / f"{did}.json").write_text(json.dumps({"detection_id": did, "dropped": f"{type(e).__name__}:{str(e)[:160]}"}))
                    print(f"  {k}/{len(todo)} {did} dropped {type(e).__name__}: {str(e)[:120]}")
            print(f"usage {total} cost ${cost_usd(total, batch=False):.3f}")
        else:
            from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
            from anthropic.types.messages.batch_create_params import Request
            bdir = DATA / "locate" / slug / "batches"; bdir.mkdir(parents=True, exist_ok=True)
            # chunked submissions: the API caps one batch at 256 MB, and ~5,000 image requests overflow it
            if args.resume:
                batch_ids = [args.resume]
            else:
                batch_ids = []
                for i in range(0, len(todo), args.chunk):
                    chunk = todo[i:i + args.chunk]
                    reqs = [Request(custom_id=r["detection_id"], params=MessageCreateParamsNonStreaming(**build_request(ROOT / r["crop"], r["classification"]))) for r in chunk]
                    b = client.messages.batches.create(requests=reqs)
                    (bdir / f"{b.id}.json").write_text(json.dumps({"id": b.id, "n": len(reqs), "submitted": time.time()}))
                    print(f"submitted batch {b.id} with {len(reqs)} requests ({i + len(chunk)}/{len(todo)})")
                    batch_ids.append(b.id)
                if args.no_wait:
                    print("collect later: --resume " + " / --resume ".join(batch_ids))
                    return
            n_ok = n_bad = 0
            for batch_id in batch_ids:
              while True:
                b = client.messages.batches.retrieve(batch_id)
                if b.processing_status == "ended":
                    break
                print(f"\r  {batch_id} {b.processing_status}: {b.request_counts.processing} processing", end="", flush=True)
                time.sleep(30)
              for res in client.messages.batches.results(batch_id):
                  did = res.custom_id
                  try:
                      if res.result.type != "succeeded":
                          raise ValueError(f"batch:{res.result.type}")
                      rec, usage = parse(res.result.message)
                      for kk in total: total[kk] += usage[kk]
                      (res_dir / f"{did}.json").write_text(json.dumps({"detection_id": did, "model": MODEL, "result": rec, "usage": usage, "batch": batch_id}))
                      n_ok += 1
                  except Exception as e:
                      (res_dir / f"{did}.json").write_text(json.dumps({"detection_id": did, "dropped": f"{type(e).__name__}:{str(e)[:160]}"}))
                      n_bad += 1
            print(f"\nresults: {n_ok} ok, {n_bad} dropped   usage {total}   cost ${cost_usd(total, batch=True):.3f}")
    out = DATA / "locate" / slug / "locations.jsonl"
    n = 0
    with out.open("w") as fh:
        for r in rows:
            p = res_dir / f"{r['detection_id']}.json"
            if p.exists():
                d = json.loads(p.read_text())
                fh.write(json.dumps({"detection_id": r["detection_id"], "image_id": r["image_id"], "feature_id": r["feature_id"], "locations": d.get("result"), "dropped": d.get("dropped")}) + "\n")
                n += 1
    print(f"wrote {n} rows -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
