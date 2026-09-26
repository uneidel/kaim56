// kAIm56 — Code flow app: tests for flowcheck.js (parity with validate_flow.py) and lib.js.
// SPDX-License-Identifier: AGPL-3.0-or-later
// Run after test_validate.py (it writes py-results.json): node tests/test.mjs  (see run-tests.sh)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validateTree } from './flowcheck.mjs';
import * as L from './lib.mjs';

const FX = JSON.parse(readFileSync(new URL('./fixtures.json', import.meta.url)));
const PY = JSON.parse(readFileSync(new URL('./py-results.json', import.meta.url)));
const clone = o => JSON.parse(JSON.stringify(o));
function apply(g, ops) {
  for (const [op, path, val] of ops) {
    const keys = path.split('.'); let obj = g;
    for (const k of keys.slice(0, -1)) obj = obj[k];
    const last = keys[keys.length - 1];
    if (op === 'set') obj[last] = val;
    else if (op === 'del') delete obj[last];
    else if (op === 'append') obj[last].push(val);
    else if (op === 'pop') obj[last].pop();
  }
  return g;
}
let n = 0;
for (const c of FX.cases) {
  const graphs = { overview: clone(FX.overview), ...clone(FX.details) };
  const readJSON = async name => {
    const id = name.replace(/\.json$/, '');
    if (!(id in graphs) || (id === c.target && c.absent)) return null;
    if (id === c.target && 'raw' in c) { try { return JSON.parse(c.raw); } catch (e) { return { __invalid: 'x' }; } }
    return id === c.target ? apply(graphs[id], c.ops || []) : graphs[id];
  };
  const readLines = async f => (f in FX.files ? FX.files[f].split('\n') : null);
  const res = await validateTree(readJSON, readLines);
  // parity: the JS checker says exactly what the Python one says (JSON parser messages aside)
  const norm = es => es.map(e => e.replace(/not valid JSON: .*/, 'not valid JSON: …'));
  assert.deepEqual(norm(res.errors), norm(PY[c.name]), `parity: ${c.name}`);
  if (c.expect) assert.ok(res.errors.join(' | ').includes(c.expect), c.name);
  else assert.deepEqual([res.ok, res.graphs, res.nodes], [true, 2, 5]);
  n++;
}

// lib.js
assert.deepEqual(L.parseRepo('https://github.com/acme/shop.git'), { owner: 'acme', repo: 'shop', full: 'acme/shop' });
assert.deepEqual(L.parseRepo('git@github.com:acme/shop.git').full, 'acme/shop');
for (const bad of ['shop', 'https://gitlab.com/a/b', 'https://evil.com/github.com/a/b', 'acme/..', ''])
  assert.throws(() => L.parseRepo(bad), undefined, bad);
assert.equal(L.instanceName('acme', 'shop', []), 'repo-shop');
assert.equal(L.instanceName('acme', 'shop', [{ name: 'repo-shop', config: { CODEFLOW_REPO: 'other/shop' } }]), 'repo-acme-shop');
assert.equal(L.instanceName('acme', 'shop', [{ name: 'repo-shop', config: { CODEFLOW_REPO: 'Acme/Shop' } }]), 'repo-shop');
const tree = { '': [{ name: 'pyproject.toml' }, { name: 'web', dir: true }, { name: 'node_modules', dir: true }, { name: 'Dockerfile' }, { name: 'app', dir: true }],
  web: [{ name: 'package.json' }, { name: 'src', dir: true }], 'web/src': [{ name: 'App.tsx' }], app: [{ name: 'main.py' }], node_modules: [{ name: 'x.js' }] };
const files = await L.listFiles(async p => tree[p] || []);
assert.deepEqual(files.sort(), ['Dockerfile', 'app/main.py', 'pyproject.toml', 'web/package.json', 'web/src/App.tsx']);
const text = { 'pyproject.toml': "dependencies = ['fastapi', 'psycopg2']", 'web/package.json': '{"dependencies": {"react": "18"}}' };
const tags = await L.detectStack(files, async f => text[f] || '');
for (const t of ['python', 'fastapi', 'javascript', 'typescript', 'react', 'docker', 'postgres']) assert.ok(tags.includes(t), t);
assert.ok(!tags.includes('django'));
assert.deepEqual(L.pickSkills(tags, [{ name: 'python-perf-optimization' }, { name: 'docker' }, { name: 'pythonic-poetry' }, { name: 'postgres-expert' }]),
  ['python-perf-optimization', 'docker', 'postgres-expert']);
const p1 = L.analysisPrompt('acme/shop', 'abc123def456', ['python'], ['docker'], 'CHECKER');
assert.ok(p1.startsWith(`/steps ${L.ANALYSIS_STEPS} Analyse the repository acme/shop (commit abc123def4)`));
for (const want of ['<<<VALIDATE_FLOW_PY\nCHECKER\nVALIDATE_FLOW_PY', 'do not draw from memory', 'validate_flow.py find NAME', 'flow/overview.json', 'memory_store'])
  assert.ok(p1.includes(want), want);
const p2 = L.followupPrompt('acme/shop', 'abc', [], [], 'CHECKER', ['overview: problem one'], 2);
assert.ok(p2.startsWith(`/steps ${L.ANALYSIS_STEPS} CONTINUE (round 2 of ${L.MAX_ROUNDS})`));
assert.ok(p2.includes('- overview: problem one')); assert.equal(p2.split('/steps').length - 1, 1);
assert.equal(L.oneLine('a\nb\r\nc'), 'a b c');
assert.ok(L.frame('PERSONA', 'acme/shop').startsWith('PERSONA\n\nYou are the code agent of the GitHub repository acme/shop'));
console.log(`OK: ${n} checker cases in parity with validate_flow.py, lib.js checks passed`);
