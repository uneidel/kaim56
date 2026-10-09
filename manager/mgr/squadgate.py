# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Squad gate (security boundary): the user approves before a squad works.

A project's lead PROPOSES; only the operator's approval in the UI acts:

- ``squad``: the plan — goal, members (new or existing instances, with
  persona, skills, model tier, role), tickets, a daily token budget. One
  pending per project. Approve: create the missing instances, add them to the
  project, set the budget, enable + approve the squad, create the tickets,
  tell the lead. Every step is recorded in the proposal and skips what is
  already done, so approving again retries only what failed.
- ``member``: one more member later (after the squad is approved).
- ``merge``: a writer's branch to the project's base, by the existing merge path.

Tickets: per project, {id, key, title, description, assignee, status,
depends_on (keys), comments}. The lead creates and assigns them only after the
squad is approved; the assignee or the lead updates them; the operator edits
status and assignee. Assigning a ticket whose dependencies are done — or the
last dependency reaching done — wakes the assignee through squads.post (the
phase-1 wake path, same guards).

State: squadgate.json in the manager dir (root-only).

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import os
import re
import threading
import time
import uuid

from mgr import audit as _audit
from mgr import instances as _instances
from mgr import notify as _notify
from mgr import paths as _paths
from mgr import personas as _personas
from mgr import projects as _projects
from mgr import router as _router
from mgr import skills as _skills
from mgr import squads as _squads

GATE_FILE = os.path.join(_paths.BASE, "squadgate.json")
KINDS = ("squad", "member", "merge")
STATUSES = ("open", "doing", "review", "done", "blocked")
MEMBER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
KEY_RE = re.compile(r"^[A-Za-z0-9-]{1,16}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9._:/@-]{1,160}$")
MAX_MEMBERS, MAX_TICKETS, MAX_SKILLS, MAX_DEPS = 8, 50, 10, 10
MAX_PENDING = 10                # pending proposals per project
KEEP_PROPOSALS = 200
MAX_COMMENTS = 100
MEMBER_TOOLS = ("squad_post", "squad_ask", "squad_read", "squad_members", "ticket_list", "ticket_update")
_lock = threading.Lock()        # the file
_decide = threading.Lock()      # one approval at a time


def _load():
    try:
        with open(GATE_FILE) as fh:
            d = json.load(fh)
        if isinstance(d, dict):
            return {"proposals": list(d.get("proposals") or []), "tickets": list(d.get("tickets") or [])}
    except (FileNotFoundError, ValueError):
        pass
    except OSError as e:          # root-only; only a non-root caller (a test) lands here
        print(f"[quiet] squadgate.json unreadable: {e!r}", flush=True)
    return {"proposals": [], "tickets": []}


def _save(d):
    tmp = GATE_FILE + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(d, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, GATE_FILE)


def _txt(v, cap):
    return str(v or "").strip()[:cap]


def lead(proj):
    return next((n for n, m in (proj.get("members") or {}).items() if m.get("role") == "lead"), "")


def approved(proj):
    return bool(_squads.settings(proj).get("approved"))


# ---- validation -------------------------------------------------------------
def _member(raw, proj, by, warn):
    """One proposed member -> (member, '') or (None, why); unknown persona or
    skills are kept but flagged in `warn`."""
    if not isinstance(raw, dict):
        return None, "a member must be an object"
    name = _txt(raw.get("name"), 64).lower()
    if not MEMBER_RE.match(name):
        return None, f"member name {name!r}: a-z 0-9 -, at most 32 characters"
    role = _txt(raw.get("role") or "writer", 16)
    if role not in _projects.ROLES:
        return None, f"{name}: role must be one of {', '.join(_projects.ROLES)}"
    if role == "lead" and name != by:
        return None, f"{name}: the project has one lead ({by})"
    known = {i["name"] for i in _instances.load_instances()}
    existing = bool(raw.get("existing")) or name in (proj.get("members") or {})
    if existing and name not in known:
        return None, f"{name}: no such instance (existing=true)"
    if not existing and name in known:
        return None, f"{name}: an instance of that name exists — set existing=true or pick another name"
    tpl = _txt(raw.get("template") or "openrouter", 64)
    if not existing and tpl not in {t.get("template") for t in _instances.load_templates()}:
        return None, f"{name}: unknown template {tpl!r}"
    tier = _txt(raw.get("model_tier"), 16)
    if tier and tier not in _router.TIERS:
        return None, f"{name}: model_tier must be one of {', '.join(_router.TIERS)}"
    model = _txt(raw.get("model"), 160)
    if model and not MODEL_RE.match(model):
        return None, f"{name}: model has characters outside A-Z a-z 0-9 . _ : / @ -"
    persona = _txt(raw.get("persona"), 64)
    if persona and persona not in {p.get("name") for p in _personas.load_personas()}:
        warn.append(f"{name}: persona {persona!r} does not exist")
    sk = raw.get("skills") or []
    sk = [x.strip() for x in (sk.split(",") if isinstance(sk, str) else sk) if str(x).strip()]
    if len(sk) > MAX_SKILLS:
        return None, f"{name}: at most {MAX_SKILLS} skills"
    sk = [_txt(x, 48) for x in sk]
    have = {s.get("name") for s in _skills.load_skills()}
    if [x for x in sk if x not in have]:
        warn.append(f"{name}: unknown skill(s) {', '.join(x for x in sk if x not in have)}")
    return {"name": name, "purpose": _txt(raw.get("purpose"), 500), "persona": persona, "skills": sk,
            "model_tier": tier, "model": model, "template": tpl, "role": role, "existing": existing}, ""


