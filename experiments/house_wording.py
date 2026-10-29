# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pillow", "python-dotenv"]
# ///
"""Does the wording of the target help? "house" vs "simple house" etc.

Needs the canned house runs (public/explore/presets-objects.js) and

    uv run run_examples.py genetic 8 "simple house" "schematic house" "drawing of a house"

Then asks every final drawing, and random block drawings as a baseline, all four
noul wordings plus the house/rocket/boat/other choice, in one call per drawing.
A wording only helps if its drawings score higher on the *same* questions, not
just on their own. Run from the project root:

    uv run experiments/house_wording.py
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
from confusion import QUESTION, final  # noqa: E402

WORDINGS = ["house", "simple house", "schematic house", "drawing of a house"]


def main():
    canned = json.loads(Path(rx.PUBLIC["genetic"]).read_text().split("PRESETS.genetic = ")[1].rstrip().rstrip(";"))
    variants = json.loads(Path("results/presets-genetic-simple_house-schematic_house-drawing_of_a_house.json").read_text())
    items = [(r["object"], r["p"], r["stop"], final(r)) for r in canned + variants if r["object"] in WORDINGS]
    rng = random.Random(11)
    for _ in range(24):
        kept = []
        for _ in range(rng.randint(2, 6)):
            kept += rx.random_blocks(rng, kept)
        items.append(("random", None, "", kept))
    questions = {f"w{i}": rx.question(w) for i, w in enumerate(WORDINGS)} | {"what": QUESTION}
    headers = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}
    with httpx.Client(headers=headers) as client, ThreadPoolExecutor(4) as pool:
        resps = list(pool.map(lambda it: rx.decide(client, rx.render(it[3]), questions), items))
    rows = [
        {"evolved_for": o, "own_p": p, "stop": stop, "blocks": len(shapes),
         **{w: r["answers"][f"w{i}"]["noul"] for i, w in enumerate(WORDINGS)},
         "choice": r["answers"]["what"]["choice"], "p_house_choice": r["answers"]["what"]["probabilities"]["house"]}
        for (o, p, stop, shapes), r in zip(items, resps)
    ]
    Path("results/house_wording.json").write_text(json.dumps(rows, indent=1))
    mean = lambda xs: sum(xs) / len(xs)  # noqa: E731
    print(f"{'evolved for':20s}{'hits':>6s}{'own P':>7s}{'blocks':>7s}" + "".join(f"{w[:12]:>14s}" for w in WORDINGS)
          + f"{'named house':>13s}{'P(house) choice':>17s}")
    for o in WORDINGS + ["random"]:
        rs = [r for r in rows if r["evolved_for"] == o]
        hits = f"{sum(r['stop'] == 'target' for r in rs)}/{len(rs)}" if o != "random" else ""
        own = f"{mean([r['own_p'] for r in rs]):7.2f}" if o != "random" else f"{'':7s}"
        print(f"{o:20s}{hits:>6s}{own}{mean([r['blocks'] for r in rs]):7.1f}"
              + "".join(f"{mean([r[w] for r in rs]):14.2f}" for w in WORDINGS)
              + f"{sum(r['choice'] == 'house' for r in rs):>9d}/{len(rs):<3d}{mean([r['p_house_choice'] for r in rs]):17.2f}")
    print(f"\n{len(items)} drawings, cost ${sum(r.get('usage', {}).get('cost', 0) for r in resps):.4f}")


if __name__ == "__main__":
    main()
