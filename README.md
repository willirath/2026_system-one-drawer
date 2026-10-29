# System-One Drawer

A static page that draws the alphabet from toy building blocks, with TypeSafe's
Jev (through OpenRouter) as the only judge. An explore page does the same for
any object. Jev is a fast decision model: it answers
typed questions about an image with probabilities and gives no reasons.

Blocks are a square (A x A), a double square (2A x A), a half square (legs A,
long side sqrt(2) A) and a roof (base 2A, height A), on a 12A x 12A canvas, at
most 8 per drawing. Blocks meet face to face: two faces on the same line,
overlapping by at least A/2. Triangles turn in quarter turns; squares and double
squares can also sit diagonally (45 degrees). Since the only diagonal faces are a
triangle's slanted sides, a diagonal block attaches to a triangle's slanted face
or to another diagonal block, and a triangle's slanted face leads back to
straight blocks. After each change the drawing is recentred on the canvas, so
it can grow in any direction until it spans 12A.

The drawing evolves: 10 drawings at once. Each generation, Jev sees them side
by side in a 5 x 2 grid of framed panels (shuffled every generation) and answers
a `choice` question, "Which panel shows a(n) <object>?". Its probabilities are
the fitness. For the pick, Jev also answers a `noul` question, "The image shows
a(n) <object>."; that P tracks progress. Two drawings carry over unchanged: the
pick, and the best drawing so far (highest P, replaced only when a pick scores
at least as high), so the population never loses its best drawing. The others
are children of parents drawn in proportion to fitness: half by crossover (one
parent's blocks on one side of a random vertical or horizontal cut, the other
parent's on the other side, keeping the largest connected group), half by
mutation (1-3 blocks added up to the cap of 8, or 30% of the time one block
taken away while the rest stays connected). A run stops at P > 0.66 or after 30 generations, and its
result is the best drawing.

## Web app

The page shows the alphabet: [public/index.html](public/index.html) replays the
best canned run per capital letter and digit step by step and draws any letter
live, with your own OpenRouter key. The key goes straight from the browser to
openrouter.ai.

Word mode ([public/words.js](public/words.js)) sets a word of up to 7 letters
or digits (SYSTEM1 to start with) from the canned letters, free and without a
key. With a key, Evolve improves the word as a whole: 10 versions per
generation, each child changing one letter (mutation) or taking each letter from
one of two parents (crossover). Jev picks from a 2 x 5 grid, "Which panel shows
the word ...?", and its `noul` P on "The image shows the word ..." tracks
progress, with the same keep-the-best rule and limits as single drawings.
[public/explore/](public/explore/index.html) does the same for objects (house,
rocket, flagpole with flag) and draws anything you type. Both pages share
[public/drawer.js](public/drawer.js) and [public/style.css](public/style.css).

GitHub Pages serves `public/` via
[.github/workflows/pages.yml](.github/workflows/pages.yml), and GitLab Pages via
[.gitlab-ci.yml](.gitlab-ci.yml). Locally: `python3 -m http.server -d public`.

## Canned runs

```bash
cp .env.template .env                 # then paste an OpenRouter key
uv run run_examples.py alphabet 3     # 3 runs each of "capital letter A" ... "Z", "digit 0" ... "9"
uv run run_examples.py genetic 8      # explore/: 8 runs each of house, rocket, flagpole with flag
uv run run_examples.py genetic-nox 8  # without crossover, for comparison
uv run run_examples.py genetic 3 dog  # or: mode, runs per object, objects
```

The alphabet's best run per letter goes to `public/presets-alphabet.js`, all its
runs to `results/presets-alphabet-all.json`. The default genetic runs go to
`public/explore/presets-objects.js`. Everything else goes to
`results/presets-<mode>[-<objects>].json`. Final drawings land in
`results/<mode>/`.

## Results (2026-10-29, `typesafe/jev-1.14`)

Canned runs: 12A x 12A canvas, at most 8 blocks, diagonal blocks, recentring.

**Objects**, 8 runs each:

| target             | final P   | P > 0.66 | named correctly |
|--------------------|-----------|----------|-----------------|
| house              | 0.44-0.80 | 6 of 8   | 7 of 8          |
| rocket             | 0.60-0.72 | 3 of 8   | 8 of 8          |
| flagpole with flag | 0.47-0.78 | 1 of 8   | 6 of 8          |
| total              |           | 10 of 24 | 21 of 24        |

915 Jev calls, $0.050. "Named correctly" comes from
[experiments/confusion.py](experiments/confusion.py) (see below). Before
diagonals and recentring, Jev named only 2-3 of 8 flagpoles correctly and called
the rest "rocket". Random drawings still come out as rocket 9 times in 24, so
part of the rocket score comes free.

**Alphabet**, 3 runs per capital letter, the best shown in the app.
[experiments/alphabet_confusion.py](experiments/alphabet_confusion.py) asks Jev
which letter (A-Z or none) each final drawing shows. It reads 38 of 78 runs as
the intended letter, and 14 of the 26 best runs: F G I J K L S T U V W X Y Z.
Letters made of straight strokes work; round ones (B, D, O, Q) and busy ones
(E, H, M) don't. Random block drawings come out as K 13 times in 26, so K comes
free. Diagonals and recentring raised the correct reads from 26 to 38 of 78.
3491 Jev calls, $0.19.

**Digits**, 3 runs each ("digit 0" ... "digit 9"): 1 and 4 reach P > 0.66 in
all their runs; round digits stay low (0: 0.16, 8: 0.26, 6: 0.30 at best). 1278
Jev calls, $0.07.

The comparisons below ran on the earlier 8A x 8A canvas with house, rocket and
boat. Final P, and how many runs reached P > 0.66. "Random walk"
is an earlier mode, since removed: each try added 1-3 blocks and was kept unless
P went down, for up to 80 tries.

| target | random walk        | genetic            | genetic, no crossover |
|--------|--------------------|--------------------|-----------------------|
| house  | 0.37-0.67 (1 of 8) | 0.63-0.79 (7 of 8) | 0.30-0.71 (5 of 8)    |
| rocket | 0.46-0.67 (1 of 8) | 0.58-0.71 (4 of 8) | 0.55-0.74 (3 of 8)    |
| boat   | 0.31-0.60 (0 of 8) | 0.47-0.65 (0 of 8) | 0.42-0.70 (2 of 8)    |
| total  | 2 of 24            | 11 of 24           | 10 of 24              |
| calls  | 1720, $0.051       | 897, $0.049        | 897, $0.049           |

Can Jev tell the final drawings apart? [experiments/confusion.py](experiments/confusion.py)
asks one `choice` question per final drawing: house, rocket, boat or something
else. Rows are what the genetic drawing (8A canvas) was made for, columns Jev's
answer:

| genetic | house | rocket | boat | other |
|---------|-------|--------|------|-------|
| house   | 8     | 0      | 0    | 0     |
| rocket  | 0     | 8      | 0    | 0     |
| boat    | 3     | 1      | 3    | 1     |

That is 19 of 24 correct, the same as without crossover (19 of 24) and well
ahead of the random walk (12 of 24). Random block drawings that never saw Jev
come out as rocket 9 times in 24 and other 15 times, so rocket is Jev's default
guess for tall junk and part of the rocket score comes free. House and boat
never come out of random drawings.

Genetic runs end in small, legible icons: a roof on a block for a house, a tall
bar with a pointed nose for a rocket, a flat hull with a triangle for a boat.
Random-walk runs end as piles of 10-30 blocks. Boat is the hard target.

An earlier version carried over only the current pick, so the population lost
its best drawing whenever Jev picked a child instead (80% of generations). Back
then crossover looked like it doubled the hit rate (12 against 6 of 24). With
the best drawing kept, the two are level (11 against 10), so most of that gap
came from losing good drawings, not from crossover itself.

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
- **Wording:** [experiments/house_wording.py](experiments/house_wording.py)
  evolved houses for "simple house", "schematic house" and "drawing of a house"
  and asked every final drawing all four wordings. "Schematic house" hits its own
  target in 8 of 8 runs, but random junk already scores 0.43 on it (0.28 for
  "house"), and its drawings score lowest on the shared questions. Drawings
  evolved for plain "house" score highest on every wording, so the target stays
  "house".
- **Noise:** asking again about the same image barely moves P (by 0.005-0.035),
  but small changes to a drawing can move it a lot. Keeping a try whenever P
  rises therefore partly selects lucky drawings, which inflates random-walk
  scores.
