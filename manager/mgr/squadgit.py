# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Squad git remote (security boundary): a project's members push their
branches and open PRs and issues on GitHub or Gitea — every remote operation
runs HERE, on the host, with a token from the host secret store that never
enters a VM. Design: docs/plans/2026-10-09-squad-git-design.md.

- Settings live in the project (projects.json, key ``remote``; admin only):
  provider, https repo URL, the NAME of the token secret, base branch, label,
  per-member daily limit for PR/issue creation. The token secret is reserved:
  mgr/secrets.py never releases it to an instance.
- git: ``push_branch`` snapshots the writer's worktree branch (mgr/projwt.py)
  and pushes it to ``squad/<writer>/<name>`` on the remote (force: that
  namespace is the squad's own; the base and anything outside the prefix are
  refused). ``fetch`` updates ``refs/remotes/kaim56/*`` in the project repo
  and nothing else. The token rides an ``http.extraHeader`` in the
  environment (GIT_CONFIG_*), never argv, the stored remote URL, or a log.
- API (stdlib urllib, no redirects): PRs and issues — create, list, view,
  comment; the label is added (created when missing) and may fail quietly.
  Merging is NOT offered: it stays with the user (or the squad merge gate).

State: squadgit.json in the manager dir (root-only): the PRs/issues the squad
created (the Squad panel's list, the daily limit).

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import base64
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from mgr import paths as _paths
from mgr import projects as _projects
from mgr import projwt as _projwt
from mgr import secrets as _secrets
from mgr import squadmetrics as _squadmetrics

STATE_FILE = os.path.join(_paths.BASE, "squadgit.json")
PROVIDERS = ("github", "gitea")
DEFAULTS = {"provider": "github", "url": "", "token_secret": "", "base": "main", "label": "kaim56-squad",
            "daily_limit": 20}
GIT_TIMEOUT, API_TIMEOUT = 120, 20
TITLE_MAX, BODY_MAX, COMMENT_SHOW = 200, 20000, 20
KEEP = 500                                  # created PRs/issues remembered
TOKEN_PREFIX = "SQUAD_GIT_TOKEN_"           # the kickoff's per-host tokens (mgr/squadkick.py)
REMOTE_NS = "refs/remotes/kaim56"           # where fetch puts the remote's branches
FILE_PROTOCOL = "never"                     # the tests' local bare "remote" sets "always"
URL_RE = re.compile(r"^https://([A-Za-z0-9.-]{1,253}(?::\d{1,5})?)((?:/[A-Za-z0-9._~-]+)*?)"
                    r"/([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100}?)(?:\.git)?/?$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")
LABEL_RE = re.compile(r"^[^\x00-\x1f,]{1,50}$")
_lock = threading.Lock()                    # the state file + limit check
_create = threading.Lock()                  # one creation at a time: the daily limit stays honest


class Refused(Exception):
    """A request the rules refuse -> (message, http status)."""
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


# ---- settings ---------------------------------------------------------------
def parse_url(url):
    """(host, prefix, owner, repo) of an https repo URL, or None."""
    m = URL_RE.match(str(url or "").strip())
    if not m or {m.group(3), m.group(4)} & {".", ".."} or ".." in m.group(2):
        return None
    return m.groups()


def settings(proj):
    r = (proj or {}).get("remote")
    return {**DEFAULTS, **r} if isinstance(r, dict) and r.get("url") else None


def normalize(body):
    """(remote, '') or (None, why) from the admin's form."""
    b = body if isinstance(body, dict) else {}
    provider = str(b.get("provider") or "github")
    if provider not in PROVIDERS:
        return None, f"provider must be one of {', '.join(PROVIDERS)}"
    url = str(b.get("url") or "").strip()
    if not parse_url(url):
        return None, "url: https://<host>/<owner>/<repo> (no credentials, no query)"
    tok = str(b.get("token_secret") or "").strip()
    if tok not in _secrets.load_secrets_file():
        return None, "token_secret: name a secret in the host secret store (Secrets tab)"
    base = str(b.get("base") or "main").strip()
    if not _projwt.REF_RE.match(base) or base.startswith("squad/"):
        return None, "base must be a branch name outside squad/"
    label = str(b.get("label") if b.get("label") is not None else DEFAULTS["label"]).strip()
    if label and not LABEL_RE.match(label):
        return None, "label: at most 50 characters, no commas"
    try:
        limit = int(b.get("daily_limit") if b.get("daily_limit") not in (None, "") else DEFAULTS["daily_limit"])
    except (TypeError, ValueError):
        return None, "daily_limit must be a number"
    if not 0 <= limit <= 200:
        return None, "daily_limit must be between 0 and 200"
    return {"provider": provider, "url": url, "token_secret": tok, "base": base, "label": label,
            "daily_limit": limit}, ""


def reserved_secrets():
    """The token secret names of every project remote, and every kickoff
    token (SQUAD_GIT_TOKEN_*, mgr/squadkick.py) — never released to a VM."""
    return ({r["token_secret"] for r in (settings(p) for p in _projects.load()) if r and r.get("token_secret")}
            | {k for k in _secrets.load_secrets_file() if k.startswith(TOKEN_PREFIX)})


def _token(cfg):
    tok = _secrets.load_secrets_file().get(cfg["token_secret"], "")
    if not tok:
        raise Refused(f"the token secret {cfg['token_secret']} is not in the host secret store", 409)
    return tok


def _scrub(text, token):
    return str(text or "").replace(token, "***") if token else str(text or "")


# ---- endpoints (tests replace these two) -------------------------------------
def git_url(cfg):
    host, prefix, owner, repo = parse_url(cfg["url"])
    return f"https://{host}{prefix}/{owner}/{repo}.git"


def api_base(cfg):
    host, prefix, owner, repo = parse_url(cfg["url"])
    if cfg["provider"] == "gitea":
        return f"https://{host}{prefix}/api/v1/repos/{owner}/{repo}"
    root = "https://api.github.com" if host.lower() == "github.com" else f"https://{host}{prefix}/api/v3"
    return f"{root}/repos/{owner}/{repo}"


def repo_name(cfg):
    p = parse_url(cfg["url"])
    return f"{p[2]}/{p[3]}" if p else ""


# ---- git on the host ----------------------------------------------------------
def auth_env(provider, token):
    """The environment that hands git the token: an http.extraHeader in
    GIT_CONFIG_*, no system/global config, no prompt, no redirect."""
    user = "x-access-token" if provider == "github" else "kaim56"
    auth = base64.b64encode(f"{user}:{token}".encode()).decode()
    return {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ASKPASS": "/bin/false", "SSH_ASKPASS": "/bin/false",
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": "http.extraHeader", "GIT_CONFIG_VALUE_0": f"Authorization: Basic {auth}",
            "GIT_CONFIG_KEY_1": "credential.helper", "GIT_CONFIG_VALUE_1": "",
            "GIT_CONFIG_KEY_2": "http.followRedirects", "GIT_CONFIG_VALUE_2": "false"}


def _git(repo, args, cfg, token):
    """git in the project repo as the repo owner; the token only in the environment."""
    uid, gid = _projwt._owner(repo)
    env = {**_projwt._env(repo, uid), **auth_env(cfg["provider"], token)}
    try:
        r = subprocess.run(["git", *_projwt.GIT_SAFE, "-c", f"protocol.file.allow={FILE_PROTOCOL}", *args],
                           cwd=repo, env=env, capture_output=True, text=True, timeout=GIT_TIMEOUT, check=False,
                           **_projwt._as(uid, gid))
    except subprocess.TimeoutExpired:
        raise Refused(f"git {args[0]}: timed out after {GIT_TIMEOUT} s", 504)
    if r.returncode != 0:
        raise Refused(f"git {args[0]}: {_scrub((r.stderr or r.stdout).strip()[-400:], token)}", 502)
    return _scrub(r.stdout, token)


def remote_branch(writer, name):
    return f"squad/{writer}/{name}"


def push_branch(proj, cfg, writer, name):
    """Snapshot the writer's folder and push its branch to squad/<writer>/<name>."""
    if proj.get("strategy") != "worktree":
        raise Refused("push works on worktree projects (a branch per writer)")
    if writer not in _projects.writers(proj):
        raise Refused(f"{writer!r} is no writer of {proj['name']}", 403)
    name = name or proj["name"]
    if not NAME_RE.match(name) or name.endswith(".lock") or ".." in name:
        raise Refused("branch name: A-Z a-z 0-9 . _ -, at most 60 characters")
    dest = remote_branch(writer, name)
    if dest == cfg["base"] or not dest.startswith(f"squad/{writer}/"):
        raise Refused("pushes go to squad/<writer>/<name> only", 403)
    token = _token(cfg)
    try:
        tip = _projwt.snapshot(proj, writer)
    except (_projwt.GitError, OSError, subprocess.SubprocessError) as e:
        raise Refused(f"snapshot failed: {e}", 500)
    _git(proj["source"]["path"], ["push", "--porcelain", "--no-verify", git_url(cfg),
                                  f"+{tip}:refs/heads/{dest}"], cfg, token)
    return {"branch": dest, "commit": tip, "repo": repo_name(cfg)}


def fetch(proj, cfg):
    """The remote's branches into refs/remotes/kaim56/* — no checkout, no local branch moves."""
    repo = _projects.source_dir(proj) and proj["source"]["path"]
    if not repo or not os.path.isdir(os.path.join(repo, ".git")):
        raise Refused("fetch needs a host project whose folder is a git repository")
    token = _token(cfg)
    _git(repo, ["fetch", "--prune", "--no-tags", "--no-recurse-submodules", git_url(cfg),
                f"+refs/heads/*:{REMOTE_NS}/*"], cfg, token)
    refs = _projwt.git(repo, ["for-each-ref", "--format=%(refname:strip=3) %(objectname:short)", REMOTE_NS],
                       check=False).split("\n")
    refs = [r.split(" ", 1) for r in refs if " " in r]
    return {"repo": repo_name(cfg), "refs": len(refs), "base": dict(refs).get(cfg["base"], ""),
            "namespace": REMOTE_NS}


# ---- the REST API -------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None                          # a redirect would carry the token elsewhere


_opener = urllib.request.build_opener(_NoRedirect)


def _api(cfg, token, method, path, body=None, query=None):
    url = api_base(cfg) + path + ("?" + urllib.parse.urlencode(query) if query else "")
    hd = {"User-Agent": "kaim56-squad", "Content-Type": "application/json"}
    if cfg["provider"] == "github":
        hd.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"})
    else:
        hd.update({"Authorization": f"token {token}", "Accept": "application/json"})
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers=hd)
    try:
        with _opener.open(req, timeout=API_TIMEOUT) as r:
            raw = r.read(4_000_000)
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read(20000) or b"{}").get("message") or e.reason
        except (ValueError, AttributeError, OSError):
            msg = e.reason
        finally:
            e.close()
        raise Refused(f"{cfg['provider']} {e.code}: {_scrub(str(msg)[:300], token)}", 502 if e.code >= 500 else 400)
    except (urllib.error.URLError, OSError) as e:
        raise Refused(f"{cfg['provider']} not reachable: {_scrub(repr(e)[:200], token)}", 502)
    try:
        return json.loads(raw or b"null")
    except ValueError:
        raise Refused(f"{cfg['provider']}: no JSON in the answer", 502)


def _item(d, full=False):
    d = d if isinstance(d, dict) else {}
    out = {"number": d.get("number"), "title": str(d.get("title") or "")[:TITLE_MAX],
           "url": d.get("html_url") or "", "state": d.get("state") or "",
           "author": (d.get("user") or {}).get("login", ""),
           "labels": [str(x.get("name")) for x in d.get("labels") or [] if isinstance(x, dict)]}
    if isinstance(d.get("head"), dict):
        out.update(head=d["head"].get("ref", ""), base=(d.get("base") or {}).get("ref", ""),
                   merged=bool(d.get("merged") or d.get("merged_at")))
    if full:
        out["body"] = str(d.get("body") or "")[:BODY_MAX]
    return out


def _label(cfg, token, number):
    """Add the project's label (create it when missing); a failure is only noted."""
    name = cfg.get("label")
    if not name:
        return ""
    try:
        if cfg["provider"] == "github":
            try:
                _api(cfg, token, "GET", "/labels/" + urllib.parse.quote(name, safe=""))
            except Refused:
                _api(cfg, token, "POST", "/labels", {"name": name, "color": "6f42c1"})
            _api(cfg, token, "POST", f"/issues/{number}/labels", {"labels": [name]})
        else:
            have = _api(cfg, token, "GET", "/labels", query={"limit": 50})
            lid = next((x.get("id") for x in have or [] if isinstance(x, dict) and x.get("name") == name), None)
            if lid is None:
                lid = (_api(cfg, token, "POST", "/labels", {"name": name, "color": "#6f42c1"}) or {}).get("id")
            _api(cfg, token, "POST", f"/issues/{number}/labels", {"labels": [lid]})
        return ""
    except Refused as e:
        return f"label not added: {e}"


def _comments(cfg, token, number):
    got = _api(cfg, token, "GET", f"/issues/{number}/comments", query={"per_page": 100, "limit": 50})
    return [{"author": (c.get("user") or {}).get("login", ""), "created": c.get("created_at", ""),
             "body": str(c.get("body") or "")[:2000]} for c in (got or [])[-COMMENT_SHOW:] if isinstance(c, dict)]


# ---- the record + the daily limit ----------------------------------------------
def _load():
    try:
        with open(STATE_FILE) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) and isinstance(d.get("created"), list) else {"created": []}
    except (FileNotFoundError, ValueError):
        return {"created": []}
    except OSError as e:
        print(f"[quiet] squadgit.json unreadable: {e!r}", flush=True)
        return {"created": []}


def _record(entry):
    with _lock:
        d = _load()
        d["created"] = (d["created"] + [entry])[-KEEP:]
        tmp = STATE_FILE + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(d, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)


def created(project, limit=30):
    return [e for e in _load()["created"] if e.get("project") == project][-limit:][::-1]


def created_today(project, member):
    t = time.localtime()
    midnight = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    return sum(1 for e in _load()["created"]
               if e.get("project") == project and e.get("by") == member and e.get("ts", 0) >= midnight)


# ---- what a member may do ---------------------------------------------------------
TOOLS = {"push": "git_push_branch", "fetch": "git_fetch", "pr-create": "pr_create", "prs": "pr_list",
         "pr": "pr_view", "pr-comment": "pr_comment", "issue-create": "issue_create", "issues": "issue_list",
         "issue-comment": "issue_comment"}
WRITE_ROLES = ("writer", "lead")
WRITER_ONLY = ("push", "pr-create")


def resolve(inst_name, project=""):
    """(project, role) of a member with a remote, or Refused."""
    mine = _projects.memberships(inst_name)
    project = str(project or "").strip()
    if project:
        pm = next(((p, m) for p, m in mine if p.get("name") == project), None)
        if pm is None:
            raise Refused(f"you are not a member of project {project!r}", 403)
        if not settings(pm[0]):
            raise Refused(f"project {project!r} has no git remote", 404)
        return pm[0], pm[1].get("role")
    remote = [(p, m) for p, m in mine if settings(p)]
    if len(remote) == 1:
        return remote[0][0], remote[0][1].get("role")
    if not remote:
        raise Refused("none of your projects has a git remote", 404)
    raise Refused("name the project: " + ", ".join(p["name"] for p, _m in remote))


def _text(v, cap, what):
    s = str(v or "").strip()
    if not s:
        raise Refused(f"{what} is empty")
    return s[:cap]


def _number(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise Refused("number must be a PR/issue number")
    if n <= 0:
        raise Refused("number must be a PR/issue number")
    return n


def _state(v):
    s = str(v or "open").strip().lower()
    if s not in ("open", "closed", "all"):
        raise Refused("state must be open, closed or all")
    return s


def _writer_of(proj, caller, role, b):
    """The writer whose branch a push/PR uses: the caller itself, or — for the
    lead — the writer it names."""
    if role == "writer":
        return caller
    w = str(b.get("writer") or "").strip()
    if not w:
        raise Refused("the lead names the writer whose branch to push (writer=...)")
    return w


def _footer(member, project):
    return f"\n\n---\n_Opened by kAIm56 squad member `{member}` (project `{project}`)._"


def call(inst_name, action, b):
    """One member request -> (result, project name, repo, number). Raises Refused."""
    if action not in TOOLS:
        raise Refused("unknown operation", 404)
    b = b if isinstance(b, dict) else {}
    proj, role = resolve(inst_name, b.get("project"))
    cfg = settings(proj)
    if action in WRITER_ONLY and role not in WRITE_ROLES:
        raise Refused(f"{TOOLS[action]}: writers and the lead only (you are {role})", 403)
    pname, repo = proj["name"], repo_name(cfg)
    if action == "push":
        r = push_branch(proj, cfg, _writer_of(proj, inst_name, role, b), str(b.get("name") or "").strip())
        _squadmetrics.git(pname, inst_name, "push", f"{r['branch']}@{r['commit']}", detail={"repo": repo})
        return r, pname, repo, ""
    if action == "fetch":
        return fetch(proj, cfg), pname, repo, ""
    token = _token(cfg)
    if action in ("pr-create", "issue-create"):
        title = _text(b.get("title"), TITLE_MAX, "title")
        body = str(b.get("body") or "")[:BODY_MAX] + _footer(inst_name, pname)
        with _create:
            if created_today(pname, inst_name) >= cfg["daily_limit"]:
                raise Refused(f"daily limit reached: {cfg['daily_limit']} PRs/issues per member and day", 429)
            if action == "pr-create":
                pushed = push_branch(proj, cfg, _writer_of(proj, inst_name, role, b), str(b.get("name") or "").strip())
                d = _api(cfg, token, "POST", "/pulls", {"title": title, "body": body, "head": pushed["branch"],
                                                        "base": cfg["base"]})
            else:
                d = _api(cfg, token, "POST", "/issues", {"title": title, "body": body})
            out = _item(d)
            _record({"ts": int(time.time()), "project": pname, "kind": "pr" if action == "pr-create" else "issue",
                     "number": out["number"], "title": out["title"], "url": out["url"], "by": inst_name})
            _squadmetrics.git(pname, inst_name, "pr" if action == "pr-create" else "issue", out["number"],
                              detail={"title": str(out["title"] or "")[:120], "url": out["url"]})
        note = _label(cfg, token, out["number"]) if out.get("number") else ""
        return {**out, **({"note": note} if note else {})}, pname, repo, out.get("number") or ""
    if action in ("prs", "issues"):
        q = {"state": _state(b.get("state")), "per_page": 30, "limit": 30}
        if action == "issues" and cfg["provider"] == "gitea":
            q["type"] = "issues"
        got = _api(cfg, token, "GET", "/pulls" if action == "prs" else "/issues", query=q)
        items = [_item(x) for x in got or [] if isinstance(x, dict) and not (action == "issues" and x.get("pull_request"))]
        return {"items": items}, pname, repo, ""
    n = _number(b.get("number"))
    if action == "pr":
        out = _item(_api(cfg, token, "GET", f"/pulls/{n}"), full=True)
        out["comments"] = _comments(cfg, token, n)
        return out, pname, repo, n
    # pr-comment / issue-comment: both are issue comments on either provider
    d = _api(cfg, token, "POST", f"/issues/{n}/comments", {"body": _text(b.get("body"), BODY_MAX, "body")})
    return {"number": n, "url": (d or {}).get("html_url", ""), "id": (d or {}).get("id")}, pname, repo, n
