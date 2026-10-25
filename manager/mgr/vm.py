# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Firecracker VM lifecycle: config disk, overlay rootfs and harness drive, the per-instance write layer, start and stop, stale-image detection.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import hashlib
import os
import shutil
import tempfile
import threading
import json
import shlex
import signal
import socket
import subprocess
import time

from mgr import instances as _instances
from mgr import notify as _notify
from mgr import paths as _paths
from mgr import settings as _settings
from mgr import util as _util
from mgr import guests as _guests
from mgr import host as _host
from mgr import mcp as _mcp
from mgr import mounts as _mounts
from mgr import netfw as _netfw
from mgr import plugins as _plugins
from mgr import toolchain as _toolchain


def mkfs_image(path, size_mb, label=None, srcdir=None):
    """Build an ext4 image atomically: a sparse file of size_mb, mkfs
    (populated from srcdir when given), renamed into place so a running VM
    keeps its old inode. False when mkfs fails — the old image, if any, stays."""
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".new",
                               dir=os.path.dirname(path) or ".")
    with os.fdopen(fd, "wb") as fh:
        fh.truncate(size_mb * 1024 * 1024)
    mkfs = shutil.which("mkfs.ext4", path="/usr/sbin:/sbin:" + os.environ.get("PATH", "")) or "mkfs.ext4"
    args = ["-F", "-q"] + (["-L", label] if label else []) + (["-d", srcdir] if srcdir else [])
    r = _util.sh(mkfs, *args, tmp, check=False)
    if r.returncode != 0:
        print(f"[mkfs] {os.path.basename(path)}: {r.stderr.strip()[:200]}", flush=True)
        os.unlink(tmp)
        return False
    os.replace(tmp, path)
    return True


# ---- Overlay rootfs ---------------------------------------------------------
# For images in OVERLAY_ROOTFS the VM boots with the SHARED base read-only
# (Firecracker blocks writes at the host level -> no journal conflict) plus a
# small rw upper image per instance; the guest init assembles the root from
# them via overlayfs+pivot_root. Advantage: no 2-GB copy per start, and with
# inst["persist_disk"]=true the write layer (installations!) survives a
# stop/start. Other images run unchanged via private_rootfs().
OVERLAY_ROOTFS = {"instances/openrouter-rootfs.ext4", "instances/claude-rootfs.ext4"}


def is_overlay(inst):
    """Shared read-only base + per-instance upper: the built-in images, and
    any instance whose template declared "overlay": true (agents/<x>/template.json —
    its guest-init must assemble the overlay from fc_upper, see agents/skeleton)."""
    return inst.get("rootfs") in OVERLAY_ROOTFS or bool(inst.get("overlay"))

# ---- Harness disk: the agent code as a read-only drive, not baked in ---------
# Pattern from Claude Code's sandbox (harness and skills are read-only shared
# layers next to the rootfs): the openrouter agent (agent.py, run_agent.py,
# webterm.py) lives on a small ext4 image the manager rebuilds from AGENT_SRC
# whenever the sources' CONTENT changes (a digest next to the image; mtimes
# lie after rsync, checkouts and clock skew), attached read-only to every VM
# on a rootfs that carries this agent. An agent change is then one instance
# restart — no docker build, no 2 GB image. The guest mounts it at /harness
# (boot arg fc_harness=/dev/vdX) and prefers it over /app; without the drive
# it boots from the rootfs as before.
AGENT_SRC = os.environ.get("AGENT_SRC") or _settings.SITE.get("AGENT_SRC") or ""
HARNESS_FILES = ("agent", "run_agent.py", "webterm.py")   # "agent" is the package directory
HARNESS_IMG = os.path.join(_paths.RUN_DIR, "harness.ext4")
HARNESS_ROOTFS = {"instances/openrouter-rootfs.ext4"}     # images built from AGENT_SRC
_harness_lock = threading.Lock()


