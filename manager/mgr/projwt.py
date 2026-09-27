# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Project worktrees (security boundary): the git side of the ``worktree``
strategy — every writer works on its own branch ``proj/<project>/<member>``
in its own folder, the lead or the operator reviews and merges.

The writer's folder is plain files (no .git), exported to its VM only. git
never runs on anything a VM can write:

- **Fill / reset** a writer's folder: ``git archive`` in the operator's repo
  (as the repo owner), extracted by ``tar`` as the GUEST user — the process
  enters the folder as root and drops privileges there, so a symlink the VM
  planted can only lead where the guest may write anyway.
- **Snapshot** (before a diff or merge): the guest copies the folder into a
  private stage under /var/tmp that no VM reaches; root removes special files
  and hands the stage to the repo owner; only then git reads it — with an
  explicit GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE, no hooks, no fsmonitor,
  no external diff or textconv. The result is a commit on the member's branch
  (the operator's identity, no trailer); with a subdir the member's tree is
  grafted into the branch's full tree.
- **Merge** into the base branch: when the base is checked out in the repo it
  must be clean and ``git merge --no-ff`` runs there; otherwise ``merge-tree``
  + ``commit-tree`` + ``update-ref``. A conflict changes nothing and names the
  files.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import os
import pwd
import re
import shutil
import stat
import subprocess
import tempfile
import threading

from mgr import host as _host
from mgr import mounts as _mounts

STAGE_ROOT = os.environ.get("REPO_STAGE", "/var/tmp")
WT_MAX_BYTES = int(os.environ.get("PROJECT_WT_MAX_BYTES", str(2 * 1024 ** 3)))
GIT_TIMEOUT = 600
DIFF_MAX = 400_000
REF_RE = re.compile(r"^(?!-)(?!.*\.\.)[A-Za-z0-9._/-]{1,120}(?<!\.lock)(?<!/)$")
GIT_SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
            "-c", "core.untrackedCache=false", "-c", "protocol.file.allow=never",
            "-c", "protocol.ext.allow=never", "-c", "submodule.recurse=false",
            "-c", "core.quotePath=false", "-c", "diff.external=", "-c", "merge.renames=true"]
_lock = threading.Lock()          # one git operation at a time (index files, refs)


class GitError(RuntimeError):
    pass


def trees_root():
    return os.path.join(_mounts.AGENT_ROOT, ".projects")


def tree_dir(project, member):
    return os.path.join(trees_root(), project, "wt", member)


def branch(project, member):
    return f"proj/{project}/{member}"


# ---- processes -------------------------------------------------------------
def _owner(repo):
    st = os.stat(os.path.join(repo, ".git"))
    return st.st_uid, st.st_gid


def _as(uid, gid):
    """subprocess kwargs to run as uid:gid — only when the manager is root."""
    if os.geteuid() != 0 or uid == 0:
        return {}
    return {"user": uid, "group": gid, "extra_groups": []}


def _env(repo, uid):
    try:
        home = pwd.getpwuid(uid).pw_dir
    except KeyError:
        home = "/nonexistent"
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home, "LANG": "C.UTF-8",
            "GIT_DIR": os.path.join(repo, ".git"), "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}


def _run(repo, args, work_tree=None, index=None, text=True):
    """git in the operator's repo, as the repo owner, hardened -> CompletedProcess."""
    uid, gid = _owner(repo)
    env = _env(repo, uid)
    if work_tree:
        env["GIT_WORK_TREE"] = work_tree
    if index:
        env["GIT_INDEX_FILE"] = index
    # umask 022: the manager's own (077, root-only state) would leave the
    # operator's merged files readable by nobody but the owner
    return subprocess.run(["git", *GIT_SAFE, *args], cwd=work_tree or repo, env=env, capture_output=True,
                          text=text, timeout=GIT_TIMEOUT, check=False, umask=0o022, **_as(uid, gid))


def git(repo, args, work_tree=None, index=None, check=True):
    """stdout of a git command; GitError when it fails and `check`."""
    r = _run(repo, args, work_tree, index)
    if check and r.returncode != 0:
        raise GitError(f"git {args[0]}: {(r.stderr or r.stdout).strip()[-400:]}")
    return r.stdout


def git_ok(repo, args, work_tree=None):
    return _run(repo, args, work_tree).returncode == 0


def _git_bytes(repo, args):
    r = _run(repo, args, text=False)
    if r.returncode != 0:
        raise GitError(f"git {args[0]}: {r.stderr.decode('utf-8', 'replace').strip()[-400:]}")
    return r.stdout


def _as_guest(cmd, cwd, input=None):
    """A command as the guest user INSIDE `cwd` (root enters it first, so the
    operator's 0700 home is never walked by the guest)."""
    kw = _as(_mounts.GUEST_UID, _host.ADMIN_GID) if os.geteuid() == 0 else {}
    r = subprocess.run(cmd, cwd=cwd, input=input, capture_output=True, timeout=GIT_TIMEOUT, check=False,
                       umask=0o027, **kw)
    if r.returncode != 0:
        raise GitError(f"{cmd[0]}: {r.stderr.decode('utf-8', 'replace').strip()[-300:]}")


