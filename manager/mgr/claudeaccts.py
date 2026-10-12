# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Claude subscription accounts (security boundary): several logins next to the host's, one per instance (CLAUDE_ACCOUNT), and the usage gauges (5 h / week) polled for each.

An account is a long-lived token from ``claude setup-token`` (what a VM gets,
as CLAUDE_CODE_OAUTH_TOKEN) plus, optionally, a full OAuth login (the
~/.claude/.credentials.json of a login made for kAIm56) used only to read the
usage — the manager owns that one and refreshes it. The pseudo-account
``default`` is the host login (secrets.CLAUDE_CRED_SRC): read only, never
stored, never refreshed — it belongs to the operator's own Claude Code, and a
refresh would rotate its refresh token and log that session out.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request

from mgr import audit as _audit
from mgr import paths as _paths
from mgr import secrets as _secrets

ACCTS_FILE = os.path.join(_paths.BASE, "claude_accounts.json")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
# Claude Code's own refresh (cli 2.1.x: TOKEN_URL, CLIENT_ID, JSON body).
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
UA = "kaim56"
POLL_EVERY = 300
_lock = threading.Lock()            # read-modify-write of the store
_busy = threading.Lock()            # one poll at a time
_last = [0.0]                       # last poll (epoch s)
USAGE = {}                          # {name: {five_hour, seven_day, checked, error}}


def _load():
    try:
        with open(ACCTS_FILE) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d):
    """Atomically, mode 0600 (the tokens are in it)."""
    fd, tmp = tempfile.mkstemp(prefix=".claude_accounts.", dir=os.path.dirname(ACCTS_FILE))
    with os.fdopen(fd, "w") as fh:
        json.dump(d, fh, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, ACCTS_FILE)


def _host_oauth():
    try:
        with open(_secrets.CLAUDE_CRED_SRC) as fh:
            return json.load(fh).get("claudeAiOauth") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def list_public():
    """Names and what is set — never a secret value."""
    host = bool(_host_oauth().get("accessToken"))
    out = [{"name": "default", "label": "host login", "has_token": host, "has_creds": host, "readonly": True}]
    for n, a in sorted(_load().items()):
        out.append({"name": n, "label": a.get("label", ""), "has_token": bool(a.get("token")),
                    "has_creds": bool(a.get("creds")), "added": a.get("added", 0), "by": a.get("by", "")})
    return out


def add(name, label="", token="", creds_json="", by="admin"):
    """Add or update an account; an empty token/creds keeps the stored one."""
    name = str(name or "").strip()
    if not NAME_RE.match(name) or name == "default":
        return "invalid name (a-z, 0-9, -; up to 32 chars; not 'default')"
    token = str(token or "").strip()
    if token and (re.search(r"\s", token) or len(token) > 4096):
        return "token must be one word without spaces or newlines"
    creds = None
    if str(creds_json or "").strip():
        try:
            creds = json.loads(creds_json).get("claudeAiOauth")
        except (ValueError, AttributeError):
            creds = None
        if not (isinstance(creds, dict) and isinstance(creds.get("accessToken"), str) and creds["accessToken"]):
            return "credentials JSON needs claudeAiOauth.accessToken"
    with _lock:
        d = _load()
        old = d.get(name) or {}
        if not token and not old.get("token"):
            return "token required (claude setup-token)"
        d[name] = {"label": str(label or old.get("label", "")).strip().replace("\n", " ")[:64],
                   "token": token or old["token"], "creds": creds or old.get("creds"),
                   "added": old.get("added") or int(time.time()), "by": old.get("by") or by}
        _save(d)
    _last[0] = 0.0                                  # gauges at the next idle tick
    _audit.audit_append("admin", "claude_account_set", name, True)
    return f"{name} {'updated' if old else 'added'}"


