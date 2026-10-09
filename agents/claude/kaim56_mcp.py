#!/usr/bin/env python3
"""MCP server for the kAIm56 platform APIs — runs in the Claude guest.

Claude Code has a native tool mechanism (MCP over stdio); curl recipes in the
CLAUDE.md were the stopgap. This server turns the manager APIs into real,
typed tools: memory_store, memory_recall, web_search, list_skills, load_skill,
notify, and the squad tools (squad_post/ask/read/members: the project channel; squad_propose/request_member/request_merge and ticket_*: plans the user approves, tickets). The manager is the host gateway (.1 of the /30) on :8700 — guests only
ever read/write their own memory, the manager decides by source IP.

It also relays the instance's catalog MCPs (MCP_SERVERS): names come from
/api/mcp-config, calls go through the manager's MCP hub (/api/mcp), exposed as
"<server>__<tool>". Secrets and LAN access stay on the host.

Stdlib only, newline-delimited JSON-RPC as the MCP stdio transport demands.
"""
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def manager_base():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    finally:
        s.close()
    return f"http://{ip.rsplit('.', 1)[0]}.1:8700"


BASE = manager_base()


def _get(path):
    return urllib.request.urlopen(BASE + path, timeout=30).read().decode("utf-8", "replace")


def _post(path, payload, timeout=30):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")


def t_memory_store(key, value):
    return _post("/api/memory/self", {"key": key, "value": value})


def t_memory_recall(key=None):
    tail = f"/{urllib.parse.quote(str(key), safe='')}" if key else ""
    return _get("/api/memory/self" + tail)


def t_web_search(query, count=5):
    q = urllib.parse.urlencode({"q": query, "count": count})
    return json.loads(_get("/api/websearch?" + q)).get("result", "")


def t_list_skills():
    arr = json.loads(_get("/api/skills?meta=1"))
    return "\n".join(f"- {s.get('name')}: {s.get('description', '')}" for s in arr)


def t_load_skill(name):
    return _get(f"/api/skills/{urllib.parse.quote(str(name), safe='')}")


def t_notify(title, message=""):
    return _post("/api/notify", {"title": title, "message": message})


def _squad(fn):
    """A refused squad call says why (the manager's error text), not just 'HTTP 403'."""
    def run(**kw):
        try:
            return fn(**kw)
        except urllib.error.HTTPError as e:
            try:
                return "Error: " + json.loads(e.read().decode("utf-8", "replace")).get("error", f"HTTP {e.code}")
            except ValueError:
                return f"Error: HTTP {e.code}"
    return run


def _q(**kw):
    return urllib.parse.urlencode({k: v for k, v in kw.items() if v})


def t_squad_post(text, to="", reply_to="", project=""):
    d = json.loads(_post("/api/squad/post", {"to": to, "text": text, "reply_to": reply_to or None, "project": project}))
    st = ("delivered to " + ", ".join(d["delivered"])) if d.get("delivered") else \
        ("not delivered: " + d["blocked"] if d.get("blocked") else "stored")
    return f"Posted #{d['id']} in {d['project']} ({st})."


def t_squad_ask(to, question, wait=True, timeout=600, project=""):
    d = json.loads(_post("/api/squad/ask", {"to": to, "question": question, "project": project}))
    if d.get("blocked"):
        return f"Question #{d['id']} not delivered: {d['blocked']}"
    if not wait:
        return f"Asked #{d['id']}; the answer appears in squad_read as a reply to #{d['id']}."
    end = time.time() + max(10, min(int(timeout or 600), 1800))
    while time.time() < end:
        time.sleep(5)
        a = json.loads(_get(f"/api/squad/answer/{d['id']}"))
        if a.get("status") == "answered":
            return f"{a.get('from', to)} answered: {a.get('answer', '')}"
        if a.get("status") == "expired":
            return f"No answer to #{d['id']}: {a.get('reason', 'expired')}"
    return f"No answer to #{d['id']} yet; check later with squad_read."


