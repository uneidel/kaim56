#!/usr/bin/env python3
# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""The data-flow graph contract of the Code flow app, and its checker.

The Code flow app (apps/codeflow) hands this file to the repo's agent with the
analysis task; the agent writes it into its workspace and runs it. The app
applies the same rules in the browser (flowcheck.js) to decide whether a round
is done — keep both in step (tests/ runs them on the same fixtures).

The workspace holds the checkout in src/ and the graphs the agent writes in
flow/: flow/overview.json (how data moves between the components) and
flow/<id>.json for every overview node with "detail": "<id>" (the same shape,
one level down, at function level). Nodes carry no code themselves — only
references (file relative to src/, 1-based inclusive lines); the app cuts the
snippets from the checkout, so what is shown is the real code, never the
model's paraphrase of it.

    {"version": 1, "title": str, "summary": str,
     "nodes": [{"id", "label", "kind": entry|process|store|external|output,
                "summary", "code": [{"file", "start", "end", "symbol"}],
                "detail"?: id}],                 # detail only in overview.json
     "edges": [{"source", "target", "data"}]}    # data = WHAT flows, not "calls"

Every code reference names its "symbol" (the function, class, key or table
as written in the code) and that name must occur in the referenced lines.
That is the grounding check: a model that knows a famous repository draws a
plausible graph from memory with line ranges that exist but hold something
else — the first real run did exactly that. The symbol rule forces it to read.

    python3 validate_flow.py                    check flow/ (exit 0 = OK)
    python3 validate_flow.py map                the files in src/ with line counts
    python3 validate_flow.py find NAME          where NAME is defined/used in src/, with line numbers
    python3 validate_flow.py show FILE A B      lines A..B of src/FILE, numbered

