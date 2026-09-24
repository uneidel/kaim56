#!/usr/bin/env python3
# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""The smallest agent that works with the kAIm56 manager. Stdlib only.

The contract with the manager (TRANSPORT=web), all on port 8080:

  POST /api/chat          {"message": str, "chat": id?, "image": base64?}
                          -> {"reply": str}          (one whole answer)
  POST /api/chat/stream   same body -> text/plain, the answer as it is
                          produced (the manager streams it to the web UI,
                          the app and the voice client). Optional: without
                          this route the manager falls back to /api/chat.

What the environment gives you (config.env, sourced by /init):
  FC_INSTANCE    this instance's name          MANAGER_URL   http://<gateway>:8700
  WORKDIR        the NFS workspace (read-write) TZ, GUEST_DNS
  KEY_PROXY=1    the manager injects the LLM key: POST
                 $MANAGER_URL/api/llm/openrouter/chat/completions with an
                 OpenAI-style body and NO Authorization header
  plus every key of the template's params (here: OPENROUTER_MODEL, GREETING)

This skeleton answers by echo, or — with OPENROUTER_MODEL set and the key
proxy on — with one model call through the manager. Replace answer() with
your own logic; keep the two routes.
"""
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("WEB_PORT", "8080"))
GREETING = os.environ.get("GREETING", "skeleton heard:")
MODEL = os.environ.get("OPENROUTER_MODEL", "").strip()
MANAGER = os.environ.get("MANAGER_URL", "").rstrip("/")
KEY_PROXY = os.environ.get("KEY_PROXY") == "1"
SYSTEM = ("You are a kAIm56 agent inside a microVM. Answer briefly; "
          f"your workspace is {os.environ.get('WORKDIR', '/home/agent/workspace')}.")

_history = [{"role": "system", "content": SYSTEM}]   # one conversation, in memory


def llm(messages):
    """One chat completion through the manager's key proxy."""
    req = urllib.request.Request(
        f"{MANAGER}/api/llm/openrouter/chat/completions",
        data=json.dumps({"model": MODEL, "messages": messages}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        out = json.loads(r.read().decode("utf-8", "replace"))
    return (out.get("choices") or [{}])[0].get("message", {}).get("content") or "(empty answer)"


def answer(message):
    """The agent. Echo, or one model turn when a model and the key proxy are set."""
    if message.strip() == "/reset":
        del _history[1:]
        return "🔄 Context reset."
    if not (MODEL and KEY_PROXY and MANAGER):
        return f"{GREETING} {message}"
    _history.append({"role": "user", "content": message})
    try:
        reply = llm(_history)
    except Exception as e:           # keep the turn: the error is the answer
        _history.pop()
        return f"⚠️ model call failed: {e!r}"
    _history.append({"role": "assistant", "content": reply})
    return reply


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        print("[web]", fmt % a, flush=True)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def _send(self, code, ctype, data):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, "application/json", b'{"ok": true}')
        self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):
        if self.path not in ("/api/chat", "/api/chat/stream"):
            return self._send(404, "text/plain; charset=utf-8", b"not found")
        msg = str(self._body().get("message", ""))
        reply = answer(msg)
        if self.path == "/api/chat":
            return self._send(200, "application/json",
                              json.dumps({"reply": reply}, ensure_ascii=False).encode())
        # stream: the manager reads text/plain until the connection closes.
        # A real agent writes tokens here as they arrive.
        self._send(200, "text/plain; charset=utf-8", reply.encode())


if __name__ == "__main__":
    print(f"[agent] skeleton on :{PORT} mode={'model ' + MODEL if MODEL and KEY_PROXY else 'echo'}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
