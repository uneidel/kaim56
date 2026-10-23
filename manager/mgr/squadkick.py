# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Squad kickoff (security boundary): one chat line — a repo URL and a goal —
becomes a squad waiting for its plan. Admin only (mgr/auth.py: default deny).

The first https URL in the text is the repo, the rest the goal. Hosts:
github.com and the Gitea hosts of the site setting SQUAD_GITEA_HOSTS (comma
list, default none); others are refused. The squad's name comes from the repo
([a-z0-9-], -2 … when taken). Then, as a job the UI polls:

a. token: the host secret ``SQUAD_GIT_TOKEN_<HOST>`` (github.com → ``…_GITHUB``,
   git.example.org → ``…_GIT_EXAMPLE_ORG``); without one the clone is tried
   anonymously, and an auth failure names the secret to create. These names
   are reserved: never released to a VM (mgr/squadgit.reserved_secrets).
b. the squad record (mgr/squadrec.py: squad ID, folder SQUADS_DIR/<id>__<name>),
   then the clone on the host into <folder>/projects/<name>/repo (SQUADS_DIR:
   site setting; default ~<manager owner>/squads, made 0750) as that folder's
   owner, umask 022:
   full history, no submodules, no hooks, no system/global git config, the
   token only in the environment (squadgit.auth_env) — never argv, the stored
   URL or a log. A timeout and a size cap (SQUAD_MAX_BYTES, default 2 GB,
   watched while cloning and checked after) abort and clean up.
