# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""The "written by AI?" gauge of the chat page: the assets of xkqr.org/aicomment
(a small linear classifier over token features, runs entirely in the browser),
fetched from the author's site on first use and cached under run/ — served
same-origin because the upstream sends no CORS headers. NOT vendored into the
repo: the author publishes no license. The text never leaves the browser; the
manager only hands out the classifier.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import os
import re
import urllib.request

from mgr import paths as _paths

UPSTREAM = "https://xkqr.org/aicomment/"
ALLOWED = re.compile(r"^(classifier|preprocess|pychars|extract|model)\.(mjs|json)$|^langs/[a-z_]+\.mjs$")
CTYPES = {".mjs": "text/javascript; charset=utf-8", ".json": "application/json"}
MAX_BYTES = 4 * 1024 * 1024


def cache_dir():
    return os.path.join(_paths.RUN_DIR, "aicheck")


def fetch(name):
    req = urllib.request.Request(UPSTREAM + name, headers={"User-Agent": "kaim56-manager"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(MAX_BYTES + 1)


def asset(name):
    """(bytes, content-type) or (None, reason). Allowlisted names only, cached
    on disk after the first fetch."""
    name = (name or "").strip("/")
    if not ALLOWED.match(name):
        return None, "not an aicomment asset"
    p = os.path.join(cache_dir(), name)
    ct = CTYPES[os.path.splitext(name)[1]]
    if os.path.exists(p):
        with open(p, "rb") as fh:
            return fh.read(), ct
    try:
        data = fetch(name)
    except Exception as e:
        return None, f"upstream unavailable: {e!r}"[:200]
    if not data or len(data) > MAX_BYTES:
        return None, "upstream returned nothing usable"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, p)
    return data, ct
