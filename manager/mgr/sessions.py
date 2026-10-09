# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Login sessions (security boundary) after an OIDC login: web (cookie) and
device (Bearer). The token is 32 random bytes handed out once; only its
sha256 is stored. Expiry slides with use. The IdP refresh token is kept to
re-check the user every CHECK_EVERY seconds (mgr/auth.py).

Part of the mgr package: no import from manager.py.
"""
import hashlib
import os
import secrets
import sqlite3
import time
from contextlib import closing

from mgr import paths as _paths

DB = os.path.join(_paths.BASE, "sessions.db")
TTL = {"web": 30 * 86400, "device": 90 * 86400}   # sliding; the IdP re-check is what revokes
CHECK_EVERY = 900
_COLS = "id, sub, name, email, role, kind, device_name, refresh, created, expires, last_check, last_use"


def _db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS sessions(
        id INTEGER PRIMARY KEY AUTOINCREMENT, hash TEXT UNIQUE NOT NULL,
        sub TEXT, name TEXT, email TEXT, role TEXT, kind TEXT, device_name TEXT,
        refresh TEXT, created INTEGER, expires INTEGER, last_check INTEGER, last_use INTEGER)""")
    return c


def _hash(token):
    return hashlib.sha256(str(token).encode()).hexdigest()


def create(p, kind, refresh_token, device_name=""):
    """New session for Principal `p`; returns the token (the only copy)."""
    token, now = secrets.token_urlsafe(32), int(time.time())
    with closing(_db()) as c, c:
        c.execute("INSERT INTO sessions(hash, sub, name, email, role, kind, device_name, refresh, created,"
                  " expires, last_check, last_use) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (_hash(token), p.sub, p.name, p.email, p.role, kind, device_name, refresh_token,
                   now, now + TTL[kind], now, now))
    return token


def get(token, now=None):
    """The live session row (dict) for a token, its expiry slid; None when
    unknown or expired (an expired one is deleted)."""
    now = int(now or time.time())
    with closing(_db()) as c, c:
        r = c.execute(f"SELECT {_COLS} FROM sessions WHERE hash=?", (_hash(token),)).fetchone()
        if r is None:
            return None
        if r["expires"] < now:
            c.execute("DELETE FROM sessions WHERE id=?", (r["id"],))
            return None
        if now - r["last_use"] > 60:        # slide, but not one write per request
            c.execute("UPDATE sessions SET last_use=?, expires=? WHERE id=?", (now, now + TTL[r["kind"]], r["id"]))
    return dict(r)


def claim_check(sid, seen_last_check, now):
    """True for exactly one of several parallel requests that all saw the
    same stale last_check: that one refreshes, the others go on (a rotated
    refresh token may be used only once)."""
    with closing(_db()) as c, c:
        return c.execute("UPDATE sessions SET last_check=? WHERE id=? AND last_check=?",
                         (int(now), sid, seen_last_check)).rowcount == 1


def due_for_check(before):
    """Live sessions with an IdP refresh token, last checked before `before`."""
    with closing(_db()) as c:
        rows = c.execute(f"SELECT {_COLS} FROM sessions WHERE refresh<>'' AND last_check<? AND expires>=?",
                         (int(before), int(time.time()))).fetchall()
    return [dict(r) for r in rows]


def update(sid, p, refresh_token):
    with closing(_db()) as c, c:
        c.execute("UPDATE sessions SET name=?, email=?, role=?, refresh=? WHERE id=?",
                  (p.name, p.email, p.role, refresh_token, sid))


def delete(sid):
    with closing(_db()) as c, c:
        return c.execute("DELETE FROM sessions WHERE id=?", (int(sid),)).rowcount == 1


def delete_token(token):
    with closing(_db()) as c, c:
        c.execute("DELETE FROM sessions WHERE hash=?", (_hash(token),))


def list_all():
    """For the admin view: no hash, no refresh token."""
    with closing(_db()) as c, c:
        rows = c.execute("SELECT id, sub, name, role, kind, device_name, created, expires, last_use "
                         "FROM sessions WHERE expires>=? ORDER BY last_use DESC", (int(time.time()),)).fetchall()
    return [dict(r) for r in rows]
