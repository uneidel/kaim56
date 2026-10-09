# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Code index: one microVM (template ``indexer``, agents/indexer) runs repowise for the git projects — started on demand, stopped when idle.

- The indexer joins a project as a **reader** (ensure_member): it sees the
  folder read-only at /project/<name> and indexes its own clone of it.
- ``reindex(project)``: in a background thread start the VM when stopped,
  wait for /health, POST /reindex; the result is kept per project
  (``indexer.json``). Fired when a squad ticket reaches done and when a merge
  is approved (mgr/squadgate.py), or by the operator (Squad panel).
- MCP server ``repowise`` (catalog name): a member's calls go from /api/mcp
  straight to the indexer's /mcp (mgr/mcp.py SERVER_HOOKS) — not through the
  hub: the hub container cannot reach a guest, and this way the manager
  checks each tools/call's ``project`` against the caller's memberships.
  initialize/tools/list are answered here (tools cached), so a member
  booting does not boot the indexer; a call does, and is retried once.
- idle_sweep (task worker): stop the VM after IDLE_STOP s without a call.
- Dashboard (admin only): GET /indexer/open/<project> starts the indexer and
  repowise's web UI for that project (open_dashboard), then /indexer/ui/ is
  reverse-proxied to it (mgr/guestproxy.py dash_relay). The UI is built with
  that basePath (agents/indexer/Dockerfile.rootfs): paths pass unchanged.
  Every proxied request counts as use for the idle stop. Served only on its
  own origin settings.INDEX_HOST with its own "dash" session (mgr/httpd.py
  _auth_index, mgr/routes_auth.py); unset = no dashboard.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

from mgr import instances as _instances
from mgr import mcp as _mcp
from mgr import paths as _paths
from mgr import projects as _projects
from mgr import vm as _vm

TEMPLATE = "indexer"
SERVER = "repowise"                  # the MCP catalog entry members get (MCP_SERVERS=…,repowise)
PORT = 7340                          # indexer_service.py in the guest
DASH_PORT = 7341                     # its dashboard UI (repowise web, Next.js)
DASH_BASE = "/indexer/ui"            # the UI's basePath = the manager path it is served at
IDLE_STOP = int(os.environ.get("INDEXER_IDLE_STOP", "1800"))
HEALTH_WAIT = 180                    # boot + service start, bounded
INDEX_TIMEOUT = 3600
MCP_TIMEOUT = 150
STATE_FILE = os.path.join(_paths.BASE, "indexer.json")
_lock = threading.Lock()             # state file + the queue
_run_lock = threading.Lock()         # one index run at a time
_pending = set()
_last_use = [time.time()]            # a running indexer gets IDLE_STOP after a manager restart


def instance():
    """The platform's indexer instance (the first of template 'indexer') or None."""
    return next((i for i in _instances.load_instances() if i.get("template") == TEMPLATE), None)


def touch():
    _last_use[0] = time.time()


def _spawn(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True, name="indexer").start()


def _http(inst, path, body=None, timeout=10):
    url = f"http://{_instances.net_of(inst)['guest']}:{PORT}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def healthy(inst):
    try:
        return bool(_http(inst, "/health", timeout=3).get("ok"))
    except (OSError, ValueError):
        return False


def ensure_up(inst):
    """Start the VM when stopped, wait for /health (HEALTH_WAIT) -> '' or why not."""
    if not _instances.is_running(inst):
        try:
            _vm.start(inst)
        except Exception as e:
            return f"indexer start failed: {e!r}"[:300]
    end = time.time() + HEALTH_WAIT
    while not healthy(inst):
        if time.time() >= end:
            return f"indexer not healthy after {HEALTH_WAIT}s"
        time.sleep(2)
    return ""


# ---- state --------------------------------------------------------------------
def _load():
    try:
        with open(STATE_FILE) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _update(fn):
    with _lock:
        d = _load()
        fn(d)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(d, fh, indent=1)
        os.replace(tmp, STATE_FILE)


