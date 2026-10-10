# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Human in the loop (security boundary): everything that waits for the operator.

One persistent queue (hitlq.json in the manager dir, root-only) of items::

  {id (12 hex), kind: approval|question|review,
   status: open|approved|denied|answered|changes_requested|expired,
   created, decided, decided_by, instance, project, squad_id, ticket (key or ""),
   title, detail, options [], tool, target, answer, comment, wake_on_decision}

- approval: a gated tool call. A squad member (any squad-enabled project) asks
  for the tools of its squad's ``approval_tools`` (default DEFAULT_TOOLS; "bash"
  = bash commands matching BASH_RE) and gets "waiting for approval <id>" — no
  timeout; the decision wakes it (a channel post, the existing wake path) and
  lets the same call (tool + target) through ONCE. A non-squad instance keeps
  the old bounded polling (mgr/signal.py hitl_* is the facade; HITL_TTL).
- question: a member's ``ask_human``; its ticket goes to blocked, the answer
  sets it back to doing and wakes the member.
- review: a supervised squad (setting ``autonomy``) turns a member's ticket
  "done" into "review"; Approve -> done (dependents wake), Request changes ->
  doing + the assignee is woken with the comment.

Every new item: a card in its squad's channel, a notification (web bell + app),
the squad archive (mgr/squadrec.py). Decisions only by the admin (/api/waiting).

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import os
import threading
import time
import uuid

from mgr import notify as _notify
from mgr import paths as _paths
from mgr import projects as _projects
from mgr import squadgate as _squadgate
from mgr import squadrec as _squadrec
from mgr import squads as _squads

QUEUE_FILE = os.path.join(_paths.BASE, "hitlq.json")
KINDS = ("approval", "question", "review")
ACTIONS = {"approval": ("approve", "deny"), "question": ("answer",), "review": ("approve", "request_changes")}
DONE = {"approve": "approved", "deny": "denied", "answer": "answered", "request_changes": "changes_requested"}
DEFAULT_TOOLS = ["git_push_branch", "pr_create", "issue_create", "bash"]
# bash commands that need approval in a squad (network, installs, deletes, pushes)
BASH_RE = r"\b(curl|wget)\b|\bpip3?\s+install\b|\bnpm\s+(install|i|add)\b|\bapt(-get)?\b|\brm\s+-[a-zA-Z]*[rR]|\bgit\s+push\b"
HITL_TTL = 600                  # a non-squad (polled) approval expires after this
KEEP = 1000                     # decided items kept
FIELDS = ("id", "kind", "status", "created", "decided", "decided_by", "instance", "project", "squad_id", "ticket",
          "title", "detail", "options", "tool", "target", "answer", "comment", "wake_on_decision")
_lock = threading.RLock()


def _load():
    try:
        with open(QUEUE_FILE) as fh:
            d = json.load(fh)
        return [x for x in d if isinstance(x, dict)] if isinstance(d, list) else []
    except (FileNotFoundError, ValueError):
        return []


def _save(items):
    done = [x for x in items if x["status"] != "open"]
    drop = {x["id"] for x in done[:-KEEP]} if len(done) > KEEP else set()
    tmp = QUEUE_FILE + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump([x for x in items if x["id"] not in drop], fh, indent=1, ensure_ascii=False)
    os.replace(tmp, QUEUE_FILE)


def public(it):
    return {k: it.get(k) for k in FIELDS}


def _expire(items, now):
    """Polled approvals (non-squad) die after HITL_TTL, as the in-memory ones did."""
    hit = False
    for x in items:
        if x["status"] == "open" and not x.get("wake_on_decision") and now - x["created"] > HITL_TTL:
            x.update(status="expired", decided=int(now), decided_by="timeout")
            hit = True
    return hit


def items(status="open"):
    with _lock:
        lst = _load()
        if _expire(lst, time.time()):
            _save(lst)
    return [public(x) for x in lst if status in ("", "all") or x["status"] == status]


def get(iid):
    return next((x for x in items("all") if x["id"] == str(iid)), None)


# ---- squads -------------------------------------------------------------------
def squad_of(inst_name):
    """The first squad-enabled project the instance is a member of (None: none)."""
    return next((p for p, _m in _projects.memberships(inst_name) if _squads.settings(p)["enabled"]), None)


# Tools the MANAGER executes (squad git/PR/issue, mgr/squadgit.py): their
# approval is enforced in the manager's own route, not trusted to the VM's hook
# — a compromised guest could skip the hook and call /api/git/* directly.
SERVER_GATED = {"git_push_branch", "git_fetch", "pr_create", "pr_list", "pr_view", "pr_comment",
                "issue_create", "issue_list", "issue_comment"}


