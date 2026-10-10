# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Skills library (expert knowledge the agent loads on demand) and skills from experience: proposals, lint, approval, session search.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import os
import re
import time
import uuid

from mgr import chats as _chats
from mgr import notify as _notify
from mgr import paths as _paths
from mgr import store as _store


# ---- Skills library (expert knowledge, loaded on demand by the agent) -------
SKILLS_FILE = os.path.join(_paths.BASE, "skills.json")


def load_skills():
    try:
        with open(SKILLS_FILE) as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
    except (FileNotFoundError, ValueError):
        pass
    return []


def save_skills(items):
    if not isinstance(items, list):
        return -1
    try:
        with open(SKILLS_FILE, "w") as fh:
            json.dump(items, fh, indent=2, ensure_ascii=False)
        return len(items)
    except OSError:
        return -1


def upsert_skill(name, description, content, default=None):
    """`default`: a standard skill — every agent is told about it each turn and
    loads it when it applies. None keeps the current flag."""
    name = re.sub(r"[^a-z0-9_-]", "", (name or "").lower())
    if not name:
        return "invalid name (only a-z 0-9 _ -)"
    prev = next((s for s in load_skills() if s.get("name") == name), {})
    items = [s for s in load_skills() if s.get("name") != name]
    entry = {"name": name, "description": description or "", "content": content or "",
             "added": prev.get("added") or int(time.time()),
             "category": prev.get("category") if prev.get("category") in CATEGORIES else guess_category(name, description)}
    if (prev.get("default") if default is None else default):
        entry["default"] = True
    items.append(entry)
    save_skills(items)
    return f"skill '{name}' saved"


def set_default(name, on):
    items = load_skills()
    sk = next((s for s in items if s.get("name") == name), None)
    if sk is None:
        return f"unknown skill '{name}'"
    sk.pop("default", None)
    if on:
        sk["default"] = True
    save_skills(items)
    return f"skill '{name}': {'standard' if on else 'on demand'}"


_dup_warned = set()


def unique(items):
    """The skills with each name once (the first) — the store may hold a
    duplicate; it is left as it is, a warning is logged once per name."""
    seen, out = set(), []
    for x in items:
        n = x.get("name", "")
        if n in seen:
            if n not in _dup_warned:
                _dup_warned.add(n)
                print(f"[skills] duplicate skill {n!r} in skills.json: the first one is used", flush=True)
            continue
        seen.add(n)
        out.append(x)
    return out


# ---- categories; security skills only on explicit opt-in --------------------
# A catalog with jailbreak / exploit / LPE material in a planning context trips
# the model vendors' cyber safeguards (live 2026-10-10), and most agents never
# need it: category "security" is hidden from guests unless the instance
# (config SECURITY_SKILLS=1) or one of its squads ("Security skills") opts in.
CATEGORIES = ("coding", "design", "devops", "data", "research", "writing", "meta", "personal", "security")
_RULES = (  # checked against the NAME first (first match wins), then the description
    ("security", r"secur|exploit|jailbreak|red.?team|social.?engineer|stack.?smash|\blpe\b|-lpe-|hacker|malware|"
                 r"pentest|osint|forbidden.tool|attack|vulnerab"),
    ("personal", r"job.?search|jobsuche|saddler|weekly.review"),
    ("devops", r"docker|kubernetes|nginx|linux|github|\bgit\b|git-|deploy|bootstrap|\bci\b"),
    ("data", r"\bdata\b|data-|postgres|\bsql\b|ml-engineer|analyst"),
    ("design", r"design|architecture|\badr\b|\bui\b|\bux\b"),
    ("coding", r"code|coding|tdd|test|standard|error|perf|scaffold|api|pbt|refactor|review|bench"),
    ("writing", r"naming|narrative|caveman|agents-md|\bdocs?\b|writ"),
    ("research", r"research|investigat|discovery|-eval|eval-|alpha(evolve|proof)|quantum|sequence|search"),
    ("meta", r"skill|prompt|\bagents?\b|agent-|claude|harness|karpathy|reasoning|approach|failure.modes|flinch|"
             r"qualia|matilda|dogfood|context|verification|rubric|problem"),
)
# In a description only unambiguous offensive terms make a skill "security"
# ("a code review that checks security" is a coding skill).
_DESC_SECURITY = r"jailbreak|exploit(ation)?\b|red.?team|social.?engineer|stack.?smash|privilege escalation|malware"


def guess_category(name, description=""):
    n, d = (name or "").lower(), (description or "").lower()
    hit = next((c for c, rx in _RULES if re.search(rx, n)), None)
    if hit:
        return hit
    if re.search(_DESC_SECURITY, d):
        return "security"
    return next((c for c, rx in _RULES[1:] if re.search(rx, d)), "coding")


def category(sk):
    c = (sk or {}).get("category")
    return c if c in CATEGORIES else guess_category((sk or {}).get("name", ""), (sk or {}).get("description", ""))


