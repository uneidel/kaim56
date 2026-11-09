# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Deploy target "instance" (security boundary): a project's service in its own Firecracker VM.

What celld cannot run — any server a squad writes (Python, Node, Go …) — runs
as ``svc-<project>``: the base image the squad's members use plus the squad's
toolchain layer (mgr/toolchain.py), so it has the tools the squad built and
tested with. ``build``, ``start`` and ``port`` are the squad's: ``deploy.json`` at
the root of the base branch (the code it runs is the squad's anyway), else the
operator's; RAM and vCPUs only the operator's. A deploy is the operator's (or
the lead's) button.

A deploy: ``git archive`` of the project's base branch -> a read-only release
image (``mkfs.ext4 -d``: the host never mounts it), a fresh throwaway upper, the
persistent data disk (``/data``, kept across deploys), a config disk with the
spec and the operator's secrets for this service (env of build and start; set
only by the operator, names shown, values never again, never to a member) — no
LLM key, no NFS, no host folders. The VM
reaches the internet but not the host (a mangle rule drops every new
connection to a host address; the manager's own connections to it are
answered). guest-init copies the release, runs ``build`` and ``start`` as uid
1000 (env PORT, DATA_DIR, BASE_PATH) and prints KAIM56_DEPLOY_RESULT lines on
the console. The manager waits until the port answers; a failed deploy boots
the previous release again and keeps the log.

Reached only through the manager on the apps' own origin (settings.APPS_HOST):
``/d/<project>/…`` (GET, POST; the path arrives unchanged, so serve under BASE_PATH), admin login there; the kAIm56 session cookie
and Authorization never reach the VM, the VM cannot set that cookie.

State: deploys.json in the manager dir (root-only), keyed by project; images in
instances/deploy/<project>/. Part of the mgr package: no import from
manager.py. Sibling modules are used as ``_name.func`` (module attribute), so a
test can replace one definition in one place.
"""
import http.client
import io
import json
import os
import re
import shlex
import shutil
import tarfile
import tempfile
import threading
import time

from mgr import instances as _instances
from mgr import netfw as _netfw
from mgr import notify as _notify
from mgr import paths as _paths
from mgr import projects as _projects
from mgr import projwt as _projwt
from mgr import squads as _squads
from mgr import toolchain as _toolchain
from mgr import util as _util
from mgr import vm as _vm

STATE_FILE = os.path.join(_paths.BASE, "deploys.json")
DIR = os.path.join(_paths.INST_DIR, "deploy")
INDEXES = range(230, 250)          # service VMs' /30s: above the instances, below the toolchain builders
UPPER_MB, DATA_MB = 16384, 4096    # sparse
TIMEOUT = 30 * 60                  # build + start until the port answers; the guest stops the build at 25 min
RESULT = "KAIM56_DEPLOY_RESULT="
LOG_TAIL = 8000
RESTART_EVERY = 600                # a service that died is booted again at most this often (sweep)
SESSION_COOKIE = "__Host-kaim56_apps"
MAX_BODY = 64 * 1024 * 1024
SECRET_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
SECRET_RESERVED = {"PATH", "HOME", "PORT", "DATA_DIR", "BASE_PATH", "GUEST_DNS", "IFS", "ENV", "BASH_ENV", "SHELL"}
SECRETS_MAX, SECRET_LEN = 20, 8192
REPO_SPEC = "deploy.json"             # {build, start, port} in the base branch
SPEC_DEFAULT = {"build": "", "start": "", "port": 8080, "mem": 1024, "vcpus": 1}
_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-authorization",
        "proxy-authenticate", "content-length", "host"}
_lock = threading.Lock()           # the state file
_plocks, _plocks_lock = {}, threading.Lock()
_restarted = {}


# ---- state -------------------------------------------------------------------
def _load():
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _update(project, fn):
    with _lock:
        d = _load()
        r = d.setdefault(project, {"spec": dict(SPEC_DEFAULT), "state": "none"})
        fn(r)
        tmp = STATE_FILE + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(d, fh, indent=1)
        os.replace(tmp, STATE_FILE)
        return dict(r)


def record(project):
    return _load().get(project) or {"spec": dict(SPEC_DEFAULT), "state": "none"}


def _plock(project):
    with _plocks_lock:
        return _plocks.setdefault(project, threading.Lock())


def name_of(project):
    return "svc-" + project


def normalize(spec):
    """The operator's spec -> (spec, '') or (None, why)."""
    s = spec if isinstance(spec, dict) else {}
    out = {}
    for k in ("build", "start"):
        v = str(s.get(k) or "").strip()
        if len(v) > 2000 or "\0" in v:
            return None, f"{k}: at most 2000 characters"
        out[k] = v
    try:
        out["port"], out["mem"], out["vcpus"] = (int(s.get(k) or SPEC_DEFAULT[k]) for k in ("port", "mem", "vcpus"))
    except (TypeError, ValueError):
        return None, "port, mem and vcpus are numbers"
    if not 1024 <= out["port"] <= 65535:
        return None, "port: 1024–65535"
    if not 256 <= out["mem"] <= 8192:
        return None, "mem: 256–8192 MiB"
    if not 1 <= out["vcpus"] <= 4:
        return None, "vcpus: 1–4"
    return out, ""