def harness_sources():
    """All HARNESS_FILES under AGENT_SRC (a directory entry means every .py
    file below it) — or nothing: a half-present set (a partial rsync, a
    package mid-rename) must not become the drive a VM boots from;
    run_agent.py imports agent with no fallback."""
    if not AGENT_SRC:
        return []
    out = []
    for f in HARNESS_FILES:
        p = os.path.join(AGENT_SRC, f)
        if os.path.isdir(p):
            files = sorted(os.path.join(r, x) for r, _, fs in os.walk(p) for x in fs
                           if x.endswith(".py") and "__pycache__" not in r)
            if not files:
                return []
            out += files
        elif os.path.isfile(p):
            out.append(p)
        else:
            return []
    return out


HARNESS_FORMAT = "2"        # bumped when the staging changes; part of the digest, so the image is rebuilt


def _harness_stage(srcs, d):
    """Copy the sources into the staging dir with modes the guest can read.
    The manager runs with umask 077: a directory it creates is 0700 root, and
    mke2fs -d carries that into the image — the agent package was then a
    namespace package without files for uid 1000 (2026-09-17)."""
    for p in srcs:
        dst = os.path.join(d, os.path.relpath(p, AGENT_SRC))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(p, dst)
        os.chmod(dst, 0o644)
    for root, _dirs, _files in os.walk(d):
        os.chmod(root, 0o755)


def _harness_digest(srcs):
    h = hashlib.sha256()
    h.update(HARNESS_FORMAT.encode() + b"\0")
    for p in srcs:
        h.update(os.path.relpath(p, AGENT_SRC).encode() + b"\0")
        with open(p, "rb") as fh:
            h.update(fh.read())
        h.update(b"\0")
    return h.hexdigest()


def harness_image():
    """Path of the harness drive, (re)built when the sources' digest differs
    from the one recorded at the last build; None when AGENT_SRC is not
    configured or incomplete. Serialized: two starts (or the sweep and a
    start) must not build into the same file."""
    srcs = harness_sources()
    if not srcs:
        return None
    with _harness_lock:
        stamp = HARNESS_IMG + ".src"
        try:
            want = _harness_digest(srcs)
            with open(stamp) as fh:
                have = fh.read().strip()
        except OSError:
            have = ""
        if have == want and os.path.exists(HARNESS_IMG):
            return HARNESS_IMG
        d = tempfile.mkdtemp(prefix="harness-", dir=_paths.RUN_DIR)
        try:
            _harness_stage(srcs, d)
            if not mkfs_image(HARNESS_IMG, 8, "kaim56-harness", srcdir=d):
                return HARNESS_IMG if os.path.exists(HARNESS_IMG) else None
            with open(stamp, "w") as fh:
                fh.write(want)
            print(f"[harness] rebuilt from {AGENT_SRC} ({len(srcs)} files)", flush=True)
            return HARNESS_IMG
        finally:
            shutil.rmtree(d, ignore_errors=True)


def uses_harness(inst):
    """By image, not template name: llama/orcarouter share the openrouter
    rootfs and its agent, so they take (and go stale with) the same drive."""
    return inst.get("rootfs") in HARNESS_ROOTFS


def image_state(inst):
    """(stale, built, started): stale when a RUNNING VM on a shared base image
    was started before that image was last rebuilt — it still runs the old
    agent and will until stop/start. spawn_subagent was dead for three weeks
    and a tool fix missed the voice instance this way; nobody could see it."""
    if not is_overlay(inst) or not _instances.is_running(inst):
        return False, 0, 0
    try:
        built = os.path.getmtime(os.path.join(_paths.BASE, inst["rootfs"]))
        if uses_harness(inst) and os.path.exists(HARNESS_IMG):
            built = max(built, os.path.getmtime(HARNESS_IMG))   # agent code counts too
        started = os.path.getmtime(_instances.pidfile(inst))
    except OSError:
        return False, 0, 0
    return started < built, built, started


def stale_instances():
    return [i["name"] for i in _instances.load_instances() if image_state(i)[0]]


_img_seen = {}      # rootfs path -> mtime last seen (filled at startup: no push for old news)