def remove(name):
    name = str(name or "").strip()
    if name == "default":
        return "default is the host login; it cannot be removed"
    with _lock:
        d = _load()
        if name not in d:
            return f"{name} unknown"
        del d[name]
        _save(d)
    USAGE.pop(name, None)
    _audit.audit_append("admin", "claude_account_remove", name, True)
    return f"{name} removed"


def token_for(name):
    return (_load().get(str(name or "")) or {}).get("token") or ""


# ---- usage --------------------------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _open(req):
    return urllib.request.build_opener(_NoRedirect).open(req, timeout=15)


def _refresh(name, oauth):
    """New access token for a MANAGER-OWNED login; stored back. None on failure."""
    body = {"grant_type": "refresh_token", "refresh_token": oauth["refreshToken"], "client_id": CLIENT_ID}
    if oauth.get("scopes"):
        body["scope"] = " ".join(oauth["scopes"])
    req = urllib.request.Request(TOKEN_URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    try:
        r = json.load(_open(req))
        new = {**oauth, "accessToken": r["access_token"], "refreshToken": r.get("refresh_token") or oauth["refreshToken"],
               "expiresAt": int((time.time() + int(r.get("expires_in") or 3600)) * 1000)}
    except (OSError, ValueError, KeyError, TypeError):
        return None
    with _lock:
        d = _load()
        if name in d:
            d[name]["creds"] = new
            _save(d)
    return new


def _credential(name, acct, now):
    """(access token, '') or ('', why) — which credential reads this account's usage."""
    if name == "default":                                  # read only: never refreshed here
        o = _host_oauth()
        if not o.get("accessToken"):
            return "", "no host login"
        return ("", "expired") if (o.get("expiresAt") or 0) / 1000 < now else (o["accessToken"], "")
    o = acct.get("creds") or {}
    if o.get("accessToken"):
        if (o.get("expiresAt") or 0) / 1000 > now + 60:
            return o["accessToken"], ""
        if o.get("refreshToken"):
            o = _refresh(name, o)
            if o:
                return o["accessToken"], ""
    if acct.get("token"):
        return acct["token"], ""
    return "", "expired"


def _fetch(tok):
    req = urllib.request.Request(USAGE_URL, headers={"Authorization": f"Bearer {tok}",
                                                     "anthropic-beta": "oauth-2025-04-20", "User-Agent": UA})
    try:
        d = json.load(_open(req))
    except urllib.error.HTTPError as e:
        return {"error": f"no usage access for this token ({e.code})" if e.code in (401, 403) else f"http {e.code}"}
    except (OSError, ValueError):
        return {"error": "network"}
    out = {"error": ""}
    for k in ("five_hour", "seven_day"):
        w = d.get(k) if isinstance(d, dict) else None
        if isinstance(w, dict):
            try:
                out[k] = {"pct": float(w.get("utilization") or 0), "resets_at": str(w.get("resets_at") or "")}
            except (TypeError, ValueError):
                pass
    return out


def poll_usage(now=None):
    """Refresh the cache for every account, at most every POLL_EVERY seconds."""
    now = time.time() if now is None else now
    if now - _last[0] < POLL_EVERY:
        return USAGE
    _last[0] = now
    accts = {"default": {}, **_load()}
    for name, acct in accts.items():
        tok, why = _credential(name, acct, now)
        res = _fetch(tok) if tok else {"error": why}
        USAGE[name] = {**res, "checked": int(now)}
    for gone in set(USAGE) - set(accts):
        USAGE.pop(gone, None)
    return USAGE


def poll_usage_bg():
    """From the idle loop: the poll in a thread (network, 15 s timeouts) when due."""
    if time.time() - _last[0] < POLL_EVERY or not _busy.acquire(blocking=False):
        return

    def run():
        try:
            poll_usage()
        except Exception as e:
            print(f"[quiet] claude usage poll: {e!r}", flush=True)
        finally:
            _busy.release()
    threading.Thread(target=run, daemon=True).start()
