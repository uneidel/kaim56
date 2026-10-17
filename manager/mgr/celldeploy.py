# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""celld in production mode: the apps and the Worker go to an S3 bucket.

install.sh --with-celld runs SeaweedFS (container kaim56-s3, S3 on loopback)
and celld with --bucket (container kaim56-celld), both on the docker network
kaim56-apps, and writes ENV_FILE (root-only): the bucket's keys, CELLD_BUCKET,
S3_ENDPOINT (as celld sees it) and S3_HOST_ENDPOINT (as the manager sees it).

sync() deploys when something changed: the celld project (worker.js,
wrangler.json, do/) plus every app under APPS_DIR as its static assets —
staged into a temp folder, then `celld deploy` in a throwaway container. The
running celld adopts the new version at its next poll, without a restart.
Without ENV_FILE nothing happens (celld dev mounts apps/ directly).

Part of the mgr package: no import from manager.py.
"""
import datetime
import hashlib
import hmac
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

from mgr import apps as _apps
from mgr import paths as _paths

ENV_FILE = os.path.join(_paths.BASE, "celld.env")
IMAGE = "kaim56-celld"
NETWORK = "kaim56-apps"
DEPLOY_TIMEOUT = 180
MIN_INTERVAL = 30                     # the idle worker checks at most this often
PROJECT_FILES = ("worker.js", "wrangler.json", "do")
_lock = threading.Lock()
_state = {"fingerprint": "", "checked": 0.0,
          "last": {"ok": None, "ts": 0, "version": "", "error": ""}}


def load_env():
    """{key: value} of ENV_FILE, {} when celld runs in dev mode."""
    try:
        with open(ENV_FILE, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return {}
    return dict(l.split("=", 1) for l in lines if "=" in l and not l.lstrip().startswith("#"))


def project_dir():
    return os.path.join(os.path.dirname(_apps.apps_dir()), "celld")


def _walk(root, skip_dot=True):
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not (skip_dot and x.startswith(".")))
        for f in sorted(files):
            if not (skip_dot and f.startswith(".")):
                yield os.path.join(d, f)


def fingerprint():
    """Changes when a deployed file changes (path, size, mtime)."""
    h = hashlib.sha256()
    proj, ad = project_dir(), _apps.apps_dir()
    paths = [p for x in PROJECT_FILES for p in ([os.path.join(proj, x)] if os.path.isfile(os.path.join(proj, x))
                                               else _walk(os.path.join(proj, x)))]
    paths += list(_walk(ad)) if ad and os.path.isdir(ad) else []
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        h.update(f"{p}\0{st.st_size}\0{st.st_mtime_ns}\0".encode())
    return h.hexdigest()


def _stage(dst):
    """The celld project with the apps as public/apps (no dotfiles, no .trash)."""
    proj = project_dir()
    for x in PROJECT_FILES:
        src = os.path.join(proj, x)
        (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, os.path.join(dst, x))
    shutil.copytree(_apps.apps_dir(), os.path.join(dst, "public", "apps"),
                    ignore=lambda d, names: [n for n in names if n.startswith(".")])


def _sign(method, url, ak, sk, region="us-east-1"):
    """SigV4 headers for a body-less S3 request."""
    host, path = url.split("//", 1)[1].split("/", 1)
    amz = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ph = hashlib.sha256(b"").hexdigest()
    canon = (f"{method}\n/{path}\n\nhost:{host}\nx-amz-content-sha256:{ph}\nx-amz-date:{amz}\n\n"
             f"host;x-amz-content-sha256;x-amz-date\n{ph}")
    scope = f"{amz[:8]}/{region}/s3/aws4_request"
    k = ("AWS4" + sk).encode()
    for x in (amz[:8], region, "s3", "aws4_request"):
        k = hmac.new(k, x.encode(), hashlib.sha256).digest()
    sig = hmac.new(k, f"AWS4-HMAC-SHA256\n{amz}\n{scope}\n{hashlib.sha256(canon.encode()).hexdigest()}".encode(),
                   hashlib.sha256).hexdigest()
    return {"x-amz-date": amz, "x-amz-content-sha256": ph,
            "Authorization": f"AWS4-HMAC-SHA256 Credential={ak}/{scope}, "
                             f"SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature={sig}"}


def ensure_bucket(env):
    """Create the bucket (CELLD_BUCKET) if it is missing; True when it is there."""
    name = env.get("CELLD_BUCKET", "").removeprefix("s3://").split("/", 1)[0]
    url = f"{env.get('S3_HOST_ENDPOINT', '').rstrip('/')}/{name}"
    region = env.get("AWS_REGION") or "us-east-1"
    req = urllib.request.Request(url, method="PUT", headers=_sign("PUT", url, env.get("AWS_ACCESS_KEY_ID", ""),
                                                                   env.get("AWS_SECRET_ACCESS_KEY", ""), region))
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except urllib.error.HTTPError as e:
        if e.code != 409:                      # 409 BucketAlreadyOwnedByYou / Exists
            raise
    return True


def deploy():
    """Stage and `celld deploy` -> the last-result dict."""
    env = load_env()
    run = os.path.join(_paths.BASE, "run")             # not /tmp: the docker daemon resolves the path on the host
    os.makedirs(run, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="celld-deploy-", dir=run)
    try:
        ensure_bucket(env)
        _stage(tmp)
        r = subprocess.run(["docker", "run", "--rm", "--network", NETWORK, "--env-file", ENV_FILE,
                            "-v", f"{tmp}:/project", "-w", "/project", IMAGE, "deploy", "/project"],
                           capture_output=True, text=True, timeout=DEPLOY_TIMEOUT, check=False)
        out = (r.stdout + r.stderr).strip()
        ver = next((l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("Current Version ID:")), "")
        res = {"ok": r.returncode == 0, "ts": int(time.time()), "version": ver,
               "error": "" if r.returncode == 0 else out[-400:]}
    except (OSError, ValueError, subprocess.SubprocessError, urllib.error.URLError) as e:
        res = {"ok": False, "ts": int(time.time()), "version": "", "error": repr(e)[:400]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return res


def sync(force=False):
    """Deploy when something changed since the last good deploy -> the result,
    or None (dev mode, nothing changed, too soon, or a deploy is running)."""
    if not os.path.isfile(ENV_FILE) or not _apps.apps_dir():
        return None
    now = time.time()
    if not force and now - _state["checked"] < MIN_INTERVAL:
        return None
    if not _lock.acquire(blocking=False):
        return None
    try:
        _state["checked"] = now
        fp = fingerprint()
        if fp == _state["fingerprint"] and not force:
            return None
        res = deploy()
        _state["last"] = res
        if res["ok"]:
            _state["fingerprint"] = fp
        else:
            print(f"[celld] deploy failed: {res['error']}", flush=True)
        return res
    finally:
        _lock.release()


def sync_bg():
    """sync(force=True) in a thread: right after a publish, rename or delete."""
    threading.Thread(target=sync, args=(True,), daemon=True, name="celld-deploy").start()


def status():
    """{mode: dev|bucket, last: {ok, ts, version, error}}."""
    return {"mode": "bucket" if os.path.isfile(ENV_FILE) else "dev", "last": dict(_state["last"])}