# ---- repo checks -----------------------------------------------------------
def check_repo(path, base, subdir):
    """'' when `path` is a git repo's top (a real .git folder) with branch
    `base` holding `subdir`, else why not. `base` '' = the checked-out branch."""
    if not os.path.isdir(os.path.join(path, ".git")):
        return f"{path} is not the top of a git repository (no .git folder)"
    try:
        base = base or current_branch(path)
        if not base or not REF_RE.match(base):
            return "base must be a branch name"
        if not git_ok(path, ["rev-parse", "--verify", "-q", f"refs/heads/{base}^{{commit}}"]):
            return f"branch {base!r} does not exist"
        if subdir and not git_ok(path, ["cat-file", "-e", f"refs/heads/{base}:{subdir}"]):
            return f"branch {base!r} has no folder {subdir!r}"
    except (OSError, subprocess.SubprocessError) as e:
        return f"git failed: {e}"
    return ""


def current_branch(repo):
    return git(repo, ["symbolic-ref", "--short", "-q", "HEAD"], check=False).strip()


def _sha(repo, ref):
    return git(repo, ["rev-parse", "--verify", "-q", f"{ref}^{{commit}}"]).strip()


# ---- a writer's folder -----------------------------------------------------
def _fill(repo, rev, subdir, dst):
    """Extract rev[:subdir] into dst as the guest."""
    data = _git_bytes(repo, ["archive", "--format=tar", f"{rev}:{subdir}" if subdir else rev])
    _as_guest(["tar", "-x", "--no-same-owner", "--no-same-permissions", "-f", "-"], dst, input=data)


def ensure_tree(proj, member):
    """The writer's branch and folder exist (a re-joining writer gets its
    branch back, not the base). True when the folder was created now."""
    src, name = proj["source"], proj["name"]
    repo, subdir, base = src["path"], src.get("subdir", ""), proj["base"]
    d = tree_dir(name, member)
    if os.path.isdir(d):
        return False
    with _lock:
        ref = f"refs/heads/{branch(name, member)}"
        if not git(repo, ["rev-parse", "--verify", "-q", ref], check=False).strip():
            git(repo, ["update-ref", ref, _sha(repo, f"refs/heads/{base}"), ""])
        for p in (trees_root(), os.path.dirname(os.path.dirname(d)), os.path.dirname(d)):
            _mounts.own_guest_dir(p, 0o2750)
        _mounts.own_guest_dir(d, 0o2770)
        try:
            _fill(repo, ref, subdir, d)
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise
    return True


def remove_tree(project, member):
    """Drop a writer's folder (after its export is gone). The branch stays —
    the work is not lost, a re-join brings it back."""
    shutil.rmtree(tree_dir(project, member), ignore_errors=True)   # fd-based, never follows a symlink


def remove_project(project):
    shutil.rmtree(os.path.join(trees_root(), project), ignore_errors=True)


# ---- snapshot, status, diff ------------------------------------------------
def _du(path):
    n = 0
    for d, dirs, files in os.walk(path):
        for f in files:
            try:
                n += os.lstat(os.path.join(d, f)).st_size
            except OSError:
                pass
    return n


def _stage_copy(src_dir, repo):
    """(stage, tree): a private, stable copy of a writer's folder owned by the
    repo owner. The copy runs as the guest; nothing a VM reaches is read by git."""
    if _du(src_dir) > WT_MAX_BYTES:
        raise GitError(f"the folder is over {WT_MAX_BYTES // 1024 ** 2} MB")
    stage = tempfile.mkdtemp(prefix="kaim56-proj-", dir=STAGE_ROOT)
    t = os.path.join(stage, "t")
    try:
        if os.geteuid() == 0:
            os.chown(stage, _mounts.GUEST_UID, _host.ADMIN_GID)
        _as_guest(["cp", "-a", "--no-preserve=ownership", ".", t], src_dir)
        uid, gid = _owner(repo)
        for d, dirs, files in os.walk(t):            # does not follow symlinks
            for n in dirs + files:
                p = os.path.join(d, n)
                st = os.lstat(p)
                if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode)):
                    os.unlink(p)                     # FIFOs, devices, sockets: never handed to git
                elif os.geteuid() == 0:
                    os.lchown(p, uid, gid)
        if os.geteuid() == 0:
            os.chown(stage, uid, gid)
            os.chown(t, uid, gid)
        return stage, t
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def snapshot(proj, member):
    """Commit the writer's folder on its branch when it changed -> tip sha."""
    src, name = proj["source"], proj["name"]
    repo, subdir = src["path"], src.get("subdir", "")
    ref = f"refs/heads/{branch(name, member)}"
    with _lock:
        tip = _sha(repo, ref)
        stage, t = _stage_copy(tree_dir(name, member), repo)
        try:
            idx = os.path.join(stage, "index")
            git(repo, ["add", "-A", "--", "."], work_tree=t, index=idx)
            tree = git(repo, ["write-tree"], work_tree=t, index=idx).strip()
            if subdir:
                full = os.path.join(stage, "index-full")
                git(repo, ["read-tree", tip], index=full)
                git(repo, ["rm", "-r", "--cached", "-f", "-q", "--ignore-unmatch", "--", subdir], index=full)   # index only
                git(repo, ["read-tree", f"--prefix={subdir}/", tree], index=full)
                tree = git(repo, ["write-tree"], index=full).strip()
            if tree == git(repo, ["rev-parse", f"{tip}^{{tree}}"]).strip():
                return tip
            new = git(repo, ["commit-tree", tree, "-p", tip, "-m", f"{name}: {member}'s work"]).strip()
            git(repo, ["update-ref", ref, new, tip])
            return new
        finally:
            shutil.rmtree(stage, ignore_errors=True)


