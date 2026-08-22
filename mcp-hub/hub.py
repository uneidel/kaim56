#!/usr/bin/env python3
"""MCP hub for kAIm56: keeps the MCP server processes on the host, not in the VMs.

Previously each microVM ran its own mcp-remote/mcp-portainer — along with the
tokens that had to go into the guest for it, and LAN access for every VM. Now
the processes run here, ONCE per (instance, server), and the guests only speak
JSON-RPC through the manager. The guest sees neither token nor LAN.

Like the voice service: the container binds 0.0.0.0, the restriction sits on
the host side of the port mapping (-p 127.0.0.1:8771:8771). So the hub is only
reachable by the manager; there is no separate permission check here, the
authorization (which guest may use which server) is done by the manager.

  POST /rpc    {"key","argv","env","payload"} -> JSON-RPC reply from the server
  POST /kill   {"key"} or {"prefix"}          -> terminate processes
  GET  /health                                -> {"procs": [...]}

The hub remembers the initialize message per key: if a server process dies, it
is restarted on the next call and the initialization is replayed — the client
in the VM notices nothing.
"""
import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8771"))
HOST = os.environ.get("HOST", "0.0.0.0")
RPC_TIMEOUT = int(os.environ.get("RPC_TIMEOUT", "90"))
MAX_BODY = 4 * 1024 * 1024

_procs = {}          # key -> {"p": Popen, "lock": Lock, "init": [payloads]}
_procs_lock = threading.Lock()


def _spawn(key, argv, env):
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, bufsize=1,
                         env={**os.environ, **(env or {})})
    print(f"[hub] {key}: gestartet ({argv[0]}, pid {p.pid})", flush=True)
    return p


def _entry(key, argv, env):
    with _procs_lock:
        e = _procs.get(key)
        if e is None:
            e = {"p": _spawn(key, argv, env), "lock": threading.Lock(), "init": []}
            _procs[key] = e
        elif e["p"].poll() is not None:
            # Process has died -> restart and replay the remembered
            # initialization, otherwise the server sits there "raw".
            e["p"] = _spawn(key, argv, env)
            for msg in e["init"]:
                _write(e["p"], msg)
                if msg.get("id") is not None:
                    _read_until(e["p"], msg["id"], 30)
        return e


def _write(p, obj):
    p.stdin.write(json.dumps(obj) + "\n")
    p.stdin.flush()


def _read_until(p, want_id, timeout):
    """Read lines until the reply with the wanted id arrives. The server's own
    requests/notifications are skipped — no one answers the server's callbacks
    (sampling) here, there is no human for that."""
    end = time.time() + timeout
    while time.time() < end:
        line = p.stdout.readline()
        if not line:
            raise RuntimeError("MCP-Prozess hat stdout geschlossen")
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == want_id and ("result" in msg or "error" in msg):
            return msg
    raise TimeoutError(f"no reply to id {want_id} within {timeout}s")


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            with _procs_lock:
                alive = [k for k, e in _procs.items() if e["p"].poll() is None]
            return self._json(200, {"ready": True, "procs": alive})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0) or 0)
        if ln > MAX_BODY:
            return self._json(413, {"error": "body too large"})
        try:
            b = json.loads(self.rfile.read(ln) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})

        if self.path == "/kill":
            pref = b.get("prefix")
            keys = [b["key"]] if b.get("key") else []
            with _procs_lock:
                if pref:
                    keys = [k for k in _procs if k.startswith(pref)]
                n = 0
                for k in keys:
                    e = _procs.pop(k, None)
                    if e:
                        try:
                            e["p"].terminate()
                        except Exception:
                            pass
                        n += 1
            return self._json(200, {"killed": n})

        if self.path == "/rpc":
            key, argv, payload = b.get("key"), b.get("argv"), b.get("payload")
            if not key or not argv or not isinstance(payload, dict):
                return self._json(400, {"error": "key/argv/payload missing"})
            try:
                e = _entry(key, argv, b.get("env"))
            except Exception as ex:
                return self._json(502, {"error": f"spawn failed: {ex!r}"})
            with e["lock"]:                      # one RPC per process at a time
                try:
                    # keep the initialize traffic for the restart case.
                    if payload.get("method") in ("initialize",
                                                 "notifications/initialized"):
                        e["init"].append(payload)
                    _write(e["p"], payload)
                    if payload.get("id") is None:        # Notification
                        return self._json(200, {})
                    return self._json(200, _read_until(e["p"], payload["id"],
                                                       RPC_TIMEOUT))
                except Exception as ex:
                    return self._json(502, {"error": repr(ex)[:300]})

        self._json(404, {"error": "not found"})


if __name__ == "__main__":
    print(f"[hub] hoert auf {HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