def save_spec(project, spec):
    s, why = normalize(spec)
    if s is None:
        return None, why
    _update(project, lambda r: r.update(spec=s))
    return s, ""


def save_secrets(project, secrets):
    """The operator's {NAME: value} -> (names, '') or (None, why). An empty value removes NAME."""
    if not isinstance(secrets, dict):
        return None, "secrets: {NAME: value}"
    for k, v in secrets.items():
        if not SECRET_NAME.match(k) or k in SECRET_RESERVED or k.startswith(("DEPLOY_", "LD_")):
            return None, f"{k}: a name of A-Z, 0-9 and _ (upper case), not PATH, PORT, DEPLOY_* or LD_*"
        if not isinstance(v, str) or len(v) > SECRET_LEN or "\0" in v:
            return None, f"{k}: a text of at most {SECRET_LEN} characters"
    merged = lambda r: {k: v for k, v in {**r.get("secrets", {}), **secrets}.items() if v}
    if len(merged(record(project))) > SECRETS_MAX:
        return None, f"at most {SECRETS_MAX} secrets"
    return sorted(_update(project, lambda r: r.update(secrets=merged(r)))["secrets"]), ""


def _base_ref(proj):
    return proj.get("base") or _projwt.current_branch(proj["source"]["path"])


def effective(proj, ref=None):
    """The spec a deploy of `ref` (default the base branch) runs with -> (spec, '') or (None, why):
    build/start/port from its deploy.json if there is one, RAM and vCPUs the operator's."""
    op = record(proj["name"])["spec"]
    raw = _projwt.git(proj["source"]["path"], ["show", f"{ref or _base_ref(proj)}:{REPO_SPEC}"], check=False)
    try:
        rs = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return None, f"{REPO_SPEC} in the base branch is not JSON"
    if not isinstance(rs, dict):
        return None, f"{REPO_SPEC}: an object {{build, start, port}}"
    if not (rs.get("start") or op.get("start")):
        return None, f"no start command: add {REPO_SPEC} {{build, start, port}} to the base branch (or set it in Squads -> Settings -> Deploy)"
    mine = {k: rs[k] for k in ("build", "start", "port") if k in rs} if rs.get("start") else {}
    s, why = normalize({**op, **mine})
    return (s, "") if s else (None, (f"{REPO_SPEC}: " if rs.get("start") else "") + why)


# ---- the VM --------------------------------------------------------------------
def _inst(project, index):
    return {"name": name_of(project), "index": index, "internet": True, "config": {}}


def _free_index(project):
    taken = {i.get("index") for i in _instances.load_instances()}
    taken |= {r.get("index") for p, r in _load().items() if p != project}
    return next((n for n in INDEXES if n not in taken), None)


def _pids(project):
    """Live Firecrackers of this service, found by `--id svc-<project>` in /proc —
    not by a pidfile: a VM the pidfile lost track of was never halted (seen live)."""
    needle, out = b"\0--id\0" + name_of(project).encode() + b"\0", []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                cmd = b"\0" + fh.read()
            with open(f"/proc/{pid}/stat") as fh:
                zombie = fh.read().rsplit(")", 1)[1].split()[0] == "Z"
        except (OSError, IndexError):
            continue
        if needle in cmd and not zombie:
            out.append(int(pid))
    return out


def running(project):
    return bool(_pids(project))


