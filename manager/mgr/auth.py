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
from mgr import cloud as _cloud
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
    return _trusted_cache["hosts"] - {_settings.INDEX_HOST, _settings.APPS_HOST}   # side origins never are


# Side origins: hosts that serve one part of the manager on an origin of its
# own, so their JavaScript never runs with the manager's session. Each has
# its own login (mgr/routes_auth.py), session kind and host-only cookie, and
# answers on its own paths only (mgr/httpd._auth_side).
#   index (INDEX_HOST): the repowise code dashboard (mgr/indexer.py), admins only
#   apps  (APPS_HOST):  the browser apps /apps/<name>/ and the API they use
#                       (mgr/cloud.APP_TOKEN_PATHS); admins and role "user"
SIDE_KIND = {"index": "dash", "apps": "apps"}           # side -> its session kind
SIDE_COOKIE = {"index": "__Host-kaim56_dash", "apps": "__Host-kaim56_apps"}   # host-only (the prefix forbids Domain=)


def side_host_name(side):
    return {"index": _settings.INDEX_HOST, "apps": _settings.APPS_HOST}.get(side, "")


def side_host(headers):
    """Which side origin this request is for ("index", "apps"), or "" (the manager)."""
    host = ((headers.get("Host") if headers is not None else "") or "").split(":")[0].strip().lower()
    for side in SIDE_KIND:
        if host and host == side_host_name(side):
            return side
    return ""


def is_index_host(headers):
    """Is this request for the code dashboard's own origin (settings.INDEX_HOST)?"""
    return side_host(headers) == "index"


def is_apps_host(headers):
    """Is this request for the apps' own origin (settings.APPS_HOST)?"""
    return side_host(headers) == "apps"


# On a side host nothing but its own paths and its login (mgr/httpd.py).
INDEX_AUTH_PATHS = ("/auth/login", "/auth/callback", "/auth/logout")


def index_path_allowed(path):
    p = path.split("?", 1)[0]
    return p in INDEX_AUTH_PATHS or p.startswith("/indexer/open/") or p == "/indexer/ui" or p.startswith("/indexer/ui/")


def apps_path_allowed(method, path):
    """APPS_HOST: the login, the apps (files and _api) and the manager API the
    apps call — the list an app's Cloudflare token gets (mgr/cloud.py), never
    settings writes, secrets, terminals or the admin page."""
    p = path.split("?", 1)[0]
    return (p in INDEX_AUTH_PATHS or p.startswith("/apps/")
            or any(p == x or p.startswith(x) for x in _cloud.APP_TOKEN_PATHS.get(method, ())))


def side_path_allowed(side, method, path):
    return apps_path_allowed(method, path) if side == "apps" else index_path_allowed(path)


def origin_allowed(origin, side_host=""):
    """CSRF guard for state-changing requests. Browsers send Origin on every
    POST; the app, the desktop client and curl do not (empty = fine). A page
    on another site — or a DNS-rebound name — carries a foreign Origin and is
    refused, so it cannot export a folder into a VM or create an instance.
    On a side host (side_host = its name) only its own Origin passes, none missing."""
    o = (origin or "").strip()
    if not o:
        return not side_host
    try:
        host = (urllib.parse.urlsplit(o).hostname or "").lower()
    except ValueError:
        return False
    if side_host:
        return host == side_host
    return bool(host) and host in trusted_hosts()

USER = os.environ.get("MANAGER_USER", "admin")
PW = os.environ.get("MANAGER_PASS", "")   # empty => no auth (only behind Traefik!)

# Failed logins per client: after AUTH_FAILS_MAX within AUTH_FAIL_WINDOW the
# client is refused for AUTH_LOCK seconds, right password or not.
AUTH_FAILS_MAX, AUTH_FAIL_WINDOW, AUTH_LOCK = 10, 900, 900
_auth_fails, _auth_lock = {}, threading.Lock()
_PROXY_PEERS = ("127.0.0.1", "::1", "172.17.")


def auth_client_key(peer, xff="", node=""):
    """Who is knocking: the iroh node-id the gateway vouched for (iroh_node), the
    socket peer — or, when that is a proxy on this host (Traefik on
    loopback/docker), the first X-Forwarded-For hop."""
    if node:
        return "iroh:" + node
    if xff and (peer in _PROXY_PEERS or peer.startswith(_PROXY_PEERS[2])):
        return xff.split(",")[0].strip() or peer
    return peer


