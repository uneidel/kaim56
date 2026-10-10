# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""The HTTP handler: authentication (Basic-auth or OIDC sessions with roles, guests by their source IP), the guest allow and deny lists, dispatch through the route table (mgr/routes), the admin page as fallback, and the small helpers every route uses (_json, _body, _raw, _guest).

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
from http.server import BaseHTTPRequestHandler
import base64
import hmac
import json
import urllib.parse

from mgr import audit as _audit
from mgr import auth as _auth
from mgr import cloud as _cloud
from mgr import guestproxy as _guestproxy
from mgr import guests as _guests
from mgr import guestgrants as _grants
from mgr import irohgw as _irohgw
from mgr import llmproxy as _llmproxy
from mgr import oidc as _oidc
from mgr import routes as _routes
from mgr import ui as _ui
from mgr import util as _util


class H(_guestproxy.GuestProxyMixin, _llmproxy.LLMProxyMixin, BaseHTTPRequestHandler):
    # Reading a request (headers, body) may not hang a thread for ever; the
    # tunnel and the long-polls lift this per socket / wait server-side.
    timeout = 120
    iroh_node = ""          # the iroh device behind this connection (parse_request)

    def parse_request(self):
        # iroh-gw opens one connection per app request and puts "PROXY IROH
        # <node-id>" before the HTTP: the device is then the lockout key, not
        # the gateway's 127.0.0.1. Untrusted, malformed, not first on the
        # connection or no longer allow-listed: the connection is dropped.
        first, self._seen = not getattr(self, "_seen", False), True
        if self.raw_requestline.startswith(b"PROXY "):
            node = _auth.iroh_node(self.client_address[0], self.raw_requestline) if first else None
            if node is None or node not in {a["id"].lower() for a in _irohgw.load_allow()}:
                print(f"[auth] {self.client_address[0]}: refused iroh header {self.raw_requestline[:90]!r}", flush=True)
                self.close_connection = True
                return False
            self.iroh_node = node
            self.raw_requestline = self.rfile.readline(65537)
            if len(self.raw_requestline) > 65536:
                self.close_connection = True
                return False
        return super().parse_request()

    # Protection layer: an unhandled exception in a route must NOT tear the
    # connection down hard (the agent would otherwise see "RemoteDisconnected").
    # If no header has been sent yet, we respond cleanly with HTTP 500; otherwise
    # the response is just ended. The error lands in the journal.
    def send_response(self, code, message=None):
        self._status = code
        return super().send_response(code, message)

    def end_headers(self):
        self._sent = True
        self.send_header("X-Content-Type-Options", "nosniff")
        if _auth.is_index_host(getattr(self, "headers", None)):    # the code dashboard: never framed, no Referer out
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
        else:
            self.send_header("X-Frame-Options", "SAMEORIGIN")
        return super().end_headers()

    def _fail500(self):
        import traceback
        tb = traceback.format_exc()
        print(f"[http] unhandled in {self.command} {self.path}:\n{tb}", flush=True)
        if getattr(self, "_sent", False):
            return
        try:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"internal server error"}')
        except Exception:
            pass

    def do_GET(self):
        self._sent = False
        try:
            self._do_GET()
        except Exception:
            self._fail500()

    def do_POST(self):
        self._sent = False
        try:
            self._do_POST()
            p = getattr(self, "principal", None)
            if p is not None:                # a person changed something: the access log
                _audit.access_append(p, "POST", self.path, getattr(self, "_status", 0))
        except _util.BodyTooLarge as e:
            self.close_connection = True          # the body was never read
            if not getattr(self, "_sent", False):
                self._json({"error": f"body too large ({e} bytes)"}, 413)
        except Exception:
            self._fail500()

    def _forbid(self):
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":"forbidden"}')

    def _auth(self):
        # Who is asking lands in self.principal (mgr/oidc.Principal): an OIDC
        # session, or BASIC for the MANAGER_PASS login. None for guests, app
        # tokens, host services and the open (no password, no OIDC) manager.
        self.principal = None
        if _auth.is_index_host(self.headers):
            return self._auth_index()
        oidc = _oidc.enabled()
        # Guests (VMs) carry no credentials: they are identified by source IP
        # and gated by the guest allow/deny lists. Without this exemption a set
        # MANAGER_PASS would lock every agent out of its own manager.
        if (not _auth.PW and not oidc) or _guests.instance_by_ip(self.client_address[0]) is not None:
            return True
        # Host services (containers on the docker bridge, e.g. Hindsight) may
        # use the key proxy without a login — only that path, only from there.
        if self.path.startswith("/api/llm/") and self.client_address[0].startswith("172.17."):
            return True
        key = _auth.auth_client_key(self.client_address[0], self.headers.get("X-Forwarded-For", ""), self.iroh_node)
        if _auth.auth_locked(key):
            self._send(b'{"error":"too many failed logins, try again later"}', "application/json", 429)
            return False
        hdr = self.headers.get("Authorization", "")
        # An app on Cloudflare calls the API with its own token (mgr/cloud.py):
        # only while it is in the cloud, only for the API paths apps use.
        if hdr.startswith("Bearer ") and _cloud.token_app(hdr[7:].strip(), self.command, self.path):
            _auth.auth_succeeded(key)
            return True
        if oidc:
            if self.path.split("?", 1)[0] in _auth.PUBLIC_PATHS:
                return True                  # the login itself (rate-limited in mgr/routes_auth.py)
            tok = hdr[7:].strip() if hdr.startswith("Bearer ") else \
                _auth.cookie(self.headers.get("Cookie", ""), _auth.SID_COOKIE)
            if tok:
                self.principal = _auth.session_principal(tok)
                if self.principal is not None:
                    _auth.auth_succeeded(key)
                    return True
        if hdr.startswith("Basic ") and _auth.PW:
            try:
                u, p = base64.b64decode(hdr[6:]).decode().split(":", 1)
                if hmac.compare_digest(u.encode(), str(_auth.USER).encode()) and \
                        hmac.compare_digest(p.encode(), str(_auth.PW).encode()):
                    _auth.auth_succeeded(key)
                    self.principal = _auth.BASIC if oidc else None
                    return True
            except Exception:
                pass
            if _auth.auth_failed(key):
                print(f"[auth] {key}: {_auth.AUTH_FAILS_MAX} failed logins, locked for {_auth.AUTH_LOCK // 60} min", flush=True)
        # A browser asking for a page goes to the OIDC login; API clients get 401.
        if oidc and self.command == "GET" and not self.path.startswith("/api/"):
            self.send_response(302)
            self.send_header("Location", "/auth/login")
            self.end_headers()
            return False
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="kAIm56"' if _auth.PW else 'Bearer realm="kAIm56"')
        self.end_headers()
        return False

    def _auth_index(self):
        """INDEX_HOST, the code dashboard's own origin: its paths only
        (mgr/auth.index_path_allowed, else 404) and its own "dash" session
        cookie only — no guest by IP, no Basic, no Bearer, no manager session."""
        if not _auth.index_path_allowed(self.path) or not _oidc.enabled():
            self._json({"error": "not found"}, 404)
            return False
        key = _auth.auth_client_key(self.client_address[0], self.headers.get("X-Forwarded-For", ""), self.iroh_node)
        if _auth.auth_locked(key):
            self._send(b'{"error":"too many failed logins, try again later"}', "application/json", 429)
            return False
        if self.path.split("?", 1)[0] in _auth.INDEX_AUTH_PATHS:
            return True                      # the login itself (rate-limited in mgr/routes_auth.py)
        tok = _auth.cookie(self.headers.get("Cookie", ""), _auth.DASH_COOKIE)
        self.principal = _auth.session_principal(tok, dash=True) if tok else None
        if self.principal is not None:
            _auth.auth_succeeded(key)
            return True
        if self.command == "GET":            # back here after the login (routes_auth: next)
            self.send_response(302)
            self.send_header("Location", "/auth/login?next=" + urllib.parse.quote(self.path, safe=""))
            self.end_headers()
            return False
        self.send_response(401)
        self.end_headers()
        return False

    def log_message(self, *a):
        pass

    def _do_GET(self):
        if not self._auth():
            return
        if self._dispatch("GET"):
            return
        # No route: the admin UI for everything else (index, deep links).
        # Guests get nothing here; unknown API paths a clean 404.
        if _guests.instance_by_ip(self.client_address[0]) is not None or _auth.is_index_host(self.headers):
            return self._forbid()
        if self.path.split("?", 1)[0].startswith("/api/"):
            return self._json({"error": "not found"}, 404)
        body = _ui.render().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        # Never cache: a stale manager page after an update produces ghost
        # errors (old JS logic against a new API).
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _do_POST(self):
        if not self._auth():
            return
        p = self.path.split("?", 1)[0]
        guest = _guests.instance_by_ip(self.client_address[0])
        idx = _auth.is_index_host(self.headers)       # there: same-origin only (mgr/auth.origin_allowed)
        if (guest is None or idx) and not _auth.origin_allowed(self.headers.get("Origin", ""), idx):
            return self._json({"error": "cross-site request refused"}, 403)
        if guest is not None and not (
                p in _guests.GUEST_POST_PATHS or p.startswith(_guests.GUEST_POST_PREFIXES)
                or _grants.allowed(guest, "POST", p)):             # MANAGE_APPS (mgr/guestgrants.py)
            return self._forbid()
        if self._dispatch("POST"):
            return
        self._json({"msg": "unknown"}, 404)

    def _dispatch(self, method):
        """Route-table lookup. True when a route answered (or was forbidden)."""
        hit = _routes.ROUTER.resolve(method, self.path)
        if hit is None:
            return False
        fn, admin_only = hit
        guest = _guests.instance_by_ip(self.client_address[0]) if admin_only else None
        if guest is not None and not _grants.allowed(guest, method, self.path):
            self._forbid()
            return True
        p = getattr(self, "principal", None)
        if p is not None and p.role != "admin" and not _auth.user_allowed(method, self.path):
            self._forbid()                   # role "user": only the routes in mgr/auth._USER_ROUTES
            return True
        out = fn(self)
        if out is not None:
            body, ct = out
            self._send(body, ct)
        return True

    # ---- small helpers every route uses ------------------------------------
    def _send(self, body, ct="application/json", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj, ensure_ascii=False).encode(), "application/json", code)

    def _raw(self, limit=None):
        """Request body, capped (BODY_MAX unless the route says otherwise);
        a missing or bad length is an empty body. Over the cap: BodyTooLarge,
        answered 413 by do_POST without reading a byte of it."""
        try:
            ln = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            ln = 0
        if ln > (limit or _routes.BODY_MAX):
            raise _util.BodyTooLarge(ln)
        return self.rfile.read(ln) if ln > 0 else b""

    def _body(self, default=None):
        """JSON body; {} (or `default`) when empty or malformed."""
        raw = self._raw()
        if not raw:
            return {} if default is None else default
        try:
            return json.loads(raw)
        except ValueError:
            return {} if default is None else default

    def _guest(self):
        return _guests.instance_by_ip(self.client_address[0])
