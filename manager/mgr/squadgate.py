# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Squad gate (security boundary): the user approves before a squad works.

A project's lead PROPOSES; only the operator's approval in the UI acts:

- ``squad``: the plan — goal, members (new or existing instances, with
  persona — or a ``new_persona`` created only on approval —, skills, model
  tier, template openrouter|claude, role), tickets, a daily token budget.
  Each member records who chose each field (``by``: architect, jev (score),
  you). Jev fills in a missing persona / tier only from JEV_AUTO_MIN, never
  skills (its other picks are hints on the card). The proposal keeps the
  repo profile (mgr/repoprofile.py) its choices were based on.
  The lead reads what it can choose from with squad_catalog. One
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

State: squadgate.json in the manager dir (root-only); every proposal and
ticket change is also archived in the squad's folder (mgr/squadrec.py).

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
from mgr import indexer as _indexer
from mgr import instances as _instances
from mgr import jevsetup as _jevsetup
from mgr import notify as _notify
from mgr import paths as _paths
from mgr import personas as _personas
from mgr import policy as _policy
from mgr import projects as _projects
from mgr import repoprofile as _repoprofile
from mgr import router as _router
from mgr import skills as _skills
from mgr import squadrec as _squadrec
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
MEMBER_TOOLS = ("squad_post", "squad_ask", "squad_read", "squad_members", "ticket_list", "ticket_update",
                "git_push_branch", "git_fetch", "pr_create", "pr_list", "pr_view", "pr_comment",
                "issue_create", "issue_list", "issue_comment")   # git: only with a project remote, role-checked
LEAD_TOOLS = ("squad_catalog", "squad_propose", "squad_request_member", "squad_request_merge", "ticket_create", "ticket_assign",
              "project_status", "project_diff")   # a lead created here (the kickoff's architect)
# The templates a squad member may use, with what the architect is told.
MEMBER_TEMPLATES = {"openrouter": "API model via OpenRouter (512 MiB); counts against the squad's daily token budget",
                    "claude": "Claude Code on the user's subscription (1024 MiB); the tier picks haiku/sonnet/opus"}
CLAUDE_TIER = {"cheap": "haiku", "code": "sonnet", "strong": "opus"}   # no anthropic/* model of the tier in Router
PERSONA_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
PERSONA_PROMPT_MAX, PERSONA_DESC_MAX = 6000, 300
# Planning personas: Jev never hands them to a non-lead by itself (it gave a
# CLI developer code-architect at 0.96 — live 2026-10-09); a hint only.
LEAD_PERSONAS = {"code-architect", "orchestrator"}
JEV_AUTO_MIN = 0.8              # Jev fills a squad member's persona / tier by itself only from this score
PROV_FIELDS = ("persona", "new_persona", "skills", "model_tier", "model", "template")
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
def _new_persona(raw, name, strict, warn):
    """A member's proposed persona -> (persona, '') or (None, why). An existing
    name: refused for the lead (`strict`), flagged otherwise (approve uses it)."""
    if not isinstance(raw, dict):
        return None, f"{name}: new_persona must be an object {{name, description, prompt, tools?}}"
    pn = _txt(raw.get("name"), 64).lower()
    if not PERSONA_NAME_RE.match(pn):
        return None, f"{name}: new_persona name {pn!r}: a-z 0-9 -, at most 32 characters"
    prompt = str(raw.get("prompt") or "").strip()
    if not prompt or len(prompt) > PERSONA_PROMPT_MAX:
        return None, f"{name}: new_persona prompt: 1 to {PERSONA_PROMPT_MAX} characters"
    if pn in {p.get("name") for p in _personas.load_personas()}:
        if strict:
            return None, f"{name}: persona {pn!r} exists — use persona={pn!r} or another name"
        warn.append(f"{name}: persona {pn!r} exists now — the existing one is used")
    tl = raw.get("tools") or []
    tl = [str(x).strip() for x in (tl.split(",") if isinstance(tl, str) else tl) if str(x).strip()][:60]
    bad = [x for x in tl if x not in _policy.tool_names()]
    if bad:
        return None, f"{name}: new_persona tools unknown: {', '.join(bad[:5])}"
    out = {"name": pn, "description": _txt(raw.get("description"), PERSONA_DESC_MAX), "prompt": prompt}
    if tl:
        out["tools"] = tl
    return out, ""


