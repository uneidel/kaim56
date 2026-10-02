# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Apps to Cloudflare and back (security boundary).

An app normally runs on celld (mgr/apps.py). "Upload" turns it into a
Cloudflare Worker through the Workers API (mgr/cfapi.py, cfdo's upload path
ported to the standard library):

- the app's files as static assets, its Durable Object class (celld/do/<app>.js
  plus do/storage.js, assembled into one module), a router;
- the router asks every request for a login: admin + the default secret
  (CF_APP_SECRET, bound as CFDO_SECRET) — Cloudflare serves the static
  files before the worker, so the files themselves are public, the data is not;
- the app's /api/... calls go to this manager with a per-app token (in the
  worker's code): Bearer, revocable here, valid
  only while the app is in the cloud and only for the API paths apps use
  (APP_TOKEN_PATHS) — never the admin password, never terminals or settings;
- the state moves: export from celld, import at Cloudflare (replace), counts
  compared. Then the app is "cloud": /apps/<app>/ redirects there and its
  local _api refuses writes, so there is one copy that changes.

"Restore" copies the state back (Cloudflare -> celld, replace), revokes the
token and makes the app local again; the worker stays deployed (unused).

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request

from mgr import apps as _apps
from mgr import cfapi as _cfapi
from mgr import paths as _paths
from mgr import settings as _settings

CLOUD_FILE = os.path.join(_paths.BASE, "apps_cloud.json")
HTTP_TIMEOUT = 30
READY_WAIT = 240                                        # a new workers.dev name needs TLS first
COMPAT_DATE = "2026-09-01"
BINDING = "APP_DO"
MIGRATION_TAG = "v1"
WORKER_URL = "https://{script}.{sub}.workers.dev"        # https only: the default secret travels as Basic
# What an app's token may call on the manager — the API the apps use, nothing
# that administers the platform itself (settings, secrets, update, terminals).
APP_TOKEN_PATHS = {
    "GET": ("/api/instances", "/api/tasks", "/api/settings", "/api/personas", "/api/skills",
            "/api/playbooks", "/api/openrouter-models", "/api/workspace/", "/api/checkout/"),
    "POST": ("/api/create", "/api/instances/", "/api/checkout/", "/api/tasks", "/api/playbook-add",
             "/api/playbook-remove", "/api/chat/"),
}
_lock = threading.Lock()
_jobs = {}                                              # app -> {"op", "step", "error", "started", "done"}


# ---- state -----------------------------------------------------------------
def load():
    try:
        with open(CLOUD_FILE) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save(d):
    tmp = CLOUD_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(d, fh, indent=1)
    os.replace(tmp, CLOUD_FILE)


def where(app):
    """("cloud", url) or ("local", "")."""
    e = load().get(app) or {}
    return ("cloud", e.get("url", "")) if e.get("where") == "cloud" else ("local", "")


def creds():
    """(account, token, secret) from Settings, or raises ValueError naming what is missing."""
    st = _settings.load_settings()
    vals = [(st.get(k) or "").strip() for k in ("CF_ACCOUNT_ID", "CF_API_TOKEN", "CF_APP_SECRET")]
    missing = [k for k, v in zip(("CF_ACCOUNT_ID", "CF_API_TOKEN", "CF_APP_SECRET"), vals) if not v]
    if missing:
        raise ValueError("Cloudflare is not set up — fill in Settings: " + ", ".join(missing))
    return tuple(vals)


# ---- the app's token on the manager ----------------------------------------
def _sha(token):
    return hashlib.sha256(token.encode()).hexdigest()


def token_app(token, method, path):
    """The app this Bearer token belongs to, if it is in the cloud and the path
    is one apps may call — else None (then the request needs the admin login)."""
    if not token or len(token) > 200:
        return None
    path = path.split("?", 1)[0]
    if not any(path == p or path.startswith(p) for p in APP_TOKEN_PATHS.get(method, ())):
        return None
    want = _sha(token)
    for app, e in load().items():
        if e.get("where") == "cloud" and e.get("token_sha") and hmac.compare_digest(e["token_sha"], want):
            return app
    return None


# ---- the Cloudflare project ------------------------------------------------
def script_name(app):
    return f"kaim56-{app}"


def celld_dir():
    return os.path.join(os.path.dirname(_apps.apps_dir()), "celld")


def do_class(app):
    """(class name, module source) of the app's Durable Object, or (None, '')."""
    p = os.path.join(celld_dir(), "do", f"{app}.js")
    if not os.path.isfile(p):
        return None, ""
    with open(p, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"^export class (\w+) extends DurableObject", src, re.M)
    return (m.group(1), src) if m else (None, "")


