# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Saddler: a weekly failure digest over the tool-call audit.

Idea from Microsoft's AutoSaddler (diagnose execution traces, propose
structured patches, reflect on the outcome) — reduced to what a one-user
platform actually needs and to this codebase's rules (stdlib, no framework):

- The MANAGER computes a digest of failed tool calls: grouped by instance,
  tool and error signature, this week next to last week (the week-over-week
  drop or rise IS the reflection step).
- A scheduled task hands the digest to the orchestrator, which proposes
  patches as PLAYBOOK lines and notifies the user. Applying them stays a
  HUMAN decision — the saddler never patches anything itself.

Part of the mgr package: no imports from manager.py. The audit directory is
injected via configure().
"""
import json
import os
import re
import time

AUDIT_DIR = None      # via configure()


def configure(audit_dir):
    global AUDIT_DIR
    AUDIT_DIR = audit_dir


def _signature(err):
    """Group errors by their shape, not their exact words: digits, hashes and
    URLs vary per call, the failure class does not."""
    e = str(err)[:160]
    e = re.sub(r"https?://\S+", "<url>", e)
    e = re.sub(r"0x[0-9a-fA-F]+", "<hex>", e)
    e = re.sub(r"\d+", "<n>", e)
    return e.strip()


def digest(days=7):
    """Failure groups for the last `days`, with the previous window next to
    them. Also counts total calls, so 3 failures mean something different at
    30 calls than at 3000."""
    now = int(time.time())
    cur_from, prev_from = now - days * 86400, now - 2 * days * 86400
    groups = {}
    totals = {"cur_calls": 0, "prev_calls": 0, "cur_failed": 0, "prev_failed": 0}
    for fn in sorted(os.listdir(AUDIT_DIR or "") or []):
        if not fn.endswith(".jsonl"):
            continue
        inst = fn[:-6]
        try:
            lines = open(os.path.join(AUDIT_DIR, fn)).readlines()
        except OSError:
            continue
        for line in lines:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = r.get("ts", 0)
            if ts < prev_from:
                continue
            cur = ts >= cur_from
            totals["cur_calls" if cur else "prev_calls"] += 1
            if r.get("ok", True):
                continue
            totals["cur_failed" if cur else "prev_failed"] += 1
            key = (inst, r.get("tool", "?"), _signature(r.get("err", "")))
            g = groups.setdefault(key, {
                "instance": inst, "tool": key[1], "error": key[2],
                "cur": 0, "prev": 0, "example_target": "", "example_ts": 0})
            g["cur" if cur else "prev"] += 1
            if cur and ts > g["example_ts"]:
                g["example_ts"] = ts
                g["example_target"] = str(r.get("target", ""))[:200]
    out = sorted(groups.values(), key=lambda g: (-g["cur"], -g["prev"]))
    return {"days": days, "totals": totals, "groups": out}


def render(d):
    """The digest as text for an agent prompt — compact, but with everything a
    diagnosis needs: instance, tool, error shape, counts both windows, and one
    concrete example target."""
    t = d["totals"]
    lines = [f"Tool-call failure digest, last {d['days']} days "
             f"(previous window in brackets):",
             f"- calls: {t['cur_calls']} ({t['prev_calls']}), "
             f"failed: {t['cur_failed']} ({t['prev_failed']})"]
    if not d["groups"]:
        return "\n".join(lines + ["- no failures recorded. Nothing to do."])
    for g in d["groups"][:30]:
        lines.append(
            f"- [{g['instance']}] {g['tool']}: {g['cur']}x ({g['prev']}x) — "
            f"{g['error'] or '(no error text)'}"
            + (f" | e.g. target: {g['example_target']}" if g["example_target"] else ""))
    if len(d["groups"]) > 30:
        lines.append(f"- … {len(d['groups']) - 30} more groups omitted")
    return "\n".join(lines)
