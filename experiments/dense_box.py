"""Seeing shapes in clouds: dense random polygon fields, Jev boxes, readouts.

Run from the project root:
    uv run --with httpx --with pillow --with python-dotenv python experiments/dense_box.py <step>

Steps: probe, main, iterate, search. Results go to results/dense/.

A dense canvas holds N random 3-6 corner polygons (same generator as
run_examples.random_shape, but centred uniformly over the canvas). For each
target Jev answers a box question ("where is the <obj>?") and a noul question
("the image shows a <obj>"). The box is read out two ways:
  crop  - the canvas cropped to the box, scaled to fill 384 px (aspect kept, white pad)
  poly  - only the polygons whose centroid is inside the box, re-rendered and zoomed
          so that the box fills the canvas
Controls: the same readouts for a random box of the same size on the same canvas.
"""

import base64
import io
import json
import math
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv(os.path.join(os.getcwd(), ".env"))
URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = os.environ.get("JEV_MODEL", "typesafe/jev-1.14")
SIZE = 384
HI = 4  # supersampling for sharp crops
FILL = (0, 0, 0, 178)  # black at 70% opacity, as in run_examples
OBJECTS = ["elephant", "house", "cat", "tree"]
OUT = Path("results/dense")
BUDGET = 0.45  # hard stop, below the $0.50 limit

_cost = 0.0
_calls = 0
_lock = threading.Lock()


# ---------------------------------------------------------------- shapes, rendering

def dense_shape(rng):
    """A 3-6 corner polygon like run_examples.random_shape, centred anywhere on the canvas."""
    cx, cy = rng.uniform(0.05, 0.95), rng.uniform(0.05, 0.95)
    n = rng.randint(3, 6)
    r = rng.uniform(0.05, 0.2)
    stretch = rng.uniform(0.3, 1)
    rot = rng.uniform(0, 2 * math.pi)
    points = []
    for i in range(n):
        a = 2 * math.pi * (i + rng.uniform(-0.3, 0.3)) / n
        d = r * rng.uniform(0.6, 1)
        x, y = d * math.cos(a), d * stretch * math.sin(a)
        points.append([cx + x * math.cos(rot) - y * math.sin(rot), cy + x * math.sin(rot) + y * math.cos(rot)])
    return {"center": [cx, cy], "points": points}


def canvas(n, seed):
    rng = random.Random(f"dense-{n}-{seed}")
    return [dense_shape(rng) for _ in range(n)]


def render(shapes, size=SIZE, view=(0, 0, 1, 1)):
    """Render shapes; view = (left, top, right, bottom) in canvas fractions is mapped to the full image."""
    l, t, r, b = view
    s = size / max(r - l, b - t)  # keep the aspect ratio, centre the view
    ox = (size - (r - l) * s) / 2
    oy = (size - (b - t) * s) / 2
    img = Image.new("RGBA", (size, size), "white")
    for sh in shapes:
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).polygon([(ox + (x - l) * s, oy + (y - t) * s) for x, y in sh["points"]], fill=FILL)
        img = Image.alpha_composite(img, layer)
    return img.convert("RGB")


def render_fast(shapes, view=(0, 0, 1, 1)):
    """Supersampled render, downscaled to SIZE."""
    return render(shapes, SIZE * HI, view).resize((SIZE, SIZE), Image.LANCZOS)


def centroid(sh):
    xs, ys = zip(*sh["points"])
    return sum(xs) / len(xs), sum(ys) / len(ys)


