// kAIm56 — Core War app: the page. The game loop of the standalone page, plus
// AI players: every N cycles the round pauses, each warrior with a model gets a
// task (ephemeral VM, existing /api/tasks) with the rules, the state and the
// core matrix, and its answer is written into the core (marsPatch).
// SPDX-License-Identifier: AGPL-3.0-or-later
import { marsNew, marsStep, marsState, marsDump, marsPatch, marsCheck, marsSlot, marsCycle } from './mars.js';
import { CORE, COLS, ROWS, DEFAULTS, rules, matrix, buildTask, parseAnswer, lineDiff, taskId } from './coach.js';

/* ---- presets ---- */
const PRESETS = {
  "Imp": `; Imp — kopiert sich unaufhaltsam vorwärts
MOV 0, 1`,
  "Dwarf": `; Dwarf — bombardiert jede 4. Zelle mit DAT
loop  ADD #4, bomb
      MOV bomb, @bomb
      JMP loop
bomb  DAT #0, #0`,
  "Gemini": `; Gemini — kopiert sich 1000 Zellen weiter und zieht um
src  DAT #0, #0
dst  DAT #0, #999
loop MOV @src, @dst
     ADD #1, src
     ADD #1, dst
     CMP #9, src
     JMP loop
     SUB #7, dst
     JMP @dst
     ORG loop`,
  "Eigener Code": ``
};

const $ = id => document.getElementById(id);
const cvs = $("core"), ctx = cvs.getContext("2d");
const off = document.createElement("canvas");
off.width = COLS; off.height = ROWS;
const octx = off.getContext("2d");
const img = octx.createImageData(COLS, ROWS);

const owners = new Uint8Array(CORE);
const agesU8 = new Uint8Array(CORE);

const COLOR = {
  bg:   [13, 17, 24],
  w1:   { fresh: [255, 180, 84],  old: [96, 66, 30]  },
  w2:   { fresh: [88, 193, 255],  old: [32, 70, 100] },
  pc1:  "#ffe9c2",
  pc2:  "#e2f4ff",
};

let running = false, finished = false, solo = false;
// the engine always runs its first warrior first, we swap the load order every second round
let swapped = false;
let match = { rounds: 1, round: 0, a: 0, tie: 0, b: 0 }; // a = W1-Siege (solo: überlebt)
let nextTimer = null;

/* ---- AI state ---- */
let adapting = false;            // a checkpoint is being worked on: the loop does not step
let gen = 0;                     // bumps on every new round: answers of an older round are dropped
let nextCp = DEFAULTS.every;     // next checkpoint, in cycles per warrior
const used = { 1: 0, 2: 0 };     // adaptations this round, per editor warrior
const roundHist = { 1: [], 2: [] };
let changes = [];                // the Changes tab, newest first

