# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""HTTP routes of the Squads page (mgr/squadkick.py):

- ``GET /api/squads`` — the squad-enabled projects (also role "user": it
  reads the channel and posts through the existing /api/projects/<p>/squad/
  routes).
- admin only (default deny in mgr/auth.py): ``POST /api/squads/kickoff``
  {text, architect_template?: openrouter|claude}, ``GET /api/squads/kickoff`` (the jobs), ``GET …/kickoff/<id>`` (one,
  polled), ``POST …/kickoff/<id>/retry``.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
from mgr import audit as _audit
from mgr import routes as _routes
from mgr import settings as _settings
from mgr import squadkick as _squadkick

_KICK = "/api/squads/kickoff"


def _who(h):
    pr = getattr(h, "principal", None)
    return pr.name if pr is not None and pr.sub != "basic" else "admin"


@_routes.ROUTER.get("/api/squads", admin=True)
def _rt_squads(h):
    return h._json({"squads": _squadkick.overview(), "index_host": _settings.INDEX_HOST})


@_routes.ROUTER.get(_KICK, prefix=True, admin=True)
def _rt_kick_get(h):
    jid = h.path.split("?", 1)[0][len(_KICK):].strip("/")
    if not jid:
        return h._json({"jobs": _squadkick.jobs()})
    j = _squadkick.get(jid)
    return h._json(j) if j else h._json({"error": "unknown kickoff"}, 404)


@_routes.ROUTER.post(_KICK, prefix=True, admin=True)
def _rt_kick_post(h):
    rest = h.path.split("?", 1)[0][len(_KICK):].strip("/")
    b = h._body() or {}
    who = _who(h)
    if not rest:
        j, why = _squadkick.start(b.get("text"), who, b.get("architect_template"))
    elif rest.endswith("/retry"):
        j, why = _squadkick.retry(rest[:-len("/retry")])
    else:
        return h._json({"error": "unknown operation"}, 404)
    if not j:
        return h._json({"error": why}, 400)
    _audit.audit_append(j["arch"], "squad_kickoff", f"{j['url']} by {who}"[:400], True, result=j["status"])
    return h._json(j)
