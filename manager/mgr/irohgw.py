# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""iroh app<->manager gateway: pairing helpers (node-id + phone allowlist).

Part of the mgr package; plain file stores under BASE/iroh-gw. The gateway
process (dist/iroh-gw) writes its node-id to nodeid.txt and reads the allowlist
from allow.txt (one node-id per line, optional '# label'). This module only
reads the node-id and manages the allowlist — the actual transport lives in the
Rust gateway.
"""
import os
import re
import threading

_lock = threading.Lock()
GW_DIR = None
NODEID_FILE = None
ALLOW_FILE = None

_NODEID_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def configure(base):
    global GW_DIR, NODEID_FILE, ALLOW_FILE
    GW_DIR = os.path.join(base, "iroh-gw")
    NODEID_FILE = os.path.join(GW_DIR, "nodeid.txt")
    ALLOW_FILE = os.path.join(GW_DIR, "allow.txt")


def gateway_node_id():
    """The gateway's node-id (the address the app dials), or '' if not up yet."""
    try:
        with open(NODEID_FILE) as fh:
            nid = fh.read().strip()
        return nid if _NODEID_RE.match(nid) else ""
    except OSError:
        return ""


def load_allow():
    """[{id, label}] of allowlisted phone node-ids (order preserved)."""
    out = []
    try:
        with open(ALLOW_FILE) as fh:
            for line in fh:
                raw = line.strip()
                if not raw or raw.startswith("#"):
                    continue
                nid, _, label = raw.partition("#")
                nid = nid.strip()
                if _NODEID_RE.match(nid):
                    out.append({"id": nid, "label": label.strip()})
    except OSError:
        pass
    return out


def _save_allow(entries):
    os.makedirs(GW_DIR, exist_ok=True)
    lines = ["# kAIm56 iroh app allowlist — one node-id per line, optional '# label'\n"]
    for e in entries:
        lbl = (e.get("label") or "").replace("\n", " ").strip()
        lines.append(e["id"] + (f"  # {lbl}" if lbl else "") + "\n")
    with open(ALLOW_FILE, "w") as fh:
        fh.writelines(lines)


def allow_add(node_id, label=""):
    """Add a phone node-id to the allowlist. Returns (ok, message)."""
    node_id = (node_id or "").strip().lower()
    if not _NODEID_RE.match(node_id):
        return False, "not a node-id (expected 64 hex characters)"
    with _lock:
        entries = load_allow()
        if any(e["id"].lower() == node_id for e in entries):
            # update the label if given
            if label:
                for e in entries:
                    if e["id"].lower() == node_id:
                        e["label"] = label
                _save_allow(entries)
            return True, "already allowed"
        entries.append({"id": node_id, "label": label.strip()})
        _save_allow(entries)
    return True, "added"


def allow_remove(node_id):
    node_id = (node_id or "").strip().lower()
    with _lock:
        entries = [e for e in load_allow() if e["id"].lower() != node_id]
        _save_allow(entries)
    return True, "removed"


def status():
    return {"node_id": gateway_node_id(), "allow": load_allow(),
            "available": bool(gateway_node_id())}