def project_status(project):
    """For the Squad panel: the indexer, running or not, this project's last run."""
    inst = instance()
    return {"instance": inst["name"] if inst else "", "running": bool(inst) and _instances.is_running(inst),
            "pending": project in _pending, "last": (_load().get("projects") or {}).get(project),
            "member": bool(inst) and inst["name"] in ((_projects.get(project) or {}).get("members") or {})}


# ---- membership, reindex ---------------------------------------------------------
def indexable(proj):
    """A host project whose folder is a git repository's root."""
    src = _projects.source_dir(proj or {})
    return bool(src) and os.path.exists(os.path.join(src, ".git"))


def ensure_member(project, inst=None):
    """Add the indexer to the project as a reader -> '' or why not."""
    inst = inst or instance()
    p = _projects.get(project)
    if inst is None or p is None:
        return "no indexer instance" if inst is None else "unknown project"
    if inst["name"] in (p.get("members") or {}):
        return ""
    members = {n: {"role": m.get("role")} for n, m in (p.get("members") or {}).items()}
    members[inst["name"]] = {"role": "reader"}
    r = _projects.upsert({**p, "members": members})
    return "" if r.get("ok") else str(r.get("error") or "project save failed")


def reindex(project, why=""):
    """Queue one index run of the project -> a short message. Never blocks."""
    if instance() is None:
        return "no indexer instance (create one from template 'indexer')"
    p = _projects.get(project)
    if p is None:
        return "unknown project"
    if not indexable(p):
        return "not a git repository on the host: nothing to index"
    with _lock:
        if project in _pending:
            return "reindex already queued"
        _pending.add(project)
    _spawn(_run, project, why)
    return "reindex queued"


def _run(project, why):
    t0, ok, detail = time.time(), False, ""
    try:
        with _run_lock:
            with _lock:
                _pending.discard(project)        # a hook during this run queues the next one
            touch()
            inst = instance()
            no = ensure_member(project, inst) if inst else "no indexer instance"
            no = no or ensure_up(inst)
            if no:
                raise RuntimeError(no)
            base = (_projects.get(project) or {}).get("base") or "HEAD"
            r = _http(inst, "/reindex", {"project": project, "ref": base}, timeout=INDEX_TIMEOUT)
            ok, detail = bool(r.get("ok")), str(r.get("error") or r.get("commit") or "")
    except Exception as e:
        detail = str(e) if isinstance(e, RuntimeError) else repr(e)
    finally:
        touch()
    rec = {"ts": int(time.time()), "ok": ok, "detail": detail[:300], "why": str(why)[:120],
           "seconds": round(time.time() - t0)}

    def put(d):
        d.setdefault("projects", {})[project] = rec
        if ok:
            d.pop("tools", None)                 # re-read from the (maybe rebuilt) indexer at the next tools/list
    _update(put)
    print(f"[indexer] {project} ({why}): {'ok' if ok else 'FAILED'} {detail[:200]} in {rec['seconds']}s", flush=True)
    return rec


def idle_sweep(now=None):
    """Task worker, every idle cycle: stop the indexer after IDLE_STOP s unused."""
    inst = instance()
    if inst is None or _pending or _run_lock.locked() or not _instances.is_running(inst):
        return ""
    if (now or time.time()) - _last_use[0] < IDLE_STOP:
        return ""
    _vm.stop(inst)
    print(f"[indexer] {inst['name']} idle {IDLE_STOP}s -> stopped", flush=True)
    return "stopped"