def _member(raw, proj, by, warn, strict=True):
    """One proposed member -> (member, '') or (None, why); unknown persona or
    skills are kept but flagged in `warn`. `strict`: the lead's proposal (a
    new_persona must not exist); False for the operator's edits."""
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
    if not existing and (tpl not in MEMBER_TEMPLATES or tpl not in {t.get("template") for t in _instances.load_templates()}):
        return None, f"{name}: unknown template {tpl!r} (a member uses {' or '.join(MEMBER_TEMPLATES)})"
    tier = _txt(raw.get("model_tier"), 16)
    if tier and tier not in _router.TIERS:
        return None, f"{name}: model_tier must be one of {', '.join(_router.TIERS)}"
    model = _txt(raw.get("model"), 160)
    if model and not MODEL_RE.match(model):
        return None, f"{name}: model has characters outside A-Z a-z 0-9 . _ : / @ -"
    persona, newp = _txt(raw.get("persona"), 64), None
    if raw.get("new_persona") and not existing:
        newp, why = _new_persona(raw["new_persona"], name, strict, warn)
        if not newp:
            return None, why
        persona = newp["name"]
    elif persona and persona not in {p.get("name") for p in _personas.load_personas()}:
        warn.append(f"{name}: persona {persona!r} does not exist")
    sk = raw.get("skills") or []
    sk = [x.strip() for x in (sk.split(",") if isinstance(sk, str) else sk) if str(x).strip()]
    if len(sk) > MAX_SKILLS:
        return None, f"{name}: at most {MAX_SKILLS} skills"
    sk = [_txt(x, 48) for x in sk]
    have = {s.get("name") for s in _skills.load_skills()}
    if [x for x in sk if x not in have]:
        warn.append(f"{name}: unknown skill(s) {', '.join(x for x in sk if x not in have)}")
    out = {"name": name, "purpose": _txt(raw.get("purpose"), 500), "persona": persona, "skills": sk,
           "model_tier": tier, "model": model, "template": tpl, "role": role, "existing": existing}
    if newp:
        out["new_persona"] = newp
    return out, ""


def _chose(m, who, before=None, raw=None):
    """m["by"]: who chose each set field — `who` for every field (no
    `before`; a template the `raw` proposal left out is "default"), else the
    earlier marks and `who` where the value changed."""
    old = (before or {}).get("by") or {}
    by = {f: (old.get(f) if before is not None and m.get(f) == before.get(f) else who)
          for f in PROV_FIELDS if m.get(f) and not (f == "template" and m["existing"])}
    if raw is not None and not raw.get("template") and "template" in by:
        by["template"] = "default"
    m["by"] = {f: v for f, v in by.items() if v}
    return m


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


def validate_squad(proj, by, body, strict=True):
    """The squad plan of a proposal -> (fields, '') or (None, why)."""
    goal = _txt(body.get("goal"), 2000)
    if not goal:
        return None, "goal missing"
    raw = body.get("members") or []
    if not isinstance(raw, list) or not raw or len(raw) > MAX_MEMBERS:
        return None, f"members: a list of 1 to {MAX_MEMBERS}"
    warn, members = [], []
    for r in raw:
        m, why = _member(r, proj, by, warn, strict)
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


# ---- what a lead chooses from (squad_catalog) -------------------------------
def _summary(p):
    text = re.sub(r"\s+", " ", p.get("description") or p.get("prompt") or "").strip()
    return re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0][:200]


def profile(proj):
    """The project's repo profile (mgr/repoprofile.py), None when there is none or it fails."""
    try:
        return _repoprofile.of_project(proj)
    except Exception as e:                       # a proposal or the catalog never fails over it
        print(f"[quiet] repo profile of {(proj or {}).get('name')}: {e!r}", flush=True)
        return None


