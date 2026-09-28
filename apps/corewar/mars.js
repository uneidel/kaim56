// kAIm56 — Core War app: the MARS engine (ICWS'88), unchanged from the
// standalone page except: it is a module, it remembers each warrior's loading
// address, and marsPatch() writes new code there while its processes keep running.
// SPDX-License-Identifier: AGPL-3.0-or-later
const OPS = ["DAT", "MOV", "ADD", "SUB", "JMP", "JMZ", "JMN", "DJN", "CMP", "SPL", "SLT"];
const [DAT, MOV, ADD, SUB, JMP, JMZ, JMN, DJN, CMP, SPL, SLT] = OPS.map((_, i) => i);
const OPCODE = Object.fromEntries(OPS.map((o, i) => [o, i]));
OPCODE.SEQ = CMP;
const MODES = "#$@<";
const IMM = 0, DIR = 1, IND = 2, DEC = 3;
// these need a B operand in '88, JMP SPL and DAT can live with one
const NEEDS_B = new Set([MOV, ADD, SUB, JMZ, JMN, DJN, CMP, SLT]);

const MAX_LEN = 100;
const MAX_PROCS = 8000;
const MAX_CYCLES = 80000;
const MIN_DIST = 100;
// one age step per this many executed instructions, only used for the fade on the map
const AGE_DIV = 64;

const LABEL_RE = /^[A-Za-z_]\w*$/;
const isOp = w => Object.hasOwn(OPCODE, w.toUpperCase());
const isPseudo = w => /^(ORG|END)$/i.test(w);

function parse(src, size) {
  const labels = new Map();
  const raw = [];
  let org = null;

  const lines = src.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const ln = i + 1;
    let text = lines[i].replace(/;.*/, "").trim();
    if (!text) continue;

    let [, word, rest] = text.match(/^(\S+)\s*(.*)$/);
    if (!isOp(word) && !isPseudo(word)) {
      const name = word.replace(/:$/, "");
      const next = rest.match(/^(\S+)\s*(.*)$/);
      // a label needs an opcode behind it or a line of its own
      if (!LABEL_RE.test(name) || (next && !isOp(next[1]) && !isPseudo(next[1]))) {
        throw `line ${ln}: unknown opcode "${word}"`;
      }
      if (labels.has(name)) throw `line ${ln}: label "${name}" defined twice`;
      labels.set(name, raw.length);
      if (!next) continue;
      [, word, rest] = next;
    }

    const upper = word.toUpperCase();
    if (upper === "END") {
      if (rest) org = { expr: rest, ln };
      break;
    }
    if (upper === "ORG") {
      if (!rest) throw `line ${ln}: ORG needs a start label`;
      org = { expr: rest, ln };
      continue;
    }

    const op = OPCODE[upper];
    const args = rest === "" ? [] : rest.split(",").map(s => s.trim());
    if (args.length === 0) throw `line ${ln}: ${upper} needs an operand`;
    if (args.length > 2) throw `line ${ln}: too many operands`;
    if (args.includes("")) throw `line ${ln}: empty operand`;
    if (args.length === 1 && NEEDS_B.has(op)) throw `line ${ln}: ${upper} needs two operands`;

    const operand = s => MODES.includes(s[0])
      ? { mode: MODES.indexOf(s[0]), expr: s.slice(1).trim() }
      : { mode: DIR, expr: s };
    let a, b;
    if (args.length === 2) { a = operand(args[0]); b = operand(args[1]); }
    else if (op === DAT) { a = { mode: IMM, expr: "0" }; b = operand(args[0]); }
    else { a = operand(args[0]); b = { mode: DIR, expr: "0" }; }
    raw.push({ ln, op, a, b });
    if (raw.length > MAX_LEN) throw `line ${ln}: warrior is longer than ${MAX_LEN} instructions`;
  }

  if (raw.length === 0) throw "needs at least one instruction";

  const evalExpr = (expr, addr, where) => {
    if (expr === "") throw `${where}: missing value`;
    let s = expr, val = 0, first = true;
    while (s.length) {
      const m = s.match(first ? /^([+-]?)\s*([A-Za-z_]\w*|\d+)\s*/ : /^([+-])\s*([A-Za-z_]\w*|\d+)\s*/);
      if (!m) throw `${where}: can't read "${expr}"`;
      let v;
      if (/^\d/.test(m[2])) v = parseInt(m[2], 10);
      else if (labels.has(m[2])) v = labels.get(m[2]) - addr;
      else throw `${where}: unknown label "${m[2]}"`;
      val += m[1] === "-" ? -v : v;
      s = s.slice(m[0].length);
      first = false;
    }
    return ((val % size) + size) % size;
  };

  const code = raw.map((r, addr) => ({
    op: r.op,
    am: r.a.mode, a: evalExpr(r.a.expr, addr, `line ${r.ln}, A-field`),
    bm: r.b.mode, b: evalExpr(r.b.expr, addr, `line ${r.ln}, B-field`),
  }));

  let start = 0;
  if (org) {
    start = evalExpr(org.expr, 0, `line ${org.ln}, start`);
    if (start >= code.length) throw `line ${org.ln}: start is outside the warrior`;
  }
  return { code, start };
}

