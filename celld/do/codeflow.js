// kAIm56 — Code flow's Durable Object: the per-repo state machine's state
// One module per app class; worker.js (celld) imports it, and the manager
// assembles it with do/storage.js into a single worker for Cloudflare
// (mgr/cloud.py) — so: no other imports, and helpers prefixed per module.
// SPDX-License-Identifier: AGPL-3.0-or-later
import { DurableObject } from "cloudflare:workers";
import { storageExport, storageImport } from "./storage.js";

const cfJson = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

// ---- Code flow: the per-repo state machine's state ------------------------
// One object for the app; one storage entry per repo instance. Every request
// is handled alone (a Durable Object is single-threaded), which is what the
// state machine needs: a multi-field update is atomic, and the claim is a real
// compare-and-set — no "write, wait, read back" any more.
const CF_NAME = /^[a-z0-9][a-z0-9_-]{0,62}$/;
const CF_KEY = /^CODEFLOW_[A-Z]{2,20}$/;
const CF_MAX_VALUE = 4000;

function cfClean(set) {
  const out = {};
  for (const [k, v] of Object.entries(set || {})) {
    if (!CF_KEY.test(k) || k === "CODEFLOW_REPO") throw new Error(`key ${k} is not state`);
    out[k] = String(v ?? "").replace(/[\r\n]+/g, " ").slice(0, CF_MAX_VALUE);
  }
  return out;
}

export class CodeflowState extends DurableObject {
  async fetch(request) {
    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean);       // repos[/<name>[/op]] | summary | export | import
    if (parts[0] === "export" && request.method === "GET") return cfJson({ entries: await storageExport(this.ctx) });
    if (parts[0] === "import" && request.method === "POST") return cfJson(await storageImport(this.ctx, await request.json()));
    if (parts[0] === "summary") {
      const all = [...(await this.ctx.storage.list({ prefix: "repo:" })).values()];
      const states = {};
      for (const r of all) states[r.CODEFLOW_STATE || "?"] = (states[r.CODEFLOW_STATE || "?"] || 0) + 1;
      return cfJson({ repos: all.length, states, text: `${all.length} repos` +
        (all.length ? " · " + Object.entries(states).map(([k, v]) => `${v} ${k}`).join(" · ") : "") });
    }
    if (parts[0] !== "repos") return cfJson({ error: "not found" }, 404);
    if (parts.length === 1 && request.method === "GET") {
      const all = await this.ctx.storage.list({ prefix: "repo:" });
      return cfJson(Object.fromEntries([...all].map(([k, v]) => [k.slice(5), v])));
    }
    const name = parts[1] || "";
    if (!CF_NAME.test(name) || request.method !== "POST") return cfJson({ error: "bad request" }, 400);
    let body = {};
    try { body = await request.json(); } catch { /* empty body */ }
    const key = "repo:" + name;
    const cur = (await this.ctx.storage.get(key)) || null;
    const op = parts[2] || "";
    try {
      if (op === "") {                                  // atomic update; "" removes a field
        const next = { ...(cur || {}) };
        for (const [k, v] of Object.entries(cfClean(body.set))) { if (v === "") delete next[k]; else next[k] = v; }
        await this.ctx.storage.put(key, next);
        return cfJson(next);
      }
      if (op === "seed") {                              // the one-time migration from the instance config
        if (cur) return cfJson({ seeded: false, state: cur });
        const next = Object.fromEntries(Object.entries(cfClean(body.set)).filter(([, v]) => v !== ""));
        await this.ctx.storage.put(key, next);
        return cfJson({ seeded: true, state: next });
      }
      if (op === "claim") {                             // compare-and-set with a time-to-live
        const tab = String(body.tab || "").slice(0, 40), ttl = Math.max(1000, +body.ttl || 600000);
        if (!tab) return cfJson({ error: "tab missing" }, 400);
        const [owner, ts] = String(cur?.CODEFLOW_LOCK || "").split("@");
        if (owner && owner !== tab && Date.now() - (+ts || 0) < ttl) return cfJson({ ok: false, holder: owner });
        await this.ctx.storage.put(key, { ...(cur || {}), CODEFLOW_LOCK: `${tab}@${Date.now()}` });
        return cfJson({ ok: true });
      }
      if (op === "release") {
        const tab = String(body.tab || "");
        if (cur && String(cur.CODEFLOW_LOCK || "").split("@")[0] === tab) {
          const next = { ...cur }; delete next.CODEFLOW_LOCK;
          await this.ctx.storage.put(key, next);
        }
        return cfJson({ ok: true });
      }
      if (op === "forget") {                            // the repo's instance is gone
        await this.ctx.storage.delete(key);
        return cfJson({ ok: true });
      }
    } catch (e) {
      return cfJson({ error: String(e.message || e) }, 400);
    }
    return cfJson({ error: "unknown operation" }, 404);
  }
}
