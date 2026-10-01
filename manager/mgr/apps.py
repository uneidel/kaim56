# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Apps: browser front-ends next to the chat page, one folder each.

<APPS_DIR>/<name>/app.json  {"title", "description", "icon", "instance"}
<APPS_DIR>/<name>/index.html + whatever it needs (js, css, images)

The manager lists them (/api/apps, the chat sidebar) and serves the files
under /apps/<name>/ behind the admin login — the operator's own files, so
NOT the sandbox the guest proxy puts on VM pages. Logic lives in the agents
and the API; an app is only UI. APPS_DIR: site.json, else apps/ next to
manager/ (the repo layout), else BASE/apps.

Hosting: with CELLD_URL in Settings the files come from celld (the repo's
celld/ project, self-hosted Workers / Durable Objects, loopback only) — the
same path checks first, the same admin login, the same origin for the apps'
/api calls. celld down or slow: the manager serves the file itself.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request

from mgr import paths as _paths
from mgr import settings as _settings

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
_SEG = re.compile(r"^[A-Za-z0-9._-]+$")
MAX_FILE = 8 * 1024 * 1024


def apps_dir():
    d = _settings.SITE.get("APPS_DIR") or ""
    if d:
        return d
    for cand in (os.path.join(os.path.dirname(_paths.BASE), "apps"), os.path.join(_paths.BASE, "apps")):
        if os.path.isdir(cand):
            return cand
    return ""


def load_apps():
    """[{name, title, description, icon, instance}] — folders with a valid app.json."""
    out = []
    ad = apps_dir()
    for f in sorted(os.listdir(ad)) if ad and os.path.isdir(ad) else []:
        mf = os.path.join(ad, f, "app.json")
        if not _NAME.match(f) or not os.path.isfile(mf) or not os.path.isfile(os.path.join(ad, f, "index.html")):
            continue
        try:
            with open(mf, encoding="utf-8") as fh:
                m = json.load(fh)
        except ValueError as e:
            print(f"[apps] {mf}: {e}", flush=True)
            continue
        out.append({"name": f, "title": str(m.get("title") or f)[:60], "description": str(m.get("description") or "")[:200],
                    "icon": str(m.get("icon") or "▦")[:4], "instance": str(m.get("instance") or "")[:40]})
    return out


CELLD_TIMEOUT = 5


def celld_url():
    return (_settings.load_settings().get("CELLD_URL") or "").strip().rstrip("/")


def check_path(name, rel):
    """(segments, '') when /apps/<name>/<rel> may be served, else (None, why).
    Every path segment is checked — no traversal, no dotfiles; an empty path
    is index.html."""
    ad = apps_dir()
    if not ad or not _NAME.match(name or "") or not os.path.isfile(os.path.join(ad, name, "app.json")):
        return None, "unknown app"          # a folder without a manifest is not an app, whatever it holds
    segs = ((rel or "").strip("/") or "index.html").split("/")
    if any(not _SEG.match(s) or s.startswith(".") for s in segs):
        return None, "bad path"
    return segs, ""


def hosted(name, rel):
    """(bytes, content-type) from celld, (None, "not found") on its 404, or
    None when celld is not configured or does not answer (-> serve locally)."""
    base = celld_url()
    segs, why = check_path(name, rel)
    if not base or segs is None:
        return None
    url = f"{base}/apps/{name}/" + "/".join(urllib.request.quote(s) for s in segs)
    try:
        with urllib.request.urlopen(url, timeout=CELLD_TIMEOUT) as r:
            data = r.read(MAX_FILE + 1)
            if len(data) > MAX_FILE:
                return None, "not found"
            return data, r.headers.get("Content-Type", "application/octet-stream")
    except urllib.error.HTTPError as e:
        return (None, "not found") if e.code == 404 else None
    except (OSError, ValueError):
        return None


def file_of(name, rel):
    """(bytes, content-type) of a file inside the app's folder, or (None, why).
    check_path() first, and no symlink escape."""
    segs, why = check_path(name, rel)
    if segs is None:
        return None, why
    root = os.path.realpath(os.path.join(apps_dir(), name))
    p = os.path.realpath(os.path.join(root, *segs))
    if not p.startswith(root + os.sep) or not os.path.isfile(p) or os.path.getsize(p) > MAX_FILE:
        return None, "not found"
    ct = mimetypes.guess_type(p)[0] or "application/octet-stream"
    if ct.startswith("text/") or ct in ("application/javascript", "application/json"):
        ct += "; charset=utf-8"
    with open(p, "rb") as fh:
        return fh.read(), ct