def t_squad_read(since="", project=""):
    d = json.loads(_get("/api/squad/read?" + _q(since=since, project=project)))
    return "\n".join(f"#{m['id']} {m['from']} -> {', '.join(m['to'])}"
                     f"{' (re ' + str(m['reply_to']) + ')' if m.get('reply_to') else ''} [{m['kind']}]: {m['text']}"
                     for m in d.get("messages") or []) or f"No new messages in {d.get('project')}."


def t_squad_members(project=""):
    d = json.loads(_get("/api/squad/members?" + _q(project=project)))
    return f"Project {d['project']}: " + ", ".join(f"{m['name']} ({m['role']})" for m in d.get("members") or [])


def _proposed(path, payload):
    d = json.loads(_post(path, payload))
    w = d.get("warnings") or []
    return (f"Proposal {d['id']} ({d['kind']}) in {d['project']} is waiting for the user's approval — nothing "
            "happens before; a squad message brings the decision." + (" Flags: " + "; ".join(w) if w else ""))


def t_squad_propose(goal, members, tickets=None, budget_tokens_per_day=0, project=""):
    return _proposed("/api/squad/propose", {"goal": goal, "members": members, "tickets": tickets or [],
                                            "budget_tokens_per_day": budget_tokens_per_day or None, "project": project})


def t_squad_request_member(member, reason="", project=""):
    return _proposed("/api/squad/request-member", {"member": member, "reason": reason, "project": project})


def t_squad_request_merge(writer, summary, project=""):
    return _proposed("/api/squad/request-merge", {"writer": writer, "summary": summary, "project": project})


def _tline(t):
    deps = f" after {','.join(t['depends_on'])}" if t.get("depends_on") else ""
    return f"#{t['id']} {t['key']} [{t['status']}] {t['title']} -> {t.get('assignee') or 'unassigned'}{deps}"


def t_ticket_list(project=""):
    d = json.loads(_get("/api/squad/tickets?" + _q(project=project)))
    head = f"Project {d['project']}" + ("" if d.get("approved") else " (squad plan not approved yet)")
    return head + ":\n" + ("\n".join(_tline(t) for t in d.get("tickets") or []) or "no tickets")


def _ticket(path, payload):
    d = json.loads(_post(path, payload))
    woke = d.get("woke") or ({d["key"]: d["wake"]} if d.get("wake") else {})
    return _tline(d) + "".join(f"\n{k}: {v}" for k, v in woke.items())


def t_ticket_update(id, status="", comment="", project=""):
    return _ticket("/api/squad/ticket-update", {"id": id, "status": status, "comment": comment, "project": project})


def t_ticket_create(title, description="", assignee="", depends_on=None, key="", project=""):
    return _ticket("/api/squad/ticket-create", {"title": title, "description": description, "assignee": assignee,
                                                "depends_on": depends_on or [], "key": key, "project": project})


def t_ticket_assign(id, assignee, project=""):
    return _ticket("/api/squad/ticket-assign", {"id": id, "assignee": assignee, "project": project})


