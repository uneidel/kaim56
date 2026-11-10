# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""HTTP routes of the squad git remote (mgr/squadgit.py), in one place:

- guest: ``POST /api/git/<op>`` — push, fetch, pr-create, prs, pr,
  pr-comment, issue-create, issues, issue-comment; the caller is the VM by
  source IP and must be a member of the project; every call is audited.
- admin: ``GET/POST /api/squad-git/<project>`` (the remote settings; the
  token secret's value never leaves the host) and ``POST …/<project>/fetch``;
  ``GET …/<project>/graph`` and ``…/commit?sha=`` read the repo (the Squad graph);
  ``GET …/<project>/pr-draft?member=`` (title + body from the local LLM, else
  from the ticket and commits, plus the diff) and ``POST …/<project>/pr``
  {member, title, body}: the operator opens a PR from a member's branch.
  Role "user" (OIDC) is refused: default deny in mgr/auth.py.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import os
import urllib.parse

from mgr import audit as _audit
from mgr import decision as _decision
from mgr import hitlq as _hitlq
from mgr import policy as _policy
from mgr import projects as _projects
from mgr import projwt as _projwt
from mgr import routes as _routes
from mgr import secrets as _secrets
from mgr import squadgate as _squadgate
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
    # Human in the loop, enforced HERE (the VM's hook is only a convenience):
    # a gated call waits for an approval of exactly this call.
    target = f"{op}: " + " ".join(str(b.get(k) or "") for k in ("project", "name", "title", "number") if b.get(k))
    try:
        gproj = _squadgit.resolve(inst["name"], b.get("project"))[0]
    except _squadgit.Refused:
        gproj = None                           # call() below refuses it with the right status
    wait = _hitlq.server_gate(inst["name"], tool, target.strip(), gproj)
    if wait is not None:
        return h._json({"error": f"waiting for approval {wait['id']} — end your turn; you will be woken with the decision",
                        "waiting": wait["id"]}, 403)
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
    name, sub = _name(h)
    p = _projects.get(name)
    if p is None:
        return h._json({"error": "unknown project"}, 404)
    if sub == "pr-draft":
        member = urllib.parse.parse_qs(urllib.parse.urlsplit(h.path).query).get("member", [""])[0]
        if member not in _projects.writers(p) or not _squadgit.settings(p):
            return h._json({"error": "a writer of a project with a git remote"}, 400)
        try:
            return h._json(_pr_draft(p, member))
        except _projwt.GitError as e:
            return h._json({"error": str(e)}, 400)
    if sub in ("graph", "commit"):
        # GET /api/squad-git/<p>/graph | /commit?sha=<hex>: the repo read, nothing written
        src = p.get("source") or {}
        if p.get("strategy") != "worktree" or src.get("type") != "host" or not os.path.isdir(os.path.join(src.get("path") or "/nonexistent", ".git")):
            return h._json({"error": "the graph needs a worktree project on a host git repository"}, 400)
        try:
            if sub == "commit":
                sha = urllib.parse.parse_qs(urllib.parse.urlsplit(h.path).query).get("sha", [""])[0]
                return h._json(_projwt.commit(p, sha))
            g = _projwt.graph(p)
        except _projwt.GitError as e:
            return h._json({"error": str(e)}, 400)
        doing = {}
        for t in sorted(_squadgate.tickets(name), key=lambda t: t.get("status") == "doing"):
            if t.get("status") != "done" and t.get("assignee"):
                doing[t["assignee"]] = {"key": t["key"], "title": t["title"][:120], "status": t["status"]}
        for b in g["branches"]:
            b["ticket"] = doing.get(b["member"])
            b["folder"] = _projwt.tree_dir(name, b["member"])
        return h._json({**g, "remote": bool(_squadgit.settings(p))})
    return h._json({"project": name, "remote": _squadgit.settings(p), "defaults": _squadgit.DEFAULTS,
                    "providers": list(_squadgit.PROVIDERS), "secrets": sorted(_secrets.load_secrets_file()),
                    "strategy": p.get("strategy"), "created": _squadgit.created(name)})


PR_SYSTEM = ("You write the pull request for a branch. Answer with only a JSON object "
             '{"title": one line, at most 72 characters, "body": markdown — what changed and why, then a short list '
             'of the main changes}. Use only facts from the ticket, the commits and the diff.')


def _pr_draft(p, member):
    """{title, body, diff, by}: the local LLM writes title and body; without it
    (or when it fails) the member's ticket and commit subjects do."""
    diff = _projwt.diff(p, member)                 # snapshots the folder first
    subjects = _projwt.git(p["source"]["path"], ["log", "--format=%s", "-n", "50", f"refs/heads/{p['base']}.."
                                                 f"refs/heads/{_projwt.branch(p['name'], member)}"]).splitlines()
    mine = [t for t in _squadgate.tickets(p["name"]) if t.get("assignee") == member]
    t = next((x for x in mine if x.get("status") != "done"), mine[-1] if mine else None)
    title = f"{t['key']}: {t['title']}" if t else (subjects[0] if subjects else f"{member}'s work")
    body = ((t.get("description") or "").strip() + "\n\n" if t else "") + "Commits:\n" + "\n".join(f"- {s}" for s in subjects)
    by = "ticket and commits"
    try:
        d = _decision._json_of(_decision.write(PR_SYSTEM, json.dumps(
            {"ticket": {k: t.get(k) for k in ("key", "title", "description")} if t else None,
             "commits": subjects, "diff": diff[:40000]}, ensure_ascii=False)))
        if str(d.get("title") or "").strip() and str(d.get("body") or "").strip():
            title, body, by = str(d["title"]).strip(), str(d["body"]).strip(), "local LLM"
    except Exception as e:                       # the template stands
        by += f" (local LLM: {_decision.safe_err(e)})"
    return {"title": title[:_squadgit.TITLE_MAX], "body": body[:_squadgit.BODY_MAX], "diff": diff, "by": by}


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
    if sub == "pr":
        b = h._body() or {}
        cfg, member = _squadgit.settings(p), str(b.get("member") or "")
        if not cfg:
            return h._json({"error": "no git remote set"}, 400)
        try:
            title = _squadgit._text(b.get("title"), _squadgit.TITLE_MAX, "title")
            body = (str(b.get("body") or "")[:_squadgit.BODY_MAX]
                    + f"\n\n---\n_Opened by the operator in kAIm56 from `{member}`'s branch (project `{name}`)._")
            r = _squadgit.open_pr(p, cfg, _squadgit._token(cfg), member, "", title, body, "operator")
        except _squadgit.Refused as e:
            _audit.audit_append("operator", "pr_create", f"{name} {member}", False, err=str(e))
            return h._json({"error": str(e)}, e.status)
        _audit.audit_append("operator", "pr_create", f"{name} {member} #{r.get('number')}", True, result=r.get("url") or "")
        return h._json(r)
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
    for t in _squadgate.tickets(name):              # the board so far -> issues (known ones: updated)
        _squadgit.mirror_ticket(name, t)
    return h._json({"ok": True, "remote": r})
