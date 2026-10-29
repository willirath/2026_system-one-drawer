// Word tab: like a font, every distinct character of a word is one letter run, shared by its
// repeats (the canned runs from the alphabet, or fresh ones evolved in parallel with your key),
// and the tab animates them side by side, one generation at a time. Uses the runs, painting and
// API helpers of drawer.js.
const MAX_WORD = 7, GAP = 2;  // GAP: space between letters, in grid units
const WORD_W = 768, WORD_H = 384;
const CHANGED = "rgba(26,127,55,0.85)";  // letters that changed since the previous generation
const cannedRuns = Object.fromEntries(DRAWER.runs.map((r) => [r.object.slice(-1), r]));
const objectOf = (c) => /[0-9]/.test(c) ? `digit ${c}` : `capital letter ${c}`;

// Paint a word (one block list per letter, null for a letter not drawn yet) into the box x, y, w, h:
// letters side by side, cropped to their outlines, all at one scale, centred. fills: one colour per letter.
function paintWord(ctx, word, x, y, w, h, fills = []) {
  const boxes = word.map((blocks) => {
    if (!blocks) return [0, 8, 0, 0];
    const xs = blocks.flatMap((b) => b.pts.map((p) => p[0])), ys = blocks.flatMap((b) => b.pts.map((p) => p[1]));
    return [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  });
  const width = boxes.reduce((sum, b) => sum + b[1] - b[0], 0) + GAP * (word.length - 1);
  const height = Math.max(...boxes.map((b) => b[3] - b[2]), 8);
  const s = Math.min(w / (width + 2 * GAP), h / (height + 2 * GAP));
  let left = x + (w - width * s) / 2;
  word.forEach((blocks, i) => {
    const [x0, x1, y0, y1] = boxes[i];
    if (blocks) paint(ctx, blocks, fills[i] || KEPT, left - x0 * s, y + (h - (y1 - y0) * s) / 2 - y0 * s, s * GRID);
    left += (x1 - x0 + GAP) * s;
  });
}

// ---- player: a word run is { object, runs: the letter run of each position; repeats share one } ----

let wrun = null, wpos = 1, wtimer = null;
const wcount = () => Math.max(0, ...wrun.runs.map((r) => r.generations.length));  // generations so far
const wlast = () => wcount() + 1;  // the final-word position
// Generation g of a letter run (its last one if it stopped earlier), or null before its first.
const genAt = (r, g) => r.generations.length ? r.generations[Math.min(g, r.generations.length) - 1] : null;
const pickAt = (r, g) => { const gen = genAt(r, g); return gen && gen.candidates[gen.best].shapes; };
const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;

function wrender() {
  const n = wcount(), chars = [...wrun.object], ctx = $("wordview").getContext("2d"), cap = $("wordcaption");
  $("wslider").max = wlast();
  $("wslider").value = wpos;
  clear(ctx);
  let ps;
  if (wpos > n) {
    ps = wrun.runs.map((r) => r.p);
    paintWord(ctx, wrun.runs.map((r) => r.generations.length ? finalShapes(r) : null), 0, 0, WORD_W, WORD_H);
    cap.textContent = `Final ${wrun.object}: the best drawing of each character`;
  } else {
    ps = wrun.runs.map((r) => (genAt(r, wpos) || { p: 0 }).p);
    const word = wrun.runs.map((r) => pickAt(r, wpos));
    const prev = wpos > 1 ? wrun.runs.map((r) => pickAt(r, wpos - 1)) : word;
    paintWord(ctx, word, 0, 0, WORD_W, WORD_H, word.map((l, i) => !l || !prev[i] || same(l, prev[i]) ? KEPT : CHANGED));
    cap.textContent = `Generation ${wpos}/${n}: Jev's pick for each character`;
  }
  cap.className = n && ps.every((p) => p > TARGET) ? "yes" : "";
  $("wordps").innerHTML = chars.map((c, i) => `<span class="${ps[i] > TARGET ? "yes" : ""}">${c} ${ps[i] >= 0 ? ps[i].toFixed(2) : "–"}</span>`)
    .join(" · ") + (n ? ` · mean P = ${mean(ps.map((p) => Math.max(p, 0))).toFixed(2)}` : "");
  wchart();
}

// Mean P of the picks per generation (green: new best), mean of the best so far as a step line.
function wchart() {
  const svg = $("wchart"), count = wcount(), n = Math.max(count, 10), W = 384, H = 90, pad = 6;
  const x = (i) => pad + (W - 2 * pad) * (i + 0.5) / n, y = (p) => H - pad - (H - 2 * pad) * p;
  let line = "", b = -1, dots = "";
  for (let i = 0; i < count; i++) {
    const p = mean(wrun.runs.map((r) => (genAt(r, i + 1) || { p: 0 }).p));
    const top = mean(wrun.runs.map((r) => Math.max(0, ...r.generations.slice(0, i + 1).map((g) => g.p))));
    const up = top > b;
    b = top;
    line += `${i ? "L" : "M"}${x(i - 0.5).toFixed(1)},${y(top).toFixed(1)}H${x(i + 0.5).toFixed(1)}`;
    const on = i === wpos - 1;
    dots += `<circle cx="${x(i).toFixed(1)}" cy="${y(p).toFixed(1)}" r="${on ? 5 : 3}"
      fill="var(--${up ? "yes" : "muted"})" ${on ? 'stroke="var(--fg)" stroke-width="1.5"' : ""}/>`;
  }
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

const wordText = () => $("wordtext").value.trim().toUpperCase();
const validWord = (text) => new RegExp(`^[A-Z0-9]{1,${MAX_WORD}}$`).test(text);

// The word from the canned letter runs, played from the start.
function setWord() {
  wstopPlay();
  const text = wordText();
  if (!validWord(text)) {
    clear($("wordview").getContext("2d"));
    $("wordcaption").textContent = `Enter 1 to ${MAX_WORD} letters or digits.`;
    $("wordps").textContent = "";
    return;
  }
  wrun = { object: text, runs: [...text].map((c) => cannedRuns[c]) };
  wpos = 0;
  if (!$("tab-word").hidden) wplay();
  else { wpos = wlast(); wrender(); }
}
$("wordtext").oninput = setWord;
setWord();

// ---- live run: one letter run per distinct character, all at once ----

let wordCancelled = false;
$("wstop").onclick = () => { wordCancelled = true; };

$("evolve").onclick = async () => {
  const key = $("key").value.trim(), text = wordText(), st = $("wstatus");
  if (!key) { st.textContent = "Enter your OpenRouter key below."; return; }
  if (!validWord(text)) { st.textContent = `Enter 1 to ${MAX_WORD} letters or digits.`; return; }
  try { $("remember").checked ? localStorage.setItem("orKey", key) : localStorage.removeItem("orKey"); } catch (e) {}
  wstopPlay();
  wordCancelled = false;
  $("evolve").disabled = true; $("wstop").disabled = false; $("wordtext").disabled = true;
  const chars = [...text], glyphs = [...new Set(chars)];
  const empty = Object.fromEntries(glyphs.map((c) => [c, { generations: [], p: -1, calls: 0, cost: 0 }]));
  wrun = { object: text, runs: chars.map((c) => empty[c]) };
  wpos = 1;
  wrender();
  const what = `${glyphs.length} character${glyphs.length > 1 ? "s" : ""}`;
  st.textContent = `Evolving ${what} in parallel…`;
  try {
    const runs = await Promise.all(glyphs.map((c) => evolve(key, objectOf(c), (r) => {
      chars.forEach((d, i) => { if (d === c) wrun.runs[i] = r; });  // repeats share the glyph
      wpos = wcount();
      wrender();
      st.textContent = `Evolving ${what} in parallel: generation ${wcount()}…`;
    }, () => wordCancelled)));
    const stops = [...new Set(runs.map((r) => STOPS[r.stop]))].join(", ");
    st.textContent = `Done (${stops}), ${runs.reduce((a, r) => a + r.calls, 0)} Jev calls, `
      + `cost $${runs.reduce((a, r) => a + r.cost, 0).toFixed(5)}.`;
    wpos = wlast();
    wrender();
  } catch (e) {
    st.textContent = e.message;
  } finally {
    $("evolve").disabled = false; $("wstop").disabled = true; $("wordtext").disabled = false;
  }
};
