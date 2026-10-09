# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Login (security boundary): the Basic-auth credentials, OIDC sessions and the role table, lockout after failed logins, and which browser Origins are trusted (CSRF guard).

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import http.cookies
import os
import re
import subprocess
import threading
import time
import urllib.request

from mgr import audit as _audit
from mgr import oidc as _oidc
from mgr import sessions as _sessions
from mgr import settings as _settings


_trusted_cache = {"ts": 0.0, "hosts": set()}


def trusted_hosts():
    """Hosts a browser may present as Origin: the site's public name(s),
    localhost and the host's own addresses (refreshed every minute), plus
    site.json TRUSTED_HOSTS for a reverse proxy under another name."""
    now = time.time()
    if now - _trusted_cache["ts"] > 60:
        hosts = {_settings.PUBLIC_HOST, "localhost", "127.0.0.1", "::1"}
        hosts.update(str(x) for x in (_settings.SITE.get("TRUSTED_HOSTS") or []) if x)
        try:
            r = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True, timeout=5)
            hosts.update(re.findall(r"inet (\d+\.\d+\.\d+\.\d+)", r.stdout))
        except (OSError, subprocess.SubprocessError):
            pass
        _trusted_cache.update(ts=now, hosts={h.lower() for h in hosts})
    return _trusted_cache["hosts"]


def origin_allowed(origin):
    """CSRF guard for state-changing requests. Browsers send Origin on every
    POST; the app, the desktop client and curl do not (empty = fine). A page
    on another site — or a DNS-rebound name — carries a foreign Origin and is
    refused, so it cannot export a folder into a VM or create an instance."""
    o = (origin or "").strip()
    if not o:
        return True
    try:
        host = (urllib.parse.urlsplit(o).hostname or "").lower()
    except ValueError:
        return False
    return bool(host) and host in trusted_hosts()

USER = os.environ.get("MANAGER_USER", "admin")
PW = os.environ.get("MANAGER_PASS", "")   # empty => no auth (only behind Traefik!)

# Failed logins per client: after AUTH_FAILS_MAX within AUTH_FAIL_WINDOW the
# client is refused for AUTH_LOCK seconds, right password or not.
AUTH_FAILS_MAX, AUTH_FAIL_WINDOW, AUTH_LOCK = 10, 900, 900
_auth_fails, _auth_lock = {}, threading.Lock()
_PROXY_PEERS = ("127.0.0.1", "::1", "172.17.")


def auth_client_key(peer, xff=""):
    """Who is knocking: the socket peer — or, when that is a proxy on this
    host (Traefik on loopback/docker), the first X-Forwarded-For hop."""
    if xff and (peer in _PROXY_PEERS or peer.startswith(_PROXY_PEERS[2])):
        return xff.split(",")[0].strip() or peer
    return peer


def auth_locked(key, now=None):
    now = now or time.time()
    with _auth_lock:
        fails = [t for t in _auth_fails.get(key, []) if now - t < AUTH_FAIL_WINDOW]
        _auth_fails[key] = fails
        return len(fails) >= AUTH_FAILS_MAX and now - fails[-1] < AUTH_LOCK


def auth_failed(key, now=None):
    """Record a failure; True when this one closed the door."""
    now = now or time.time()
    with _auth_lock:
        fails = [t for t in _auth_fails.get(key, []) if now - t < AUTH_FAIL_WINDOW]
        fails.append(now)
        _auth_fails[key] = fails
        return len(fails) == AUTH_FAILS_MAX


def auth_succeeded(key):
    with _auth_lock:
        _auth_fails.pop(key, None)


# ---- OIDC sessions and roles (mgr/oidc.py, mgr/sessions.py) ----------------
SID_COOKIE = "kaim56_sid"
BASIC = _oidc.Principal("basic", "basic", "", "admin")   # MANAGER_PASS login, during the migration
# Reachable without a login while OIDC is on; they rate-limit themselves.
PUBLIC_PATHS = ("/auth/login", "/auth/callback", "/auth/logout", "/api/auth/device", "/api/auth/device/poll")


