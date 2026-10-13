#!/usr/bin/env python3
"""MCP server for the kAIm56 platform APIs — runs in the Claude guest.

Claude Code has a native tool mechanism (MCP over stdio); curl recipes in the
CLAUDE.md were the stopgap. This server turns the manager APIs into real,
typed tools: memory_store, memory_recall, web_search, list_skills, load_skill,
notify, ask_human, and the squad tools (squad_post/ask/read/members: the project channel; squad_catalog/squad_propose/request_member/request_merge/toolchain_propose and ticket_*: plans the user approves, tickets; toolchain_status: the shared toolchain; git_push_branch/git_fetch/pr_*/issue_*: the project's git remote, run on the host; project_sync: the base into your folder). The manager is the host gateway (.1 of the /30) on :8700 — guests only
ever read/write their own memory, the manager decides by source IP.

It also relays the instance's catalog MCPs (MCP_SERVERS): names come from
/api/mcp-config, calls go through the manager's MCP hub (/api/mcp), exposed as
"<server>__<tool>". Secrets and LAN access stay on the host.

Stdlib only, newline-delimited JSON-RPC as the MCP stdio transport demands.
"""
import json
import re
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


def t_list_skills(category=""):
    arr = json.loads(_get("/api/skills?meta=1" + (f"&category={urllib.parse.quote(category)}" if category else "")))
    if category == "security" and not arr:
        return ("Security skills are not enabled for this instance "
                "(operator: SECURITY_SKILLS=1, or the squad's 'Security skills').")
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


def t_squad_post(text, to="", reply_to="", project="", wake=True):
    d = json.loads(_post("/api/squad/post", {"to": to, "text": text, "reply_to": reply_to or None, "project": project, "wake": wake}))
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
    w, v = d.get("warnings") or [], d.get("version") or 1
    head = (f"Proposal {d['id']} is now v{v}, waiting for approval (it replaced v{v - 1}) — " if v > 1 else
            f"Proposal {d['id']} ({d['kind']}) in {d['project']} is waiting for the user's approval — ")
    return (head + "nothing "
            "happens before; a squad message brings the decision. Your work for now is done: end this turn with a short "
            "summary (no tickets, no further posts until the decision arrives)." + (" Flags: " + "; ".join(w) if w else ""))


def t_squad_catalog(project=""):
    d = json.loads(_get("/api/squad/catalog?" + _q(project=project)))
    out = [f"Squad catalog for {d.get('project', '')} — choose per member:"]
    out += [d["profile_text"]] if d.get("profile_text") else []
    out.append("Personas (persona=<name>):")
    out += [f"- {p['name']}: {p.get('description', '')}" + (f" [tools: {', '.join(p['tools'])}]" if p.get("tools") else "")
            for p in d.get("personas") or []]
    out.append("  none fits -> new_persona: {name, description, prompt, tools?}")
    out.append("Skills (skills=[...]; 'standard' ones every agent has already):")
    out += [f"- {s['name']}{' (standard)' if s.get('default') else ''}: {s.get('description', '')}"
            for s in d.get("skills") or []]
    out.append("Model tiers (model_tier=):")
    out += [f"- {t['tier']}: {', '.join(t['models']) or 'no model assigned'}" for t in d.get("tiers") or []]
    out.append("Templates (template=):")
    out += [f"- {t['template']}: {t.get('note', '')}" for t in d.get("templates") or []]
    return "\n".join(out)


def t_squad_propose(goal, members, tickets=None, budget_tokens_per_day=0, project=""):
    return _proposed("/api/squad/propose", {"goal": goal, "members": members, "tickets": tickets or [],
                                            "budget_tokens_per_day": budget_tokens_per_day or None, "project": project})


def t_squad_request_member(member, reason="", project=""):
    return _proposed("/api/squad/request-member", {"member": member, "reason": reason, "project": project})


def t_squad_request_merge(writer, summary, project=""):
    return _proposed("/api/squad/request-merge", {"writer": writer, "summary": summary, "project": project})


def t_toolchain_propose(apt=None, script="", env=None, reason="", project=""):
    return _proposed("/api/squad/toolchain-propose", {"apt": apt or [], "script": script or "", "env": env or {},
                                                     "reason": reason, "project": project})


