// kAIm56 — self-hosted Firecracker AI-agent platform
// Copyright (C) 2026 the kAIm56 authors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Code flow's state machine, per repo, driven by every open Code flow page:
//
//   cloning  --checkout done-->  (prepare)  -->  analysing
//   analysing --task over, checker OK-->          ready
//   analysing --task over, problems, rounds left-->  analysing (next round)
//   analysing --task over, problems, no rounds-->   incomplete
//   (any error)                                    -> failed
//
// Where the state lives: the instance config keeps CODEFLOW_REPO (this
// instance IS a Code flow repo) and the model; everything that changes while
// the machine runs (STATE, TASK, ROUND, COMMIT, STACK, SKILLS, ERROR, LOCK)
// lives in the app's Durable Object on celld (/apps/codeflow/_api/repos).
// A Durable Object handles one request at a time, so a multi-field update is
// atomic and the claim is a real compare-and-set — the two workarounds of the
// config-key days ("write, wait, read back" for the lock; dependent keys
// before STATE) are gone. Repos from those days are moved over once (migrate).
// `api` is injected (the page passes its fetch wrappers, tests a fake).

import * as L from './lib.js';
import { validateTree } from './flowcheck.js';

export const LOCK_TTL = 10 * 60 * 1000;

/** The keys that live in the Durable Object, not in the instance config. */
export const STATE_KEYS = ['CODEFLOW_STATE', 'CODEFLOW_TASK', 'CODEFLOW_ROUND', 'CODEFLOW_COMMIT', 'CODEFLOW_STACK',
  'CODEFLOW_SKILLS', 'CODEFLOW_ERROR', 'CODEFLOW_LOCK'];

/** Repos = instances with CODEFLOW_REPO; `states` (name -> state from the Durable
 *  Object) wins over leftover config keys of a repo not migrated yet. */
export function reposFrom(instances, states = {}) {
  return (instances || []).filter(i => i.config?.CODEFLOW_REPO).map(i => {
    const c = { ...i.config, ...(states[i.name] || {}) };
    return { name: i.name, full: c.CODEFLOW_REPO, url: `https://github.com/${c.CODEFLOW_REPO}`, state: c.CODEFLOW_STATE || 'failed',
      task: c.CODEFLOW_TASK || '', round: +c.CODEFLOW_ROUND || 0, commit: c.CODEFLOW_COMMIT || '', error: c.CODEFLOW_ERROR || '',
      stack: (c.CODEFLOW_STACK || '').split(',').filter(Boolean), skills: (c.CODEFLOW_SKILLS || '').split(',').filter(Boolean),
      model: c.LLAMA_ENDPOINT ? `local · ${hostOf(c.LLAMA_ENDPOINT)}` : c.OPENROUTER_MODEL || '',
      local: !!c.LLAMA_ENDPOINT, running: !!i.running, lock: c.CODEFLOW_LOCK || '' };
  }).sort((a, b) => a.full.toLowerCase().localeCompare(b.full.toLowerCase()));
}
export const LOCAL = 'local';                    // the model choice "the self-hosted llama.cpp"
export const hostOf = ep => (String(ep).match(/^https?:\/\/([^/:]+)/) || [])[1] || '';

/** The llama.cpp endpoint the platform already uses: Settings, else an existing llama instance. */
export function localEndpoint(settings, instances) {
  return String(settings?.LLAMA_ENDPOINT || '').trim() ||
    ((instances || []).find(i => i.template === 'llama' && i.config?.LLAMA_ENDPOINT)?.config.LLAMA_ENDPOINT || '');
}

/** Config keys for a model choice. Local: the agent talks to llama.cpp directly (the
 *  manager's firewall opens exactly that host:port) and EGRESS_ALLOW pins everything
 *  else shut — the private address is rejected before the allowlist, so only the
 *  endpoint and DNS stay open. Cloud: through the key proxy, no internet at all. */
export function modelConfig(model, endpoint, proxy) {
  if (model === LOCAL) {
    if (!endpoint) throw new Error('no local model is set up (no LLAMA_ENDPOINT in Settings or on a llama instance)');
    return { cfg: { LLAMA_ENDPOINT: endpoint, LLAMA_MODEL: 'local-model', EGRESS_ALLOW: hostOf(endpoint), OPENROUTER_MODEL: '' }, internet: true };
  }
  return { cfg: { LLAMA_ENDPOINT: '', LLAMA_MODEL: '', EGRESS_ALLOW: '', OPENROUTER_MODEL: model || L.DEFAULT_MODEL }, internet: !proxy };
}

const taskIdOf = r => ((r && r.msg) || '').match(/task (\w+) created/)?.[1] || '';

