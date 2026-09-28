// kAIm56 — Core War app: the coach. Everything a model gets and gives back,
// pure functions (no DOM, no fetch) so tests/test.mjs can check them:
// the rules, the game-state matrix, the task text, the answer parser, the diff.
// SPDX-License-Identifier: AGPL-3.0-or-later

export const CORE = 8000, COLS = 100, ROWS = 80;

/** The adaptation settings the app runs with (defaults; the UI may change them). */
export const DEFAULTS = { every: 500, perRound: 10, maxSteps: 8 };

/** The rules, sent with EVERY task — the model has no other source. */
export function rules(cfg = DEFAULTS) {
  return `CORE WAR RULES (this arena)
Standard: ICWS'88 subset. Core: ${CORE} cells, addresses wrap modulo ${CORE}. All
arithmetic is modulo ${CORE}. Every cell starts as DAT $0, $0.
Instructions: DAT MOV ADD SUB JMP JMZ JMN DJN CMP(=SEQ) SPL SLT.
- DAT a, b      executing it kills that process (a one-operand DAT x means DAT #0, x)
- MOV a, b      copy cell A to cell B; with #a only the B-field of B is set to a
- ADD/SUB a, b  add/subtract A to/from B (with #a: only B-field; else both fields)
- JMP a         jump to A
- JMZ a, b      jump to A if the B-field of B is 0
- JMN a, b      jump to A if the B-field of B is not 0
- DJN a, b      decrement the B-field of B, jump to A if it is then not 0
- CMP a, b      skip the next instruction if A equals B (with #a: a equals B's B-field)
- SLT a, b      skip the next instruction if A (or #a) is less than B's B-field
- SPL a         queue a new process at A (the current one continues with the next cell);
                at most 8000 processes per warrior
Addressing: # immediate, $ direct (default), @ B-field indirect, < predecrement B-field indirect.
Labels, ';' comments and ORG/END label are allowed. At most 100 instructions.
Execution: the two warriors alternate one instruction each; each warrior runs its
processes round-robin. A warrior with no processes left loses the round. After
80000 cycles per warrior the round is a tie. Start positions are random, at least
100 cells apart; who moves first alternates per round. A match has several rounds
with new random positions; most round wins takes the match.

ADAPTATION (the part that is special here)
- Every ${cfg.every} cycles (per warrior) the round pauses and each AI player may send
  a new version of its program — at most ${cfg.perRound} times per round. Both players
  adapt at the same checkpoint, without seeing each other's new version.
- Your new code is WRITTEN INTO THE CORE AT YOUR LOADING ADDRESS (given below),
  cell by cell, overwriting what is there (also opponent bombs: a repair).
  Your running processes are NOT moved or reset: they continue exactly at the
  addresses where they are now. ORG / the start label is ignored mid-round.
  Cells after the end of your new code keep whatever is in them.
- So: keep instructions where your processes will run next meaningful, or make
  sure they reach your new code. A program that parses wrong is rejected and
  your old code stays in place (the adaptation still counts).
- Your new code also becomes your program for the following rounds.`;
}

/** The core as 80 rows × 100 cells from player `me`'s view (1 or 2):
 *  A = cell last written by you, B = by the opponent, . = untouched,
 *  a / b = a cell where one of your / the opponent's processes is queued. */
export function matrix(owners, pcsMine, pcsTheirs, me) {
  const row = new Array(CORE);
  for (let i = 0; i < CORE; i++) {
    const o = owners[i];
    row[i] = o === 0 ? '.' : (o === me ? 'A' : 'B');
  }
  for (const p of pcsTheirs || []) row[p] = 'b';
  for (const p of pcsMine || []) row[p] = 'a';
  const lines = [];
  for (let r = 0; r < ROWS; r++) {
    lines.push(String(r * COLS).padStart(4, '0') + ' ' + row.slice(r * COLS, (r + 1) * COLS).join(''));
  }
  return lines.join('\n');
}

