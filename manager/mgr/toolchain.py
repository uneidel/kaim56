# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Squad toolchain layer (security boundary): one read-only overlay layer per squad and base image.

Every squad member used to install Go, gcc, GL libs … itself, into its own
persistent upper: the same work N times, tokens, and versions that drift. A
squad now has ONE spec (squad setting ``toolchain``: apt packages, a root
script, env — validated in mgr/squads.py; it changes only by the operator:
the settings dialog or approving the lead's ``toolchain_propose``).

Build: never on the host. A throwaway builder VM per base image the members
use (claude / openrouter rootfs) boots with an EMPTY 16 GiB upper, a config
disk that holds only ``TOOLCHAIN_BUILD=1`` and the generated script — no
secrets, no credentials, no skills, no mounts, but internet; it cannot reach
the host at all (a mangle rule drops everything to a host address).
guest-init runs the script as root (30 min limit), freezes the upper and
reboots; the result is the last ``KAIM56_TOOLCHAIN_RESULT=`` line on the
console log. On success the upper image is copied (``cp --sparse=always``),
checked with e2fsck (no mount on the host, ever) and recorded; on failure
the log tail is kept and the previous layer stays active.

Use: vm.gen_config attaches the ready layer of the member's base image
read-only (``fc_layer=``); guest-init stacks it between the base and the
member's own upper: base (ro) < toolchain (ro) < member upper (rw). A layer
whose base image changed since its build is skipped ("stale") and rebuilt
once by the idle sweep.

State: toolchains.json in the manager dir (root-only), keyed by squad id;
layers in instances/toolchains/<squad_id>-<base>-v<N>.ext4 — paths only
from a validated squad id, a known base name and an int.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time

from mgr import instances as _instances
from mgr import netfw as _netfw
from mgr import notify as _notify
from mgr import paths as _paths
from mgr import projects as _projects
from mgr import squads as _squads
from mgr import util as _util
from mgr import vm as _vm

STATE_FILE = os.path.join(_paths.BASE, "toolchains.json")
LAYER_DIR = os.path.join(_paths.INST_DIR, "toolchains")
BASES = {"claude": "instances/claude-rootfs.ext4", "openrouter": "instances/openrouter-rootfs.ext4"}
SID_RE = re.compile(r"^[a-z0-9]{16}$")
UPPER_MB = 16384                # the builder's empty upper (sparse)
BUILD_TIMEOUT = 35 * 60         # the manager's limit; the guest stops the script at 30 min
GRACE = 15                      # after the result line: time to reboot by itself
BUILDER_INDEXES = range(250, 255)   # builders' /30s, far above what instances take
BUILDER_MEM, BUILDER_VCPUS = 2048, 2
RESULT = "KAIM56_TOOLCHAIN_RESULT="
LOG_TAIL = 6000
ENV_FILE = "/etc/kaim56-toolchain.env"          # guest-init sources it for the agent processes
PROFILE_FILE = "/etc/profile.d/kaim56-toolchain.sh"   # login shells (webterm, ssh)
_lock = threading.Lock()        # the state file
_active, _active_lock = set(), threading.Lock()   # squads with a build thread in this process
_gate = threading.Lock()        # one builder VM at a time (host RAM; one tap index)


# ---- state -------------------------------------------------------------------
def _load():
    try:
        with open(STATE_FILE) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d):
    tmp = STATE_FILE + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(d, fh, indent=1)
    os.replace(tmp, STATE_FILE)


def _update(sid, fn):
    with _lock:
        d = _load()
        rec = d.setdefault(sid, {})
        rec.setdefault("layers", {})
        rec.setdefault("builds", {})
        fn(rec)
        _save(d)


def _set_build(sid, tpl, **kw):
    _update(sid, lambda r: r["builds"].setdefault(tpl, {}).update(kw))