c. project <name>: worktree strategy, the clone as source, its default branch as base.
d. git remote (squadgit) with the token secret — skipped, with a note, without one.
e. instance <name>-architect: template openrouter (or claude, the
   composer's choice), persona code-architect, model of router tier "strong"
   (else the template default, said in the chat) — squadgate's creation
   path; the project's lead.
f. squad enabled, NOT approved (only the lead can be woken), budget 2M
   tokens/day; reindex when an indexer exists (queued, never blocks).
g. the goal with the repo profile (mgr/repoprofile.py, computed right after
   the clone), posted as user:<name> to @<name>-architect: propose the squad.

Each step skips what is done (a retry resumes). A failure removes what THIS
job created (instance, project, clone, squad record + folder) — never anything
it found.

Jobs live in memory (the last KEEP_JOBS). Part of the mgr package: no import
from manager.py. Sibling modules are used as ``_name.func`` (module
attribute), so a test can replace one definition in one place.
"""
import json
import os
import pwd
import re
import shutil
import subprocess
import threading
import time
import uuid

from mgr import audit as _audit
from mgr import gateway as _gateway
from mgr import hitlq as _hitlq
from mgr import indexer as _indexer
from mgr import instances as _instances
from mgr import notify as _notify
from mgr import projects as _projects
from mgr import projwt as _projwt
from mgr import repoprofile as _repoprofile
from mgr import secrets as _secrets
from mgr import settings as _settings
from mgr import squadgate as _squadgate
from mgr import squadreq as _squadreq
from mgr import squadgit as _squadgit
from mgr import squadmetrics as _squadmetrics
from mgr import squadrec as _squadrec
from mgr import squads as _squads
from mgr import toolchain as _toolchain

URL_IN_TEXT = re.compile(r"https://[^\s<>\"'`]+")
GOAL_MAX = 50000
GOAL_INLINE = 2500           # a longer goal goes as GOAL.md into the workspace (a message holds 8000)
NAME_MAX = 20                        # + "-architect" stays within an instance name's 32
ARCH_SUFFIX, ARCH_PERSONA, ARCH_TIER = "-architect", "code-architect", "strong"
BUDGET = 2_000_000
CLONE_TIMEOUT = int(_settings.SITE.get("SQUAD_CLONE_TIMEOUT") or 900)
MAX_BYTES = int(_settings.SITE.get("SQUAD_MAX_BYTES") or 2 * 1024 ** 3)
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"      # git's empty tree: an empty repo's first commit
KEEP_JOBS = 50
_AUTH = re.compile(r"authentication failed|could not read username|terminal prompts disabled|"
                   r"\b40[13]\b|not found|invalid username or password|access denied", re.I)
_jobs = {}
_lock = threading.Lock()             # the job table + name choice
_run_lock = threading.Lock()         # one kickoff runs at a time


class Stop(Exception):
    """A step that cannot go on: the message goes into the chat."""


# ---- settings -----------------------------------------------------------------
def squads_dir():
    return _squadrec.squads_dir()


def hosts():
    """{host: provider} the kickoff clones from."""
    g = _settings.SITE.get("SQUAD_GITEA_HOSTS", "")
    g = g.split(",") if isinstance(g, str) else (g or [])
    return {"github.com": "github", **{str(x).strip().lower(): "gitea" for x in g if str(x).strip()}}


def token_secret(host):
    """The host secret holding the token for this host."""
    h = host.split(":", 1)[0].lower()
    return (_squadgit.TOKEN_PREFIX + ("GITHUB" if h == "github.com" else re.sub(r"[^A-Z0-9]+", "_", h.upper()).strip("_")))[:64]


def clone_url(job):
    """Where git clones from (tests point it at a local repo)."""
    return _squadgit.git_url({"url": job["url"]})


# ---- the request ----------------------------------------------------------------
def parse(text):
    """{url, host, provider, owner, repo, goal} or (None, why) — friendly words."""
    text = str(text or "").strip()
    m = URL_IN_TEXT.search(text)
    if not m:
        return None, ("Paste the repository's https URL and say what the squad should do, e.g. "
                      "\"https://github.com/owner/repo add a dark mode\".")
    url = m.group(0).rstrip(".,;:!?)]}>")
    sep = " \t\r\n-—–:,;"
    goal = " ".join(x for x in (text[:m.start()].strip(sep), text[m.start() + len(url):].strip(sep)) if x)
    p = _squadgit.parse_url(url)
    if not p:
        return None, f"{url} is no repository URL: https://<host>/<owner>/<repo>"
    host, prefix, owner, repo = p
    provider = hosts().get(host.split(":", 1)[0].lower())
    if not provider:
        return None, f"host {host} is not allowed: {', '.join(sorted(hosts()))} (site setting SQUAD_GITEA_HOSTS)"
    if provider == "github" and prefix:
        return None, "a GitHub repo URL is https://github.com/<owner>/<repo>"
    if not goal:
        return None, "What should the squad do? Write the goal next to the URL."
    if len(goal) > GOAL_MAX:
        return None, f"the goal is longer than {GOAL_MAX} characters"
    return {"url": f"https://{host}{prefix}/{owner}/{repo}", "host": host, "provider": provider,
            "owner": owner, "repo": repo, "goal": goal}, ""


def _taken(name):
    return (_projects.get(name) is not None or os.path.lexists(os.path.join(squads_dir(), name))
            or _squadrec.name_taken(name)
            or any(i["name"] == name + ARCH_SUFFIX for i in _instances.load_instances())
            or any(j["name"] == name for j in _jobs.values() if j["status"] != "failed"))


def derive_name(repo):
    base = re.sub(r"[^a-z0-9]+", "-", str(repo).lower()).strip("-")[:NAME_MAX].strip("-") or "squad"
    for i in range(1, 100):
        n = base if i == 1 else f"{base[:NAME_MAX - len(str(i)) - 1]}-{i}"
        if not _taken(n):
            return n
    return ""


# ---- jobs -------------------------------------------------------------------------
def _spawn(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True, name="squadkick").start()


def view(job):
    return {k: job[k] for k in ("id", "name", "arch", "arch_template", "url", "repo", "provider", "goal", "status",
                                "log", "created", "who")}


def jobs():
    return [view(j) for j in sorted(_jobs.values(), key=lambda j: j["created"], reverse=True)]


def get(jid):
    j = _jobs.get(str(jid))
    return view(j) if j else None


def _team(team):
    """The New squad form's Team box; None (an API caller without it) = the default roles."""
    return _squads.TEAM_DEFAULT if team is None else str(team).strip()[:_squads.TEAM_MAX]


def start(text, who, architect_template="", team=None, decision=""):
    """A new kickoff -> (job view, '') or (None, why). The steps run in a thread."""
    tpl = str(architect_template or "openrouter").strip()
    if tpl not in _squadgate.MEMBER_TEMPLATES:
        return None, f"architect_template must be {' or '.join(_squadgate.MEMBER_TEMPLATES)}"
    ok, why = _squads.normalize_settings({"decision": decision})       # the Suggestions switch
    if ok is None:
        return None, why
    f, why = parse(text)
    if not f:
        return None, why
    with _lock:
        name = derive_name(f["repo"])
        if not name:
            return None, f"no free squad name for {f['repo']}"
        job = {**f, "id": uuid.uuid4().hex[:10], "name": name, "arch": name + ARCH_SUFFIX, "who": who,
               "arch_template": tpl, "status": "running", "log": [], "made": [], "done": [], "created": time.time(), "base": "",
               "team": _team(team), "decision": str(decision or "").strip()}
        _jobs[job["id"]] = job
        for old in sorted(_jobs.values(), key=lambda j: j["created"])[:-KEEP_JOBS]:
            _jobs.pop(old["id"], None)
    _say(job, f"Kickoff of squad {name}: {f['url']}")
    _spawn(run, job["id"])
    return view(job), ""


def start_repo(project, goal, who, architect_template="", team=None, decision=""):
    """A squad on an existing repo (the Repos tab): no clone, no new project —
    the architect, the squad, the goal (STEPS_REPO) -> (job view, '') or (None, why)."""
    tpl = str(architect_template or "openrouter").strip()
    if tpl not in _squadgate.MEMBER_TEMPLATES:
        return None, f"architect_template must be {' or '.join(_squadgate.MEMBER_TEMPLATES)}"
    ok, why = _squads.normalize_settings({"decision": decision})       # the Suggestions switch
    if ok is None:
        return None, why
    goal = str(goal or "").strip()
    if not goal:
        return None, "say what the squad should do"
    if len(goal) > GOAL_MAX:
        return None, f"the goal is longer than {GOAL_MAX} characters"
    p = _projects.get(str(project or "").strip())
    if p is None:
        return None, "unknown repo"
    if (p.get("source") or {}).get("type") != "host":
        return None, "a squad needs a host-folder repo"
    if _squads.settings(p).get("enabled"):
        return None, f"{p['name']} has a squad already"
    path = p["source"]["path"]
    lead = next((n for n, m in (p.get("members") or {}).items() if m.get("role") == "lead"), "")
    with _lock:
        job = {"id": uuid.uuid4().hex[:10], "name": p["name"], "arch": lead or p["name"] + ARCH_SUFFIX, "who": who,
               "arch_template": tpl, "status": "running", "log": [], "made": [], "done": [], "created": time.time(),
               "base": p.get("base") or "", "url": path, "repo": p["name"], "provider": "local", "goal": goal,
               "path": path, "steps": "lead" if lead else "repo", "team": _team(team),
               "decision": str(decision or "").strip()}    # the repo's lead plans
        _jobs[job["id"]] = job
        for old in sorted(_jobs.values(), key=lambda j: j["created"])[:-KEEP_JOBS]:
            _jobs.pop(old["id"], None)
    _say(job, f"Squad on the existing repo {p['name']} ({path})")
    _spawn(run, job["id"])
    return view(job), ""


def remove(project):
    """Delete a squad (the Squads tab) -> (summary, '') or (None, why): its
    members' instances (unless another project has them), tickets, proposals,
    channel and settings. The repo stays in Repos with its code and branches;
    the squad's archive stays (the record is closed)."""
    p = _projects.get(str(project or "").strip())
    if p is None or not _squads.settings(p).get("enabled"):
        return None, "no squad on that repo"
    name, members = p["name"], sorted(p.get("members") or {})
    elsewhere = {n for x in _projects.load() if x["name"] != name for n in (x.get("members") or {})}
    try:
        _toolchain.purge(p)                       # its layers (by squad id: before the record detaches)
    except Exception as e:
        print(f"[quiet] toolchain purge {name}: {e!r}", flush=True)
    r = _projects.upsert({**p, "members": {}})    # unmounts, removes the writers' folders (branches stay)
    if "error" in r:
        return None, r["error"]
    _projects.set_squad(name, None)
    _squads.purge(name)
    _squadgate.purge(name)
    gone = [n for n in members if n not in elsewhere and _instances.delete_instance(n) != "unknown"]
    return {"project": name, "deleted": gone, "kept": [n for n in members if n in elsewhere]}, ""


def retry(jid):
    job = _jobs.get(str(jid))
    if job is None:
        return None, "unknown kickoff"
    if job["status"] != "failed":
        return None, f"the kickoff is {job['status']}"
    job["status"] = "running"
    _say(job, "Retrying …")
    _spawn(run, job["id"])
    return view(job), ""


def _say(job, text, ok=True):
    job["log"].append({"ts": int(time.time()), "text": str(text)[:2000], "ok": bool(ok)})


def run(jid):
    job = _jobs[jid]
    with _run_lock:
        try:
            for step in {"repo": STEPS_REPO, "lead": STEPS_LEAD}.get(job.get("steps"), STEPS):
                step(job)
            job["status"] = "done"
            _say(job, f"Squad {job['name']} is set up. {job['arch']} answers in this channel with a plan to approve.")
        except Stop as e:
            _say(job, str(e), ok=False)
            _cleanup(job)
            job["status"] = "failed"
        except Exception as e:                   # a bug or the host: the chat says it, the rest goes
            _say(job, f"kickoff failed: {e!r}"[:500], ok=False)
            _cleanup(job)
            job["status"] = "failed"


def _cleanup(job):
    """Remove what this job created (newest first); never what it found."""
    for what in reversed(job["made"]):
        try:
            if what == "instance":
                _instances.delete_instance(job["arch"])
            elif what == "engineer":
                _instances.delete_instance(job["name"] + _squadreq.SUFFIX)
            elif what == "project":
                _projects.delete(job["name"])
                _squads.purge(job["name"])
                _squadgate.purge(job["name"])
            elif what == "clone":
                shutil.rmtree(job["path"], ignore_errors=True)
            elif what == "squad":
                _squadrec.drop(job["squad_id"])
        except Exception as e:
            _say(job, f"cleanup of the {what} failed: {e!r}"[:300], ok=False)
    if job["made"]:
        _say(job, "Removed what this kickoff had created (" + ", ".join(reversed(job["made"])) + "). Retry runs it again.")
    job["made"], job["done"] = [], []


# ---- the steps ----------------------------------------------------------------------
def _token(job):
    return _secrets.load_secrets_file().get(job["token_secret"], "")


def step_token(job):
    job["token_secret"] = token_secret(job["host"])
    if _token(job):
        _say(job, f"Token: host secret {job['token_secret']}.")
    else:
        _say(job, f"No host secret {job['token_secret']}: cloning without a token (a public repo).")


def _du(path):
    total = 0
    for d, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(d, f)).st_size
            except OSError:
                pass
    return total


