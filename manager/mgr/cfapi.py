# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Cloudflare Workers API (security boundary): what mgr/cloud.py needs to put an
app on Cloudflare — a port of the upload path of cfdo (github.com/uneidel/cfdo,
f483cd7) to the standard library, so the manager needs no binary for it.

- static assets: the direct-upload session (manifest of path -> hash/size, only
  the buckets Cloudflare does not already have, base64 parts, completion token);
  the hash is Cloudflare's reference one: sha256(base64(data) + extension)[:32];
- the script: one ES module, a Durable Object namespace binding, CFDO_SECRET as
  a secret_text binding, the assets binding; the class migration is derived
  from the tag Cloudflare reports for the script (nothing kept locally);
- workers.dev on, and the account's subdomain for the URL.

Every request carries a User-Agent of its own: Cloudflare's bot protection
answers Python's default one with 403 "error code: 1010".

Part of the mgr package: no import from manager.py.
"""
import base64
import hashlib
import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

API = os.environ.get("CF_API_BASE", "https://api.cloudflare.com/client/v4")
UA = "kAIm56-manager/1.0 (+https://github.com/uneidel/kaim56)"
TIMEOUT = 120
MAX_ASSET = 25 << 20                         # Cloudflare's limit per static file
ASSET_CONFIG = {"html_handling": "auto-trailing-slash", "not_found_handling": "none"}


class CFError(RuntimeError):
    def __init__(self, status, errors, body=""):
        self.status, self.errors = status, errors or []
        msgs = "; ".join(f"{e.get('code')}: {e.get('message')}" for e in self.errors if isinstance(e, dict))
        super().__init__(f"Cloudflare HTTP {status}: {msgs or body[:300]}")


def _q(s):
    return urllib.parse.quote(str(s), safe="")


def request(method, path, token, body=None, ctype=None, sleep=time.sleep):
    """One API call -> its `result`. Retries 429/5xx and network errors (4 tries)."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": UA}
    if ctype:
        headers["Content-Type"] = ctype
    last = None
    for attempt in range(1, 5):
        req = urllib.request.Request(API + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw, status = r.read(), r.status
        except urllib.error.HTTPError as e:
            raw, status = e.read(), e.code
        except (OSError, ValueError) as e:
            last = e
            sleep(2 ** attempt)
            continue
        if status == 429 or status >= 500:
            last = CFError(status, [], raw.decode("utf-8", "replace"))
            sleep(2 ** attempt)
            continue
        try:
            env = json.loads(raw or b"{}")
        except ValueError:
            raise CFError(status, [], raw.decode("utf-8", "replace"))
        if status >= 400 or not env.get("success", False):
            raise CFError(status, env.get("errors"), raw.decode("utf-8", "replace"))
        return env.get("result")
    raise last if isinstance(last, CFError) else CFError(0, [], f"network: {last!r}")


def multipart(parts):
    """[(name, filename|None, content-type, bytes)] -> (body, content-type)."""
    boundary = uuid.uuid4().hex
    out = []
    for name, filename, ct, data in parts:
        disp = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
        out.append(f"--{boundary}\r\nContent-Disposition: {disp}\r\nContent-Type: {ct}\r\n\r\n".encode() + data + b"\r\n")
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


# ---- static assets ---------------------------------------------------------
def asset_hash(data, url_path):
    ext = os.path.splitext(url_path)[1].lstrip(".")
    return hashlib.sha256(base64.b64encode(data) + ext.encode()).hexdigest()[:32]


def scan_assets(root, skip=("tests", "__pycache__")):
    """{"/path": (hash, size, bytes)} — hidden files/dirs and `skip` stay out."""
    out = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x not in skip)
        for f in sorted(files):
            if f.startswith("."):
                continue
            p = os.path.join(d, f)
            if os.path.islink(p):
                continue
            with open(p, "rb") as fh:
                data = fh.read()
            if len(data) > MAX_ASSET:
                raise ValueError(f"{f} is {len(data)} bytes — Cloudflare takes at most {MAX_ASSET} per file")
            url = "/" + os.path.relpath(p, root).replace(os.sep, "/")
            out[url] = (asset_hash(data, url), len(data), data)
    return out


def upload_assets(account, script, token, assets, **kw):
    """The direct-upload flow -> the completion token the script upload carries."""
    manifest = {u: {"hash": h, "size": n} for u, (h, n, _) in assets.items()}
    by_hash = {h: (u, data) for u, (h, _, data) in assets.items()}
    sess = request("POST", f"/accounts/{_q(account)}/workers/scripts/{_q(script)}/assets-upload-session", token,
                   json.dumps({"manifest": manifest}).encode(), "application/json", **kw) or {}
    buckets = sess.get("buckets") or []
    if not buckets:
        return sess.get("jwt", "")                        # everything is stored already
    done = ""
    for bucket in buckets:
        parts = []
        for h in bucket:
            if h not in by_hash:
                raise CFError(0, [], f"the upload session asked for an unknown hash {h}")
            url, data = by_hash[h]
            parts.append((h, h, mimetypes.guess_type(url)[0] or "application/octet-stream", base64.b64encode(data)))
        body, ct = multipart(parts)
        res = request("POST", f"/accounts/{_q(account)}/workers/assets/upload?base64=true", sess["jwt"], body, ct, **kw) or {}
        done = res.get("jwt") or done                    # bucket uploads authenticate with the session token
    if not done:
        raise CFError(0, [], "asset upload finished without a completion token")
    return done


# ---- the script ------------------------------------------------------------
def deployed_tag(account, script, token, **kw):
    """(exists, migration tag Cloudflare has applied)."""
    for s in request("GET", f"/accounts/{_q(account)}/workers/scripts", token, **kw) or []:
        if s.get("id") == script:
            return True, s.get("migration_tag") or ""
    return False, ""


def migration(cls, tag, applied):
    """What the upload must carry: the class is created once (first tag), later
    tags step old -> new, the same tag needs nothing."""
    if applied == tag:
        return None
    m = {"new_tag": tag, "new_sqlite_classes": [] if applied else [cls]}
    if applied:
        m["old_tag"] = applied
    return m


def upload_script(account, script, token, *, module_name, module_src, cls, binding, secret,
                  compat_date, assets_jwt=None, tag="v1", **kw):
    _, applied = deployed_tag(account, script, token, **kw)
    bindings = [{"type": "durable_object_namespace", "name": binding, "class_name": cls},
                {"type": "secret_text", "name": "CFDO_SECRET", "text": secret}]
    meta = {"main_module": module_name, "compatibility_date": compat_date, "bindings": bindings}
    if assets_jwt is not None:
        bindings.append({"type": "assets", "name": "ASSETS"})
        meta["assets"] = {"jwt": assets_jwt, "config": ASSET_CONFIG}
    mig = migration(cls, tag, applied)
    if mig:
        meta["migrations"] = mig
    body, ct = multipart([("metadata", None, "application/json", json.dumps(meta).encode()),
                          (module_name, module_name, "application/javascript+module", module_src.encode())])
    return request("PUT", f"/accounts/{_q(account)}/workers/scripts/{_q(script)}", token, body, ct, **kw)


def enable_workers_dev(account, script, token, **kw):
    request("POST", f"/accounts/{_q(account)}/workers/scripts/{_q(script)}/subdomain", token,
            json.dumps({"enabled": True, "previews_enabled": True}).encode(), "application/json", **kw)


def workers_dev_subdomain(account, token, **kw):
    return (request("GET", f"/accounts/{_q(account)}/workers/subdomain", token, **kw) or {}).get("subdomain", "")
