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
HISTORY_DB = None     # via configure(); read-only here (llm_usage)


def configure(audit_dir: str, history_db: "str | None" = None) -> None:
    global AUDIT_DIR, HISTORY_DB
    AUDIT_DIR = audit_dir
    HISTORY_DB = history_db


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
        if inst.startswith("e2e-"):
            continue          # the test suite's own traces are not operations
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
    return {"days": days, "totals": totals, "groups": out,
            "usage": _usage(cur_from, prev_from)}


def _usage(cur_from, prev_from):
    """LLM calls and cost per instance, both windows. This is the half of a
    feature review that used to be hand work — and the half a wrong hunch
    about "what costs money" cannot survive (the idle heartbeat turned out to
    cost $0.004 while the assumption said 90% of spend)."""
    if not HISTORY_DB:
        return []
    import sqlite3
    try:
        db = sqlite3.connect(HISTORY_DB)
        rows = db.execute(
            """SELECT instance,
                      SUM(CASE WHEN ts >= ? THEN 1 ELSE 0 END),
                      ROUND(SUM(CASE WHEN ts >= ? THEN cost ELSE 0 END), 4),
                      SUM(CASE WHEN ts < ? THEN 1 ELSE 0 END),
                      ROUND(SUM(CASE WHEN ts < ? THEN cost ELSE 0 END), 4)
               FROM llm_usage WHERE ts >= ? AND instance NOT LIKE 'e2e-%'
               GROUP BY instance ORDER BY 3 DESC""",
            (cur_from, cur_from, cur_from, cur_from, prev_from)).fetchall()
        db.close()
    except Exception:
        return []
    return [{"instance": r[0], "cur_calls": r[1] or 0, "cur_cost": r[2] or 0,
             "prev_calls": r[3] or 0, "prev_cost": r[4] or 0} for r in rows]


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
    if d.get("usage"):
        lines.append("LLM usage per instance (previous window in brackets):")
        for u in d["usage"][:12]:
            lines.append(f"- {u['instance']}: {u['cur_calls']} calls, "
                         f"${u['cur_cost']} ({u['prev_calls']} calls, ${u['prev_cost']})")
    return "\n".join(lines)