def step_clone(job):
    root, uid, gid = _squadrec.root()
    if "squad" not in job["made"]:
        rec = _squadrec.create(job["name"], job["name"])
        job["squad_id"] = rec["id"]
        job["made"].append("squad")
        _say(job, f"Squad ID {rec['id']}, folder {_squadrec.folder(rec)}.")
    rec = next(r for r in _squadrec.records() if r["id"] == job["squad_id"])
    dest = job["path"] = _squadrec.repo_dir(rec, job["name"])
    if "clone" in job["made"] and os.path.isdir(os.path.join(dest, ".git")):
        return _say(job, f"Clone: {dest} is there.")
    if os.path.lexists(dest):
        raise Stop(f"{dest} exists and is not this kickoff's — remove it or rename the squad")
    _squadrec.mkdirs(os.path.dirname(dest))
    _squadrec.set_repo(rec, job["name"], dest)
    tok = _token(job)
    try:
        home = pwd.getpwuid(uid).pw_dir
    except KeyError:
        home = "/nonexistent"
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home, "LANG": "C.UTF-8",
           "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_ASKPASS": "/bin/false", "SSH_ASKPASS": "/bin/false", "GIT_LFS_SKIP_SMUDGE": "1"}
    if tok:
        env.update(_squadgit.auth_env(job["provider"], tok))
    cmd = ["git", *_projwt.GIT_SAFE, "-c", f"protocol.file.allow={_squadgit.FILE_PROTOCOL}",
           "clone", "--quiet", "--no-recurse-submodules", "--template=", "--", clone_url(job), dest]
    _say(job, f"Cloning {job['owner']}/{job['repo']} into {dest} …")
    job["made"].append("clone")
    proc = subprocess.Popen(cmd, cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True, umask=0o022, **_projwt._as(uid, gid))
    t0, err = time.time(), ""
    while True:
        try:
            err = proc.communicate(timeout=3)[1] or ""
            break
        except subprocess.TimeoutExpired:
            why = (f"the clone took longer than {CLONE_TIMEOUT} s" if time.time() - t0 > CLONE_TIMEOUT
                   else f"the repository is larger than {MAX_BYTES // 2**20} MB (SQUAD_MAX_BYTES)"
                   if _du(dest) > MAX_BYTES else "")
            if why:
                proc.kill()
                proc.communicate()
                raise Stop("Clone aborted: " + why)
    err = _squadgit._scrub(err.strip()[-400:], tok)
    if proc.returncode != 0:
        if not tok and _AUTH.search(err):
            raise Stop(f"The clone needs a token: create the host secret {job['token_secret']} (Secrets) with a "
                       f"{job['provider']} token that may read {job['owner']}/{job['repo']}, then Retry.")
        raise Stop("git clone failed: " + (err or f"exit {proc.returncode}"))
    size = _du(dest)
    if size > MAX_BYTES:
        raise Stop(f"Clone aborted: the repository is larger than {MAX_BYTES // 2**20} MB (SQUAD_MAX_BYTES)")
    if not _projwt.git_ok(dest, ["rev-parse", "--verify", "-q", "HEAD"]):      # an empty repo: an empty first commit on main
        _projwt.git(dest, ["symbolic-ref", "HEAD", "refs/heads/main"])
        sha = _projwt.git(dest, ["-c", "user.name=kAIm56", "-c", "user.email=kaim56@localhost",
                                 "commit-tree", EMPTY_TREE, "-m", "Initial commit"]).strip()
        _projwt.git(dest, ["update-ref", "refs/heads/main", sha])
        _say(job, "The repository is empty: an empty first commit on main.")
    job["base"] = _projwt.current_branch(dest)
    if not job["base"]:
        raise Stop("the clone has no branch checked out (a detached HEAD)")
    _say(job, f"Cloned ({size / 2**20:.1f} MB), default branch {job['base']}.")
    prof = _profile(job)
    if prof:
        _say(job, "Repo profile: " + (", ".join(prof["keywords"]) or "no keywords"))