def _tickets(raw, assignees, known=frozenset()):
    """The proposed tickets -> (tickets, '') or (None, why). `known`: keys of
    tickets that exist already (a dependency may name them)."""
    if not isinstance(raw, list) or len(raw) > MAX_TICKETS:
        return None, f"tickets: a list of at most {MAX_TICKETS}"
    out, keys = [], set()
    for i, t in enumerate(raw):
        t = t if isinstance(t, dict) else {}
        key = _txt(t.get("key") or f"T{i + 1}", 16)
        if not KEY_RE.match(key) or key in keys or key in known:
            return None, f"ticket key {key!r}: A-Z a-z 0-9 -, at most 16, unique"
        title = _txt(t.get("title"), 200)
        if not title:
            return None, f"ticket {key}: title missing"
        who = _txt(t.get("assignee"), 64)
        if who and who not in assignees:
            return None, f"ticket {key}: assignee {who!r} is no member"
        deps = [_txt(x, 16) for x in (t.get("depends_on") or []) if str(x).strip()]
        if len(deps) > MAX_DEPS:
            return None, f"ticket {key}: at most {MAX_DEPS} dependencies"
        keys.add(key)
        out.append({"key": key, "title": title, "description": _txt(t.get("description"), 4000),
                    "assignee": who, "depends_on": deps})
    for t in out:
        bad = [d for d in t["depends_on"] if (d not in keys and d not in known) or d == t["key"]]
        if bad:
            return None, f"ticket {t['key']}: depends_on {', '.join(bad)} is no other ticket"
    return out, ""


def _budget(v):
    lo, hi = _squads.LIMITS["daily_token_budget"]
    try:
        v = int(v if v not in (None, "") else _squads.DEFAULTS["daily_token_budget"])
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


def validate_squad(proj, by, body):
    """The squad plan of a proposal -> (fields, '') or (None, why)."""
    goal = _txt(body.get("goal"), 2000)
    if not goal:
        return None, "goal missing"
    raw = body.get("members") or []
    if not isinstance(raw, list) or not raw or len(raw) > MAX_MEMBERS:
        return None, f"members: a list of 1 to {MAX_MEMBERS}"
    warn, members = [], []
    for r in raw:
        m, why = _member(r, proj, by, warn)
        if not m:
            return None, why
        if any(x["name"] == m["name"] for x in members):
            return None, f"{m['name']} twice"
        members.append(m)
    tickets, why = _tickets(body.get("tickets") or [], {m["name"] for m in members} | set(proj.get("members") or {}))
    if tickets is None:
        return None, why
    budget = _budget(body.get("budget_tokens_per_day"))
    if budget is None:
        return None, "budget_tokens_per_day must be a number from 0 to %d" % _squads.LIMITS["daily_token_budget"][1]
    return {"goal": goal, "members": members, "tickets": tickets, "budget_tokens_per_day": budget,
            "warnings": warn}, ""


# ---- proposals --------------------------------------------------------------
def proposals(project):
    return [p for p in _load()["proposals"] if p.get("project") == project]


def _get(project, pid):
    return next((p for p in proposals(project) if p.get("id") == pid), None)


