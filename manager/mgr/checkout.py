# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Checkout (security boundary): put a GitHub repository's files into an
instance's workspace, as src/. Generic — any app or operator can use it
(POST/GET /api/checkout/<instance>); what is done with the code is not the
manager's business.

The clone runs on the HOST as the guest user, never in the VM: a private repo
needs GITHUB_TOKEN from the host secret store, and that token only travels as
an HTTP header in the git process's environment — not in argv (visible in
ps), not in .git/config, never in a VM. git clones into a private staging
folder under /var/tmp (the guest user cannot traverse the operator's home),
hardened (no hooks, no symlinks, no submodules, no file/ext transports); then
only the plain files move into the workspace — no .git reaches the VM, and
git never runs on anything a VM can write (a planted .git/config filter or
fsmonitor would otherwise run on the host). A new checkout replaces src/.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

from mgr import gateway as _gateway
from mgr import host as _host
from mgr import instances as _instances
from mgr import mounts as _mounts
from mgr import secrets as _secrets

TOKEN_KEY = "GITHUB_TOKEN"
REPO_MAX_KB = int(os.environ.get("REPO_MAX_KB", "300000"))     # GitHub's size field (packed), 300 MB
CHECKOUT_MAX_BYTES = 2 * 1024 ** 3
GIT_TIMEOUT = 900
STAGE_ROOT = os.environ.get("REPO_STAGE", "/var/tmp")        # traversable by the guest user

_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_REF = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")
_jobs = {}                       # instance -> the last checkout's state (in memory; src/ itself is the record)
_lock = threading.Lock()


def parse_repo(text):
    """'owner/repo', 'github.com/owner/repo', 'https://github.com/owner/repo(.git)',
    '…/tree/<ref>' or 'git@github.com:owner/repo.git' -> (owner, repo, ref).
    GitHub only; anything else is a ValueError with the reason."""
    t = (text or "").strip()
    m = re.match(r"^git@github\.com:([^/\s]+)/([^/\s]+?)(?:\.git)?$", t)
    if m:
        owner, repo, ref = m.group(1), m.group(2), ""
    else:
        t = re.sub(r"^https?://", "", t)
        t = re.sub(r"^(www\.)?github\.com/", "", t, flags=re.I)
        if "://" in t or t.startswith(("/", ".")) or re.match(r"^[^/]+\.[a-z]{2,}/", t, re.I):
            raise ValueError("only GitHub repositories (github.com/owner/repo)")
        parts = [p for p in t.split("?", 1)[0].split("#", 1)[0].split("/") if p]
        if len(parts) < 2:
            raise ValueError("give the repository as owner/repo or its GitHub URL")
        owner, repo = parts[0], re.sub(r"\.git$", "", parts[1])
        ref = "/".join(parts[3:]) if len(parts) > 3 and parts[2] == "tree" else ""
    if not _OWNER.match(owner) or not _REPO.match(repo) or repo in (".", ".."):
        raise ValueError(f"not a valid GitHub owner/repo: {owner}/{repo}")
    if ref and (not _REF.match(ref) or ref.startswith(("-", "/")) or ".." in ref):
        raise ValueError(f"not a usable branch or tag: {ref}")
    return owner, repo, ref


def token():
    return (_secrets.secret_store().get(TOKEN_KEY) or "").strip()


def _http_get_json(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def github_meta(owner, repo, tok=""):
    """(meta, error): full_name, private, size (KB), default_branch."""
    h = {"Accept": "application/vnd.github+json", "User-Agent": "kaim56-manager",
         "X-GitHub-Api-Version": "2022-11-28"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    try:
        d = _http_get_json(f"https://api.github.com/repos/{owner}/{repo}", h)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403) and tok:
            return None, f"GitHub refused the token (HTTP {e.code}) — check {TOKEN_KEY} in the Secrets tab"
        if e.code == 404:
            return None, (f"{owner}/{repo}: not found, or the token has no read access to it" if tok else
                          f"{owner}/{repo}: not found — if it is private, add a fine-grained GitHub token "
                          f"with read access as {TOKEN_KEY} in the Secrets tab")
        return None, f"GitHub API HTTP {e.code}"
    except Exception as e:
        return None, f"GitHub API unreachable: {e!r}"[:200]
    return {"full_name": d.get("full_name") or f"{owner}/{repo}", "private": bool(d.get("private")),
            "size_kb": int(d.get("size") or 0), "default_branch": d.get("default_branch") or ""}, ""


def git_env(tok, home):
    """The git process's environment: no user/system config, no prompts, and
    the token (if any) as an Authorization header scoped to github.com —
    GIT_CONFIG_* is how actions/checkout does it; argv and .git/config stay clean."""
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": home, "LANG": "C.UTF-8",
           "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_LFS_SKIP_SMUDGE": "1"}
    if tok:
        basic = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
        env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                    "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}"})
    return env


# Hardening for code from outside: no symlinks in the checkout, no hooks, no
# local/ext transports (a crafted submodule URL), no submodules at all.
GIT_SAFE = ["-c", "core.symlinks=false", "-c", "core.hooksPath=/dev/null",
            "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never",
            "-c", "submodule.recurse=false"]


