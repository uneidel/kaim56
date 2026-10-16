# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Squad requirements (security boundary: reads a file a VM writes): before the
plan, an ephemeral requirements engineer (the squad setting ``engineer``, made
by the kickoff for the architect — mgr/squadkick.step_engineer) interviews the
operator and keeps REQUIREMENTS.md at the top of its workspace; squads without
one have the lead do it. The operator agrees on the board: the lead gets the
document and plans (mgr/squadgate.propose refuses while drafting), the
engineer is retired (member removed, instance deleted).

State in the squad settings: ``requirements`` = "drafting" (a new kickoff) |
"agreed" (the default: squads from before keep working). The file is read
with O_NOFOLLOW as a regular file only, capped — the lead's VM can write that
folder, so a symlink must not show the manager's files on the board.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import os
import stat

from mgr import host as _host
from mgr import instances as _instances
from mgr import mounts as _mounts
from mgr import projects as _projects
from mgr import squadgate as _squadgate
from mgr import squads as _squads

FILE = "REQUIREMENTS.md"
MAX = 20000
SUFFIX = "-req"                # the engineer's instance: <squad>-req

INTERVIEW = (
    "Before any plan, work out the requirements with me — you are the requirements engineer now. "
    "Ask ONE question at a time (squad_post to me) until nothing important is open: the problem and goal, "
    "who uses it, what is in and what is out of scope, acceptance criteria (testable), constraints "
    "(platform, stack, security, budget), and risks. Propose answers when you can, so I only confirm. "
    f"Keep {FILE} at the top of your workspace current after every answer, with the sections Goal, Users, "
    "Scope, Out of scope, Acceptance criteria, Constraints, Open questions. Do not call squad_propose: "
    "it is refused until I mark the requirements agreed on the board.")
# what the ephemeral engineer is told on top (mgr/squadkick.step_post)
ENGINEER = ("You are the squad's requirements engineer, only for this phase: you write no code and plan "
            "nothing. When I mark the requirements agreed, the architect {arch} gets {file} and plans the "
            "squad; you are shut down then.")


def engineer(proj):
    """The requirements engineer's instance while it is a member, else ''."""
    e = _squads.settings(proj).get("engineer") or ""
    return e if e in (proj.get("members") or {}) else ""


def path(proj):
    who = engineer(proj) or _squadgate.lead(proj)
    return os.path.join(_mounts.AGENT_ROOT, who, FILE) if who else ""


def read(proj):
    """The engineer's (else the lead's) REQUIREMENTS.md (capped), '' when missing, a symlink or no regular file."""
    p = path(proj)
    if not p:
        return ""
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return ""
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return ""
        return os.read(fd, MAX).decode("utf-8", "replace")
    finally:
        os.close(fd)


def view(proj):
    return {"state": _squads.settings(proj).get("requirements", "agreed"), "text": read(proj), "file": FILE,
            "engineer": engineer(proj)}


def write_doc(inst, fname, text):
    """A document into an instance's workspace top (the agreed requirements, a long kickoff
    goal) — never through a link or into a non-directory the VM planted -> ok."""
    d = os.path.join(_mounts.AGENT_ROOT, inst)
    try:
        if not os.path.lexists(d):
            _mounts.own_guest_dir(d)
        if not stat.S_ISDIR(os.lstat(d).st_mode):
            return False
        fd = os.open(os.path.join(d, fname), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o664)
    except OSError as e:
        print(f"[quiet] {fname} to {inst}: {e!r}", flush=True)
        return False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
        os.write(fd, text.encode() + b"\n")
        if os.geteuid() == 0:
            os.fchown(fd, _mounts.GUEST_UID, _host.ADMIN_GID)
        return True
    finally:
        os.close(fd)


def _hand_over(proj, doc):
    """The agreed document into the lead's workspace -> ok."""
    ld = _squadgate.lead(proj)
    return bool(ld) and write_doc(ld, FILE, doc)


def retire(proj):
    """The engineer leaves: member removed, instance deleted, setting cleared -> its name or ''."""
    e = engineer(proj)
    if not e:
        return ""
    r = _projects.upsert({**proj, "members": {n: x.get("role") for n, x in (proj.get("members") or {}).items() if n != e}})
    if "error" in r:
        print(f"[quiet] requirements engineer {e}: {r['error']}", flush=True)
        return ""
    _squads.save_settings(proj["name"], {**_squads.settings(_projects.get(proj["name"])), "engineer": ""})
    _instances.delete_instance(e)
    return e


def agree(proj, who):
    """The operator agrees -> (view, '') or (None, why); the lead is told to plan now."""
    if _squads.settings(proj).get("requirements") != "drafting":
        return None, "the requirements are agreed already"
    doc = read(proj).strip()
    if not doc:
        return None, f"no {FILE} yet — it is written during the interview"
    eng = engineer(proj)
    _squads.save_settings(proj["name"], {**_squads.settings(proj), "requirements": "agreed"})
    p = _projects.get(proj["name"])
    team = _squads.settings(p).get("team")
    _squads.post(p, "user:" + who, [_squadgate.lead(p)],
                 f"Requirements agreed ({FILE} as it is now). Plan the squad now: {_squadgate.PLAN} "
                 "Every ticket carries the acceptance criteria it covers."
                 + (f"\nStaff the squad with exactly these roles (one member each unless a count is given; "
                    f"put the role in each member's purpose):\n{team}" if team else "")
                 + (f"\n{FILE} (written with @{eng}, who is shut down now) is at the top of your workspace."
                    if eng and _hand_over(p, doc) else ""))
    retire(_projects.get(proj["name"]))
    return view(_projects.get(proj["name"])), ""