def _put(prop):
    with _lock:
        d = _load()
        d["proposals"] = [p for p in d["proposals"] if p.get("id") != prop["id"]] + [prop]
        keep = [p for p in d["proposals"] if p.get("status") == "pending"]
        done = [p for p in d["proposals"] if p.get("status") != "pending"]
        d["proposals"] = keep + done[-KEEP_PROPOSALS:]
        _save(d)


def propose(proj, by, kind, body):
    """A lead's proposal -> (proposal, '') or (None, why). Notifies the user."""
    if lead(proj) != by:
        return None, f"only the lead of {proj['name']} may propose"
    mine = proposals(proj["name"])
    pending = [p for p in mine if p.get("status") == "pending"]
    if len(pending) >= MAX_PENDING:
        return None, f"{MAX_PENDING} proposals are waiting already"
    prop = {"id": uuid.uuid4().hex[:10], "project": proj["name"], "kind": kind, "status": "pending",
            "by": by, "created": int(time.time())}
    if kind == "squad":
        if any(p.get("kind") == "squad" for p in pending):
            return None, "a squad proposal is waiting for approval already"
        f, why = validate_squad(proj, by, body)
        if not f:
            return None, why
        prop.update(f)
    elif kind == "member":
        if not approved(proj):
            return None, "waiting for approval: propose the squad first (squad_propose)"
        warn = []
        m, why = _member(body.get("member"), proj, by, warn)
        if not m:
            return None, why
        if m["name"] in (proj.get("members") or {}):
            return None, f"{m['name']} is a member already"
        prop.update(members=[m], warnings=warn, goal=_txt(body.get("reason"), 1000))
    elif kind == "merge":
        writer = _txt(body.get("writer"), 64)
        if proj.get("strategy") != "worktree" or writer not in _projects.writers(proj):
            return None, f"{writer!r} is no writer of a worktree project {proj['name']}"
        summary = _txt(body.get("summary"), 2000)
        if not summary:
            return None, "summary missing"
        prop.update(writer=writer, summary=summary)
    else:
        return None, "unknown kind"
    _put(prop)
    try:
        _notify.notify_add(by, f"Squad {proj['name']}: proposal waiting for approval",
                           f"{kind} proposal by {by} — review it in Projects → Squad", link="projects")
    except Exception as e:                       # the proposal stands without the bell
        print(f"[quiet] squad notify failed: {e!r}", flush=True)
    return prop, ""


def _model_cfg(m, steps):
    """Config keys for the member's model: explicit model, else the first
    concrete model of its tier, else the persona's model, else the template's
    default (flagged)."""
    want = m["model"]
    if not want and m["model_tier"]:
        want = next((k for k, t in sorted(_router.load()["tiers"].items()) if t == m["model_tier"]), "")
        if not want:
            steps.append({"step": f"model {m['name']}", "ok": False,
                          "detail": f"no model has tier {m['model_tier']} (Router tiers) — template default kept"})
    if not want:
        return {}
    backend, _, rest = want.partition("/")
    if backend in _instances.PROVIDER_MODEL_KEY and rest:
        if backend == "llama":
            steps.append({"step": f"model {m['name']}", "ok": False,
                          "detail": f"{want} is a local model — set LLAMA_ENDPOINT by hand; template default kept"})
            return {}
        return {_instances.PROVIDER_MODEL_KEY[backend]: rest}
    return {"OPENROUTER_MODEL": want}


def _create(proj, m, steps):
    """The member's instance (created when missing)."""
    if any(i["name"] == m["name"] for i in _instances.load_instances()):
        return True
    cfg = {"SQUAD_SKILLS": ",".join(m["skills"])} if m["skills"] else {}
    pers = next((p for p in _personas.load_personas() if p.get("name") == m["persona"]), None) if m["persona"] else None
    if m["persona"] and not pers:
        steps.append({"step": f"persona {m['name']}", "ok": False, "detail": f"persona {m['persona']!r} not found — none set"})
    if pers and pers.get("tools"):                # a tool subset still needs the squad's own tools
        cfg["AGENT_TOOLS"] = ",".join(dict.fromkeys(list(pers["tools"]) + list(MEMBER_TOOLS)))
    if pers and pers.get("model"):
        cfg["OPENROUTER_MODEL"] = pers["model"]
    cfg.update(_model_cfg(m, steps))
    lines = [(pers or {}).get("prompt") or "",
             f"You are {m['name']}, {m['role']} in the squad of project {proj['name']}."
             + (f" Your purpose: {m['purpose']}" if m["purpose"] else ""),
             ("At the start of a task, load these skills with load_skill: " + ", ".join(m["skills"]))
             if m["skills"] else ""]
    cfg["AGENT_SYSTEM"] = "\n\n".join(x for x in lines if x)
    msg = _instances.create_instance(m["name"], m["template"], cfg)
    ok = "created" in str(msg)
    steps.append({"step": f"create {m['name']}", "ok": ok, "detail": str(msg)[:300]})
    return ok


