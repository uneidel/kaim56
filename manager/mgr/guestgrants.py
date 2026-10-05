# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Guest grants (security boundary): the few admin routes an instance may use
because the operator granted it — today the apps (celld) and their Cloudflare
side, for the agent tools `apps` and `cloudflare`.

Per instance, config key MANAGE_APPS (empty = nothing, the default):
  read   list apps and Cloudflare workers, read an app's server-side state,
         download a cfdo worker's Durable Objects
  write  + upload to / restore from Cloudflare, rename, write app state
  full   + delete an app or a Cloudflare worker

The one question asked from mgr/httpd.py — for admin-only routes and for the
guest POST allowlist — is allowed(instance, method, path). Everything else
stays as it was: a guest without the key gets 403 like before.

Part of the mgr package: no import from manager.py.
"""
import re

LEVELS = ("read", "write", "full")
_NAME = r"[a-z0-9][a-z0-9_-]{0,62}"
# (minimum level, method, path regex) — the path without its query string
_GRANTS = [
    ("read", "GET", r"/api/apps"),
    ("read", "GET", r"/api/apps/cloudflare"),
    ("read", "GET", rf"/apps/{_NAME}/_api(/.*)?"),
    ("read", "POST", rf"/api/cfworkers/{_NAME}/export"),
    ("write", "POST", rf"/api/apps/{_NAME}/(upload|restore|rename)"),
    ("write", "POST", rf"/apps/{_NAME}/_api(/.*)?"),
    ("full", "POST", rf"/api/apps/{_NAME}/delete"),
    ("full", "POST", rf"/api/cfworkers/{_NAME}/delete"),
]
_COMPILED = [(LEVELS.index(lv), m, re.compile(p + r"\Z")) for lv, m, p in _GRANTS]


def level(inst):
    """The instance's MANAGE_APPS level as an index into LEVELS, -1 = none."""
    v = str(((inst or {}).get("config") or {}).get("MANAGE_APPS", "")).strip().lower()
    return LEVELS.index(v) if v in LEVELS else -1


def allowed(inst, method, path):
    """May this guest instance call this admin route?"""
    lv = level(inst)
    if lv < 0:
        return False
    p = path.split("?", 1)[0]
    return any(lv >= need and method == m and rx.match(p) for need, m, rx in _COMPILED)