_IROH_LINE = re.compile(rb"PROXY IROH ([0-9a-f]{64})\r?\n\Z")


def iroh_node(peer, line):
    """The node-id in iroh-gw's per-connection header ("PROXY IROH <id>"), or
    None. iroh-gw relays raw TCP from loopback, one connection per iroh stream,
    so the line is believed from loopback only (iroh-gw/src/main.rs)."""
    m = _IROH_LINE.match(line) if peer in ("127.0.0.1", "::1") else None
    return m.group(1).decode() if m else None


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
DASH_COOKIE = SIDE_COOKIE["index"]
BASIC = _oidc.Principal("basic", "basic", "", "admin")   # MANAGER_PASS login, during the migration
# Reachable without a login while OIDC is on; they rate-limit themselves.
PUBLIC_PATHS = ("/auth/login", "/auth/callback", "/auth/logout", "/api/auth/device", "/api/auth/device/poll")


def cookie(header, name):
    try:
        m = http.cookies.SimpleCookie(header or "").get(name)
    except http.cookies.CookieError:
        return ""
    return m.value if m else ""


def session_principal(token, now=None, kind=""):
    """The Principal behind a session token, or None. Every CHECK_EVERY
    seconds the IdP is asked again (refresh, rotated token, fresh role); a
    refused refresh or a lost role ends the session. An IdP that is merely
    unreachable does not: the session goes on and is checked again later.
    Without a refresh token (client lacks the refresh flow) there is nothing
    to re-check — the session runs to its TTL; admins can still revoke it.
    A side host's session ("dash" on INDEX_HOST, "apps" on APPS_HOST) is valid
    there only (kind=that kind), and only it: no token crosses between a side
    origin and the manager (kind="": web and device sessions)."""
    now = int(now or time.time())
    s = _sessions.get(token, now)
    if s is None or (s["kind"] if s["kind"] in SIDE_KIND.values() else "") != kind:
        return None
    cached = _oidc.Principal(s["sub"], s["name"], s["email"], s["role"])
    if (not s["refresh"] or now - s["last_check"] <= _sessions.CHECK_EVERY
            or not _sessions.claim_check(s["id"], s["last_check"], now)):
        return cached
    return _recheck(s, cached)


def _recheck(s, cached):
    """Refresh one session at the IdP: the new Principal, the cached one when
    the IdP is merely unreachable, or None (session deleted) when it refuses."""
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
        _audit.access_append(cached, "", "", 401, event="expired")
        return None
    _sessions.update(s["id"], p, rt)
    return p


KEEP_EVERY = 6 * 3600


def keep_sessions(now=None):
    """Idle sessions keep their IdP refresh token alive: one refresh every
    KEEP_EVERY. Without it a browser closed for longer than the IdP's refresh
    lifetime came back to a dead token and a new login, although the kAIm56
    session (30 days) was still valid. Revocation still bites here."""
    now = int(now or time.time())
    for s in _sessions.due_for_check(now - KEEP_EVERY):
        if _sessions.claim_check(s["id"], s["last_check"], now):
            _recheck(s, _oidc.Principal(s["sub"], s["name"], s["email"], s["role"]))


# Role "user" = use, not configure: these routes (method, path regex without
# the query string) and nothing else; every other route is admin-only, new
# ones included (default deny).
_N = r"[A-Za-z0-9_-]{1,64}"
_USER_ROUTES = [
    ("GET", r"/(chat|katfs|favicon\.ico|logo\.svg)"),
    ("GET", r"/api/(instances|tasks|tasks-open|chats|notifications|version|resources|usage|usage-by-model"
            r"|models|personas|skills|agents|prompts|missions|playbooks|history|voice-health|apps"
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
    ("GET", rf"/api/projects/{_N}/squad/(messages|board|notes)"),  # a squad's channel, proposals/tickets, notes:
                                                            # read and post — not its settings, decisions, ticket edits
    ("POST", rf"/api/projects/{_N}/squad/(post|notes|notes/send|notes/\d{{1,12}}/delete)"),  # notes: mgr/squadnotes.py
    ("GET", r"/api/squads"),                                # the Squads page's list — not the kickoff
    ("GET", rf"/api/squads/{_N}/metrics/(events|summary)"), # squad metrics (mgr/squadmetrics.py): read
    ("GET", r"/api/waiting"),                               # what waits for the operator: read, never decide
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
