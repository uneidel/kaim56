# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Conversation persistence: the history survives an agent restart.

The history lived in memory only, so every instance restart (a harness
rebuild, a rootfs rebuild, a host reboot) dropped the running conversation.
Between turns the durable part of the history is written to the memory
folder (``<MEMORY_DIR>/.state/history.json``, gitignored there by the
manager) and read back once at start. Durable = the conversation and its
[Summary]/[Branch]/[Sidenote] blocks; the per-turn injections (playbooks,
recall, memory index, missions, [Now]) are dropped because the next turn
sets them up fresh, and image bytes are dropped because they are large and
rarely needed twice. The system prompt is never stored: the current one
from the config applies after a restart. HISTORY_PERSIST=0 turns it off.

Generations: a squad turn names its context (the member's ticket, the lead's
phase, "channel"; the manager's squadgate.generation). switch() parks the
running history under ``.state/gens/<generation>.json`` and brings that
generation's back — a new one starts empty. The parked files stay: every
generation's conversation is kept in the memory folder on the host.

Part of the openrouter agent package (runs inside the VM): no import from the
package root. Sibling modules are used as ``_name.func`` (module attribute),
so a test can replace one definition in one place.
"""
import json
import os
import re
import time

from . import config as _config
from . import context as _context

PERSIST = os.environ.get("HISTORY_PERSIST", "1") != "0"
STATE_DIR = ".state"
FILE = "history.json"
_ROLES = ("user", "assistant", "system", "tool")
_last = [""]           # last serialized message list written (no rewrite when nothing changed)
GENS, GEN_FILE, UNNAMED = "gens", "generation", "chat"
_gen = [None]          # the generation the running history belongs to (None: not read yet)


def path():
    d = _context.MEMORY_DIR
    return os.path.join(d, STATE_DIR, FILE) if (PERSIST and d) else ""


def _transient_tags():
    return (_context.RECALL_TAG, _context.PLAYBOOK_TAG, _context.MEMINDEX_TAG,
            _context.NOW_TAG, _context.MISSION_TAG)


def _durable(msgs):
    """The messages worth keeping across a restart (see the module docstring)."""
    out = []
    for m in msgs:
        if m.get("role") == "system" and str(m.get("content", "")).startswith(_transient_tags()):
            continue
        c = m.get("content")
        if isinstance(c, list):        # vision content: keep the text, drop the image bytes
            txt = " ".join(p.get("text", "") for p in c
                           if isinstance(p, dict) and p.get("type") == "text").strip()
            m = dict(m, content=(txt + " " if txt else "") + "[image not kept across restart]")
        out.append(m)
    return out


def save():
    """Write the durable history (everything after the system prompt).
    Called between turns only — never mid tool cycle, so a tool result is
    never stored without its tool_calls. Never raises: a failed write costs
    the restart, not the turn. True when a file was written."""
    p = path()
    if not p:
        return False
    body = json.dumps(_durable(_context._history[1:]), ensure_ascii=False)
    if body == _last[0]:
        return False
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write('{"v": 1, "ts": %r, "messages": %s}' % (time.time(), body))
        os.replace(tmp, p)
    except OSError as e:
        _config.log("history: not saved:", repr(e)[:120])
        return False
    _last[0] = body
    return True


def _read(p):
    """-> (messages, ts) of a history file; raises OSError/ValueError/KeyError/TypeError."""
    with open(p, encoding="utf-8") as fh:
        d = json.load(fh)
    msgs = d["messages"]
    if not isinstance(msgs, list) or not all(isinstance(m, dict) and m.get("role") in _ROLES for m in msgs):
        raise ValueError("not a message list")
    return msgs, float(d.get("ts") or 0)


def _write(p, msgs):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write('{"v": 1, "ts": %r, "messages": %s}' % (time.time(), json.dumps(msgs, ensure_ascii=False)))
    os.replace(tmp, p)


def switch(gen):
    """Make `gen` the running generation (see the module docstring). Never
    raises: a failed park costs that context, not the turn. -> True when the
    context changed."""
    p, gen = path(), re.sub(r"[^A-Za-z0-9._-]", "_", str(gen or ""))[:120]
    if not (p and gen):
        return False
    d = os.path.dirname(p)
    if _gen[0] is None:
        try:
            with open(os.path.join(d, GEN_FILE), encoding="utf-8") as fh:
                _gen[0] = fh.read().strip()
        except OSError:
            _gen[0] = ""
    if gen == _gen[0]:
        return False
    try:
        _write(os.path.join(d, GENS, (_gen[0] or UNNAMED) + ".json"), _durable(_context._history[1:]))
    except OSError as e:
        _config.log("generation: not parked:", repr(e)[:120])
    try:
        msgs = _read(os.path.join(d, GENS, gen + ".json"))[0]
    except (OSError, ValueError, KeyError, TypeError):
        msgs = []
    _context._history[1:] = msgs
    _gen[0], _last[0] = gen, ""
    try:
        with open(os.path.join(d, GEN_FILE), "w", encoding="utf-8") as fh:
            fh.write(gen)
    except OSError:
        pass
    _config.log(f"generation: {gen} ({len(msgs)} message(s) back)")
    save()
    return True


def load():
    """Read the history back at start. Returns the timestamp of the saved
    state (0.0 when nothing was restored) so the caller can treat the idle
    time before the restart like idle time in a running process."""
    p = path()
    if not p or len(_context._history) > 1:      # nothing to restore, or already running
        return 0.0
    try:
        msgs, ts = _read(p)
    except (OSError, ValueError, KeyError, TypeError) as e:
        if not isinstance(e, FileNotFoundError):
            _config.log("history: not restored:", repr(e)[:120])
        return 0.0
    if not msgs:
        return 0.0
    _context._history[1:] = msgs
    _last[0] = json.dumps(msgs, ensure_ascii=False)
    _config.log(f"history: restored {len(msgs)} message(s) from {p}")
    return ts