def _profile(job):
    """The clone's repo profile (cached by HEAD), None when it fails — the kickoff goes on without."""
    try:
        return _repoprofile.get(job["name"], job["path"])
    except Exception as e:
        _say(job, f"Repo profile failed: {e!r}"[:300], ok=False)
        return None


def step_project(job):
    name = job["name"]
    if _projects.get(name) is not None:
        if "project" in job["made"]:
            return
        raise Stop(f"a project {name} exists and is not this kickoff's")
    job["base"] = job["base"] or _projwt.current_branch(job["path"])
    r = _projects.upsert({"name": name, "strategy": "worktree", "base": job["base"], "members": {},
                          "source": {"type": "host", "path": job["path"]}})
    if "error" in r:
        raise Stop("project: " + r["error"])
    job["made"].append("project")
    _say(job, f"Project {name}: worktree on {job['base']}.")


def step_remote(job):
    if _squadgit.settings(_projects.get(job["name"])):
        return
    if not _token(job):
        return _say(job, f"No git remote: without the host secret {job['token_secret']} the squad cannot push or open "
                         f"PRs — create it, then set the remote in Projects → Squad.", ok=False)
    r, why = _squadgit.normalize({"provider": job["provider"], "url": job["url"],
                                  "token_secret": job["token_secret"], "base": job["base"]})
    if not r:
        raise Stop("git remote: " + why)
    _projects.set_remote(job["name"], r)
    _say(job, f"Git remote: {job['provider']} {job['owner']}/{job['repo']}, base {job['base']}.")