# ---- what a squad has --------------------------------------------------------
def base_of(inst):
    """'claude' / 'openrouter' for an instance on a built-in base image, else ''."""
    return next((k for k, v in BASES.items() if inst.get("rootfs") == v), "")


def base_image_id(tpl):
    """Cheap and stable: the base image's mtime + size — what vm.image_state
    already treats as "rebuilt". '' when the image is missing."""
    try:
        st = os.stat(os.path.join(_paths.BASE, BASES[tpl]))
    except (OSError, KeyError):
        return ""
    return f"{int(st.st_mtime)}-{st.st_size}"


def layer_path(sid, tpl, version):
    if not SID_RE.match(str(sid)) or tpl not in BASES:
        raise ValueError("bad squad id or base")
    return os.path.join(LAYER_DIR, f"{sid}-{tpl}-v{int(version)}.ext4")


def spec_of(proj):
    s = _squads.settings(proj)
    return s.get("toolchain") or {}, int(s.get("toolchain_version") or 0)


def empty(spec):
    return not ((spec or {}).get("apt") or str((spec or {}).get("script") or "").strip() or (spec or {}).get("env"))


def templates(proj):
    """The base images the squad's members boot (the code indexer is no worker)."""
    names = set(proj.get("members") or {})
    return sorted({base_of(i) for i in _instances.load_instances()
                   if i["name"] in names and i.get("template") != "indexer"} - {""})


def layer_for(inst):
    """Host path of the ready toolchain layer for this instance, or None: a
    member of exactly one squad-enabled project with a spec, a layer for its
    base image built on the CURRENT base image, and the file present."""
    tpl = base_of(inst)
    if not tpl:
        return None
    proj, _why = _squads.resolve(inst["name"])
    if not proj:
        return None
    spec, _v = spec_of(proj)
    sid = _squads.squad_id(proj)
    if empty(spec) or not SID_RE.match(sid or ""):
        return None
    lay = ((_load().get(sid) or {}).get("layers") or {}).get(tpl)
    if not lay or not lay.get("ok") or lay.get("base_image_id") != base_image_id(tpl):
        return None
    p = layer_path(sid, tpl, lay.get("version") or 0)
    return p if os.path.exists(p) else None


def status(proj):
    """The UI's toolchain row: {version, spec, empty, templates: [{template, state, …}]}.
    state: ready | stale | building | queued | failed | none."""
    spec, ver = spec_of(proj)
    sid = _squads.squad_id(proj)
    rec = (_load().get(sid) or {}) if sid else {}
    now, out = int(time.time()), []
    for t in sorted(set(templates(proj)) | set(rec.get("layers") or {})):
        lay, b = (rec.get("layers") or {}).get(t) or {}, (rec.get("builds") or {}).get(t) or {}
        row = {"template": t, "layer_version": lay.get("version"), "size": lay.get("size", 0), "built": lay.get("built", 0)}
        if b.get("status") in ("building", "queued"):
            row.update(state=b["status"], elapsed=max(0, now - int(b.get("started") or b.get("queued") or now)))
        elif lay and lay.get("base_image_id") != base_image_id(t):
            row["state"] = "stale"
        elif b.get("status") == "failed" and int(b.get("version") or 0) >= int(lay.get("version") or 0):
            row.update(state="failed", error=b.get("error", ""), ready=bool(lay))
        elif lay:
            row["state"] = "ready"
        else:
            row["state"] = "none"
        out.append(row)
    return {"version": ver, "spec": spec, "empty": empty(spec), "squad_id": sid, "templates": out}


def logs(proj):
    """The last build's log tail per base image (a running build: its live console)."""
    sid = _squads.squad_id(proj)
    rec = (_load().get(sid) or {}) if SID_RE.match(sid or "") else {}
    out = {}
    for t, b in (rec.get("builds") or {}).items():
        if t not in BASES:
            continue
        txt = b.get("log_tail", "")
        if b.get("status") == "building":
            txt = _tail(os.path.join(_paths.RUN_DIR, f"tc-{sid}-{t}.log"))
        out[t] = {"status": b.get("status", ""), "version": b.get("version"), "error": b.get("error", ""), "log": txt}
    return out


