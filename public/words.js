// Word mode on the alphabet page: sets a word from the canned letters, and evolves the whole word
// with Jev. Uses the blocks, mutation and API helpers of drawer.js.
const MAX_WORD = 7, GAP = 2;  // GAP: space between letters, in grid units
const WORD_W = 768, WORD_H = 256, WCOLS = 2, WCELL_W = 480, WCELL_H = 160;  // word image and grid sheet, in pixels
const letterShapes = Object.fromEntries(DRAWER.runs.map((r) => [r.object.slice(-1), finalShapes(r)]));

// Paint a word (one block list per letter) into the box x, y, w, h: letters side by side,
// cropped to their outlines, all at one scale, centred.
function paintWord(ctx, word, x, y, w, h) {
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
    paint(ctx, blocks, KEPT, left - x0 * s, y + (h - (y1 - y0) * s) / 2 - y0 * s, s * GRID);
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

// The words side by side in framed panels, WCOLS per row, as Jev sees them.
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

const wordText = () => $("word").value.trim().toUpperCase();
const validWord = (text) => new RegExp(`^[A-Z0-9]{1,${MAX_WORD}}$`).test(text);

function showWord(word, caption) {
  const ctx = $("wordview").getContext("2d");
  clear(ctx);
  if (word) paintWord(ctx, word, 0, 0, WORD_W, WORD_H);
  $("wordcaption").textContent = caption;
}

function setWord() {
  const text = wordText();
  if (!validWord(text)) { showWord(null, `Enter 1 to ${MAX_WORD} letters or digits.`); return; }
  showWord([...text].map((c) => letterShapes[c]), "Set from the canned letters.");
  $("wordsheet").hidden = true;
}
$("word").oninput = setWord;
setWord();

let wordCancelled = false;
$("wstop").onclick = () => { wordCancelled = true; };

async function evolveWord(key, text, st) {
  const sheet = $("wordsheet"), sh = sheet.getContext("2d"), start = [...text].map((c) => letterShapes[c]);
  let cands = [{ word: start, op: "seed" }], top = null, topP = -1, calls = 0, cost = 0, g = 0, stop = "generations";
  while (cands.length < POP) cands.push(mutateWord(start));
  const tallyWord = (resp) => { calls++; cost += (resp.usage && resp.usage.cost) || 0; };
  sheet.hidden = false;
  for (g = 1; g <= GENERATIONS; g++) {
    if (wordCancelled) { stop = "stopped"; break; }
    for (let i = cands.length - 1; i > 0; i--) {  // new panel positions every generation, against position bias
      const j = randint(0, i);
      [cands[i], cands[j]] = [cands[j], cands[i]];
    }
    drawWordSheet(sh, cands.map((c) => c.word));
    st.textContent = `Generation ${g}: asking Jev to pick a panel…`;
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
    if (p >= topP) { topP = p; top = cands[best].word; }
    cands.forEach((c, i) => {  // annotations for the viewer only, not sent to Jev
      const x = i % WCOLS * WCELL_W, y = Math.floor(i / WCOLS) * WCELL_H;
      if (i === best) { sh.strokeStyle = "#1a7f37"; sh.lineWidth = 3 * BORDER; sh.strokeRect(x + 6, y + 6, WCELL_W - 12, WCELL_H - 12); }
      sh.font = "bold 22px system-ui, sans-serif";
      sh.fillStyle = "rgba(255,255,255,0.85)";
      sh.fillRect(x + 12, y + 12, 58, 28);
      sh.fillStyle = i === best ? "#1a7f37" : "#333";
      sh.fillText(probs[i].toFixed(2), x + 16, y + 34);
    });
    showWord(top, `Generation ${g}: Jev picks panel ${best + 1} (${probs[best].toFixed(2)}), P = ${p.toFixed(2)}; best P = ${topP.toFixed(2)}`);
    $("wordcaption").className = topP > TARGET ? "yes" : "";
    if (p > TARGET) { stop = "target"; break; }
    // the best word so far and the current pick survive; children fill the rest
    const next = [{ word: top, op: "best", p: topP }];
    if (cands[best].word !== top) next.push({ word: cands[best].word, op: "elite", p });
    while (next.length < POP) next.push(childWord(cands, probs));
    cands = next;
  }
  st.textContent = `Done (${{ stopped: "stopped by you", target: "P > 0.66", generations: "30 generations" }[stop]}), `
    + `${calls} Jev calls, cost $${cost.toFixed(5)}. Best P = ${topP.toFixed(2)}.`;
}

$("evolve").onclick = async () => {
  const key = $("key").value.trim(), text = wordText(), st = $("wstatus");
  if (!key) { st.textContent = "Enter your OpenRouter key below."; return; }
  if (!validWord(text)) { st.textContent = `Enter 1 to ${MAX_WORD} letters or digits.`; return; }
  try { $("remember").checked ? localStorage.setItem("orKey", key) : localStorage.removeItem("orKey"); } catch (e) {}
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