def step_architect(job):
    arch, name = job["arch"], job["name"]
    inst = next((i for i in _instances.load_instances() if i["name"] == arch), None)
    if inst is not None and "instance" not in job["made"]:
        raise Stop(f"an instance {arch} exists and is not this kickoff's")
    if inst is None:
        m = {"name": arch, "purpose": f"architect and lead of squad {name} ({job['url']})", "persona": ARCH_PERSONA,
             "skills": [], "model_tier": ARCH_TIER, "model": "", "template": job["arch_template"], "role": "lead",
             "existing": False}
        steps = []
        ok = _squadgate._create(_projects.get(name), m, steps)
        if ok:
            job["made"].append("instance")
        for s in steps:
            if not s["ok"] and not s["step"].startswith("create"):
                _say(job, s["detail"], ok=False)
        if not ok:
            raise Stop(f"creating {arch} failed: " + next((s["detail"] for s in steps if s["step"].startswith("create")), "?"))
        inst = next((i for i in _instances.load_instances() if i["name"] == arch), {})
        model = (inst.get("config") or {}).get("ANTHROPIC_MODEL" if job["arch_template"] == "claude" else "OPENROUTER_MODEL")
        _say(job, f"Instance {arch} ({job['arch_template']}): persona {ARCH_PERSONA}, model "
                  + (model or "the template default") + (f" (tier {ARCH_TIER})" if model else ""))
    proj = _projects.get(name)
    members = {n: {"role": x.get("role")} for n, x in (proj.get("members") or {}).items()}
    if members.get(arch, {}).get("role") != "lead":
        members[arch] = {"role": "lead"}
        r = _projects.upsert({**proj, "members": members})
        if "error" in r:
            raise Stop("adding the lead: " + r["error"])
        _say(job, f"{arch} is the project's lead.")


def step_squad(job):
    name = job["name"]
    if "squad" not in job["done"]:
        proj = _projects.get(name)
        s, _why = _squads.normalize_settings({"enabled": True, "approved": False, "paused": False,
                                              "daily_token_budget": BUDGET, "requirements": "drafting",
                                              "team": job.get("team", ""), "decision": job.get("decision", "")},
                                             proj.get("squad"))
        _squads.save_settings(name, s)
        _squadrec.ensure(_projects.get(name), {job["arch"]: ARCH_PERSONA})
        job["done"].append("squad")
        _say(job, f"Squad enabled, budget {BUDGET:,} tokens/day — a requirements engineer interviews you first; "
                  "mark the requirements agreed on the board, then the architect proposes the plan.")
        added = _squadgate.squad_tools(_projects.get(name))
        if added:
            _say(job, _squadgate.tools_note(added))
    if "index" not in job["done"]:
        job["done"].append("index")
        if _indexer.instance():
            _say(job, "Code index: " + _indexer.reindex(name, "squad kickoff"))