def _strip_module(src):
    """A module's body for the single-file worker: its imports go (the worker
    imports DurableObject once and inlines storage.js), helpers lose 'export'."""
    out = []
    for line in src.splitlines():
        if line.startswith("import "):
            continue
        if line.startswith("export async function ") or line.startswith("export function "):
            line = line[len("export "):]
        out.append(line)
    return "\n".join(out)


def worker_source(app, token, manager_url):
    cls, mod = do_class(app)
    with open(os.path.join(celld_dir(), "do", "storage.js"), encoding="utf-8") as fh:
        storage = fh.read()
    body = (_strip_module(storage) + "\n\n" + _strip_module(mod)) if cls else \
        "export class AppIdle extends DurableObject {}   // the app keeps no state; the binding needs a class"
    return f'''// Generated by kAIm56 (mgr/cloud.py) for the app "{app}" — do not edit, re-upload instead.
import {{ DurableObject }} from "cloudflare:workers";

const KAIM_APP = {json.dumps(app)};
const KAIM_MANAGER = {json.dumps(manager_url.rstrip("/"))};
const KAIM_TOKEN = {json.dumps(token)};   // this app's manager token: revocable there, API paths only

{body}

function kaimSame(a, b) {{
  const x = new TextEncoder().encode(a), y = new TextEncoder().encode(b);
  let d = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) d |= (x[i] | 0) ^ (y[i] | 0);
  return d === 0;
}}
function kaimLoggedIn(request, env) {{
  const h = request.headers.get("Authorization") || "";
  if (!env.CFDO_SECRET || !h.startsWith("Basic ")) return false;
  let dec = "";
  try {{ dec = atob(h.slice(6)); }} catch {{ return false; }}
  const i = dec.indexOf(":");
  return i > 0 && kaimSame(dec.slice(0, i), "admin") & kaimSame(dec.slice(i + 1), env.CFDO_SECRET);
}}

export default {{
  async fetch(request, env) {{
    if (!kaimLoggedIn(request, env)) {{
      return new Response("login required", {{ status: 401,
        headers: {{ "WWW-Authenticate": 'Basic realm="kAIm56 app", charset="UTF-8"' }} }});
    }}
    const url = new URL(request.url);
    if (url.pathname === "/_api" || url.pathname.startsWith("/_api/")) {{   // the app's own state
      const stub = env.{BINDING}.get(env.{BINDING}.idFromName(KAIM_APP));
      return stub.fetch(new Request(new URL(url.pathname.slice(5) || "/", url), request));
    }}
    if (url.pathname.startsWith("/api/")) {{                                 // the platform, with this app's token
      const headers = new Headers({{ Authorization: "Bearer " + KAIM_TOKEN }});
      const ct = request.headers.get("Content-Type");
      if (ct) headers.set("Content-Type", ct);
      const r = await fetch(KAIM_MANAGER + url.pathname + url.search, {{
        method: request.method, headers,
        body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body }});
      return new Response(r.body, {{ status: r.status,
        headers: {{ "Content-Type": r.headers.get("Content-Type") || "application/json", "Cache-Control": "no-store" }} }});
    }}
    if (url.pathname === "/chat") return Response.redirect(KAIM_MANAGER + "/chat", 302);
    return env.ASSETS.fetch(request);
  }},
}};
'''


def deploy(app, token, account, cf_token, secret):
    """Put the app on Cloudflare (assets, the worker, workers.dev) -> its URL."""
    cls, _ = do_class(app)
    assets = _cfapi.scan_assets(os.path.join(_apps.apps_dir(), app))
    _step(app, f"uploading {len(assets)} files")
    jwt = _cfapi.upload_assets(account, script_name(app), cf_token, assets)
    _step(app, "uploading the worker")
    _cfapi.upload_script(account, script_name(app), cf_token, module_name="worker.mjs",
                         module_src=worker_source(app, token, f"https://{_settings.PUBLIC_HOST}"),
                         cls=cls or "AppIdle", binding=BINDING, secret=secret, compat_date=COMPAT_DATE,
                         assets_jwt=jwt, tag=MIGRATION_TAG)
    _cfapi.enable_workers_dev(account, script_name(app), cf_token)
    sub = _cfapi.workers_dev_subdomain(account, cf_token)
    if not sub:
        raise RuntimeError("the Cloudflare account has no workers.dev subdomain yet — open Workers & Pages "
                           "in the dashboard once (or set one), then upload again")
    return WORKER_URL.format(script=script_name(app), sub=sub)


