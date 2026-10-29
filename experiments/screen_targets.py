# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pillow", "python-dotenv"]
# ///
"""Which targets does Jev readily see in block drawings?

Asks one noul question per target, all in one call per drawing, for the canned
final genetic drawings and for fresh random block drawings that never saw Jev
(the baseline). Polygon and random-walk drawings were part of the first
screening, before those modes were removed. Run from the project root:

    uv run experiments/screen_targets.py
"""

import json
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

sys.path.insert(0, os.getcwd())
import run_examples as rx  # noqa: E402

TARGETS = [
    "house", "tower", "castle", "pyramid", "staircase", "arrow", "rocket", "sailboat",
    "Christmas tree", "robot", "cross", "table", "chair", "ball", "sun", "fish", "bird",
    "car", "dog", "elephant", "cat", "tree", "boat", "ship",
]


def presets(mode):
    text = Path(f"public/presets-{mode}.js").read_text()
    return json.loads(text.split(f"PRESETS.{mode} = ")[1].rstrip().rstrip(";"))


def with_points(b):
    w, h, _ = rx.BLOCKS[b["kind"]]
    points = rx.outline(b["kind"], w, h)
    for _ in range(b["rot"]):
        points, w, h = [[h - py, px] for px, py in points], h, w
    x, y = b["box"][:2]
    return {**b, "points": [[(x + px) / rx.GRID, (y + py) / rx.GRID] for px, py in points]}


def drawings():
    out = []
    for r in presets("genetic"):
        g = max(r["generations"], key=lambda g: g["p"])
        out.append(("genetic", r["object"], [with_points(b) for b in g["candidates"][g["best"]]["shapes"]]))
    rng = random.Random(42)
    for i in range(20):
        kept = []
        for _ in range(rng.randint(2, 6)):
            kept += rx.random_blocks(rng, kept)
        out.append(("random blocks", "-", kept))
    return out


def main():
    items = drawings()
    questions = {f"t{i}": rx.question(t) for i, t in enumerate(TARGETS)}
    headers = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}
    with httpx.Client(headers=headers) as client, ThreadPoolExecutor(4) as pool:
        resps = list(pool.map(lambda it: rx.decide(client, rx.render(it[2]), questions), items))
    rows = [
        {"source": src, "drawn_as": obj, **{t: r["answers"][f"t{i}"]["noul"] for i, t in enumerate(TARGETS)}}
        for (src, obj, _), r in zip(items, resps)
    ]
    Path("results/screen_targets.json").write_text(json.dumps(rows, indent=1))
    cost = sum(r.get("usage", {}).get("cost", 0) for r in resps)
    sources = ["random blocks", "genetic"]
    print(f"{'target':15s}" + "".join(f"{s:>17s}" for s in sources) + f"{'max':>7s}")
    print(f"{'':15s}" + "".join(f"{'mean / max':>17s}" for _ in sources))
    for t in sorted(TARGETS, key=lambda t: -sum(r[t] for r in rows if r["source"] == "random blocks")):
        cells = []
        for s in sources:
            v = [r[t] for r in rows if r["source"] == s]
            cells.append(f"{sum(v) / len(v):.2f} / {max(v):.2f}".rjust(17))
        print(f"{t:15s}" + "".join(cells) + f"{max(r[t] for r in rows):7.2f}")
    print(f"\n{len(items)} drawings x {len(TARGETS)} targets in {len(items)} calls, cost ${cost:.4f}")


if __name__ == "__main__":
    main()