def step_engineer(job):
    """The architect's ephemeral requirements engineer <squad>-req: a reader member that
    interviews the operator until the requirements are agreed (mgr/squadreq.py retires it)."""
    name = job["name"]
    eng = name + _squadreq.SUFFIX
    proj = _projects.get(name)
    if (any(i["name"] == eng for i in _instances.load_instances()) and "engineer" not in job["made"]
            and eng not in (proj.get("members") or {})):            # this squad's own one (a retry, a new goal) is reused
        raise Stop(f"an instance {eng} exists and is not this kickoff's")
    m = {"name": eng, "purpose": f"requirements engineer of squad {name} — interviews the operator until the request "
                                 f"is fully defined, then hands REQUIREMENTS.md to the architect {job['arch']}",
         "persona": "", "skills": [], "model_tier": ARCH_TIER, "model": "", "template": job["arch_template"],
         "role": "reader", "existing": False}
    steps = []
    if not _squadgate._create(proj, m, steps):
        raise Stop(f"creating {eng} failed: " + next((x["detail"] for x in steps if x["step"].startswith("create")), "?"))
    if "engineer" not in job["made"]:
        job["made"].append("engineer")
    members = {n: x.get("role") for n, x in (proj.get("members") or {}).items()}
    if members.get(eng) != "reader":
        r = _projects.upsert({**proj, "members": {**members, eng: "reader"}})
        if "error" in r:
            raise Stop(f"adding {eng}: " + r["error"])
    _squads.save_settings(name, {**_squads.settings(_projects.get(name)), "engineer": eng})
    _squadgate.squad_tools(_projects.get(name))
    _say(job, f"Requirements engineer {eng} started for the architect — it interviews you in the chat.")


def step_post(job):
    if "post" in job["done"]:
        return
    arch, prof = job["arch"], _repoprofile.text(_profile(job))
    eng = _squadreq.engineer(_projects.get(job["name"]))
    to = eng or arch
    goal = job["goal"]
    if len(goal) > GOAL_INLINE:                   # the whole goal as GOAL.md, its start in the message
        if not _squadreq.write_doc(to, "GOAL.md", goal):
            raise Stop(f"writing GOAL.md into {to}'s workspace failed")
        goal = (goal[:1500].rstrip() + f"\n\n[… {len(job['goal']):,} characters in all — read GOAL.md at the top "
                "of your workspace first, it is the full goal]")
    text = (f"@{to} Kickoff by {job['who']}. Repository {job['url']} — at /project/{job['name']}, base branch "
            f"{job['base']}.\n\nGoal:\n{goal}\n\n" + (prof + "\n\n" if prof else "") + f"The code is the repository mounted read-only at /project/{job['name']} "
            f"(not your workspace, not the web): read it there first (list_dir /project/{job['name']}, read_file, "
            f"bash: ls/grep/cat). Then: {_squadreq.INTERVIEW}"
            + (" " + _squadreq.ENGINEER.format(arch=arch, file=_squadreq.FILE) if eng else ""))
    m, why = _squads.post(_projects.get(job["name"]), "user:" + job["who"], [to], text)
    if not m:
        raise Stop("posting the goal failed: " + why)
    job["done"].append("post")
    if m.get("blocked"):
        _say(job, f"Goal posted, not delivered: {m['blocked']}", ok=False)
    else:
        _say(job, f"Goal posted to @{to}.")


STEPS = (step_token, step_clone, step_project, step_remote, step_architect, step_squad, step_engineer, step_post)
STEPS_REPO = (step_architect, step_squad, step_engineer, step_post)   # an existing repo: no clone, no new project
STEPS_LEAD = (step_squad, step_engineer, step_post)                   # … whose lead becomes the architect


# ---- the Squads page's list ---------------------------------------------------------
def overview():
    """The squad-enabled projects: name, status, lead, pending proposals, last
    message, members working now, ticket counts, open HITL items (the pills)."""
    out, hq = [], [x["project"] for x in _hitlq.items("open")]
    for p in sorted(_projects.load(), key=lambda x: x.get("name", "")):
        s = _squads.settings(p)
        if not s["enabled"]:
            continue
        last = _squads.read(p["name"], None, 0, 1)
        pend = sum(1 for x in _squadgate.proposals(p["name"]) if x.get("status") == "pending")
        rec = _squadrec.of_project(p["name"]) or {}
        tix = _squadgate.tickets(p["name"])
        out.append({"name": p["name"], "lead": _squadgate.lead(p), "id": rec.get("id", ""),
                    "folder": _squadrec.folder(rec) if rec else "",
                    "members_ids": [{"name": m["instance"], "role": m.get("role", ""), "member_id": m["member_id"]}
                                    for m in rec.get("members") or [] if not m.get("left")],
                    "status": "paused" if s["paused"] else "running" if s["approved"] else "waiting for approval",
                    "pending": pend, "members": len(p.get("members") or {}),
                    "last_id": last[-1]["id"] if last else 0, "last_ts": last[-1]["ts"] if last else 0,
                    "remote": (_squadgit.settings(p) or {}).get("url", ""),
                    "working": sum(1 for w in _squads.working(p["name"]) if w["state"] == "working"),
                    "tickets_done": sum(1 for t in tix if t["status"] == "done"), "tickets_total": len(tix),
                    "waiting_for_you": hq.count(p["name"])})
    return out


# ---- who does what (the Squads board's member rows) -------------------------------------
STRIP_S = 3600                       # the activity strip: the last hour (6 h was more than anyone read)
_TICKET = re.compile(r"Ticket (\S{1,32}) \(id \d+\) is yours")