# ---- the generated script ----------------------------------------------------
def _dq(v):
    """A validated env value inside double quotes: $NAME / ${NAME} expand
    (PATH additions), nothing else does — squads.toolchain_spec refused ` and $(."""
    return v.replace("\\", "\\\\").replace('"', '\\"')


def env_text(env):
    return "".join(f'export {k}="{_dq(v)}"\n' for k, v in sorted((env or {}).items()))


def build_script(spec, version):
    """toolchain.sh for the config disk: apt, the squad's script, cleanup, env."""
    pk = " ".join(shlex.quote(p) for p in spec.get("apt") or [])
    lines = ["#!/bin/sh",
             f"# kAIm56 squad toolchain v{int(version)} - generated by the manager; root in a throwaway builder VM",
             "set -e", "export DEBIAN_FRONTEND=noninteractive", "cd /tmp", "apt-get update"]
    if pk:
        lines.append(f"apt-get install -y --no-install-recommends {pk}")
    if str(spec.get("script") or "").strip():
        lines.append("bash -e /config/toolchain-user.sh")
    lines += ["apt-get clean", "rm -rf /var/lib/apt/lists/*", "mkdir -p /etc/profile.d",
              f"cat > {ENV_FILE} <<'KAIM56_ENV'", env_text(spec.get("env")).rstrip("\n"), "KAIM56_ENV",
              f"cp {ENV_FILE} {PROFILE_FILE}", f"chmod 0644 {ENV_FILE} {PROFILE_FILE}",
              f"echo v{int(version)} > /.kaim56-toolchain-ok"]
    return "\n".join(x for x in lines if x != "") + "\n"


def builder_files(spec, version):
    """What the builder's config disk holds: TOOLCHAIN_BUILD=1, DNS, PATH and
    the two scripts — nothing from an instance config, the settings or the
    secret store."""
    return {"config.env": "".join(f"{k}={shlex.quote(v)}\n" for k, v in (
                ("TOOLCHAIN_BUILD", "1"), ("GUEST_DNS", _netfw.GUEST_DNS),
                ("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"))),
            "toolchain.sh": build_script(spec, version),
            "toolchain-user.sh": str(spec.get("script") or "") + "\n"}


def _config_disk(name, spec, version):
    d = os.path.join(_paths.RUN_DIR, f"{name}.cfgdir")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    for f, txt in builder_files(spec, version).items():
        with open(os.path.join(d, f), "w") as fh:
            fh.write(txt)
        os.chmod(os.path.join(d, f), 0o644)
    os.chmod(d, 0o755)
    img = os.path.join(_paths.RUN_DIR, f"{name}.config.ext4")
    ok = _vm.mkfs_image(img, 16, "fcconfig", srcdir=d)
    shutil.rmtree(d, ignore_errors=True)
    if not ok:
        raise RuntimeError("mkfs of the builder's config disk failed")
    return img


# ---- the builder VM ----------------------------------------------------------
_popen = subprocess.Popen
_sleep = time.sleep


def _spawn(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True, name="toolchain-build").start()


def _tail(path, n=LOG_TAIL):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - n))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def result(log):
    """The guest's verdict: the LAST result line ('ok', 'fail rc=N') or ''."""
    hits = [ln.split(RESULT, 1)[1].strip() for ln in log.splitlines() if RESULT in ln]
    return hits[-1] if hits else ""


def _host_guard(inst, on):
    """The builder must not reach the host at all (no manager API, no NFS):
    mangle PREROUTING sees the packet before every INPUT ACCEPT the taps get."""
    spec = ("-i", _instances.net_of(inst)["tap"], "-m", "addrtype", "--dst-type", "LOCAL", "-j", "DROP")
    while _util.sh("iptables", "-t", "mangle", "-C", "PREROUTING", *spec, check=False).returncode == 0:
        _util.sh("iptables", "-t", "mangle", "-D", "PREROUTING", *spec, check=False)
    if on:
        _util.sh("iptables", "-t", "mangle", "-I", "PREROUTING", "1", *spec, check=False)


