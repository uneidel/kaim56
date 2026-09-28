// kAIm56 — Core War app: tests for coach.js (what the model gets and gives back)
// and the engine's mid-round patch. Run: tests/run-tests.sh
// SPDX-License-Identifier: AGPL-3.0-or-later
import assert from 'node:assert/strict';
import { rules, matrix, buildTask, parseAnswer, lineDiff, taskId, CORE, DEFAULTS } from './coach.js';
import { marsNew, marsStep, marsState, marsDump, marsPatch, marsCheck, marsSlot, marsCycle } from './mars.js';

let n = 0;
const test = (name, fn) => { fn(); n++; };

test('rules name the checkpoint, the cap and the swap-in', () => {
  const r = rules({ every: 500, perRound: 10, maxSteps: 8 });
  assert.match(r, /Every 500 cycles/);
  assert.match(r, /at most 10 times per round/);
  assert.match(r, /LOADING ADDRESS/);
  assert.match(r, /processes are NOT moved or reset/);
  assert.match(rules({ every: 250, perRound: 3, maxSteps: 2 }), /Every 250 cycles[\s\S]*at most 3 times/);
});

test('matrix: 80 rows of 100 cells from the player\'s view', () => {
  const owners = new Uint8Array(CORE);
  owners[0] = 1; owners[1] = 2; owners[7999] = 2; owners[150] = 1;
  const m1 = matrix(owners, [150], [7999], 1).split('\n');
  assert.equal(m1.length, 80);
  assert.ok(m1.every(l => l.length === 105));
  assert.equal(m1[0].slice(0, 7), '0000 AB');
  assert.equal(m1[1][5 + 50], 'a');                  // own process on an own cell
  assert.equal(m1[79].slice(-1), 'b');
  const m2 = matrix(owners, [7999], [150], 2).split('\n');
  assert.equal(m2[0].slice(0, 7), '0000 BA');         // the other player sees it mirrored
});

test('the task carries rules, state, matrix, program and the answer format', () => {
  const v = { player: 2, round: 1, rounds: 5, cycle: 500, used: 0, score: 'W1 0 : ties 0 : W2 0',
    slot: { pos: 4000, len: 4 }, me: { processes: 1, cells: 30, codeAtPc: [{ addr: 4001, pc: true, text: 'MOV $0, $1' }] },
    them: { processes: 2, cells: 40, codeAtPc: [] }, source: 'MOV 0, 1\n', matrix: '0000 ' + '.'.repeat(100), history: [] };
  const t = buildTask(v, DEFAULTS);
  for (const want of ['CORE WAR RULES', 'Warrior 2', 'round 1/5', 'Your loading address: 4000', '4001 > MOV $0, $1',
                      'MOV 0, 1', '0000 ....', 'WHY:', '```redcode', 'adaptation 1 of 10'])
    assert.ok(t.includes(want), want);
});

test('answers: the last code block and the WHY text', () => {
  const a = parseAnswer('WHY: Der Gegner bombt alle 4 Zellen, also ein Imp-Ring.\n```redcode\nold\n```\nbetter:\n```redcode\nMOV 0, 2667\n```');
  assert.equal(a.code, 'MOV 0, 2667');
  assert.match(a.why, /^Der Gegner bombt/);
  assert.ok(!a.why.includes('```'));
  assert.equal(parseAnswer('nothing here').error, 'no code block in the answer');
  assert.equal(parseAnswer('```\n\n```').error, 'the code block is empty');
  assert.equal(parseAnswer('Kurz: mehr SPL.\n```\nSPL 0\n```').why, 'Kurz: mehr SPL.');
});

test('line diff and task id', () => {
  assert.deepEqual(lineDiff('a\nb\nc', 'a\nx\nc'), [[' ', 'a'], ['-', 'b'], ['+', 'x'], [' ', 'c']]);
  assert.deepEqual(lineDiff('', 'x'), [['-', ''], ['+', 'x']]);
  assert.equal(taskId('task 18ef0ecf6707 created (pending)'), '18ef0ecf6707');
  assert.equal(taskId('model/max_steps apply to ephemeral tasks only'), null);
});

test('marsPatch rewrites the code at the loading address, processes keep running', () => {
  const imp = 'MOV 0, 1', dwarf = 'loop ADD #4, bomb\nMOV bomb, @bomb\nJMP loop\nbomb DAT #0, #0';
  assert.equal(marsNew(dwarf, imp, 8000, 42), '');
  marsStep(200);
  const owners = new Uint8Array(8000), ages = new Uint8Array(8000);
  const before = marsState(owners, ages);
  const s0 = marsSlot(0);
  assert.equal(s0.len, 4);
  assert.equal(marsPatch(0, 'loop ADD #3, bomb\nMOV bomb, @bomb\nJMP loop\nbomb DAT #0, #0\nDAT #1, #1'), '');
  const after = marsState(owners, ages);
  assert.deepEqual(after.pcs1, before.pcs1);          // the queue is untouched
  assert.equal(after.cycle, before.cycle);
  assert.equal(marsSlot(0).len, 5);
  const dump = marsDump(s0.pos, 5).map(c => c.text);
  assert.equal(dump[0], 'ADD #3, $3');
  assert.equal(dump[4], 'DAT #1, #1');
  assert.ok(marsDump(s0.pos, 5).every(c => c.owner === 1));
  // a broken program is refused and nothing changes
  assert.match(marsPatch(0, 'FOO 1, 2'), /unknown opcode/);
  assert.equal(marsDump(s0.pos, 1)[0].text, 'ADD #3, $3');
  assert.match(marsCheck('MOV 1'), /two operands/);
  assert.equal(marsCheck(imp), '');
  assert.equal(marsCycle(), 200);
});

console.log(`corewar: ${n} tests passed`);
