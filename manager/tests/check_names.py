#!/usr/bin/env python3
# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Undefined-name gate: names that are USED but never defined or imported.

Why this exists: two real defects in one week were exactly this class —
`mgr/store.py` lost `import uuid` in the package split (task creation broken
for 13 days) and the agent gained code using `re` without importing it (log
previews silently fell back). Python compiles both without complaint; the
crash only comes when the line RUNS, and if no test runs it, nobody notices.

This is pyflakes-light in stdlib AST, tuned to this codebase's style
(module-level functions, injected cross-references via configure()). It
deliberately over-approximates scopes a little; anything it cannot be sure
about, it stays silent on — the gate must never cry wolf.
"""
import ast
import builtins
import os
import sys

BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__",
                                 "__loader__", "__package__", "__builtins__"}


def defined_names(tree):
    """Everything the module defines at ANY level: imports, assignments,
    functions, classes, arguments, comprehension targets, exception names,
    with-targets, for-targets, globals declared in functions."""
    names = set()

    class D(ast.NodeVisitor):
        def visit_Import(self, n):
            for a in n.names:
                names.add((a.asname or a.name).split(".")[0])

        def visit_ImportFrom(self, n):
            for a in n.names:
                names.add(a.asname or a.name)

        def visit_FunctionDef(self, n):
            names.add(n.name)
            for a in (n.args.args + n.args.posonlyargs + n.args.kwonlyargs):
                names.add(a.arg)
            if n.args.vararg:
                names.add(n.args.vararg.arg)
            if n.args.kwarg:
                names.add(n.args.kwarg.arg)
            self.generic_visit(n)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, n):
            names.add(n.name)
            self.generic_visit(n)

        def visit_Lambda(self, n):
            for a in (n.args.args + n.args.posonlyargs + n.args.kwonlyargs):
                names.add(a.arg)
            if n.args.vararg:
                names.add(n.args.vararg.arg)
            if n.args.kwarg:
                names.add(n.args.kwarg.arg)
            self.generic_visit(n)

        def visit_Name(self, n):
            if isinstance(n.ctx, (ast.Store, ast.Del)):
                names.add(n.id)
            self.generic_visit(n)

        def visit_ExceptHandler(self, n):
            if n.name:
                names.add(n.name)
            self.generic_visit(n)

        def visit_Global(self, n):
            names.update(n.names)

        def visit_Nonlocal(self, n):
            names.update(n.names)

    D().visit(tree)
    return names


def used_names(tree):
    """(name, lineno) for every load-context name."""
    out = []

    class U(ast.NodeVisitor):
        def visit_Name(self, n):
            if isinstance(n.ctx, ast.Load):
                out.append((n.id, n.lineno))
            self.generic_visit(n)

    U().visit(tree)
    return out


def check_file(path):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, path)
    have = defined_names(tree) | BUILTINS
    seen = set()
    problems = []
    for name, line in used_names(tree):
        if name in have or name in seen:
            continue
        seen.add(name)
        problems.append((path, line, name))
    return problems


def main(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, fns in os.walk(p):
                files += [os.path.join(root, f) for f in fns if f.endswith(".py")]
        elif p.endswith(".py"):
            files.append(p)
    bad = []
    for f in sorted(set(files)):
        try:
            bad += check_file(f)
        except SyntaxError as e:
            bad.append((f, e.lineno or 0, f"SYNTAX: {e.msg}"))
    for path, line, name in bad:
        print(f"{path}:{line}: undefined name '{name}'")
    if bad:
        print(f"\n{len(bad)} undefined name(s) — a missing import fails only "
              f"when the line RUNS; this gate fails now instead.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["."]))
