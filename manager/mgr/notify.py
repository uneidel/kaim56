# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Notifications: Push-Kanal an App + Web (Store, rev/Long-Poll, mark-read).

Teil des mgr-Pakets; kein Import aus manager. BASE via configure().
"""
import json
import os
import threading
import time
import uuid

from mgr.gateway import redact_secrets

NOTIF_FILE = None


def configure(base):
    global NOTIF_FILE, _notif_rev
    NOTIF_FILE = os.path.join(base, "notifications.json")
    try:
        _notif_rev = int(os.path.getmtime(NOTIF_FILE) * 1000)
    except OSError:
        _notif_rev = 0


# ---- Notifications: Push an App + Web -------------------------------------
# Eigener Kanal neben Signal: ein Agent ruft das Tool `notify`, der Eintrag
# landet hier und wird von App (Android-Systemnotification) und Web-Manager
# (Glocke + optional Browser-Notification) via Long-Poll abgeholt. Global
# (nicht pro Instanz), kurzlebig, gedeckelt.
_notif_lock = threading.Lock()
NOTIF_MAX = 200
NOTIF_RATE = (30, 300)          # max 30 in 5 min gegen Spam
_notif_sent = []
_notif_cv = threading.Condition()
_notif_rev = 0


def load_notifications():
    try:
        with open(NOTIF_FILE) as fh:
            d = json.load(fh)
            return d if isinstance(d, list) else []
    except (FileNotFoundError, ValueError):
        return []


def _save_notifications(lst):
    tmp = NOTIF_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(lst[-NOTIF_MAX:], fh, ensure_ascii=False)
    os.replace(tmp, NOTIF_FILE)


def _bump_notif_rev():
    global _notif_rev
    with _notif_cv:
        _notif_rev = max(_notif_rev + 1, int(time.time() * 1000))
        _notif_cv.notify_all()


def notify_add(instance, title, body, link=""):
    """link steuert, wohin ein Klick auf die Notification fuehrt:
    'missions' | 'tasks' | 'chat:<instanz>' | '' (nichts)."""
    title, h1 = redact_secrets((title or "").strip()[:120])
    body, h2 = redact_secrets((body or "").strip()[:1000])
    if not title and not body:
        return None, "empty"
    now = time.time()
    with _notif_lock:
        limit, window = NOTIF_RATE
        _notif_sent[:] = [t for t in _notif_sent if now - t < window]
        if len(_notif_sent) >= limit:
            return None, f"rate limit: max {limit} per {window // 60} min"
        _notif_sent.append(now)
        nid = uuid.uuid4().hex[:10]
        lst = load_notifications()
        lst.append({"id": nid, "ts": int(now), "title": title or "(ohne Titel)",
                    "body": body, "instance": instance or "", "read": False,
                    "link": str(link or "")[:80]})
        _save_notifications(lst)
    _bump_notif_rev()
    return nid, "ok"


def notif_mark_read(nid=None, mark_all=False):
    with _notif_lock:
        lst = load_notifications()
        n = 0
        for it in lst:
            if mark_all or it.get("id") == nid:
                if not it.get("read"):
                    it["read"] = True
                    n += 1
        if n:
            _save_notifications(lst)
    if n:
        _bump_notif_rev()
    return n


def notif_clear():
    """Alle Benachrichtigungen entfernen (UI-Aktion „Leeren")."""
    with _notif_lock:
        n = len(load_notifications())
        _save_notifications([])
    if n:
        _bump_notif_rev()
    return n


def wait_notifs(since, timeout):
    """(rev, notifications|None) — analog zu wait_chats."""
    deadline = time.time() + max(0.0, timeout)
    with _notif_cv:
        while _notif_rev <= since:
            rest = deadline - time.time()
            if rest <= 0:
                break
            _notif_cv.wait(min(1.0, rest))
        rev = _notif_rev
    return rev, (load_notifications() if rev > since else None)