def _free_index():
    used = {i.get("index") for i in _instances.load_instances()}
    return next((n for n in BUILDER_INDEXES if n not in used), None)


def _builder(sid, tpl, index):
    return {"name": f"tc-{sid}-{tpl}", "index": index, "rootfs": BASES[tpl], "internet": True, "config": {}}


def _stop(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(10)


def run_builder(sid, tpl, spec, version):
    """Boot the builder, wait for its verdict, tear it down -> (ok, log_tail, why).
    Its upper image stays for install() and is removed by the caller."""
    idx = _free_index()
    if idx is None:
        return False, "", "no free network slot for a builder"
    inst = _builder(sid, tpl, idx)
    name, run = inst["name"], _paths.RUN_DIR
    upper = os.path.join(run, f"{name}.upper.ext4")
    logp, sock, conf, pidf = (os.path.join(run, f"{name}.{x}") for x in ("log", "sock", "config.json", "pid"))
    if not _vm.mkfs_image(upper, UPPER_MB, "fcupper"):
        return False, "", "mkfs of the builder's upper failed"
    cfg = _config_disk(name, spec, version)
    n = _instances.net_of(inst)
    boot = (f"console=ttyS0 reboot=k panic=1 pci=off ip={n['guest']}::{n['host']}:{n['mask']}::eth0:off "
            f"init=/init fc_upper=/dev/vdc fc_build=1")
    with open(conf, "w") as fh:
        json.dump({"boot-source": {"kernel_image_path": _paths.KERNEL, "boot_args": boot},
                   "drives": [{"drive_id": "rootfs", "path_on_host": os.path.join(_paths.BASE, BASES[tpl]),
                               "is_root_device": True, "is_read_only": True},
                              {"drive_id": "config", "path_on_host": cfg, "is_root_device": False, "is_read_only": True},
                              {"drive_id": "upper", "path_on_host": upper, "is_root_device": False, "is_read_only": False}],
                   "network-interfaces": [{"iface_id": "eth0", "host_dev_name": n["tap"], "guest_mac": n["mac"]}],
                   "machine-config": {"vcpu_count": BUILDER_VCPUS, "mem_size_mib": BUILDER_MEM}}, fh)
    _set_build(sid, tpl, index=idx)
    proc, why = None, ""
    try:
        _netfw.ensure_net_base()
        _netfw.setup_tap(inst)
        _host_guard(inst, True)
        if os.path.exists(sock):
            os.remove(sock)
        with open(logp, "wb") as log:               # a fresh console log per build
            proc = _popen([_paths.BIN, "--api-sock", sock, "--config-file", conf],
                          stdout=log, stderr=log, start_new_session=True)
        with open(pidf, "w") as fh:
            fh.write(str(proc.pid))
        end, seen = time.time() + BUILD_TIMEOUT, 0
        while proc.poll() is None:
            if result(_tail(logp)):
                seen = seen or time.time()
                if time.time() - seen > GRACE:
                    break
            if time.time() > end:
                why = f"timeout: the builder ran longer than {BUILD_TIMEOUT // 60} min"
                break
            _sleep(5)
    finally:
        if proc is not None:
            _stop(proc)
        _host_guard(inst, False)
        _netfw.teardown_tap(inst)
        for p in (sock, conf, pidf, cfg):
            try:
                os.remove(p)
            except OSError:
                pass
    tail = _tail(logp)
    res = result(tail)
    if why:
        return False, tail, why
    if res != "ok":
        return False, tail, ("build failed: " + res) if res else "no result from the builder (it died early?)"
    return True, tail, ""


def install(sid, tpl, version, upper):
    """The builder's upper -> the layer file: sparse copy, e2fsck (replays the
    journal if the freeze left one, then must be clean), atomic rename.
    Host-side only cp and e2fsck touch the image — never a mount. -> size."""
    os.makedirs(LAYER_DIR, mode=0o700, exist_ok=True)
    dst = layer_path(sid, tpl, version)
    tmp = dst + ".new"
    try:
        _util.sh("cp", "--sparse=always", upper, tmp)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    e2 = shutil.which("e2fsck", path="/usr/sbin:/sbin:" + os.environ.get("PATH", "")) or "e2fsck"
    r = _util.sh(e2, "-fy", tmp, check=False)
    r2 = _util.sh(e2, "-fn", tmp, check=False) if r.returncode in (0, 1) else r
    if r2.returncode != 0:
        os.unlink(tmp)
        raise RuntimeError(f"e2fsck: exit {r2.returncode}: {(r2.stdout + r2.stderr).strip()[-300:]}")
    os.replace(tmp, dst)
    return os.stat(dst).st_blocks * 512


def _drop_old(sid, tpl, keep):
    pre = f"{sid}-{tpl}-v"
    for f in os.listdir(LAYER_DIR) if os.path.isdir(LAYER_DIR) else []:
        if f.startswith(pre) and f.endswith(".ext4") and f != os.path.basename(keep):
            try:
                os.remove(os.path.join(LAYER_DIR, f))    # a running member keeps its open inode
            except OSError:
                pass


def build_one(sid, project, tpl, spec, version):
    """One base image: builder, install, record -> (ok, why)."""
    cur, t0 = base_image_id(tpl), int(time.time())
    _set_build(sid, tpl, status="building", started=t0, version=version, base_image_id=cur, error="", log_tail="")
    upper = os.path.join(_paths.RUN_DIR, f"tc-{sid}-{tpl}.upper.ext4")
    size, tail = 0, ""
    try:
        ok, tail, why = run_builder(sid, tpl, spec, version)
        if ok:
            size = install(sid, tpl, version, upper)
    except Exception as e:
        ok, why = False, f"{type(e).__name__}: {e}"[:300]
    finally:
        try:
            os.remove(upper)
        except OSError:
            pass
    now = int(time.time())

    def rec(r):
        r["project"] = project
        r["builds"][tpl] = {"status": "ok" if ok else "failed", "version": version, "base_image_id": cur,
                            "started": t0, "finished": now, "error": "" if ok else why, "log_tail": tail[-LOG_TAIL:]}
        if ok:
            r["layers"][tpl] = {"version": version, "template": tpl, "base_image_id": cur, "built": now,
                                "size": size, "ok": True, "log_tail": tail[-2000:]}
    _update(sid, rec)
    if ok:
        _drop_old(sid, tpl, layer_path(sid, tpl, version))
    _util._wlog(f"toolchain {project}/{tpl} v{version}: " + ("ok, %.1f GB" % (size / 1e9) if ok else why))
    return ok, why


def _build_all(sid, project, tpls, spec, version):
    res = {}
    try:
        for t in tpls:
            with _gate:
                res[t] = build_one(sid, project, t, spec, version)
    finally:
        with _active_lock:
            _active.discard(sid)
    ok = [t for t, r in res.items() if r[0]]
    bad = {t: r[1] for t, r in res.items() if not r[0]}
    text = (f"Toolchain v{version}: " + (f"ready for {', '.join(ok)} — members pick it up after Restart members. " if ok else "")
            + "".join(f"{t} failed ({w[:200]}); the previous layer stays. " for t, w in bad.items())).strip()
    try:
        proj = _projects.get(project)
        if proj:
            _squads.post(proj, "manager", ["all"], text, kind="note")
        _notify.notify_add("toolchain", f"Squad {project}: toolchain v{version} " + ("failed" if bad else "ready"),
                           text, link="squads")
    except Exception as e:
        _util._wlog(f"toolchain note {project}: {e!r}")


def ensure(proj, force=False, who="manager"):
    """Start a build when one is due -> what happened, one line. Due: a base
    image the members use whose layer is not this spec version on the current
    base image — each (version, base image) tried once by itself; `force`
    (the operator's Rebuild) builds every base image again."""
    sid = _squads.squad_id(proj) if proj else ""
    if not SID_RE.match(sid or ""):
        return "no squad record yet — the layer is built once the squad is enabled"
    spec, ver = spec_of(proj)
    if empty(spec):
        return "no toolchain spec"
    tpls = templates(proj)
    if not tpls:
        return "no member on a claude/openrouter image yet — built once there is one"
    if sid in _active:
        return "a build is running already"
    st = _load().get(sid) or {}
    todo = []
    for t in tpls:
        lay, b, cur = (st.get("layers") or {}).get(t) or {}, (st.get("builds") or {}).get(t) or {}, base_image_id(t)
        if force or not ((lay.get("version") == ver and lay.get("base_image_id") == cur)
                         or (b.get("version") == ver and b.get("base_image_id") == cur)):
            todo.append(t)
    if not todo:
        return "up to date"
    with _active_lock:
        if sid in _active:
            return "a build is running already"
        _active.add(sid)
    now = int(time.time())
    for t in todo:
        _set_build(sid, t, status="queued", queued=now, started=0, version=ver, base_image_id=base_image_id(t), by=who)
    _update(sid, lambda r: r.update(project=proj["name"]))
    try:
        _spawn(_build_all, sid, proj["name"], todo, spec, ver)
    except Exception:
        with _active_lock:
            _active.discard(sid)
        raise
    return f"building toolchain v{ver} for {', '.join(todo)}"


def sweep():
    """Idle worker: a build left 'building' by a manager restart is failed (its
    builder killed, its tap removed); every enabled squad gets what is due
    (a new spec version, a stale base image — once per base image)."""
    for sid, rec in _load().items():
        for t, b in (rec.get("builds") or {}).items():
            if SID_RE.match(sid) and t in BASES and b.get("status") in ("building", "queued"):
                with _active_lock:              # ensure() marks a squad active before it writes "queued"
                    if sid not in _active:
                        _orphan(sid, t, b)
    out = {}
    for p in _projects.load():
        if _squads.settings(p).get("enabled") and not empty(spec_of(p)[0]):
            msg = ensure(p, who="sweep")
            if msg.startswith("building"):
                out[p["name"]] = msg
    return out


def _orphan(sid, tpl, b):
    pidf = os.path.join(_paths.RUN_DIR, f"tc-{sid}-{tpl}.pid")
    try:
        with open(pidf) as fh:
            os.kill(int(fh.read().strip()), 9)
    except (OSError, ValueError):
        pass
    if isinstance(b.get("index"), int) and b["index"] in BUILDER_INDEXES:
        inst = _builder(sid, tpl, b["index"])
        _host_guard(inst, False)
        _netfw.teardown_tap(inst)
    for f in (f"tc-{sid}-{tpl}.{x}" for x in ("pid", "sock", "config.json", "config.ext4", "upper.ext4")):
        try:
            os.remove(os.path.join(_paths.RUN_DIR, f))
        except OSError:
            pass
    _set_build(sid, tpl, status="failed", error="interrupted: the manager restarted during the build", finished=int(time.time()))


def purge(proj):
    """A deleted project's layers and state go with it."""
    sid = _squads.squad_id(proj) if proj else ""
    if not SID_RE.match(sid or ""):
        return
    for f in os.listdir(LAYER_DIR) if os.path.isdir(LAYER_DIR) else []:
        if f.startswith(sid + "-"):
            try:
                os.remove(os.path.join(LAYER_DIR, f))
            except OSError:
                pass
    with _lock:
        d = _load()
        if d.pop(sid, None) is not None:
            _save(d)
