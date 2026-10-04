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
export { CodeflowState } from "./do/codeflow.js";
export { CorewarLog } from "./do/corewar.js";
export { AgentState } from "./do/agents.js";

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

// The apps with a server side, and their Durable Object namespace.
const SERVER_SIDE = env => ({ codeflow: env.CODEFLOW, corewar: env.COREWAR, agents: env.AGENTS });

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/_status") {                 // for the manager's Apps panel: one summary per app
      const apps = {};
      for (const [name, ns] of Object.entries(SERVER_SIDE(env))) {
        try {
          const r = await ns.get(ns.idFromName(name)).fetch(new Request(new URL("/summary", url)));
          apps[name] = await r.json();
        } catch (e) { apps[name] = { error: String(e.message || e) }; }
      }
      return json({ ok: true, apps });
    }
    const m = url.pathname.match(/^\/apps\/([a-z0-9][a-z0-9_-]{0,40})\/_api(\/.*)?$/);
    if (m && m[1] === "agents") return agentsRoute(env, url, request, m[2] || "/");
    if (m) {
      const ns = SERVER_SIDE(env)[m[1]];
      if (!ns) return json({ error: "this app has no server-side state" }, 404);
      const stub = ns.get(ns.idFromName(m[1]));          // one object per app
      return stub.fetch(new Request(new URL(m[2] || "/", url), request));
    }
    if (url.pathname.endsWith("/")) url.pathname += "index.html";
    return env.ASSETS.fetch(new Request(url, request));
  },
};

// /apps/agents/_api/...: one Durable Object per agent ("agent:<name>"), the
// list in the object "agents". create/delete keep both in step.
const AGENT_NAME = /^[a-z0-9][a-z0-9_-]{0,31}$/;
async function agentsRoute(env, url, request, path) {
  const ns = env.AGENTS;
  const list = ns.get(ns.idFromName("agents"));
  const call = (stub, p, init = {}, name = "") =>
    stub.fetch(new Request(new URL(p, url), { ...init, headers: { "Content-Type": "application/json", "x-agent-name": name } }));
  const parts = path.split("/").filter(Boolean);
  if (parts.length === 0 || parts[0] === "summary") return call(list, "/" + parts.join("/"), { method: request.method });
  if (parts[0] === "create" && request.method === "POST") {
    const b = await request.json().catch(() => ({}));
    if (!AGENT_NAME.test(b.name || "")) return json({ error: "name: a-z, 0-9, - and _, at most 32" }, 400);
    const r = await call(ns.get(ns.idFromName("agent:" + b.name)), "/setup", { method: "POST", body: JSON.stringify(b) }, b.name);
    const d = await r.json();
    if (r.ok) await call(list, "/add", { method: "POST", body: JSON.stringify({ name: b.name, model: d.agent.model }) });
    return json(d, r.status);
  }
  const name = parts[0];
  if (!AGENT_NAME.test(name)) return json({ error: "bad agent name" }, 400);
  const stub = ns.get(ns.idFromName("agent:" + name));
  const init = { method: request.method };
  if (request.method === "POST") init.body = await request.text();
  const r = await call(stub, "/" + parts.slice(1).join("/"), init, name);
  if (parts[1] === "delete" && r.ok) await call(list, "/remove", { method: "POST", body: JSON.stringify({ name }) });
  return r;
}