def _run(prop, who):
    """The approval's steps, in order, each skipping what is done -> steps."""
    steps, name = [], prop["project"]
    proj = _projects.get(name)
    if proj is None:
        return [{"step": "project", "ok": False, "detail": "project gone"}]
    if prop["kind"] == "merge":
        r, st = _projects.member_op(name, "merge", prop["writer"])
        steps.append({"step": f"merge {prop['writer']}", "ok": st == 200,
                      "detail": (r.get("error") or r.get("note") or r.get("commit") or "merged") if isinstance(r, dict) else str(r)[:300]})
    else:
        made = [m for m in prop["members"] if _create(proj, m, steps)]
        members = {n: {"role": x.get("role")} for n, x in (proj.get("members") or {}).items()}
        add = {m["name"]: m["role"] for m in made if members.get(m["name"], {}).get("role") != m["role"]}
        if add:
            members.update({n: {"role": r} for n, r in add.items()})
            r = _projects.upsert({**proj, "members": members})
            steps.append({"step": "members " + ", ".join(f"{n}={r_}" for n, r_ in sorted(add.items())),
                          "ok": bool(r.get("ok")), "detail": r.get("error") or r.get("warn") or "added"})
        proj = _projects.get(name)
        if prop["kind"] == "squad":
            s = {**_squads.settings(proj), "enabled": True, "approved": True,
                 "daily_token_budget": prop["budget_tokens_per_day"]}
            _projects.set_squad(name, s)
            steps.append({"step": "squad", "ok": True, "detail": f"enabled, approved, budget {s['daily_token_budget']}"})
            have = {t["key"] for t in tickets(name)}
            new = [t for t in prop["tickets"] if t["key"] not in have]
            for t in new:
                _ticket_add(name, t, who)
            steps.append({"step": "tickets", "ok": True, "detail": f"{len(new)} created, {len(prop['tickets']) - len(new)} existed"})
    return steps


def decide(project, pid, ok, who, note="", edits=None):
    """Approve or reject a proposal (the operator) -> (proposal, '') or (None, why).
    Approving again re-runs the steps; each skips what is done."""
    with _decide:
        prop = _get(project, pid)
        if prop is None:
            return None, "unknown proposal"
        proj = _projects.get(project)
        if proj is None:
            return None, "unknown project"
        if prop["status"] == "rejected" or (not ok and prop["status"] != "pending"):
            return None, f"proposal is {prop['status']} already"
        note = _txt(note, 1000) or prop.get("note", "")
        if ok and edits and prop["status"] == "pending" and prop["kind"] == "squad":
            f, why = validate_squad(proj, prop["by"], {"goal": prop["goal"], **edits})
            if not f:
                return None, why
            prop.update(f)
        elif ok and edits and prop["status"] == "pending" and prop["kind"] == "member":
            warn = []
            m, why = _member((edits.get("members") or [None])[0], proj, prop["by"], warn)
            if not m:
                return None, why
            prop.update(members=[m], warnings=warn)
        prop.update(status="approved" if ok else "rejected", decided=int(time.time()), decided_by=who, note=note)
        if ok:
            prop["steps"] = _run(prop, who)
        _put(prop)
    failed = [s["step"] for s in prop.get("steps") or [] if not s["ok"]]
    _audit.audit_append(prop["by"], "squad_decision", f"{project} {prop['kind']} {pid}", ok and not failed,
                        err=", ".join(failed), result=f"{prop['status']} by {who}" + (f": {note}" if note else ""))
    lead_ = lead(_projects.get(project) or {}) or prop["by"]
    what = {"squad": "squad plan", "member": "member request", "merge": f"merge of {prop.get('writer', '')}"}[prop["kind"]]
    text = (f"Your {what} ({pid}) was {prop['status']} by {who}." + (f" Note: {note}" if note else "")
            + (f" Steps not done: {', '.join(failed)}." if failed else "")
            + (" The members and tickets are set up — start them with ticket_assign or squad_post."
               if ok and prop["kind"] == "squad" else ""))
    _squads.post(_projects.get(project), "user:" + who, [lead_], text)
    return prop, ""


