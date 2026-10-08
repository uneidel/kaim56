# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""HTTP routes of the OIDC login (mgr/oidc.py): web login and logout, the
device flow for native clients, who-am-I, and the admin's session list.
All 404 while OIDC is off. The /auth/* and /api/auth/device* routes are
reachable without a login (mgr/auth.PUBLIC_PATHS); lockout and a per-client
rate limit guard them. admin=True everywhere: no guest VM reaches them.

Part of the mgr package: no import from manager.py.
"""
import hmac
import html
import json

from mgr import audit as _audit
from mgr import auth as _auth
from mgr import oidc as _oidc
from mgr import routes as _routes
from mgr import sessions as _sessions
from mgr import settings as _settings

LOGIN_COOKIE = "kaim56_login"      # binds the callback to the browser that started the login


def _redirect_uri():
    return f"https://{_settings.PUBLIC_HOST}/auth/callback"


def _key(h):
    return _auth.auth_client_key(h.client_address[0], h.headers.get("X-Forwarded-For", ""))


def _redirect(h, url, cookies=()):
    h.send_response(302)
    h.send_header("Location", url)
    for c in cookies:
        h.send_header("Set-Cookie", c)
    h.send_header("Content-Length", "0")
    h.end_headers()


def _page(h, code, text, cookies=(), refresh=""):
    body = ("<!doctype html><meta charset=utf-8><title>kAIm56</title>"
            + (f'<meta http-equiv="refresh" content="0;url={refresh}">' if refresh else "")
            + f"<p style='font-family:sans-serif'>{text}</p>").encode()
    h.send_response(code)
    h.send_header("Content-Type", "text/html; charset=utf-8")
    h.send_header("Cache-Control", "no-store")
    for c in cookies:
        h.send_header("Set-Cookie", c)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def _off(h):
    if _oidc.enabled():
        return False
    h._json({"error": "not found"}, 404)
    return True


@_routes.ROUTER.get("/auth/login", admin=True)
def _rt_login(h):
    if _off(h):
        return
    # The callback goes to the public name; the binding cookie must live there too.
    if (h.headers.get("Host") or "").split(":")[0].lower() != _settings.PUBLIC_HOST.lower():
        return _redirect(h, f"https://{_settings.PUBLIC_HOST}/auth/login")
    if not _auth.auth_rate(_key(h)):
        return h._json({"error": "too many login attempts, try again later"}, 429)
    try:
        url, state = _oidc.login_start(_redirect_uri())
    except _oidc.OIDCError as e:
        return _page(h, 502, f"Login unavailable ({e}).")
    _redirect(h, url, [f"{LOGIN_COOKIE}={state}; Path=/auth/callback; Max-Age={_oidc.PENDING_TTL}; "
                       "HttpOnly; Secure; SameSite=Lax"])


@_routes.ROUTER.get("/auth/callback", admin=True)
def _rt_callback(h):
    if _off(h):
        return
    q = _routes._qs(h)
    state, code = q.get("state", [""])[0], q.get("code", [""])[0]
    bound = _auth.cookie(h.headers.get("Cookie", ""), LOGIN_COOKIE)
    clear = f"{LOGIN_COOKIE}=; Path=/auth/callback; Max-Age=0; HttpOnly; Secure; SameSite=Lax"
    if q.get("error") or not state or not bound or not hmac.compare_digest(state.encode(), bound.encode()):
        _auth.auth_failed(_key(h))
        return _page(h, 400, "Login failed or expired. <a href=/auth/login>Try again</a>.", [clear])
    try:
        p, rt = _oidc.login_finish(state, code, _redirect_uri())
    except _oidc.OIDCError as e:
        _auth.auth_failed(_key(h))
        print(f"[auth] web login failed: {e}", flush=True)
        return _page(h, 400, "Login failed. <a href=/auth/login>Try again</a>.", [clear])
    if p.role is None:
        _audit.access_append(p, "GET", "/auth/callback", 403, event="refused")
        return _page(h, 403, f"{html.escape(p.name)} has no kAIm56 role.", [clear])
    tok = _sessions.create(p, "web", rt)
    _auth.auth_succeeded(_key(h))
    _audit.access_append(p, "GET", "/auth/callback", 200, event="login")
    # A page, not a 302: a SameSite=Strict cookie set in a redirect chain that
    # started at the IdP is not sent on that chain's next hop (login loop).
    _page(h, 200, "Logged in. <a href=/>Continue</a>.", refresh="/", cookies=[
        clear, f"{_auth.SID_COOKIE}={tok}; Path=/; Max-Age={_sessions.TTL['web']}; HttpOnly; Secure; SameSite=Strict"])


@_routes.ROUTER.get("/auth/logout", admin=True)
def _rt_logout(h):
    if _off(h):
        return
    tok = _auth.cookie(h.headers.get("Cookie", ""), _auth.SID_COOKIE)
    s = _sessions.get(tok) if tok else None
    if s is not None:
        _sessions.delete(s["id"])
        _audit.access_append(_oidc.Principal(s["sub"], s["name"], s["email"], s["role"]),
                             "GET", "/auth/logout", 200, event="logout")
    cookie = f"{_auth.SID_COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict"
    end = _oidc.end_session_url()
    if end:
        return _redirect(h, end, [cookie])
    _page(h, 200, "Logged out. <a href=/auth/login>Log in</a>.", [cookie])


@_routes.ROUTER.post("/api/auth/device", admin=True)
def _rt_device_start(h):
    # {"device_name"} -> {handle, user_code, verification_uri(_complete), interval, expires_in}
    if _off(h):
        return
    if not _auth.auth_rate(_key(h)):
        return h._json({"error": "too many login attempts, try again later"}, 429)
    try:
        handle, show = _oidc.device_start(str((h._body() or {}).get("device_name") or ""))
    except _oidc.OIDCError as e:
        return h._json({"error": f"login unavailable: {e}"}, 502)
    return h._json({"handle": handle, **show})


@_routes.ROUTER.post("/api/auth/device/poll", admin=True)
def _rt_device_poll(h):
    # {"handle"} -> {"status": "pending"} | {"status": "ok", token, name, role} (once)
    if _off(h):
        return
    try:
        got = _oidc.device_poll(str((h._body() or {}).get("handle") or ""))
    except _oidc.OIDCError as e:
        _auth.auth_failed(_key(h))          # guessing handles runs into the lockout
        return h._json({"error": str(e)}, 400)
    if got is None:
        return h._json({"status": "pending"})
    p, rt, device = got
    if p.role is None:
        _audit.access_append(p, "POST", "/api/auth/device/poll", 403, event="refused")
        return h._json({"error": f"{p.name} has no kAIm56 role"}, 403)
    tok = _sessions.create(p, "device", rt, device)
    _audit.access_append(p, "POST", "/api/auth/device/poll", 200, event="login")
    return h._json({"status": "ok", "token": tok, "name": p.name, "role": p.role,
                    "expires_in": _sessions.TTL["device"]})


@_routes.ROUTER.get("/api/auth/me", admin=True)
def _rt_me(h):
    p = getattr(h, "principal", None)
    person = p is not None and p.sub != _auth.BASIC.sub
    return h._json({"oidc": _oidc.enabled(), "name": p.name if person else "", "role": p.role if p else "admin"})


@_routes.ROUTER.get("/api/auth/sessions", admin=True)
def _rt_sessions(h):
    if _off(h):
        return
    return json.dumps({"sessions": _sessions.list_all()}).encode(), "application/json"


@_routes.ROUTER.post("/api/auth/sessions/revoke", admin=True)
def _rt_revoke(h):
    # {"id"} -> the session is gone; its next request is a 401. Logged by do_POST.
    if _off(h):
        return
    try:
        ok = _sessions.delete(int((h._body() or {}).get("id")))
    except (TypeError, ValueError):
        return h._json({"error": "id missing"}, 400)
    return h._json({"ok": ok}, 200 if ok else 404)
