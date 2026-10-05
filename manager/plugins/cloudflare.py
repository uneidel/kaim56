# kAIm56 tool plugin: Workers on the operator's Cloudflare account.
# Needs MANAGE_APPS on this instance (read | full) — see mgr/guestgrants.py.
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
import socket
import urllib.error
import urllib.request

DESC = ("Cloudflare Workers of the kAIm56 operator. action: list (every Worker on the account; "
        "'app' = the local app it belongs to, empty = created outside the manager) | export (all "
        "Durable Objects of a cfdo worker as JSON; secret = that worker's CFDO_SECRET, empty = the "
        "default secret) | delete (a worker created outside the manager, with its data — cannot be undone). "
        "Needs MANAGE_APPS on this instance: read (list/export), full (+delete).")

PARAMS = {
    "action": {"type": "string", "enum": ["list", "export", "delete"], "description": "what to do"},
    "worker": {"type": "string", "description": "export/delete: the worker's name"},
    "secret": {"type": "string", "description": "export: the worker's CFDO_SECRET (optional)"},
    "refresh": {"type": "boolean", "description": "list: skip the 60 s cache"},
}
REQUIRED = ["action"]


def _base():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    finally:
        s.close()
    return f"http://{ip.rsplit('.', 1)[0]}.1:8700"


def _call(method, path, body=None, timeout=120):
    req = urllib.request.Request(_base() + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}"), ""
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return None, ("forbidden — this instance needs MANAGE_APPS "
                          f"({'full' if path.endswith('/delete') else 'read'}) in its config")
        try:
            return None, json.loads(e.read()).get("error", f"HTTP {e.code}")
        except ValueError:
            return None, f"HTTP {e.code}"
    except (OSError, ValueError) as e:
        return None, f"manager not reachable: {e}"


def run(action, worker="", secret="", refresh=False, **_):
    if action == "list":
        d, err = _call("GET", "/api/apps/cloudflare" + ("?refresh=1" if refresh else ""))
        if err:
            return "error: " + err
        if not d.get("configured", True):
            return "Cloudflare is not set up on the manager (Settings: CF_ACCOUNT_ID, CF_API_TOKEN, CF_APP_SECRET)"
        out = [f"- {w['name']}: {w.get('url') or '(no workers.dev URL)'}"
               + (f" — app {w['app']}" if w.get("app") else " — created outside the manager")
               + (f", updated {w.get('modified', '')[:10]}" if w.get("modified") else "")
               for w in d.get("workers", [])]
        return "\n".join(out + ([f"(Cloudflare: {d['error']})"] if d.get("error") else [])) or "no workers"
    if not worker:
        return "error: 'worker' is required"
    if action == "export":
        d, err = _call("POST", f"/api/cfworkers/{worker}/export", {"secret": secret}, timeout=300)
        if err:
            return "error: " + err
        d.pop("ok", None)
        return json.dumps(d, ensure_ascii=False)[:50000]
    if action == "delete":
        d, err = _call("POST", f"/api/cfworkers/{worker}/delete", {})
        return ("error: " + err) if err else f"worker {worker} deleted"
    return f"error: unknown action {action!r}"
