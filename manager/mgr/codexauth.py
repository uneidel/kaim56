# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Codex subscription login (security boundary): the ChatGPT login of the codex template, shared by its VMs.

The operator logs in once (``codex login`` writes ~/.codex/auth.json, see
SRC). Codex refreshes its own tokens and OpenAI rotates the refresh token on
every refresh, so a VM's refreshed copy must come back here, or the next VM
boots with a dead one: guests GET the newest copy and POST theirs after a
turn; the newer ``last_refresh`` wins. The manager keeps its copy in FILE
(0600) and never refreshes itself.

Part of the mgr package: no import from manager.py.
"""
import json
import os
import tempfile
import threading

from mgr import paths as _paths

SRC = os.environ.get("CODEX_AUTH_SRC", os.path.join(_paths.HOME_DIR, ".codex", "auth.json"))
FILE = os.path.join(_paths.BASE, "codex_auth.json")
MAX_BYTES = 64 * 1024
_lock = threading.Lock()


def _read(p):
    try:
        with open(p) as fh:
            d = json.load(fh)
        return d if valid(d) else None
    except (OSError, ValueError):
        return None


def valid(d):
    """A ChatGPT login as codex writes it: tokens with an access and a refresh token."""
    t = d.get("tokens") if isinstance(d, dict) else None
    return isinstance(t, dict) and all(isinstance(t.get(k), str) and t.get(k) for k in ("access_token", "refresh_token"))


def _stamp(d):
    return str((d or {}).get("last_refresh") or "")


def current():
    """The newest login of the operator's file and the manager's copy, or None."""
    a, b = _read(FILE), _read(SRC)
    if a and b:
        return a if _stamp(a) >= _stamp(b) else b
    return a or b


def store(d):
    """Keep a VM's copy when it is newer -> (kept, why)."""
    if not valid(d):
        return False, "not a codex ChatGPT login (tokens.access_token/refresh_token)"
    if len(json.dumps(d)) > MAX_BYTES:
        return False, "too large"
    with _lock:
        cur = current()
        if cur and _stamp(d) <= _stamp(cur):
            return False, "not newer"
        fd, tmp = tempfile.mkstemp(prefix=".codex.", dir=os.path.dirname(FILE))
        with os.fdopen(fd, "w") as fh:
            json.dump(d, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, FILE)
    return True, ""


def state():
    """For the session panel: present or not (codex refreshes in the VM)."""
    d = current()
    if not d:
        return "missing (run codex login on the host, see the template)"
    return "ok · refreshed " + (_stamp(d)[:16].replace("T", " ") or "never")