def security_allowed(inst):
    """May this guest see / load security skills? Its own SECURITY_SKILLS=1, or
    a squad it belongs to with 'Security skills' on."""
    if not inst:
        return False
    if str((inst.get("config") or {}).get("SECURITY_SKILLS") or "") == "1":
        return True
    from mgr import projects as _projects          # projects -> instances -> skills: imported late
    from mgr import squads as _squads
    return any(_squads.settings(p).get("enabled") and _squads.settings(p).get("security_skills")
               for p, _m in _projects.memberships(inst["name"]))


def visible(items, allowed):
    """The skills a caller may see: all, or all but category security."""
    return list(items) if allowed else [x for x in items if category(x) != "security"]


def set_category(name, cat):
    if cat not in CATEGORIES:
        return f"category must be one of: {', '.join(CATEGORIES)}"
    items = load_skills()
    hit = [s for s in items if s.get("name") == name]
    if not hit:
        return f"unknown skill '{name}'"
    for s in hit:
        s["category"] = cat
    save_skills(items)
    return f"skill '{name}': {cat}"


def migrate_categories():
    """Startup, once per skill: write the guessed category where none is
    stored (never over an operator's choice); a backup of the store first."""
    items = load_skills()
    todo = [s for s in items if s.get("category") not in CATEGORIES]
    if not todo:
        return 0
    bak = SKILLS_FILE + ".bak-categories"
    if not os.path.exists(bak):
        try:
            with open(SKILLS_FILE) as src, open(bak, "w") as dst:
                dst.write(src.read())
        except OSError:
            return 0
    for s in todo:
        s["category"] = guess_category(s.get("name", ""), s.get("description", ""))
    save_skills(items)
    return len(todo)


def meta(items=None):
    """name, description, default, category — what agents and the UI list (each name once)."""
    return [{"name": x.get("name", ""), "description": x.get("description", ""), "default": bool(x.get("default")),
             "category": category(x)}
            for x in unique(load_skills() if items is None else items)]


def names_error(value, security=True):
    """'' when every name of a comma list is in the catalog (and, without
    `security`, none is a security skill), else the complaint (config key
    SKILLS: the skills an instance gets in addition to the standard ones)."""
    items = load_skills()
    have = {s.get("name"): s for s in items}
    names = [n for n in (x.strip() for x in str(value or "").split(",")) if n]
    bad = [n for n in names if n not in have]
    if bad:
        return f"unknown skill(s): {', '.join(bad)}"
    sec = [n for n in names if not security and category(have[n]) == "security"]
    return (f"security skill(s) {', '.join(sec)} need SECURITY_SKILLS=1 (or the squad's 'Security skills')"
            if sec else "")


def of_instance(inst):
    """The names in the instance's SKILLS (SQUAD_SKILLS: squads phase 2, read as a fallback)."""
    cfg = (inst or {}).get("config") or {}
    return [x.strip() for x in str(cfg.get("SKILLS") or cfg.get("SQUAD_SKILLS") or "").split(",") if x.strip()]


def delete_skill(name):
    save_skills([s for s in load_skills() if s.get("name") != name])
    return f"skill '{name}' deleted"


# ---- skills from experience -------------------------------------------------
# After a long, successful turn the agent distills the way it went into a
# SKILL proposal (Hermes' learning loop, with our approval gate): it lands
# here, the operator approves it in the Skills tab, only then it enters the
# catalog every agent loads from. Nothing an agent writes becomes a skill by
# itself.
SKILL_PROPOSALS_FILE = os.path.join(_paths.BASE, "skill_proposals.json")
PROPOSALS_MAX = 50
REDRAW_DAYS = 30
_SECRETISH = re.compile(r"(sk-or-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9]{16,}|Bearer [A-Za-z0-9._-]{16,}|hf_[A-Za-z0-9]{16,})")


def load_proposals():
    try:
        with open(SKILL_PROPOSALS_FILE) as fh:
            d = json.load(fh)
        return d if isinstance(d, list) else []
    except (FileNotFoundError, ValueError):
        return []


def save_proposals(items):
    tmp = SKILL_PROPOSALS_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(items, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, SKILL_PROPOSALS_FILE)


