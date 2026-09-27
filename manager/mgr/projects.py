# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Projects (security boundary): one folder set, named once, joined by several
instances with a role — lead, writer, reader. Design: docs/design-projects.md.

A host-folder project rides the host folder mechanism of mgr/mounts.py
(per-VM-IP NFS export, the guest's 5-second reconciler, live join/leave) and
appears at /project/<name>:

- ``shared``: the one folder — read-write for writers, read-only for the lead
  and readers. A katfs project (shared only) is the instance's katfs share when
  its config names none (mgr/katfs.py); only writers may write.
- ``worktree`` (a git repo): each writer gets its own folder on its own branch
  (mgr/projwt.py); lead and readers see the source read-only, the lead also
  every writer's folder at /project/<name>.members/<writer>. Diff, merge,
  discard: the operator, or the lead when the project allows it.

State: projects.json in the manager dir (root-only, like the other stores).
Membership lives in the project, never in the instance config.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import os
import re
import subprocess
import threading

from mgr import instances as _instances
from mgr import mounts as _mounts
from mgr import paths as _paths
from mgr import projwt as _projwt

PROJECTS_FILE = os.path.join(_paths.BASE, "projects.json")
STRATEGIES = ("shared", "worktree")
PLANNED = ("overlay",)                     # design phase 4
ROLES = ("lead", "writer", "reader")
MAX_PER_INSTANCE = 64                      # fsid slots per instance (mounts.PROJECT_FSID)
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
SHARE_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_lock = threading.Lock()


def guest_path(name, member=""):
    return f"/project/{name}.members/{member}" if member else f"/project/{name}"


def load():
    try:
        with open(PROJECTS_FILE) as fh:
            data = json.load(fh)
        return [p for p in data if isinstance(p, dict)] if isinstance(data, list) else []
    except (FileNotFoundError, ValueError):
        return []
    except OSError as e:          # root-only; only a non-root caller (a test) lands here
        print(f"[quiet] projects.json unreadable: {e!r}", flush=True)
        return []


def _save(items):
    tmp = PROJECTS_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(items, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, PROJECTS_FILE)


def source_dir(p):
    """The host folder a host project shares (path + subdir), '' for katfs."""
    src = p.get("source") or {}
    if src.get("type") != "host":
        return ""
    return os.path.join(src.get("path", ""), src.get("subdir", "")).rstrip("/") or "/"


def _normalize(b, others):
    """(project, '') or (None, why). `others` = the other projects, for the
    per-instance limits (fsid slots, one katfs share)."""
    name = str(b.get("name") or "").strip().lower()
    if not NAME_RE.match(name):
        return None, "name: a-z 0-9 _ -, at most 32 characters"
    strategy = str(b.get("strategy") or "shared")
    if strategy in PLANNED:
        return None, f"strategy '{strategy}' is not built yet"
    if strategy not in STRATEGIES:
        return None, f"strategy must be one of {', '.join(STRATEGIES)}"
    s = b.get("source") if isinstance(b.get("source"), dict) else {}
    if s.get("type") == "host":
        path = os.path.realpath(str(s.get("path") or "").strip() or "/nonexistent")
        sub = os.path.normpath(str(s.get("subdir") or "").strip().strip("/") or ".")
        if sub.startswith("..") or os.path.isabs(sub):
            return None, "subdir must be a path inside the source folder"
        source = {"type": "host", "path": path, "subdir": "" if sub == "." else sub}
        why = _mounts.mount_error(os.path.join(path, source["subdir"]), guest_path(name))
        if why:
            return None, why
    elif s.get("type") == "katfs":
        share = str(s.get("share") or "").strip()
        if not SHARE_RE.match(share):
            return None, "katfs source needs a share id"
        source = {"type": "katfs", "share": share}
    else:
        return None, "source.type must be 'host' or 'katfs'"
    known = {i["name"] for i in _instances.load_instances()}
    members = {}
    raw = b.get("members") if isinstance(b.get("members"), dict) else {}
    for inst, m in raw.items():
        role = str((m or {}).get("role") if isinstance(m, dict) else m)
        if inst not in known:
            return None, f"unknown instance {inst!r}"
        if role not in ROLES:
            return None, f"{inst}: role must be one of {', '.join(ROLES)}"
        members[inst] = {"role": role}
    if sum(m["role"] == "lead" for m in members.values()) > 1:
        return None, "at most one lead per project"
    base = str(b.get("base") or "").strip()
    if strategy == "worktree":
        if source["type"] != "host":
            return None, "worktree needs a host folder (a git repository) — katfs works with shared only"
        why = _projwt.check_repo(source["path"], base, source["subdir"])
        if why:
            return None, why
        base = base or _projwt.current_branch(source["path"])
    proj = {"name": name, "strategy": strategy, "source": source, "members": members,
            "lead_may_merge": bool(b.get("lead_may_merge"))}
    if strategy == "worktree":
        proj["base"] = base
    for inst in members:
        mine = [o for o in others if inst in (o.get("members") or {})]
        if source["type"] == "katfs" and any((o.get("source") or {}).get("type") == "katfs" for o in mine):
            return None, f"{inst} is already in a katfs project — an instance has one katfs share"
    return proj, ""


def _mount_keys(proj, inst):
    """What the member sees, one fsid slot each: '' = /project/<name>, a
    writer's name = the lead's view of that writer's folder."""
    if (proj.get("source") or {}).get("type") != "host":
        return []
    m = proj["members"][inst]
    if proj.get("strategy") == "worktree" and m.get("role") == "lead":
        return [""] + sorted(w for w, x in proj["members"].items() if x.get("role") == "writer")
    return [""]


def _assign_slots(proj, old, others):
    """Stable fsid slots: a key keeps its slot across saves (a changed fsid
    under a mounted folder is a stale NFS handle in the running guest)."""
    for inst, m in proj["members"].items():
        prev = (((old or {}).get("members") or {}).get(inst) or {}).get("slots") or {}
        used = {n for o in others for n in (((o.get("members") or {}).get(inst) or {}).get("slots") or {}).values()}
        slots = {}
        for key in _mount_keys(proj, inst):
            if key in prev and prev[key] not in used:
                slots[key] = prev[key]
        for key in _mount_keys(proj, inst):
            if key not in slots:
                free = [k for k in range(MAX_PER_INSTANCE) if k not in used and k not in slots.values()]
                if not free:
                    return f"{inst} has {MAX_PER_INSTANCE} project folders already"
                slots[key] = free[0]
        m["slots"] = slots
    return ""


def writers(proj):
    return sorted(n for n, m in (proj or {}).get("members", {}).items() if m.get("role") == "writer")


def _trees(proj):
    return set(writers(proj)) if proj and proj.get("strategy") == "worktree" else set()


def _apply(names, before):
    """Re-export the folders of the running instances whose membership changed."""
    for inst in _instances.load_instances():
        if inst["name"] in names and _instances.is_running(inst):
            _mounts.apply_live(inst, before.get(inst["name"], []))


def _affected(*projects):
    return {n for p in projects if p for n in (p.get("members") or {})}


def upsert(body):
    """Create or replace a project. {'ok': True, 'project': …, 'warn': …} or {'error': why}."""
    with _lock:
        items = load()
        name = str((body or {}).get("name") or "").strip().lower()
        old = next((p for p in items if p.get("name") == name), None)
        rest = [p for p in items if p.get("name") != name]
        proj, why = _normalize(body or {}, rest)
        if not proj:
            return {"error": why}
        why = _assign_slots(proj, old, rest)
        if why:
            return {"error": why}
        old_trees = _trees(old) if old and old.get("source") == proj["source"] else set()
        try:
            for w in sorted(_trees(proj) - old_trees):      # before the export: it skips missing folders
                _projwt.ensure_tree(proj, w)
        except (_projwt.GitError, OSError, subprocess.SubprocessError) as e:
            return {"error": f"could not create a writer's folder: {e}"}
        names = _affected(old, proj)
        before = {i["name"]: _mounts.mount_specs(i) for i in _instances.load_instances() if i["name"] in names}
        _save(rest + [proj])
    _apply(names, before)
    for w in (_trees(old) - _trees(proj)) if old else ():
        _projwt.remove_tree(name, w)                         # after the export is gone; the branch stays
    warn = ""
    if proj["strategy"] == "shared" and proj["source"]["type"] == "host" and writers(proj):
        _mounts.ensure_guest_user()
        if not _mounts.guest_can_write(source_dir(proj)):
            warn = f"read-only for the writers until {_mounts.GUEST_USER} may write in {source_dir(proj)}"
    return {"ok": True, "project": proj, "warn": warn}


def delete(name):
    with _lock:
        items = load()
        old = next((p for p in items if p.get("name") == name), None)
        if not old:
            return {"error": "unknown project"}
        names = _affected(old)
        before = {i["name"]: _mounts.mount_specs(i) for i in _instances.load_instances() if i["name"] in names}
        _save([p for p in items if p.get("name") != name])
    _apply(names, before)
    if old.get("strategy") == "worktree":
        _projwt.remove_project(name)                         # the branches stay in the repo
    return {"ok": True}


def get(name):
    return next((p for p in load() if p.get("name") == name), None)


def memberships(inst_name):
    """[(project, member)] of one instance, sorted by project name."""
    out = [(p, p["members"][inst_name]) for p in load() if inst_name in (p.get("members") or {})]
    return sorted(out, key=lambda t: t[0].get("name", ""))


def host_mounts(inst_name):
    """The host-folder mounts of an instance's projects, for mounts.mount_specs."""
    out = []
    for p, m in memberships(inst_name):
        slots = m.get("slots") or {}
        for key in _mount_keys(p, inst_name):
            if key:                                           # the lead's view of a writer
                host, ro = _projwt.tree_dir(p["name"], key), True
            elif p.get("strategy") == "worktree" and m.get("role") == "writer":
                host, ro = _projwt.tree_dir(p["name"], inst_name), False
            else:
                host, ro = source_dir(p), m.get("role") != "writer"
            out.append({"project": p["name"], "key": key, "host": host, "guest": guest_path(p["name"], key),
                        "ro": ro, "slot": int(slots.get(key, 0)) % MAX_PER_INSTANCE})
    return out


def katfs_membership(inst_name):
    """(share, writable) of the instance's katfs project, or ('', False)."""
    for p, m in memberships(inst_name):
        if (p.get("source") or {}).get("type") == "katfs":
            return p["source"]["share"], m.get("role") == "writer"
    return "", False


# ---- review: status, diff, merge, discard (operator, or the lead) ----------
MEMBER_OPS = {"status": _projwt.status, "diff": _projwt.diff, "merge": _projwt.merge, "discard": _projwt.discard}


def member_op(name, op, member):
    """One review operation on a writer of a worktree project -> (result, http status)."""
    p = get(name)
    if not p:
        return {"error": "unknown project"}, 404
    if p.get("strategy") != "worktree":
        return {"error": "review works on worktree projects"}, 400
    if member not in writers(p):
        return {"error": f"{member!r} is no writer of {name}"}, 404
    try:
        r = MEMBER_OPS[op](p, member)
    except (_projwt.GitError, OSError, subprocess.SubprocessError) as e:
        return {"error": str(e)}, 500
    if isinstance(r, dict) and "error" in r:
        return r, 409
    return r, 200


def review(name):
    """Every writer's status of one project -> {member: status | {'error'}}."""
    p = get(name)
    out = {}
    for w in writers(p) if p and p.get("strategy") == "worktree" else []:
        out[w] = member_op(name, "status", w)[0]
    return out


def lead_of(inst_name):
    """The worktree projects this instance leads."""
    return [p for p, m in memberships(inst_name) if m.get("role") == "lead" and p.get("strategy") == "worktree"]
