# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Playbooks (fixed rules per agent) + prompt templates (/name commands).

Part of the mgr package; plain file stores under BASE, no upward dependency.
"""
import json
import os
import re
import threading
import time
import uuid

PLAYBOOKS_FILE = None
PROMPTS_FILE = None


def configure(base):
    global PLAYBOOKS_FILE, PROMPTS_FILE
    PLAYBOOKS_FILE = os.path.join(base, "playbooks.json")
    PROMPTS_FILE = os.path.join(base, "prompts.json")


# ---- Playbooks: permanent rules that ALWAYS apply (not only on relevance
# like the semantic memory). The agent fills them itself from the conversation
# and gets them shown in the prompt every turn. Kept separate per instance.
_pb_lock = threading.Lock()
PB_MAX = 40


def load_playbooks():
    try:
        with open(PLAYBOOKS_FILE) as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _save_playbooks(d):
    try:
        tmp = PLAYBOOKS_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(d, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, PLAYBOOKS_FILE)
    except OSError:
        pass


def pb_list(instance):
    return load_playbooks().get(instance, [])


def pb_add(instance, text):
    text = (text or "").strip()
    if not instance or not text:
        return None
    with _pb_lock:
        d = load_playbooks()
        lst = d.setdefault(instance, [])
        low = text.lower()
        if any(x.get("text", "").lower() == low for x in lst):
            return "exists"
        pid = uuid.uuid4().hex[:6]
        lst.append({"id": pid, "text": text, "ts": int(time.time())})
        d[instance] = lst[-PB_MAX:]
        _save_playbooks(d)
    return pid


def pb_remove(instance, pid):
    with _pb_lock:
        d = load_playbooks()
        lst = d.get(instance, [])
        n = len(lst)
        d[instance] = [x for x in lst if x.get("id") != str(pid)]
        _save_playbooks(d)
        return n - len(d[instance])


# ---- Prompt-Templates: /name -> gespeicherter Prompt (pi.dev-Idee) ----------
_pr_lock = threading.Lock()
PROMPTS_MAX = 50


def load_prompts():
    try:
        with open(PROMPTS_FILE) as fh:
            d = json.load(fh)
            return d if isinstance(d, list) else []
    except (FileNotFoundError, ValueError):
        return []


def prompt_upsert(name, text):
    name = re.sub(r"[^a-z0-9_-]", "", str(name).lower())[:32]
    text = str(text or "").strip()[:4000]
    if not name or not text:
        return "error: name/text missing"
    if name in ("reset", "fresh", "reasoning", "goal", "model", "task", "help", "agents"):
        return f"error: '{name}' is a built-in command"
    with _pr_lock:
        lst = load_prompts()
        cur = next((p for p in lst if p.get("name") == name), None)
        if cur:
            cur["text"] = text
        elif len(lst) >= PROMPTS_MAX:
            return f"error: max {PROMPTS_MAX} prompts"
        else:
            lst.append({"name": name, "text": text})
        tmp = PROMPTS_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(lst, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, PROMPTS_FILE)
    return "saved"


def prompt_delete(name):
    with _pr_lock:
        lst = load_prompts()
        n = len(lst)
        lst = [p for p in lst if p.get("name") != name]
        tmp = PROMPTS_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(lst, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, PROMPTS_FILE)
    return "deleted" if len(lst) < n else "unknown"


