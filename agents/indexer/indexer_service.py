#!/usr/bin/env python3
# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Code index service of the indexer microVM (repowise). Stdlib only.

Runs as uid 1000. The manager adds this instance to a project as a reader,
so the project's folder appears read-only at /project/<name>; the service
keeps its own clone of it under /var/lib/indexer/<name> (the persistent
write layer), so repowise's `.repowise/` never touches the source.

  GET  /health                       -> {"ok", "projects": {name: last run}, "busy", "mcp"}
  POST /reindex {"project", "ref"?}  -> {"ok", "commit"|"error", "seconds"}  (synchronous)
  POST /mcp     one JSON-RPC message -> its answer (202 for a notification)
  POST /dashboard {"project"}        -> {"ok", "project", "port"} | {"ok": false, "error"}
  POST /dashboard/stop               -> {"ok"}

/dashboard runs repowise's web UI for one indexed project at a time (another
project replaces it): `repowise serve --no-ui` on loopback plus the Next.js
UI baked into the image (/opt/repowise-web, built with basePath /indexer/ui)
on DASH_PORT. The manager reverse-proxies /indexer/ui/ to it, admin only.
It lives until the VM's idle stop.

/mcp is one MCP server for every project: tools/list is repowise's list with
a required `project` argument added, tools/call goes to that project's
`repowise mcp` process (stdio, started on first use, restarted after a
reindex). Which projects a caller may name is decided by the manager, the
only client that reaches this port.
"""
import copy
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("INDEXER_PORT", "7340"))
DATA = os.environ.get("INDEXER_DATA", "/var/lib/indexer")
SRC_ROOT = os.environ.get("PROJECT_ROOT", "/project")
INDEX_TIMEOUT = int(os.environ.get("INDEX_TIMEOUT", "3600"))
RPC_TIMEOUT = int(os.environ.get("RPC_TIMEOUT", "120"))
MAX_MCP = int(os.environ.get("MAX_MCP", "4"))          # live repowise mcp processes (~250 MB each)
MCP_TOOLS = "+get_dependency_path,+get_execution_flows"  # default set plus the two read-only graph tools
MAX_BODY = 1024 * 1024
DASH_PORT = int(os.environ.get("DASH_PORT", "7341"))           # the UI, for the manager
DASH_API_PORT = int(os.environ.get("DASH_API_PORT", "7342"))   # repowise's API, loopback only
DASH_WEB = os.environ.get("DASH_WEB", "/opt/repowise-web/packages/web")
DASH_WAIT = 60
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")    # = mgr/projects.py NAME_RE
REF_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/-]{0,199}$")
# Telemetry off (both switches), no editor files, no hints. No LLM key here:
# the init runs --no-prose, the update keeps that mode.
ENV = {**os.environ, "DO_NOT_TRACK": "1", "REPOWISE_TELEMETRY_DISABLED": "1",
       "REPOWISE_SKIP_EDITOR_SETUP": "1", "REPOWISE_NO_HINTS": "1", "GIT_TERMINAL_PROMPT": "0"}
STATE_FILE = os.path.join(DATA, "state.json")


def log(*a):
    print("[indexer]", *a, flush=True)


# ---- index ------------------------------------------------------------------
def index_steps(project, ref, cloned, indexed):
    """The commands of one (re)index, in order: clone once, move the clone to
    the source's `ref` (detached, clean except .repowise), then repowise init
    the first time and update after that."""
    src, wc = os.path.join(SRC_ROOT, project), os.path.join(DATA, project)
    steps = [] if cloned else [["git", "clone", "--quiet", "--no-checkout", src, wc]]
    steps += [["git", "-C", wc, "fetch", "--quiet", "--prune", "origin", ref],
              ["git", "-C", wc, "checkout", "--quiet", "--force", "--detach", "FETCH_HEAD"],
              ["git", "-C", wc, "clean", "-fdxq", "-e", ".repowise"]]
    if indexed:
        steps.append(["repowise", "update", "--no-agents", wc])
    else:
        steps.append(["repowise", "init", "--no-prose", "-y", "--no-editor-setup", "--no-hook",
                      "--no-agents", "--no-claude-md", wc])
    return steps


def _tail(s, n=300):
    s = (s or "").strip()
    return s[-n:]


def load_state():
    try:
        with open(STATE_FILE) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(d):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(d, fh, indent=1)
    os.replace(tmp, STATE_FILE)


_state_lock = threading.Lock()
_locks = {}                    # project -> Lock: one index run per project


def _lock_of(project):
    with _state_lock:
        return _locks.setdefault(project, threading.Lock())


def busy():
    with _state_lock:
        return sorted(p for p, lk in _locks.items() if lk.locked())


def reindex(project, ref="HEAD", run=subprocess.run, wait_src=60):
    """Bring the clone to `ref` and index it -> {"ok", "commit"|"error", "seconds"}."""
    if not NAME_RE.match(project or ""):
        return {"ok": False, "error": "bad project name"}
    if not REF_RE.match(ref or "") or ".." in ref:
        return {"ok": False, "error": "bad ref"}
    src, wc = os.path.join(SRC_ROOT, project), os.path.join(DATA, project)
    t0 = time.time()
    with _lock_of(project):
        # The host-folder reconciler mounts a fresh membership within ~5 s.
        deadline = time.time() + wait_src
        while not os.path.exists(os.path.join(src, ".git")) and time.time() < deadline:
            time.sleep(1)
        if not os.path.exists(os.path.join(src, ".git")):
            out = {"ok": False, "error": f"{src} is no git repository (not mounted, or a subfolder of one)"}
        else:
            cloned = os.path.isdir(os.path.join(wc, ".git"))
            if not cloned and os.path.exists(wc):
                shutil.rmtree(wc, ignore_errors=True)        # a half clone from an earlier failure
            indexed = os.path.exists(os.path.join(wc, ".repowise", "state.json"))
            out = {"ok": True}
            if DASH.current() == project:                    # its server would sync on its own mid-run
                with DASH.lock:
                    DASH.stop()
            for argv in index_steps(project, ref, cloned, indexed):
                r = run(argv, env=ENV, capture_output=True, text=True, timeout=INDEX_TIMEOUT)
                if r.returncode != 0:
                    out = {"ok": False, "error": f"{' '.join(argv[:3])}: exit {r.returncode}: "
                                                 + _tail(r.stderr or r.stdout)}
                    break
            if out["ok"]:
                r = run(["git", "-C", wc, "rev-parse", "HEAD"], env=ENV, capture_output=True, text=True, timeout=30)
                out["commit"] = (r.stdout or "").strip()[:40]
                MCP.drop(project)                            # the next call reads the fresh index
        out["seconds"] = round(time.time() - t0, 1)
        try:
            os.sync()                                        # the write layer is mounted without -o sync
        except OSError:
            pass
        with _state_lock:
            st = load_state()
            st[project] = {"ts": int(time.time()), "ref": ref, **out}
            _save_state(st)
    log(project, "reindex", "ok" if out["ok"] else "FAILED", out.get("commit") or out.get("error"), f"{out['seconds']}s")
    return out


# ---- MCP: one stdio repowise process per project --------------------------------
class Proc:
    """`repowise mcp <clone>` over stdio; a reader thread queues its lines."""

    def __init__(self, project):
        wc = os.path.join(DATA, project)
        self.p = subprocess.Popen(["repowise", "mcp", "--tools", MCP_TOOLS, wc], cwd=wc, env=ENV,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.q = queue.Queue()
        self.lock = threading.Lock()
        self.id = 0
        self.used = time.time()
        threading.Thread(target=self._read, daemon=True).start()
        self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                "clientInfo": {"name": "kaim56-indexer", "version": "1"}})
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _read(self):
        for line in self.p.stdout:
            self.q.put(line)
        self.q.put(None)

    def _write(self, obj):
        self.p.stdin.write(json.dumps(obj) + "\n")
        self.p.stdin.flush()

    def alive(self):
        return self.p.poll() is None

    def rpc(self, method, params, timeout=RPC_TIMEOUT):
        """-> the JSON-RPC message (result or error) answering this call."""
        with self.lock:
            self.used = time.time()
            self.id += 1
            want = self.id
            self._write({"jsonrpc": "2.0", "id": want, "method": method, "params": params})
            end = time.time() + timeout
            while True:
                left = end - time.time()
                if left <= 0:
                    raise TimeoutError(f"repowise: no answer to {method} within {timeout}s")
                try:
                    line = self.q.get(timeout=left)
                except queue.Empty:
                    continue
                if line is None:
                    raise RuntimeError("repowise mcp exited")
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("id") == want and ("result" in msg or "error" in msg):
                    return msg

    def kill(self):
        try:
            self.p.kill()
        except OSError:
            pass


class Pool:
    def __init__(self, factory=Proc, size=MAX_MCP):
        self.factory, self.size = factory, size
        self.procs = {}
        self.lock = threading.Lock()

    def get(self, project):
        with self.lock:
            pr = self.procs.get(project)
            if pr is not None and pr.alive():
                return pr
            while len(self.procs) >= self.size:              # the least recently used goes
                old = min(self.procs, key=lambda k: self.procs[k].used)
                self.procs.pop(old).kill()
            pr = self.procs[project] = self.factory(project)
            return pr

    def drop(self, project):
        with self.lock:
            pr = self.procs.pop(project, None)
        if pr is not None:
            pr.kill()

    def live(self):
        with self.lock:
            return sorted(k for k, p in self.procs.items() if p.alive())


MCP = Pool()


def indexed_projects():
    return sorted(p for p, r in load_state().items() if r.get("ok")
                  and os.path.exists(os.path.join(DATA, p, ".repowise", "state.json")))


PROJECT_ARG = {"type": "string",
               "description": "The kAIm56 project (repository) to query — one you are a member of."}


def with_project_arg(tools):
    """repowise's tools with a required `project` argument (its own `repo`,
    a workspace alias, means nothing here and goes)."""
    out = []
    for t in copy.deepcopy(tools or []):
        s = t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {"type": "object"}
        props = s.get("properties") if isinstance(s.get("properties"), dict) else {}
        props.pop("repo", None)
        s["properties"] = {"project": dict(PROJECT_ARG), **props}
        s["required"] = ["project"] + [r for r in s.get("required") or [] if r not in ("repo", "project")]
        t["inputSchema"] = s
        out.append(t)
    return out


_tools_cache = []


def list_tools():
    if not _tools_cache:
        for p in indexed_projects()[:1]:
            msg = MCP.get(p).rpc("tools/list", {})
            _tools_cache[:] = (msg.get("result") or {}).get("tools") or []
    return with_project_arg(_tools_cache)


def call_tool(project, name, args):
    if project not in indexed_projects():
        return None, f"project {project!r} is not indexed here (yet)"
    msg = MCP.get(project).rpc("tools/call", {"name": name, "arguments": args})
    if "error" in msg:
        return None, str((msg["error"] or {}).get("message") or msg["error"])[:500]
    return msg.get("result") or {}, ""


def handle_rpc(msg, tools=list_tools, call=call_tool):
    """One JSON-RPC message -> the answer, None for a notification."""
    if not isinstance(msg, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
    mid, method, params = msg.get("id"), msg.get("method", ""), msg.get("params") or {}
    if "id" not in msg:
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, text):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}
    try:
        if method == "initialize":
            return ok({"protocolVersion": params.get("protocolVersion") or "2024-11-05",
                       "capabilities": {"tools": {}},
                       "serverInfo": {"name": "kaim56-indexer", "version": "1"},
                       "instructions": "Codebase intelligence (repowise) for the kAIm56 projects: dependency graph, "
                                       "blast radius, change risk, dead code, health. Name the project in every call."})
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": tools()})
        if method == "tools/call":
            args = dict(params.get("arguments") or {})
            project = str(args.pop("project", "") or "")
            args.pop("repo", None)
            if not NAME_RE.match(project):
                return err(-32602, "argument `project` missing or invalid")
            result, why = call(project, str(params.get("name") or ""), args)
            return ok(result) if why == "" else err(-32000, why)
        return err(-32601, f"method {method!r} not supported")
    except Exception as e:                    # a dead/slow repowise process is an answer, not a crash
        return err(-32000, f"{type(e).__name__}: {e}"[:300])


# ---- dashboard: repowise's web UI for one project ---------------------------------
def dashboard_cmds(project):
    """(cwd, argv, env) of the API and of the UI for this project's clone.
    The API stays on loopback (no key needed, nothing else reaches it); the
    UI's middleware forwards /indexer/ui/api/* to it."""
    wc = os.path.join(DATA, project)
    # FORWARDED_ALLOW_IPS: uvicorn trusts X-Forwarded-For from loopback by default, and
    # the UI's proxy sets it to the outer address — repowise then sees a network
    # client and refuses keyless access (403 "reachable from the network").
    api = (wc, ["repowise", "serve", "--no-ui", "--host", "127.0.0.1", "--port", str(DASH_API_PORT)],
           {**ENV, "REPOWISE_EMBEDDER": "mock",                       # no embedder prompt, no key
            "FORWARDED_ALLOW_IPS": "192.0.2.1"})                      # trust no proxy header
    ui = (DASH_WEB, ["node", os.path.join(DASH_WEB, "server.js")],
          {**ENV, "NEXT_TELEMETRY_DISABLED": "1", "HOSTNAME": "0.0.0.0", "PORT": str(DASH_PORT),
           "REPOWISE_API_URL": f"http://127.0.0.1:{DASH_API_PORT}"})
    return [api, ui]