def _guard(inst, on):
    """The service must not open a connection to the host (no manager API, no
    NFS); answers to the manager's own connections pass."""
    spec = ("-i", _instances.net_of(inst)["tap"], "-m", "addrtype", "--dst-type", "LOCAL",
            "-m", "conntrack", "!", "--ctstate", "ESTABLISHED,RELATED", "-j", "DROP")
    while _util.sh("iptables", "-t", "mangle", "-C", "PREROUTING", *spec, check=False).returncode == 0:
        _util.sh("iptables", "-t", "mangle", "-D", "PREROUTING", *spec, check=False)
    if on:
        _util.sh("iptables", "-t", "mangle", "-I", "PREROUTING", "1", *spec, check=False)


def _base(proj):
    """(base template, toolchain layer path or None): a base image the squad has
    a ready layer for, openrouter first; none -> openrouter without a layer."""
    sid = _squads.squad_id(proj) if proj else ""
    layers = ((_toolchain._load().get(sid) or {}).get("layers") or {}) if sid else {}
    for tpl in ("openrouter", "claude"):
        lay = layers.get(tpl)
        if lay and lay.get("ok") and lay.get("base_image_id") == _toolchain.base_image_id(tpl):
            p = _toolchain.layer_path(sid, tpl, lay.get("version") or 0)
            if os.path.exists(p):
                return tpl, p
    return "openrouter", None


def _dir(project):
    d = os.path.join(DIR, project)
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def _release_path(project, n):
    return os.path.join(DIR, project, f"release-{int(n)}.ext4")