// mulberry32, the same seed gives the same round
function rng(seed) {
  let t = seed >>> 0;
  return n => {
    t = (t + 0x6D2B79F5) >>> 0;
    let x = t;
    x = Math.imul(x ^ (x >>> 15), x | 1);
    x ^= x + Math.imul(x ^ (x >>> 7), x | 61);
    return Math.floor((((x ^ (x >>> 14)) >>> 0) / 4294967296) * n);
  };
}

class Queue {
  constructor(cap) { this.buf = new Int32Array(cap); this.head = 0; this.len = 0; }
  push(v) { this.buf[(this.head + this.len) % this.buf.length] = v; this.len++; }
  shift() { const v = this.buf[this.head]; this.head = (this.head + 1) % this.buf.length; this.len--; return v; }
  toArray() {
    const out = new Array(this.len);
    for (let i = 0; i < this.len; i++) out[i] = this.buf[(this.head + i) % this.buf.length];
    return out;
  }
}

let m = null;

function marsNew(src1, src2, size, seed) {
  size = size | 0;
  const srcs = src2.trim() === "" ? [src1] : [src1, src2];
  const ws = [];
  for (let i = 0; i < srcs.length; i++) {
    try { ws.push(parse(srcs[i], size)); }
    catch (e) { m = null; return `Warrior ${i + 1}: ${e}`; }
  }

  const core = {
    op: new Uint8Array(size), am: new Uint8Array(size), bm: new Uint8Array(size),
    a: new Int32Array(size), b: new Int32Array(size),
  };
  // '88 starts with DAT $0, $0 everywhere
  core.am.fill(DIR); core.bm.fill(DIR);
  const owner = new Uint8Array(size);
  const wtime = new Int32Array(size);

  const rand = rng(seed);
  const pos = [rand(size)];
  if (ws.length === 2) {
    const span = size - ws[0].code.length - ws[1].code.length - 2 * MIN_DIST;
    if (span < 0) { m = null; return "core is too small for both warriors"; }
    pos.push((pos[0] + ws[0].code.length + MIN_DIST + rand(span + 1)) % size);
  }

  const queues = ws.map((w, i) => {
    w.code.forEach((ins, k) => {
      const t = (pos[i] + k) % size;
      core.op[t] = ins.op; core.am[t] = ins.am; core.a[t] = ins.a;
      core.bm[t] = ins.bm; core.b[t] = ins.b;
      owner[t] = i + 1;
    });
    const q = new Queue(MAX_PROCS);
    q.push((pos[i] + w.start) % size);
    return q;
  });

  m = { size, core, owner, wtime, queues, pos, lens: ws.map(w => w.code.length), n: ws.length, turn: 0, cycle: 0, status: 0 };
  return "";
}

