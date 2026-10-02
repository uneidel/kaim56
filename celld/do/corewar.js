// kAIm56 — Core War's Durable Object: the Changes tab
// One module per app class; worker.js (celld) imports it, and the manager
// assembles it with do/storage.js into a single worker for Cloudflare
// (mgr/cloud.py) — so: no other imports, and helpers prefixed per module.
// SPDX-License-Identifier: AGPL-3.0-or-later
import { DurableObject } from "cloudflare:workers";
import { storageExport, storageImport } from "./storage.js";

const cwJson = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

// ---- Core War: the Changes tab (every adaptation with reason and diff) -----
// Shared by every browser instead of living in one browser's localStorage.
// Keys sort by time, newest are listed first; the oldest go beyond CW_MAX_CHANGES.
const CW_MAX_CHANGES = 1000;
const CW_MAX_ENTRY = 32 * 1024;

export class CorewarLog extends DurableObject {
  async fetch(request) {
    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean);       // changes[/clear] | summary | export | import
    if (parts[0] === "export" && request.method === "GET") return cwJson({ entries: await storageExport(this.ctx) });
    if (parts[0] === "import" && request.method === "POST") return cwJson(await storageImport(this.ctx, await request.json()));
    if (parts[0] === "summary") {
      const all = await this.ctx.storage.list({ prefix: "c:" });
      const last = [...all.values()].pop();
      return cwJson({ changes: all.size, last: last?.ts || 0,
        text: `${all.size} change${all.size === 1 ? "" : "s"}` + (last ? ` · last ${new Date(last.ts).toISOString().slice(0, 16).replace("T", " ")} UTC` : "") });
    }
    if (parts[0] !== "changes") return cwJson({ error: "not found" }, 404);
    if (parts.length === 1 && request.method === "GET") {
      const limit = Math.min(CW_MAX_CHANGES, Math.max(1, +url.searchParams.get("limit") || 300));
      const rows = await this.ctx.storage.list({ prefix: "c:", reverse: true, limit });
      return cwJson([...rows.values()]);
    }
    if (request.method !== "POST") return cwJson({ error: "bad request" }, 400);
    if (parts[1] === "clear") {
      await this.ctx.storage.deleteAll();
      return cwJson({ ok: true });
    }
    let entries;
    try { entries = await request.json(); } catch { return cwJson({ error: "JSON body expected" }, 400); }
    entries = Array.isArray(entries) ? entries : [entries];
    const put = {};
    for (const e of entries.slice(0, 500)) {
      const raw = JSON.stringify(e || {});
      if (raw.length > CW_MAX_ENTRY || typeof e !== "object") return cwJson({ error: "entry too large or not an object" }, 400);
      const ts = Math.max(0, Math.min(+e.ts || Date.now(), Date.now() + 60000));
      put[`c:${String(ts).padStart(15, "0")}:${crypto.randomUUID().slice(0, 8)}`] = { ...e, ts };
    }
    await this.ctx.storage.put(put);
    const extra = await this.ctx.storage.list({ prefix: "c:", limit: 500 });   // oldest first
    const total = (await this.ctx.storage.list({ prefix: "c:" })).size;
    if (total > CW_MAX_CHANGES) await this.ctx.storage.delete([...extra.keys()].slice(0, total - CW_MAX_CHANGES));
    return cwJson({ ok: true, stored: Object.keys(put).length });
  }
}
