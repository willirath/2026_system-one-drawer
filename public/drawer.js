// The block drawer, shared by the alphabet page and explore/. Each page sets DRAWER first:
// runs (canned runs to show), letters (show them as an alphabet grid), object() (what to draw
// live, or null if the input is not usable) and hint (what to say then).
const MODEL = "typesafe/jev-1.14";
const SIZE = 384, TARGET = 0.66;
const KEPT = "rgba(0,0,0,0.7)";
// Blocks live on a GRID x GRID canvas in grid units; a block side A is 2 units.
// kind: [unrotated outline in grid units, all wound the same way so that faces that touch run in
// opposite directions; the turns it may take in 45 degree steps]. Squares and double squares can
// sit diagonally; triangles only take quarter turns.
const GRID = 24, EPS = 1e-4;
const BLOCKS = {
  square: [[[0, 0], [2, 0], [2, 2], [0, 2]], [0, 1, 2, 3, 4, 5, 6, 7]],  // A x A
  double: [[[0, 0], [4, 0], [4, 2], [0, 2]], [0, 1, 2, 3, 4, 5, 6, 7]],  // 2A x A
  half: [[[0, 0], [2, 2], [0, 2]], [0, 2, 4, 6]],                       // half square: legs A, long side sqrt(2) A
  roof: [[[0, 2], [2, 0], [4, 2]], [0, 2, 4, 6]],                       // base 2A, height A
};
// drawings per generation, and the grid sheet Jev sees, in pixels
const POP = 10, GENERATIONS = 30, P_CROSS = 0.5, P_REMOVE = 0.3, MAX_BLOCKS = 8, COLS = 5, CELL = 200, BORDER = 4;
const $ = (id) => document.getElementById(id);

try {
  const k = localStorage.getItem("orKey");
  if (k) { $("key").value = k; $("remember").checked = true; }
} catch (e) {}

// ---- drawing loop (same as run_examples.py) ----

const randint = (a, b) => a + Math.floor(Math.random() * (b - a + 1));
const pick = (xs) => xs[randint(0, xs.length - 1)];

// The block's outline after k turns of 45 degrees (clockwise on screen) about its first corner.
function turned(kind, k) {
  const c = Math.cos(k * Math.PI / 4), s = Math.sin(k * Math.PI / 4);
  return BLOCKS[kind][0].map(([x, y]) => [x * c - y * s, x * s + y * c]);
}
const faces = (pts) => pts.map((p, i) => [p, pts[(i + 1) % pts.length]]);
const unit = (p, q) => { const d = Math.hypot(q[0] - p[0], q[1] - p[1]); return [(q[0] - p[0]) / d, (q[1] - p[1]) / d, d]; };
// Presets store outlines in grid units; scale them to the canvas once.
const placed = (b) => b.points || (b.points = b.pts.map(([x, y]) => [x / GRID, y / GRID]));

// Whether two convex outlines overlap by more than a touch (separating axis test).
function overlap(a, b) {
  for (const [[x1, y1], [x2, y2]] of [...faces(a), ...faces(b)]) {
    const nx = y1 - y2, ny = x2 - x1;
    const pa = a.map(([x, y]) => x * nx + y * ny), pb = b.map(([x, y]) => x * nx + y * ny);
    if (Math.max(...pa) <= Math.min(...pb) + EPS || Math.max(...pb) <= Math.min(...pa) + EPS) return false;
  }
  return true;
}