def make_release(proj, n):
    """The base branch's tree -> release-<n>.ext4 (mkfs -d; never mounted here) -> commit sha."""
    repo, base = proj["source"]["path"], proj.get("base") or _projwt.current_branch(proj["source"]["path"])
    sha = _projwt.git(repo, ["rev-parse", "--verify", base + "^{commit}"]).strip()
    tar = _projwt._git_bytes(repo, ["archive", "--format=tar", sha])
    tmp = tempfile.mkdtemp(prefix="release.", dir=_dir(proj["name"]))
    try:
        with tarfile.open(fileobj=io.BytesIO(tar)) as tf:
            tf.extractall(tmp, filter="data")
        size = sum(os.lstat(os.path.join(d, f)).st_size for d, _s, fs in os.walk(tmp) for f in fs)
        if not _vm.mkfs_image(_release_path(proj["name"], n), max(64, size * 2 // 2**20 + 64), "fcrelease", srcdir=tmp):
            raise RuntimeError("mkfs of the release image failed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return sha


def config_files(project, spec):
    """The config disk: the operator's secrets for this service, then the spec, DNS, PATH (they win) —
    nothing from an instance, the settings or the secret store."""
    env = (*sorted(record(project).get("secrets", {}).items()), ("DEPLOY_SERVICE", "1"), ("DEPLOY_BUILD", spec["build"]), ("DEPLOY_START", spec["start"]),
           ("DEPLOY_PORT", str(spec["port"])), ("BASE_PATH", f"/d/{project}"), ("GUEST_DNS", _netfw.GUEST_DNS),
           ("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"))
    return {"config.env": "".join(f"{k}={shlex.quote(v)}\n" for k, v in env)}


def _config_disk(project, spec):
    name = name_of(project)
    d = os.path.join(_paths.RUN_DIR, f"{name}.cfgdir")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    for f, txt in config_files(project, spec).items():
        with open(os.path.join(d, f), "w") as fh:
            fh.write(txt)
        os.chmod(os.path.join(d, f), 0o644)
    os.chmod(d, 0o755)
    img = os.path.join(_paths.RUN_DIR, f"{name}.config.ext4")
    ok = _vm.mkfs_image(img, 16, "fcconfig", srcdir=d)
    shutil.rmtree(d, ignore_errors=True)
    if not ok:
        raise RuntimeError("mkfs of the config disk failed")
    return img


def spec_of(rec, n):
    """The spec release n was deployed with (a rollback must not take the new, broken one)."""
    return (rec.get("specs") or {}).get(str(n)) or rec["spec"]


def vm_config(project, rec, n, cfg_img, upper, data, base, layer):
    inst = _inst(project, rec["index"])
    net, spec = _instances.net_of(inst), spec_of(rec, n)
    drives = [{"drive_id": "rootfs", "path_on_host": os.path.join(_paths.BASE, _toolchain.BASES[base]),
               "is_root_device": True, "is_read_only": True},
              {"drive_id": "config", "path_on_host": cfg_img, "is_root_device": False, "is_read_only": True},
              {"drive_id": "release", "path_on_host": _release_path(project, n), "is_root_device": False, "is_read_only": True},
              {"drive_id": "data", "path_on_host": data, "is_root_device": False, "is_read_only": False}]
    boot = (f"console=ttyS0 reboot=k panic=1 pci=off ip={net['guest']}::{net['host']}:{net['mask']}::eth0:off "
            f"init=/init fc_svc=1 fc_release=/dev/vdc fc_data=/dev/vdd")
    if layer:
        drives.append({"drive_id": "toolchain", "path_on_host": layer, "is_root_device": False, "is_read_only": True})
        boot += f" fc_layer=/dev/vd{chr(ord('a') + len(drives) - 1)}"
    drives.append({"drive_id": "upper", "path_on_host": upper, "is_root_device": False, "is_read_only": False})
    boot += f" fc_upper=/dev/vd{chr(ord('a') + len(drives) - 1)}"
    return _vm.add_limits({"boot-source": {"kernel_image_path": _paths.KERNEL, "boot_args": boot}, "drives": drives,
                           "network-interfaces": [{"iface_id": "eth0", "host_dev_name": net["tap"], "guest_mac": net["mac"]}],
                           "machine-config": {"vcpu_count": spec["vcpus"], "mem_size_mib": spec["mem"]}})


def _logpath(project):
    return os.path.join(_paths.RUN_DIR, name_of(project) + ".log")


def boot(project, n):
    """Start release n (the VM must be down) -> pid."""
    rec, proj = record(project), _projects.get(project)
    inst = _inst(project, rec["index"])
    d = _dir(project)
    data = os.path.join(d, "data.ext4")
    if not os.path.exists(data) and not _vm.mkfs_image(data, DATA_MB, "fcdata"):
        raise RuntimeError("mkfs of the data disk failed")
    upper = os.path.join(d, "upper.ext4")
    if not _vm.mkfs_image(upper, UPPER_MB, "fcupper"):            # fresh each boot: the build starts clean
        raise RuntimeError("mkfs of the upper failed")
    base, layer = _base(proj)
    cfg = vm_config(project, rec, n, _config_disk(project, spec_of(rec, n)), upper, data, base, layer)
    _netfw.ensure_net_base()
    _netfw.setup_tap(inst, *(_vm.jail_ids(inst["index"]) if _vm.jailed() else ()))
    _guard(inst, True)
    with open(_logpath(project), "wb") as log:
        proc = _vm.popen_vm(inst["name"], inst["index"], cfg, log)
    _util._wlog(f"deploy {project}: release {n} booting ({base}{' + toolchain' if layer else ''})")
    return proc.pid


def halt(project):
    """Stop the service VM (graceful, then SIGKILL) and tidy its tap, guard, jail."""
    rec = record(project)
    how = "not running"
    for pid in _pids(project):
        how = _vm.halt(name_of(project), pid)
    if rec.get("index") is not None:
        inst = _inst(project, rec["index"])
        _guard(inst, False)
        _netfw.teardown_tap(inst)
    _vm.jail_clean(name_of(project))
    try:
        os.remove(os.path.join(_paths.RUN_DIR, name_of(project) + ".config.ext4"))
    except OSError:
        pass
    return how


def _tail(project):
    return _toolchain._tail(_logpath(project), LOG_TAIL)


def result(log):
    hits = [ln.split(RESULT, 1)[1].strip() for ln in log.splitlines() if RESULT in ln]
    return hits[-1] if hits else ""


def _answers(host, port, path="/"):
    try:
        c = http.client.HTTPConnection(host, port, timeout=3)
        c.request("GET", path)
        ok = c.getresponse().status < 400
        c.close()
        return ok
    except (OSError, http.client.HTTPException):
        return False


_sleep = time.sleep


def wait_ready(project, n, timeout=TIMEOUT):
    """-> '' when release n answers on its port, else why."""
    (host, port), end = address(project, n), time.time() + timeout
    while time.time() < end:
        r = result(_tail(project))
        if r.startswith("fail"):
            return r
        if r == "built" and _answers(host, port, f"/d/{project}/"):     # the path the proxy sends
            return ""
        if not running(project):
            return "the VM ended" + (f" ({r})" if r else "")
        _sleep(3)
    return f"timeout: no answer on port {port} after {timeout // 60} min"


# ---- deploy, stop, remove --------------------------------------------------------
def deploy(project, who="operator"):
    """Start a deploy in the background -> (msg, '') or (None, why)."""
    proj = _projects.get(project)
    if proj is None:
        return None, "unknown project"
    if (proj.get("source") or {}).get("type") != "host":
        return None, "a deploy needs a host-folder repo"
    rec = record(project)
    _s, why = effective(proj)
    if why:
        return None, why
    if rec.get("state") == "deploying":
        return None, "a deploy is running"
    if not _plock(project).acquire(blocking=False):
        return None, "a deploy is running"
    _update(project, lambda r: r.update(state="deploying", started=int(time.time()), error="", by=who))
    threading.Thread(target=_run, args=(project, who), daemon=True, name="deploy").start()
    return f"deploying {project} …", ""


def _run(project, who):
    try:
        _deploy(project, who)
    except Exception as e:
        why = f"{type(e).__name__}: {e}"[:400]
        _update(project, lambda r: r.update(state="failed", error=why))
        _util._wlog(f"deploy {project}: {e!r}")
    finally:
        _plock(project).release()


def _deploy(project, who):
    proj = _projects.get(project)
    rec = record(project)
    if rec.get("index") is None:
        idx = _free_index(project)
        if idx is None:
            raise RuntimeError("no free network slot for a service VM")
        rec = _update(project, lambda r: r.update(index=idx))
    prev, n = rec.get("release"), int(rec.get("last") or rec.get("release") or 0) + 1
    sha = make_release(proj, n)
    spec, why = effective(proj, sha)                 # the deploy.json of exactly this release
    if why:
        raise RuntimeError(why)
    _update(project, lambda r: r.update(last=n, specs={**(r.get("specs") or {}), str(n): spec}))
    halt(project)
    boot(project, n)
    why = wait_ready(project, n)
    tail = _tail(project)
    if not why:
        _update(project, lambda r: r.update(state="running", release=n, prev=prev, commit=sha, ok_at=int(time.time()),
                                            error="", log_tail=tail[-LOG_TAIL:]))
        _drop_old(project, {n, prev})
        seen, good = smoke(project)
        return _tell(proj, f"Deployed {project} ({sha[:10]}) — /d/{project}/ · through the proxy: {seen}", ok=good)
    halt(project)
    if prev:
        boot(project, prev)
        back = wait_ready(project, prev)
        state = "running" if not back else "failed"
        msg = f"Deploy of {project} ({sha[:10]}) failed: {why} — " + (
            f"release {prev} runs again" if not back else f"release {prev} did not come back either: {back}")
    else:
        state, msg = "failed", f"Deploy of {project} ({sha[:10]}) failed: {why}"
        halt(project)
    _update(project, lambda r: r.update(state=state, error=why[:400], log_tail=tail[-LOG_TAIL:]))
    _tell(proj, msg, ok=False)


def _drop_old(project, keep):
    d = os.path.join(DIR, project)
    for f in os.listdir(d):
        m = re.fullmatch(r"release-(\d+)\.ext4", f)
        if m and int(m.group(1)) not in keep:
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass
    _update(project, lambda r: r.update(specs={k: v for k, v in (r.get("specs") or {}).items() if int(k) in keep}))


def _tell(proj, text, ok):
    for v in record(proj["name"]).get("secrets", {}).values():   # the service's console is its own: it may print one
        text = text.replace(v, "***")
    try:
        if _squads.settings(proj).get("enabled"):
            _squads.post(proj, "manager", ["all"], text, kind="note")
        _notify.notify_add("deploy", text, "" if ok else "the log is in Squads → Deploy", link="squads")
    except Exception as e:
        _util._wlog(f"deploy note {proj['name']}: {e!r}")


def stop(project):
    with _plock(project):
        how = halt(project)
        _update(project, lambda r: r.update(state="stopped"))
    return how


def remove(project):
    """The service goes: VM, images, data disk, state."""
    with _plock(project):
        halt(project)
        shutil.rmtree(os.path.join(DIR, project), ignore_errors=True)
        with _lock:
            d = _load()
            d.pop(project, None)
            tmp = STATE_FILE + ".tmp"
            with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
                json.dump(d, fh, indent=1)
            os.replace(tmp, STATE_FILE)


def sweep():
    """Idle cycle: a service that should run and does not (host reboot, crash)
    boots its release again — at most every RESTART_EVERY s."""
    now = time.time()
    for project, r in _load().items():
        if r.get("state") != "running" or not r.get("release") or running(project):
            continue
        if now - _restarted.get(project, 0) < RESTART_EVERY or not _plock(project).acquire(blocking=False):
            continue
        _restarted[project] = now
        try:
            halt(project)
            boot(project, r["release"])
        except Exception as e:
            _util._wlog(f"deploy {project}: restart failed: {e!r}")
        finally:
            _plock(project).release()


def status(project):
    r = record(project)
    out = {k: r.get(k) for k in ("spec", "state", "release", "prev", "commit", "ok_at", "started", "error", "by")}
    out["secrets"] = sorted(r.get("secrets", {}))           # names only: a value never leaves deploys.json
    proj = _projects.get(project)
    try:
        out["run_spec"], out["spec_error"] = effective(proj) if proj and (proj.get("source") or {}).get("type") == "host" else (None, "")
    except Exception as e:                                   # a broken repo never costs the board
        out["run_spec"], out["spec_error"] = None, str(e)[:200]
    out["running"] = running(project)
    if r.get("state") == "running" and not out["running"]:
        out["state"] = "down"
    out["url"] = f"/d/{project}/"
    return out


def log(project):
    return _tail(project) or record(project).get("log_tail") or ""


def smoke(project, hops=5):
    """-> (what a browser gets at /d/<project>/ through the proxy, ok): status, redirects
    followed, the page title or the body's start. The squad cannot open that URL itself."""
    path, trail = "", []
    for _ in range(hops):
        st, hdrs, data = forward("GET", project, path, {})
        trail.append(str(st))
        loc = next((v for k, v in hdrs if k.lower() == "location"), "")
        if not (300 <= st < 400 and loc.startswith(f"/d/{project}/")):
            text = data[:4000].decode("utf-8", "replace")
            m = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
            show = (f"title “{m.group(1).strip()[:80]}”" if m else " ".join(text.split())[:160]) or "empty body"
            return " → ".join(trail) + f" · {show}", st < 400
        path = loc[len(f"/d/{project}/"):]
    return " → ".join(trail) + " · redirect loop", False


def services():
    """Every project with a service VM: its status, for the Instances tab."""
    return [{"project": p, **status(p)} for p in sorted(_load())]


# ---- the proxy -------------------------------------------------------------------
def _strip_cookie(value):
    keep = [c for c in (value or "").split(";") if c.strip() and c.strip().split("=", 1)[0] != SESSION_COOKIE]
    return "; ".join(c.strip() for c in keep)


def address(project, n=None):
    """(guest ip, port) of release n (default: the running one)."""
    r = record(project)
    return _instances.net_of(_inst(project, r["index"]))["guest"], spec_of(r, n or r.get("release"))["port"]


def forward(method, project, rel, headers, body=b""):
    """One request to the service -> (status, [(header, value)], body). 503 when it is not up."""
    r = record(project)
    if r.get("state") not in ("running", "deploying") or not running(project):
        return 503, [("Content-Type", "text/plain; charset=utf-8")], f"{project} is not running".encode()
    host, port = address(project)
    fwd = {k: v for k, v in headers.items() if k.lower() not in _HOP and k.lower() not in ("authorization", "cookie")}
    ck = _strip_cookie(headers.get("Cookie", ""))
    if ck:
        fwd["Cookie"] = ck
    fwd.update({"X-Forwarded-Prefix": f"/d/{project}", "X-Forwarded-Proto": "https"})
    try:
        c = http.client.HTTPConnection(host, port, timeout=120)
        # the full path: the service serves under BASE_PATH (stripping it made /d/x/ -> / -> 302 /d/x/ loop)
        c.request(method, f"/d/{project}/" + rel, body=body or None, headers=fwd)
        resp = c.getresponse()
        data = resp.read(MAX_BODY + 1)
        c.close()
    except (OSError, http.client.HTTPException) as e:
        return 502, [("Content-Type", "text/plain; charset=utf-8")], f"{project}: no answer ({e})".encode()
    if len(data) > MAX_BODY:
        return 502, [("Content-Type", "text/plain; charset=utf-8")], f"{project}: answer over {MAX_BODY >> 20} MB".encode()
    out = []
    for k, v in resp.getheaders():
        kl = k.lower()
        if kl in _HOP or (kl == "set-cookie" and v.split("=", 1)[0].strip() == SESSION_COOKIE):
            continue
        if kl == "location" and v.startswith("/") and not v.startswith("//") and not v.startswith(f"/d/{project}/"):
            v = f"/d/{project}" + v                     # a redirect to the service's own root stays under its prefix
        out.append((k, v))
    out += [("Cross-Origin-Opener-Policy", "same-origin"), ("X-Frame-Options", "DENY")]
    return resp.status, out, data
