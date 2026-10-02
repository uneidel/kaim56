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

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

// The apps with a server side, and their Durable Object namespace.
const SERVER_SIDE = env => ({ codeflow: env.CODEFLOW, corewar: env.COREWAR });

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
