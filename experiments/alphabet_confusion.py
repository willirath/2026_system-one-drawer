# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "pillow", "python-dotenv"]
# ///
"""Which letter does Jev read in each evolved letter?

Needs `uv run run_examples.py alphabet 3` (results/presets-alphabet-all.json).
Asks one `choice` question per final drawing: A-Z or no letter. Random block
drawings that never saw Jev are the baseline: letters they come out as come
free. Run from the project root:

    uv run experiments/alphabet_confusion.py
"""

import json
import os
import random
import string
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

sys.path.insert(0, os.getcwd())
import run_examples as rx  # noqa: E402
from confusion import final  # noqa: E402

QUESTION = {
    "type": "choice",
    "instructions": "Which capital letter does the image show?",
    "criteria": {**{c: f"The image shows the capital letter {c}." for c in string.ascii_uppercase},
                 "none": "The image shows no letter."},
}


def main():
    runs = json.loads(Path("results/presets-alphabet-all.json").read_text())
    items = [(r["object"][-1], r["p"], final(r)) for r in runs]
    rng = random.Random(5)
    for _ in range(26):
        kept, n = [], rng.randint(2, rx.MAX_BLOCKS)
        for _ in range(n):
            if len(kept) < n:
                kept += rx.random_blocks(rng, kept, n - len(kept))
        items.append(("-", None, kept))
    headers = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}
    with httpx.Client(headers=headers) as client, ThreadPoolExecutor(4) as pool:
        resps = list(pool.map(lambda it: rx.decide(client, rx.render(it[2]), {"what": QUESTION}), items))
    rows = [{"letter": l, "noul": p, "read_as": r["answers"]["what"]["choice"],
             "p_own": r["answers"]["what"]["probabilities"].get(l)} for (l, p, _), r in zip(items, resps)]
    Path("results/alphabet_confusion.json").write_text(json.dumps(rows, indent=1))
    print("letter  noul (3 runs)        read as     P(own letter)")
    for l in string.ascii_uppercase:
        rs = [r for r in rows if r["letter"] == l]
        print(f"{l:6s}  {' '.join(f'{r['noul']:.2f}' for r in rs):20s} {' '.join(r['read_as'] for r in rs):10s}  "
              + " ".join(f"{r['p_own']:.2f}" for r in rs))
    letters = [r for r in rows if r["letter"] != "-"]
    hits = sum(r["read_as"] == r["letter"] for r in letters)
    best = {l: max((r for r in letters if r["letter"] == l), key=lambda r: r["noul"]) for l in string.ascii_uppercase}
    print(f"\nread correctly: {hits}/{len(letters)} runs; best run per letter (as in the app): "
          f"{sum(r['read_as'] == l for l, r in best.items())}/26")
    print("random drawings read as:", dict(Counter(r["read_as"] for r in rows if r["letter"] == "-").most_common()))
    print(f"cost ${sum(r.get('usage', {}).get('cost', 0) for r in resps):.4f}")


if __name__ == "__main__":
    main()
