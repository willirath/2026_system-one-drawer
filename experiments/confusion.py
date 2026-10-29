# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pillow", "python-dotenv"]
# ///
"""Can Jev tell the final drawings apart?

Asks one `choice` question per final drawing: house, rocket, boat or something
else. Covers the canned genetic runs, genetic without crossover
(results/presets-genetic-nox.json, if present) and random block drawings that
never saw Jev, as a baseline. (The blocks random walk, since removed, scored
12 of 24.) Run from the project root:

    uv run experiments/confusion.py
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

CLASSES = rx.OBJECTS + ["other"]
QUESTION = {
    "type": "choice",
    "instructions": "What does the image show?",
    "criteria": {**{o: f"The image shows a {o}." for o in rx.OBJECTS}, "other": "The image shows something else."},
}


def with_points(b):
    return {**b, "points": [[x / rx.GRID, y / rx.GRID] for x, y in b["pts"]]}


def final(run):
    if "steps" in run:
        return [s for st in run["steps"] if st["accepted"] for s in st["shapes"]]
    g = max(run["generations"], key=lambda g: g["p"])
    return [with_points(b) for b in g["candidates"][g["best"]]["shapes"]]


def load(mode):
    js, js_json = Path(f"public/presets-{mode}.js"), Path(f"results/presets-{mode}.json")
    if js.exists():
        return json.loads(js.read_text().split(f"PRESETS.{mode} = ")[1].rstrip().rstrip(";"))
    return json.loads(js_json.read_text()) if js_json.exists() else []


def main():
    items = []
    for mode in ("genetic", "genetic-nox"):
        items += [(mode, r["object"], r["p"], final(r)) for r in load(mode)]
    rng = random.Random(7)
    for _ in range(24):
        kept = []
        for _ in range(rng.randint(2, 6)):
            kept += rx.random_blocks(rng, kept)
        items.append(("random", "-", None, kept))
    headers = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}
    with httpx.Client(headers=headers) as client, ThreadPoolExecutor(4) as pool:
        resps = list(pool.map(lambda it: rx.decide(client, rx.render(it[3]), {"what": QUESTION}), items))
    rows = [
        {"mode": m, "drawn_as": o, "noul": p, **r["answers"]["what"]["probabilities"], "choice": r["answers"]["what"]["choice"]}
        for (m, o, p, _), r in zip(items, resps)
    ]
    Path("results/confusion.json").write_text(json.dumps(rows, indent=1))
    for mode in ("genetic", "genetic-nox", "random"):
        sel = [r for r in rows if r["mode"] == mode]
        if not sel:
            continue
        print(f"\n{mode}: rows = drawn as, columns = Jev's choice (count), then mean P(drawn-as class)")
        print(f"{'':8s}" + "".join(f"{c[:7]:>8s}" for c in CLASSES) + f"{'P(own)':>9s}")
        for o in sorted({r["drawn_as"] for r in sel}, key=lambda o: (o == "-", o)):
            rs = [r for r in sel if r["drawn_as"] == o]
            counts = "".join(f"{sum(r['choice'] == c for r in rs):8d}" for c in CLASSES)
            own = f"{sum(r[o] for r in rs) / len(rs):9.2f}" if o in rx.OBJECTS else ""
            print(f"{o[:8]:8s}{counts}{own}")
        if mode != "random":
            hits = sum(r["choice"] == r["drawn_as"] for r in sel)
            print(f"accuracy {hits}/{len(sel)}")
    cost = sum(r.get("usage", {}).get("cost", 0) for r in resps)
    print(f"\n{len(items)} drawings, cost ${cost:.4f}")


if __name__ == "__main__":
    main()