# ---- dashboard: repowise's web UI, proxied at DASH_BASE ------------------------------
def open_dashboard(project):
    """Start the indexer when stopped and its dashboard for this project -> '' or why not."""
    inst = instance()
    if inst is None:
        return "no indexer instance (create one from template 'indexer')"
    if _projects.get(project) is None:
        return "unknown project"
    touch()
    try:
        no = ensure_up(inst)
        if no:
            return no
        r = _http(inst, "/dashboard", {"project": project}, timeout=90)
    except urllib.error.HTTPError as e:
        try:
            r = json.loads(e.read() or b"{}")
        except ValueError:
            r = {}
        return str(r.get("error") or f"indexer HTTP {e.code}")[:300]
    except (OSError, ValueError) as e:
        return f"indexer unreachable: {e!r}"[:300]
    finally:
        touch()
    return "" if r.get("ok") else str(r.get("error") or "dashboard did not start")[:300]


def dashboard_target():
    """(guest ip, port) of the dashboard while the indexer runs, else None. A use."""
    inst = instance()
    if inst is None or not _instances.is_running(inst):
        return None
    touch()
    return _instances.net_of(inst)["guest"], DASH_PORT


# ---- MCP: the members' 'repowise' server -------------------------------------------
def _mine(caller):
    """The projects the caller may query: its memberships the indexer reads too."""
    inst = instance()
    idx = inst["name"] if inst else None
    return [p["name"] for p, _m in _projects.memberships(caller["name"]) if idx in (p.get("members") or {})]


def _scoped(tools, mine):
    """The cached tool list with `project` limited to the caller's projects."""
    out = []
    for t in tools:
        t = json.loads(json.dumps(t))
        prop = ((t.get("inputSchema") or {}).get("properties") or {}).get("project")
        if isinstance(prop, dict) and mine:          # an empty enum is an invalid schema for some providers
            prop["enum"] = list(mine)
        out.append(t)
    return out


def _forward(payload):
    """POST one JSON-RPC message to the indexer's /mcp; start it when stopped,
    and once more when the call fails to connect. -> (status, body)."""
    inst = instance()
    if inst is None:
        return 503, {"error": "no indexer instance"}
    touch()
    err = None
    for attempt in (0, 1):
        if attempt or not _instances.is_running(inst):
            no = ensure_up(inst)
            if no:
                return 503, {"error": no}
        try:
            out = _http(inst, "/mcp", payload, timeout=MCP_TIMEOUT)
            touch()
            return 200, out
        except urllib.error.HTTPError as e:
            return e.code, {"error": f"indexer HTTP {e.code}"}
        except (OSError, ValueError) as e:
            err = e
    return 502, {"error": f"indexer unreachable: {err!r}"[:300]}


def mcp_call(caller, payload):
    """A member's JSON-RPC message for SERVER -> (status, body)."""
    mid, method = payload.get("id"), payload.get("method", "")
    mine = _mine(caller)
    if "id" not in payload:
        return 200, {}                                   # notifications: nothing to forward
    if method == "initialize":
        return 200, {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": (payload.get("params") or {}).get("protocolVersion") or "2024-11-05",
            "capabilities": {"tools": {}}, "serverInfo": {"name": "kaim56-indexer", "version": "1"}}}
    if method == "tools/list":
        tools = _load().get("tools")
        if not tools:
            st, out = _forward(payload)
            tools = ((out.get("result") or {}).get("tools") if st == 200 else None) or []
            if not tools:
                return st, out
            _update(lambda d: d.__setitem__("tools", tools))
        return 200, {"jsonrpc": "2.0", "id": mid, "result": {"tools": _scoped(tools, mine)}}
    if method == "tools/call":
        params = payload.get("params") or {}
        args = dict(params.get("arguments") or {})
        proj = str(args.get("project") or (mine[0] if len(mine) == 1 else ""))
        if proj not in mine:
            return 200, {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message":
                         f"project {proj!r}: name one of your indexed projects ({', '.join(mine) or 'none'})"}}
        payload = {**payload, "params": {**params, "arguments": {**args, "project": proj}}}
    return _forward(payload)


_mcp.SERVER_HOOKS[SERVER] = mcp_call
