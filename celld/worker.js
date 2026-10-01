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
      if (m[1] !== "codeflow") return json({ error: "this app has no server-side state" }, 404);
      const stub = env.CODEFLOW.get(env.CODEFLOW.idFromName("codeflow"));
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