def catalog(proj=None):
    """Personas, skills (standard flagged), model tiers, the member
    templates and the project's repo profile — read-only, for the lead's
    squad_propose."""
    prof = profile(proj) if proj else None
    tiers = _router.load()["tiers"]
    have = {t.get("template") for t in _instances.load_templates()}
    return {"personas": [{"name": p["name"], "description": _summary(p), **({"tools": p["tools"]} if p.get("tools") else {})}
                         for p in _personas.load_personas() if p.get("name")],
            "skills": _skills.meta(),
            "tiers": [{"tier": t, "models": sorted(k for k, v in tiers.items() if v == t)} for t in _router.TIERS],
            "templates": [{"template": t, "note": n} for t, n in MEMBER_TEMPLATES.items() if t in have],
            "profile": prof, "profile_text": _repoprofile.text(prof)}


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
    _squadrec.log_proposal(prop)


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
        prop.update(f, members=[_chose(m, "architect", raw=r) for m, r in zip(f["members"], body["members"])])
    elif kind == "member":
        if not approved(proj):
            return None, "waiting for approval: propose the squad first (squad_propose)"
        warn = []
        m, why = _member(body.get("member"), proj, by, warn)
        if not m:
            return None, why
        if m["name"] in (proj.get("members") or {}):
            return None, f"{m['name']} is a member already"
        prop.update(members=[_chose(m, "architect", raw=body["member"])], warnings=warn, goal=_txt(body.get("reason"), 1000))
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
    if kind != "merge":
        prop["profile"] = profile(proj)          # what the choices are based on (the card shows it)
    _put(prop)
    if kind != "merge" and _router.jev_url() and any(_wants_suggestion(m) for m in prop["members"]):
        _spawn(fill_suggestions, prop["project"], prop["id"])     # ~20 s per member: not in the lead's call
    try:
        _notify.notify_add(by, f"Squad {proj['name']}: proposal waiting for approval",
                           f"{kind} proposal by {by} — review it in Projects → Squad", link="projects")
    except Exception as e:                       # the proposal stands without the bell
        print(f"[quiet] squad notify failed: {e!r}", flush=True)
    return prop, ""