def image_sweep():
    """Idle worker: when a base image was rebuilt, push ONCE which running
    instances still sit on the old one. Stays quiet if nobody is affected."""
    hit = []
    try:
        harness_image()        # an edited agent.py shows up here, not at the next start
    except Exception as e:
        _util._wlog(f"image-sweep harness: {e!r}")
    for rel in sorted(OVERLAY_ROOTFS) + [HARNESS_IMG]:
        try:
            mt = os.path.getmtime(rel if os.path.isabs(rel) else os.path.join(_paths.BASE, rel))
        except OSError:
            continue
        if rel in _img_seen and mt > _img_seen[rel]:
            hit.append(rel)
        _img_seen[rel] = mt
    if not hit:
        return []
    old = stale_instances()
    if old:
        try:
            _notify.notify_add("rebuild", f"Rootfs rebuilt: {len(old)} instance(s) on the old image",
                       ", ".join(old) + " — restart them to pick up the new agent.",
                       link="instances")
        except Exception as e:
            _util._wlog(f"image-sweep notify: {e!r}")
    return old


# ---- firecracker lifecycle -------------------------------------------------
def guest_env(inst):
    """What the guest reads from config.env: the instance's config plus what
    the manager adds for this boot (paths, zone, DNS, exports)."""
    cfg = dict(inst.get("config", {}))
    # Second guard: older instance JSONs may still contain a key/MCP_CONFIG;
    # they still must not reach the disk.
    for k in _settings.NEVER_PERSIST:
        cfg.pop(k, None)
    # A minimal VM init does not have /usr/local/bin in PATH -> inject it so
    # claude/fabric are found (guest-init sources the config disk).
    cfg.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    cfg["FC_INSTANCE"] = inst["name"]   # for the host-folder reconciler in the guest
    cfg.setdefault("TZ", _host.HOST_TZ)        # the agent's clock: [Now] line per turn
    cfg["GUEST_DNS"] = _netfw.GUEST_DNS         # guest-init writes resolv.conf from it (site.json, not the image)
    # The Claude bridge's turn limit (its default 600 s) = the task worker's patience
    # (tasks.TASK_TIMEOUT): a long squad turn was cut off at 10 min. An instance may set its own.
    cfg.setdefault("WEB_TIMEOUT", os.environ.get("TASK_TIMEOUT", "1800"))
    cfg["AGENT_EXPORT"] = _mounts.workspace_dir(inst)   # its own workspace export, by absolute path
    if uses_harness(inst):
        cfg["MEMORY_DIR"] = "/memory"    # Markdown memory folder (mgr/memfs.py)
    if inst["name"] == _guests.ORCH_INSTANCE:   # only the orchestrator may manage tasks
        cfg["TASK_ADMIN"] = "1"
    # Key injection proxy active? Then the agent sends chat requests to the
    # manager instead of directly to the router — so the VM never sees an LLM key
    # (not even via the secret broker). The switch lives in the shared settings
    # so that ALL instances are switched over consistently.
    st = _settings.load_settings()
    if st.get("LLM_KEY_PROXY") == "1":
        cfg["KEY_PROXY"] = "1"
    # The max_tokens cap: the agent sends it (the only cap on a direct llama
    # path); the key proxy enforces it anyway.
    if not cfg.get("LLM_MAX_TOKENS") and str(st.get("LLM_MAX_TOKENS") or "").strip():
        cfg["LLM_MAX_TOKENS"] = str(st["LLM_MAX_TOKENS"]).strip()
    return cfg