def run_git(args, cwd, tok):
    """Run git as the guest user (when the manager is root) -> CompletedProcess.
    Output is redacted: an error message must never carry the token."""
    kw = {}
    if os.geteuid() == 0:
        kw = {"user": _mounts.GUEST_UID, "group": _host.ADMIN_GID, "extra_groups": [_host.ADMIN_GID]}
    r = subprocess.run(["git", *GIT_SAFE, *args], cwd=cwd, env=git_env(tok, cwd), capture_output=True,
                       text=True, timeout=GIT_TIMEOUT, check=False, **kw)
    if tok:
        r.stdout, r.stderr = r.stdout.replace(tok, "***"), r.stderr.replace(tok, "***")
    return r


def _du(path):
    n = 0
    for d, dirs, files in os.walk(path):
        for f in files:
            try:
                n += os.lstat(os.path.join(d, f)).st_size
            except OSError:
                pass
    return n


def _guest_owned(path, mode):
    if os.geteuid() == 0:
        os.lchown(path, _mounts.GUEST_UID, _host.ADMIN_GID)
    if not os.path.islink(path):
        os.chmod(path, mode)


def clone_into(full_name, ref, tok, dst):
    """Fresh shallow clone as the guest into a private staging folder, then
    only the files (no .git) into `dst`, owned by the guest. Returns the commit."""
    stage = tempfile.mkdtemp(prefix="kaim56-clone-", dir=STAGE_ROOT)
    try:
        _guest_owned(stage, 0o700)
        args = ["clone", "--depth", "1", "--single-branch", "--no-recurse-submodules"]
        if ref:
            args += ["--branch", ref]
        r = run_git(args + ["--", f"https://github.com/{full_name}.git", "wt"], stage, tok)
        if r.returncode != 0:
            raise RuntimeError("git: " + (r.stderr or r.stdout).strip()[-400:])
        wt = os.path.join(stage, "wt")
        commit = run_git(["rev-parse", "HEAD"], wt, tok).stdout.strip()
        shutil.rmtree(os.path.join(wt, ".git"), ignore_errors=True)
        if _du(wt) > CHECKOUT_MAX_BYTES:
            raise RuntimeError(f"checkout over {CHECKOUT_MAX_BYTES // 1024 ** 3} GB")
        new = dst + ".new"
        shutil.rmtree(new, ignore_errors=True)
        shutil.move(wt, new)                     # a rename when /var/tmp and the workspace share a filesystem
        for d, dirs, files in os.walk(new):      # os.walk does not follow symlinks (and clone made none)
            _guest_owned(d, 0o2750)
            for f in files:
                _guest_owned(os.path.join(d, f), 0o640)
        shutil.rmtree(dst, ignore_errors=True)
        os.replace(new, dst)
        return commit
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _spawn(fn, *a):
    threading.Thread(target=fn, args=a, daemon=True).start()


def start(inst_name, text):
    """(ok, state): validate the repo and the instance, then clone in the
    background into the instance's workspace src/. State as in status()."""
    inst = next((i for i in _instances.load_instances() if i["name"] == inst_name), None)
    if inst is None:
        return False, {"status": "failed", "error": f"unknown instance '{inst_name}'"}
    try:
        owner, repo, ref = parse_repo(text)
    except ValueError as e:
        return False, {"status": "failed", "error": str(e)}
    tok = token()
    meta, err = github_meta(owner, repo, tok)
    if err:
        return False, {"status": "failed", "error": err}
    if meta["size_kb"] > REPO_MAX_KB:
        return False, {"status": "failed", "error": f"{meta['full_name']} is {meta['size_kb'] // 1024} MB — "
                                                     f"over the {REPO_MAX_KB // 1024} MB limit"}
    with _lock:
        if (_jobs.get(inst_name) or {}).get("status") == "running":
            return False, {**_jobs[inst_name], "error": "a checkout is already running"}
        _jobs[inst_name] = {"status": "running", "repo": meta["full_name"], "branch": ref or meta["default_branch"],
                            "private": meta["private"], "commit": "", "error": "", "started": int(time.time())}
        started = dict(_jobs[inst_name])        # the state at start, whatever the thread has done by the time we return
    _spawn(_run, inst_name, inst, meta["full_name"], ref, tok)
    return True, started


def _run(inst_name, inst, full_name, ref, tok):
    try:
        ws = _mounts.workspace_dir(inst)
        _mounts.own_guest_dir(ws)
        commit = clone_into(full_name, ref, tok, os.path.join(ws, "src"))
        upd = {"status": "done", "commit": commit}
    except Exception as e:
        msg, _n = _gateway.redact_secrets(str(e))
        if tok:
            msg = msg.replace(tok, "***")
        print(f"[checkout] {inst_name}: {msg[:300]}", flush=True)
        upd = {"status": "failed", "error": msg[:400]}
    with _lock:
        _jobs[inst_name] = {**_jobs.get(inst_name, {}), **upd, "finished": int(time.time())}


def status(inst_name):
    """The last checkout of this instance since the manager started:
    {status: none|running|done|failed, repo, branch, private, commit, error, …}."""
    with _lock:
        return dict(_jobs.get(inst_name) or {"status": "none"})
