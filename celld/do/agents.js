// kAIm56 — agents as Durable Objects (prototype): one object per agent.
// A turn is accepted at once (POST turn -> id), runs in the background
// (alarm, with waitUntil as the kick) and is fetched by polling (GET turn/<id>)
// — no connection has to stay open. Tools: web search (DuckDuckGo HTML, no
// key), fetch a page, the agent's own memory. The LLM goes through the
// manager's key proxy on celld (env.LLM_URL), no key in here.
// One module per app class; worker.js (celld) imports it, the manager
// assembles it with do/storage.js for Cloudflare — so: no other imports,
// helpers prefixed per module.
// SPDX-License-Identifier: AGPL-3.0-or-later
import { DurableObject } from "cloudflare:workers";
import { storageExport, storageImport } from "./storage.js";

const agJson = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
const AG_NAME = /^[a-z0-9][a-z0-9_-]{0,31}$/;
const AG_MAX_STEPS = 8;
const AG_HISTORY = 40;            // messages kept (user + assistant)
const AG_FETCH_MAX = 8000;        // characters of a fetched page the model sees
const AG_DEFAULT_SYSTEM = "You are a helpful assistant. Answer in the user's language. Use the tools when you need current facts; say which pages you used.";

const AG_TOOLS = [
  { type: "function", function: { name: "web_search", description: "Search the web; returns titles, links and snippets.",
    parameters: { type: "object", properties: { query: { type: "string" } }, required: ["query"] } } },
  { type: "function", function: { name: "fetch_url", description: "Fetch a web page and return its text.",
    parameters: { type: "object", properties: { url: { type: "string" } }, required: ["url"] } } },
  { type: "function", function: { name: "memory_store", description: "Remember a fact permanently under a key.",
    parameters: { type: "object", properties: { key: { type: "string" }, value: { type: "string" } }, required: ["key", "value"] } } },
  { type: "function", function: { name: "memory_recall", description: "List everything remembered (key: value).",
    parameters: { type: "object", properties: {} } } },
];

function agText(html) {
  return html.replace(/<script[\s\S]*?<\/script>|<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#x27;|&#39;/g, "'").replace(/\s+/g, " ").trim();
}

// DuckDuckGo's HTML endpoint: result links come as /l/?uddg=<url>
function agParseDDG(html) {
  const out = [];
  const re = /<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)<\/a>[\s\S]*?class="result__snippet"[^>]*>([\s\S]*?)<\/a>/g;
  let m;
  while ((m = re.exec(html)) && out.length < 8) {
    let href = m[1].replace(/&amp;/g, "&");
    const u = href.match(/[?&]uddg=([^&]+)/);
    if (u) href = decodeURIComponent(u[1]);
    out.push({ title: agText(m[2]), url: href, snippet: agText(m[3]) });
  }
  return out;
}

export class AgentState extends DurableObject {
  async fetch(request) {
    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean);
    const name = request.headers.get("x-agent-name") || "";
    if (parts[0] === "export" && request.method === "GET") return agJson({ entries: await storageExport(this.ctx) });
    if (parts[0] === "import" && request.method === "POST") return agJson(await storageImport(this.ctx, await request.json()));
    if (name === "") return this.registry(request, parts);            // the "agents" object: the list
    const cfg = await this.ctx.storage.get("cfg");
    if (!cfg && !(parts[0] === "setup" && request.method === "POST")) return agJson({ error: `no agent '${name}'` }, 404);