def policy(inst_name):
    """What a guest's tool hook asks: its squad, the tools it must gate itself
    (server-gated ones are left to the manager), the bash pattern."""
    p = squad_of(inst_name) if inst_name else None
    if p is None:
        return {"squad": "", "tools": [], "bash_re": ""}
    return {"squad": p["name"], "tools": [t for t in _squads.settings(p)["approval_tools"] if t not in SERVER_GATED],
            "bash_re": BASH_RE}


def server_gate(inst_name, tool, target, p):
    """For a manager-executed tool on project `p` (the one the call acts on):
    None = go ahead (no squad there, not gated, or an approval for this very
    call was used up now), else the open approval item to wait for."""
    if p is None or not _squads.settings(p)["enabled"] or tool not in _squads.settings(p)["approval_tools"]:
        return None
    it = approval(inst_name, tool, target, p)
    return None if it["status"] == "approved" else it


def _ticket(project, ref):
    ref = str(ref or "").strip()
    return next((t for t in _squadgate.tickets(project) if ref and (t["key"] == ref or str(t["id"]) == ref)), None)


def _operator(proj):
    return _squads._last_user(proj["name"]) or "user:operator"


def _card(proj, it):
    """The item as a channel line from the member to the operator (wakes nobody)."""
    head = {"approval": "needs your approval", "question": "asks you", "review": "asks for a review"}[it["kind"]]
    text = f"⏳ {it['instance']} {head} ({it['id']}): {it['title']}"
    if it["detail"] and it["detail"] != it["title"]:
        text += "\n" + it["detail"]
    if it["options"]:
        text += "\nOptions: " + " | ".join(it["options"])
    _squads.post(proj, it["instance"], [_operator(proj)], text[:_squads.TEXT_MAX], kind="hitl")


def _tell(proj, who, member, text):
    """A channel post from the operator to the member: the existing wake path."""
    m, why = _squads.post(proj, "user:" + who, [member], text[:_squads.TEXT_MAX])
    return (m or {}).get("blocked") or why or "woken"


def _new(kind, instance, title, detail="", proj=None, **kw):
    now = int(time.time())
    it = {"id": uuid.uuid4().hex[:12], "kind": kind, "status": "open", "created": now, "decided": 0, "decided_by": "",
          "instance": instance, "project": proj["name"] if proj else "", "squad_id": _squads.squad_id(proj) if proj else "",
          "ticket": "", "title": str(title or "").strip()[:200], "detail": str(detail or "").strip()[:4000], "options": [],
          "tool": "", "target": "", "answer": "", "comment": "", "wake_on_decision": proj is not None, "consumed": False}
    it.update(kw)
    with _lock:
        lst = _load()
        lst.append(it)
        _save(lst)
    if proj is not None:
        _squadrec.log_hitl(proj["name"], "create", public(it))
        try:
            _card(proj, it)
        except Exception as e:                   # the item stands without the card
            print(f"[quiet] hitl card {it['id']}: {e!r}", flush=True)
    try:
        _notify.notify_add(instance, "Waiting for you: " + it["title"], it["detail"], link="squads")
    except Exception as e:
        print(f"[quiet] hitl notify {it['id']}: {e!r}", flush=True)
    return it


def approval(instance, tool, target, proj=None):
    """A gated tool call. With `proj` (a squad member): an approved, unused item
    for the same call is used up and returned (the retry goes through), an open
    one is returned as is, else a new one. Without: always a new (polled) one."""
    tool, target = str(tool or "")[:40], str(target or "")[:200]
    if proj is not None:
        with _lock:
            lst = _load()
            same = [x for x in lst if x["kind"] == "approval" and x["instance"] == instance and x["tool"] == tool
                    and x["target"] == target and x.get("wake_on_decision")]
            ok = next((x for x in same if x["status"] == "approved" and not x.get("consumed")), None)
            if ok:
                ok["consumed"] = True
                _save(lst)
                return public(ok)
            op = next((x for x in same if x["status"] == "open"), None)
            if op:
                return public(op)
    return public(_new("approval", instance, f"{instance} wants to run {tool}", target, proj, tool=tool, target=target))


def ask(inst_name, question, options=None, ticket=""):
    """ask_human: a question item; the ticket (named, or the member's doing one) goes to blocked."""
    question = str(question or "").strip()
    if not question:
        return None, "question missing"
    proj = squad_of(inst_name)
    if proj is None:
        return None, "ask_human works in a squad only (you are in no squad-enabled project) — use notify or send_signal"
    opts = [str(o).strip()[:200] for o in (options or []) if str(o).strip()][:10] if isinstance(options, list) else []
    t = _ticket(proj["name"], ticket) if ticket else next(
        (x for x in _squadgate.tickets(proj["name"]) if x["assignee"] == inst_name and x["status"] == "doing"), None)
    if ticket and t is None:
        return None, f"no ticket {ticket} in {proj['name']}"
    it = _new("question", inst_name, question[:120], question, proj, options=opts, ticket=t["key"] if t else "")
    if t:
        _squadgate.update_ticket(proj, inst_name, t["id"], {"status": "blocked", "comment": f"asked the operator ({it['id']}): {question[:300]}"},
                                 lambda _t: "")
    return it, ""


