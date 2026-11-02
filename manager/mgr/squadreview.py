# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Squad result reviews (security boundary): a writer's HTML results — UI
mockups, design documents — for the operator to view, annotate and approve.

The lead asks for a review (squadgate kind ``review``: writer, files,
summary). The writer's folder is snapshotted then (mgr/projwt.py) and the
proposal keeps that commit: every version shows exactly what was asked about,
read from git objects (regular blobs only, never the VM-writable folder).

A result is the squad's own HTML, so it never runs with the manager's origin:
it is served with ``Content-Security-Policy: sandbox allow-scripts`` (an
opaque origin, no cookies, no API) and no network but inline/data:/https
assets. A short script is appended that reports a text selection or an
Alt+click to the parent page by postMessage — the Results tab turns it into
an annotation; the annotations go to the lead with "Request changes".

Part of the mgr package: no import from manager.py.
"""
import html
import re

from mgr import projwt as _projwt

PATH_RE = re.compile(r"^(?!.*(?:^|/)\.)[A-Za-z0-9_][A-Za-z0-9._/-]{0,199}$")
EXT = (".html", ".htm", ".md")
MAX_FILES, MAX_BYTES = 10, 5 << 20
MAX_NOTES, QUOTE_MAX, NOTE_MAX, TEXT_MAX = 50, 300, 1000, 12000
CSP = ("sandbox allow-scripts allow-popups; default-src 'none'; script-src 'unsafe-inline'; "
       "style-src 'unsafe-inline' data: https:; img-src data: https:; font-src data: https:; media-src data: https:")
ANNOTATE = """<script>(()=>{const P=window.parent;if(P===window)return;
document.addEventListener('mouseup',()=>{const q=String(getSelection()||'').trim();if(q)P.postMessage({kaimAnnot:1,quote:q.slice(0,300)},'*')});
document.addEventListener('click',e=>{if(!e.altKey)return;e.preventDefault();const el=e.target,
q=(el.getAttribute('aria-label')||el.alt||el.textContent||'').trim().replace(/\\s+/g,' ').slice(0,200);
P.postMessage({kaimAnnot:1,quote:'<'+el.tagName.toLowerCase()+'> '+q},'*')},true);})();</script>"""


def _full(proj, path):
    sub = proj["source"].get("subdir", "")
    return f"{sub}/{path}" if sub else path


def _entry(proj, commit, path):
    """(sha, size) of a regular file at commit:path, or None."""
    out = _projwt._git_bytes(proj["source"]["path"], ["ls-tree", "-l", "-z", commit, "--", _full(proj, path)])
    meta, _, rel = out.rstrip(b"\0").partition(b"\t")
    if not rel or rel.decode("utf-8", "replace") != _full(proj, path):
        return None
    mode, typ, sha, size = meta.split()
    if typ != b"blob" or mode not in (b"100644", b"100755"):
        return None
    return sha.decode(), int(size)


def snapshot(proj, writer, files):
    """Validate the files, commit the writer's folder -> (commit, files, '') or (None, None, why)."""
    if not isinstance(files, list) or not files:
        return None, None, "files missing: the result's paths in the writer's folder, e.g. [\"design/index.html\"]"
    files = list(dict.fromkeys(str(f or "").strip().removeprefix("./") for f in files))
    if len(files) > MAX_FILES:
        return None, None, f"at most {MAX_FILES} files"
    bad = [f for f in files if not PATH_RE.match(f) or ".." in f.split("/") or not f.lower().endswith(EXT)]
    if bad:
        return None, None, f"not a result path (relative, {'/'.join(EXT)}, no dot segments): {', '.join(bad)[:300]}"
    try:
        commit = _projwt.snapshot(proj, writer)
        for f in files:
            e = _entry(proj, commit, f)
            if e is None:
                return None, None, f"{f} is no file in {writer}'s folder"
            if e[1] > MAX_BYTES:
                return None, None, f"{f} is larger than {MAX_BYTES >> 20} MB"
    except _projwt.GitError as e:
        return None, None, str(e)[:300]
    return commit, files, ""


def page(proj, prop, path):
    """(html bytes, '') of one file of a review, the annotate script appended, or (None, why)."""
    if path not in (prop.get("files") or []):
        return None, "no such file in this review"
    try:
        e = _entry(proj, prop["commit"], path)
        if e is None or e[1] > MAX_BYTES:
            return None, "file gone"
        raw = _projwt._git_bytes(proj["source"]["path"], ["cat-file", "blob", e[0]]).decode("utf-8", "replace")
    except _projwt.GitError as e:
        return None, str(e)[:300]
    if path.lower().endswith(".md"):
        raw = ('<!doctype html><meta charset=utf-8><body style="font:15px/1.6 system-ui;max-width:860px;margin:24px auto;'
               f'padding:0 16px"><pre style="white-space:pre-wrap;font:inherit">{html.escape(raw)}</pre></body>')
    return (raw + ANNOTATE).encode(), ""


def compose(comment, notes):
    """The operator's comment plus the annotations as ONE text for the lead -> (text, '') or (None, why)."""
    comment = str(comment or "").strip()
    if notes is not None and not isinstance(notes, list):
        return None, "annotations must be a list"
    notes = [n for n in notes or [] if isinstance(n, dict) and str(n.get("comment") or "").strip()]
    if len(notes) > MAX_NOTES:
        return None, f"at most {MAX_NOTES} annotations"
    lines = [f"{i}. {str(n.get('file') or '')[:200]}: «{str(n.get('quote') or '').strip()[:QUOTE_MAX]}» — "
             f"{str(n['comment']).strip()[:NOTE_MAX]}" for i, n in enumerate(notes, 1)]
    text = "\n".join(([comment] if comment else []) + (["Annotations on the result:"] + lines if lines else []))
    if not text:
        return None, "say what should change (comment or annotations missing)"
    return text[:TEXT_MAX], ""