// A block flush against a face of a random kept block, overlapping it by at least A/2. Faces only
// meet when they lie on the same line, so a diagonal block can only sit on a triangle's slanted
// face or on another diagonal block. The first block sits straight, near the centre.
// Returns null if no free spot turns up.
function randomBlock(kept) {
  for (let tries = 0; tries < 1000; tries++) {
    const kind = pick(Object.keys(BLOCKS)), k = pick(BLOCKS[kind][1]);
    let pts = turned(kind, k), dx, dy;
    if (kept.length) {
      const [p1, p2] = pick(faces(pick(kept).pts)), [ux, uy, la] = unit(p1, p2);
      const match = faces(pts).filter(([q1, q2]) => { const [vx, vy] = unit(q1, q2); return ux * vx + uy * vy < -1 + EPS; });
      if (!match.length) continue;
      const [q1, q2] = pick(match), lb = unit(q1, q2)[2];
      const t = 1 - lb + randint(0, Math.floor(la + lb - 2 + EPS));  // q2 sits t along the face, in steps of A/2
      dx = p1[0] + t * ux - q2[0]; dy = p1[1] + t * uy - q2[1];
    } else {
      if (k % 2) continue;
      const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
      const w = Math.round(Math.max(...xs) - Math.min(...xs)), h = Math.round(Math.max(...ys) - Math.min(...ys));
      dx = randint(GRID / 2 - 4, GRID / 2 + 4 - w) - Math.min(...xs);  // near the centre
      dy = randint(GRID / 2 - 4, GRID / 2 + 4 - h) - Math.min(...ys);
    }
    pts = pts.map(([x, y]) => [Math.round((x + dx) * 1e6) / 1e6, Math.round((y + dy) * 1e6) / 1e6]);
    // the drawing is recentred after each change, so only its total size must fit the canvas
    const all = [...kept.flatMap((b) => b.pts), ...pts], xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
    if (Math.max(...xs) - Math.min(...xs) > GRID + EPS || Math.max(...ys) - Math.min(...ys) > GRID + EPS) continue;
    if (kept.some((b) => overlap(pts, b.pts))) continue;
    return block(kind, k, pts);
  }
  return null;
}

const block = (kind, rot, pts) => ({ kind, rot, pts, points: pts.map(([x, y]) => [x / GRID, y / GRID]) });

// Shift the drawing by whole grid units so its bounding box sits in the middle of the canvas.
function recenter(blocks) {
  const all = blocks.flatMap((b) => b.pts), xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
  const shift = (lo, hi) => Math.min(Math.max(Math.round(GRID / 2 - (lo + hi) / 2), -lo), GRID - hi);
  const dx = shift(Math.min(...xs), Math.max(...xs)), dy = shift(Math.min(...ys), Math.max(...ys));
  if (!dx && !dy) return blocks;
  return blocks.map((b) => block(b.kind, b.rot, b.pts.map(([x, y]) => [Math.round((x + dx) * 1e6) / 1e6, Math.round((y + dy) * 1e6) / 1e6])));
}

// 1-3 blocks (at most room), each against a kept block or an earlier one of the group.
function randomBlocks(kept, room = 3) {
  const group = [];
  for (let n = Math.min(randint(1, 3), room); n > 0; n--) {
    const b = randomBlock([...kept, ...group]);
    if (!b) break;
    group.push(b);
  }
  return group;
}

// Whether two blocks meet face to face: faces on the same line, running opposite ways, overlapping.
function touching(a, b) {
  for (const [p1, p2] of faces(a.pts)) {
    const [ux, uy, la] = unit(p1, p2);
    for (const [q1, q2] of faces(b.pts)) {
      const [vx, vy] = unit(q1, q2);
      if (ux * vx + uy * vy > -1 + EPS || Math.abs(ux * (q1[1] - p1[1]) - uy * (q1[0] - p1[0])) > EPS) continue;
      const [s1, s2] = [q1, q2].map((q) => (q[0] - p1[0]) * ux + (q[1] - p1[1]) * uy).sort((x, y) => x - y);
      if (Math.min(la, s2) - Math.max(0, s1) > EPS) return true;
    }
  }
  return false;
}

// Index groups of blocks that connect flat face to flat face.
function components(blocks) {
  const left = new Set(blocks.keys()), groups = [];
  while (left.size) {
    const [first] = left, group = [first], todo = [first];
    left.delete(first);
    while (todo.length) {
      const i = todo.pop();
      for (const j of [...left]) {
        if (touching(blocks[i], blocks[j])) { left.delete(j); group.push(j); todo.push(j); }
      }
    }
    groups.push(group);
  }
  return groups;
}