# ---- moving the state ------------------------------------------------------
def _basic(secret):
    return "Basic " + base64.b64encode(f"admin:{secret}".encode()).decode()


def _call(url, method="GET", body=None, auth=None, timeout=HTTP_TIMEOUT):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "User-Agent": _cfapi.UA}   # Python's own UA: 403 "1010"
    if auth:
        headers["Authorization"] = auth
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def export_local(app):
    return _call(f"{_apps.celld_url()}/apps/{app}/_api/export").get("entries") or []


def import_local(app, entries):
    return _call(f"{_apps.celld_url()}/apps/{app}/_api/import", "POST", {"entries": entries, "replace": True})


def export_cloud(url, secret):
    return _call(f"{url}/_api/export", auth=_basic(secret)).get("entries") or []


def import_cloud(url, secret, entries):
    return _call(f"{url}/_api/import", "POST", {"entries": entries, "replace": True}, auth=_basic(secret))


def wait_ready(url, secret, sleep=time.sleep, deadline=READY_WAIT):
    """A fresh workers.dev name answers only once its certificate exists."""
    t0, last = time.monotonic(), ""
    while time.monotonic() - t0 < deadline:
        try:
            _call(f"{url}/_api/export", auth=_basic(secret), timeout=15)
            return ""
        except urllib.error.HTTPError as e:
            if e.code == 404:                                  # an app without state: reachable is enough
                return ""
            last = f"HTTP {e.code}"
        except (OSError, ValueError) as e:
            last = repr(e)[:160]
        sleep(10)
    return f"the worker did not answer within {deadline} s ({last})"


# ---- the two operations (background jobs) ----------------------------------
def _step(app, text):
    with _lock:
        _jobs[app]["step"] = text


def upload(app):
    """Upload the app and move its state to Cloudflare. Raises with the reason."""
    account, cf_token, secret = creds()
    if where(app)[0] == "cloud":
        raise ValueError(f"{app} is already on Cloudflare")
    has_state = do_class(app)[0] is not None
    if has_state and not _apps.celld_url():
        raise ValueError("the app keeps state on celld, but CELLD_URL is not set")
    token = secrets.token_urlsafe(32)
    url = deploy(app, token, account, cf_token, secret)
    _step(app, "waiting for the worker to answer")
    why = wait_ready(url, secret)
    if why:
        raise RuntimeError(why)
    if has_state:
        _step(app, "copying the state to Cloudflare")
        entries = export_local(app)
        r = import_cloud(url, secret, entries)
        if r.get("total") != len(entries):
            raise RuntimeError(f"state copy incomplete: {r} for {len(entries)} entries")
    with _lock:
        d = load()
        d[app] = {"where": "cloud", "url": url, "script": script_name(app), "token_sha": _sha(token),
                  "since": int(time.time())}
        _save(d)
    return url


def restore(app):
    """Copy the state back to celld and make the app local; the worker stays."""
    _, _, secret = creds()
    state, url = where(app)
    if state != "cloud":
        raise ValueError(f"{app} is not on Cloudflare")
    if do_class(app)[0] is not None:
        if not _apps.celld_url():
            raise ValueError("CELLD_URL is not set — nowhere to restore the state to")
        _step(app, "copying the state back to celld")
        entries = export_cloud(url, secret)
        r = import_local(app, entries)
        if r.get("total") != len(entries):
            raise RuntimeError(f"state copy incomplete: {r} for {len(entries)} entries")
    with _lock:
        d = load()
        d[app] = {**d.get(app, {}), "where": "local", "token_sha": "", "restored": int(time.time())}
        _save(d)
    return "local"


OPS = {"upload": upload, "restore": restore}


def start(app, op):
    """Run upload/restore in the background -> (ok, job)."""
    if op not in OPS:
        return False, {"error": "unknown operation"}
    if not any(a["name"] == app for a in _apps.load_apps()):
        return False, {"error": "unknown app"}
    with _lock:
        if (_jobs.get(app) or {}).get("done") is False:
            return False, {"error": "an operation on this app is still running", **_jobs[app]}
        _jobs[app] = {"op": op, "step": "starting", "error": "", "started": int(time.time()), "done": False}

    def run():
        try:
            OPS[op](app)
            res = {"step": "done", "error": ""}
        except Exception as e:                        # reported in the Apps tab, nothing half-switched
            res = {"step": "failed", "error": str(e)[:600]}
        with _lock:
            _jobs[app].update(res, done=True)
    threading.Thread(target=run, daemon=True).start()
    return True, dict(_jobs[app])


def job(app):
    with _lock:
        return dict(_jobs.get(app) or {})
