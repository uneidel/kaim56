# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Squad requirements (security boundary: reads a file a VM writes): before the
plan, the lead interviews the operator and keeps REQUIREMENTS.md at the top of
its workspace; the operator agrees on the board, only then may the lead
propose the squad (mgr/squadgate.propose refuses while drafting).

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

from mgr import mounts as _mounts
from mgr import projects as _projects
from mgr import squadgate as _squadgate
from mgr import squads as _squads

FILE = "REQUIREMENTS.md"
MAX = 20000

INTERVIEW = (
    "Before any plan, work out the requirements with me — you are the requirements engineer now. "
    "Ask ONE question at a time (squad_post to me) until nothing important is open: the problem and goal, "
    "who uses it, what is in and what is out of scope, acceptance criteria (testable), constraints "
    "(platform, stack, security, budget), and risks. Propose answers when you can, so I only confirm. "
    f"Keep {FILE} at the top of your workspace current after every answer, with the sections Goal, Users, "
    "Scope, Out of scope, Acceptance criteria, Constraints, Open questions. Do not call squad_propose: "
    "it is refused until I mark the requirements agreed on the board.")


def path(proj):
    ld = _squadgate.lead(proj)
    return os.path.join(_mounts.AGENT_ROOT, ld, FILE) if ld else ""


def read(proj):
    """The lead's REQUIREMENTS.md (capped), '' when missing, a symlink or no regular file."""
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
    return {"state": _squads.settings(proj).get("requirements", "agreed"), "text": read(proj), "file": FILE}


def agree(proj, who):
    """The operator agrees -> (view, '') or (None, why); the lead is told to plan now."""
    if _squads.settings(proj).get("requirements") != "drafting":
        return None, "the requirements are agreed already"
    if not read(proj).strip():
        return None, f"no {FILE} yet — the lead writes it while it interviews you"
    _squads.save_settings(proj["name"], {**_squads.settings(proj), "requirements": "agreed"})
    p = _projects.get(proj["name"])
    team = _squads.settings(p).get("team")
    _squads.post(p, "user:" + who, [_squadgate.lead(p)],
                 f"Requirements agreed ({FILE} as it is now). Plan the squad now: {_squadgate.PLAN} "
                 "Every ticket carries the acceptance criteria it covers."
                 + (f"\nStaff the squad with exactly these roles (one member each unless a count is given; "
                    f"put the role in each member's purpose):\n{team}" if team else ""))
    return view(p), ""
