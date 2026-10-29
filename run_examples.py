# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pillow", "python-dotenv"]
# ///
"""Precompute drawing runs like the web app does: the alphabet and the explore/ objects.

Usage: uv run run_examples.py [mode] [runs_per_object] [object ...]
Modes: `genetic` (default, the objects in OBJECTS), `alphabet` (the same for the
capital letters A-Z and the digits 0-9), or `genetic-nox` (objects without crossover, for
comparison). Defaults: 5 runs of each object. Default `alphabet` runs put the
best run per letter in public/presets-alphabet.js (the main page) and all runs in
results/presets-alphabet-all.json; default `genetic` runs go to
public/explore/presets-objects.js.
Anything else goes to results/presets-<mode>[-<objects>].json.

Drawings are made of toy building blocks (square, double square, half square,
roof), placed face to face. Triangles turn in quarter turns; squares and double
squares can also sit diagonally, against a triangle's slanted face or another
diagonal block.

A run evolves POP drawings at once. Each generation shows them side by
side in a grid and asks Jev a `choice` question ("which panel shows a
<object>?"). The choice probabilities are the fitness. Two drawings carry over
unchanged: the best so far (highest noul P, replaced only when a pick scores at
least as high) and the current pick. The rest are children of parents picked in
proportion to fitness, by crossover or mutation. A `noul` question on the best
drawing decides when to stop (P > 0.66, or after GENERATIONS generations); the
result is the pick with the highest P over all generations.
"""

import base64
import io
import json
import math
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from dotenv import load_dotenv
from PIL import Image, ImageDraw