# ---- Jev's suggestions (mgr/jevsetup.py): filled into the pending proposal ---
def _spawn(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


def _wants_suggestion(m):
    return not m["existing"] and not (m["persona"] and (m["model_tier"] or m["model"]))


def jev_context(prop):
    """Jev's context for a member: the repo profile's keywords, then the squad goal."""
    kw, goal = _repoprofile.context(prop.get("profile")), prop.get("goal") or ""
    return ((f"Repository: {kw}. " if kw else "") + (f"Squad goal: {goal}" if goal else "")).strip()


def fill_suggestions(project, pid, only="", force=False):
    """Jev's persona / tier for the new members that lack them, filled in
    only from JEV_AUTO_MIN (skills never: Jev's skill picks were noise); below
    it, and Jev's skills, are kept as hints in member["suggested"]. `only` with
    force ('Suggest again'): that member gets all three, whatever the score.
    Written only while the proposal is pending; the user approves."""
    prop = _get(project, pid)
    if prop is None or prop.get("status") != "pending" or prop["kind"] == "merge":
        return None
    done = {}
    for m in prop["members"]:
        if (only and m["name"] != only) or m["existing"] or not (force or _wants_suggestion(m)):
            continue
        s = _jevsetup.suggest(m["purpose"] or prop.get("goal", ""), jev_context(prop))
        mark, by, hint = {"note": s["note"], "ms": s["ms"]}, m.setdefault("by", {}), {}
        if s["persona"] and (force or not m["persona"]) and not m.get("new_persona"):   # never over a proposed one
            lead_only = s["persona"]["name"] in LEAD_PERSONAS and m["role"] != "lead"
            if force or (s["persona"]["score"] >= JEV_AUTO_MIN and not lead_only):
                m["persona"], mark["persona"] = s["persona"]["name"], s["persona"]["score"]
                by["persona"] = f"jev ({s['persona']['score']:.2f})"
            else:
                hint["persona"] = s["persona"]
        if s["skills"] and (force or not m["skills"]):
            if force:
                m["skills"], mark["skills"] = [x["name"] for x in s["skills"]], {x["name"]: x["score"] for x in s["skills"]}
                by["skills"] = f"jev ({max(x['score'] for x in s['skills']):.2f})"
            else:
                hint["skills"] = {x["name"]: x["score"] for x in s["skills"]}
        if s["tier"] and (force or not (m["model_tier"] or m["model"])):
            if force or s["tier"]["score"] >= JEV_AUTO_MIN:
                m["model_tier"], mark["tier"] = s["tier"]["name"], s["tier"]["score"]
                by["model_tier"] = f"jev ({s['tier']['score']:.2f})"
                if force:
                    m["model"] = ""
                    by.pop("model", None)
            else:
                hint["tier"] = s["tier"]
        if hint:
            mark["hint"] = hint
        m["suggested"] = mark
        done[m["name"]] = m
    with _lock:
        d = _load()
        cur = next((p for p in d["proposals"] if p.get("id") == pid), None)
        if cur is None or cur.get("status") != "pending":
            return None
        cur["members"] = [done.get(m["name"], m) for m in cur["members"]]
        _save(d)
    return cur


def suggest_member(project, pid, member):
    """'Suggest again' on the approval card -> (proposal, '') or (None, why)."""
    prop = _get(project, pid)
    if prop is None or prop.get("status") != "pending":
        return None, "no pending proposal " + pid
    m = next((x for x in prop.get("members") or [] if x["name"] == member), None)
    if m is None or m["existing"]:
        return None, f"{member!r} is no new member of this proposal"
    if not _router.jev_url():
        return None, "Jev is not configured (JEV_URL in Settings)"
    p = fill_suggestions(project, pid, member, force=True)
    return (p, "") if p else (None, "the proposal was decided meanwhile")


def _claude_model(m, steps):
    """ANTHROPIC_MODEL for a claude member: its model (anthropic/<id>, an
    alias or a claude-* id), else the tier's anthropic/* model from the Router
    tiers, else the tier's alias (CLAUDE_TIER); otherwise the account default
    of the claude template (flagged)."""
    want, why = m["model"], ""
    if want:
        backend, _, rest = want.partition("/")
        if backend == "anthropic" and rest:
            return {"ANTHROPIC_MODEL": rest}
        if "/" not in want:
            return {"ANTHROPIC_MODEL": want}
        why = f"{want} is no Claude model"
    elif m["model_tier"]:
        key = next((k for k, t in sorted(_router.load()["tiers"].items())
                    if t == m["model_tier"] and k.startswith("anthropic/")), "")
        model = key.partition("/")[2] or CLAUDE_TIER.get(m["model_tier"], "")
        if model:
            return {"ANTHROPIC_MODEL": model}
        why = f"tier {m['model_tier']} has no Claude model"
    if why:
        steps.append({"step": f"model {m['name']}", "ok": False,
                      "detail": why + " — the claude template's account default kept"})
    return {}


def _model_cfg(m, steps):
    """Config keys for the member's model: explicit model, else the first
    concrete model of its tier, else the persona's model, else the template's
    default (flagged). A claude member: _claude_model."""
    if m.get("template") == "claude":
        return _claude_model(m, steps)
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


LEAD_MAX_STEPS = 40


def _create(proj, m, steps):
    """The member's instance (created when missing)."""
    if any(i["name"] == m["name"] for i in _instances.load_instances()):
        return True
    # TRANSPORT=web: a squad member is reached through its web bridge (wake =
    # task -> _chat_post); a template's own default (signal) left the first
    # live architect deaf to every message (2026-10-09).
    cfg = {"TRANSPORT": "web"}
    if m["role"] == "lead":                         # a lead studies code before it plans: 12 tool rounds were
        cfg["AGENT_MAX_STEPS"] = str(LEAD_MAX_STEPS)  # not enough for the first live architect (2026-10-09)
    if m["skills"]:
        cfg["SKILLS"] = ",".join(m["skills"])
    pers = next((p for p in _personas.load_personas() if p.get("name") == m["persona"]), None) if m["persona"] else None
    if m["persona"] and not pers:
        steps.append({"step": f"persona {m['name']}", "ok": False, "detail": f"persona {m['persona']!r} not found — none set"})
    if pers and pers.get("tools"):                # a tool subset still needs the squad's own tools
        extra = MEMBER_TOOLS + (LEAD_TOOLS if m["role"] == "lead" else ())
        cfg["AGENT_TOOLS"] = ",".join(dict.fromkeys(list(pers["tools"]) + list(extra)))
    if pers and pers.get("model") and m["template"] != "claude":   # a persona's model is an API model
        cfg["OPENROUTER_MODEL"] = pers["model"]
    cfg.update(_model_cfg(m, steps))
    lines = [(pers or {}).get("prompt") or "",
             f"You are {m['name']}, {m['role']} in the squad of project {proj['name']}."
             + (f" Your purpose: {m['purpose']}" if m["purpose"] else "")]
    cfg["AGENT_SYSTEM"] = "\n\n".join(x for x in lines if x)
    msg = _instances.create_instance(m["name"], m["template"], cfg)
    ok = "created" in str(msg)
    steps.append({"step": f"create {m['name']}", "ok": ok, "detail": str(msg)[:300]})
    return ok


def _persona_for(m, steps):
    """A member's proposed persona, created on approval — never over an
    existing one (then that one is used, noted)."""
    np = m.get("new_persona")
    if not np or m["existing"] or any(i["name"] == m["name"] for i in _instances.load_instances()):
        return
    m["persona"] = np["name"]
    if any(p.get("name") == np["name"] for p in _personas.load_personas()):
        if not m.get("persona_created"):
            steps.append({"step": f"persona {np['name']}", "ok": True,
                          "detail": "exists already — the existing persona is used, the proposed one is dropped"})
        return
    msg = _personas.upsert_persona(np["name"], np["prompt"], np.get("tools") or None, description=np.get("description"))
    m["persona_created"] = True
    steps.append({"step": f"persona {np['name']}", "ok": "saved" in str(msg), "detail": str(msg)[:300]})


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
        if st == 200 and _indexer.instance():
            steps.append({"step": "reindex", "ok": True, "detail": _indexer.reindex(name, f"merge {prop['writer']}")})
    else:
        for m in prop["members"]:
            _persona_for(m, steps)
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
            _squads.save_settings(name, s)
            steps.append({"step": "squad", "ok": True, "detail": f"enabled, approved, budget {s['daily_token_budget']}"})
            have = {t["key"] for t in tickets(name)}
            new = [t for t in prop["tickets"] if t["key"] not in have]
            for t in new:
                _ticket_add(name, t, who)
            steps.append({"step": "tickets", "ok": True, "detail": f"{len(new)} created, {len(prop['tickets']) - len(new)} existed"})
        proj = _projects.get(name)
        if proj and _squads.settings(proj)["enabled"]:
            try:                                  # the new members' IDs (mgr/squadrec.py)
                _squadrec.ensure(proj, {m["name"]: m["persona"] for m in made})
            except Exception as e:
                steps.append({"step": "squad record", "ok": False, "detail": repr(e)[:300]})
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
        if ok and edits and prop["status"] == "pending":
            prop["edits"] = edits                 # what the operator changed (the archive keeps it)
        before = {m["name"]: m for m in prop.get("members") or []}
        if ok and edits and prop["status"] == "pending" and prop["kind"] == "squad":
            f, why = validate_squad(proj, prop["by"], {"goal": prop["goal"], **edits}, strict=False)
            if not f:
                return None, why
            prop.update(f, members=[_chose(m, "you", before.get(m["name"], {})) for m in f["members"]])
        elif ok and edits and prop["status"] == "pending" and prop["kind"] == "member":
            warn = []
            m, why = _member((edits.get("members") or [None])[0], proj, prop["by"], warn, strict=False)
            if not m:
                return None, why
            prop.update(members=[_chose(m, "you", before.get(m["name"], {}))], warnings=warn)
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
    _squadrec.log_ticket(project, "create", new, who)
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
        old_assignee, old_status = t["assignee"], t["status"]
        if status:
            t["status"] = status
        if assignee is not None:
            t["assignee"] = assignee
        if comment:
            t["comments"] = (t["comments"] + [{"ts": int(time.time()), "by": who, "text": comment}])[-MAX_COMMENTS:]
        t["updated"] = int(time.time())
        _save(d)
    _squadrec.log_ticket(proj["name"], "update", t, who,
                         {k: v for k, v in (("status", status), ("assignee", assignee), ("comment", comment))
                          if v or (k == "assignee" and v is not None)})
    now = [x for x in mine if _ready(x, mine)]
    wake = [x for x in now if x["key"] not in before
            or (x is t and assignee and assignee != old_assignee)]
    out = dict(t)
    out["woke"] = {x["key"]: _wake(proj, x, who, depth) or "woken" for x in wake}
    # A finished feature: refresh the code index (mgr/indexer.py; queued, never blocks).
    if t["status"] == "done" and old_status != "done" and _indexer.instance():
        out["reindex"] = _indexer.reindex(proj["name"], f"ticket {t['key']} done")
    return out, ""


def purge(project):
    """A deleted project's proposals and tickets go with it."""
    with _lock:
        d = _load()
        if any(x.get("project") == project for x in d["proposals"] + d["tickets"]):
            d = {k: [x for x in v if x.get("project") != project] for k, v in d.items()}
            _save(d)
