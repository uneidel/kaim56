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
             "added": prev.get("added") or int(time.time())}
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


def meta(items=None):
    """name, description, default — what agents and the UI list."""
    return [{"name": x.get("name", ""), "description": x.get("description", ""), "default": bool(x.get("default"))}
            for x in (load_skills() if items is None else items)]


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