/** A round that ended on an error another round would only repeat — the per-instance
 *  token budget (429 guardrail), a missing key or credit (401/402/403) — stops the
 *  machine with that message. Timeouts and dropped connections are transient: the
 *  next round may get through, so they do not stop it. */
export function stopReason(result) {
  const r = String(result || '');
  if (/guardrail: budget/.test(r)) return 'the daily token budget of this instance is used up (' +
    (r.match(/budget: ([\d,]+\/[\d,]+ tokens used today)/)?.[1] || 'guardrail') + ') — raise it in the manager\'s Policy tab (applies at once) or try tomorrow';
  if (/HTTP (401|402|403|429)\b/.test(r)) return 'the model provider refused the request: ' + r.replace(/^⚠️\s*/, '').slice(0, 240);
  return '';
}

/** Move a repo's state from its instance config into the Durable Object, once:
 *  seed (only if the object has nothing yet), then drop the config keys. */
export async function migrate(api, instances, states) {
  let moved = 0;
  for (const i of instances || []) {
    if (!i.config?.CODEFLOW_REPO) continue;
    const left = STATE_KEYS.filter(k => k in i.config);
    if (!left.length) continue;
    if (!states[i.name]) await api.seed(i.name, Object.fromEntries(left.map(k => [k, i.config[k]])));
    for (const k of left) await api.setCfg(i.name, k, '');
    moved++;
  }
  return moved;
}