def skill_lint(name, description, content):
    """'' when a proposal is acceptable, else why not. Advisory rules in the
    spirit of Hermes' linter: a usable name, a one-line description, a body
    that is a procedure and not a dump, nothing that looks like a credential."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,47}", name or ""):
        return "name must be 2-48 chars of a-z 0-9 _ -"
    if not (description or "").strip() or len(description) > 200:
        return "description must be one line (1-200 chars)"
    body = (content or "").strip()
    if len(body) < 80:
        return "content too short to be a procedure"
    if len(body) > 12000:
        return "content over 12 kB — a skill is a procedure, not a log"
    if _SECRETISH.search(body):
        return "content looks like it contains a credential"
    return ""


def proposal_add(instance, name, description, content, turn="", note=""):
    name = re.sub(r"[^a-z0-9_-]", "", str(name or "").lower())[:48]
    why = skill_lint(name, str(description or "").strip(), str(content or ""))
    if why:
        return None, why
    turned_down = discarded_recently(name)
    if turned_down:
        return None, (f"'{name}' was discarded {time.strftime('%Y-%m-%d', time.localtime(turned_down['decided']))}"
                      + (f" ({turned_down['reason']})" if turned_down.get("reason") else "")
                      + f" — not proposed again within {REDRAW_DAYS} days")
    items = [p for p in load_proposals() if not (p.get("name") == name and p.get("status") == "proposed")]
    pid = uuid.uuid4().hex[:10]
    items.append({"id": pid, "ts": int(time.time()), "instance": str(instance or "")[:80], "turn": str(turn or "")[:16],
                  "name": name, "description": str(description).strip()[:200], "content": str(content).strip(),
                  "note": str(note or "")[:200], "status": "proposed",
                  "update": any(s.get("name") == name for s in load_skills())})
    items = items[-PROPOSALS_MAX:]
    save_proposals(items)
    try:
        _notify.notify_add(instance or "skills", f"Skill proposal: {name}",
                   f"{str(description).strip()[:160]} — tap to review in the Skills tab", link="skills")
    except Exception:
        pass
    return pid, "ok"


def discarded_recently(name):
    """The newest discard of this name within REDRAW_DAYS, else None — an idea
    the operator turned down is not redrawn (RRSI: condition on the edit history)."""
    cut = time.time() - REDRAW_DAYS * 86400
    hits = [p for p in load_proposals() if p.get("name") == name and p.get("status") == "discarded"
            and (p.get("decided") or 0) >= cut]
    return max(hits, key=lambda p: p.get("decided") or 0) if hits else None


def proposal_history(limit=20):
    """For the distiller: the catalog's names and the recent discards with the reason."""
    disc = [p for p in load_proposals() if p.get("status") == "discarded"][-limit:]
    return {"skills": sorted(s.get("name", "") for s in load_skills()),
            "discarded": [{"name": p["name"], "description": p.get("description", ""),
                           "reason": p.get("reason", "")} for p in disc]}


def proposal_decide(pid, approve, reason=""):
    items = load_proposals()
    p = next((x for x in items if x.get("id") == pid), None)
    if p is None:
        return "unknown"
    if approve:
        msg = upsert_skill(p["name"], p.get("description", ""), p.get("content", ""))
        p["status"] = "approved"
    else:
        msg = f"proposal '{p['name']}' discarded"
        p["status"] = "discarded"
        p["reason"] = str(reason or "").strip()[:200]
    p["decided"] = int(time.time())
    save_proposals(items)
    return msg


def sessions_search(query, instance=None, limit=10):
    """Exact search over chats and task runs (FTS5), index refreshed from
    chats.json when it changed."""
    try:
        mt = os.path.getmtime(_chats.CHATS_FILE)
    except OSError:
        mt = 0
    _store.sessions_refresh(_chats.load_chats(), mt)
    return _store.sessions_query(query, instance=instance, limit=limit)


# ---- what a skill does (measured, not assumed) -----------------------------
# Each load_skill is recorded with its turn (store.skill_use); the turn's
# outcome and tokens say whether the skill helps. Below SKILL_MIN_USES loads
# nothing is judged — a couple of turns is noise, not evidence.
SKILL_WINDOW_DAYS = 30          # = the turns' retention (store.turns_prune)
SKILL_MIN_USES = 5


def skill_stats(now=None):
    """{name: {uses, ok, failed, unknown, instances, last, load_tokens,
    turn_tokens, verdict, why}} over the last SKILL_WINDOW_DAYS."""
    now = int(now or time.time())
    since = now - SKILL_WINDOW_DAYS * 86400
    rows = _store.skill_use_rows(since)
    skills = load_skills()
    if any(not s.get("added") for s in skills):
        # older than the measurement: the clock starts now, not "unused since forever"
        for s in skills:
            s.setdefault("added", now)
            s["added"] = s["added"] or now
        save_skills(skills)
    out = {}
    for s in skills:
        name = s.get("name", "")
        mine = [r for r in rows if r[0] == name]
        ok = sum(1 for r in mine if r[4] == "ok")
        known = [r for r in mine if r[4]]
        failed = len(known) - ok
        toks = sorted(r[5] for r in mine if r[5])
        st = {"uses": len(mine), "ok": ok, "failed": failed, "unknown": len(mine) - len(known),
              "instances": sorted({r[1] for r in mine}), "last": max((r[3] for r in mine), default=0),
              "load_tokens": len(s.get("content", "")) // 4,
              "turn_tokens": toks[len(toks) // 2] if toks else 0}
        young = (s.get("added") or 0) > since
        if not mine:
            st["verdict"], st["why"] = ("new", "not loaded yet") if young else \
                ("unused", f"not loaded in {SKILL_WINDOW_DAYS} days — a candidate to delete")
        elif len(known) < SKILL_MIN_USES:
            st["verdict"], st["why"] = "few", f"{len(known)} judged loads — at least {SKILL_MIN_USES} before a verdict"
        elif ok * 2 < len(known):
            st["verdict"], st["why"] = "failing", f"{failed} of {len(known)} turns that loaded it did not end well"
        else:
            st["verdict"], st["why"] = "working", f"{ok} of {len(known)} turns that loaded it ended well"
        out[name] = st
    return out