// Add 1-3 blocks (up to MAX_BLOCKS) or, sometimes, take away one whose removal keeps the rest connected.
function mutate(parent) {
  if (parent.length > 1 && (parent.length >= MAX_BLOCKS || Math.random() < P_REMOVE)) {
    const without = (i) => parent.filter((_, j) => j !== i);
    const removable = parent.map((_, i) => i).filter((i) => components(without(i)).length === 1);
    if (removable.length) return { shapes: recenter(without(removable[randint(0, removable.length - 1)])), op: "remove" };
  }
  return { shapes: recenter([...parent, ...randomBlocks(parent, MAX_BLOCKS - parent.length)]), op: "add" };
}

// Blocks of a on one side of a random vertical or horizontal cut, blocks of b on the other.
// b's blocks that overlap a's are dropped, and only the largest connected group stays.
function cross(a, b) {
  const axis = randint(0, 1), mid = (blk) => blk.pts.reduce((m, pt) => m + pt[axis], 0) / blk.pts.length;  // axis 0: left/right halves
  const mids = [...a, ...b].map(mid), lo = Math.min(...mids), cut = lo + Math.random() * (Math.max(...mids) - lo);
  const child = a.filter((blk) => mid(blk) < cut);
  child.push(...b.filter((blk) => mid(blk) >= cut && !child.some((c) => overlap(blk.pts, c.pts))));
  if (!child.length) return [...a];
  return recenter(components(child).reduce((x, y) => y.length > x.length ? y : x).map((i) => child[i]));
}

const same = (a, b) => {
  const key = (blocks) => blocks.map((blk) => [blk.kind, blk.rot, ...blk.pts.flat().map((v) => v.toFixed(3))].join()).sort().join(";");
  return key(a) === key(b);
};

// One child: crossover of two parents drawn by fitness, or a mutated copy of one.
function childOf(cands, probs) {
  const total = probs.reduce((x, y) => x + y, 0), draw = () => {
    let r = Math.random() * total, i = 0;
    while (i < probs.length - 1 && (r -= probs[i]) > 0) i++;
    return i;
  };
  const i = draw(), j = draw(), a = cands[i].shapes, b = cands[j].shapes;
  if (Math.random() < P_CROSS && !same(a, b)) {
    const shapes = cross(a, b);
    if (shapes.length <= MAX_BLOCKS && !same(shapes, a) && !same(shapes, b)) return { shapes, parent: i, parent2: j, op: "cross" };
  }
  return { ...mutate(a), parent: i };
}

function paint(ctx, shapes, fill, ox = 0, oy = 0, w = ctx.canvas.width) {
  ctx.fillStyle = fill;
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 2 * w / SIZE;
  for (const s of shapes) {
    ctx.beginPath();
    placed(s).forEach(([x, y], i) => i ? ctx.lineTo(ox + x * w, oy + y * w) : ctx.moveTo(ox + x * w, oy + y * w));
    ctx.closePath();
    ctx.fill();
    ctx.stroke();  // a white edge keeps neighbouring blocks apart
  }
}

function clear(ctx) {
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height);
}

// The drawings side by side in framed panels, COLS per row, as Jev sees them.
function drawSheet(ctx, drawings) {
  clear(ctx);
  const inner = CELL - 2 * BORDER - 8;
  drawings.forEach((shapes, i) => {
    const x = i % COLS * CELL, y = Math.floor(i / COLS) * CELL;
    paint(ctx, shapes, KEPT, x + BORDER + 4, y + BORDER + 4, inner);
    ctx.strokeStyle = "#000";
    ctx.lineWidth = BORDER;
    ctx.strokeRect(x + BORDER / 2, y + BORDER / 2, CELL - BORDER, CELL - BORDER);
  });
}

function pngOf(shapes) {
  const c = document.createElement("canvas");
  c.width = c.height = SIZE;
  const ctx = c.getContext("2d");
  clear(ctx);
  paint(ctx, shapes, KEPT);
  return c.toDataURL("image/png");
}