function execOne(w) {
  const { size, core, owner, wtime } = m;
  const q = m.queues[w];
  const mark = t => { owner[t] = w + 1; wtime[t] = m.cycle; };
  const pc = q.shift();
  const next = (pc + 1) % size;
  const op = core.op[pc], am = core.am[pc], bm = core.bm[pc];
  const av = core.a[pc], bv = core.b[pc];

  const resolve = (mode, v) => {
    if (mode === IMM) return pc;
    let t = (pc + v) % size;
    if (mode === DEC) {
      core.b[t] = (core.b[t] + size - 1) % size;
      mark(t);
    }
    if (mode === IND || mode === DEC) t = (t + core.b[t]) % size;
    return t;
  };
  const snap = t => ({ op: core.op[t], am: core.am[t], a: core.a[t], bm: core.bm[t], b: core.b[t] });

  // A is resolved and buffered before B, a predecrement in B can't change what A read
  const pa = resolve(am, av);
  const ia = snap(pa);
  const pb = resolve(bm, bv);
  const ib = snap(pb);

  switch (op) {
    case DAT:
      return;
    case MOV:
      if (am === IMM) core.b[pb] = av;
      else { core.op[pb] = ia.op; core.am[pb] = ia.am; core.a[pb] = ia.a; core.bm[pb] = ia.bm; core.b[pb] = ia.b; }
      mark(pb);
      break;
    case ADD:
    case SUB: {
      const f = op === ADD ? (x, y) => (x + y) % size : (x, y) => (x - y + size) % size;
      if (am === IMM) core.b[pb] = f(ib.b, av);
      else { core.a[pb] = f(ib.a, ia.a); core.b[pb] = f(ib.b, ia.b); }
      mark(pb);
      break;
    }
    case JMP:
      q.push(pa);
      return;
    case JMZ:
      q.push(ib.b === 0 ? pa : next);
      return;
    case JMN:
      q.push(ib.b !== 0 ? pa : next);
      return;
    case DJN:
      core.b[pb] = (core.b[pb] + size - 1) % size;
      mark(pb);
      q.push(core.b[pb] !== 0 ? pa : next);
      return;
    case CMP: {
      const eq = am === IMM
        ? av === ib.b
        : ia.op === ib.op && ia.am === ib.am && ia.a === ib.a && ia.bm === ib.bm && ia.b === ib.b;
      q.push(eq ? (pc + 2) % size : next);
      return;
    }
    case SLT:
      q.push((am === IMM ? av : ia.b) < ib.b ? (pc + 2) % size : next);
      return;
    case SPL:
      q.push(next);
      if (q.len < MAX_PROCS) q.push(pa);
      return;
  }
  q.push(next);
}

function marsStep(n) {
  if (!m) return 0;
  for (let i = 0; i < n && !m.status; i++) {
    const w = m.turn;
    execOne(w);
    m.cycle++;
    if (m.queues[w].len === 0) {
      m.status = m.n === 1 ? 4 : (w === 0 ? 2 : 1);
    } else if (m.cycle >= MAX_CYCLES * m.n) {
      m.status = 3;
    } else if (m.n === 2) {
      m.turn = 1 - w;
    }
  }
  return m.status;
}

function marsState(owners, ages) {
  if (!m) return { cycle: 0, winner: 0, p1: 0, p2: 0, pcs1: [], pcs2: [] };
  owners.set(m.owner);
  for (let i = 0; i < m.size; i++) {
    ages[i] = Math.min(255, ((m.cycle - m.wtime[i]) / AGE_DIV) | 0);
  }
  const q2 = m.queues[1];
  return {
    cycle: m.cycle, winner: m.status,
    p1: m.queues[0].len, p2: q2 ? q2.len : 0,
    pcs1: m.queues[0].toArray(), pcs2: q2 ? q2.toArray() : [],
  };
}

function marsDump(from, count) {
  if (!m) return [];
  const { size, core, owner } = m;
  const signed = v => (v > size / 2 ? v - size : v);
  const out = [];
  for (let k = 0; k < count; k++) {
    const t = (((from + k) % size) + size) % size;
    out.push({
      addr: t,
      owner: owner[t],
      text: `${OPS[core.op[t]]} ${MODES[core.am[t]]}${signed(core.a[t])}, ${MODES[core.bm[t]]}${signed(core.b[t])}`,
    });
  }
  return out;
}

/** Check a warrior without touching the core -> "" or the error text. */
export function marsCheck(src, size = 8000) {
  try { parse(src, size); return ""; } catch (e) { return String(e); }
}

/** Where engine slot `w` (0/1) was loaded, and how long its code is now. */
export function marsSlot(w) {
  return m && w < m.n ? { pos: m.pos[w], len: m.lens[w] } : null;
}

/** Mid-round reprogramming: the new code overwrites the cells from the
 *  slot's loading address on; its process queue is NOT touched (the processes
 *  keep running at their addresses), ORG is ignored. -> "" or error text. */
export function marsPatch(w, src) {
  if (!m || w >= m.n) return "no running round";
  let prog;
  try { prog = parse(src, m.size); } catch (e) { return String(e); }
  const { size, core, owner, wtime } = m;
  prog.code.forEach((ins, k) => {
    const t = (m.pos[w] + k) % size;
    core.op[t] = ins.op; core.am[t] = ins.am; core.a[t] = ins.a;
    core.bm[t] = ins.bm; core.b[t] = ins.b;
    owner[t] = w + 1; wtime[t] = m.cycle;
  });
  m.lens[w] = prog.code.length;
  return "";
}

/** Executed instructions so far (both warriors together). */
export function marsCycle() { return m ? m.cycle : 0; }

export { marsNew, marsStep, marsState, marsDump, OPS };
