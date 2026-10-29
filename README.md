# System-One Drawer

A static page that draws an object from toy building blocks, with TypeSafe's Jev
(through OpenRouter) as the only judge. Jev is a fast decision model: it answers
typed questions about an image with probabilities and gives no reasons.

Blocks are a square (A x A), a double square (2A x A), a half square (legs A,
long side sqrt(2) A) and a roof (base 2A, height A), on an 8A x 8A canvas. They
turn in quarter turns and only meet flat face to flat face, overlapping by at
least A/2. Slanted faces don't attach.

The drawing evolves: 10 drawings at once. Each generation, Jev sees them side
by side in a 5 x 2 grid of framed panels (shuffled every generation) and answers
a `choice` question, "Which panel shows a(n) <object>?". Its probabilities are
the fitness. The pick carries over unchanged. The other 9 are children of
parents drawn in proportion to fitness: half by crossover (one parent's blocks
on one side of a random vertical or horizontal cut, the other parent's on the
other side, keeping the largest connected group), half by mutation (1-3 blocks
added, or 30% of the time one block taken away while the rest stays connected).
A `noul` question on the pick, "The image shows a(n) <object>.", tracks
progress. A run stops at P > 0.66 or after 30 generations, and its result is the
pick with the highest P.

## Web app

[public/index.html](public/index.html) is the whole app. It replays the canned
runs for house, rocket and boat step by step, and draws any object you type,
live, with your own OpenRouter key. The key goes straight from the browser to
openrouter.ai.

GitHub Pages serves `public/` via
[.github/workflows/pages.yml](.github/workflows/pages.yml), and GitLab Pages via
[.gitlab-ci.yml](.gitlab-ci.yml). Locally: `python3 -m http.server -d public`.

## Canned runs

```bash
cp .env.template .env                 # then paste an OpenRouter key
uv run run_examples.py genetic 8      # 8 runs each of house, rocket, boat
uv run run_examples.py genetic-nox 8  # without crossover, for comparison
uv run run_examples.py genetic 3 dog  # or: mode, runs per object, objects
```

This writes `public/presets-genetic.js` (`results/presets-genetic-nox.json` for
the comparison) and the final drawings to `results/<mode>/`.

## Results (2026-10-29, `typesafe/jev-1.14`)

8 runs per target. Final P, and how many runs reached P > 0.66. "Random walk"
is an earlier mode, since removed: each try added 1-3 blocks and was kept unless
P went down, for up to 80 tries.

| target | random walk        | genetic            | genetic, no crossover |
|--------|--------------------|--------------------|-----------------------|
| house  | 0.37-0.67 (1 of 8) | 0.63-0.81 (7 of 8) | 0.37-0.75 (4 of 8)    |
| rocket | 0.46-0.67 (1 of 8) | 0.57-0.70 (5 of 8) | 0.58-0.73 (2 of 8)    |
| boat   | 0.31-0.60 (0 of 8) | 0.43-0.59 (0 of 8) | 0.39-0.61 (0 of 8)    |
| total  | 2 of 24            | 12 of 24           | 6 of 24               |
| calls  | 1720, $0.051       | 872, $0.046        | 1072, $0.056          |

Can Jev tell the final drawings apart? [experiments/confusion.py](experiments/confusion.py)
asks one `choice` question per final drawing: house, rocket, boat or something
else. Rows are what the genetic drawing was made for, columns Jev's answer:

| genetic | house | rocket | boat | other |
|---------|-------|--------|------|-------|
| house   | 7     | 1      | 0    | 0     |
| rocket  | 0     | 8      | 0    | 0     |
| boat    | 1     | 3      | 3    | 1     |

That is 18 of 24 correct for genetic, against 13 of 24 without crossover and 12
of 24 for the random walk. 24 random block drawings that never saw Jev
come out as rocket 8 times and other 16 times, so rocket is Jev's default guess
for tall junk and part of the rocket score comes free. House and boat never come
out of random drawings.

Genetic runs end in small, legible icons: a roof on a block for a house, a tall
bar with a pointed nose for a rocket, a flat hull with a triangle for a boat.
Random-walk runs end as piles of 10-30 blocks. Crossover doubles the hit rate
and raises the share of drawings Jev names correctly. Boat is the hard target:
genetic boats look like boats (or fish) but stay below 0.6.

Jev's `choice` over the grid is often sharp (the pick gets 0.6-0.8) even when the
pick's own `noul` is low, so ranking works better than judging in absolute terms.
When no panel looks like the target, the choice probabilities are flat and
selection is close to random. A choice call on the 10-panel grid costs about
2.5 times a single noul call.

## Earlier experiments

- **Polygons:** an earlier mode drew filled 3-6 corner polygons instead of
  blocks. It trailed blocks on every target and was removed.
- **Random walk:** see the results table. It ends in piles of 10-30 blocks and
  was removed in favour of the genetic mode.
- **Targets:** [experiments/screen_targets.py](experiments/screen_targets.py)
  asks Jev about 24 candidate targets on canned and random drawings. Tower,
  staircase and arrow score about 0.35 on random block piles, so reaching 0.66
  there is mostly luck. Elephant, cat and tree stayed below 0.66. House, rocket
  and boat score low on random piles and are three different shapes: boxy with a
  roof, tall and thin, wide and flat.
- **Dense random fields:** [experiments/dense_box.py](experiments/dense_box.py)
  drew dense random polygon fields and asked Jev for a `box` around the target.
  The box mostly covers the whole canvas, doesn't depend on the target, and its
  readouts score no better than random boxes of the same size.
- **Noise:** asking again about the same image barely moves P (by 0.005-0.035),
  but small changes to a drawing can move it a lot. Keeping a try whenever P
  rises therefore partly selects lucky drawings, which inflates random-walk
  scores.