def _tool(member, since):
    """The member's latest tool call in this turn (its audit trail: the OpenRouter agent
    reports each call after it, Claude Code before it via the PreToolUse hook) -> "tool: target", short, no secrets."""
    a = next((x for x in _audit.audit_read(member, 20) if int(x.get("ts") or 0) >= since), None)
    if not a:
        return ""
    line = (str(a.get("tool") or "") + (": " + str(a["target"]) if a.get("target") else "")).splitlines()[0]
    return _gateway.redact_secrets(line)[0][:90]


def _why(member, msg, outcome):
    """A failed turn's reason, one line: what its waking message recorded, else the outcome."""
    m = _squads._get(msg) if str(msg or "").isdigit() else None
    why = ((m or {}).get("failed") or {}).get(member) or outcome
    return (str(why).strip().splitlines() or [""])[0][:120]


def activity(proj, now=None):
    """Per member what it does or why not: {members: {name: {state work|wait|err|ask|idle,
    reason, ticket, dep, since, tool, template, last_turn, strip, msgs}}, failed_members,
    stalled_min, queued}. First match wins: working, queued, last turn failed, asks you,
    waiting for a dependency, idle."""
    now, name = int(now or time.time()), proj["name"]
    insts = {i["name"]: i for i in _instances.load_instances()}
    names = sorted(n for n in (proj.get("members") or {}) if (insts.get(n) or {}).get("template") != "indexer")
    sid = (_squadrec.of_project(name) or {}).get("id", "")
    ev = _squadmetrics.events(sid, now - STRIP_S, "", _squadmetrics.EVENTS_MAX) if sid else []
    last = _squadmetrics.last_turns(sid) if sid else {}
    gone = _squads.settings(proj).get("dismissed") or {}
    ws, tix = _squads.working(name), _squadgate.tickets(name)
    st = {t["key"]: t["status"] for t in tix}
    hq = [x for x in _hitlq.items("open") if x.get("project") == name]
    out = {}
    for n in names:
        w = next((x for x in ws if x["member"] == n), None)
        lt = last.get(n)
        mine = [t for t in tix if t.get("assignee") == n and t["status"] in ("doing", "open")]
        mine.sort(key=lambda t: (t["status"] != "doing", t["id"]))
        dep = next(((t, d) for t in mine for d in t.get("depends_on") or [] if st.get(d) != "done"), None)
        ready = next((t for t in mine if not any(st.get(d) != "done" for d in t.get("depends_on") or [])), None)
        a = {"state": "idle", "reason": "", "ticket": (ready or (mine[0] if mine else {})).get("key", ""), "dep": "",
             "since": 0, "tool": "", "template": (insts.get(n) or {}).get("template", "")}
        q = next((x for x in hq if x.get("instance") == n), None)
        if w:
            m = _squads._get(w["msg"]) if str(w["msg"] or "").isdigit() else None
            k = _TICKET.search((m or {}).get("text") or "")
            a.update(state="work" if w["state"] == "working" else "wait", since=w["since"],
                     reason="" if w["state"] == "working" else "turn queued",
                     ticket=k.group(1) if k else next((t["key"] for t in mine if t["status"] == "doing"), a["ticket"]))
            if w["state"] == "working":
                a["tool"] = _tool(n, w["since"])
        elif lt and lt["outcome"] != "ok" and lt["ts"] > gone.get(n, 0):      # not dismissed (Squads → Dismiss)
            a.update(state="err", reason=_why(n, lt["msg"], lt["outcome"]), since=lt["ts"], ticket=lt["ticket"] or a["ticket"])
        elif q:
            a.update(state="ask", reason=str(q.get("title") or "")[:120], since=int(q.get("created") or 0),
                     ticket=q.get("ticket") or a["ticket"])
        elif dep and not ready:
            a.update(state="wait", ticket=dep[0]["key"], dep=dep[1], reason=st.get(dep[1], "missing"))
        if lt:      # error also while a new turn runs: a retry loop must not hide why the last one failed
            a["last_turn"] = {"ts": lt["ts"], "ok": lt["outcome"] == "ok", "dur": int((lt["duration_ms"] or 0) / 1000),
                              "error": a["reason"] if a["state"] == "err" else "" if lt["outcome"] == "ok" else _why(n, lt["msg"], lt["outcome"])}
        a["strip"] = [{"start": int(e["ts"] - (e["duration_ms"] or 0) / 1000), "end": e["ts"], "ok": e["outcome"] == "ok",
                       **({} if e["outcome"] == "ok" else {"why": _why(n, json.loads(e.get("detail") or "{}").get("msg"), e["outcome"])})}
                      for e in ev if e["kind"] == "turn_end" and e["member"] == n]
        if a["state"] == "work":
            a["strip"].append({"start": a["since"], "end": 0, "ok": True})     # the running turn
        a["msgs"] = [e["ts"] for e in ev if e["kind"] == "message" and e["member"] == n][-60:]
        out[n] = a
    queued = _squads.queue_info(name)["count"] + sum(1 for w in ws if w["state"] != "working")
    s = _squads.settings(proj)
    idle = s["approved"] and not s["paused"] and not ws and not queued and any(t["status"] != "done" for t in tix)
    ended = max((x["ts"] for x in last.values()), default=0)
    stalled = idle and ended
    return {"members": out, "failed_members": sum(1 for a in out.values() if a["state"] == "err"),
            "stalled_min": max(0, (now - ended) // 60) if stalled else None,
            "stalled_why": _stall_why(tix, out, st) if stalled else "", "stalled_since": ended if stalled else 0,
            "queued": queued}


STALL_TICKETS = 3

# ---- automatic status check: the lead of a stalled squad is woken ----------
# Live 2026-10-23: a lead's last post of a turn went out with wake=false
# ("Ack — holding"), nobody owned the next step and the squad slept for hours
# until the operator pressed "Nudge lead". The idle worker does that now.
STALL_NUDGE_MIN = 15       # nobody worked this long although tickets are open
STALL_NUDGE_MAX = 2        # nudges in a row that moved no ticket -> the operator instead
STALL_NUDGE_FROM = "auto (stall check)"
STALL_NUDGE = ("Status check (automatic): nobody has worked for {min} min although tickets are open. "
               "Holdups: {why}. Unblock what you can and wake the members who can continue — "
               "a post that hands someone the next step must wake them (no wake=false).")
_nudge_check = [0]
_nudged = {}               # project -> {"since": stall start, "n": nudges in a row, "sig": ticket states}


def auto_nudge(now=None):
    """Idle worker: wake the lead of a squad stalled for STALL_NUDGE_MIN, with
    why. Not while the operator holds it (an open question/approval/review or
    proposal): that is a wait for you, not a stall. Once per stall; after
    STALL_NUDGE_MAX nudges in a row without a ticket moving, one notification
    instead (no token loop). -> the projects nudged."""
    now, done = int(now or time.time()), []
    if now - _nudge_check[0] < 60:                    # the idle loop ticks fast; once a minute is plenty
        return done
    _nudge_check[0] = now
    held = {x.get("project") for x in _hitlq.items("open")}
    for proj in _projects.load():
        name = proj["name"]
        if not _squads.settings(proj).get("enabled") or name in held:
            continue
        a = activity(proj, now)
        if a["stalled_min"] is None or a["stalled_min"] < STALL_NUDGE_MIN:
            continue
        if any(p.get("status") in _squadgate.OPEN for p in _squadgate.proposals(name)):
            continue
        last = _nudged.get(name) or {}
        if last.get("since") == a["stalled_since"]:
            continue                                  # this stall had its turn
        sig = json.dumps(sorted((t["key"], t["status"]) for t in _squadgate.tickets(name)))
        n = last.get("n", 0) + 1 if last.get("sig") == sig else 1
        _nudged[name] = {"since": a["stalled_since"], "n": n, "sig": sig}
        if n > STALL_NUDGE_MAX:
            if n == STALL_NUDGE_MAX + 1:
                _notify.notify_add(_squadgate.lead(proj) or name, f"Squad {name}: stalled",
                                   f"{STALL_NUDGE_MAX} automatic status checks moved no ticket. {a['stalled_why']}",
                                   link="squads")
            continue
        m, why = _squadgate.operator_post(proj, STALL_NUDGE_FROM, {"text": STALL_NUDGE.format(
            min=a["stalled_min"], why=a["stalled_why"])})
        if m:
            done.append(name)
    return done


def _stall_why(tix, out, st):
    """Why nobody works although tickets are open: the holdup of each open ticket (a few)."""
    left = [t for t in tix if t["status"] != "done"]
    why = []
    for t in left[:STALL_TICKETS]:
        who = t.get("assignee") or ""
        a = out.get(who)
        dep = next((d for d in t.get("depends_on") or [] if st.get(d) != "done"), "")
        if t["status"] == "blocked":
            r = "blocked"
        elif t["status"] == "review":
            r = "waits for the lead's review"
        elif not who:
            r = "nobody assigned"
        elif a is None:
            r = f"{who} is not a member"
        elif dep:
            r = f"waits for {dep} ({st.get(dep, 'missing')})"
        elif a["state"] == "err":
            r = f"{who}'s last turn failed: {a['reason']}"
        elif a["state"] == "ask":
            r = f"{who} asks you: {a['reason']}"
        else:
            r = f"{who} ended its turn without finishing it"
        why.append(f"#{t['id']} {t['key']} ({t['status']}): {r}")
    return "; ".join(why) + (f"; +{len(left) - STALL_TICKETS} more" if len(left) > STALL_TICKETS else "")