def toolchain_text(d):
    """The squad's shared toolchain as a member reads it (shared wording with the Claude guest)."""
    sp = d.get("spec") or {}
    if not (sp.get("apt") or sp.get("script") or sp.get("env")):
        return (f"Squad {d.get('project', '')}: no shared toolchain yet "
                "(the lead proposes one with toolchain_propose; until then install what you need with sudo).")
    out = [f"Squad {d.get('project', '')} toolchain v{d.get('version', 0)}: your VM "
           + ("HAS it (read-only layer under your disk)." if d.get("active") else
              "does NOT have it yet (it applies after a restart, once the layer for your image is ready)."),
           "apt: " + (" ".join(sp.get("apt") or []) or "-"),
           "env: " + (", ".join(f"{k}={v}" for k, v in (sp.get("env") or {}).items()) or "-"),
           "layers: " + (", ".join(f"{t['template']} {t['state']}" + (f" (v{t['layer_version']})" if t.get("layer_version") else "")
                                   for t in d.get("templates") or []) or "-")]
    if sp.get("script"):
        out += ["script:", sp["script"]]
    return "\n".join(out)


def t_toolchain_status(project=""):
    return toolchain_text(json.loads(_get("/api/squad/toolchain?" + _q(project=project))))


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
    return _tline(d) + "".join(f"\n{k}: {v}" for k, v in woke.items()) + (f"\n{d['note']}" if d.get("note") else "")


def t_ask_human(question, options=None, ticket=""):
    d = json.loads(_post("/api/hitl/ask", {"question": question, "options": options or [], "ticket": ticket}))
    return (f"asked {d['id']} — end your turn; the answer wakes you"
            + (f" (ticket {d['ticket']} is blocked until then)" if d.get("ticket") else ""))


def t_ticket_update(id, status="", comment="", project="", title="", description=""):
    return _ticket("/api/squad/ticket-update", {"id": id, "status": status, "comment": comment, "project": project,
                                                "title": title, "description": description})


def t_ticket_create(title, description="", assignee="", depends_on=None, key="", project=""):
    return _ticket("/api/squad/ticket-create", {"title": title, "description": description, "assignee": assignee,
                                                "depends_on": depends_on or [], "key": key, "project": project})


def t_ticket_assign(id, assignee, project=""):
    return _ticket("/api/squad/ticket-assign", {"id": id, "assignee": assignee, "project": project})



# --- squad git remote: push, PRs, issues — run on the host (mgr/squadgit.py) --
def _git(op, payload, timeout=180):
    return json.loads(_post(f"/api/git/{op}", {k: v for k, v in payload.items() if v not in (None, "")}, timeout))


def _gline(i):
    extra = f" {i['head']} -> {i['base']}" if i.get("head") else ""
    return f"#{i.get('number')} [{i.get('state')}] {i.get('title')} ({i.get('author')}){extra} {i.get('url')}"


def t_git_push_branch(name="", writer="", project=""):
    d = _git("push", {"name": name, "writer": writer, "project": project})
    return f"Pushed {d.get('commit', '')[:12]} to {d.get('branch')} on {d.get('repo')}."


def t_project_sync():
    d = json.loads(_post("/api/project/sync", {}, 180))
    return f"{d.get('note') or 'synced'} ({d.get('commit', '')[:12]})"


def t_git_fetch(project=""):
    d = _git("fetch", {"project": project})
    return f"Fetched {d.get('repo')}: {d.get('refs')} branches in {d.get('namespace')} (base at {d.get('base') or '?'})."


def t_pr_create(title, body="", name="", writer="", project=""):
    d = _git("pr-create", {"title": title, "body": body, "name": name, "writer": writer, "project": project})
    return f"Opened PR {_gline(d)}" + (f"\n{d['note']}" if d.get("note") else "")


def t_pr_list(state="open", project=""):
    d = _git("prs", {"state": state, "project": project}, 60)
    return "\n".join(_gline(i) for i in d.get("items") or []) or f"No {state} PRs in {d.get('repo')}."


