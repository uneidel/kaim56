# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Squad kickoff (security boundary): one chat line — a repo URL and a goal —
becomes a squad waiting for its plan. Admin only (mgr/auth.py: default deny).

The first https URL in the text is the repo, the rest the goal. Hosts:
github.com and the Gitea hosts of the site setting SQUAD_GITEA_HOSTS (default
git.kat56.de); others are refused. The squad's name comes from the repo
([a-z0-9-], -2 … when taken). Then, as a job the UI polls:

a. token: the host secret ``SQUAD_GIT_TOKEN_<HOST>`` (github.com → ``…_GITHUB``,
   git.kat56.de → ``…_GIT_KAT56_DE``); without one the clone is tried
   anonymously, and an auth failure names the secret to create. These names
   are reserved: never released to a VM (mgr/squadgit.reserved_secrets).
b. clone on the host into SQUADS_DIR/<name> (site setting; default
   ~<manager owner>/squads, made 0750) as that folder's owner, umask 022:
   full history, no submodules, no hooks, no system/global git config, the
   token only in the environment (squadgit.auth_env) — never argv, the stored
   URL or a log. A timeout and a size cap (SQUAD_MAX_BYTES, default 2 GB,
   watched while cloning and checked after) abort and clean up.
c. project <name>: worktree strategy, the clone as source, its default branch as base.
d. git remote (squadgit) with the token secret — skipped, with a note, without one.
e. instance <name>-architect: template openrouter, persona code-architect,
   model of router tier "strong" (else the template default, said in the
   chat) — squadgate's creation path; the project's lead.
f. squad enabled, NOT approved (only the lead can be woken), budget 2M
   tokens/day; reindex when an indexer exists (queued, never blocks).
g. the goal, posted as user:<name> to @<name>-architect: propose the squad.

Each step skips what is done (a retry resumes). A failure removes what THIS
job created (instance, project, clone) — never anything it found.