    if (parts[0] === "setup" && request.method === "POST") {          // create / change model and system prompt
      const b = await request.json().catch(() => ({}));
      const next = { name, model: String(b.model || cfg?.model || this.env.DEFAULT_MODEL || "google/gemini-2.5-flash").slice(0, 120),
                     system: String(b.system ?? cfg?.system ?? AG_DEFAULT_SYSTEM).slice(0, 8000), created: cfg?.created || Date.now() };
      await this.ctx.storage.put("cfg", next);
      return agJson({ ok: true, agent: next });
    }
    if (parts.length === 0 && request.method === "GET") {
      const turns = await this.ctx.storage.list({ prefix: "t:", reverse: true, limit: 10 });
      return agJson({ agent: cfg, queue: (await this.ctx.storage.get("q")) || [],
        turns: [...turns.values()].map(t => ({ id: t.id, status: t.status, ts: t.ts, message: t.message.slice(0, 120) })) });
    }
    if (parts[0] === "turn" && parts.length === 1 && request.method === "POST") {
      const b = await request.json().catch(() => ({}));
      const message = String(b.message || "").trim();
      if (!message) return agJson({ error: "message missing" }, 400);
      const id = Date.now().toString(36) + crypto.randomUUID().slice(0, 6);
      const turn = { id, status: "queued", message: message.slice(0, 20000), text: "", steps: [], ts: Date.now() };
      const q = (await this.ctx.storage.get("q")) || [];
      q.push(id);
      await this.ctx.storage.put({ ["t:" + id]: turn, q });
      await this.kick();
      return agJson({ turn: id, status: "queued" }, 202);
    }
    if (parts[0] === "turn" && parts[1] && request.method === "GET") {
      // a poll also revives a queue nobody is working on (lost alarm)
      if (!this.running && ((await this.ctx.storage.get("q")) || []).length) this.ctx.waitUntil(this.run());
      const t = await this.ctx.storage.get("t:" + parts[1]);
      return t ? agJson(t) : agJson({ error: "unknown turn" }, 404);
    }
    if (parts[0] === "history" && request.method === "GET") return agJson((await this.ctx.storage.get("h")) || []);
    if (parts[0] === "memory" && request.method === "GET") {
      const m = await this.ctx.storage.list({ prefix: "m:" });
      return agJson(Object.fromEntries([...m].map(([k, v]) => [k.slice(2), v])));
    }
    if (parts[0] === "reset" && request.method === "POST") {
      await this.ctx.storage.delete("h");
      return agJson({ ok: true });
    }
    if (parts[0] === "delete" && request.method === "POST") {
      await this.ctx.storage.deleteAlarm().catch(() => {});
      await this.ctx.storage.deleteAll();
      return agJson({ ok: true });
    }
    return agJson({ error: "not found" }, 404);
  }

  // ---- the list of agents (one object named "agents") ----------------------
  async registry(request, parts) {
    if (parts[0] === "summary") {
      const all = await this.ctx.storage.list({ prefix: "a:" });
      return agJson({ agents: all.size, text: `${all.size} agent${all.size === 1 ? "" : "s"}` });
    }
    if (parts.length === 0 && request.method === "GET") {
      const all = await this.ctx.storage.list({ prefix: "a:" });
      return agJson([...all.values()]);
    }
    const b = await request.json().catch(() => ({}));
    if (parts[0] === "add" && request.method === "POST") {
      await this.ctx.storage.put("a:" + b.name, { name: b.name, model: b.model, created: Date.now() });
      return agJson({ ok: true });
    }
    if (parts[0] === "remove" && request.method === "POST") {
      await this.ctx.storage.delete("a:" + b.name);
      return agJson({ ok: true });
    }
    return agJson({ error: "not found" }, 404);
  }

  // ---- running turns --------------------------------------------------------
  async kick() {
    // The alarm is the durable path (survives a restart); waitUntil starts at
    // once where alarms are slow or missing. run() is idempotent per turn.
    try { await this.ctx.storage.setAlarm(Date.now()); } catch {}
    this.ctx.waitUntil(this.run());
  }

  async alarm() { await this.run(); }

  async run() {
    if (this.running) return;
    this.running = true;
    try {
      for (;;) {
        const q = (await this.ctx.storage.get("q")) || [];
        if (!q.length) {
          try { await this.ctx.storage.deleteAlarm(); } catch {}
          return;
        }
        // watchdog: if this process dies mid-turn (restart, deploy), the alarm
        // resumes the queue — a turn left "running" is simply run again
        try { await this.ctx.storage.setAlarm(Date.now() + 90_000); } catch {}
        const id = q[0];
        const t = await this.ctx.storage.get("t:" + id);
        if (t && (t.status === "queued" || t.status === "running")) await this.turn(t);
        const rest = ((await this.ctx.storage.get("q")) || []).filter(x => x !== id);
        await this.ctx.storage.put("q", rest);
      }
    } finally {
      this.running = false;
    }
  }

  async save(t) { await this.ctx.storage.put("t:" + t.id, t); }

  async turn(t) {
    const cfg = await this.ctx.storage.get("cfg");
    t.status = "running"; t.started = Date.now();
    await this.save(t);
    const history = (await this.ctx.storage.get("h")) || [];
    const msgs = [{ role: "system", content: `${cfg.system}\n\nNow: ${new Date().toISOString()}. You are the agent "${cfg.name}".` },
                  ...history, { role: "user", content: t.message }];
    try {
      for (let step = 0; step < AG_MAX_STEPS; step++) {
        const r = await fetch(this.env.LLM_URL, { method: "POST", headers: { "Content-Type": "application/json", "X-Kaim-Turn": t.id.slice(0, 16) },
          body: JSON.stringify({ model: cfg.model, messages: msgs, tools: AG_TOOLS }) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok || !d.choices) throw new Error(`LLM HTTP ${r.status}: ${JSON.stringify(d.error || d).slice(0, 300)}`);
        const m = d.choices[0].message;
        msgs.push({ role: "assistant", content: m.content || "", ...(m.tool_calls ? { tool_calls: m.tool_calls } : {}) });
        if (!m.tool_calls || !m.tool_calls.length) {
          t.text = m.content || ""; t.status = "done"; t.ended = Date.now();
          break;
        }
        for (const c of m.tool_calls) {
          let args = {};
          try { args = JSON.parse(c.function.arguments || "{}"); } catch {}
          t.steps.push(`${c.function.name} ${JSON.stringify(args).slice(0, 120)}`);
          await this.save(t);                                   // progress is visible while polling
          msgs.push({ role: "tool", tool_call_id: c.id, content: (await this.tool(c.function.name, args)).slice(0, AG_FETCH_MAX) });
        }
      }
      if (t.status !== "done") { t.status = "done"; t.text = t.text || "(max steps reached)"; t.ended = Date.now(); }
      const h = [...history, { role: "user", content: t.message }, { role: "assistant", content: t.text }].slice(-AG_HISTORY);
      await this.ctx.storage.put("h", h);
    } catch (e) {
      t.status = "error"; t.error = String(e.message || e); t.ended = Date.now();
    }
    await this.save(t);
  }

  async tool(name, a) {
    try {
      if (name === "web_search") {
        const r = await fetch("https://html.duckduckgo.com/html/?q=" + encodeURIComponent(a.query || ""),
          { headers: { "User-Agent": "Mozilla/5.0 (kAIm56 agent)" } });
        const res = agParseDDG(await r.text());
        return res.length ? res.map(x => `${x.title}\n${x.url}\n${x.snippet}`).join("\n\n") : "no results";
      }
      if (name === "fetch_url") {
        if (!/^https?:\/\//.test(a.url || "")) return "error: http(s) URL expected";
        const r = await fetch(a.url, { headers: { "User-Agent": "Mozilla/5.0 (kAIm56 agent)" }, redirect: "follow" });
        const ct = r.headers.get("content-type") || "";
        const body = await r.text();
        return `HTTP ${r.status}\n` + (ct.includes("html") ? agText(body) : body);
      }
      if (name === "memory_store") {
        await this.ctx.storage.put("m:" + String(a.key).slice(0, 100), String(a.value).slice(0, 4000));
        return "stored";
      }
      if (name === "memory_recall") {
        const m = await this.ctx.storage.list({ prefix: "m:" });
        return m.size ? [...m].map(([k, v]) => `${k.slice(2)}: ${v}`).join("\n") : "nothing stored yet";
      }
      return `error: unknown tool ${name}`;
    } catch (e) {
      return `error: ${e.message || e}`;
    }
  }
}
