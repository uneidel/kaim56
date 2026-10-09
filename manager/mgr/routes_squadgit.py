# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""HTTP routes of the squad git remote (mgr/squadgit.py), in one place:

- guest: ``POST /api/git/<op>`` — push, fetch, pr-create, prs, pr,
  pr-comment, issue-create, issues, issue-comment; the caller is the VM by
  source IP and must be a member of the project; every call is audited.
- admin: ``GET/POST /api/squad-git/<project>`` (the remote settings; the
  token secret's value never leaves the host) and ``POST …/<project>/fetch``.
  Role "user" (OIDC) is refused: default deny in mgr/auth.py.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import urllib.parse

from mgr import audit as _audit
from mgr import policy as _policy
from mgr import projects as _projects
from mgr import routes as _routes
from mgr import secrets as _secrets
from mgr import squadgit as _squadgit
from mgr import util as _util


@_routes.ROUTER.post("/api/git/", prefix=True)
def _rt_git_guest(h):
    op = h.path.split("?", 1)[0][len("/api/git/"):].strip("/")
    inst = h._guest()
    if inst is None:
        return h._json({"error": "guests only"}, 403)
    tool = _squadgit.TOOLS.get(op)
    if tool is None:
        return h._json({"error": "unknown operation"}, 404)
    if not _policy.tool_allowed(inst, tool):
        return h._json({"error": f"{tool} not allowed for this instance"}, 403)
    if not _util.rate_ok(("squadgit", inst["name"]), 60, 300):
        return h._json({"error": "rate limit: 60 git/PR/issue calls per 5 minutes"}, 429)
    b = h._body() or {}
    try:
        r, project, repo, num = _squadgit.call(inst["name"], op, b)
    except _squadgit.Refused as e:
        _audit.audit_append(inst["name"], tool, f"{b.get('project') or ''} {b.get('number') or ''}".strip(), False,
                            err=str(e))
        return h._json({"error": str(e)}, e.status)
    _audit.audit_append(inst["name"], tool, f"{project} {repo}" + (f" #{num}" if num else ""), True,
                        result=r.get("url") or r.get("branch") or r.get("note") or "")
    return h._json({"project": project, "repo": repo, **r})


def _name(h):
    parts = urllib.parse.unquote(h.path.split("?", 1)[0])[len("/api/squad-git/"):].strip("/").split("/")
    return (parts + [""])[:2]


@_routes.ROUTER.get("/api/squad-git/", prefix=True, admin=True)
def _rt_squadgit_get(h):
    name, _sub = _name(h)
    p = _projects.get(name)
    if p is None:
        return h._json({"error": "unknown project"}, 404)
    return h._json({"project": name, "remote": _squadgit.settings(p), "defaults": _squadgit.DEFAULTS,
                    "providers": list(_squadgit.PROVIDERS), "secrets": sorted(_secrets.load_secrets_file()),
                    "strategy": p.get("strategy"), "created": _squadgit.created(name)})


@_routes.ROUTER.post("/api/squad-git/", prefix=True, admin=True)
def _rt_squadgit_post(h):
    # POST /api/squad-git/<p> {provider,url,token_secret,base,label,daily_limit} | {"remove": true}
    #      /api/squad-git/<p>/fetch
    name, sub = _name(h)
    p = _projects.get(name)
    if p is None:
        return h._json({"error": "unknown project"}, 404)
    if sub == "fetch":
        cfg = _squadgit.settings(p)
        if not cfg:
            return h._json({"error": "no git remote set"}, 400)
        try:
            return h._json(_squadgit.fetch(p, cfg))
        except _squadgit.Refused as e:
            return h._json({"error": str(e)}, e.status)
    if sub:
        return h._json({"error": "unknown operation"}, 404)
    b = h._body() or {}
    if b.get("remove"):
        _projects.set_remote(name, None)
        return h._json({"ok": True, "remote": None})
    r, why = _squadgit.normalize(b)
    if r is None:
        return h._json({"error": why}, 400)
    _projects.set_remote(name, r)
    return h._json({"ok": True, "remote": r})