const store = {
  get(k, d) { try { const v = localStorage.getItem("corewar." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("corewar." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

function cfg() {
  const n = (id, lo, hi, d) => Math.min(hi, Math.max(lo, Math.round(+$(id).value) || d));
  return { every: n("cfgEvery", 50, 80000, DEFAULTS.every), perRound: n("cfgPerRound", 0, 50, DEFAULTS.perRound),
           maxSteps: n("cfgSteps", 1, 50, DEFAULTS.maxSteps) };
}

function lerp(a, b, t) { return (a + (b - a) * t) | 0; }

function state() {
  const st = marsState(owners, agesU8);
  if (!swapped) return st;
  for (let i = 0; i < CORE; i++) {
    if (owners[i] !== 0) owners[i] = 3 - owners[i];
  }
  return { ...st, p1: st.p2, p2: st.p1, pcs1: st.pcs2, pcs2: st.pcs1 };
}

// cycles per warrior, the engine counts every executed instruction
function warriorCycles(st) {
  return solo ? st.cycle : Math.ceil(st.cycle / 2);
}

function render() {
  const st = state();
  const d = img.data;
  for (let i = 0; i < CORE; i++) {
    const o = owners[i];
    let r, g, b;
    if (o === 0) {
      [r, g, b] = COLOR.bg;
    } else {
      const c = o === 1 ? COLOR.w1 : COLOR.w2;
      const t = Math.min(agesU8[i], 160) / 160;
      r = lerp(c.fresh[0], c.old[0], t);
      g = lerp(c.fresh[1], c.old[1], t);
      b = lerp(c.fresh[2], c.old[2], t);
    }
    const p = i * 4;
    d[p] = r; d[p + 1] = g; d[p + 2] = b; d[p + 3] = 255;
  }
  octx.putImageData(img, 0, 0);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(off, 0, 0, cvs.width, cvs.height);

  // task pointers as bright sparks
  const cw = cvs.width / COLS, ch = cvs.height / ROWS;
  const drawPCs = (pcs, color) => {
    ctx.fillStyle = color;
    for (const pc of pcs) {
      ctx.fillRect((pc % COLS) * cw, ((pc / COLS) | 0) * ch, cw, ch);
    }
  };
  drawPCs(st.pcs1, COLOR.pc1);
  drawPCs(st.pcs2, COLOR.pc2);

  $("stCycle").textContent = warriorCycles(st).toLocaleString("de-DE");
  $("stP1").textContent = st.p1;
  $("stP2").textContent = solo ? "–" : st.p2;
  return st;
}

function setBanner(msg, isErr = false, cls = "") {
  const b = $("banner");
  b.textContent = msg;
  b.className = "banner" + (isErr ? " err" : "") + (cls ? " " + cls : "");
}

function updateScore() {
  $("stRound").textContent = match.round + "/" + match.rounds;
  $("stScore").innerHTML = solo
    ? `überlebt <b class="c1">${match.a}</b> · gestorben <b class="c2">${match.b}</b>`
    : `W1 <b class="c1">${match.a}</b> · U <b>${match.tie}</b> · W2 <b class="c2">${match.b}</b>`;
  updateLeft();
}

function scoreText() {
  return solo ? `survived ${match.a}, died ${match.b}` : `W1 ${match.a} : ties ${match.tie} : W2 ${match.b}`;
}

function roundResultText(status) {
  if (solo) return status === 4
    ? "Warrior 1 ist gestorben (DAT ausgeführt)"
    : "Warrior 1 hat das Zykluslimit überlebt";
  return status === 3 ? "Unentschieden" : `Warrior ${status} gewinnt`;
}

function matchResultText() {
  if (solo) return `Match beendet — ${match.a} von ${match.rounds} Runden überlebt.`;
  const s = `${match.a}:${match.tie}:${match.b} (W1-Siege : Unentschieden : W2-Siege)`;
  if (match.a > match.b) return `Warrior 1 gewinnt das Match ${s}.`;
  if (match.b > match.a) return `Warrior 2 gewinnt das Match ${s}.`;
  return `Match unentschieden — ${s}.`;
}

function finishRound(status) {
  running = false;
  if (solo) { if (status === 3) match.a++; else match.b++; }
  else if (status === 1) match.a++;
  else if (status === 2) match.b++;
  else match.tie++;
  updateScore();

  if (match.round < match.rounds) {
    setBanner(`Runde ${match.round}/${match.rounds}: ${roundResultText(status)} — nächste Runde …`);
    nextTimer = setTimeout(() => {
      nextTimer = null;
      match.round++;
      updateScore();
      if (newRound()) {
        running = true;
        $("btnRun").textContent = "Pause";
      }
    }, 900);
  } else {
    finished = true;
    $("btnRun").textContent = "Start";
    setBanner(matchResultText());
  }
}

function speedCycles() {
  // logarithmic feel: 1..60 → ~2..4000 cycles per frame
  return Math.max(2, Math.round(Math.pow(1.135, +$("speed").value) * 2));
}

/* ---- AI players ---- */
function aiPlayers() {
  const out = [];
  for (const k of solo ? [1] : [1, 2]) if ($("model" + k).value) out.push(k);
  return out;
}

function updateLeft() {
  const c = cfg();
  for (const k of [1, 2]) {
    const on = $("model" + k).value && !(solo && k === 2);
    $("left" + k).textContent = on ? `${Math.max(0, c.perRound - used[k])}/${c.perRound} übrig` : "";
  }
}

// the engine slot (0/1) of editor warrior k
const slotOf = k => (swapped ? (k === 1 ? 1 : 0) : k - 1);

function loop() {
  if (running && !adapting) {
    // never step past the next checkpoint: the AI sees the state exactly there
    let n = speedCycles();
    const players = aiPlayers(), c = cfg();
    const wantAi = players.some(k => used[k] < c.perRound);
    if (wantAi) {
      const target = solo ? nextCp : 2 * nextCp;
      n = Math.max(0, Math.min(n, target - marsCycle()));
    }
    let status = n > 0 ? marsStep(n) : 0;
    if (swapped && (status === 1 || status === 2)) status = 3 - status;
    const st = render();
    if (status > 0) finishRound(status);
    else if (wantAi && warriorCycles(st) >= nextCp) checkpoint();
  }
  requestAnimationFrame(loop);
}

const api = {
  post: (url, body) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })
    .then(r => r.json()),
  tasks: () => fetch("/api/tasks", { cache: "no-store" }).then(r => r.json()),
};

function viewFor(k, st, snap) {
  const me = k === 1 ? snap.w1 : snap.w2, them = k === 1 ? snap.w2 : snap.w1;
  return {
    player: k, round: match.round, rounds: match.rounds, cycle: warriorCycles(st), used: used[k],
    slot: marsSlot(slotOf(k)), score: scoreText(),
    me: { processes: me.processes, cells: me.cells, codeAtPc: mine(me.codeAtPc, k) },
    them: them ? { processes: them.processes, cells: them.cells, codeAtPc: mine(them.codeAtPc, k) } : null,
    source: $("src" + k).value,
    matrix: matrix(owners, k === 1 ? st.pcs1 : st.pcs2, k === 1 ? st.pcs2 : st.pcs1, k),
    history: roundHist[k],
  };
}
// codeAtPc carries editor owners (1/2); that is what the model needs to read "who wrote it"
const mine = (list, k) => (list || []).map(c => ({ ...c, text: c.text + (c.owner ? (c.owner === k ? "   ; yours" : "   ; opponent") : "") }));

async function checkpoint() {
  const c = cfg(), players = aiPlayers().filter(k => used[k] < c.perRound);
  const myGen = gen, cp = nextCp;
  nextCp += c.every;
  if (!players.length) return;
  adapting = true;
  const st = state(), snap = snapshot();
  const jobs = [];
  for (const k of players) {
    const model = $("model" + k).value, before = $("src" + k).value;
    const message = buildTask(viewFor(k, st, snap), c);
    used[k]++;
    const entry = { k, model, round: match.round, cycle: cp, used: used[k], perRound: c.perRound, before, t0: Date.now() };
    try {
      const r = await api.post("/api/tasks", { instance: "ephemeral", message, model, max_steps: c.maxSteps });
      entry.id = taskId(r.msg);
      if (!entry.id) entry.error = r.msg || "task not created";
    } catch (e) { entry.error = "manager not reachable"; }
    jobs.push(entry);
  }
  updateLeft();
  await waitFor(jobs, myGen);
  if (myGen !== gen) return;                 // a new round/match started meanwhile
  for (const j of jobs) apply(j);
  adapting = false;
  render();
  setBanner(`Zyklus ${cp.toLocaleString("de-DE")}: ${jobs.map(j => `W${j.k} ${j.ok ? "angepasst" : "unverändert"}`).join(" · ")} — weiter.`);
}

async function waitFor(jobs, myGen) {
  const open = () => jobs.filter(j => j.id && j.result === undefined && !j.error);
  const t0 = Date.now();
  while (open().length && myGen === gen) {
    const secs = Math.round((Date.now() - t0) / 1000);
    setBanner(`Zyklus ${jobs[0].cycle.toLocaleString("de-DE")} — KI denkt nach: ${open().map(j => `W${j.k} (${j.model})`).join(", ")} · ${secs} s`, false, "ai-wait");
    await new Promise(r => setTimeout(r, 3000));
    let list = [];
    try { list = await api.tasks(); } catch { continue; }
    for (const j of open()) {
      const t = list.find(x => x.id === j.id);
      if (!t) { j.error = "task disappeared"; continue; }
      if (["pending", "running", "scheduled"].includes(t.status)) continue;
      j.result = String(t.result || "");
      j.secs = Math.round((Date.now() - j.t0) / 1000);
      if (t.status !== "done") j.error = `task ${t.status}: ${j.result.slice(0, 200)}`;
      api.post(`/api/tasks/${j.id}/delete`, {}).catch(() => {});   // keep the Tasks tab clean
    }
    if (Date.now() - t0 > 20 * 60 * 1000) for (const j of open()) j.error = "no answer within 20 minutes";
  }
  if (myGen !== gen)                             // abandoned (new round/match): drop the queued tasks
    for (const j of open()) api.post(`/api/tasks/${j.id}/delete`, {}).catch(() => {});
}

function apply(j) {
  j.secs = j.secs || Math.round((Date.now() - j.t0) / 1000);
  if (!j.error) {
    const a = parseAnswer(j.result);
    if (a.error) j.error = a.error;
    else {
      j.why = a.why; j.after = a.code;
      const bad = marsCheck(a.code) || marsPatch(slotOf(j.k), a.code);
      if (bad) j.error = "Code abgelehnt: " + bad;
    }
  }
  j.ok = !j.error;
  if (j.ok) {
    $("src" + j.k).value = j.after;
    $("preset" + j.k).value = "Eigener Code";
  }
  roundHist[j.k].push({ cycle: j.cycle, why: j.why || j.error, ok: j.ok });
  changes.unshift({ k: j.k, model: j.model, round: j.round, cycle: j.cycle, used: j.used, perRound: j.perRound,
                    secs: j.secs, ok: j.ok, why: j.why || "", error: j.error || "",
                    answer: String(j.result || "").slice(0, 6000),
                    diff: j.after ? lineDiff(j.before, j.after) : [], ts: Date.now() });
  changes = changes.slice(0, 300);
  store.set("changes", changes);
  renderChanges();
}

/* ---- Changes tab ---- */
const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
function renderChanges() {
  $("chCount").textContent = changes.length ? String(changes.length) : "";
  if (!changes.length) {
    $("changes").innerHTML = '<p class="empty">Noch keine Anpassungen. Wähle für einen Warrior ein Modell und starte ein Match.</p>';
    return;
  }
  $("changes").innerHTML = changes.map(c => {
    const add = c.diff.filter(d => d[0] === "+").length, del = c.diff.filter(d => d[0] === "-").length;
    const diff = c.diff.map(([t, l]) => `<span class="${t === "+" ? "add" : t === "-" ? "del" : "same"}">${t} ${esc(l)}</span>`).join("");
    return `<article class="change${c.k === 2 ? " p2" : ""}${c.ok ? "" : " bad"}">
      <div class="meta"><b>W${c.k}</b><span>Runde ${c.round}</span><span>Zyklus ${c.cycle.toLocaleString("de-DE")}</span>
        <span>Anpassung ${c.used}/${c.perRound}</span><span>${esc(c.model)}</span><span>${c.secs} s</span>
        ${c.diff.length ? `<span>+${add} −${del}</span>` : ""}</div>
      ${c.why ? `<p class="why">${esc(c.why)}</p>` : ""}
      ${c.error ? `<p class="err">${esc(c.error)}</p>` : ""}
      ${c.diff.length ? `<details${c.ok ? " open" : ""}><summary>Änderung am Programm</summary><pre class="diff">${diff}</pre></details>` : ""}
      ${c.answer ? `<details><summary>Antwort des Modells</summary><pre class="diff">${esc(c.answer)}</pre></details>` : ""}
    </article>`;
  }).join("");
}

function newRound() {
  const seed = (Date.now() ^ (Math.random() * 0xffffffff)) >>> 1;
  solo = $("src2").value.trim() === "";
  swapped = !solo && match.round % 2 === 0;
  gen++; adapting = false;
  nextCp = cfg().every; used[1] = used[2] = 0; roundHist[1] = []; roundHist[2] = [];
  const s1 = $("src1").value, s2 = $("src2").value;
  let err = swapped ? marsNew(s2, s1, CORE, seed) : marsNew(s1, s2, CORE, seed);
  // error text names the engine slot, map it back to the editor
  if (err && swapped) err = err.replace(/Warrior ([12])/, (m, n) => "Warrior " + (3 - n));
  if (err) {
    running = false; finished = true;
    setBanner("Fehler — " + err, true);
    return false;
  }
  render();
  updateLeft();
  return true;
}

function newMatch() {
  if (nextTimer) { clearTimeout(nextTimer); nextTimer = null; }
  running = false; finished = false;
  $("btnRun").textContent = "Start";
  match.rounds = Math.min(99, Math.max(1, Math.round(+$("rounds").value) || 1));
  $("rounds").value = match.rounds;
  match.round = 1; match.a = match.tie = match.b = 0;
  if (!newRound()) return false;
  updateScore();
  setBanner((solo
    ? `Solo-Modus — ${match.rounds} Runde(n), Warrior 1 läuft allein.`
    : `Bereit — Match über ${match.rounds} Runde(n), Startpositionen zufällig.`) + " " + aiNote());
  return true;
}

// says whether the checkpoints will pause at all — "no AI" is the default and easy to miss
function aiNote() {
  const on = aiPlayers(), c = cfg();
  if (!on.length) return "Keine KI gewählt: ohne Modell unter „KI“ läuft das Spiel ohne Pause durch.";
  return `KI: ${on.map(k => `W${k} = ${$("model" + k).value}`).join(", ")} — Pause alle ${c.every} Zyklen.`;
}

$("btnRun").addEventListener("click", () => {
  if (nextTimer || adapting) return; // Rundenwechsel oder KI-Anpassung läuft gerade
  if (finished && !newMatch()) return;
  running = !running;
  $("btnRun").textContent = running ? "Pause" : "Start";
});

$("btnNew").addEventListener("click", () => { newMatch(); });

/* ---- JSON snapshot for an LLM ---- */
function snapshot() {
  const st = state();
  const blocks = [];
  let cells1 = 0, cells2 = 0;
  for (let b = 0; b < CORE; b += 100) {
    let w1 = 0, w2 = 0;
    for (let i = b; i < b + 100; i++) {
      if (owners[i] === 1) w1++;
      else if (owners[i] === 2) w2++;
    }
    cells1 += w1; cells2 += w2;
    if (w1 || w2) blocks.push([b, w1, w2]);
  }
  const pcs = a => [...a].sort((x, y) => x - y).slice(0, 32);
  // 4 cells before and 8 after the next PC to run, owner mapped like the map
  const code = q => q.length === 0 ? [] : marsDump(q[0] - 4, 13).map(c => ({
    addr: c.addr,
    owner: swapped && c.owner ? 3 - c.owner : c.owner,
    pc: c.addr === q[0],
    text: c.text,
  }));
  return {
    format: "corewar-snapshot/1",
    rules: { standard: "ICWS'88", coreSize: CORE, maxCyclesPerWarrior: 80000, minDistance: 100 },
    mode: solo ? "solo" : "duel",
    round: match.round, rounds: match.rounds,
    firstMover: solo ? 1 : (swapped ? 2 : 1),
    score: solo
      ? { survived: match.a, died: match.b }
      : { w1: match.a, tie: match.tie, w2: match.b },
    cycle: warriorCycles(st),
    finished,
    w1: { processes: st.p1, pcs: pcs(st.pcs1), cells: cells1, source: $("src1").value, codeAtPc: code(st.pcs1) },
    w2: solo ? null : { processes: st.p2, pcs: pcs(st.pcs2), cells: cells2, source: $("src2").value, codeAtPc: code(st.pcs2) },
    blocksLegend: "[startAddress, cellsWrittenByW1, cellsWrittenByW2] per 100 cells, empty blocks left out",
    blocks,
  };
}
window.marsSnapshot = snapshot;

$("btnSnap").addEventListener("click", async () => {
  const json = JSON.stringify(snapshot(), null, 2);
  try {
    await navigator.clipboard.writeText(json);
    setBanner(`Snapshot kopiert (Zyklus ${warriorCycles(state()).toLocaleString("de-DE")}).`);
  } catch {
    // no clipboard access, like on some file:// setups, we download instead
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([json], { type: "application/json" }));
    a.download = `corewar-snapshot-r${match.round}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    setBanner("Zwischenablage nicht verfügbar, Snapshot als Datei gespeichert.");
  }
});
$("rounds").addEventListener("change", () => { newMatch(); });

$("speed").addEventListener("input", () => {
  $("speedVal").textContent = speedCycles() + "/Frame";
});
$("speedVal").textContent = speedCycles() + "/Frame";

/* preset selectors */
const SOLO_OPT = "Kein Gegner (Solo)";
for (const [selId, srcId, initial] of [["preset1", "src1", "Imp"], ["preset2", "src2", "Dwarf"]]) {
  const sel = $(selId);
  const names = Object.keys(PRESETS);
  if (selId === "preset2") names.push(SOLO_OPT);
  for (const name of names) {
    const o = document.createElement("option");
    o.value = o.textContent = name;
    sel.appendChild(o);
  }
  sel.value = initial;
  $(srcId).value = PRESETS[initial];
  sel.addEventListener("change", () => {
    if (sel.value === SOLO_OPT) $(srcId).value = "";
    else if (PRESETS[sel.value] !== "") $(srcId).value = PRESETS[sel.value];
    newMatch();
  });
  $(srcId).addEventListener("input", () => {
    sel.value = (selId === "preset2" && $(srcId).value.trim() === "") ? SOLO_OPT : "Eigener Code";
  });
}

/* model selectors: the manager's OpenRouter list, "" = a human plays (no AI) */
async function loadModels() {
  let models = [];
  try {
    const d = await (await fetch("/api/openrouter-models?relevant=1", { cache: "no-store" })).json();
    models = (Array.isArray(d) ? d : d.models || []).filter(m => m.id);
  } catch { /* offline: only "no AI" */ }
  for (const k of [1, 2]) {
    const sel = $("model" + k), saved = store.get("model" + k, "");
    sel.innerHTML = '<option value="">— keine KI (Mensch) —</option>' +
      models.map(m => `<option value="${esc(m.id)}">${esc(m.label || m.id)}</option>`).join("");
    if (saved && !models.some(m => m.id === saved)) sel.insertAdjacentHTML("beforeend", `<option value="${esc(saved)}">${esc(saved)}</option>`);
    sel.value = saved;
    sel.addEventListener("change", () => {
      store.set("model" + k, sel.value); updateLeft();
      if (!running && !adapting) setBanner(aiNote());
    });
  }
  updateLeft();
  if (!running && !adapting) setBanner($("banner").textContent.replace(/ (Keine KI gewählt|KI: ).*$/, "") + " " + aiNote());
}
for (const id of ["cfgEvery", "cfgPerRound", "cfgSteps"]) {
  const saved = store.get(id, null);
  if (saved !== null) $(id).value = saved;
  $(id).addEventListener("change", () => { store.set(id, +$(id).value); $("rulesText").textContent = rules(cfg()); updateLeft(); });
}

/* tabs */
for (const b of document.querySelectorAll("nav.tabs button")) {
  b.addEventListener("click", () => {
    for (const x of document.querySelectorAll("nav.tabs button")) x.setAttribute("aria-selected", String(x === b));
    for (const t of ["arena", "changes", "rules"]) $("tab-" + t).hidden = t !== b.dataset.tab;
  });
}

/* ---- boot ---- */
changes = store.get("changes", []);
renderChanges();
$("rulesText").textContent = rules(cfg());
$("btnRun").disabled = false;
$("btnNew").disabled = false;
$("btnSnap").disabled = false;
loadModels();
newMatch();
requestAnimationFrame(loop);