def make_config_disk(inst):
    """Create a small ext4 drive with the instance config (key=value) -> vdb."""
    cfg = guest_env(inst)
    d = os.path.join(_paths.RUN_DIR, f"{inst['name']}.cfgdir")
    os.makedirs(d, exist_ok=True)
    # Put tool plugins (firecracker/plugins/*.py) on the disk too — the agent
    # loads them at start from /config/plugins. New plugin = file + Approve in
    # the Plugins tab + stop/start: only approved, unmodified plugins go on the
    # disk (checked on the copy, see plugins.approved).
    pdst = os.path.join(d, "plugins")
    shutil.rmtree(pdst, ignore_errors=True)
    psrc = os.path.join(_paths.BASE, "plugins")
    if os.path.isdir(psrc):
        os.makedirs(pdst, exist_ok=True)
        for f0 in sorted(os.listdir(psrc)):
            if f0.startswith((".", "__")):        # __pycache__, .pins.json: not tools
                continue
            sp = os.path.join(psrc, f0)
            if os.path.isdir(sp):                 # multi-file tool: whole folder
                shutil.copytree(sp, os.path.join(pdst, f0), dirs_exist_ok=True)
                name = f0
            elif f0.endswith(".py"):              # single .py (backwards compatible)
                shutil.copy2(sp, os.path.join(pdst, f0))
                name = f0[:-3]
            else:
                continue
            if not _plugins.approved(name, pdst):
                dp = os.path.join(pdst, f0)
                shutil.rmtree(dp, ignore_errors=True) if os.path.isdir(dp) else os.remove(dp)
                print(f"[plugins] {inst['name']}: '{name}' not delivered — not approved or modified "
                      f"since (Plugins tab → Approve)", flush=True)
    with open(os.path.join(d, "config.env"), "w") as f:
        for k, v in cfg.items():
            # quote values (EXTRA_MOUNTS and others contain shell metacharacters like | and ;)
            f.write(f"{k}={shlex.quote(str(v))}\n")
    # The agent reads this disk as uid 1000: world-readable, whatever the
    # manager's umask (nothing on it is secret — NEVER_PERSIST above).
    for root, dirs, files in os.walk(d):
        os.chmod(root, 0o755)
        for f0 in files:
            os.chmod(os.path.join(root, f0), 0o644)
    img = os.path.join(_paths.RUN_DIR, f"{inst['name']}.config.ext4")
    mkfs_image(img, 16, "fcconfig", srcdir=d)
    return img



UPPER_SIZE_MB = 8192          # throwaway layer per start; sparse (toolchains, build caches)
UPPER_PERSIST_SIZE_MB = 16384  # persistent layer (apt/pip need room); sparse


def upper_path(inst):
    if inst.get("persist_disk"):
        return os.path.join(_paths.INST_DIR, f"{inst['name']}-upper.ext4")
    return os.path.join(_paths.RUN_DIR, f"{inst['name']}.upper.ext4")


def make_upper(inst):
    """Provide an empty (or, with persist, existing) upper image."""
    p = upper_path(inst)
    if inst.get("persist_disk") and os.path.exists(p):
        return p
    size = UPPER_PERSIST_SIZE_MB if inst.get("persist_disk") else UPPER_SIZE_MB
    if not mkfs_image(p, size, "fcupper"):
        raise RuntimeError(f"mkfs of the upper layer for {inst['name']} failed")
    return p


def reset_upper(name):
    """Delete the persistent write layer (factory reset). Only while stopped."""
    inst = next((i for i in _instances.load_instances() if i["name"] == name), None)
    if not inst:
        return "unknown"
    if _instances.is_running(inst):
        return "error: instance is running — stop it first"
    n = 0
    for p in (os.path.join(_paths.INST_DIR, f"{name}-upper.ext4"),
              os.path.join(_paths.RUN_DIR, f"{name}.upper.ext4")):
        try:
            os.remove(p); n += 1
        except OSError:
            pass
    return f"disk reset ({n} layer(s) removed)" if n else "nothing to reset"


def set_persist_disk(name, on):
    inst = next((i for i in _instances.load_instances() if i["name"] == name), None)
    if not inst:
        return "unknown"
    if not is_overlay(inst):
        return "error: this template's rootfs has no overlay support (yet)"
    inst["persist_disk"] = bool(on)
    _instances.save_instance(inst)
    running = " (applies after stop/start)" if _instances.is_running(inst) else ""
    return f"persistent disk for '{name}' {'ON' if on else 'off'}{running}"


def private_rootfs(inst):
    """Create a fresh rootfs copy for exactly this VM and return its path.

    All instances of a template pointed at the SAME ext4 image, writable. Two
    VMs running simultaneously then share one journal — that worked as long as
    barely anything was written, and ended on Aug 15 with 'error loading
    journal' at boot. Hence: a separate copy per start (sparse, ~seconds fast).
    Side effect, and a deliberate one: a restart always boots the current
    template image, rootfs updates take effect as before with stop/start. State
    that should persist doesn't live here anyway, but centrally (memory.json,
    chats.json, katfs)."""
    src = os.path.join(_paths.BASE, inst["rootfs"])
    dst = os.path.join(_paths.RUN_DIR, f"{inst['name']}.rootfs.ext4")
    tmp = dst + ".new"
    # --sparse=always: the 2-GB image carries ~550 MB; the copy should occupy
    # just as little. First .new, then rename — a half copy must never start as
    # a rootfs.
    _util.sh("cp", "--sparse=always", src, tmp)
    os.replace(tmp, dst)
    return dst


