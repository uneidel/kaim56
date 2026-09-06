#!/usr/bin/env python3
# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Render the manager's client script (mgr/ui_js.py) to a plain .js file.

Why: the script is a Python string. An escape that Python interprets ('\\n'
instead of '\\\\n') silently becomes a real newline inside a JS string
literal — the page still serves with HTTP 200, only the browser's parser
fails, and every unit test stays green. That shipped once (2026-09-06,
missions dialog). run-tests.sh feeds the rendered file to `node --check`
where a node is available (host or the mcp-hub container), so the class is
caught before a restart.

Usage: check_js.py <out.js>   (exit 0, prints nothing on success)
"""
import importlib.util
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
src = os.path.join(os.path.dirname(here), "mgr", "ui_js.py")
spec = importlib.util.spec_from_file_location("uijs", src)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
js = mod.JS
i = js.index("<script>") + len("<script>")
j = js.index("</script>")
body = js[i:j]
# placeholders the manager fills at render time -> valid JS stand-ins
for ph, val in (("__TPLJSON__", "{}"), ("__SETTINGS__", "{}"), ("__PERSONAS__", "[]"), ("__SKILLS__", "[]")):
    body = body.replace(ph, val)
out = sys.argv[1] if len(sys.argv) > 1 else "/dev/stdout"
with open(out, "w") as fh:
    fh.write(body)
