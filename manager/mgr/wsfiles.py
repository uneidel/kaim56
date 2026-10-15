# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Read-only access to an instance's workspace for the operator's apps:
GET /api/workspace/<instance>/<path> lists a folder or returns a file.
The workspace is the NFS export the agent writes into (mounts.workspace_dir);
apps read what the agent produced (a jobs list, a report) without a route
per file type. Path checked segment by segment, no dotfiles, no symlink
escape, size cap, admin only.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import mimetypes
import os
import re
import urllib.parse

from mgr import instances as _instances
from mgr import mounts as _mounts

# Any segment a repo path may hold ([id].tsx, @types, a,b) — but no control
# characters or separators; dot-leading segments ('..', dotfiles) are refused below.
_SEG = re.compile(r"^[^\x00-\x1f\x7f/\\]+$")
MAX_FILE = 16 * 1024 * 1024
LIST_MAX = 500
# a file name in an agent's answer: runs/2026-10-15.json, /home/node/workspace/jobs.xlsx
_MENTION = re.compile(r"(?<![\w./-])((?:/[\w.-]+)*/workspace/)?([\w-][\w.-]*(?:/[\w-][\w.-]*)*\.[A-Za-z0-9]{1,8})(?![\w/-])")
MENTION_MAX = 10


def _root(inst_name):
    inst = next((i for i in _instances.load_instances() if i["name"] == inst_name), None)
    return os.path.realpath(_mounts.workspace_dir(inst)) if inst else ""


def read(inst_name, rel):
    """(kind, payload, content-type): kind 'dir' -> payload is a list of
    {name, size, mtime, dir}; kind 'file' -> payload is bytes; kind None ->
    payload is the reason."""
    root = _root(inst_name)
    if not root or not os.path.isdir(root):
        return None, "unknown instance or no workspace", ""
    rel = (rel or "").strip("/")
    segs = [s for s in rel.split("/") if s] if rel else []
    if any(not _SEG.match(s) or s.startswith(".") for s in segs):
        return None, "bad path", ""
    p = os.path.realpath(os.path.join(root, *segs)) if segs else root
    if p != root and not p.startswith(root + os.sep):
        return None, "bad path", ""
    if os.path.isdir(p):
        out = []
        for f in sorted(os.listdir(p))[:LIST_MAX]:
            if f.startswith("."):
                continue
            fp = os.path.join(p, f)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            out.append({"name": f, "size": st.st_size, "mtime": int(st.st_mtime), "dir": os.path.isdir(fp)})
        return "dir", out, "application/json"
    if not os.path.isfile(p) or os.path.islink(os.path.join(root, *segs)) if segs else False:
        return None, "not found", ""
    if os.path.getsize(p) > MAX_FILE:
        return None, "too large", ""
    ct = mimetypes.guess_type(p)[0] or "application/octet-stream"
    if ct.startswith("text/") or ct in ("application/json", "application/javascript"):
        ct += "; charset=utf-8"
    with open(p, "rb") as fh:
        return "file", fh.read(), ct


def mentioned(inst_name, text):
    """The workspace files an answer names (relative, or under .../workspace/)
    that exist as regular files read() would serve -> [rel], at most MENTION_MAX."""
    root = _root(inst_name)
    if not root or not os.path.isdir(root):
        return []
    out = []
    for m in _MENTION.finditer(str(text or "")):
        rel = m.group(2)
        segs = rel.split("/")
        if rel in out or any(s.startswith(".") for s in segs):
            continue
        p = os.path.join(root, *segs)
        if os.path.islink(p) or not os.path.isfile(p) or not os.path.realpath(p).startswith(root + os.sep):
            continue
        if os.path.getsize(p) <= MAX_FILE:
            out.append(rel)
            if len(out) >= MENTION_MAX:
                break
    return out


def links(inst_name, text):
    """Download links for the files an answer names, as a Markdown line for the
    chat ('' when none): the web chat renders /api/workspace/ links as downloads."""
    files = mentioned(inst_name, text)
    base = "/api/workspace/" + urllib.parse.quote(inst_name, safe="") + "/"
    return ("\n\n📎 " + " · ".join(f"[{os.path.basename(f)}]({base}{urllib.parse.quote(f)})" for f in files)) if files else ""