def review(proj, inst_name, t, comment=""):
    """A supervised squad: the member's 'done' waits for the operator (one open review per ticket)."""
    cur = next((x for x in items() if x["kind"] == "review" and x["project"] == proj["name"] and x["ticket"] == t["key"]), None)
    if cur:
        return cur
    return _new("review", inst_name, f"Review {t['key']}: {t['title']}", str(comment or t.get("description") or ""),
                proj, ticket=t["key"])


# ---- deciding -------------------------------------------------------------------
def decide(iid, action, who, text="", choice=""):
    """The operator's decision -> (item, '', 200) or (None, why, 400|404|409)."""
    text, choice = str(text or "").strip()[:4000], str(choice or "").strip()[:200]
    with _lock:
        lst = _load()
        _expire(lst, time.time())
        it = next((x for x in lst if x["id"] == str(iid)), None)
        if it is None:
            return None, "unknown item", 404
        if it["status"] != "open":
            return None, f"already decided: {it['status']}", 409
        if action not in ACTIONS[it["kind"]]:
            return None, f"a {it['kind']} takes action {' or '.join(ACTIONS[it['kind']])}", 400
        if action == "answer" and not (text or choice):
            return None, "answer: text or choice required", 400
        if action == "request_changes" and not text:
            return None, "request_changes: text required (what should change)", 400
        if choice and it["options"] and choice not in it["options"]:
            return None, f"choice must be one of: {', '.join(it['options'])}", 400
        it.update(status=DONE[action], decided=int(time.time()), decided_by=str(who or "admin")[:64])
        if action == "answer":
            it["answer"] = choice or text
            it["comment"] = text if choice else ""
        else:
            it["comment"] = text
        _save(lst)
    out = public(it)
    if it["project"]:
        _squadrec.log_hitl(it["project"], "decide", out)
        proj = _projects.get(it["project"])
        if proj is not None:
            try:
                out["effect"] = _effect(proj, out, action, who)
            except Exception as e:                # the decision stands; say what did not follow
                out["effect"] = f"failed: {e!r}"[:300]
    return out, "", 200


def _effect(proj, it, action, who):
    """What follows a squad item's decision: wake the member, move its ticket."""
    note = f"\nComment: {it['comment']}" if it["comment"] else ""
    if it["kind"] == "approval":
        if not it["wake_on_decision"]:
            return ""
        if action == "approve":
            text = (f"Approval {it['id']} for {it['tool']} ({it['target'] or 'no arguments'}) was APPROVED by {who}.{note}\n"
                    "Run exactly the same call again now — it goes through once.")
        else:
            text = (f"Approval {it['id']} for {it['tool']} ({it['target'] or 'no arguments'}) was DENIED by {who}.{note}\n"
                    "Do not retry it; continue without it, or ask_human how to proceed.")
        return _tell(proj, who, it["instance"], text)
    t = _ticket(proj["name"], it["ticket"]) if it["ticket"] else None
    if it["kind"] == "question":
        if t and t["status"] == "blocked":
            _squadgate.update_ticket(proj, "user:" + who, t["id"], {"status": "doing", "comment": f"answered ({it['id']}): {it['answer']}"[:2000]},
                                     lambda _t: "")
        return _tell(proj, who, it["instance"], f"Answer to your question {it['id']} ({it['title']}): {it['answer']}"
                     + (f"\n{it['comment']}" if it["comment"] else "")
                     + (f"\nTicket {t['key']} is back to doing." if t else ""))
    if t is None:
        return f"ticket {it['ticket']} gone"
    if action == "approve":
        _r, why = _squadgate.update_ticket(proj, "user:" + who, t["id"], {"status": "done", "comment": it["comment"]}, lambda _t: "")
        return why or "done"
    _squadgate.update_ticket(proj, "user:" + who, t["id"], {"status": "doing", "comment": "changes requested: " + it["comment"]},
                             lambda _t: "")
    return _tell(proj, who, t["assignee"] or it["instance"],
                 f"Review of ticket {t['key']} (id {t['id']}): changes requested by {who}: {it['comment']}\n"
                 f"Continue the ticket and set it done again when fixed.")


# ---- the old in-memory contract (mgr/signal.py hitl_*) ----------------------------
def legacy_status(iid, instance=None):
    """pending|approved|denied|unknown. With `instance` (a guest asking) only its own."""
    it = get(iid)
    if not it or (instance and it["instance"] != instance):
        return "unknown"
    return {"open": "pending", "approved": "approved"}.get(it["status"], "denied")
