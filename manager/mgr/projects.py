# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Projects (security boundary): one folder set, named once, joined by several
instances with a role — lead, writer, reader. Design: docs/design-projects.md.

Built so far: the ``shared`` strategy. A host-folder project rides the host
folder mechanism of mgr/mounts.py (per-VM-IP NFS export, the guest's 5-second
reconciler, live join/leave); it is mounted at /project/<name>, read-write for
writers, read-only for the lead and readers. A katfs project is the instance's
katfs share when its config names none (mgr/katfs.py); only writers may write.

State: projects.json in the manager dir (root-only, like the other stores).
Membership lives in the project, never in the instance config.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import os
import re
import threading

from mgr import instances as _instances
from mgr import mounts as _mounts
from mgr import paths as _paths

PROJECTS_FILE = os.path.join(_paths.BASE, "projects.json")
STRATEGIES = ("shared",)                   # worktree, overlay: design phases 3/4
PLANNED = ("worktree", "overlay")
ROLES = ("lead", "writer", "reader")
MAX_PER_INSTANCE = 16                      # one fsid slot each (mounts.PROJECT_FSID)
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
SHARE_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_lock = threading.Lock()


def guest_path(name):
    return f"/project/{name}"


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
        return None, f"strategy '{strategy}' is not built yet — only 'shared' for now"
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
    for inst, m in members.items():
        mine = [o for o in others if inst in (o.get("members") or {})]
        if source["type"] == "katfs" and any((o.get("source") or {}).get("type") == "katfs" for o in mine):
            return None, f"{inst} is already in a katfs project — an instance has one katfs share"
        used = {(o["members"][inst] or {}).get("slot") for o in mine}
        free = [k for k in range(MAX_PER_INSTANCE) if k not in used]
        if not free:
            return None, f"{inst} is in {MAX_PER_INSTANCE} projects already"
        m["slot"] = free[0]
    return {"name": name, "strategy": strategy, "source": source, "members": members,
            "lead_may_merge": bool(b.get("lead_may_merge"))}, ""


def _keep_slots(new, old):
    """A member keeps its fsid slot across saves: a changed fsid under a
    mounted folder is a stale NFS handle in the running guest."""
    for inst, m in new["members"].items():
        prev = ((old or {}).get("members") or {}).get(inst)
        if prev and "slot" in prev:
            m["slot"] = prev["slot"]


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
        _keep_slots(proj, old)
        names = _affected(old, proj)
        before = {i["name"]: _mounts.mount_specs(i) for i in _instances.load_instances() if i["name"] in names}
        _save(rest + [proj])
    _apply(names, before)
    warn = ""
    if proj["source"]["type"] == "host" and any(m["role"] == "writer" for m in proj["members"].values()):
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
    return {"ok": True}


def memberships(inst_name):
    """[(project, member)] of one instance, sorted by project name."""
    out = [(p, p["members"][inst_name]) for p in load() if inst_name in (p.get("members") or {})]
    return sorted(out, key=lambda t: t[0].get("name", ""))


def host_mounts(inst_name):
    """The host-folder projects of an instance, for mounts.mount_specs."""
    return [{"project": p["name"], "host": source_dir(p), "guest": guest_path(p["name"]),
             "ro": m.get("role") != "writer", "slot": int(m.get("slot", 0)) % MAX_PER_INSTANCE}
            for p, m in memberships(inst_name) if (p.get("source") or {}).get("type") == "host"]


def katfs_membership(inst_name):
    """(share, writable) of the instance's katfs project, or ('', False)."""
    for p, m in memberships(inst_name):
        if (p.get("source") or {}).get("type") == "katfs":
            return p["source"]["share"], m.get("role") == "writer"
    return "", False