async function decide(key, uri, questions) {
  for (let attempt = 0; ; attempt++) {
    const r = await fetch("https://openrouter.ai/api/alpha/decisions", {
      method: "POST",
      headers: { "Authorization": `Bearer ${key}`, "Content-Type": "application/json" },
      body: JSON.stringify({ model: MODEL, state: [{ type: "image_url", image_url: { url: uri } }], questions }),
    });
    if ([429, 500, 502, 503].includes(r.status) && attempt < 4) {  // back off and retry, e.g. when many runs go at once
      await new Promise((ok) => setTimeout(ok, 1000 * 2 ** attempt));
      continue;
    }
    if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
    return r.json();
  }
}

const article = (obj) => /^[aeiou]/i.test(obj) ? "an" : "a";
const ask = (key, shapes, obj) =>
  decide(key, pngOf(shapes), { is: { type: "noul", instructions: `The image shows ${article(obj)} ${obj}.` } });

function choiceQuestion(obj, n) {
  const a = `${article(obj)} ${obj}`, criteria = {};
  for (let i = 0; i < n; i++) {
    criteria[i + 1] = `Panel ${i + 1} (row ${Math.floor(i / COLS) + 1}, column ${i % COLS + 1}) shows ${a}.`;
  }
  return {
    type: "choice", criteria,
    instructions: `The image is a grid of ${n} framed panels, ${COLS} per row, numbered left to right, `
      + `top to bottom. Which panel shows ${a}?`,
  };
}

// ---- player: shows a run generation by generation ----

let run = null, pos = 1, timer = null;
const last = () => run.generations.length + 1;  // the final-drawing position
const bestGen = (r) => r.generations.reduce((a, g) => g.p >= a.p ? g : a);
const finalShapes = (r) => ((g) => g.candidates[g.best].shapes)(bestGen(r));

function render() {
  $("slider").max = last();
  $("slider").value = pos;
  renderGeneration();
  chart();
}

// pos 1..N shows generation pos: Jev's grid with its choice probabilities, and the pick large.
// pos N+1 shows the pick with the highest P.
function renderGeneration() {
  const gens = run.generations, n = gens.length, ctx = $("view").getContext("2d"), cap = $("caption");
  const g = gens[Math.min(pos, n) - 1], sh = $("sheet").getContext("2d");
  sh.canvas.height = Math.ceil(g.candidates.length / COLS) * CELL;
  drawSheet(sh, g.candidates.map((c) => c.shapes));
  g.candidates.forEach((c, i) => {  // annotations for the viewer only, not sent to Jev
    const x = i % COLS * CELL, y = Math.floor(i / COLS) * CELL, pick = i === g.best;
    if (pick) { sh.strokeStyle = "#1a7f37"; sh.lineWidth = 3 * BORDER; sh.strokeRect(x + 6, y + 6, CELL - 12, CELL - 12); }
    sh.font = "bold 22px system-ui, sans-serif";
    sh.fillStyle = "rgba(255,255,255,0.85)";
    sh.fillRect(x + 12, y + 12, 58, 28);
    sh.fillStyle = pick ? "#1a7f37" : "#333";
    sh.fillText(g.probs[i].toFixed(2), x + 16, y + 34);
    sh.font = "16px system-ui, sans-serif";
    sh.fillStyle = "#666";
    sh.textAlign = "right";
    sh.fillText({ seed: "new", best: "best", elite: "last pick", add: "+", remove: "−", cross: "×" }[c.op], x + CELL - 14, y + 30);
    sh.textAlign = "left";
  });
  clear(ctx);
  if (pos > n) {
    paint(ctx, finalShapes(run), KEPT);
    cap.className = run.p > TARGET ? "yes" : "";
    cap.textContent = `Final ${run.object}: P = ${run.p.toFixed(2)}, best pick of ${n} generations`;
  } else {
    paint(ctx, g.candidates[g.best].shapes, KEPT);
    cap.className = g.p > TARGET ? "yes" : "";
    cap.textContent = `Generation ${pos}/${n}: Jev picks panel ${g.best + 1} (${g.probs[g.best].toFixed(2)}), P = ${g.p.toFixed(2)}`;
  }
}

