#!/usr/bin/env python3
"""Step 3: crop each observation around its pole and classify it with Claude.

Usage:
  uv run python3 classify.py --town "Greenpoint, Brooklyn, New York" --estimate
  uv run python3 classify.py --town "..." --limit 10 --mode direct     # sync, for eyeballing
  uv run python3 classify.py --town "..."                              # Batches API, half price
  uv run python3 classify.py --town "..." --resume <batch_id>          # pick up a submitted batch
  uv run python3 classify.py --town "..." --redo-lean moderate,severe --mode direct   # resend old-schema lean calls

Reads  data/fetch/<slug>/observations.jsonl
Writes data/crops/<detection_id>.jpg                     crop sent to the model
       data/classify/<slug>/results/<detection_id>.json  one result per observation (cache)
       data/classify/<slug>/batches/<batch_id>.json      submitted batch manifest
       data/classify/<slug>/classifications.jsonl        observation + result rows, regenerated

Only uncached observations are sent. Malformed JSON: retry once, then drop
with a reason. Reruns from cache make no API calls.
"""
import argparse
import base64
import json
import sys
import time
from pathlib import Path

from PIL import Image

from coverage import DATA, ROOT, load_env, slugify

MODEL = "claude-sonnet-5"
SCHEMA_VERSION = 2                          # 2: push_brace added to pole_type (2026-09-16); stored in each result file
PRICE_IN, PRICE_OUT = 2.00, 10.00          # $ per 1M tokens, Sonnet 5
BATCH_DISCOUNT = 0.5
MAX_LONG_SIDE = 1200                        # crop resize cap; ~640 image tokens at 1200x400
MIN_POLE_H_PX = 150                         # skip poles shorter than this in the source frame
MIN_POLE_W_PX = 20                          # or thinner than this; attachments are unreadable below

SYSTEM = """You are inspecting street-level photographs of utility poles for a small electric utility.
Each image is a crop around one pole detected by an upstream model. Answer only from what is
visible. Do not infer structural condition that a photo cannot show (rot, ground-line decay,
loading). When something is not visible or the image is too blurry, say unclear rather than guess.

Definitions:
- pole_present: a vertical pole is the main subject of the crop.
- pole_type: wood_utility = wooden pole carrying electric or communication lines;
  concrete_or_steel_utility = same role, concrete or steel; street_light = pole whose only job is a
  luminaire (often ornamental cast iron in New York); traffic_signal = signal or sign mast;
  push_brace = a shorter pole set on purpose at a steep angle against a straight utility pole to
  hold it up (also called a prop or stub brace); its angle is by design, so when the crop's main
  subject is the brace report push_brace and lean_severity unclear, and do not call the braced
  pole leaning because of the brace; other = flagpole, fence post, sign post, etc.
- attachment_count: number of distinct NON-electric attachments on the pole: communication cable
  bundles (telecom, cable TV, fiber), their terminal boxes, splice enclosures, risers, antennas,
  cameras. Exclude the electric conductors, the streetlight arm, and signs. Count what you can see.
- crossarm_condition: intact, damaged (broken, split, hanging, missing insulator with exposed
  wire), none_visible, or unclear.
- lean_severity: none (vertical), slight (noticeable but under about 5 degrees), moderate
  (5 to 15 degrees), severe (over 15 degrees or visibly unstable). Judge against nearby
  verticals such as building edges, not against the frame edge, since panoramas distort.
- vegetation_contact: touching (branches or vines in contact with conductors or the pole top),
  near (within about a meter), none, unclear.
- transformer_present: a can-shaped distribution transformer mounted on the pole.
- confidence: your overall confidence in this record, 0 to 1.
- notes: at most one sentence, only for something a line crew would want to know."""