def readout_crop(shapes, box):
    """Crop of the full canvas to the box (all polygons, clipped by the view), zoomed to fill."""
    view = (box["left"], box["top"], box["right"], box["bottom"])
    full = render(shapes, SIZE * HI)
    l, t, r, b = (round(v * SIZE * HI) for v in view)
    crop = full.crop((l, t, max(r, l + 1), max(b, t + 1)))
    w, h = crop.size
    s = SIZE / max(w, h)
    crop = crop.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
    img = Image.new("RGB", (SIZE, SIZE), "white")
    img.paste(crop, ((SIZE - crop.size[0]) // 2, (SIZE - crop.size[1]) // 2))
    return img


def inside(shapes, box):
    return [sh for sh in shapes if box["left"] <= centroid(sh)[0] <= box["right"] and box["top"] <= centroid(sh)[1] <= box["bottom"]]


def readout_poly(shapes, box):
    """Only polygons with centroid in the box, re-rendered with the box zoomed to fill the canvas."""
    view = (box["left"], box["top"], box["right"], box["bottom"])
    return render_fast(inside(shapes, box), view), len(inside(shapes, box))


def random_box(rng, box):
    w, h = box["right"] - box["left"], box["bottom"] - box["top"]
    l, t = rng.uniform(0, 1 - w), rng.uniform(0, 1 - h)
    return {"left": l, "top": t, "right": l + w, "bottom": t + h}


def iou(a, b):
    w = max(0, min(a["right"], b["right"]) - max(a["left"], b["left"]))
    h = max(0, min(a["bottom"], b["bottom"]) - max(a["top"], b["top"]))
    i = w * h
    area = lambda x: (x["right"] - x["left"]) * (x["bottom"] - x["top"])
    return i / (area(a) + area(b) - i) if i else 0.0


def darkness(img, box=None):
    """Mean ink (0 white .. 1 black) of the image, or of the box region."""
    g = img.convert("L")
    if box:
        g = g.crop(tuple(round(box[k] * SIZE) for k in ("left", "top", "right", "bottom")))
    hist = g.histogram()
    n = sum(hist)
    return 1 - sum(i * c for i, c in enumerate(hist)) / (255 * n) if n else 0.0


# ---------------------------------------------------------------- API

def article(obj):
    return "an" if obj[:1].lower() in "aeiou" else "a"


def noul_q(obj):
    return {"type": "noul", "instructions": f"The image shows {article(obj)} {obj}."}


def box_q(obj):
    return {"type": "box", "instructions": f"the {obj}"}


def decide(client, img, questions):
    global _cost, _calls
    with _lock:
        if _cost > BUDGET:
            raise RuntimeError(f"budget reached: ${_cost:.4f}")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    body = {"model": MODEL, "state": [{"type": "image_url", "image_url": {"url": uri}}], "questions": questions}
    for attempt in range(6):
        try:
            r = client.post(URL, json=body, timeout=90)
        except httpx.HTTPError:
            if attempt < 5:
                time.sleep(2**attempt)
                continue
            raise
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 5:
            time.sleep(2**attempt)
            continue
        if r.is_error:
            raise RuntimeError(f"{r.status_code}: {r.text[:300]}")
        resp = r.json()
        with _lock:
            _cost += resp.get("usage", {}).get("cost", 0) or 0
            _calls += 1
        return resp
    raise RuntimeError("retries exhausted")


def noul(client, img, obj):
    return decide(client, img, {"q": noul_q(obj)})["answers"]["q"]["noul"]


def nouls(client, img, objs):
    """All targets' noul in one request (questions are independent)."""
    a = decide(client, img, {o: noul_q(o) for o in objs})["answers"]
    return {o: a[o]["noul"] for o in objs}


def client():
    return httpx.Client(headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"})


def save(img, name):
    OUT.mkdir(parents=True, exist_ok=True)
    img.save(OUT / name)


def label_sheet(items, cols, cell=192, title=None):
    """items: list of (image, caption). A contact sheet with captions under each panel."""
    rows = -(-len(items) // cols)
    cap, top = 30, 26 if title else 0
    sheet = Image.new("RGB", (cols * cell, top + rows * (cell + cap)), "white")
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 12)
        tfont = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 16)
    except OSError:
        font = tfont = ImageFont.load_default()
    if title:
        d.text((6, 4), title, fill="black", font=tfont)
    for i, (img, text) in enumerate(items):
        x, y = i % cols * cell, top + i // cols * (cell + cap)
        sheet.paste(img.resize((cell - 6, cell - 6), Image.LANCZOS), (x + 3, y + 3))
        d.rectangle([x + 2, y + 2, x + cell - 3, y + cell - 3], outline="#999")
        for j, line in enumerate(text.split("\n")[:2]):
            d.text((x + 4, y + cell + j * 14), line, fill="black", font=font)
    return sheet


def draw_boxes(img, boxes):
    """Overlay labelled boxes (dict name -> (box, colour)) on a copy of img."""
    img = img.copy()
    d = ImageDraw.Draw(img)
    for name, (b, col) in boxes.items():
        r = [b["left"] * SIZE, b["top"] * SIZE, b["right"] * SIZE, b["bottom"] * SIZE]
        d.rectangle(r, outline=col, width=3)
        d.text((r[0] + 4, r[1] + 2), name, fill=col)
    return img


COLOURS = {"elephant": "#d62728", "house": "#1f77b4", "cat": "#2ca02c", "tree": "#ff7f0e"}


# ---------------------------------------------------------------- steps

def probe():
    """One canvas: combined vs separate questions, cost per call, determinism."""
    shapes = canvas(100, 0)
    img = render_fast(shapes)
    save(img, "probe_100_0.png")
    with client() as c:
        qs = {f"box_{o}": box_q(o) for o in OBJECTS} | {f"p_{o}": noul_q(o) for o in OBJECTS}
        r1 = decide(c, img, qs)
        print("combined:", json.dumps(r1["answers"]), "usage:", r1.get("usage"))
        r2 = decide(c, img, qs)
        print("combined again:", json.dumps(r2["answers"]), "usage:", r2.get("usage"))
        for o in OBJECTS[:2]:
            r = decide(c, img, {"b": box_q(o)})
            print(f"box {o} alone:", r["answers"], r.get("usage"))
            r = decide(c, img, {"p": noul_q(o)})
            print(f"noul {o} alone:", r["answers"], r.get("usage"))
            r = decide(c, img, {"p": noul_q(o)})
            print(f"noul {o} alone again:", r["answers"])
    print(f"calls {_calls} cost ${_cost:.5f}")


NS = [40, 100, 200]
SEEDS = range(8)
REPEATS = 3  # noul repeats per image, to average out noise
N_RANDOM = 2  # random control boxes per target box


def sane(b):
    """Clip a box to the canvas and give it a minimum size of 0.05."""
    b = {k: min(1.0, max(0.0, float(b[k]))) for k in ("left", "top", "right", "bottom")}
    for lo, hi in (("left", "right"), ("top", "bottom")):
        if b[hi] - b[lo] < 0.05:
            c = (b[hi] + b[lo]) / 2
            b[lo], b[hi] = max(0, c - 0.025), min(1, c + 0.025)
    return b


def repeated_nouls(c, img, reps=REPEATS):
    runs = [nouls(c, img, OBJECTS) for _ in range(reps)]
    return {o: [r[o] for r in runs] for o in OBJECTS}


def full_stage(c, n, seed):
    shapes = canvas(n, seed)
    img = render_fast(shapes)
    qs = {f"box_{o}": box_q(o) for o in OBJECTS} | {f"p_{o}": noul_q(o) for o in OBJECTS}
    reps = [decide(c, img, qs)["answers"] for _ in range(REPEATS)]
    out = {"n": n, "seed": seed, "ink": darkness(img), "targets": {}}
    for o in OBJECTS:
        boxes = [sane(r[f"box_{o}"]["box"]) for r in reps]
        out["targets"][o] = {
            "box": boxes[0],
            "box_repeats": boxes,
            "conf": [r[f"box_{o}"]["confidence"] for r in reps],
            "p_full": [r[f"p_{o}"]["noul"] for r in reps],
            "ink_box": darkness(img, boxes[0]),
        }
    return out


def readout_stage(c, rec):
    shapes = canvas(rec["n"], rec["seed"])
    rng = random.Random(f"ctrl-{rec['n']}-{rec['seed']}")
    for o, t in rec["targets"].items():
        box = t["box"]
        crop = readout_crop(shapes, box)
        poly, k = readout_poly(shapes, box)
        t["crop"] = repeated_nouls(c, crop)
        t["poly"] = repeated_nouls(c, poly)
        t["poly_count"] = k
        t["controls"] = []
        for i in range(N_RANDOM):
            rb = random_box(rng, box)
            rpoly, rk = readout_poly(shapes, rb)
            t["controls"].append({
                "box": rb,
                "crop": repeated_nouls(c, readout_crop(shapes, rb)),
                "poly": repeated_nouls(c, rpoly),
                "poly_count": rk,
            })
        tag = f"{rec['n']}_{rec['seed']}_{o}"
        save(crop, f"crop_{tag}.png")
        save(poly, f"poly_{tag}.png")
    return rec


def pmap(fn, jobs):
    with client() as c, ThreadPoolExecutor(4) as pool:
        return list(pool.map(lambda j: fn(c, *j), jobs))


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else float("nan")


def main():
    jobs = [(n, s) for n in NS for s in SEEDS]
    recs = pmap(full_stage, jobs)
    print(f"full stage: {_calls} calls ${_cost:.4f}")
    (OUT / "main_full.json").write_text(json.dumps(recs))
    recs = pmap(readout_stage, [(r,) for r in recs])
    print(f"readout stage: {_calls} calls ${_cost:.4f}")
    (OUT / "main.json").write_text(json.dumps(recs))
    sheets(recs)
    analyse()


def sheets(recs):
    for r in recs:
        shapes = canvas(r["n"], r["seed"])
        img = render_fast(shapes)
        boxed = draw_boxes(img, {o: (t["box"], COLOURS[o]) for o, t in r["targets"].items()})
        items = [(boxed, f"N={r['n']} seed={r['seed']}\nboxes: " + " ".join(o[0] for o in OBJECTS))]
        for o, t in r["targets"].items():
            tag = f"{r['n']}_{r['seed']}_{o}"
            items.append((Image.open(OUT / f"crop_{tag}.png"), f"{o} crop P={mean(t['crop'][o]):.2f}\nfull P={mean(t['p_full']):.2f} conf={mean(t['conf']):.2f}"))
            items.append((Image.open(OUT / f"poly_{tag}.png"), f"{o} poly P={mean(t['poly'][o]):.2f}\n{t['poly_count']} polys, area={area(t['box']):.2f}"))
        save(label_sheet(items, 9, 200), f"sheet_{r['n']}_{r['seed']}.png")


def area(b):
    return (b["right"] - b["left"]) * (b["bottom"] - b["top"])


def analyse():
    recs = json.loads((OUT / "main.json").read_text())
    print("\n== Q1/Q2: mean P(target) by readout (each value = mean of 3 repeats, then over seeds)")
    print(f"{'N':>4} {'target':9} {'area':>5} {'conf':>5} {'full':>5} {'crop':>5} {'rcrop':>5} {'poly':>5} {'rpoly':>5}  crop>rc poly>rp")
    rows = []
    for n in NS:
        for o in OBJECTS:
            ts = [r["targets"][o] for r in recs if r["n"] == n]
            v = {
                "area": mean(area(t["box"]) for t in ts),
                "conf": mean(mean(t["conf"]) for t in ts),
                "full": mean(mean(t["p_full"]) for t in ts),
                "crop": mean(mean(t["crop"][o]) for t in ts),
                "rcrop": mean(mean(mean(ct["crop"][o]) for ct in t["controls"]) for t in ts),
                "poly": mean(mean(t["poly"][o]) for t in ts),
                "rpoly": mean(mean(mean(ct["poly"][o]) for ct in t["controls"]) for t in ts),
            }
            wc = sum(mean(t["crop"][o]) > mean(mean(ct["crop"][o]) for ct in t["controls"]) for t in ts)
            wp = sum(mean(t["poly"][o]) > mean(mean(ct["poly"][o]) for ct in t["controls"]) for t in ts)
            rows.append((n, o, v))
            print(f"{n:>4} {o:9} " + " ".join(f"{v[k]:5.2f}" for k in v) + f"   {wc}/{len(ts)}    {wp}/{len(ts)}")
    print("\nall N pooled:")
    for o in OBJECTS:
        vs = [v for n, oo, v in rows if oo == o]
        print(f"     {o:9} " + " ".join(f"{mean(v[k] for v in vs):5.2f}" for k in vs[0]))
    # max over everything
    best = []
    for r in recs:
        for o, t in r["targets"].items():
            best += [(mean(t["crop"][o]), "crop", r["n"], r["seed"], o), (mean(t["poly"][o]), "poly", r["n"], r["seed"], o),
                     (mean(t["p_full"]), "full", r["n"], r["seed"], o)]
            for ct in t["controls"]:
                best += [(mean(ct["crop"][o]), "rcrop", r["n"], r["seed"], o), (mean(ct["poly"][o]), "rpoly", r["n"], r["seed"], o)]
    print("\ntop 8 single images (mean of 3 repeats):")
    for b in sorted(best, reverse=True)[:8]:
        print("  P=%.2f %-5s N=%d seed=%d %s" % b)
    for kind in ("full", "crop", "poly", "rcrop", "rpoly"):
        ps = [b[0] for b in best if b[1] == kind]
        print(f"  {kind:5}: max {max(ps):.2f}, share > 0.5: {mean(p > 0.5 for p in ps):.2f}, share > 0.66: {mean(p > 0.66 for p in ps):.2f}  (n={len(ps)})")

    print("\n== noise: SD of repeated noul on identical image (pooled)")
    sds = []
    for r in recs:
        for o, t in r["targets"].items():
            for xs in [t["p_full"], t["crop"][o], t["poly"][o]]:
                m = mean(xs)
                sds.append(math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)))
    print(f"  mean within-image SD {mean(sds):.3f}, median {sorted(sds)[len(sds)//2]:.3f}, max {max(sds):.3f}")

    print("\n== Q3: box specificity")
    print(f"{'N':>4} {'IoU between targets':>20} {'IoU repeat same target':>23} {'IoU target vs random box':>25} {'area>0.8':>9} {'ink box/canvas':>15}")
    for n in NS:
        rs = [r for r in recs if r["n"] == n]
        between, rep, rnd, big, ink = [], [], [], [], []
        for r in rs:
            T = r["targets"]
            for i, a in enumerate(OBJECTS):
                for b in OBJECTS[i + 1:]:
                    between.append(iou(T[a]["box"], T[b]["box"]))
                rep += [iou(T[a]["box"], x) for x in T[a]["box_repeats"][1:]]
                rnd += [iou(T[a]["box"], ct["box"]) for ct in T[a]["controls"]]
                big.append(area(T[a]["box"]) > 0.8)
                ink.append(T[a]["ink_box"] / r["ink"])
        print(f"{n:>4} {mean(between):20.2f} {mean(rep):23.2f} {mean(rnd):25.2f} {mean(big):9.2f} {mean(ink):15.2f}")
    print("\nbox confidence by target (all N):", {o: round(mean(mean(r['targets'][o]['conf']) for r in recs), 3) for o in OBJECTS})
    print("box area by target (all N):", {o: round(mean(area(r['targets'][o]['box']) for r in recs), 2) for o in OBJECTS})

    print("\n== Q3b: specificity matrix, poly readout of target row, P(column), mean over all canvases")
    for kind in ("crop", "poly"):
        print(f"  {kind}: readout \\ asked " + " ".join(f"{o:>8}" for o in OBJECTS))
        for a in OBJECTS:
            print(f"  {a:>22} " + " ".join(f"{mean(mean(r['targets'][a][kind][b]) for r in recs):8.2f}" for b in OBJECTS))
    print("  full canvas P(column): " + " ".join(f"{mean(mean(r['targets'][b]['p_full']) for r in recs):8.2f}" for b in OBJECTS))