// P of each generation's pick (green: new best), best-so-far as a step line, target dashed.
function chart() {
  const svg = $("chart"), gens = run.generations, n = Math.max(gens.length, 10), W = 384, H = 90, pad = 6;
  const x = (i) => pad + (W - 2 * pad) * (i + 0.5) / n, y = (p) => H - pad - (H - 2 * pad) * p;
  let line = "", b = -1, dots = "";
  gens.forEach((g, i) => {
    const up = g.p > b;
    b = Math.max(b, g.p);
    line += `${i ? "L" : "M"}${x(i - 0.5).toFixed(1)},${y(b).toFixed(1)}H${x(i + 0.5).toFixed(1)}`;
    const on = i === pos - 1;
    dots += `<circle cx="${x(i).toFixed(1)}" cy="${y(g.p).toFixed(1)}" r="${on ? 5 : 3}"
      fill="var(--${up ? "yes" : "muted"})" ${on ? 'stroke="var(--fg)" stroke-width="1.5"' : ""}/>`;
  });
  svg.innerHTML = `<line x1="0" x2="${W}" y1="${y(TARGET)}" y2="${y(TARGET)}" stroke="var(--muted)" stroke-dasharray="4 3"/>
    <text x="${W - 2}" y="${y(TARGET) - 3}" font-size="10" text-anchor="end" fill="var(--muted)">0.66</text>
    <path d="${line}" fill="none" stroke="var(--fg)" stroke-width="1.5" vector-effect="non-scaling-stroke"/>${dots}`;
}

function go(k) { stopPlay(); pos = Math.max(1, Math.min(last(), k)); render(); }
function stopPlay() { clearInterval(timer); timer = null; $("play").textContent = "▶ Play"; }
function play() {
  if (timer) return stopPlay();
  if (pos >= last()) pos = 0;
  $("play").textContent = "⏸ Pause";
  timer = setInterval(() => {
    pos++;
    render();
    if (pos >= last()) stopPlay();
  }, 500);
}
$("first").onclick = () => go(1);
$("prev").onclick = () => go(pos - 1);
$("next").onclick = () => go(pos + 1);
$("last").onclick = () => go(Infinity);
$("play").onclick = play;
$("slider").oninput = () => go(+$("slider").value);

function show(r, note) {
  stopPlay();
  run = r;
  pos = 0;
  $("note").textContent = note;
  play();
}

// ---- gallery of canned runs ----

let thumbs = [];

function thumb(p, label) {
  const fig = document.createElement("figure"), c = document.createElement("canvas");
  c.width = c.height = 160;
  const ctx = c.getContext("2d");
  clear(ctx);
  paint(ctx, finalShapes(p), KEPT);
  const cap = document.createElement("figcaption");
  cap.textContent = `${label}P = ${p.p.toFixed(2)}`;
  if (p.p > TARGET) cap.className = "yes";
  fig.append(c, cap);
  fig.onclick = () => {
    thumbs.forEach((t) => t.classList.remove("sel"));
    fig.classList.add("sel");
    show(p, `Canned run: ${p.object}, seed ${p.seed}, ${p.model}, cost $${p.cost.toFixed(5)}.`);
  };
  thumbs.push(fig);
  return fig;
}

// Letters: one run per letter, in one grid. Objects: all runs, a row per object.
function gallery() {
  const runs = DRAWER.runs;
  if (DRAWER.letters) {
    const grid = document.createElement("div");
    grid.className = "gallery letters";
    runs.forEach((p) => grid.append(thumb(p, `${p.object.slice(-1)} · `)));
    $("gallery").append(grid);
  } else {
    for (const obj of new Set(runs.map((p) => p.object))) {
      const label = document.createElement("div"), grid = document.createElement("div");
      label.className = "row-label";
      label.textContent = obj;
      grid.className = "gallery";
      runs.filter((p) => p.object === obj).forEach((p) => grid.append(thumb(p, "")));
      $("gallery").append(label, grid);
    }
  }
  const best = runs.reduce((a, p, i) => a < 0 || p.p > runs[a].p ? i : a, -1);
  if (best >= 0) thumbs[best].click();  // start with the best canned run
}
gallery();