SCHEMA = {
    "type": "object",
    "properties": {
        "pole_present": {"type": "boolean"},
        "pole_type": {"type": "string", "enum": ["wood_utility", "concrete_or_steel_utility", "street_light", "traffic_signal", "push_brace", "other", "unclear"]},
        "material": {"type": "string", "enum": ["wood", "concrete", "steel", "fiberglass", "unclear"]},
        "lean_severity": {"type": "string", "enum": ["none", "slight", "moderate", "severe", "unclear"]},
        "crossarm_condition": {"type": "string", "enum": ["none_visible", "intact", "damaged", "unclear"]},
        "transformer_present": {"type": "boolean"},
        "vegetation_contact": {"type": "string", "enum": ["none", "near", "touching", "unclear"]},
        "attachment_count": {"type": "integer"},
        "confidence": {"type": "number"},
        "notes": {"type": "string"},
    },
    "required": ["pole_present", "pole_type", "material", "lean_severity", "crossarm_condition",
                 "transformer_present", "vegetation_contact", "attachment_count", "confidence", "notes"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- crops
def needs_redo(cached, leans):
    """True when a cached result should be resent: its lean call is in `leans` and it predates SCHEMA_VERSION."""
    if not leans or not cached.get("result"):
        return False
    return cached["result"].get("lean_severity") in leans and cached.get("schema", 1) < SCHEMA_VERSION


def supersede(p, cached):
    """Move a result aside as <id>.v<schema>.json before its rerun; every generation is kept."""
    p.rename(p.with_suffix(f".v{cached.get('schema', 1)}.json"))


def write_drop(res_dir, did, err):
    """Record a dropped observation. A rerun that fails restores the superseded result instead of losing it to a drop."""
    p = res_dir / f"{did}.json"
    old = sorted(res_dir.glob(f"{did}.v*.json"))
    if old:
        old[-1].rename(p)
        return "restored"
    p.write_text(json.dumps({"detection_id": did, "dropped": err}))
    return "dropped"


def make_crop(obs, crops_dir):
    """Crop around the pole bbox with context, resize, save. Returns (path, (w,h)) or (None, reason)."""
    out = crops_dir / f"{obs['detection_id']}.jpg"
    meta = out.with_suffix(".json")
    if out.exists() and meta.exists():
        with Image.open(out) as im:
            return out, im.size
    if not obs.get("thumb"):
        return None, "no_thumbnail"
    with Image.open(ROOT / obs["thumb"]) as im:
        W, H = im.size
        x0, y0, x1, y1 = obs["bbox_norm"]
        x0, x1, y0, y1 = x0 * W, x1 * W, y0 * H, y1 * H
        pw, ph = x1 - x0, y1 - y0
        if ph < MIN_POLE_H_PX or pw < MIN_POLE_W_PX:
            return None, f"pole_too_small_{pw:.0f}x{ph:.0f}px"
        # context: wide enough to see crossarms and attachments, more headroom than footroom
        half_w = max(1.5 * pw, 0.3 * ph, 150)
        cx = (x0 + x1) / 2
        box = (int(max(0, cx - half_w)), int(max(0, y0 - 0.25 * ph)),
               int(min(W, cx + half_w)), int(min(H, y1 + 0.05 * ph)))
        crop = im.crop(box).convert("RGB")
        crop.thumbnail((MAX_LONG_SIDE, MAX_LONG_SIDE))
        crops_dir.mkdir(parents=True, exist_ok=True)
        crop.save(out, "JPEG", quality=85)
        meta.write_text(json.dumps({"pole_px_w": round(pw), "pole_px_h": round(ph), "source_w": W, "source_h": H, "box": box, "crop_size": crop.size}))
        return out, crop.size


def image_tokens(size):
    w, h = size
    return int(w * h / 750)


def build_request(obs, crop_path):
    data = base64.standard_b64encode(crop_path.read_bytes()).decode()
    when = time.strftime("%Y-%m", time.gmtime(obs["captured_at"] / 1000)) if obs.get("captured_at") else "unknown"
    context = (f"Crop from a {'360 panorama' if obs['is_pano'] else 'standard photo'} taken {when}, "
               f"camera about {obs['distance_m']} m from the pole. Return the JSON record.")
    return {
        "model": MODEL,
        "max_tokens": 600,
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "low"},
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
            {"type": "text", "text": context},
        ]}],
    }


def parse_result(msg):
    """Return (record, usage) or (None, usage). Raises ValueError on malformed JSON."""
    if msg.stop_reason == "refusal":
        raise ValueError("refusal")
    text = next((b.text for b in msg.content if b.type == "text"), "")
    rec = json.loads(text)
    missing = [k for k in SCHEMA["required"] if k not in rec]
    if missing:
        raise ValueError(f"missing {missing}")
    usage = {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens,
             "cache_read": getattr(msg.usage, "cache_read_input_tokens", 0) or 0,
             "cache_write": getattr(msg.usage, "cache_creation_input_tokens", 0) or 0}
    return rec, usage


