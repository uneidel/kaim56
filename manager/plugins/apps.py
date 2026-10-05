# kAIm56 tool plugin: the operator's browser apps on celld (and their Cloudflare side).
# Needs MANAGE_APPS on this instance (read | write | full) — see mgr/guestgrants.py.
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
import socket
import urllib.error
import urllib.request

DESC = ("Control the kAIm56 browser apps (hosted on celld, optionally moved to Cloudflare). "
        "action: list (all apps, where they run, server-side summary) | get (an app's server-side "
        "state: path under its _api, e.g. 'changes' or 'export') | post (write to its _api: path + body) | "
        "upload (move app + state to Cloudflare) | restore (back to celld) | rename (to) | delete. "
        "Needs MANAGE_APPS on this instance: read (list/get), write (+post/upload/restore/rename), full (+delete).")

PARAMS = {
    "action": {"type": "string", "enum": ["list", "get", "post", "upload", "restore", "rename", "delete"],
               "description": "what to do"},
    "app": {"type": "string", "description": "app name (folder), e.g. corewar"},
    "path": {"type": "string", "description": "get/post: path under the app's _api, e.g. 'changes'"},
    "body": {"type": "object", "description": "post: JSON body"},
    "to": {"type": "string", "description": "rename: the new name"},
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


def _call(method, path, body=None, timeout=60):
    req = urllib.request.Request(_base() + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")[:300]
        if e.code == 403:
            return None, ("forbidden — this instance needs MANAGE_APPS "
                          f"({'full' if 'delete' in path else 'write' if method == 'POST' else 'read'}) "
                          "in its config (manager: Instances → config)")
        return None, f"HTTP {e.code}: {raw}"
    except OSError as e:
        return None, f"manager not reachable: {e}"
    try:
        return json.loads(raw), ""
    except ValueError:
        return raw, ""


def run(action, app="", path="", body=None, to="", **_):
    if action != "list" and not app:
        return "error: 'app' is required"
    if action == "list":
        d, err = _call("GET", "/api/apps")
        if err:
            return "error: " + err
        lines = []
        for a in d.get("apps", []):
            cl = a.get("cloud") or {}
            where = a.get("served_by", "")
            url = cl.get("url", "") if where == "cloudflare" else f"/apps/{a['name']}/"
            side = (a.get("server_side") or {}).get("text", "") if isinstance(a.get("server_side"), dict) else ""
            job = a.get("job") or {}
            run_ = f" [{job.get('op')}: {job.get('step')}]" if job.get("done") is False else ""
            lines.append(f"- {a['name']} ({a.get('title', '')}): {where}{run_} {url}" + (f" — {side}" if side else ""))
        return "\n".join(lines) or "no apps"
    if action in ("get", "post"):
        p = "/apps/" + app + "/_api/" + path.lstrip("/")
        d, err = _call("GET" if action == "get" else "POST", p, body if action == "post" else None)
        return ("error: " + err) if err else json.dumps(d, ensure_ascii=False)[:20000]
    if action in ("upload", "restore", "delete", "rename"):
        d, err = _call("POST", f"/api/apps/{app}/{action}", {"to": to} if action == "rename" else {}, timeout=300)
        if err:
            return "error: " + err
        if isinstance(d, dict) and d.get("ok") is False:
            return "error: " + str(d.get("error", d))
        if action in ("upload", "restore"):
            return (f"{action} of {app} started in the background — check with action=list "
                    "(the app shows the running step until it is done)")
        return json.dumps(d, ensure_ascii=False)
    return f"error: unknown action {action!r}"