def t_pr_view(number, project=""):
    d = _git("pr", {"number": number, "project": project}, 60)
    cs = "".join(f"\n--- {c.get('author')} ({c.get('created')}):\n{c.get('body')}" for c in d.get("comments") or [])
    return _gline(d) + (" (merged)" if d.get("merged") else "") + f"\n\n{d.get('body') or ''}" + cs


def t_pr_comment(number, body, project=""):
    d = _git("pr-comment", {"number": number, "body": body, "project": project}, 60)
    return f"Commented on #{d.get('number')}: {d.get('url')}"


def t_issue_create(title, body="", project=""):
    d = _git("issue-create", {"title": title, "body": body, "project": project}, 60)
    return f"Opened issue {_gline(d)}" + (f"\n{d['note']}" if d.get("note") else "")


def t_issue_list(state="open", project=""):
    d = _git("issues", {"state": state, "project": project}, 60)
    return "\n".join(_gline(i) for i in d.get("items") or []) or f"No {state} issues in {d.get('repo')}."


def t_issue_comment(number, body, project=""):
    d = _git("issue-comment", {"number": number, "body": body, "project": project}, 60)
    return f"Commented on #{d.get('number')}: {d.get('url')}"


_PROJ ={"type": "string", "description": "optional when you are in one squad"}
_GPROJ = {"type": "string", "description": "optional when one of your projects has a git remote"}
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
        "List the platform's expert skills (name + description), optionally one category.",
        {"type": "object", "properties": {"category": {"type": "string", "description":
            "optional: coding, design, devops, data, research, writing, meta, personal or security "
            "(security only when enabled for you)"}}, "required": []}),
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
                        "wake": {"type": "boolean", "description": "false = only store it, wake nobody — for an ack, thanks or 'holding' that needs no action (default true)"},
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
    "squad_catalog": (_squad(t_squad_catalog),
        "Lead: what squad members can be made of — personas, skills (standard ones flagged), model tiers with "
        "their models, templates (openrouter, claude). Call it before squad_propose.",
        {"type": "object", "properties": {"project": _PROJ}, "required": []}),
    "squad_propose": (_squad(t_squad_propose),
        "Lead only: propose the squad plan to the user — goal, members (new or existing instances) and tickets. "
        "Pick each member's persona, skills, model_tier and template from squad_catalog; when no persona fits, give "
        "new_persona (created only if the user approves). NOTHING is created or started until the user approves "
        "it in the UI; the decision arrives as a squad message. The user may ask for changes instead (a squad message "
        "'Revise proposal <id>'): then call squad_propose again with the whole updated plan — it replaces that "
        "proposal as its next version.",
        {"type": "object",
         "properties": {"goal": {"type": "string"},
                        "members": {"type": "array", "items": {"type": "object", "properties": {
                            "name": {"type": "string", "description": "a-z 0-9 -"}, "purpose": {"type": "string"},
                            "role": {"type": "string", "enum": ["writer", "reader", "lead"]},
                            "existing": {"type": "boolean"},
                            "persona": {"type": "string", "description": "a persona name from squad_catalog"},
                            "new_persona": {"type": "object", "description": "only when no persona fits; created on approval",
                                            "properties": {"name": {"type": "string", "description": "a-z 0-9 -, not existing"},
                                                           "description": {"type": "string"}, "prompt": {"type": "string"},
                                                           "tools": {"type": "array", "items": {"type": "string"}}}},
                            "skills": {"type": "array", "items": {"type": "string"}},
                            "model_tier": {"type": "string", "enum": ["cheap", "strong", "code", "local"]},
                            "model": {"type": "string"},
                            "template": {"type": "string", "enum": ["openrouter", "claude"],
                                         "description": "openrouter (API model, counts against the token budget, default) "
                                                        "or claude (Claude Code on the user's subscription)"}}}},
                        "tickets": {"type": "array", "items": {"type": "object", "properties": {
                            "key": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"},
                            "assignee": {"type": "string"}, "depends_on": {"type": "array", "items": {"type": "string"}}}}},
                        "budget_tokens_per_day": {"type": "integer"}, "project": _PROJ},
         "required": ["goal", "members"]}),
    "squad_request_member": (_squad(t_squad_request_member),
        "Lead only, after approval: ask the user for one more member (fields as in squad_propose).",
        {"type": "object", "properties": {"member": {"type": "object"}, "reason": {"type": "string"}, "project": _PROJ},
         "required": ["member"]}),
    "toolchain_propose": (_squad(t_toolchain_propose),
        "Lead only: propose the squad's SHARED toolchain — apt packages, an install script (runs as root after apt, e.g. an official tarball into /usr/local) and env (e.g. PATH=/usr/local/go/bin:$PATH). Describe it once here instead of every member installing it: after the user approves, the manager builds it in a throwaway VM and every member gets it as a read-only layer at its next restart. The whole spec each time (it replaces the current one).",
        {"type": "object", "properties": {"apt": {"type": "array", "items": {"type": "string"}, "description": "Debian package names"},
                                          "script": {"type": "string", "description": "shell, runs as root after apt (at most 8 KB)"},
                                          "env": {"type": "object", "description": "NAME: value; $NAME / ${NAME} expand"},
                                          "reason": {"type": "string"}, "project": _PROJ},
         "required": []}),
    "toolchain_status": (_squad(t_toolchain_status),
        "What the squad's shared toolchain holds (apt packages, script, env) and whether your VM has it. Read-only.",
        {"type": "object", "properties": {"project": _PROJ}, "required": []}),
    "squad_request_merge": (_squad(t_squad_request_merge),
        "Lead only: ask the user to merge a writer's branch; the user reviews the diff and approves.",
        {"type": "object", "properties": {"writer": {"type": "string"}, "summary": {"type": "string"}, "project": _PROJ},
         "required": ["writer", "summary"]}),
    "ticket_list": (_squad(t_ticket_list),
        "The squad's tickets (id, key, status, assignee, dependencies).",
        {"type": "object", "properties": {"project": _PROJ}, "required": []}),
    "ticket_update": (_squad(t_ticket_update),
        "Any ticket: a comment (no code — that goes through git). Your ticket (any, as the lead): status "
        "open/doing/review/done/blocked. Review wakes the lead; done wakes the members whose tickets waited on it. "
        "title/description: lead only; use when re-scoping "
        "instead of only commenting (keeps the old version, wakes the assignee).",
        {"type": "object", "properties": {"id": {"type": "string"}, "status": {"type": "string"},
                                          "comment": {"type": "string"}, "project": _PROJ,
                                          "title": {"type": "string", "description": "lead only: the new title (re-scope)"},
                                          "description": {"type": "string",
                                                          "description": "lead only: the new description (re-scope)"}},
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
    "git_push_branch": (_squad(t_git_push_branch),
        "Squad git: snapshot your project folder and push its branch to squad/<you>/<name> on the project's remote "
        "(runs on the host; you never hold the token). Writers; the lead names a writer.",
        {"type": "object", "properties": {"name": {"type": "string", "description": "branch name part, default the project name"},
                                          "writer": {"type": "string", "description": "lead only: whose branch"},
                                          "project": _GPROJ}, "required": []}),
    "project_sync": (_squad(t_project_sync),
        "Bring the project's base branch (merged work of others) into your folder; your unmerged work is kept (committed first). On conflict nothing changes and the files are named.",
        {"type": "object", "properties": {}, "required": []}),
    "git_fetch": (_squad(t_git_fetch),
        "Squad git: fetch the remote's branches into the project repo as refs/remotes/kaim56/*.",
        {"type": "object", "properties": {"project": _GPROJ}, "required": []}),
    "pr_create": (_squad(t_pr_create),
        "Squad git: push your branch and open a pull request to the base branch. Writers and the lead; daily limit. "
        "Merging is left to the user.",
        {"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"},
                                          "name": {"type": "string"}, "writer": {"type": "string"}, "project": _GPROJ},
         "required": ["title"]}),
    "pr_list": (_squad(t_pr_list), "Squad git: the remote's pull requests.",
        {"type": "object", "properties": {"state": {"type": "string", "enum": ["open", "closed", "all"]}, "project": _GPROJ},
         "required": []}),
    "pr_view": (_squad(t_pr_view), "Squad git: one pull request with its description and comments.",
        {"type": "object", "properties": {"number": {"type": "integer"}, "project": _GPROJ}, "required": ["number"]}),
    "pr_comment": (_squad(t_pr_comment), "Squad git: comment on a pull request.",
        {"type": "object", "properties": {"number": {"type": "integer"}, "body": {"type": "string"}, "project": _GPROJ},
         "required": ["number", "body"]}),
    "issue_create": (_squad(t_issue_create), "Squad git: open an issue on the project's remote (daily limit).",
        {"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}, "project": _GPROJ},
         "required": ["title"]}),
    "issue_list": (_squad(t_issue_list), "Squad git: the remote's issues (without PRs).",
        {"type": "object", "properties": {"state": {"type": "string", "enum": ["open", "closed", "all"]}, "project": _GPROJ},
         "required": []}),
    "issue_comment": (_squad(t_issue_comment), "Squad git: comment on an issue.",
        {"type": "object", "properties": {"number": {"type": "integer"}, "body": {"type": "string"}, "project": _GPROJ},
         "required": ["number", "body"]}),
    "ask_human": (_squad(t_ask_human),
        "Squad: ask the operator (a human) a question you cannot decide yourself — a product choice, missing access, "
        "an unclear requirement. Your ticket is set blocked; END YOUR TURN after asking: the answer wakes you as a "
        "new turn and sets the ticket back to doing.",
        {"type": "object", "properties": {"question": {"type": "string"},
                                          "options": {"type": "array", "items": {"type": "string"},
                                                      "description": "optional: choices to pick from"},
                                          "ticket": {"type": "string", "description": "optional: ticket key or id (default: your ticket in doing)"}},
         "required": ["question"]}),
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


