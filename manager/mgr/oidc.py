# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""OIDC client (security boundary): the manager is the broker between its
users and an external IdP (Rauthy). Web login = authorization code + PKCE,
native clients = device flow (the device_code never leaves the manager), and
the refresh that re-reads a user's role. Off unless OIDC_ISSUER,
OIDC_CLIENT_ID and OIDC_CLIENT_SECRET are all set.

No JWT signature check (stdlib only): the id_token is only read when it comes
straight from the token endpoint over TLS with the client secret (OIDC core
3.1.3.7 allows that), and the identity itself is taken from userinfo.

Part of the mgr package: no import from manager.py.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import namedtuple

ISSUER = os.environ.get("OIDC_ISSUER", "")
CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
SCOPE = "openid profile email"          # Rauthy: `roles` is a claim, not a scope
ROLE_CLAIMS = (("kaim56_admin", "admin"), ("kaim56_user", "user"))
PENDING_TTL, PENDING_MAX = 600, 200     # open web logins / device logins

Principal = namedtuple("Principal", "sub name email role")


class OIDCError(Exception):
    """An IdP answer we cannot use. The text is the OAuth `error` code or a
    short reason — never a token."""


def enabled():
    return bool(ISSUER and CLIENT_ID and CLIENT_SECRET)


def _call(url, form=None, bearer=None):
    """JSON from the IdP (POST when `form` is given)."""
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(url, data=data, headers={"Accept": "application/json"})
    if bearer:
        req.add_header("Authorization", f"Bearer {bearer}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read() or b"{}").get("error") or f"http {e.code}"
        except ValueError:
            err = f"http {e.code}"
        raise OIDCError(str(err)[:80]) from None
    except (OSError, ValueError) as e:
        why = getattr(e, "reason", None) or e           # URLError: the socket/DNS/TLS cause
        raise OIDCError(f"idp unreachable ({type(e).__name__}: {str(why)[:80]})") from None


def _client():
    return {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}


_disc = {"ts": 0.0, "doc": None}


def discovery():
    """The IdP's endpoints, cached for an hour."""
    if _disc["doc"] is None or time.time() - _disc["ts"] > 3600:
        doc = _call(ISSUER.rstrip("/") + "/.well-known/openid-configuration")
        if str(doc.get("issuer", "")).rstrip("/") != ISSUER.rstrip("/"):
            raise OIDCError("issuer mismatch")
        _disc.update(ts=time.time(), doc=doc)
    return _disc["doc"]


def _jwt_claims(tok):
    try:
        part = tok.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        raise OIDCError("bad id_token") from None


def _principal(tok, nonce=None):
    """Principal from a token response: id_token (iss/aud/nonce checked) plus
    userinfo. role None = neither kaim56 role -> the caller refuses."""
    if not tok.get("access_token"):
        raise OIDCError("no access_token")
    claims = {}
    if tok.get("id_token"):
        claims = _jwt_claims(tok["id_token"])
        aud = claims.get("aud")
        if str(claims.get("iss", "")).rstrip("/") != ISSUER.rstrip("/") or CLIENT_ID not in (aud if isinstance(aud, list) else [aud]):
            raise OIDCError("id_token not for us")
        if nonce is not None and not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
            raise OIDCError("nonce mismatch")
    elif nonce is not None:
        raise OIDCError("no id_token")
    info = _call(discovery()["userinfo_endpoint"], bearer=tok["access_token"])
    if not info.get("sub") or (claims and info["sub"] != claims.get("sub")):
        raise OIDCError("sub mismatch")
    claims.update(info)
    roles = claims.get("roles") or []
    role = next((r for c, r in ROLE_CLAIMS if c in roles), None)
    name = claims.get("preferred_username") or claims.get("name") or claims.get("email") or claims["sub"]
    name = "".join(c for c in str(name) if c.isprintable())[:80]     # it is shown to agents and in pages
    return Principal(str(claims["sub"]), name, str(claims.get("email") or "")[:200], role)


# ---- web login: authorization code + PKCE ----------------------------------
_pending, _devices, _lock = {}, {}, threading.Lock()


def _prune(now):
    for d in (_pending, _devices):
        for k in [k for k, v in d.items() if v["exp"] < now]:
            d.pop(k, None)


def login_start(redirect_uri):
    """(authorize URL, state). The state is also the browser-binding cookie."""
    state, verifier, nonce = (secrets.token_urlsafe(32) for _ in range(3))
    now = time.time()
    with _lock:
        _prune(now)
        if len(_pending) >= PENDING_MAX:
            raise OIDCError("too many open logins")
        _pending[state] = {"verifier": verifier, "nonce": nonce, "exp": now + PENDING_TTL}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = urllib.parse.urlencode({
        "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": redirect_uri, "scope": SCOPE,
        "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256"})
    return discovery()["authorization_endpoint"] + "?" + q, state


def login_finish(state, code, redirect_uri):
    """(Principal, refresh_token) for a callback; the state is single-use."""
    with _lock:
        p = _pending.pop(state, None)
    if p is None or p["exp"] < time.time() or not code:
        raise OIDCError("unknown or expired login")
    tok = _call(discovery()["token_endpoint"], {
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "code_verifier": p["verifier"], **_client()})
    return _principal(tok, nonce=p["nonce"]), tok.get("refresh_token", "")


# ---- device flow (APK, CLI, voice) -----------------------------------------
def device_start(device_name):
    """(handle, what the client shows). The device_code stays here."""
    d = _call(discovery()["device_authorization_endpoint"], {"scope": SCOPE, **_client()})
    if not d.get("device_code") or not d.get("user_code"):
        raise OIDCError("bad device answer")
    now, handle = time.time(), secrets.token_urlsafe(32)
    ttl = min(int(d.get("expires_in") or 600), 1800)
    with _lock:
        _prune(now)
        if len(_devices) >= PENDING_MAX:
            raise OIDCError("too many open logins")
        _devices[handle] = {"code": d["device_code"], "name": str(device_name or "")[:80],
                            "exp": now + ttl, "interval": int(d.get("interval") or 5), "next": now}
    return handle, {k: d[k] for k in ("user_code", "verification_uri", "verification_uri_complete", "interval")
                    if k in d} | {"expires_in": ttl}


def device_poll(handle):
    """None while pending; (Principal, refresh_token, device_name) once.
    OIDCError when the handle is unknown, expired, denied."""
    now = time.time()
    with _lock:
        dv = _devices.get(handle)
        if dv is None or dv["exp"] < now:
            _devices.pop(handle, None)
            raise OIDCError("unknown or expired handle")
        if now < dv["next"]:               # the client polls faster than the IdP allows
            return None
        dv["next"] = now + dv["interval"]
    try:
        tok = _call(discovery()["token_endpoint"], {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code", "device_code": dv["code"], **_client()})
    except OIDCError as e:
        if str(e) in ("authorization_pending", "slow_down"):
            if str(e) == "slow_down":
                dv["interval"] += 5
            return None
        with _lock:
            _devices.pop(handle, None)
        raise
    with _lock:
        if _devices.pop(handle, None) is None:   # a parallel poll got it first
            raise OIDCError("unknown or expired handle")
    return _principal(tok), tok.get("refresh_token", ""), dv["name"]


def refresh(refresh_token):
    """(Principal, refresh_token) — rotated when the IdP rotates."""
    if not refresh_token:
        raise OIDCError("no refresh token")
    tok = _call(discovery()["token_endpoint"], {
        "grant_type": "refresh_token", "refresh_token": refresh_token, **_client()})
    return _principal(tok), tok.get("refresh_token") or refresh_token


def end_session_url():
    try:
        return discovery().get("end_session_endpoint") or ""
    except OIDCError:
        return ""
