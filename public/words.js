// Word tab: sets a word from the canned letters, evolves the whole word with Jev, and animates the
// evolution one generation at a time. Uses the blocks, mutation and API helpers of drawer.js.
const MAX_WORD = 7, GAP = 2;  // GAP: space between letters, in grid units
const WORD_W = 768, WORD_H = 384, WCOLS = 2, WCELL_W = 480, WCELL_H = 160;  // word image and Jev's grid sheet, in pixels
const CHANGED = "rgba(26,127,55,0.85)";  // letters that changed since the previous generation
const letterShapes = Object.fromEntries(DRAWER.runs.map((r) => [r.object.slice(-1), finalShapes(r)]));
const cannedWords = Object.fromEntries((PRESETS.word || []).map((r) => [r.object, r]));

// Paint a word (one block list per letter) into the box x, y, w, h: letters side by side,
// cropped to their outlines, all at one scale, centred. fills: one colour per letter.
function paintWord(ctx, word, x, y, w, h, fills = []) {
  const boxes = word.map((blocks) => {
    const xs = blocks.flatMap((b) => b.pts.map((p) => p[0])), ys = blocks.flatMap((b) => b.pts.map((p) => p[1]));
    return [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  });
  const width = boxes.reduce((sum, b) => sum + b[1] - b[0], 0) + GAP * (word.length - 1);
  const height = Math.max(...boxes.map((b) => b[3] - b[2]));
  const s = Math.min(w / (width + 2 * GAP), h / (height + 2 * GAP));
  let left = x + (w - width * s) / 2;
  word.forEach((blocks, i) => {
    const [x0, x1, y0, y1] = boxes[i];
    paint(ctx, blocks, fills[i] || KEPT, left - x0 * s, y + (h - (y1 - y0) * s) / 2 - y0 * s, s * GRID);
    left += (x1 - x0 + GAP) * s;
  });
}

function wordPng(word) {
  const c = document.createElement("canvas");
  c.width = WORD_W;
  c.height = WORD_H;
  const ctx = c.getContext("2d");
  clear(ctx);
  paintWord(ctx, word, 0, 0, WORD_W, WORD_H);
  return c.toDataURL("image/png");
}

// The words in framed panels, WCOLS per row, as Jev sees them.
function drawWordSheet(ctx, words) {
  clear(ctx);
  words.forEach((word, i) => {
    const x = i % WCOLS * WCELL_W, y = Math.floor(i / WCOLS) * WCELL_H;
    paintWord(ctx, word, x + BORDER + 4, y + BORDER + 4, WCELL_W - 2 * BORDER - 8, WCELL_H - 2 * BORDER - 8);
    ctx.strokeStyle = "#000";
    ctx.lineWidth = BORDER;
    ctx.strokeRect(x + BORDER / 2, y + BORDER / 2, WCELL_W - BORDER, WCELL_H - BORDER);
  });
}

function wordChoice(text, n) {
  const criteria = {};
  for (let i = 0; i < n; i++) {
    criteria[i + 1] = `Panel ${i + 1} (row ${Math.floor(i / WCOLS) + 1}, column ${i % WCOLS + 1}) shows the word ${text}.`;
  }
  return {
    type: "choice", criteria,
    instructions: `The image is a grid of ${n} framed panels, ${WCOLS} per row, numbered left to right, `
      + `top to bottom. Which panel shows the word ${text}?`,
  };
}

// A child word: one letter mutated, or each letter taken from one of two parents.
function mutateWord(word) {
  const i = randint(0, word.length - 1), m = mutate(word[i]);
  return { word: word.map((l, j) => j === i ? m.shapes : l), op: m.op };
}

function childWord(cands, probs) {
  const total = probs.reduce((x, y) => x + y, 0), draw = () => {
    let r = Math.random() * total, i = 0;
    while (i < probs.length - 1 && (r -= probs[i]) > 0) i++;
    return i;
  };
  const a = cands[draw()].word, b = cands[draw()].word;
  if (Math.random() < P_CROSS && a !== b) {
    const word = a.map((l, j) => Math.random() < 0.5 ? l : b[j]);
    if (word.some((l, j) => l !== a[j]) && word.some((l, j) => l !== b[j])) return { word, op: "cross" };
  }
  return mutateWord(a);
}

// ---- player: a word run is { object, frames: [{ word, prob, p }], best, p } ----

let wrun = null, wpos = 1, wtimer = null;
const wlast = () => wrun.frames.length + 1;  // the final-word position

function wrender() {
  const n = wrun.frames.length, ctx = $("wordview").getContext("2d"), cap = $("wordcaption");
  $("wslider").max = wlast();
  $("wslider").value = wpos;
  clear(ctx);
  if (wpos > n) {
    paintWord(ctx, wrun.best, 0, 0, WORD_W, WORD_H);
    cap.className = wrun.p > TARGET ? "yes" : "";
    cap.textContent = n ? `Final ${wrun.object}: best P = ${wrun.p.toFixed(2)} after ${n} generations`
      : `${wrun.object}, set from the canned letters. Evolve it with your key.`;
  } else {
    const f = wrun.frames[wpos - 1], prev = wpos > 1 ? wrun.frames[wpos - 2].word : f.word;
    paintWord(ctx, f.word, 0, 0, WORD_W, WORD_H, f.word.map((l, i) => same(l, prev[i]) ? KEPT : CHANGED));
    cap.className = f.p > TARGET ? "yes" : "";
    cap.textContent = `Generation ${wpos}/${n}: Jev picks this (${f.prob.toFixed(2)}), P = ${f.p.toFixed(2)}`;
  }
  wchart();
}

// P of each generation's pick (green: new best), best-so-far as a step line, target dashed.
function wchart() {
  const svg = $("wchart"), n = Math.max(wrun.frames.length, 10), W = 384, H = 90, pad = 6;
  const x = (i) => pad + (W - 2 * pad) * (i + 0.5) / n, y = (p) => H - pad - (H - 2 * pad) * p;
  let line = "", b = -1, dots = "";
  wrun.frames.forEach((f, i) => {
    const up = f.p > b;
    b = Math.max(b, f.p);
    line += `${i ? "L" : "M"}${x(i - 0.5).toFixed(1)},${y(b).toFixed(1)}H${x(i + 0.5).toFixed(1)}`;
    const on = i === wpos - 1;
    dots += `<circle cx="${x(i).toFixed(1)}" cy="${y(f.p).toFixed(1)}" r="${on ? 5 : 3}"
      fill="var(--${up ? "yes" : "muted"})" ${on ? 'stroke="var(--fg)" stroke-width="1.5"' : ""}/>`;
  });
  svg.innerHTML = `<line x1="0" x2="${W}" y1="${y(TARGET)}" y2="${y(TARGET)}" stroke="var(--muted)" stroke-dasharray="4 3"/>
    <text x="${W - 2}" y="${y(TARGET) - 3}" font-size="10" text-anchor="end" fill="var(--muted)">0.66</text>
    <path d="${line}" fill="none" stroke="var(--fg)" stroke-width="1.5" vector-effect="non-scaling-stroke"/>${dots}`;
}

function wgo(k) { wstopPlay(); wpos = Math.max(1, Math.min(wlast(), k)); wrender(); }
function wstopPlay() { clearInterval(wtimer); wtimer = null; $("wplay").textContent = "▶ Play"; }
function wplay() {
  if (wtimer) return wstopPlay();
  if (wpos >= wlast()) wpos = 0;
  $("wplay").textContent = "⏸ Pause";
  wtimer = setInterval(() => {
    wpos++;
    wrender();
    if (wpos >= wlast()) wstopPlay();
  }, 400);
}
$("wfirst").onclick = () => wgo(1);
$("wprev").onclick = () => wgo(wpos - 1);
$("wnext").onclick = () => wgo(wpos + 1);
$("wlast").onclick = () => wgo(Infinity);
$("wplay").onclick = wplay;
$("wslider").oninput = () => wgo(+$("wslider").value);

const wordText = () => $("word").value.trim().toUpperCase();
const validWord = (text) => new RegExp(`^[A-Z0-9]{1,${MAX_WORD}}$`).test(text);

// A canned run if there is one, else the word set from the canned letters.
function setWord() {
  wstopPlay();
  const text = wordText();
  if (!validWord(text)) {
    clear($("wordview").getContext("2d"));
    $("wordcaption").textContent = `Enter 1 to ${MAX_WORD} letters or digits.`;
    return;
  }
  wrun = cannedWords[text] || { object: text, frames: [], best: [...text].map((c) => letterShapes[c]), p: -1 };
  wpos = wlast();
  wrender();
}
$("word").oninput = setWord;
setWord();

// ---- live run ----

let wordCancelled = false;
$("wstop").onclick = () => { wordCancelled = true; };

async function evolveWord(key, text, st) {
  const sheet = document.createElement("canvas"), start = [...text].map((c) => letterShapes[c]);
  sheet.width = WCOLS * WCELL_W;
  sheet.height = Math.ceil(POP / WCOLS) * WCELL_H;
  let cands = [{ word: start, op: "seed" }], calls = 0, cost = 0, stop = "generations";
  while (cands.length < POP) cands.push(mutateWord(start));
  const tallyWord = (resp) => { calls++; cost += (resp.usage && resp.usage.cost) || 0; };
  wrun = { object: text, frames: [], best: start, p: -1 };
  for (let g = 1; g <= GENERATIONS; g++) {
    if (wordCancelled) { stop = "stopped"; break; }
    for (let i = cands.length - 1; i > 0; i--) {  // new panel positions every generation, against position bias
      const j = randint(0, i);
      [cands[i], cands[j]] = [cands[j], cands[i]];
    }
    drawWordSheet(sheet.getContext("2d"), cands.map((c) => c.word));
    st.textContent = `Generation ${g}: asking Jev to pick one of 10 versions…`;
    let resp = await decide(key, sheet.toDataURL("image/png"), { pick: wordChoice(text, POP) });
    tallyWord(resp);
    const probs = cands.map((_, i) => resp.answers.pick.probabilities[i + 1] || 0);
    const best = probs.indexOf(Math.max(...probs));
    let p = cands[best].p;  // known for words carried over
    if (p === undefined) {
      st.textContent = `Generation ${g}: asking Jev about the pick…`;
      resp = await decide(key, wordPng(cands[best].word), { is: { type: "noul", instructions: `The image shows the word ${text}.` } });
      tallyWord(resp);
      p = resp.answers.is.noul;
    }
    if (p >= wrun.p) { wrun.p = p; wrun.best = cands[best].word; }
    wrun.frames.push({ word: cands[best].word, prob: probs[best], p });
    wpos = wrun.frames.length;
    wrender();
    if (p > TARGET) { stop = "target"; break; }
    // the best word so far and the current pick survive; children fill the rest
    const next = [{ word: wrun.best, op: "best", p: wrun.p }];
    if (cands[best].word !== wrun.best) next.push({ word: cands[best].word, op: "elite", p });
    while (next.length < POP) next.push(childWord(cands, probs));
    cands = next;
  }
  st.textContent = `Done (${{ stopped: "stopped by you", target: "P > 0.66", generations: "30 generations" }[stop]}), `
    + `${calls} Jev calls, cost $${cost.toFixed(5)}.`;
  wpos = wlast();
  wrender();
}

$("evolve").onclick = async () => {
  const key = $("key").value.trim(), text = wordText(), st = $("wstatus");
  if (!key) { st.textContent = "Enter your OpenRouter key below."; return; }
  if (!validWord(text)) { st.textContent = `Enter 1 to ${MAX_WORD} letters or digits.`; return; }
  try { $("remember").checked ? localStorage.setItem("orKey", key) : localStorage.removeItem("orKey"); } catch (e) {}
  wstopPlay();
  wordCancelled = false;
  $("evolve").disabled = true; $("wstop").disabled = false; $("word").disabled = true;
  try {
    await evolveWord(key, text, st);
  } catch (e) {
    st.textContent = e.message;
  } finally {
    $("evolve").disabled = false; $("wstop").disabled = true; $("word").disabled = false;
  }
};