def to_canvas(box, view):
    """Map a box given in readout-image fractions back to canvas fractions, for a readout of `view`."""
    l, t, r, b = view
    M = max(r - l, b - t)
    fx = lambda u: l + (u - (1 - (r - l) / M) / 2) * M
    fy = lambda v: t + (v - (1 - (b - t) / M) / 2) * M
    return {"left": fx(box["left"]), "right": fx(box["right"]), "top": fy(box["top"]), "bottom": fy(box["bottom"])}


LEVELS = 3


def iterate_one(c, n, seed, obj):
    """Box -> poly readout -> box inside the readout -> readout ... ; and the same chain with random boxes."""
    shapes = canvas(n, seed)
    rng = random.Random(f"iter-{n}-{seed}-{obj}")
    out = {"n": n, "seed": seed, "obj": obj, "chain": [], "control": []}
    for kind in ("chain", "control"):
        cur, view = shapes, (0, 0, 1, 1)
        for level in range(LEVELS + 1):
            img = render_fast(cur, view)
            reps = [decide(c, img, {"b": box_q(obj), "p": noul_q(obj)})["answers"] for _ in range(REPEATS)]
            p = [r["p"]["noul"] for r in reps]
            box = sane(reps[0]["b"]["box"])
            out[kind].append({"level": level, "count": len(cur), "p": p, "conf": reps[0]["b"]["confidence"], "box": box})
            save(img, f"iter_{kind}_{n}_{seed}_{obj}_{level}.png")
            if level == LEVELS:
                break
            if kind == "control":  # same-size random box, in readout coordinates
                box = random_box(rng, box)
            cbox = to_canvas(box, view)
            nxt = inside(cur, cbox)
            if len(nxt) < 2:
                break
            cur, view = nxt, (cbox["left"], cbox["top"], cbox["right"], cbox["bottom"])
    return out