_PROJ ={"type": "string", "description": "optional when you are in one squad"}
TOOLS = {
    "memory_store": (t_memory_store,
        "Store a value in the platform's long-term memory (survives /reset and VM restarts).",
        {"type": "object",
         "properties": {"key": {"type": "string", "description": "short key, reused to update"},
                        "value": {"type": "string", "description": "complete, self-contained statement"}},
         "required": ["key", "value"]}),
    "memory_recall": (t_memory_recall,
        "Read from long-term memory. Without a key: all entries of this instance.",
        {"type": "object",
         "properties": {"key": {"type": "string", "description": "optional: one key"}},
         "required": []}),
    "web_search": (t_web_search,
        "Web search (Brave Search API via the manager; DDG/Bing fallback). Title + URL + snippet.",
        {"type": "object",
         "properties": {"query": {"type": "string"},
                        "count": {"type": "integer", "description": "1-10, default 5"}},
         "required": ["query"]}),
    "list_skills": (t_list_skills,
        "List the platform's expert skills (name + description).",
        {"type": "object", "properties": {}, "required": []}),
    "load_skill": (t_load_skill,
        "Load one expert skill document into context.",
        {"type": "object",
         "properties": {"name": {"type": "string", "description": "name from list_skills"}},
         "required": ["name"]}),
    "squad_post": (_squad(t_squad_post),
        "Post to your project's squad channel. Members you address (to, or @name / @all in the text) are "
        "woken to act on it. Reply with reply_to=<message id>. Address only those who need to act.",
        {"type": "object",
         "properties": {"text": {"type": "string"},
                        "to": {"type": "string", "description": "member name(s), comma-separated, or 'all' (empty with reply_to = its sender)"},
                        "reply_to": {"type": "string", "description": "optional: id of the message you answer"},
                        "project": _PROJ},
         "required": ["text"]}),
    "squad_ask": (_squad(t_squad_ask),
        "Ask ONE squad member; it is woken and answers with squad_post reply_to. wait=true blocks until the answer.",
        {"type": "object",
         "properties": {"to": {"type": "string"}, "question": {"type": "string"},
                        "wait": {"type": "boolean", "description": "default true"},
                        "timeout": {"type": "integer", "description": "seconds, default 600"},
                        "project": _PROJ},
         "required": ["to", "question"]}),
    "squad_read": (_squad(t_squad_read),
        "Read your squad channel: messages to you or to all, and your own (oldest first).",
        {"type": "object",
         "properties": {"since": {"type": "string", "description": "optional: only messages after this id"},
                        "project": _PROJ},
         "required": []}),
    "squad_members": (_squad(t_squad_members),
        "The members of your squad (project) and their roles.",
        {"type": "object", "properties": {"project": _PROJ}, "required": []}),
    "squad_propose": (_squad(t_squad_propose),
        "Lead only: propose the squad plan to the user — goal, members (new or existing instances) and tickets. "
        "NOTHING is created or started until the user approves it in the UI; the decision arrives as a squad message.",
        {"type": "object",
         "properties": {"goal": {"type": "string"},
                        "members": {"type": "array", "items": {"type": "object", "properties": {
                            "name": {"type": "string", "description": "a-z 0-9 -"}, "purpose": {"type": "string"},
                            "role": {"type": "string", "enum": ["writer", "reader", "lead"]},
                            "existing": {"type": "boolean"}, "persona": {"type": "string"},
                            "skills": {"type": "array", "items": {"type": "string"}},
                            "model_tier": {"type": "string", "enum": ["cheap", "strong", "code", "local"]},
                            "model": {"type": "string"}, "template": {"type": "string"}}}},
                        "tickets": {"type": "array", "items": {"type": "object", "properties": {
                            "key": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"},
                            "assignee": {"type": "string"}, "depends_on": {"type": "array", "items": {"type": "string"}}}}},
                        "budget_tokens_per_day": {"type": "integer"}, "project": _PROJ},
         "required": ["goal", "members"]}),
    "squad_request_member": (_squad(t_squad_request_member),
        "Lead only, after approval: ask the user for one more member (fields as in squad_propose).",
        {"type": "object", "properties": {"member": {"type": "object"}, "reason": {"type": "string"}, "project": _PROJ},
         "required": ["member"]}),
    "squad_request_merge": (_squad(t_squad_request_merge),
        "Lead only: ask the user to merge a writer's branch; the user reviews the diff and approves.",
        {"type": "object", "properties": {"writer": {"type": "string"}, "summary": {"type": "string"}, "project": _PROJ},
         "required": ["writer", "summary"]}),
    "ticket_list": (_squad(t_ticket_list),
        "The squad's tickets (id, key, status, assignee, dependencies).",
        {"type": "object", "properties": {"project": _PROJ}, "required": []}),
    "ticket_update": (_squad(t_ticket_update),
        "Your ticket (any, as the lead): status open/doing/review/done/blocked and/or a comment. "
        "Done wakes the members whose tickets waited on it.",
        {"type": "object", "properties": {"id": {"type": "string"}, "status": {"type": "string"},
                                          "comment": {"type": "string"}, "project": _PROJ},
         "required": ["id"]}),
    "ticket_create": (_squad(t_ticket_create),
        "Lead only, after approval: a new ticket; an assignee whose dependencies are done is woken.",
        {"type": "object", "properties": {"title": {"type": "string"}, "description": {"type": "string"},
                                          "assignee": {"type": "string"}, "key": {"type": "string"},
                                          "depends_on": {"type": "array", "items": {"type": "string"}},
                                          "project": _PROJ},
         "required": ["title"]}),
    "ticket_assign": (_squad(t_ticket_assign),
        "Lead only, after approval: assign a ticket; wakes the assignee when its dependencies are done.",
        {"type": "object", "properties": {"id": {"type": "string"}, "assignee": {"type": "string"}, "project": _PROJ},
         "required": ["id", "assignee"]}),
    "notify": (t_notify,
        "Push a notification to the user's devices (app + web bell).",
        {"type": "object",
         "properties": {"title": {"type": "string"}, "message": {"type": "string"}},
         "required": ["title"]}),
}


