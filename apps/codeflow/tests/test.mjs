// kAIm56 — Code flow app: tests for flowcheck.js (parity with validate_flow.py) and lib.js.
// SPDX-License-Identifier: AGPL-3.0-or-later
// Run after test_validate.py (it writes py-results.json): node tests/test.mjs  (see run-tests.sh)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validateTree } from './flowcheck.js';
import * as L from './lib.js';
import { createMachine, reposFrom, LOCK_TTL, stopReason, modelConfig, localEndpoint, LOCAL } from './machine.js';

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
for (const want of ['<<<VALIDATE_FLOW_PY\nCHECKER\nVALIDATE_FLOW_PY', 'WRITE EARLY', 'WORK IN SMALL READS', 'do not draw from memory', 'validate_flow.py find NAME', 'flow/overview.json', 'memory_store'])
  assert.ok(p1.includes(want), want);
const p2 = L.followupPrompt('acme/shop', 'abc', [], [], 'CHECKER', ['overview: problem one'], 2);
assert.ok(p2.startsWith(`/steps ${L.ANALYSIS_STEPS} CONTINUE (round 2 of ${L.MAX_ROUNDS})`));
assert.ok(p2.includes('- overview: problem one')); assert.equal(p2.split('/steps').length - 1, 1);
assert.equal(L.oneLine('a\nb\r\nc'), 'a b c');
assert.ok(L.frame('PERSONA', 'acme/shop').startsWith('PERSONA\n\nYou are the code agent of the GitHub repository acme/shop'));