# ---- tickets ----------------------------------------------------------------
def tickets(project):
    return [t for t in _load()["tickets"] if t.get("project") == project]


def _ticket_add(project, t, who):
    with _lock:
        d = _load()
        mine = [x for x in d["tickets"] if x.get("project") == project]
        tid = max([x["id"] for x in mine] or [0]) + 1
        new = {"id": tid, "project": project, "key": t.get("key") or f"T{tid}", "title": t["title"],
               "description": t.get("description", ""), "assignee": t.get("assignee", ""), "status": "open",
               "depends_on": t.get("depends_on") or [], "comments": [], "updated": int(time.time()), "by": who}
        d["tickets"].append(new)
        _save(d)
    return new


def _ready(t, all_):
    done = {x["key"] for x in all_ if x["status"] == "done"}
    return t["assignee"] and t["status"] not in ("done",) and all(k in done for k in t["depends_on"])


def _wake(proj, t, sender, depth):
    text = (f"Ticket {t['key']} (id {t['id']}) is yours: {t['title']}"
            + (f"\n{t['description']}" if t["description"] else "")
            + f"\nUpdate it with ticket_update id={t['id']} (status doing/review/done/blocked, comment).")
    m, why = _squads.post(proj, sender, [t["assignee"]], text, depth=depth)
    return (m or {}).get("blocked") or why


def create_ticket(proj, who, body, depth=1):
    """The lead (after approval) or the operator -> (ticket, '') or (None, why)."""
    if not approved(proj):
        return None, "waiting for approval: tickets come with the approved squad plan"
    have = tickets(proj["name"])
    if len(have) >= 500:
        return None, "500 tickets in this project"
    row = dict(body)
    if row.get("key") in (None, ""):
        row["key"] = f"T{max([x['id'] for x in have] or [0]) + 1}"
    ts, why = _tickets([row], set(proj.get("members") or {}), {x["key"] for x in have})
    if ts is None:
        return None, why
    t = _ticket_add(proj["name"], ts[0], who)
    if _ready(t, have + [t]):
        t["wake"] = _wake(proj, t, who, depth) or "woken"
    return t, ""


def update_ticket(proj, who, tid, body, may, depth=1):
    """Change status / assignee / add a comment. `may(ticket)` -> '' or why
    the caller may not. Wakes a new assignee, and the assignees whose tickets
    the change made ready -> (ticket, '') or (None, why)."""
    status = _txt(body.get("status"), 16)
    if status and status not in STATUSES:
        return None, f"status must be one of {', '.join(STATUSES)}"
    assignee = body.get("assignee")
    if assignee is not None:
        assignee = _txt(assignee, 64)
        if assignee and assignee not in (proj.get("members") or {}):
            return None, f"{assignee!r} is no member"
    comment = _txt(body.get("comment"), 2000)
    with _lock:
        d = _load()
        mine = [x for x in d["tickets"] if x.get("project") == proj["name"]]
        t = next((x for x in mine if str(x["id"]) == str(tid).strip()), None)
        if t is None:
            return None, f"no ticket {tid} in {proj['name']}"
        why = may(t)
        if why:
            return None, why
        before = {x["key"] for x in mine if _ready(x, mine)}
        old_assignee = t["assignee"]
        if status:
            t["status"] = status
        if assignee is not None:
            t["assignee"] = assignee
        if comment:
            t["comments"] = (t["comments"] + [{"ts": int(time.time()), "by": who, "text": comment}])[-MAX_COMMENTS:]
        t["updated"] = int(time.time())
        _save(d)
    now = [x for x in mine if _ready(x, mine)]
    wake = [x for x in now if x["key"] not in before
            or (x is t and assignee and assignee != old_assignee)]
    out = dict(t)
    out["woke"] = {x["key"]: _wake(proj, x, who, depth) or "woken" for x in wake}
    return out, ""


def purge(project):
    """A deleted project's proposals and tickets go with it."""
    with _lock:
        d = _load()
        if any(x.get("project") == project for x in d["proposals"] + d["tickets"]):
            d = {k: [x for x in v if x.get("project") != project] for k, v in d.items()}
            _save(d)