def cookie(header, name):
    try:
        m = http.cookies.SimpleCookie(header or "").get(name)
    except http.cookies.CookieError:
        return ""
    return m.value if m else ""


def session_principal(token, now=None):
    """The Principal behind a session token, or None. Every CHECK_EVERY
    seconds the IdP is asked again (refresh, rotated token, fresh role); a
    refused refresh or a lost role ends the session. An IdP that is merely
    unreachable does not: the session goes on and is checked again later.
    Without a refresh token (client lacks the refresh flow) there is nothing
    to re-check — the session runs to its TTL; admins can still revoke it."""
    now = int(now or time.time())
    s = _sessions.get(token, now)
    if s is None:
        return None
    cached = _oidc.Principal(s["sub"], s["name"], s["email"], s["role"])
    if (not s["refresh"] or now - s["last_check"] <= _sessions.CHECK_EVERY
            or not _sessions.claim_check(s["id"], s["last_check"], now)):
        return cached
    try:
        p, rt = _oidc.refresh(s["refresh"])
    except _oidc.OIDCError as e:
        if str(e).startswith("idp unreachable"):
            print(f"[auth] session {s['id']}: re-check postponed ({e})", flush=True)
            return cached
        p, why = None, str(e)
    else:
        why = "role gone" if p.role is None else "other user" if p.sub != s["sub"] else ""
    if why:
        _sessions.delete(s["id"])
        print(f"[auth] session {s['id']} of {s['name']} ended: {why}", flush=True)
        _audit.access_append(_oidc.Principal(s["sub"], s["name"], s["email"], s["role"]), "", "", 401, event="expired")
        return None
    _sessions.update(s["id"], p, rt)
    return p


# Role "user" = use, not configure: these routes (method, path regex without
# the query string) and nothing else; every other route is admin-only, new
# ones included (default deny).
_N = r"[A-Za-z0-9_-]{1,64}"
_USER_ROUTES = [
    ("GET", r"/(chat|katfs|favicon\.ico|logo\.svg)"),
    ("GET", r"/api/(instances|tasks|tasks-open|chats|notifications|version|resources|usage|usage-by-model"
            r"|changelog|models|personas|skills|agents|prompts|missions|playbooks|history|voice-health|apps"
            r"|stt-recent|stt-recent/audio|auth/me)"),
    ("GET", rf"/api/(session|trace|audit|usage)/{_N}(/log)?"),
    ("GET", r"/api/katfs/(browse|file|status|zip)"),
    ("GET", r"/(katfs|aic|apps)/.*"),
    ("GET", rf"/i/{_N}(/(?!term(/|\Z)).*)?"),
    ("POST", r"/api/(chats|tasks|notifications/read|stt|tts|extract)"),
    ("POST", rf"/api/chat/{_N}"),
    ("POST", rf"/api/instances/{_N}/(start|stop|restart)"),
    ("POST", r"/api/tasks/[a-f0-9]{1,32}/(run|update|delete)"),
    ("POST", rf"/i/{_N}/(?!term(/|\Z)).*"),
    ("POST", rf"/apps/{_N}/_api(/.*)?"),
    ("GET", rf"/api/projects/{_N}/squad/messages"),        # a squad's channel: read and post, not its settings
    ("POST", rf"/api/projects/{_N}/squad/post"),
]
_USER_RX = [(m, re.compile(p + r"\Z")) for m, p in _USER_ROUTES]


def user_allowed(method, path):
    """May a principal with role "user" call this route?"""
    p = urllib.parse.unquote(path.split("?", 1)[0])
    return any(method == m and rx.match(p) for m, rx in _USER_RX)


# Public login endpoints: at most RATE_MAX starts per client per window.
RATE_MAX, RATE_WINDOW = 20, 600
_rate = {}


def auth_rate(key, now=None):
    """Count one call; False when the client is over the limit."""
    now = now or time.time()
    with _auth_lock:
        hits = [t for t in _rate.get(key, []) if now - t < RATE_WINDOW]
        hits.append(now)
        _rate[key] = hits
        return len(hits) <= RATE_MAX
