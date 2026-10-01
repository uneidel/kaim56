// kAIm56 — the apps' Worker on celld.
//
// Files: the static apps (public/apps/<name>/); a path ending in "/" gets its
// index.html. celld 0.6 dev does not resolve a directory index below the root
// itself (/t/ -> 404 while /t/index.html redirects there), so html_handling
// is "none" and the mapping happens here.
//
// State: /apps/<name>/_api/... goes to that app's Durable Object. The manager
// forwards only after its login and path checks; celld itself is loopback-only.
// SPDX-License-Identifier: AGPL-3.0-or-later
import { DurableObject } from "cloudflare:workers";

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const m = url.pathname.match(/^\/apps\/([a-z0-9][a-z0-9_-]{0,40})\/_api(\/.*)?$/);
    if (m) {
      const ns = { codeflow: env.CODEFLOW, corewar: env.COREWAR }[m[1]];
      if (!ns) return json({ error: "this app has no server-side state" }, 404);
      const stub = ns.get(ns.idFromName(m[1]));          // one object per app
      return stub.fetch(new Request(new URL(m[2] || "/", url), request));
    }
    if (url.pathname.endsWith("/")) url.pathname += "index.html";
    return env.ASSETS.fetch(new Request(url, request));
  },
};

// ---- Code flow: the per-repo state machine's state ------------------------
// One object for the app; one storage entry per repo instance. Every request
// is handled alone (a Durable Object is single-threaded), which is what the
// state machine needs: a multi-field update is atomic, and the claim is a real
// compare-and-set — no "write, wait, read back" any more.
const NAME = /^[a-z0-9][a-z0-9_-]{0,62}$/;
const KEY = /^CODEFLOW_[A-Z]{2,20}$/;
const MAX_VALUE = 4000;

function clean(set) {
  const out = {};
  for (const [k, v] of Object.entries(set || {})) {
    if (!KEY.test(k) || k === "CODEFLOW_REPO") throw new Error(`key ${k} is not state`);
    out[k] = String(v ?? "").replace(/[\r\n]+/g, " ").slice(0, MAX_VALUE);
  }
  return out;
}

export class CodeflowState extends DurableObject {
  async fetch(request) {
    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean);       // repos[/<name>[/op]]
    if (parts[0] !== "repos") return json({ error: "not found" }, 404);
    if (parts.length === 1 && request.method === "GET") {
      const all = await this.ctx.storage.list({ prefix: "repo:" });
      return json(Object.fromEntries([...all].map(([k, v]) => [k.slice(5), v])));
    }
    const name = parts[1] || "";
    if (!NAME.test(name) || request.method !== "POST") return json({ error: "bad request" }, 400);
    let body = {};
    try { body = await request.json(); } catch { /* empty body */ }
    const key = "repo:" + name;
    const cur = (await this.ctx.storage.get(key)) || null;
    const op = parts[2] || "";
    try {
      if (op === "") {                                  // atomic update; "" removes a field
        const next = { ...(cur || {}) };
        for (const [k, v] of Object.entries(clean(body.set))) { if (v === "") delete next[k]; else next[k] = v; }
        await this.ctx.storage.put(key, next);
        return json(next);
      }
      if (op === "seed") {                              // the one-time migration from the instance config
        if (cur) return json({ seeded: false, state: cur });
        const next = Object.fromEntries(Object.entries(clean(body.set)).filter(([, v]) => v !== ""));
        await this.ctx.storage.put(key, next);
        return json({ seeded: true, state: next });
      }
      if (op === "claim") {                             // compare-and-set with a time-to-live
        const tab = String(body.tab || "").slice(0, 40), ttl = Math.max(1000, +body.ttl || 600000);
        if (!tab) return json({ error: "tab missing" }, 400);
        const [owner, ts] = String(cur?.CODEFLOW_LOCK || "").split("@");
        if (owner && owner !== tab && Date.now() - (+ts || 0) < ttl) return json({ ok: false, holder: owner });
        await this.ctx.storage.put(key, { ...(cur || {}), CODEFLOW_LOCK: `${tab}@${Date.now()}` });
        return json({ ok: true });
      }
      if (op === "release") {
        const tab = String(body.tab || "");
        if (cur && String(cur.CODEFLOW_LOCK || "").split("@")[0] === tab) {
          const next = { ...cur }; delete next.CODEFLOW_LOCK;
          await this.ctx.storage.put(key, next);
        }
        return json({ ok: true });
      }
      if (op === "forget") {                            // the repo's instance is gone
        await this.ctx.storage.delete(key);
        return json({ ok: true });
      }
    } catch (e) {
      return json({ error: String(e.message || e) }, 400);
    }
    return json({ error: "unknown operation" }, 404);
  }
}

// ---- Core War: the Changes tab (every adaptation with reason and diff) -----
// Shared by every browser instead of living in one browser's localStorage.
// Keys sort by time, newest are listed first; the oldest go beyond MAX_CHANGES.
const MAX_CHANGES = 1000;
const MAX_ENTRY = 32 * 1024;

export class CorewarLog extends DurableObject {
  async fetch(request) {
    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean);       // changes[/clear]
    if (parts[0] !== "changes") return json({ error: "not found" }, 404);
    if (parts.length === 1 && request.method === "GET") {
      const limit = Math.min(MAX_CHANGES, Math.max(1, +url.searchParams.get("limit") || 300));
      const rows = await this.ctx.storage.list({ prefix: "c:", reverse: true, limit });
      return json([...rows.values()]);
    }
    if (request.method !== "POST") return json({ error: "bad request" }, 400);
    if (parts[1] === "clear") {
      await this.ctx.storage.deleteAll();
      return json({ ok: true });
    }
    let entries;
    try { entries = await request.json(); } catch { return json({ error: "JSON body expected" }, 400); }
    entries = Array.isArray(entries) ? entries : [entries];
    const put = {};
    for (const e of entries.slice(0, 500)) {
      const raw = JSON.stringify(e || {});
      if (raw.length > MAX_ENTRY || typeof e !== "object") return json({ error: "entry too large or not an object" }, 400);
      const ts = Math.max(0, Math.min(+e.ts || Date.now(), Date.now() + 60000));
      put[`c:${String(ts).padStart(15, "0")}:${crypto.randomUUID().slice(0, 8)}`] = { ...e, ts };
    }
    await this.ctx.storage.put(put);
    const extra = await this.ctx.storage.list({ prefix: "c:", limit: 500 });   // oldest first
    const total = (await this.ctx.storage.list({ prefix: "c:" })).size;
    if (total > MAX_CHANGES) await this.ctx.storage.delete([...extra.keys()].slice(0, total - MAX_CHANGES));
    return json({ ok: true, stored: Object.keys(put).length });
  }
}