// ---- live run ----

// One genetic run for obj. Calls onGeneration(run, status) after each generation and stops early
// when isCancelled() says so. Returns the run: { object, generations, p, stop, cost, calls, model }.
async function evolve(key, obj, onGeneration, isCancelled) {
  const r = { object: obj, model: MODEL, p: -1, stop: "", cost: 0, calls: 0, generations: [] };
  const tally = (resp) => { r.model = resp.model || MODEL; r.cost += (resp.usage && resp.usage.cost) || 0; r.calls++; };
  const sheet = document.createElement("canvas");
  sheet.width = COLS * CELL;
  sheet.height = Math.ceil(POP / COLS) * CELL;
  let cands = Array.from({ length: POP }, () => ({ shapes: recenter(randomBlocks([])), parent: null, op: "seed" })), top = null;
  while (!r.stop) {
    for (let i = cands.length - 1; i > 0; i--) {  // new panel positions every generation, against position bias
      const j = randint(0, i);
      [cands[i], cands[j]] = [cands[j], cands[i]];
    }
    drawSheet(sheet.getContext("2d"), cands.map((c) => c.shapes));
    let resp = await decide(key, sheet.toDataURL("image/png"), { pick: choiceQuestion(obj, POP) });
    tally(resp);
    const probs = cands.map((_, i) => resp.answers.pick.probabilities[i + 1] || 0);
    const best = probs.indexOf(Math.max(...probs));
    let p = cands[best].p;  // known for drawings carried over
    if (p === undefined) {
      resp = await ask(key, cands[best].shapes, obj);
      tally(resp);
      p = resp.answers.is.noul;
    }
    r.generations.push({ candidates: cands, probs, best, p });
    if (p >= r.p) { r.p = p; top = cands[best].shapes; }
    r.stop = isCancelled() ? "stopped" : p > TARGET ? "target" : r.generations.length >= GENERATIONS ? "generations" : "";
    onGeneration(r);
    if (r.stop) break;
    // the best so far and the current pick survive; children fill the rest
    const anchor = cands.findIndex((c) => c.shapes === top);
    const next = [{ shapes: top, parent: anchor, op: "best", p: r.p }];
    if (best !== anchor) next.push({ shapes: cands[best].shapes, parent: best, op: "elite", p });
    while (next.length < POP) next.push(childOf(cands, probs));
    cands = next;
  }
  return r;
}

const STOPS = { stopped: "stopped by you", target: "P > 0.66", generations: "30 generations" };

async function evolveLive(key, obj, st) {
  st.textContent = "Generation 1: asking Jev…";
  run = await evolve(key, obj, (r) => {
    run = r;
    pos = r.generations.length;
    render();
    st.textContent = `Generation ${r.generations.length + 1}: asking Jev…`;
  }, () => cancelled);
  done(st);
}

function done(st) {
  st.textContent = `Done (${STOPS[run.stop]}), `
    + `${run.calls} Jev calls, cost $${run.cost.toFixed(5)}.`;
  pos = last();
  render();
}

let cancelled = false;
$("stop").onclick = () => { cancelled = true; };
$("go").onclick = async () => {
  const key = $("key").value.trim(), obj = DRAWER.object(), st = $("status");
  if (!key) { st.textContent = "Enter an OpenRouter key."; return; }
  if (!obj) { st.textContent = DRAWER.hint; return; }
  try { $("remember").checked ? localStorage.setItem("orKey", key) : localStorage.removeItem("orKey"); } catch (e) {}
  thumbs.forEach((t) => t.classList.remove("sel"));
  stopPlay();
  cancelled = false;
  $("go").disabled = true; $("stop").disabled = false;
  $("note").textContent = `Live run: ${obj}, ${MODEL}.`;
  try {
    await evolveLive(key, obj, st);
  } catch (e) {
    st.textContent = e.message;
  } finally {
    $("go").disabled = false; $("stop").disabled = true;
  }
};
