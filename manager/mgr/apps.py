# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Apps: browser front-ends next to the chat page, one folder each.

<APPS_DIR>/<name>/app.json  {"title", "description", "icon", "instance", "server"}
                            ("server": true = it keeps state on celld, /apps/<name>/_api/)
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
/apps/<name>/_api/... (GET and POST) is the app's own server side on celld
(Worker + Durable Object) — forwarded the same way, no local fallback.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import mimetypes
import os
import re
import stat
import time
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
                    "icon": str(m.get("icon") or "▦")[:4], "instance": str(m.get("instance") or "")[:40],
                    "server": bool(m.get("server"))})          # it has a server side on celld (/apps/<name>/_api/)
    return out


TRASH = ".trash"                                  # <APPS_DIR>/.trash/<name>-<stamp>: deleted apps, recoverable


def _app_dir(name):
    ad = apps_dir()
    if not ad or not _NAME.match(name or "") or not os.path.isdir(os.path.join(ad, name)):
        raise ValueError(f"unknown app {name!r}")
    return ad, os.path.join(ad, name)


def trash(name):
    """Move the app's folder into <APPS_DIR>/.trash/ (a dot folder: never an
    app) -> the path it went to. Nothing is deleted; it can be moved back."""
    ad, src = _app_dir(name)
    os.makedirs(os.path.join(ad, TRASH), exist_ok=True)
    dst = os.path.join(ad, TRASH, f"{name}-{time.strftime('%Y%m%d-%H%M%S')}")
    os.rename(src, dst)
    return dst


def rename_dir(old, new):
    """Rename the app's folder (= its name and URL /apps/<name>/)."""
    ad, src = _app_dir(old)
    if not _NAME.match(new or ""):
        raise ValueError("a name is a-z, 0-9, - and _, starting with a letter or digit, at most 41 characters")
    dst = os.path.join(ad, new)
    if os.path.exists(dst):
        raise ValueError(f"{new!r} exists already")
    os.rename(src, dst)


PUBLISHER = "publisher"     # app.json key the manager sets: the instance that published the app
# An agent's app never runs with the operator's session: its own opaque origin (no allow-same-origin)
SANDBOX_CSP = "sandbox allow-scripts allow-forms allow-popups allow-modals allow-downloads"
PUB_MAX_FILES = 300
PUB_MAX_BYTES = 40 * 1024 * 1024


def publisher(name):
    """The instance that published this app ('' = the operator's own)."""
    try:
        with open(os.path.join(apps_dir(), name, "app.json"), encoding="utf-8") as fh:
            m = json.load(fh)
    except (OSError, ValueError):
        return ""
    return str(m.get(PUBLISHER) or "") if isinstance(m, dict) else ""


