# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pillow", "python-dotenv"]
# ///
"""Precompute drawing runs like the web app does and write public/presets-genetic.js.

Usage: uv run run_examples.py [mode] [runs_per_object] [object ...]
Modes: `genetic` (default), or `genetic-nox` (without crossover, for comparison).
Defaults: 5 runs of each object in OBJECTS. Only the default genetic runs go to
public/; anything else goes to results/presets-<mode>[-<objects>].json.

Drawings are made of toy building blocks (square, double square, half square,
roof), turned in quarter turns and placed flat face to flat face.

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
OBJECTS = ["house", "rocket", "boat"]
GRID = 24  # blocks live on a GRID x GRID canvas in grid units; a block side A is 2 units
# kind: (width, height, flat faces as [top, right, bottom, left]) when not rotated
BLOCKS = {
    "square": (2, 2, [1, 1, 1, 1]),  # A x A
    "double": (4, 2, [1, 1, 1, 1]),  # 2A x A
    "half": (2, 2, [0, 0, 1, 1]),  # half square: legs A on the bottom and left
    "roof": (4, 2, [0, 0, 1, 0]),  # base 2A, height A
}

POP = 10  # drawings per generation
GENERATIONS = 30
P_CROSS = 0.5  # chance that a child comes from crossover instead of mutation
P_REMOVE = 0.3  # chance that a mutation takes a block away instead of adding 1-3
MAX_BLOCKS = 24
COLS, CELL, BORDER = 5, 200, 4  # grid sheet for the choice question, in pixels


def question(obj):
    article = "an" if obj[:1].lower() in "aeiou" else "a"
    return {"type": "noul", "instructions": f"The image shows {article} {obj}."}


def outline(kind, w, h):
    """Unrotated block outline in block units, relative to the top-left corner of its box."""
    if kind == "roof":
        return [[0, h], [w / 2, 0], [w, h]]
    if kind == "half":
        return [[0, 0], [0, h], [w, h]]
    return [[0, 0], [w, 0], [w, h], [0, h]]


def flat(block, side):
    """Whether a placed block has a flat face on side 0-3 (top, right, bottom, left)."""
    return BLOCKS[block["kind"]][2][(side - block["rot"]) % 4]


def overlap(a, b):
    ax, ay, aw, ah = a["box"]
    bx, by, bw, bh = b["box"]
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def random_block(rng, kept):
    """A block turned by 0-3 quarter turns, flat face to flat face with a random kept block.

    Returns None if no free spot turns up.
    """
    for _ in range(1000):
        kind, rot = rng.choice(list(BLOCKS)), rng.randrange(4)
        w, h, _ = BLOCKS[kind]
        points = outline(kind, w, h)
        for _ in range(rot):  # quarter turn clockwise
            points, w, h = [[h - py, px] for px, py in points], h, w
        new = {"kind": kind, "rot": rot}
        if kept:
            a = rng.choice(kept)
            ax, ay, aw, ah = a["box"]
            side = rng.randrange(4)
            if not flat(a, side) or not flat(new, (side + 2) % 4):
                continue
            if side in (0, 2):  # stack above or below, overlapping by at least one unit
                x, y = rng.randint(ax - w + 1, ax + aw - 1), ay - h if side == 0 else ay + ah
            else:  # place left or right
                x, y = ax + aw if side == 1 else ax - w, rng.randint(ay - h + 1, ay + ah - 1)
        else:
            x, y = rng.randint(GRID // 2 - 4, GRID // 2 + 4 - w), rng.randint(GRID // 2 - 4, GRID // 2 + 4 - h)  # near the centre
        new["box"] = [x, y, w, h]
        if x < 0 or y < 0 or x + w > GRID or y + h > GRID or any(overlap(new, b) for b in kept):
            continue
        new["points"] = [[round((x + px) / GRID, 4), round((y + py) / GRID, 4)] for px, py in points]
        return new
    return None


def random_blocks(rng, kept):
    """1-3 blocks, each against a kept block or an earlier one of the group."""
    group = []
    for _ in range(rng.randint(1, 3)):
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
    """Whether two blocks meet flat face to flat face."""
    ax, ay, aw, ah = a["box"]
    bx, by, bw, bh = b["box"]
    side = None
    if ay < by + bh and by < ay + ah:  # overlap vertically: left or right neighbours
        side = 1 if ax + aw == bx else 3 if bx + bw == ax else None
    if side is None and ax < bx + bw and bx < ax + aw:  # overlap horizontally: above or below
        side = 2 if ay + ah == by else 0 if by + bh == ay else None
    return side is not None and flat(a, side) and flat(b, (side + 2) % 4)


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
    """Add 1-3 blocks or, sometimes, take away one block whose removal keeps the rest connected."""
    if len(parent) > 1 and (len(parent) >= MAX_BLOCKS or rng.random() < P_REMOVE):
        removable = [i for i in range(len(parent)) if len(components(parent[:i] + parent[i + 1 :])) == 1]
        if removable:
            i = rng.choice(removable)
            return parent[:i] + parent[i + 1 :], "remove"
    return parent + random_blocks(rng, parent), "add"


def cross(rng, a, b):
    """Blocks of a on one side of a random vertical or horizontal cut, blocks of b on the other.

    b's blocks that overlap a's are dropped, and only the largest connected group stays.
    """
    axis = rng.randrange(2)  # 0: cut across x (left/right halves), 1: across y (top/bottom)
    mid = lambda blk: blk["box"][axis] + blk["box"][axis + 2] / 2  # noqa: E731
    cut = rng.uniform(min(map(mid, a + b)), max(map(mid, a + b)))
    child = [blk for blk in a if mid(blk) < cut]
    child += [blk for blk in b if mid(blk) >= cut and not any(overlap(blk, c) for c in child)]
    if not child:
        return list(a)
    return [child[i] for i in max(components(child), key=len)]


def same(a, b):
    key = lambda blocks: sorted((blk["kind"], blk["rot"], *blk["box"]) for blk in blocks)  # noqa: E731
    return key(a) == key(b)


def child_of(rng, cands, probs, p_cross):
    """One child: crossover of two parents drawn by fitness, or a mutated copy of one."""
    i, j = rng.choices(range(len(cands)), weights=probs, k=2)
    a, b = cands[i]["shapes"], cands[j]["shapes"]
    if rng.random() < p_cross and not same(a, b):
        shapes = cross(rng, a, b)
        if not same(shapes, a) and not same(shapes, b):
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
    cands = [{"shapes": random_blocks(rng, []), "parent": None, "op": "seed"} for _ in range(POP)]
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
    # blocks are stored without points; the web app rebuilds them from kind, rot and box
    for g in gens:
        g["candidates"] = [
            {**c, "shapes": [{k: b[k] for k in ("kind", "rot", "box")} for b in c["shapes"]]} for c in g["candidates"]
        ]
    return {"object": obj, "seed": seed, "model": resp.get("model", MODEL), "p": p, "stop": stop, "cost": cost, "calls": calls, "generations": gens}


def main(mode="genetic", runs="5", *objects):
    (Path("results") / mode).mkdir(parents=True, exist_ok=True)
    jobs = [(mode, obj, seed) for obj in objects or OBJECTS for seed in range(int(runs))]
    headers = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}
    with httpx.Client(headers=headers) as client, ThreadPoolExecutor(4) as pool:
        presets = list(pool.map(lambda job: evolve(client, *job), jobs))
    if mode == "genetic" and not objects:  # the app's canned runs
        Path(f"public/presets-{mode}.js").write_text(
            "// Generated by run_examples.py; canned drawing runs.\nvar PRESETS = PRESETS || {};\n"
            f"PRESETS.{mode} = {json.dumps(presets, separators=(',', ':'))};\n"
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