def iterate():
    jobs = [(n, s, o) for n in (100, 200) for s in range(8) for o in OBJECTS]
    res = pmap(iterate_one, jobs)
    (OUT / "iterate.json").write_text(json.dumps(res))
    print(f"iterate: {_calls} calls ${_cost:.4f}")
    analyse_iterate()


def analyse_iterate():
    res = json.loads((OUT / "iterate.json").read_text())
    print("\n== iterate: mean P(target) per level (mean of 3 repeats), box chain vs random-box chain")
    print(f"{'target':9} " + " ".join(f"L{l}:box/rand(n)" for l in range(LEVELS + 1)))
    for o in OBJECTS + ["all"]:
        rs = [r for r in res if o in ("all", r["obj"])]
        cells = []
        for l in range(LEVELS + 1):
            a = [mean(r["chain"][l]["p"]) for r in rs if len(r["chain"]) > l]
            b = [mean(r["control"][l]["p"]) for r in rs if len(r["control"]) > l]
            cells.append(f"{mean(a):.2f}/{mean(b):.2f}({len(a)})")
        print(f"{o:9} " + "  ".join(cells))
    tops = sorted(((mean(x["p"]), r["obj"], r["n"], r["seed"], x["level"], k, x["count"]) for r in res for k in ("chain", "control") for x in r[k]), reverse=True)[:8]
    print("top 8:")
    for t in tops:
        print("  P=%.2f %s N=%d seed=%d level=%d %s (%d polys)" % t)
    items = []
    for o in OBJECTS:
        r = max((r for r in res if r["obj"] == o), key=lambda r: max(mean(x["p"]) for x in r["chain"]))
        for x in r["chain"]:
            items.append((Image.open(OUT / f"iter_chain_{r['n']}_{r['seed']}_{o}_{x['level']}.png"), f"{o} L{x['level']} P={mean(x['p']):.2f}\n{x['count']} polys conf={x['conf']:.2f}"))
        items += [(Image.new("RGB", (10, 10), "white"), "")] * (LEVELS + 1 - len(r["chain"]))
    save(label_sheet(items, LEVELS + 1, 200, "iterated box readout, best chain per target"), "sheet_iterate.png")


