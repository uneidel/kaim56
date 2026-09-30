# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Jev: the model router's classifier (OpenJev, an NLI cross-encoder).

One job: POST /classify {"premise": str, "hypotheses": [str]} ->
{"entail": [p, …], "ms": n} — the entailment probability of each hypothesis
given the premise. The manager's key proxy asks it once per routed turn and
maps the answer to a model with its policy (mgr/router.py); nothing here knows
about models, instances or keys.

Bound to loopback on the host (docker -p 127.0.0.1:…); the VMs never reach it.
The weights and OpenJev's code come from a PINNED revision of the Hugging Face
repo (trust_remote_code runs that file). One request at a time: the model is
not re-entrant and the CPU has no room for two.
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import torch
from huggingface_hub import snapshot_download

REPO = os.environ.get("JEV_REPO", "AlexWortega/openjev")
REVISION = os.environ.get("JEV_REVISION", "26de23c44b67586b4bea31c0ef2e016e3068ae66")
SUBFOLDER = os.environ.get("JEV_SUBFOLDER", "qwen3.5-2b-nli-v5")
PORT = int(os.environ.get("JEV_PORT", "8891"))
MAX_PREMISE = 4000            # characters; the premise is the last user turn (+ a little history)
MAX_HYPOTHESES = 16
MAX_HYPOTHESIS = 200
torch.set_num_threads(int(os.environ.get("JEV_THREADS", "6")))

_jev = None
_lock = threading.Lock()
_state = {"ready": False, "error": "", "loaded_s": None, "calls": 0}


def load():
    t0 = time.time()
    try:
        path = snapshot_download(REPO, revision=REVISION, allow_patterns=[f"{SUBFOLDER}/*", "modeling_openjev.py"])
        sys.path.insert(0, path)
        from modeling_openjev import OpenJevCrossEncoder
        global _jev
        _jev = OpenJevCrossEncoder(path, subfolder=SUBFOLDER)
        _jev.predict_hypotheses("warm up", ["This is a test.", "This is not a test."])
        _state.update(ready=True, loaded_s=round(time.time() - t0, 1))
        print(f"[jev] {SUBFOLDER}@{REVISION[:10]} ready in {_state['loaded_s']} s", flush=True)
    except Exception as e:                       # stays up and says why: /health
        _state["error"] = repr(e)[:400]
        print(f"[jev] load failed: {e!r}", flush=True)


def classify(premise, hypotheses):
    with _lock:
        t0 = time.monotonic()
        rows = _jev.predict_hypotheses(premise, hypotheses)
        _state["calls"] += 1
        return [round(float(r[1]), 4) for r in rows], int((time.monotonic() - t0) * 1000)   # [contr, ENTAIL, neutral]


def check(body):
    """(premise, hypotheses) or raises ValueError with the reason."""
    p, hs = body.get("premise"), body.get("hypotheses")
    if not isinstance(p, str) or not p.strip():
        raise ValueError("premise must be a non-empty string")
    if not isinstance(hs, list) or not hs or len(hs) > MAX_HYPOTHESES:
        raise ValueError(f"hypotheses must be a list of 1-{MAX_HYPOTHESES} strings")
    if not all(isinstance(h, str) and h.strip() and len(h) <= MAX_HYPOTHESIS for h in hs):
        raise ValueError(f"every hypothesis must be a string of 1-{MAX_HYPOTHESIS} characters")
    return p[-MAX_PREMISE:], hs


class H(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            return self._json({**_state, "model": SUBFOLDER, "revision": REVISION})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path.rstrip("/") != "/classify":
            return self._json({"error": "not found"}, 404)
        if not _state["ready"]:
            return self._json({"error": _state["error"] or "loading"}, 503)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 64 * 1024:
                return self._json({"error": "body too large"}, 413)
            premise, hyps = check(json.loads(self.rfile.read(n) or b"{}"))
        except (ValueError, json.JSONDecodeError) as e:
            return self._json({"error": str(e)}, 400)
        entail, ms = classify(premise, hyps)
        return self._json({"entail": entail, "ms": ms})

    def log_message(self, *a):                 # no request log: premises are user text
        pass


if __name__ == "__main__":
    threading.Thread(target=load, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