def _vdev(drives):
    """Guest device of the LAST drive in the list (virtio-blk: vda, vdb, …)."""
    return f"/dev/vd{chr(ord('a') + len(drives) - 1)}"


def gen_config(inst):
    n = _instances.net_of(inst)
    boot = (f"console=ttyS0 reboot=k panic=1 pci=off "
            f"ip={n['guest']}::{n['host']}:{n['mask']}::eth0:off init=/init")
    overlay = is_overlay(inst)
    if overlay:
        drives = [{"drive_id": "rootfs", "path_on_host": os.path.join(_paths.BASE, inst["rootfs"]),
                   "is_root_device": True, "is_read_only": True}]
    else:
        drives = [{"drive_id": "rootfs", "path_on_host": private_rootfs(inst),
                   "is_root_device": True, "is_read_only": False}]
    cfg_disk = os.path.join(_paths.RUN_DIR, f"{inst['name']}.config.ext4")
    if os.path.exists(cfg_disk):
        drives.append({"drive_id": "config", "path_on_host": cfg_disk,
                       "is_root_device": False, "is_read_only": True})
    for j, d in enumerate(inst.get("extra_drives", [])):
        drives.append({"drive_id": f"data{j}", "path_on_host": d["path"],
                       "is_root_device": False, "is_read_only": d.get("readonly", False)})
    if uses_harness(inst):
        himg = harness_image()
        if himg:
            drives.append({"drive_id": "harness", "path_on_host": himg,
                           "is_root_device": False, "is_read_only": True})
            boot += f" fc_harness={_vdev(drives)}"
    if overlay:
        # The squad's shared toolchain (mgr/toolchain.py): read-only, stacked
        # by guest-init between the base and the upper. Never costs a start.
        try:
            layer = _toolchain.layer_for(inst)
        except Exception as e:
            print(f"[toolchain] {inst['name']}: layer lookup failed: {e!r}", flush=True)
            layer = None
        if layer:
            drives.append({"drive_id": "toolchain", "path_on_host": layer,
                           "is_root_device": False, "is_read_only": True})
            boot += f" fc_layer={_vdev(drives)}"
    if overlay:
        # Last drive = upper; the device name follows from the position
        # (virtio-blk: vda, vdb, ...). The guest reads it from /proc/cmdline.
        drives.append({"drive_id": "upper", "path_on_host": make_upper(inst),
                       "is_root_device": False, "is_read_only": False})
        boot += f" fc_upper={_vdev(drives)}"
    return {
        "boot-source": {"kernel_image_path": _paths.KERNEL, "boot_args": boot},
        "drives": drives,
        "network-interfaces": [{"iface_id": "eth0", "host_dev_name": n["tap"],
                                "guest_mac": n["mac"]}],
        "machine-config": {"vcpu_count": inst.get("vcpus", 2),
                           "mem_size_mib": inst.get("mem_mib", 1024)},
    }


# ---- jail, limits, graceful stop ---------------------------------------------
# Firecracker ran as root: a VM escape was root on the host. Now (VM_JAIL, on
# by default when the manager is root) every VM starts through Firecracker's
# jailer: its own uid (JAIL_UID + index; one shared group), a chroot that holds
# only its kernel and drives (hard links: same filesystem), a cgroup with its
# CPU and memory cap. The tap belongs to that uid. Writable drives go to the
# uid (0600), shared read-only ones to the group (0640).
JAILER = os.path.join(_paths.BASE, "bin", "jailer")
JAIL_BASE = os.path.join(_paths.RUN_DIR, "jail")          # same filesystem as instances/ and run/
JAIL_UID = 900000                                         # + index; JAIL_UID itself is the shared group
CGROUP_ROOT = "/sys/fs/cgroup/firecracker"                # the jailer's default parent cgroup (v2)
MEM_SLACK_MIB = 512                                       # the VMM itself on top of the guest's memory
# Per drive and per direction of the NIC: one VM must not take the host's disk or line.
DISK_MBPS = int(os.environ.get("VM_DISK_MBPS") or _settings.SITE.get("VM_DISK_MBPS") or 200)
NET_MBITS = int(os.environ.get("VM_NET_MBITS") or _settings.SITE.get("VM_NET_MBITS") or 500)
STOP_GRACE = 10                                           # s for the guest to sync and reboot after Ctrl+Alt+Del
_locks, _locks_lock = {}, threading.Lock()