export function createMachine(api, { checkerSource, fileLines, tab = Math.random().toString(36).slice(2, 10) } = {}) {
  const inflight = new Set();
  /** State keys in one atomic update of the Durable Object, the rest (model …) to the config. */
  async function setCfgs(name, kv) {
    const state = {}, cfg = [];
    for (const [k, v] of Object.entries(kv)) (STATE_KEYS.includes(k) ? (state[k] = L.oneLine(v)) : cfg.push([k, L.oneLine(v)]));
    for (const [k, v] of cfg) await api.setCfg(name, k, v);
    if (Object.keys(state).length) await api.patch(name, state);
  }
  const fresh = async name => reposFrom(await api.instances(), await api.states()).find(r => r.name === name);

  /** Claim the repo for this page: a compare-and-set in the Durable Object. */
  const claim = name => api.claim(name, tab, LOCK_TTL);
  const release = name => api.release(name, tab).catch(() => {});

  async function checkFlow(name, commit) {
    return validateTree(f => api.flow(name, f.replace(/\.json$/, '')), f => fileLines(name, f, commit));
  }

  async function startCheckout(name, full) {
    const co = await api.checkout(name, full);
    if (!co.ok) { await setCfgs(name, { CODEFLOW_ERROR: co.error || 'checkout failed', CODEFLOW_STATE: 'failed' }); throw new Error(co.error || 'checkout failed'); }
  }

  /** Open (or re-open) a repo: instance through /api/create, files through the checkout.
   *  `model`: an OpenRouter id, LOCAL, or '' (default — keeps an existing repo's choice). */
  async function openRepo(text, model) {
    const { owner, repo, full } = L.parseRepo(text);
    const [insts, settings, personas] = await Promise.all([api.instances(), api.settings(), api.personas()]);
    const name = L.instanceName(owner, repo, insts);
    const proxy = settings.LLM_KEY_PROXY === '1';
    const existing = insts.find(i => i.name === name);
    if (!existing) {
      const persona = personas.find(p => p.name === L.PERSONA)?.prompt || '';
      const mc = modelConfig(model, localEndpoint(settings, insts), proxy);
      const cfg = { TRANSPORT: 'web', AGENT_SYSTEM: L.frame(persona, full), AGENT_TOOLS: L.TOOLS.join(','),
        SKILL_LEARN: '0', AUTO_RESET_MIN: '0', CODEFLOW_REPO: full };
      for (const [k, v] of Object.entries(mc.cfg)) if (v) cfg[k] = v;
      const r = await api.create({ name, template: 'openrouter', mounts: [], mcps: [], internet: mc.internet, config: cfg });
      if (!/created/.test(r.msg || '')) throw new Error(r.msg || 'could not create the instance');
      await setCfgs(name, { CODEFLOW_ERROR: '', CODEFLOW_STATE: 'cloning' });
    } else {
      if (model) {                                  // switching model (or local <-> cloud) on an existing repo
        const mc = modelConfig(model, localEndpoint(settings, insts), proxy);
        await setCfgs(name, mc.cfg);
        if (!!existing.internet !== mc.internet) await api.setInternet(name, mc.internet);
      }
      await setCfgs(name, { CODEFLOW_REPO: full, CODEFLOW_ERROR: '', CODEFLOW_STATE: 'cloning' });
    }
    await startCheckout(name, full);
    return name;
  }

  /** Re-analyse: a fresh checkout, then the machine takes over. */
  async function reanalyse(repo) {
    await setCfgs(repo.name, { CODEFLOW_ERROR: '', CODEFLOW_STATE: 'cloning' });
    await startCheckout(repo.name, repo.full);
  }

  /** After the checkout: stack, skills as a playbook rule, a clean conversation, round 1. */
  async function prepare(repo, commit) {
    const n = repo.name;
    const files = await L.listFiles(p => api.ls(n, 'src' + (p ? '/' + p : '')));
    const stack = await L.detectStack(files, f => api.text(n, 'src/' + f));
    const skills = L.pickSkills(stack, await api.skills());
    for (const p of await api.playbooks(n)) if (String(p.text || '').startsWith('Repository stack:')) await api.pbRemove(n, p.id);
    if (stack.length) await api.pbAdd(n, `Repository stack: ${stack.join(', ')}.` + (skills.length
      ? ` Relevant skills in the library: ${skills.join(', ')} — load one with load_skill when a question touches its area.` : ''));
    if (repo.running) await api.restart(n);        // a changed model applies on start
    await api.say(n, '/reset');                    // no reading back its own earlier analysis
    const id = taskIdOf(await api.addTask(n, L.analysisPrompt(repo.full, commit, stack, skills, await checkerSource())));
    if (!id) throw new Error('could not queue the analysis task');
    // one atomic update: other pages never see a half-written round
    await setCfgs(n, { CODEFLOW_TASK: id, CODEFLOW_ROUND: '1', CODEFLOW_COMMIT: commit, CODEFLOW_STACK: stack.join(','),
      CODEFLOW_SKILLS: skills.join(','), CODEFLOW_ERROR: '', CODEFLOW_STATE: 'analysing' });
  }

  /** One step for one repo. Returns true when it changed something. */
  async function tick(snapshot) {
    if (inflight.has(snapshot.name) || !['cloning', 'analysing'].includes(snapshot.state)) return false;
    inflight.add(snapshot.name);
    let claimed = false;
    try {
      // a cheap pre-check on the snapshot before claiming anything
      if (snapshot.state === 'cloning') { if ((await api.checkoutState(snapshot.name)).status === 'running') return false; }
      else {
        const t = (await api.tasks()).find(x => x.id === snapshot.task);
        if (t && ['pending', 'running'].includes(t.status)) return false;
      }
      if (!(claimed = await claim(snapshot.name))) return false;
      const repo = await fresh(snapshot.name);                // act on the state as it is NOW
      if (!repo) return false;
      if (repo.state === 'cloning') {
        const co = await api.checkoutState(repo.name);
        if (co.status === 'running') return false;
        if (co.status !== 'done') {
          await setCfgs(repo.name, { CODEFLOW_ERROR: co.error || 'the checkout was interrupted (manager restart?) — re-analyse', CODEFLOW_STATE: 'failed' });
          return true;
        }
        await prepare(repo, co.commit);
        return true;
      }
      if (repo.state !== 'analysing') return false;
      const t = (await api.tasks()).find(x => x.id === repo.task);
      if (t && ['pending', 'running'].includes(t.status)) return false;
      const res = await checkFlow(repo.name, repo.commit);
      const stop = !res.ok && stopReason(t?.result);
      if (stop) {                                // no round is burnt on a budget or access error — they repeat
        await setCfgs(repo.name, { CODEFLOW_ERROR: stop, CODEFLOW_STATE: 'failed' });
        return true;
      }
      if (res.ok) { await setCfgs(repo.name, { CODEFLOW_ERROR: '', CODEFLOW_STATE: 'ready' }); return true; }
      if (repo.round >= L.MAX_ROUNDS) {
        await setCfgs(repo.name, { CODEFLOW_ERROR: `${res.errors.length} problem(s) left after ${repo.round} rounds`, CODEFLOW_STATE: 'incomplete' });
        return true;
      }
      // The browser check decides; the agent's copy of the checker is only its helper — a byte-level
      // "differs" note once made a model spend a whole round hex-dumping the file.
      const id = taskIdOf(await api.addTask(repo.name, L.followupPrompt(repo.full, repo.commit, repo.stack, repo.skills,
        await checkerSource(), res.errors, repo.round + 1)));
      if (id) await setCfgs(repo.name, { CODEFLOW_TASK: id, CODEFLOW_ROUND: String(repo.round + 1) });
      return true;
    } catch (e) {
      await setCfgs(snapshot.name, { CODEFLOW_ERROR: String(e.message || e), CODEFLOW_STATE: 'failed' }).catch(() => {});
      return true;
    } finally {
      if (claimed) await release(snapshot.name);
      inflight.delete(snapshot.name);
    }
  }

  return { tick, openRepo, reanalyse, checkFlow, setCfgs, claim };
}