def _collect(dfd, prefix, out, size):
    """Regular files under the open folder dfd -> out {rel: bytes}; no
    symlinks are followed anywhere (O_NOFOLLOW per step), dotfiles skipped."""
    for e in os.scandir(dfd):
        n = e.name
        if n.startswith(".") or not _SEG.match(n):
            continue
        if e.is_dir(follow_symlinks=False):
            sub = os.open(n, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dfd)
            try:
                size = _collect(sub, prefix + n + "/", out, size)
            finally:
                os.close(sub)
        elif e.is_file(follow_symlinks=False):
            fd = os.open(n, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
            with os.fdopen(fd, "rb") as fh:
                if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                    continue
                out[prefix + n] = fh.read(PUB_MAX_BYTES + 1 - size)
            size += len(out[prefix + n])
        if len(out) > PUB_MAX_FILES or size > PUB_MAX_BYTES:
            raise ValueError(f"too big: at most {PUB_MAX_FILES} files and {PUB_MAX_BYTES >> 20} MB")
    return size


def publish(name, root, rel, inst):
    """Copy the folder <root>/<rel> (an instance's workspace) to <APPS_DIR>/<name>/
    -> {name, files, bytes, replaced}. It needs index.html and app.json; app.json
    gets publisher=inst (such an app is served sandboxed, SANDBOX_CSP). An app of
    the operator or of another instance is never replaced; a replaced one of
    the same instance goes to .trash first."""
    ad = apps_dir()
    if not ad or not os.path.isdir(ad):
        raise ValueError("no apps folder on this manager")
    if not _NAME.match(name or ""):
        raise ValueError("a name is a-z, 0-9, - and _, starting with a letter or digit, at most 41 characters")
    dst = os.path.join(ad, name)
    replaced = os.path.lexists(dst)
    if replaced and publisher(name) != inst:
        raise ValueError(f"app {name!r} exists and was not published by {inst} — pick another name")
    segs = [s for s in (rel or "").strip("/").split("/") if s]
    if any(not _SEG.match(s) or s.startswith(".") for s in segs):
        raise ValueError("bad folder path")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for s in segs:
            nxt = os.open(s, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        files = {}
        size = _collect(fd, "", files, 0)
    except FileNotFoundError:
        raise ValueError(f"no folder {rel!r} in the workspace") from None
    except NotADirectoryError:
        raise ValueError(f"{rel!r} is not a folder (or is a symlink)") from None
    finally:
        os.close(fd)
    if "index.html" not in files or "app.json" not in files:
        raise ValueError("the folder needs index.html and app.json ({\"title\", \"description\", \"icon\"})")
    try:
        m = json.loads(files["app.json"])
    except ValueError as e:
        raise ValueError(f"app.json: {e}") from None
    if not isinstance(m, dict):
        raise ValueError("app.json must be a JSON object")
    m[PUBLISHER] = inst
    m.pop("server", None)                       # static only: a server side (celld/do/) is the operator's
    files["app.json"] = json.dumps(m, ensure_ascii=False, indent=2).encode()
    tmp = os.path.join(ad, f".pub-{name}-{time.strftime('%Y%m%d-%H%M%S')}")
    for r, data in files.items():
        p = os.path.join(tmp, *r.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)
    if replaced:
        trash(name)
    os.rename(tmp, dst)
    return {"name": name, "files": len(files), "bytes": size, "replaced": replaced}


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


def celld_status():
    """For the Settings tab's Apps panel: {configured, url, reachable, ms, error,
    apps: {name: summary}} — celld's /_status (each app's server-side summary)."""
    base = celld_url()
    out = {"configured": bool(base), "url": base, "reachable": False, "ms": None, "error": "", "apps": {}}
    if not base:
        return out
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(base + "/_status", timeout=3) as r:
            d = json.load(r)
        out.update(reachable=True, apps=d.get("apps") or {})
    except (OSError, ValueError) as e:
        out["error"] = repr(e)[:200]
    out["ms"] = int((time.monotonic() - t0) * 1000)
    return out


API_PREFIX = "_api"
API_MAX_BODY = 64 * 1024


def is_api(rel):
    return (rel or "").strip("/").split("/")[0] == API_PREFIX


def api_forward(method, name, rel, body=b""):
    """(status, content-type, bytes): an app's own server side on celld.
    Same path checks as the files; 503 when celld is not set up or down."""
    base = celld_url()
    segs, why = check_path(name, rel)
    if segs is None:
        return 404, "application/json", json.dumps({"error": why}).encode()
    if not base:
        return 503, "application/json", b'{"error":"this app needs its server side (CELLD_URL is not set)"}'
    url = f"{base}/apps/{name}/" + "/".join(urllib.request.quote(s) for s in segs)
    req = urllib.request.Request(url, data=body if method == "POST" else None, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=CELLD_TIMEOUT) as r:
            return r.status, r.headers.get("Content-Type", "application/json"), r.read(MAX_FILE)
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", "application/json"), e.read(MAX_FILE)
    except (OSError, ValueError) as e:
        return 503, "application/json", json.dumps({"error": f"the app's server side (celld) is not reachable: {e!r}"[:300]}).encode()


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