def cost_usd(usage, batch):
    inp = usage["input"] + usage["cache_write"] * 1.25 + usage["cache_read"] * 0.1
    c = inp / 1e6 * PRICE_IN + usage["output"] / 1e6 * PRICE_OUT
    return c * (BATCH_DISCOUNT if batch else 1)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--town", required=True)
    ap.add_argument("--mode", choices=["batch", "direct"], default="batch")
    ap.add_argument("--limit", type=int, help="only the first N uncached observations")
    ap.add_argument("--estimate", action="store_true", help="make crops, print expected cost, send nothing")
    ap.add_argument("--resume", help="batch id to collect instead of submitting")
    ap.add_argument("--no-wait", action="store_true", help="submit the batch and exit; collect later with --resume")
    ap.add_argument("--chunk", type=int, default=BATCH_CHUNK, help="requests per batch submission")
    ap.add_argument("--redo-lean", metavar="CALLS", help="comma list, e.g. moderate,severe: resend cached results with these lean calls "
                    "that predate the current schema (old result kept as <id>.v1.json)")
    args = ap.parse_args()
    redo_leans = {x.strip() for x in args.redo_lean.split(",") if x.strip()} if args.redo_lean and not args.resume else set()

    slug = slugify(args.town)
    src = DATA / "fetch" / slug / "observations.jsonl"
    if not src.exists():
        sys.exit(f"run fetch.py first, missing {src}")
    obs_all = [json.loads(l) for l in src.open()]
    res_dir = DATA / "classify" / slug / "results"
    crops_dir = DATA / "crops"
    res_dir.mkdir(parents=True, exist_ok=True)

    # crops + skip list. Two Mapillary features can share a detection (duplicate features a few meters apart);
    # one request per detection id, and write_output gives every observation row the cached result.
    todo, skipped, sizes, redo, seen = [], {}, {}, {}, set()
    for o in obs_all:
        if o["detection_id"] in seen:
            continue
        seen.add(o["detection_id"])
        p = res_dir / f"{o['detection_id']}.json"
        if p.exists():
            cached = json.loads(p.read_text())
            if cached.get("dropped"):
                skipped[o["detection_id"]] = "cached_drop"
                continue
            if not needs_redo(cached, redo_leans):
                continue
            redo[o["detection_id"]] = cached
        path, info = make_crop(o, crops_dir)
        if path is None:
            skipped[o["detection_id"]] = info
            continue
        todo.append((o, path))
        sizes[o["detection_id"]] = info
    if args.limit:
        todo = todo[:args.limit]
    if not args.estimate:  # only what is actually being sent is moved aside, and only once the list is final
        for o, _ in todo:
            if o["detection_id"] in redo:
                supersede(res_dir / f"{o['detection_id']}.json", redo[o["detection_id"]])
    cached_n = sum(1 for o in obs_all if (res_dir / f"{o['detection_id']}.json").exists())
    feats_all = {o["feature_id"] for o in obs_all}
    feats_usable = {o["feature_id"] for o in obs_all if o["detection_id"] not in skipped}
    new_skips = {k: v for k, v in skipped.items() if v != "cached_drop"}
    print(f"{slug}: {len(obs_all)} observations, {cached_n} cached ({len(skipped) - len(new_skips)} of them dropped), "
          f"{len(new_skips)} newly skipped, {len(todo)} to send")
    print(f"  features: {len(feats_all)} total, {len(feats_usable)} with >=1 usable frame, {len(feats_all) - len(feats_usable)} with none")
    if new_skips:
        from collections import Counter
        reasons = Counter(v.rsplit('_', 1)[0] if v.startswith('pole_too_small') else v for v in new_skips.values())
        print(f"  skip reasons: {dict(reasons)}")
    est_in = sum(image_tokens(sizes[o["detection_id"]]) + 180 for o, _ in todo)  # image + context + cached system share
    est_out = len(todo) * 150
    est = (est_in / 1e6 * PRICE_IN + est_out / 1e6 * PRICE_OUT)
    print(f"  estimate: ~{est_in:,} input + ~{est_out:,} output tokens -> ${est:.2f} direct, ${est*BATCH_DISCOUNT:.2f} batched ({MODEL})")
    for did, reason in skipped.items():
        p = res_dir / f"{did}.json"
        if not p.exists():
            p.write_text(json.dumps({"detection_id": did, "dropped": reason}))
    if args.estimate or (not todo and not args.resume):
        write_output(slug, obs_all, res_dir, crops_dir)
        return

    import anthropic
    key = load_env().get("ANTHROPIC_API_KEY", "")
    if not key:
        sys.exit("ANTHROPIC_API_KEY missing from .env. See .env.example.")
    client = anthropic.Anthropic(api_key=key)

    if args.mode == "direct" and not args.resume:
        run_direct(client, todo, res_dir)
    else:
        run_batch(client, todo, res_dir, slug, args)
    write_output(slug, obs_all, res_dir, crops_dir)


def run_direct(client, todo, res_dir):
    import anthropic
    total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    for k, (o, path) in enumerate(todo, 1):
        req = build_request(o, path)
        rec, err = None, None
        for attempt in range(2):
            try:
                msg = client.messages.create(**req)
                rec, usage = parse_result(msg)
                break
            except (ValueError, json.JSONDecodeError) as e:
                err = f"malformed:{e}"
            except anthropic.APIStatusError as e:
                err = f"api:{e.status_code}:{e.message[:160]}"
                if e.status_code < 500 and e.status_code != 429:
                    break
                time.sleep(2 ** attempt)
        if rec is None:
            what = write_drop(res_dir, o["detection_id"], err)
            print(f"  {k}/{len(todo)} {o['detection_id']} {what} ({err})")
            continue
        for kk in total:
            total[kk] += usage[kk]
        (res_dir / f"{o['detection_id']}.json").write_text(json.dumps({"detection_id": o["detection_id"], "model": MODEL, "schema": SCHEMA_VERSION, "result": rec, "usage": usage}))
        print(f"  {k}/{len(todo)} {o['detection_id']} {rec['pole_type']:>26} att={rec['attachment_count']} lean={rec['lean_severity']} "
              f"xarm={rec['crossarm_condition']} veg={rec['vegetation_contact']} conf={rec['confidence']:.2f}")
    print(f"usage {total}  cost ${cost_usd(total, batch=False):.3f}")


