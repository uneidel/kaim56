# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""HTTP routes of the deploy target "instance" (mgr/deploy.py), admin only:

- ``GET /api/deploy/`` — every service VM's status; ``GET /api/deploy/<project>`` — spec, state, release, the console log.
- ``POST /api/deploy/<project>`` {spec} saves the spec; {secrets: {NAME: value}} sets them
  (an empty value removes one; reads show the names only); {action: deploy | stop | remove}.
- ``GET|POST /d/<project>/…`` — the service itself, only on the apps' own origin
  (settings.APPS_HOST): a browser on the manager's host is sent there, never
  served here (the service's pages must not run with the manager's session).

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
from mgr import auth as _auth
from mgr import deploy as _deploy
from mgr import projects as _projects
from mgr import routes as _routes
from mgr import settings as _settings

_API, _SVC = "/api/deploy/", "/d/"


def _who(h):
    pr = getattr(h, "principal", None)
    return pr.name if pr is not None and pr.sub != "basic" else "admin"


def _project(h, prefix):
    name, _, rest = h.path.split("?", 1)[0][len(prefix):].partition("/")
    return name, rest


@_routes.ROUTER.get(_API, prefix=True, admin=True)
def _rt_deploy_get(h):
    name, _ = _project(h, _API)
    if not name:
        return h._json({"services": _deploy.services()})
    if _projects.get(name) is None:
        return h._json({"error": "unknown project"}, 404)
    return h._json({"project": name, **_deploy.status(name), "log": _deploy.log(name)})


@_routes.ROUTER.post(_API, prefix=True, admin=True)
def _rt_deploy_post(h):
    name, _ = _project(h, _API)
    if _projects.get(name) is None:
        return h._json({"error": "unknown project"}, 404)
    b = h._body() or {}
    act = str(b.get("action") or "")
    if "secrets" in b and not act:
        names, why = _deploy.save_secrets(name, b["secrets"])
        return h._json({"ok": True, "secrets": names}) if names is not None else h._json({"error": why}, 400)
    if "spec" in b and not act:
        s, why = _deploy.save_spec(name, b["spec"])
        return h._json({"ok": True, "spec": s}) if s else h._json({"error": why}, 400)
    if act == "deploy":
        msg, why = _deploy.deploy(name, _who(h))
        return h._json({"msg": msg}) if msg else h._json({"error": why}, 400)
    if act == "stop":
        return h._json({"msg": f"{name}: {_deploy.stop(name)}"})
    if act == "remove":
        _deploy.remove(name)
        return h._json({"msg": f"{name}: service, release images and data removed"})
    return h._json({"error": "spec, secrets, or action deploy | stop | remove"}, 400)


def _service(h, method):
    name, rest = _project(h, _SVC)
    if not _auth.side_host(h.headers):            # never on the manager's own origin
        if not _settings.APPS_HOST:
            return h._json({"error": "a deployed service is served on the apps' host: set APPS_HOST (site.json)"}, 404)
        if method != "GET":
            return h._json({"error": f"services run on https://{_settings.APPS_HOST}"}, 403)
        h.send_response(302); h.send_header("Location", f"https://{_settings.APPS_HOST}{h.path}"); h.end_headers()
        return None
    if not _auth.is_apps_host(h.headers):
        return h._json({"error": "not found"}, 404)
    if name and not h.path.split("?", 1)[0][len(_SVC) + len(name):].startswith("/"):
        h.send_response(302); h.send_header("Location", f"{_SVC}{name}/"); h.end_headers()
        return None
    if _projects.get(name) is None:
        return h._json({"error": "unknown project"}, 404)
    q = h.path.split("?", 1)[1] if "?" in h.path else ""
    body = h._raw(_deploy.MAX_BODY) if method == "POST" else b""
    st, hdrs, data = _deploy.forward(method, name, rest + ("?" + q if q else ""), dict(h.headers.items()), body)
    host = h.headers.get("Host", "")
    own = f"https://{host}{_SVC}{name}/"
    h.send_response(st)
    for k, v in hdrs:
        h.send_header(k, v)
    # the apps' origin also serves the manager API the apps use: the service's pages may only talk to themselves
    h.send_header("Content-Security-Policy", f"connect-src {own}; form-action {own}; worker-src 'none'; "
                                             "frame-ancestors 'none'; base-uri 'none'; object-src 'none'")
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)
    return None


@_routes.ROUTER.get(_SVC, prefix=True, admin=True)
def _rt_service_get(h):
    return _service(h, "GET")


@_routes.ROUTER.post(_SVC, prefix=True, admin=True)
def _rt_service_post(h):
    return _service(h, "POST")