# --- PreToolUse hook (Claude Code settings, written by guest-init.sh) ----------
# `python3 kaim56_mcp.py --hook`: Claude Code hands the tool call on stdin. In
# a squad (the manager's /api/hitl/policy), a Bash command matching the squad's
# pattern or a listed kaim56 tool (git_push_branch, pr_create, …) is asked for:
# denied with "waiting for approval <id>" until the operator decides — the
# decision wakes the member, and the same call then goes through once.
# Manager unreachable / not in a squad: allowed (as the openrouter agent).
# Every call is also reported first (hook_report), so the board shows it live.
def hook_decision(event):
    """'' = allow, else the reason to block."""
    name = str(event.get("tool_name") or "")
    args = event.get("tool_input") or {}
    tool = "bash" if name == "Bash" else name[len("mcp__kaim56__"):] if name.startswith("mcp__kaim56__") else ""
    if not tool:
        return ""
    try:
        pol = json.loads(_get("/api/hitl/policy"))
        if not pol.get("squad") or tool not in (pol.get("tools") or []):
            return ""
        if tool == "bash" and not re.search(pol.get("bash_re") or r"(?!)", str(args.get("command", ""))):
            return ""
        target = str(args.get("command", ""))[:200] if tool == "bash" else \
            json.dumps(args, sort_keys=True, ensure_ascii=False)[:200]
        d = json.loads(_post("/api/hitl", {"tool": tool, "target": target, "async": True}))
    except Exception:
        return ""
    if not d.get("id") or d.get("status") == "approved":
        return ""
    return f"waiting for approval {d['id']} — end your turn; you will be woken with the decision"


def hook_report(event):
    """Every tool call, before it runs, into the instance's audit trail: the Squads board
    shows it live as "tool: target" (mgr/squadkick._tool), like the OpenRouter agent's."""
    name, args = str(event.get("tool_name") or ""), event.get("tool_input") or {}
    if not name or not isinstance(args, dict):
        return
    tool = "bash" if name == "Bash" else name[len("mcp__kaim56__"):] if name.startswith("mcp__kaim56__") else name
    target = next((str(args[k]) for k in ("command", "file_path", "pattern", "url", "path", "description") if args.get(k)), "")
    try:
        _post("/api/audit", {"tool": tool, "target": target[:200], "ok": True, "result": "started"}, timeout=2)
    except Exception:
        pass


def hook(stdin, stdout):
    try:
        event = json.load(stdin)
    except ValueError:
        return 0
    event = event if isinstance(event, dict) else {}
    hook_report(event)
    why = hook_decision(event)
    if why:
        stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                        "permissionDecisionReason": why}}))
    return 0


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
    if "--hook" in sys.argv:
        sys.exit(hook(sys.stdin, sys.stdout))
    main()