# --- catalog MCPs via the manager's hub -------------------------------------
_hub_tools = None   # exposed name -> (server, tool, description, schema)


def _hub(server, method, params, rid=1):
    out = json.loads(_post("/api/mcp", {"server": server, "payload": {
        "jsonrpc": "2.0", "id": rid, "method": method, "params": params}}, timeout=120) or "{}")
    if out.get("error"):
        raise RuntimeError(str(out["error"])[:300])
    return out.get("result", {})


def hub_tools():
    """Tools of the MCPs assigned to this instance; fetched once, a failing
    server is skipped (logged to stderr) so the platform tools keep working."""
    global _hub_tools
    if _hub_tools is not None:
        return _hub_tools
    found = {}
    try:
        cfg = json.loads(_get("/api/mcp-config"))
    except Exception as e:
        print(f"kaim56_mcp: mcp-config unavailable: {e!r}", file=sys.stderr)
        return found                      # not cached: retried on the next tools/list
    if cfg.get("unresolved"):
        print("kaim56_mcp: secrets not released: " + ", ".join(cfg["unresolved"]), file=sys.stderr)
    for server in (cfg.get("mcpServers") or {}):
        try:
            _hub(server, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                        "clientInfo": {"name": "kaim56-claude", "version": "1"}})
            for t in _hub(server, "tools/list", {}).get("tools", []):
                found[f"{server}__{t['name']}"[:64]] = (
                    server, t["name"], t.get("description") or t["name"],
                    t.get("inputSchema") or {"type": "object", "properties": {}})
        except Exception as e:
            print(f"kaim56_mcp: MCP '{server}' unavailable: {e!r}", file=sys.stderr)
    _hub_tools = found
    return found


def call_hub(name, args):
    server, tool, _d, _s = hub_tools()[name]
    return _hub(server, "tools/call", {"name": tool, "arguments": args})


def reply(mid, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": mid}
    if error is not None:
        out["error"] = {"code": -32000, "message": str(error)}
    else:
        out["result"] = result
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method", "")
        mid = msg.get("id")
        if method == "initialize":
            reply(mid, {"protocolVersion": msg.get("params", {}).get(
                            "protocolVersion", "2024-11-05"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "kaim56", "version": "1.0"}})
        elif method == "tools/list":
            reply(mid, {"tools": [
                {"name": n, "description": d, "inputSchema": schema}
                for n, (_f, d, schema) in TOOLS.items()] + [
                {"name": n, "description": d, "inputSchema": schema}
                for n, (_srv, _t, d, schema) in hub_tools().items()]})
        elif method == "tools/call":
            p = msg.get("params", {})
            name = p.get("name", "")
            args = p.get("arguments") or {}
            if name not in TOOLS and name not in hub_tools():
                reply(mid, error=f"unknown tool: {name}")
                continue
            try:
                if name not in TOOLS:
                    reply(mid, call_hub(name, args))   # hub result is already MCP content
                    continue
                out = TOOLS[name][0](**args)
                reply(mid, {"content": [{"type": "text", "text": str(out)}]})
            except Exception as e:
                reply(mid, {"content": [{"type": "text", "text": f"Error: {e!r}"}],
                            "isError": True})
        elif mid is not None:
            reply(mid, {})           # answer unknown requests with an empty result
        # Notifications (no id) are ignored.


if __name__ == "__main__":
    main()