def _port_open(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


class Dashboard:
    def __init__(self, popen=subprocess.Popen, ready=_port_open, wait=DASH_WAIT):
        self.popen, self.ready, self.wait = popen, ready, wait
        self.project, self.procs = "", []
        self.lock = threading.Lock()

    def _alive(self):
        return bool(self.procs) and all(p.poll() is None for p in self.procs)

    def current(self):
        return self.project if self._alive() else ""

    def stop(self):
        for p in self.procs:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except OSError:
                    pass
        self.project, self.procs = "", []

    def start(self, project):
        if not NAME_RE.match(project or ""):
            return {"ok": False, "error": "bad project name"}
        if project not in indexed_projects():
            return {"ok": False, "error": f"project {project!r} is not indexed here (yet)"}
        with self.lock:
            if self.project == project and self._alive():
                return {"ok": True, "project": project, "port": DASH_PORT}
            self.stop()
            logf = open(os.path.join(DATA, "dashboard.log"), "w")
            for cwd, argv, env in dashboard_cmds(project):
                self.procs.append(self.popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                             stdout=logf, stderr=subprocess.STDOUT))
            logf.close()
            self.project = project
            end = time.time() + self.wait
            while not (self.ready(DASH_API_PORT) and self.ready(DASH_PORT)):
                if not self._alive() or time.time() >= end:
                    self.stop()
                    return {"ok": False, "error": "dashboard did not start (see dashboard.log in the indexer)"}
                time.sleep(0.5)
        log(project, "dashboard up on", DASH_PORT)
        return {"ok": True, "project": project, "port": DASH_PORT}