Stdlib only.
"""
import json
import os
import re
import sys

KINDS = ("entry", "process", "store", "external", "output")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}$")
MAX_NODES = {"overview": 45, "detail": 60}
MAX_SPAN = 80                   # lines per code reference
MAX_REFS = 4


SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "target", "__pycache__", ".venv", "venv"}


def _lines(path, cache):
    if path not in cache:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                cache[path] = fh.read().split("\n")
        except OSError:
            cache[path] = []
    return cache[path]


def _symbol_token(sym):
    """'User.set_password' -> 'set_password', 'create_app()' -> 'create_app'."""
    parts = [p for p in re.split(r"[.:/#\s()\[\]<>,]+", sym) if p]
    return parts[-1] if parts else ""


def validate_graph(g, root, level="overview", cache=None):
    """Errors of one graph (a list of strings; empty = valid). `root` is the
    workspace (it holds src/ and flow/)."""
    cache = {} if cache is None else cache
    err = []
    if not isinstance(g, dict):
        return ["the file must hold one JSON object"]
    nodes, edges = g.get("nodes"), g.get("edges")
    if not isinstance(nodes, list) or not nodes:
        return ["'nodes' must be a non-empty list"]
    if not isinstance(edges, list):
        return ["'edges' must be a list"]
    if len(nodes) > MAX_NODES[level]:
        err.append(f"{len(nodes)} nodes — at most {MAX_NODES[level]} at the {level} level; group the minor steps")
    if not str(g.get("title") or "").strip():
        err.append("'title' is missing")
    src = os.path.realpath(os.path.join(root, "src"))
    ids = set()
    for i, n in enumerate(nodes):
        where = f"node #{i}"
        if not isinstance(n, dict):
            err.append(f"{where}: not an object"); continue
        nid = str(n.get("id") or "")
        where = f"node '{nid or i}'"
        if not ID_RE.match(nid):
            err.append(f"{where}: id must match {ID_RE.pattern}")
        elif nid in ids:
            err.append(f"{where}: duplicate id")
        ids.add(nid)
        if not str(n.get("label") or "").strip() or len(str(n.get("label"))) > 60:
            err.append(f"{where}: 'label' must be 1-60 characters")
        if n.get("kind") not in KINDS:
            err.append(f"{where}: 'kind' must be one of {', '.join(KINDS)}")
        if len(str(n.get("summary") or "")) > 400:
            err.append(f"{where}: 'summary' over 400 characters")
        code = n.get("code") or []
        if not isinstance(code, list) or len(code) > MAX_REFS:
            err.append(f"{where}: 'code' must be a list of at most {MAX_REFS} references"); code = []
        if not code and n.get("kind") not in ("external", "output"):
            err.append(f"{where}: needs at least one code reference (only external/output nodes may have none)")
        for r in code:
            if not isinstance(r, dict):
                err.append(f"{where}: a code reference is not an object"); continue
            f, s, e = str(r.get("file") or ""), r.get("start"), r.get("end")
            if not f or f.startswith("/") or ".." in f.split("/"):
                err.append(f"{where}: code file '{f}' must be a path relative to src/"); continue
            p = os.path.realpath(os.path.join(src, f))
            if not p.startswith(src + os.sep) or not os.path.isfile(p):
                hint = " — paths are relative to src/, drop the 'src/' prefix" if f.startswith("src/") else ""
                err.append(f"{where}: code file '{f}' does not exist in src/{hint}"); continue
            if not (isinstance(s, int) and isinstance(e, int) and not isinstance(s, bool) and not isinstance(e, bool)):
                err.append(f"{where}: {f}: 'start'/'end' must be integers"); continue
            lines = _lines(p, cache)
            n_lines = len(lines)
            if not (1 <= s <= e <= n_lines):
                err.append(f"{where}: {f}:{s}-{e} is outside the file (1-{n_lines})"); continue
            if e - s + 1 > MAX_SPAN:
                err.append(f"{where}: {f}:{s}-{e} spans {e - s + 1} lines — at most {MAX_SPAN}; point at the part that moves the data")
            sym = str(r.get("symbol") or "").strip()
            tok = _symbol_token(sym)
            if not tok:
                err.append(f"{where}: {f}:{s}-{e} needs a 'symbol' — the function/class/key those lines define or use, as written in the code")
            elif not re.search(r"(?<![A-Za-z0-9_$])" + re.escape(tok) + r"(?![A-Za-z0-9_$])", "\n".join(lines[s - 1:e])):
                err.append(f"{where}: symbol '{sym}' does not occur in {f}:{s}-{e} — read the file "
                           f"(python3 validate_flow.py find {tok}) and point at the real lines")
        d = n.get("detail")
        if d is not None:
            if level != "overview":
                err.append(f"{where}: 'detail' only on overview nodes")
            elif not ID_RE.match(str(d)):
                err.append(f"{where}: 'detail' must be an id like the node ids")
            elif not os.path.isfile(os.path.join(root, "flow", f"{d}.json")):
                err.append(f"{where}: detail graph flow/{d}.json does not exist")
    seen, linked = set(), set()
    for i, e in enumerate(edges):
        if not isinstance(e, dict):
            err.append(f"edge #{i}: not an object"); continue
        s, t, data = str(e.get("source") or ""), str(e.get("target") or ""), str(e.get("data") or "").strip()
        if s not in ids or t not in ids:
            err.append(f"edge {s}->{t}: source and target must be node ids"); continue
        if s == t:
            err.append(f"edge {s}->{t}: a node cannot feed itself")
        if not data or len(data) > 80:
            err.append(f"edge {s}->{t}: 'data' must name what flows, 1-80 characters")
        if (s, t, data) in seen:
            err.append(f"edge {s}->{t}: duplicate")
        seen.add((s, t, data)); linked.update((s, t))
    if len(ids) > 1:
        for nid in sorted(ids - linked):
            err.append(f"node '{nid}': not connected by any edge — the graph follows the data, so every node is on a path")
    return err


def validate_tree(root):
    """{ok, graphs, nodes, errors}: flow/overview.json plus every detail
    graph it points to."""
    cache, errors, graphs, n_nodes = {}, [], 0, 0

    def load(name):
        with open(os.path.join(root, "flow", name), encoding="utf-8") as fh:
            return json.load(fh)
    try:
        ov = load("overview.json")
    except FileNotFoundError:
        return {"ok": False, "graphs": 0, "nodes": 0, "errors": ["flow/overview.json does not exist yet"]}
    except ValueError as e:
        return {"ok": False, "graphs": 0, "nodes": 0, "errors": [f"flow/overview.json is not valid JSON: {e}"]}
    errors += [f"overview: {x}" for x in validate_graph(ov, root, "overview", cache)]
    graphs, n_nodes = 1, len(ov.get("nodes") or []) if isinstance(ov, dict) else 0
    for n in (ov.get("nodes") or []) if isinstance(ov, dict) else []:
        d = n.get("detail") if isinstance(n, dict) else None
        if not d or not ID_RE.match(str(d)) or not os.path.isfile(os.path.join(root, "flow", f"{d}.json")):
            continue
        try:
            g = load(f"{d}.json")
        except ValueError as e:
            errors.append(f"{d}: not valid JSON: {e}"); continue
        errors += [f"{d}: {x}" for x in validate_graph(g, root, "detail", cache)]
        graphs += 1
        n_nodes += len(g.get("nodes") or []) if isinstance(g, dict) else 0
    return {"ok": not errors, "graphs": graphs, "nodes": n_nodes, "errors": errors}


def find(root, name, limit=60):
    """Lines in src/ that mention `name` as a whole word, definitions first."""
    src, hits = os.path.join(root, "src"), []
    pat = re.compile(r"(?<![A-Za-z0-9_$])" + re.escape(name) + r"(?![A-Za-z0-9_$])")
    defn = re.compile(r"^\s*(def|class|function|func|fn|const|let|var|public|private|export|interface|type|struct|CREATE)\b", re.I)
    for d, dirs, files in os.walk(src):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
        for f in sorted(files):
            p = os.path.join(d, f)
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    text = fh.read(2_000_000)
            except OSError:
                continue
            if "\0" in text[:4096]:
                continue
            for i, line in enumerate(text.split("\n"), 1):
                if pat.search(line):
                    hits.append((0 if defn.match(line) else 1, os.path.relpath(p, src), i, line.strip()[:140]))
    return sorted(hits)[:limit]


def repo_map(root, limit=500):
    """The files in src/ with line counts (binaries and vendored folders skipped)."""
    src, rows, total = os.path.join(root, "src"), [], 0
    for d, dirs, files in os.walk(src):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
        for f in sorted(files):
            p = os.path.join(d, f)
            total += 1
            try:
                if os.path.getsize(p) > 2_000_000:
                    continue
                with open(p, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            if b"\0" in data[:4096] or len(rows) >= limit:
                continue
            n = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
            rows.append(f"{os.path.relpath(p, src)}  ({n})")
    if total > len(rows):
        rows.append(f"… {total - len(rows)} more (binaries, large files or over the limit)")
    return rows


def show(root, f, a, b):
    lines = _lines(os.path.join(root, "src", f), {})
    return [(i, lines[i - 1]) for i in range(max(1, a), min(len(lines), b) + 1)]


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["find"] and len(args) >= 2:
        for _k, f, i, line in find(os.getcwd(), args[1]):
            print(f"{f}:{i}: {line}")
        sys.exit(0)
    if args[:1] == ["map"]:
        print("\n".join(repo_map(os.getcwd())))
        sys.exit(0)
    if args[:1] == ["show"] and len(args) >= 4:
        for i, line in show(os.getcwd(), args[1], int(args[2]), int(args[3])):
            print(f"{i:5d}  {line}")
        sys.exit(0)
    root = args[0] if args else os.getcwd()
    res = validate_tree(root)
    if res["ok"]:
        print(f"OK: {res['graphs']} graph(s), {res['nodes']} node(s)")
        sys.exit(0)
    print(f"{len(res['errors'])} problem(s):")
    for e in res["errors"][:60]:
        print(" -", e)
    sys.exit(1)