// ---- machine.js: several open pages must not race ------------------------------------
function fakeWorld({ state, task = 't0', taskStatus = 'done', round = 1, flowOk = false, lock = '' }) {
  const cfg = { CODEFLOW_REPO: 'acme/shop', CODEFLOW_STATE: state, CODEFLOW_TASK: task, CODEFLOW_ROUND: String(round),
    CODEFLOW_COMMIT: 'c1', CODEFLOW_LOCK: lock, OPENROUTER_MODEL: 'm' };
  const w = { cfg, writes: [], tasks: [{ id: task, status: taskStatus }], added: [], playbooks: [] };
  const hop = () => new Promise(r => setTimeout(r, 1));              // every call yields, like the network
  const flows = flowOk ? { overview: clone(FX.overview), ...clone(FX.details) } : {};
  w.api = {
    instances: async () => { await hop(); return [{ name: 'repo-shop', running: false, config: { ...cfg } }]; },
    setCfg: async (n, k, v) => { await hop(); w.writes.push([k, v]); if (v === '') delete cfg[k]; else cfg[k] = v; return { msg: 'ok' }; },
    tasks: async () => { await hop(); return w.tasks.map(t => ({ ...t })); },
    checkoutState: async () => { await hop(); return { status: 'done', commit: 'c2' }; },
    addTask: async (n, message) => { await hop(); const id = 't' + (w.tasks.length); w.tasks.push({ id, status: 'pending', message }); w.added.push(message); return { msg: `task ${id} created (pending)` }; },
    playbooks: async () => { await hop(); return w.playbooks; }, pbRemove: async () => hop(), pbAdd: async (n, text) => { await hop(); w.playbooks.push({ id: 'p', text }); },
    skills: async () => [{ name: 'docker' }], restart: async () => hop(), say: async () => hop(),
    ls: async (n, path) => path === 'src' ? [{ name: 'Dockerfile' }, { name: 'app.py' }] : [],
    text: async () => '', flow: async (n, id) => (id in flows ? flows[id] : null), checkout: async () => ({ ok: true }),
  };
  w.opts = tab => ({ tab, settle: 5, checkerSource: async () => 'CHECKER', fileLines: async (n, f) => (f in FX.files ? FX.files[f].split('\n') : null) });
  w.repo = () => reposFrom([{ name: 'repo-shop', config: { ...cfg } }])[0];
  return w;
}
{ // two pages, checkout done: exactly one of them prepares; the state key is written last
  const w = fakeWorld({ state: 'cloning' });
  const A = createMachine(w.api, w.opts('A')), B = createMachine(w.api, w.opts('B'));
  const snap = w.repo();
  await Promise.all([A.tick(snap), B.tick(snap)]);
  assert.equal(w.added.length, 1, 'one analysis task');
  assert.ok(w.added[0].startsWith(`/steps ${L.ANALYSIS_STEPS} Analyse the repository acme/shop (commit c2)`));
  assert.deepEqual([w.cfg.CODEFLOW_STATE, w.cfg.CODEFLOW_ROUND, w.cfg.CODEFLOW_TASK, w.cfg.CODEFLOW_COMMIT], ['analysing', '1', 't1', 'c2']);
  const keys = w.writes.map(x => x[0]);
  assert.ok(keys.lastIndexOf('CODEFLOW_STATE') > keys.lastIndexOf('CODEFLOW_TASK'), 'state after task');
  assert.ok(keys.lastIndexOf('CODEFLOW_STATE') > keys.lastIndexOf('CODEFLOW_ROUND'), 'state after round');
  assert.equal(w.cfg.CODEFLOW_LOCK, undefined, 'lock released');
}
{ // the race of 2026-09-27: round 3 of an OLD run, its task done, while a page is mid-prepare:
  // the holder writes task/round before the state, so a second page never sees the stale pair
  const w = fakeWorld({ state: 'cloning', task: 'old', round: 3 });
  const A = createMachine(w.api, w.opts('A')), B = createMachine(w.api, w.opts('B'));
  const snap = w.repo();
  const pa = A.tick(snap);
  for (let i = 0; i < 40; i++) {                                   // B keeps polling while A prepares
    await new Promise(r => setTimeout(r, 2));
    await B.tick(w.repo());
  }
  await pa;
  assert.equal(w.added.length, 1); assert.equal(w.cfg.CODEFLOW_STATE, 'analysing'); assert.equal(w.cfg.CODEFLOW_ROUND, '1');
}
{ // two pages, task over, graph missing: exactly one follow-up round
  const w = fakeWorld({ state: 'analysing', round: 1 });
  const A = createMachine(w.api, w.opts('A')), B = createMachine(w.api, w.opts('B'));
  await Promise.all([A.tick(w.repo()), B.tick(w.repo())]);
  assert.equal(w.added.length, 1); assert.equal(w.cfg.CODEFLOW_ROUND, '2');
  assert.ok(w.added[0].includes('CONTINUE (round 2 of 3)') && w.added[0].includes('flow/overview.json does not exist yet'));
  assert.ok(!w.added[0].includes('differs from the original'));
}
{ // a running task: nothing happens, no lock taken
  const w = fakeWorld({ state: 'analysing', taskStatus: 'running' });
  assert.equal(await createMachine(w.api, w.opts('A')).tick(w.repo()), false);
  assert.equal(w.writes.length, 0);
}
{ // a foreign fresh lock blocks; a stale one is taken over
  const w = fakeWorld({ state: 'analysing', lock: `other@${Date.now()}` });
  assert.equal(await createMachine(w.api, w.opts('A')).tick(w.repo()), false); assert.equal(w.added.length, 0);
  w.cfg.CODEFLOW_LOCK = `other@${Date.now() - LOCK_TTL - 1000}`;
  assert.equal(await createMachine(w.api, w.opts('A')).tick(w.repo()), true); assert.equal(w.added.length, 1);
}
{ // a valid graph -> ready; rounds used up -> incomplete
  const w = fakeWorld({ state: 'analysing', flowOk: true });
  await createMachine(w.api, w.opts('A')).tick(w.repo()); assert.equal(w.cfg.CODEFLOW_STATE, 'ready');
  const x = fakeWorld({ state: 'analysing', round: 3 });
  await createMachine(x.api, x.opts('A')).tick(x.repo());
  assert.equal(x.cfg.CODEFLOW_STATE, 'incomplete'); assert.equal(x.added.length, 0);
  assert.match(x.cfg.CODEFLOW_ERROR, /problem\(s\) left after 3 rounds/);
}
{ // 2026-09-27: every round died at once on the token budget and the app burnt all three rounds
  const w = fakeWorld({ state: 'analysing', round: 1 });
  w.tasks[0].result = '⚠️ OpenRouter HTTP 429: {"error": {"message": "guardrail: budget: 5,021,229/5,000,000 tokens used today", "code": 429}}';
  await createMachine(w.api, w.opts('A')).tick(w.repo());
  assert.equal(w.cfg.CODEFLOW_STATE, 'failed'); assert.equal(w.added.length, 0); assert.equal(w.cfg.CODEFLOW_ROUND, '1');
  assert.match(w.cfg.CODEFLOW_ERROR, /daily token budget .*5,021,229\/5,000,000 tokens used today.*Policy tab/);
  assert.match(stopReason('⚠️ OpenRouter HTTP 402: insufficient credits'), /refused the request: OpenRouter HTTP 402/);
  assert.equal(stopReason("⚠️ OpenRouter error: TimeoutError('timed out')"), '');          // transient: a new round
  const v = fakeWorld({ state: 'analysing', round: 1 });
  v.tasks[0].result = "⚠️ OpenRouter error: TimeoutError('timed out')";
  await createMachine(v.api, v.opts('A')).tick(v.repo());
  assert.equal(v.cfg.CODEFLOW_ROUND, '2'); assert.equal(v.added.length, 1);
}
{ // local model: the Jetson's llama.cpp, reached directly, everything else shut; switching back to cloud undoes it
  const EP = 'http://192.168.56.247:8080/';
  assert.equal(localEndpoint({}, [{ template: 'llama', config: { LLAMA_ENDPOINT: EP } }]), EP);
  assert.equal(localEndpoint({ LLAMA_ENDPOINT: 'http://10.0.0.5:8080' }, []), 'http://10.0.0.5:8080');
  assert.deepEqual(modelConfig(LOCAL, EP, true), { cfg: { LLAMA_ENDPOINT: EP, LLAMA_MODEL: 'local-model', EGRESS_ALLOW: '192.168.56.247', OPENROUTER_MODEL: '' }, internet: true });
  assert.deepEqual(modelConfig('qwen/qwen3-coder', EP, true), { cfg: { LLAMA_ENDPOINT: '', LLAMA_MODEL: '', EGRESS_ALLOW: '', OPENROUTER_MODEL: 'qwen/qwen3-coder' }, internet: false });
  assert.equal(modelConfig('', EP, true).cfg.OPENROUTER_MODEL, L.DEFAULT_MODEL);
  assert.throws(() => modelConfig(LOCAL, '', true), /no local model/);
  const created = [], net = [];
  const cfg = {};
  const insts = [{ name: 'uncensored', template: 'llama', config: { LLAMA_ENDPOINT: EP } }];
  const api = { instances: async () => insts, settings: async () => ({ LLM_KEY_PROXY: '1' }), personas: async () => [{ name: 'code-explorer', prompt: 'P' }],
    create: async b => { created.push(b); insts.push({ name: b.name, internet: b.internet, config: { ...b.config } }); return { msg: `instance '${b.name}' created` }; },
    setCfg: async (n, k, v) => { cfg[k] = v; }, setInternet: async (n, on) => net.push(on), checkout: async () => ({ ok: true }) };
  const Mx = createMachine(api, { tab: 'A', settle: 1, checkerSource: async () => '', fileLines: async () => null });
  assert.equal(await Mx.openRepo('acme/shop', LOCAL), 'repo-shop');
  const c = created[0];
  assert.equal(c.template, 'openrouter'); assert.equal(c.internet, true);
  assert.equal(c.config.LLAMA_ENDPOINT, EP); assert.equal(c.config.EGRESS_ALLOW, '192.168.56.247'); assert.ok(!('OPENROUTER_MODEL' in c.config));
  assert.equal(reposFrom(insts).find(r => r.name === 'repo-shop').model, 'local · 192.168.56.247');
  await Mx.openRepo('acme/shop', 'qwen/qwen3-coder');                       // back to the cloud
  assert.deepEqual([cfg.LLAMA_ENDPOINT, cfg.EGRESS_ALLOW, cfg.OPENROUTER_MODEL], ['', '', 'qwen/qwen3-coder']); assert.deepEqual(net, [false]);
  assert.equal(created.length, 1);
}
console.log(`OK: ${n} checker cases in parity with validate_flow.py, lib.js and machine.js checks passed`);
