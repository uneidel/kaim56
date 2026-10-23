# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Action log (security boundary): what the HOST saw an agent do, out of the agent's reach.

The audit (mgr/audit.py) is what the agent reports about itself — a
compromised agent can lie there or stay silent. This log is written only by
the manager from what passes through it: the tool calls the model asked for
(LLM proxy), every manager API request of a VM, every MCP hub call.

Append-only: one JSONL file, root-only (0600), opened O_APPEND, never
truncated or rotated; ``chattr +a`` where the filesystem allows it (then not
even root rewrites it without removing the flag first). Every line carries
``prev`` and ``hash`` = sha256(prev + the line without hash), so a deleted,
changed or reordered line breaks the chain — ``verify()`` finds it.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time

from mgr import paths as _paths

ACTLOG_PATH = os.path.join(_paths.BASE, "actlog.jsonl")
FIELD_MAX = 400            # per string field; the log keeps WHAT, not whole payloads
GENESIS = "0" * 64
_lock = threading.Lock()
_last = {}                 # path -> last hash (read once from the file's tail)


def _clip(v):
    if isinstance(v, str):
        return v if len(v) <= FIELD_MAX else v[:FIELD_MAX] + f"…(+{len(v) - FIELD_MAX})"
    if isinstance(v, dict):
        return {str(k)[:80]: _clip(x) for k, x in list(v.items())[:20]}
    if isinstance(v, (list, tuple)):
        return [_clip(x) for x in list(v)[:20]]
    return v if v is None or isinstance(v, (bool, int, float)) else _clip(str(v))


def _hash(prev, rec):
    return hashlib.sha256((prev + json.dumps(rec, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()


def _tail_hash(path):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 65536))
            lines = fh.read().splitlines()
        return json.loads(lines[-1])["hash"] if lines else GENESIS
    except FileNotFoundError:
        return GENESIS


def record(inst, kind, **fields):
    """Append one action. Never raises: a full disk must not stop the platform
    — the failure goes to the journal instead."""
    path = ACTLOG_PATH
    try:
        with _lock:
            prev = _last.get(path) or _tail_hash(path)
            rec = {"ts": round(time.time(), 3), "inst": inst or "", "kind": kind,
                   **{k: _clip(v) for k, v in fields.items()}, "prev": prev}
            rec["hash"] = _hash(prev, rec)
            new = not os.path.exists(path)
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, (json.dumps(rec, ensure_ascii=False) + "\n").encode())
            finally:
                os.close(fd)
            _last[path] = rec["hash"]
        if new:          # best effort: ext4/xfs as root; elsewhere the chain alone
            subprocess.run(["chattr", "+a", path], capture_output=True, timeout=5, check=False)
    except Exception as e:
        print(f"actlog: {kind} for {inst} not recorded: {e!r}", file=sys.stderr)


def verify(path=None):
    """-> (ok, lines checked, first bad line number or 0, reason)."""
    prev, n = GENESIS, 0
    try:
        with open(path or ACTLOG_PATH, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                try:
                    rec = json.loads(line)
                    h = rec.pop("hash")
                except (ValueError, KeyError):
                    return False, n, n, "not a log line"
                if rec.get("prev") != prev:
                    return False, n, n, "chain broken (line missing, inserted or reordered)"
                if _hash(prev, rec) != h:
                    return False, n, n, "line changed"
                prev = h
    except FileNotFoundError:
        pass
    return True, n, 0, ""


def tool_calls(body):
    """The tool calls an LLM answer asks for: [{name, args}] from a chat
    completion (bytes/dict) — what the model decided, whatever the agent
    later reports."""
    try:
        j = json.loads(body) if isinstance(body, (bytes, str)) else body
        msg = ((j.get("choices") or [{}])[0].get("message") or {})
        return [{"name": (c.get("function") or {}).get("name", ""),
                 "args": (c.get("function") or {}).get("arguments", "")}
                for c in msg.get("tool_calls") or []]
    except (ValueError, AttributeError, TypeError, IndexError):
        return []


class StreamCalls:
    """The same for an SSE stream: feed() every passed-through line, calls()
    at the end. Tool-call deltas are merged by index."""

    def __init__(self):
        self._c = {}

    def feed(self, line):
        if b'"tool_calls"' not in line:
            return
        try:
            j = json.loads(line.strip()[5:] if line.strip().startswith(b"data:") else line)
            for d in ((j.get("choices") or [{}])[0].get("delta") or {}).get("tool_calls") or []:
                c = self._c.setdefault(d.get("index", 0), {"name": "", "args": ""})
                f = d.get("function") or {}
                c["name"] += f.get("name") or ""
                if len(c["args"]) < FIELD_MAX * 2:
                    c["args"] += f.get("arguments") or ""
        except (ValueError, AttributeError, TypeError, IndexError):
            pass

    def calls(self):
        return [self._c[k] for k in sorted(self._c)]


if __name__ == "__main__":          # python3 -m mgr.actlog  -> check the chain
    ok, n, bad, why = verify(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"{'OK' if ok else 'BROKEN'}: {n} lines" + ("" if ok else f", line {bad}: {why}"))
    sys.exit(0 if ok else 1)