load_dotenv()
URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = os.environ.get("JEV_MODEL", "typesafe/jev-1.14")
SIZE = 384
TARGET = 0.66
FILL = (0, 0, 0, 178)  # black at 70% opacity, same as the web app
# distinct shapes that score low on random drawings but reachably high on good ones (experiments/screen_targets.py)
OBJECTS = ["house", "rocket", "flagpole with flag"]
LETTERS = [f"capital letter {c}" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"] + [f"digit {d}" for d in range(10)]
SETS = {"genetic": OBJECTS, "genetic-nox": OBJECTS, "alphabet": LETTERS}
PUBLIC = {"alphabet": "public/presets-alphabet.js", "genetic": "public/explore/presets-objects.js"}
GRID = 24  # blocks live on a GRID x GRID canvas in grid units; a block side A is 2 units
# kind: unrotated outline in grid units, all wound the same way so that faces that
# touch run in opposite directions, and the turns it may take in 45 degree steps
# (squares and double squares can sit diagonally; triangles only in quarter turns)
BLOCKS = {
    "square": ([(0, 0), (2, 0), (2, 2), (0, 2)], range(8)),  # A x A
    "double": ([(0, 0), (4, 0), (4, 2), (0, 2)], range(8)),  # 2A x A
    "half": ([(0, 0), (2, 2), (0, 2)], range(0, 8, 2)),  # half square: legs A, long side sqrt(2) A
    "roof": ([(0, 2), (2, 0), (4, 2)], range(0, 8, 2)),  # base 2A, height A
}
EPS = 1e-4

POP = 10  # drawings per generation
GENERATIONS = 30
P_CROSS = 0.5  # chance that a child comes from crossover instead of mutation
P_REMOVE = 0.3  # chance that a mutation takes a block away instead of adding 1-3
MAX_BLOCKS = 8  # hard cap per drawing
COLS, CELL, BORDER = 5, 200, 4  # grid sheet for the choice question, in pixels


def question(obj):
    article = "an" if obj[:1].lower() in "aeiou" else "a"
    return {"type": "noul", "instructions": f"The image shows {article} {obj}."}


def turned(kind, k):
    """The block's outline after k turns of 45 degrees (clockwise on screen) about its first corner."""
    c, s = math.cos(k * math.pi / 4), math.sin(k * math.pi / 4)
    return [(x * c - y * s, x * s + y * c) for x, y in BLOCKS[kind][0]]


def faces(pts):
    return list(zip(pts, pts[1:] + pts[:1]))


def unit(p, q):
    d = math.dist(p, q)
    return (q[0] - p[0]) / d, (q[1] - p[1]) / d, d


def overlap(a, b):
    """Whether two convex outlines overlap by more than a touch (separating axis test)."""
    for (x1, y1), (x2, y2) in faces(a) + faces(b):
        nx, ny = y1 - y2, x2 - x1
        pa, pb = [x * nx + y * ny for x, y in a], [x * nx + y * ny for x, y in b]
        if max(pa) <= min(pb) + EPS or max(pb) <= min(pa) + EPS:
            return False
    return True


def random_block(rng, kept):
    """A block flush against a face of a random kept block, overlapping it by at least A/2.

    Faces only meet when they lie on the same line, so a diagonal block can only
    sit on a triangle's slanted face or on another diagonal block. The first block
    sits straight, near the centre. Returns None if no free spot turns up.
    """
    for _ in range(1000):
        kind = rng.choice(list(BLOCKS))
        k = rng.choice(BLOCKS[kind][1])
        pts = turned(kind, k)
        if kept:
            p1, p2 = rng.choice(faces(rng.choice(kept)["pts"]))
            ux, uy, la = unit(p1, p2)
            match = [(q1, q2) for q1, q2 in faces(pts) if unit(q1, q2)[0] * ux + unit(q1, q2)[1] * uy < -1 + EPS]
            if not match:
                continue
            q1, q2 = rng.choice(match)
            lb = math.dist(q1, q2)
            t = 1 - lb + rng.randrange(int(la + lb - 2 + EPS) + 1)  # q2 sits t along the face, in steps of A/2
            dx, dy = p1[0] + t * ux - q2[0], p1[1] + t * uy - q2[1]
        else:
            if k % 2:
                continue
            xs, ys = [x for x, _ in pts], [y for _, y in pts]
            w, h = round(max(xs) - min(xs)), round(max(ys) - min(ys))
            dx = rng.randint(GRID // 2 - 4, GRID // 2 + 4 - w) - min(xs)
            dy = rng.randint(GRID // 2 - 4, GRID // 2 + 4 - h) - min(ys)
        pts = [(round(x + dx, 6), round(y + dy, 6)) for x, y in pts]
        # the drawing is recentred after each change, so only its total size must fit the canvas
        xs, ys = zip(*(pt for b in kept for pt in b["pts"]), *pts)
        if max(xs) - min(xs) > GRID + EPS or max(ys) - min(ys) > GRID + EPS or any(overlap(pts, b["pts"]) for b in kept):
            continue
        return block(kind, k, pts)
    return None


def block(kind, k, pts):
    return {"kind": kind, "rot": k, "pts": pts, "points": [[x / GRID, y / GRID] for x, y in pts]}


def recenter(blocks):
    """Shift the drawing by whole grid units so its bounding box sits in the middle of the canvas."""
    xs, ys = zip(*(pt for b in blocks for pt in b["pts"]))
    dx = min(max(round(GRID / 2 - (min(xs) + max(xs)) / 2), -min(xs)), GRID - max(xs))
    dy = min(max(round(GRID / 2 - (min(ys) + max(ys)) / 2), -min(ys)), GRID - max(ys))
    if not dx and not dy:
        return blocks
    return [block(b["kind"], b["rot"], [(round(x + dx, 6), round(y + dy, 6)) for x, y in b["pts"]]) for b in blocks]


def random_blocks(rng, kept, room=3):
    """1-3 blocks (at most room), each against a kept block or an earlier one of the group."""
    group = []
    for _ in range(min(rng.randint(1, 3), room)):
        block = random_block(rng, kept + group)
        if block is None:
            break
        group.append(block)
    return group


def render(shapes):
    img = Image.new("RGBA", (SIZE, SIZE), "white")
    for s in shapes:
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        # a white edge keeps neighbouring blocks apart
        ImageDraw.Draw(layer).polygon([(x * SIZE, y * SIZE) for x, y in s["points"]], fill=FILL, outline="white", width=2)
        img = Image.alpha_composite(img, layer)
    return img.convert("RGB")


def ask(client, img, obj):
    return decide(client, img, {"is": question(obj)})


def decide(client, img, questions):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    body = {"model": MODEL, "state": [{"type": "image_url", "image_url": {"url": uri}}], "questions": questions}
    for attempt in range(5):
        r = client.post(URL, json=body, timeout=60)
        if r.status_code in (429, 500, 502, 503) and attempt < 4:
            time.sleep(2**attempt)
            continue
        if r.is_error:
            raise RuntimeError(f"{r.status_code}: {r.text}")
        return r.json()


def touching(a, b):
    """Whether two blocks meet face to face: faces on the same line, running opposite ways, overlapping."""
    for p1, p2 in faces(a["pts"]):
        ux, uy, la = unit(p1, p2)
        for q1, q2 in faces(b["pts"]):
            vx, vy, _ = unit(q1, q2)
            if ux * vx + uy * vy > -1 + EPS or abs(ux * (q1[1] - p1[1]) - uy * (q1[0] - p1[0])) > EPS:
                continue
            s1, s2 = sorted(((q[0] - p1[0]) * ux + (q[1] - p1[1]) * uy for q in (q1, q2)))
            if min(la, s2) - max(0, s1) > EPS:
                return True
    return False


def components(blocks):
    """Index groups of blocks that connect flat face to flat face."""
    left, groups = set(range(len(blocks))), []
    while left:
        todo = [left.pop()]
        group = set(todo)
        while todo:
            i = todo.pop()
            for j in [j for j in left if touching(blocks[i], blocks[j])]:
                left.remove(j)
                group.add(j)
                todo.append(j)
        groups.append(sorted(group))
    return groups


def mutate(rng, parent):
    """Add 1-3 blocks (up to MAX_BLOCKS) or, sometimes, take away one whose removal keeps the rest connected."""
    if len(parent) > 1 and (len(parent) >= MAX_BLOCKS or rng.random() < P_REMOVE):
        removable = [i for i in range(len(parent)) if len(components(parent[:i] + parent[i + 1 :])) == 1]
        if removable:
            i = rng.choice(removable)
            return recenter(parent[:i] + parent[i + 1 :]), "remove"
    return recenter(parent + random_blocks(rng, parent, MAX_BLOCKS - len(parent))), "add"


def cross(rng, a, b):
    """Blocks of a on one side of a random vertical or horizontal cut, blocks of b on the other.

    b's blocks that overlap a's are dropped, and only the largest connected group stays.
    """
    axis = rng.randrange(2)  # 0: cut across x (left/right halves), 1: across y (top/bottom)
    mid = lambda blk: sum(pt[axis] for pt in blk["pts"]) / len(blk["pts"])  # noqa: E731
    cut = rng.uniform(min(map(mid, a + b)), max(map(mid, a + b)))
    child = [blk for blk in a if mid(blk) < cut]
    child += [blk for blk in b if mid(blk) >= cut and not any(overlap(blk["pts"], c["pts"]) for c in child)]
    if not child:
        return list(a)
    return recenter([child[i] for i in max(components(child), key=len)])


def same(a, b):
    key = lambda blocks: sorted((blk["kind"], blk["rot"], *(round(v, 3) for pt in blk["pts"] for v in pt)) for blk in blocks)  # noqa: E731
    return key(a) == key(b)


def child_of(rng, cands, probs, p_cross):
    """One child: crossover of two parents drawn by fitness, or a mutated copy of one."""
    i, j = rng.choices(range(len(cands)), weights=probs, k=2)
    a, b = cands[i]["shapes"], cands[j]["shapes"]
    if rng.random() < p_cross and not same(a, b):
        shapes = cross(rng, a, b)
        if len(shapes) <= MAX_BLOCKS and not same(shapes, a) and not same(shapes, b):
            return {"shapes": shapes, "parent": i, "parent2": j, "op": "cross"}
    shapes, op = mutate(rng, a)
    return {"shapes": shapes, "parent": i, "op": op}


def sheet(drawings):
    """The drawings side by side in framed panels, COLS per row, as Jev sees them."""
    rows = -(-len(drawings) // COLS)
    img = Image.new("RGB", (COLS * CELL, rows * CELL), "white")
    inner = CELL - 2 * BORDER - 8
    for i, shapes in enumerate(drawings):
        x, y = i % COLS * CELL, i // COLS * CELL
        img.paste(render(shapes).resize((inner, inner), Image.LANCZOS), (x + BORDER + 4, y + BORDER + 4))
        ImageDraw.Draw(img).rectangle([x, y, x + CELL - 1, y + CELL - 1], outline="black", width=BORDER)
    return img


def choice_question(obj, n):
    article = "an" if obj[:1].lower() in "aeiou" else "a"
    return {
        "type": "choice",
        "instructions": f"The image is a grid of {n} framed panels, {COLS} per row, numbered left to right, "
        f"top to bottom. Which panel shows {article} {obj}?",
        "criteria": {
            str(i + 1): f"Panel {i + 1} (row {i // COLS + 1}, column {i % COLS + 1}) shows {article} {obj}."
            for i in range(n)
        },
    }


def evolve(client, mode, obj, seed):
    """Genetic run: Jev picks among POP block drawings per generation; the pick is the fitness."""
    rng = random.Random(seed)
    p_cross = 0 if mode == "genetic-nox" else P_CROSS
    cands = [{"shapes": recenter(random_blocks(rng, [])), "parent": None, "op": "seed"} for _ in range(POP)]
    gens, p, cost, calls, stop = [], -1.0, 0.0, 0, "generations"
    best_p, final = -1.0, None  # best noul so far and its drawing
    for _ in range(GENERATIONS):
        rng.shuffle(cands)  # new panel positions every generation, against position bias
        resp = decide(client, sheet([c["shapes"] for c in cands]), {"pick": choice_question(obj, POP)})
        cost += resp.get("usage", {}).get("cost", 0)
        calls += 1
        probs = [resp["answers"]["pick"]["probabilities"][str(i + 1)] for i in range(POP)]
        best = max(range(POP), key=probs.__getitem__)
        p = cands[best].get("p")  # known for drawings carried over
        if p is None:
            resp = ask(client, render(cands[best]["shapes"]), obj)
            cost += resp.get("usage", {}).get("cost", 0)
            p = resp["answers"]["is"]["noul"]
            calls += 1
        gens.append({"candidates": cands, "probs": probs, "best": best, "p": p})
        if p >= best_p:
            best_p, final = p, cands[best]["shapes"]
        if p > TARGET:
            stop = "target"
            break
        # the best so far and the current pick survive; children fill the rest
        anchor = next(i for i, c in enumerate(cands) if c["shapes"] is final)
        nxt = [{"shapes": final, "parent": anchor, "op": "best", "p": best_p}]
        if best != anchor:
            nxt.append({"shapes": cands[best]["shapes"], "parent": best, "op": "elite", "p": p})
        cands = nxt + [child_of(rng, cands, probs, p_cross) for _ in range(POP - len(nxt))]
    p = best_p
    print(f"{mode:11s} {obj:8s} seed={seed:<3d} P={p:.2f}  {len(final)} blocks after {len(gens)} generations  stop={stop}  {calls} calls  ${cost:.5f}")
    name = Path("results") / mode / f"{obj.replace(' ', '-')}_{seed}"
    render(final).save(f"{name}_{p:.2f}.png")
    sheet([c["shapes"] for c in gens[-1]["candidates"]]).save(f"{name}_sheet.png")
    # blocks are stored by their outline in grid units; the web app scales them to the canvas
    for g in gens:
        g["candidates"] = [
            {**c, "shapes": [{"kind": b["kind"], "rot": b["rot"], "pts": [[round(x, 2), round(y, 2)] for x, y in b["pts"]]}
                             for b in c["shapes"]]}
            for c in g["candidates"]
        ]
    return {"object": obj, "seed": seed, "model": resp.get("model", MODEL), "p": p, "stop": stop, "cost": cost, "calls": calls, "generations": gens}


def main(mode="genetic", runs="5", *objects):
    (Path("results") / mode).mkdir(parents=True, exist_ok=True)
    jobs = [(mode, obj, seed) for obj in objects or SETS[mode] for seed in range(int(runs))]
    headers = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}
    with httpx.Client(headers=headers) as client, ThreadPoolExecutor(4) as pool:
        presets = list(pool.map(lambda job: evolve(client, *job), jobs))
    if mode in PUBLIC and not objects:  # the app's canned runs
        shown = presets
        if mode == "alphabet":  # only the best run per letter, to keep the page small
            Path(f"results/presets-{mode}-all.json").write_text(json.dumps(presets, separators=(",", ":")))
            shown = [max((r for r in presets if r["object"] == o), key=lambda r: r["p"]) for o in SETS[mode]]
        Path(PUBLIC[mode]).write_text(
            "// Generated by run_examples.py; canned drawing runs.\nvar PRESETS = PRESETS || {};\n"
            f"PRESETS.{mode} = {json.dumps(shown, separators=(',', ':'))};\n"
        )
    else:
        tag = "".join(f"-{o.replace(' ', '_')}" for o in objects)
        Path(f"results/presets-{mode}{tag}.json").write_text(json.dumps(presets, separators=(",", ":")))
    cost = sum(p["cost"] for p in presets)
    calls = sum(p["calls"] for p in presets)
    hits = sum(p["stop"] == "target" for p in presets)
    print(f"\n{hits}/{len(presets)} reached P > {TARGET}, {calls} Jev calls, total cost ${cost:.4f}, model {MODEL}")


if __name__ == "__main__":
    main(*sys.argv[1:])