def status(proj, member):
    """{branch, ahead, behind, files: [[status, path]]} after a snapshot."""
    repo, base = proj["source"]["path"], f"refs/heads/{proj['base']}"
    tip = snapshot(proj, member)
    mb = git(repo, ["merge-base", base, tip]).strip()
    behind, ahead = git(repo, ["rev-list", "--left-right", "--count", f"{base}...{tip}"]).split()
    files = [ln.split("\t", 1) for ln in git(repo, ["diff", "--no-ext-diff", "--no-textconv", "--name-status",
                                                     "--no-renames", mb, tip]).splitlines() if "\t" in ln]
    return {"branch": branch(proj["name"], member), "ahead": int(ahead), "behind": int(behind), "files": files}


def diff(proj, member):
    """The writer's changes against where it forked from the base, as a patch."""
    repo, base = proj["source"]["path"], f"refs/heads/{proj['base']}"
    tip = snapshot(proj, member)
    mb = git(repo, ["merge-base", base, tip]).strip()
    out = git(repo, ["diff", "--no-ext-diff", "--no-textconv", "--stat", "--patch", mb, tip])
    return out if len(out) <= DIFF_MAX else out[:DIFF_MAX] + f"\n… cut at {DIFF_MAX} characters\n"


# ---- merge and discard -----------------------------------------------------
def merge(proj, member):
    """Merge the writer's branch into the base -> {'ok', 'commit'} or {'error', 'conflicts'}."""
    repo, base, name = proj["source"]["path"], proj["base"], proj["name"]
    tip = snapshot(proj, member)
    msg = f"Merge {member}'s work on {name}"
    with _lock:
        base_sha = _sha(repo, f"refs/heads/{base}")
        if git_ok(repo, ["merge-base", "--is-ancestor", tip, base_sha]):
            return {"ok": True, "commit": base_sha, "note": "nothing to merge"}
        if current_branch(repo) == base:
            if git(repo, ["status", "--porcelain", "--untracked-files=no"], work_tree=repo).strip():
                return {"error": f"{base} is checked out in {repo} with uncommitted changes — commit or stash them first",
                        "conflicts": []}
            try:
                git(repo, ["merge", "--no-ff", "--no-edit", "-m", msg, tip], work_tree=repo)
            except GitError as e:
                conflicts = git(repo, ["diff", "--name-only", "--diff-filter=U"], work_tree=repo, check=False)
                git(repo, ["merge", "--abort"], work_tree=repo, check=False)
                return {"error": f"merge conflict — nothing changed ({e})",
                        "conflicts": [c for c in (conflicts or "").splitlines() if c]}
            return {"ok": True, "commit": _sha(repo, "HEAD")}
        r = git(repo, ["merge-tree", "--write-tree", "--name-only", "--no-messages", base_sha, tip], check=False)
        lines = (r or "").splitlines()
        if not lines or not re.match(r"^[0-9a-f]{40}$", lines[0]):
            return {"error": "merge failed", "conflicts": []}
        conflicts = [x for x in lines[1:] if x]
        if conflicts:
            return {"error": "merge conflict — nothing changed", "conflicts": conflicts}
        new = git(repo, ["commit-tree", lines[0], "-p", base_sha, "-p", tip, "-m", msg]).strip()
        git(repo, ["update-ref", f"refs/heads/{base}", new, base_sha])
        return {"ok": True, "commit": new}


def discard(proj, member):
    """Throw the writer's work away: branch back to the base, folder refilled
    in place (same folder — the VM's mount stays valid)."""
    src, name = proj["source"], proj["name"]
    repo, subdir = src["path"], src.get("subdir", "")
    d = tree_dir(name, member)
    with _lock:
        ref = f"refs/heads/{branch(name, member)}"
        git(repo, ["update-ref", ref, _sha(repo, f"refs/heads/{proj['base']}")])
        _as_guest(["find", ".", "-mindepth", "1", "-delete"], d)
        _fill(repo, ref, subdir, d)
    return {"ok": True}