Jobs live in memory (the last KEEP_JOBS). Part of the mgr package: no import
from manager.py. Sibling modules are used as ``_name.func`` (module
attribute), so a test can replace one definition in one place.
"""
import os
import pwd
import re
import shutil
import subprocess
import threading
import time
import uuid

from mgr import indexer as _indexer
from mgr import instances as _instances
from mgr import paths as _paths
from mgr import projects as _projects
from mgr import projwt as _projwt
from mgr import secrets as _secrets
from mgr import settings as _settings
from mgr import squadgate as _squadgate
from mgr import squadgit as _squadgit
from mgr import squads as _squads

URL_IN_TEXT = re.compile(r"https://[^\s<>\"'`]+")
GOAL_MAX = 4000
NAME_MAX = 20                        # + "-architect" stays within an instance name's 32
ARCH_SUFFIX, ARCH_PERSONA, ARCH_TIER = "-architect", "code-architect", "strong"
BUDGET = 2_000_000
CLONE_TIMEOUT = int(_settings.SITE.get("SQUAD_CLONE_TIMEOUT") or 900)
MAX_BYTES = int(_settings.SITE.get("SQUAD_MAX_BYTES") or 2 * 1024 ** 3)
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
    d = str(_settings.SITE.get("SQUADS_DIR") or "").strip()
    if d:
        return os.path.realpath(d)
    try:
        home = pwd.getpwuid(os.stat(_paths.BASE).st_uid).pw_dir
    except (KeyError, OSError):
        home = os.path.expanduser("~")
    return os.path.join(home, "squads")


def hosts():
    """{host: provider} the kickoff clones from."""
    g = _settings.SITE.get("SQUAD_GITEA_HOSTS", "git.kat56.de")
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
    return {k: job[k] for k in ("id", "name", "arch", "url", "repo", "provider", "goal", "status", "log",
                                "created", "who")}


def jobs():
    return [view(j) for j in sorted(_jobs.values(), key=lambda j: j["created"], reverse=True)]


def get(jid):
    j = _jobs.get(str(jid))
    return view(j) if j else None


def start(text, who):
    """A new kickoff -> (job view, '') or (None, why). The steps run in a thread."""
    f, why = parse(text)
    if not f:
        return None, why
    with _lock:
        name = derive_name(f["repo"])
        if not name:
            return None, f"no free squad name for {f['repo']}"
        job = {**f, "id": uuid.uuid4().hex[:10], "name": name, "arch": name + ARCH_SUFFIX, "who": who,
               "status": "running", "log": [], "made": [], "done": [], "created": time.time(), "base": ""}
        _jobs[job["id"]] = job
        for old in sorted(_jobs.values(), key=lambda j: j["created"])[:-KEEP_JOBS]:
            _jobs.pop(old["id"], None)
    _say(job, f"Kickoff of squad {name}: {f['url']}")
    _spawn(run, job["id"])
    return view(job), ""


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
            for step in STEPS:
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
            elif what == "project":
                _projects.delete(job["name"])
                _squads.purge(job["name"])
                _squadgate.purge(job["name"])
            elif what == "clone":
                shutil.rmtree(job["path"], ignore_errors=True)
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


def _squads_root():
    root = squads_dir()
    if not os.path.isdir(root):
        st = os.stat(os.path.dirname(root))
        os.makedirs(root, 0o750, exist_ok=True)
        os.chmod(root, 0o750)
        if os.geteuid() == 0:
            os.chown(root, st.st_uid, st.st_gid)
    st = os.stat(root)
    return root, st.st_uid, st.st_gid


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
    root, uid, gid = _squads_root()
    dest = job["path"] = os.path.join(root, job["name"])
    if "clone" in job["made"] and os.path.isdir(os.path.join(dest, ".git")):
        return _say(job, f"Clone: {dest} is there.")
    if os.path.lexists(dest):
        raise Stop(f"{dest} exists and is not this kickoff's — remove it or rename the squad")
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
    job["base"] = _projwt.current_branch(dest)
    if not job["base"]:
        raise Stop("the repository is empty: no default branch to work on")
    _say(job, f"Cloned ({size / 2**20:.1f} MB), default branch {job['base']}.")


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
             "skills": [], "model_tier": ARCH_TIER, "model": "", "template": "openrouter", "role": "lead",
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
        model = (inst.get("config") or {}).get("OPENROUTER_MODEL")
        _say(job, f"Instance {arch}: persona {ARCH_PERSONA}, model "
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
                                              "daily_token_budget": BUDGET}, proj.get("squad"))
        _projects.set_squad(name, s)
        job["done"].append("squad")
        _say(job, f"Squad enabled, budget {BUDGET:,} tokens/day — waiting for your approval of the plan.")
    if "index" not in job["done"]:
        job["done"].append("index")
        if _indexer.instance():
            _say(job, "Code index: " + _indexer.reindex(name, "squad kickoff"))


def step_post(job):
    if "post" in job["done"]:
        return
    arch = job["arch"]
    text = (f"@{arch} Kickoff by {job['who']}. Repository {job['url']} — at /project/{job['name']}, base branch "
            f"{job['base']}.\n\nGoal:\n{job['goal']}\n\nStudy the code, then propose the squad with squad_propose: "
            f"members (persona, skills, model tier, role), tickets with their dependencies, and a daily token "
            f"budget (default {BUDGET}). Nothing else runs before I approve.")
    m, why = _squads.post(_projects.get(job["name"]), "user:" + job["who"], [arch], text)
    if not m:
        raise Stop("posting the goal failed: " + why)
    job["done"].append("post")
    if m.get("blocked"):
        _say(job, f"Goal posted, not delivered: {m['blocked']}", ok=False)
    else:
        _say(job, f"Goal posted to @{arch}.")


STEPS = (step_token, step_clone, step_project, step_remote, step_architect, step_squad, step_post)


# ---- the Squads page's list ---------------------------------------------------------
def overview():
    """The squad-enabled projects: name, status, lead, pending proposals, last message."""
    out = []
    for p in sorted(_projects.load(), key=lambda x: x.get("name", "")):
        s = _squads.settings(p)
        if not s["enabled"]:
            continue
        last = _squads.read(p["name"], None, 0, 1)
        pend = sum(1 for x in _squadgate.proposals(p["name"]) if x.get("status") == "pending")
        out.append({"name": p["name"], "lead": _squadgate.lead(p),
                    "status": "paused" if s["paused"] else "running" if s["approved"] else "waiting for approval",
                    "pending": pend, "members": len(p.get("members") or {}),
                    "last_id": last[-1]["id"] if last else 0, "last_ts": last[-1]["ts"] if last else 0,
                    "remote": (_squadgit.settings(p) or {}).get("url", "")})
    return out