/** The task text for one player at one checkpoint. `v` = the player's view:
 *  {player, model, round, rounds, cycle, left, slot:{pos,len}, me:{processes,cells,codeAtPc},
 *   them:{processes,cells,codeAtPc}|null, score, source, matrix, history:[{cycle, why, ok}]} */
export function buildTask(v, cfg = DEFAULTS) {
  const code = list => (list || []).map(c => `${String(c.addr).padStart(4)} ${c.pc ? '>' : ' '} ${c.text}`).join('\n') || '(none)';
  const hist = (v.history || []).length
    ? v.history.map(h => `- cycle ${h.cycle}: ${h.ok ? '' : '(rejected) '}${h.why}`).join('\n') : '- none yet';
  return `You are Warrior ${v.player} in a Core War match. Improve your program for the
situation below. Answer DIRECTLY — do not use tools (no bash, no files, no web):
everything you need is in this message, and you have at most ${cfg.maxSteps} steps
anyway. Your answer MUST end with the code block described at the bottom.

${rules(cfg)}

CURRENT STATE — round ${v.round}/${v.rounds}, cycle ${v.cycle} per warrior, adaptation ${v.used + 1} of ${cfg.perRound} this round
Score so far: ${v.score}
Your loading address: ${v.slot.pos} (your code occupies ${v.slot.pos}..${(v.slot.pos + v.slot.len - 1) % CORE}, ${v.slot.len} cells)
You: ${v.me.processes} processes, ${v.me.cells} cells written.
Opponent: ${v.them ? `${v.them.processes} processes, ${v.them.cells} cells written` : 'none (solo)'}

Code around your next process (> = next to run):
${code(v.me.codeAtPc)}

Code around the opponent's next process:
${v.them ? code(v.them.codeAtPc) : '(solo)'}

Your changes earlier in this round:
${hist}

YOUR CURRENT PROGRAM
\`\`\`redcode
${v.source.trim()}
\`\`\`

CORE MATRIX (row start address, then 100 cells; A = written by you, B = by the
opponent, . = untouched, a = your process, b = opponent process)
${v.matrix}

ANSWER FORMAT — exactly this, nothing after the code block:
WHY: two to four sentences IN GERMAN: what you change and why, based on the state above.
\`\`\`redcode
<your complete new program, at most 100 instructions>
\`\`\``;
}

/** The model's answer -> {code, why} or {error}. Takes the LAST code block
 *  (models like to quote the old program first) and the WHY text. */
export function parseAnswer(text) {
  const t = String(text || '');
  const blocks = [...t.matchAll(/```[ \t]*([A-Za-z0-9_-]*)[ \t]*\r?\n([\s\S]*?)```/g)];
  if (!blocks.length) return { error: 'no code block in the answer' };
  const code = blocks[blocks.length - 1][2].replace(/\s+$/, '');
  if (!code.trim()) return { error: 'the code block is empty' };
  const m = t.match(/WHY:\s*([\s\S]*?)(?:```|$)/i);
  let why = (m ? m[1] : t.slice(0, blocks[blocks.length - 1].index)).trim();
  why = why.replace(/\s+/g, ' ').slice(0, 600) || '(no reason given)';
  return { code, why };
}

/** Line diff (LCS) -> [[' ' | '-' | '+', line]], comments and blank lines kept. */
export function lineDiff(a, b) {
  const x = String(a || '').replace(/\s+$/, '').split('\n'), y = String(b || '').replace(/\s+$/, '').split('\n');
  const n = x.length, m = y.length;
  const L = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (x[i] === y[j]) { out.push([' ', x[i]]); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) out.push(['-', x[i++]]);
    else out.push(['+', y[j++]]);
  }
  while (i < n) out.push(['-', x[i++]]);
  while (j < m) out.push(['+', y[j++]]);
  return out;
}

/** "task abc123def456 created (pending)" -> "abc123def456" (the route's answer). */
export function taskId(msg) {
  const m = String(msg || '').match(/task ([0-9a-f]{6,}) created/);
  return m ? m[1] : null;
}