def jailed():
    """VM_JAIL=0 (site.json or env) is the way back to plain root Firecracker."""
    on = str(os.environ.get("VM_JAIL") or _settings.SITE.get("VM_JAIL", "1")) != "0"
    return on and os.geteuid() == 0 and os.path.exists(JAILER)


def jail_ids(index):
    return JAIL_UID + int(index), JAIL_UID


def jail_dir(name):
    return os.path.join(JAIL_BASE, os.path.basename(_paths.BIN), name)


def api_sock(name):
    """The VM's API socket: in its chroot when jailed, else next to the pidfile."""
    j = os.path.join(jail_dir(name), "root", "api.sock")
    return j if os.path.exists(j) else os.path.join(_paths.RUN_DIR, f"{name}.sock")


def _limit(mb_per_s):
    size = int(mb_per_s * 1024 * 1024)
    return {"bandwidth": {"size": size, "one_time_burst": size * 4, "refill_time": 1000}}


def add_limits(cfg):
    """Rate limiters on every drive and both directions of the NIC (in place)."""
    for d in cfg["drives"]:
        d["rate_limiter"] = _limit(DISK_MBPS)
    for n in cfg["network-interfaces"]:
        n["rx_rate_limiter"] = n["tx_rate_limiter"] = _limit(NET_MBITS / 8)
    return cfg


def _owned(path, uid, gid, writable):
    """The VM's uid reads (and, when writable, writes) the file — nobody else
    but root; a shared read-only image is readable by the jail group."""
    if writable:
        os.chown(path, uid, gid); os.chmod(path, 0o600)
    else:
        st = os.stat(path)
        if not st.st_mode & 0o004:                       # world-readable images stay as they are
            os.chown(path, st.st_uid, gid); os.chmod(path, (st.st_mode & 0o7777) | 0o040)


def jail_clean(name):
    """Remove a VM's chroot and cgroup (after it exited; also stale ones)."""
    d = jail_dir(name)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    try:
        os.rmdir(os.path.join(CGROUP_ROOT, name))
    except OSError:
        pass


def popen_vm(name, index, cfg, log):
    """Start Firecracker on `cfg` (host paths) -> Popen. Jailed: kernel and
    drives hard-linked into the chroot, the config rewritten to those paths."""
    if not jailed():
        sock = os.path.join(_paths.RUN_DIR, f"{name}.sock")
        conf = os.path.join(_paths.RUN_DIR, f"{name}.config.json")
        if os.path.exists(sock):
            os.remove(sock)
        with open(conf, "w") as fh:
            json.dump(cfg, fh)
        return subprocess.Popen([_paths.BIN, "--api-sock", sock, "--config-file", conf],
                                stdout=log, stderr=log, start_new_session=True)
    uid, gid = jail_ids(index)
    jail_clean(name)
    root = os.path.join(jail_dir(name), "root")
    os.makedirs(root, mode=0o755)
    cfg = json.loads(json.dumps(cfg))
    files = [("vmlinux", cfg["boot-source"], "kernel_image_path", False)]
    files += [(d["drive_id"] + ".ext4", d, "path_on_host", not d["is_read_only"]) for d in cfg["drives"]]
    for fname, obj, key, writable in files:
        src = obj[key]
        _owned(src, uid, gid, writable)
        os.link(src, os.path.join(root, fname))          # EXDEV: the image is not on the jail's filesystem
        obj[key] = "/" + fname
    with open(os.path.join(root, "config.json"), "w") as fh:
        json.dump(cfg, fh)
    if os.geteuid() == 0:                                # it reads its config and creates its socket there
        os.chown(root, uid, gid); os.chown(os.path.join(root, "config.json"), uid, gid)
    mem, cpus = cfg["machine-config"]["mem_size_mib"], cfg["machine-config"]["vcpu_count"]
    return subprocess.Popen([JAILER, "--id", name, "--exec-file", _paths.BIN, "--uid", str(uid), "--gid", str(gid),
                             "--chroot-base-dir", JAIL_BASE, "--cgroup-version", "2",
                             "--cgroup", f"memory.max={(mem + MEM_SLACK_MIB) * 1024 * 1024}",
                             "--cgroup", f"cpu.max={cpus * 100000} 100000",
                             "--", "--config-file", "/config.json", "--api-sock", "/api.sock"],
                            stdout=log, stderr=log, start_new_session=True)