BATCH_CHUNK = 2000  # requests per batch; the API caps a submission at 256 MB and ~5,000 image requests overflow it


def run_batch(client, todo, res_dir, slug, args):
    """Submit `todo` in chunks (all at once, so they process in parallel), then wait for and collect each."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    bdir = DATA / "classify" / slug / "batches"
    bdir.mkdir(parents=True, exist_ok=True)
    if args.resume:
        batches = [(args.resume, todo)]
    else:
        batches = []
        for i in range(0, len(todo), args.chunk):
            chunk = todo[i:i + args.chunk]
            reqs = [Request(custom_id=o["detection_id"], params=MessageCreateParamsNonStreaming(**build_request(o, p))) for o, p in chunk]
            batch = client.messages.batches.create(requests=reqs)
            (bdir / f"{batch.id}.json").write_text(json.dumps({"id": batch.id, "submitted": time.time(), "n": len(reqs),
                                                                "detection_ids": [o["detection_id"] for o, _ in chunk]}))
            print(f"submitted batch {batch.id} with {len(reqs)} requests ({i + len(chunk)}/{len(todo)})")
            batches.append((batch.id, chunk))
        if args.no_wait:
            print("collect later: --resume " + " / --resume ".join(b for b, _ in batches))
            return
    for batch_id, chunk in batches:
        collect_batch(client, batch_id, chunk, res_dir)


def collect_batch(client, batch_id, todo, res_dir):
    while True:
        b = client.messages.batches.retrieve(batch_id)
        if b.processing_status == "ended":
            break
        print(f"\r  {batch_id} {b.processing_status}: {b.request_counts.processing} processing, {b.request_counts.succeeded} done", end="", flush=True)
        time.sleep(30)
    print(f"\nbatch {batch_id} ended: {b.request_counts}")
    total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    n_ok = n_bad = 0
    for r in client.messages.batches.results(batch_id):
        did = r.custom_id
        if r.result.type == "succeeded":
            try:
                rec, usage = parse_result(r.result.message)
                for kk in total:
                    total[kk] += usage[kk]
                (res_dir / f"{did}.json").write_text(json.dumps({"detection_id": did, "model": MODEL, "schema": SCHEMA_VERSION, "result": rec, "usage": usage, "batch": batch_id}))
                n_ok += 1
                continue
            except (ValueError, json.JSONDecodeError) as e:
                err = f"malformed:{e}"
        else:
            err = f"batch:{r.result.type}"
        # one retry, synchronously, then drop
        o = next((o for o, _ in todo if o["detection_id"] == did), None)
        if o is not None:
            try:
                msg = client.messages.create(**build_request(o, DATA / "crops" / f"{did}.jpg"))
                rec, usage = parse_result(msg)
                for kk in total:
                    total[kk] += usage[kk]
                (res_dir / f"{did}.json").write_text(json.dumps({"detection_id": did, "model": MODEL, "schema": SCHEMA_VERSION, "result": rec, "usage": usage, "retried": err}))
                n_ok += 1
                continue
            except Exception as e:
                err = f"{err} then {type(e).__name__}"
        write_drop(res_dir, did, err)
        n_bad += 1
    print(f"results: {n_ok} ok, {n_bad} dropped   usage {total}   cost ${cost_usd(total, batch=True):.3f}")


def write_output(slug, obs_all, res_dir, crops_dir):
    out = DATA / "classify" / slug / "classifications.jsonl"
    n = 0
    with out.open("w") as fh:
        for o in obs_all:
            p = res_dir / f"{o['detection_id']}.json"
            if not p.exists():
                continue
            r = json.loads(p.read_text())
            crop = crops_dir / f"{o['detection_id']}.jpg"
            meta = crop.with_suffix(".json")
            cm = json.loads(meta.read_text()) if meta.exists() else {}
            row = dict(o, crop=str(crop.relative_to(ROOT)) if crop.exists() else None,
                       pole_px_w=cm.get("pole_px_w"), pole_px_h=cm.get("pole_px_h"),
                       classification=r.get("result"), dropped=r.get("dropped"), model=r.get("model"))
            fh.write(json.dumps(row) + "\n")
            n += 1
    print(f"wrote {n} rows -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