SEARCH_N = 160  # canvases per target
RECHECK = 5


def search_one(c, n, seed):
    """One canvas: 4 boxes; for each target the poly + crop readout and a same-size random-box poly + crop readout, one noul each."""
    shapes = canvas(n, f"s{seed}")
    img = render_fast(shapes)
    a = decide(c, img, {f"box_{o}": box_q(o) for o in OBJECTS} | {f"p_{o}": noul_q(o) for o in OBJECTS})["answers"]
    rng = random.Random(f"search-{n}-{seed}")
    out = []
    for o in OBJECTS:
        box = sane(a[f"box_{o}"]["box"])
        rb = random_box(rng, box)
        for kind, b in (("box", box), ("rand", rb)):
            pimg, k = readout_poly(shapes, b)
            pp = noul(c, pimg, o)
            cp = noul(c, readout_crop(shapes, b), o)
            out.append({"n": n, "seed": seed, "obj": o, "kind": kind, "box": b, "p_full": a[f"p_{o}"]["noul"],
                        "conf": a[f"box_{o}"]["confidence"], "poly": pp, "crop": cp, "count": k})
    return out


def search():
    jobs = [(n, s) for s in range(SEARCH_N // 2) for n in (100, 200)]
    res = [x for r in pmap(search_one, jobs) for x in r]
    print(f"search: {_calls} calls ${_cost:.4f}")
    # winners: for each target and kind (box / rand) and readout (poly / crop), the highest single-call P
    winners = []
    for o in OBJECTS:
        for kind in ("box", "rand"):
            for ro in ("poly", "crop"):
                w = max((x for x in res if x["obj"] == o and x["kind"] == kind), key=lambda x: x[ro])
                winners.append({"obj": o, "kind": kind, "readout": ro, "sel_p": w[ro], "rec": w})
        # and the best full canvas
        w = max((x for x in res if x["obj"] == o and x["kind"] == "box"), key=lambda x: x["p_full"])
        winners.append({"obj": o, "kind": "full", "readout": "full", "sel_p": w["p_full"], "rec": w})

    def recheck(c, w):
        r = w["rec"]
        shapes = canvas(r["n"], f"s{r['seed']}")
        if w["readout"] == "poly":
            img = readout_poly(shapes, r["box"])[0]
        elif w["readout"] == "crop":
            img = readout_crop(shapes, r["box"])
        else:
            img = render_fast(shapes)
        w["recheck"] = [noul(c, img, w["obj"]) for _ in range(RECHECK)]
        # same drawing, view nudged by ~1-2% (shift and zoom): sensitivity to tiny framing changes
        rng = random.Random(f"jit-{w['obj']}-{w['kind']}-{w['readout']}")
        b = r["box"] if w["readout"] != "full" else {"left": 0, "top": 0, "right": 1, "bottom": 1}
        w["jitter"] = []
        for _ in range(RECHECK):
            wd, ht = b["right"] - b["left"], b["bottom"] - b["top"]
            z = rng.uniform(-0.02, 0.02)
            dx, dy = rng.uniform(-0.01, 0.01) * wd, rng.uniform(-0.01, 0.01) * ht
            jb = {"left": b["left"] - z * wd + dx, "right": b["right"] + z * wd + dx,
                  "top": b["top"] - z * ht + dy, "bottom": b["bottom"] + z * ht + dy}
            if w["readout"] == "poly":
                jimg = render_fast(inside(shapes, b), (jb["left"], jb["top"], jb["right"], jb["bottom"]))
            else:
                jimg = render_fast(shapes, (jb["left"], jb["top"], jb["right"], jb["bottom"]))
            w["jitter"].append(noul(c, jimg, w["obj"]))
        name = f"search_{w['obj']}_{w['kind']}_{w['readout']}.png"
        save(img, name)
        w["image"] = name
        return w

    winners = pmap(recheck, [(w,) for w in winners])
    (OUT / "search.json").write_text(json.dumps({"all": res, "winners": winners}))
    print(f"search + recheck: {_calls} calls ${_cost:.4f}")
    analyse_search()


def analyse_search():
    d = json.loads((OUT / "search.json").read_text())
    res, winners = d["all"], d["winners"]
    print(f"\n== search over {SEARCH_N} canvases per target (N=100/200 half each), single-call selection, then {RECHECK} re-asks")
    print("population means (single calls): ")
    for o in OBJECTS:
        b = [x for x in res if x["obj"] == o and x["kind"] == "box"]
        r = [x for x in res if x["obj"] == o and x["kind"] == "rand"]
        print(f"  {o:9} full {mean(x['p_full'] for x in b):.2f}  box-poly {mean(x['poly'] for x in b):.2f}  rand-poly {mean(x['poly'] for x in r):.2f}"
              f"  box-crop {mean(x['crop'] for x in b):.2f}  rand-crop {mean(x['crop'] for x in r):.2f}")
    print(f"{'target':9} {'source':10} {'selected P':>10} {'re-ask mean':>11} {'re-ask range':>13} {'jitter mean':>11} {'jitter range':>13}")
    items = []
    for w in winners:
        rc = w["recheck"]
        jt = w["jitter"]
        print(f"{w['obj']:9} {w['kind'] + '-' + w['readout']:10} {w['sel_p']:10.2f} {mean(rc):11.2f}   {min(rc):.2f}-{max(rc):.2f} {mean(jt):11.2f}   {min(jt):.2f}-{max(jt):.2f}")
        items.append((Image.open(OUT / w["image"]), f"{w['obj']} {w['kind']}-{w['readout']}\nsel {w['sel_p']:.2f} re-ask {mean(rc):.2f} jit {mean(jt):.2f}"))
    save(label_sheet(items, 5, 200, "search winners: selected single-call P vs mean of 5 re-asks"), "sheet_search.png")


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "main"
    globals()[step](*sys.argv[2:])