def _api(sock, method, path, body):
    """One request to Firecracker's API socket -> HTTP status (0: no answer)."""
    data = json.dumps(body).encode()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
            c.settimeout(3)
            c.connect(sock)
            c.sendall(f"{method} {path} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
                      f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
            return int(c.recv(256).split(b" ", 2)[1])
    except (OSError, ValueError, IndexError):
        return 0


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:                                                   # a zombie child of ours is gone too
        return os.waitpid(pid, os.WNOHANG)[0] == 0
    except ChildProcessError:
        return True


def halt(name, pid, grace=None):
    """Ctrl+Alt+Del (the guest's init syncs its disks and reboots, which ends
    Firecracker), then SIGKILL after `grace` s -> how it ended."""
    how = "killed"
    if _api(api_sock(name), "PUT", "/actions", {"action_type": "SendCtrlAltDel"}) == 204:
        end = time.time() + (STOP_GRACE if grace is None else grace)
        while time.time() < end and _alive(pid):
            time.sleep(0.2)
        how = "shut down" if not _alive(pid) else how
    if _alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        end = time.time() + 5
        while time.time() < end and _alive(pid):
            time.sleep(0.1)
    return how


def _lock(name):
    """One start/stop of an instance at a time (a squad wake and a click in the UI)."""
    with _locks_lock:
        return _locks.setdefault(name, threading.Lock())


def start(inst):
    with _lock(inst["name"]):
        if _instances.is_running(inst):
            return "already running"
        _netfw.ensure_net_base()
        _netfw.setup_tap(inst, *(jail_ids(inst["index"]) if jailed() else ()))
        _mounts.setup_mounts(inst)
        make_config_disk(inst)
        log = open(os.path.join(_paths.RUN_DIR, f"{inst['name']}.log"), "ab")
        try:                                  # the operator may tail the console (root:operator, 0640)
            os.chmod(log.name, 0o640); os.chown(log.name, 0, _host.ADMIN_GID)
        except OSError:
            pass
        with log:
            p = popen_vm(inst["name"], inst["index"], add_limits(gen_config(inst)), log)
        open(_instances.pidfile(inst), "w").write(str(p.pid))
        return f"started (pid {p.pid}{', jailed' if jailed() else ''})"


def stop(inst):
    with _lock(inst["name"]):
        pf = _instances.pidfile(inst)
        how = ""
        try:
            pid = int(open(pf).read().strip())
        except (OSError, ValueError):
            pid = 0
        if pid and _instances.is_running(inst):
            how = halt(inst["name"], pid)
        try:
            os.remove(pf)
        except OSError:
            pass
        _netfw.teardown_tap(inst)
        _mounts.teardown_mounts(inst)
        _mcp.mcp_hub_kill(inst["name"])
        jail_clean(inst["name"])
        # The private rootfs copy is worthless after stopping (the next start pulls
        # a fresh one) — just disk space, so remove it.
        for f in (f"{inst['name']}.rootfs.ext4", f"{inst['name']}.upper.ext4", f"{inst['name']}.sock"):
            try:
                os.remove(os.path.join(_paths.RUN_DIR, f))
            except OSError:
                pass
        return f"stopped ({how})" if how else "stopped"
