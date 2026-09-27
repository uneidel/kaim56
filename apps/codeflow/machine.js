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
// State lives in the instance config as CODEFLOW_* keys, written one key per
// request. Two rules keep several open pages from racing (they did: a second
// tab saw STATE=analysing next to the previous run's finished task and round
// 3, and declared the repo incomplete while round 1 had just started):
//   * a page acts on a repo only while it holds CODEFLOW_LOCK (claimed, then
//     read back; stale after LOCK_TTL), and re-reads the state after claiming;
//   * the keys a transition depends on (task, round, commit) are written
//     BEFORE the state key that makes other pages look at them.
// `api` is injected (the page passes its fetch wrappers, tests a fake).

import * as L from './lib.js';
import { validateTree } from './flowcheck.js';

export const LOCK_TTL = 10 * 60 * 1000;

export function reposFrom(instances) {
  return (instances || []).filter(i => i.config?.CODEFLOW_REPO).map(i => {
    const c = i.config;
    return { name: i.name, full: c.CODEFLOW_REPO, url: `https://github.com/${c.CODEFLOW_REPO}`, state: c.CODEFLOW_STATE || 'failed',
      task: c.CODEFLOW_TASK || '', round: +c.CODEFLOW_ROUND || 0, commit: c.CODEFLOW_COMMIT || '', error: c.CODEFLOW_ERROR || '',
      stack: (c.CODEFLOW_STACK || '').split(',').filter(Boolean), skills: (c.CODEFLOW_SKILLS || '').split(',').filter(Boolean),
      model: c.OPENROUTER_MODEL || '', running: !!i.running, lock: c.CODEFLOW_LOCK || '' };
  }).sort((a, b) => a.full.toLowerCase().localeCompare(b.full.toLowerCase()));
}
const taskIdOf = r => ((r && r.msg) || '').match(/task (\w+) created/)?.[1] || '';

/** A round that ended on an error another round would only repeat — the per-instance
 *  token budget (429 guardrail), a missing key or credit (401/402/403) — stops the
 *  machine with that message. Timeouts and dropped connections are transient: the
 *  next round may get through, so they do not stop it. */
export function stopReason(result) {
  const r = String(result || '');
  if (/guardrail: budget/.test(r)) return 'the daily token budget of this instance is used up (' +
    (r.match(/budget: ([\d,]+\/[\d,]+ tokens used today)/)?.[1] || 'guardrail') + ') — raise BUDGET_TOKENS in its config or try tomorrow';
  if (/HTTP (401|402|403|429)\b/.test(r)) return 'the model provider refused the request: ' + r.replace(/^⚠️\s*/, '').slice(0, 240);
  return '';
}

export function createMachine(api, { checkerSource, fileLines, sleep = ms => new Promise(r => setTimeout(r, ms)),
  now = () => Date.now(), tab = Math.random().toString(36).slice(2, 10), settle = 1500 } = {}) {
  const inflight = new Set();
  async function setCfgs(name, kv) { for (const [k, v] of Object.entries(kv)) await api.setCfg(name, k, L.oneLine(v)); }
  const fresh = async name => reposFrom(await api.instances()).find(r => r.name === name);

  /** Claim the repo for this page: write our lock, wait, read it back. */
  async function claim(name) {
    const cur = (await fresh(name))?.lock || '';
    const [owner, ts] = cur.split('@');
    if (cur && owner !== tab && now() - (+ts || 0) < LOCK_TTL) return false;
    const mine = `${tab}@${now()}`;
    await api.setCfg(name, 'CODEFLOW_LOCK', mine);
    await sleep(settle);                       // a racing page writes in this window; the last writer wins
    return (await fresh(name))?.lock === mine;
  }
  const release = name => api.setCfg(name, 'CODEFLOW_LOCK', '').catch(() => {});

  async function checkFlow(name, commit) {
    return validateTree(f => api.flow(name, f.replace(/\.json$/, '')), f => fileLines(name, f, commit));
  }

  async function startCheckout(name, full) {
    const co = await api.checkout(name, full);
    if (!co.ok) { await setCfgs(name, { CODEFLOW_ERROR: co.error || 'checkout failed', CODEFLOW_STATE: 'failed' }); throw new Error(co.error || 'checkout failed'); }
  }

  /** Open (or re-open) a repo: instance through /api/create, files through the checkout. */
  async function openRepo(text, model) {
    const { owner, repo, full } = L.parseRepo(text);
    const [insts, settings, personas] = await Promise.all([api.instances(), api.settings(), api.personas()]);
    const name = L.instanceName(owner, repo, insts);
    if (!insts.some(i => i.name === name)) {
      const persona = personas.find(p => p.name === L.PERSONA)?.prompt || '';
      const r = await api.create({ name, template: 'openrouter', mounts: [], mcps: [],
        internet: settings.LLM_KEY_PROXY !== '1',            // with the key proxy the agent needs no internet at all
        config: { TRANSPORT: 'web', AGENT_SYSTEM: L.frame(persona, full), AGENT_TOOLS: L.TOOLS.join(','),
          OPENROUTER_MODEL: model || L.DEFAULT_MODEL, SKILL_LEARN: '0', AUTO_RESET_MIN: '0',
          CODEFLOW_REPO: full, CODEFLOW_STATE: 'cloning' } });
      if (!/created/.test(r.msg || '')) throw new Error(r.msg || 'could not create the instance');
    } else {
      if (model) await api.setCfg(name, 'OPENROUTER_MODEL', model);
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
    // everything the next step reads first, the state that makes it read them last
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