DASH = Dashboard()


# ---- HTTP -------------------------------------------------------------------
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, code, obj=None):
        body = b"" if obj is None else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/health":
            return self._json(200, {"ok": True, "projects": load_state(), "busy": busy(), "mcp": MCP.live(),
                                    "dashboard": DASH.current()})
        if self.path.split("?")[0] == "/mcp":
            return self._json(405, {"error": "no server-sent stream; POST JSON-RPC"})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            return self._json(413, {"error": "body too large"})
        try:
            b = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        path = self.path.split("?")[0]
        if path == "/reindex" and isinstance(b, dict):
            out = reindex(str(b.get("project") or ""), str(b.get("ref") or "HEAD"))
            return self._json(200 if "seconds" in out else 400, out)     # a failed run is a result, bad input is not
        if path == "/dashboard" and isinstance(b, dict):
            out = DASH.start(str(b.get("project") or ""))
            return self._json(200 if out["ok"] else 400, out)
        if path == "/dashboard/stop":
            with DASH.lock:
                DASH.stop()
            return self._json(200, {"ok": True})
        if path == "/mcp":
            out = handle_rpc(b)
            return self._json(202) if out is None else self._json(200, out)
        self._json(404, {"error": "not found"})


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    log(f"listening on :{PORT}, data {DATA}, projects {sorted(load_state())}")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
