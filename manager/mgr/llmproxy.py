# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""LLM key proxy (security boundary): the agents call /api/llm/<backend> and the manager adds the key, so no LLM key ever enters a VM; plus the per-instance budget and rate guard and the usage booking of proxied calls.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import os
import secrets
import threading
import time
import urllib.request

from mgr import notify as _notify
from mgr import store as _store
from mgr import guests as _guests
from mgr import hindsight as _hindsight
from mgr import router as _router
from mgr import routes as _routes
from mgr import settings as _settings


# Credential injection gateway (OneCLI pattern): the agent sends its chat
# requests to /api/llm/<backend>/chat/completions instead of directly to the
# router; when forwarding, the manager appends the Authorization header from
# the settings. This way the LLM keys NEVER leave the host: a compromised VM
# can at most call models through the manager (visible, throttleable), but
# cannot exfiltrate a key and reuse it outside the system.
LLM_PROXY_UPSTREAMS = {
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY"),
    "orcarouter": ("https://api.orcarouter.ai/v1/chat/completions", "ORCAROUTER_API_KEY"),
}

# ---- Guardrails: budget + rate limit for LLM calls --------------------------
# Enforcement at the key injection proxy: all of the VMs' router calls pass
# through there. Budget per instance and day (tokens, from llm_usage) and a
# frequency cap per minute. Override per instance via config: BUDGET_TOKENS
# (0 = off), LLM_RATE_MIN. On exceedance: 429 + at most one notify per hour.
GUARD_BUDGET_TOKENS = int(os.environ.get("GUARD_BUDGET_TOKENS", "5000000"))
GUARD_LLM_RATE_MIN = int(os.environ.get("GUARD_LLM_RATE_MIN", "60"))
_guard_lock = threading.Lock()
_guard_calls = {}          # instance -> [timestamps]
_guard_notified = {}       # instance -> ts of the last budget notify


def budget_of(inst):
    """The instance's daily token budget: BUDGET_TOKENS from its config (read
    per request, so a change applies at once), else the default; 0 = off."""
    try:
        return int((inst.get("config") or {}).get("BUDGET_TOKENS", GUARD_BUDGET_TOKENS))
    except (TypeError, ValueError):
        return GUARD_BUDGET_TOKENS


def used_today(name):
    """Tokens (in + out) the instance used since local midnight."""
    midnight = int(time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1)))
    u = _store.usage_for(name, midnight)
    return (u.get("in") or 0) + (u.get("out") or 0)


def _guard_check(inst):
    """(allowed, reason). inst = instance dict or None (admin/host: always ok)."""
    if inst is None:
        return True, ""
    name = inst["name"]
    cfg = inst.get("config") or {}
    now = time.time()
    # 1) Frequency per minute
    try:
        rate = int(cfg.get("LLM_RATE_MIN", GUARD_LLM_RATE_MIN))
    except ValueError:
        rate = GUARD_LLM_RATE_MIN
    with _guard_lock:
        lst = _guard_calls.setdefault(name, [])
        lst[:] = [t for t in lst if now - t < 60]
        if rate > 0 and len(lst) >= rate:
            return False, f"rate limit: {rate} LLM calls/min reached"
        lst.append(now)
    # 2) Daily budget (tokens since local midnight)
    budget = budget_of(inst)
    if budget > 0:
        used = used_today(name)
        if used >= budget:
            with _guard_lock:
                last = _guard_notified.get(name, 0)
                fire = now - last > 3600
                if fire:
                    _guard_notified[name] = now
            if fire:
                try:
                    _notify.notify_add("guardrail", f"Budget reached: {name}",
                               f"{used:,} tokens today (limit {budget:,}). LLM calls "
                               f"pause until midnight — or raise the budget in the "
                               f"Policy tab (applies at once).", link="policy")
                except Exception:
                    pass
            return False, f"budget: {used:,}/{budget:,} tokens used today"
    return True, ""



def _proxy_usage(inst, backend, raw, ms=None, turn="", step=None):
    """Book the tokens the UPSTREAM reports for this guest: the budget guard
    must not rest on what the agent chooses to tell us via /api/usage. The
    span (turn, step from the request headers, duration) rides along."""
    if inst is None:
        return
    raw = raw.strip()
    if raw.startswith(b"data:"):
        raw = raw[5:]
    try:
        j = json.loads(raw)
        u = j.get("usage") or {}
        if isinstance(u, dict) and (u.get("prompt_tokens") or u.get("completion_tokens")):
            _store.usage_add(inst["name"], j.get("model") or backend, u.get("prompt_tokens"),
                      u.get("completion_tokens"), u.get("cost"), turn=turn, ms=ms, step=step)
    except (ValueError, AttributeError, TypeError):
        pass


def _routed_upstream(target, st):
    """(url, key) for a router target, or (None, None) when it cannot be served
    (cloud key missing): then the instance's own model stays."""
    if target["backend"] == "llama":
        base = (target.get("upstream") or st.get("LLAMA_ENDPOINT") or "").strip().rstrip("/")
        if not base:
            return None, None
        return (base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")), ""
    if target["backend"] not in LLM_PROXY_UPSTREAMS:
        return None, None
    url, keyname = LLM_PROXY_UPSTREAMS[target["backend"]]
    if target["backend"] == "orcarouter" and (st.get("ORCAROUTER_URL") or "").strip():
        u = st["ORCAROUTER_URL"].strip().rstrip("/")
        url = u if u.endswith("/chat/completions") else u + ("/chat/completions" if u.endswith("/v1") else "/v1/chat/completions")
    key = (st.get(keyname) or "").strip()
    return (url, key) if key else (None, None)


# ---- max_tokens cap ----------------------------------------------------------
# A runaway generation (seen on the local 27B: it ran until the 10-min timeout)
# ends at LLM_MAX_TOKENS. Enforced here, server-side, on every call through the
# proxy (all backends, incl. a routed llama turn): a VM cannot lift it. The
# instance key wins over the settings default; neither = no cap.


def max_tokens_of(inst, st):
    """The effective cap for this caller: instance config, else settings; 0 = none."""
    for v in (((inst or {}).get("config") or {}).get("LLM_MAX_TOKENS"), st.get("LLM_MAX_TOKENS")):
        if v not in ("", None) and not _settings.max_tokens_error(v):
            return int(str(v).strip())
    return 0


def _clamp_max_tokens(body, cap):
    """Clamp max_tokens / max_completion_tokens to cap, add max_tokens when
    neither is there. True when the body changed."""
    if cap <= 0 or not isinstance(body, dict):
        return False
    changed, present = False, False
    for k in ("max_tokens", "max_completion_tokens"):
        if k in body:
            present = True
            v = body[k]
            if isinstance(v, bool) or not isinstance(v, int) or not 0 < v <= cap:
                body[k], changed = cap, True
    if not present:
        body["max_tokens"], changed = cap, True
    return changed


def _err(status, obj):
    return status, "application/json", json.dumps(obj).encode()


def _resolve(backend, payload, ginst, span):
    """What the sync and the job path share before the upstream call: host
    key, model router, max_tokens cap. -> (error, None) with error =
    (status, ctype, body), or (None, call) with call = {url, headers,
    payload, backend, stream}."""
    url, keyname = LLM_PROXY_UPSTREAMS[backend]
    st = _settings.load_settings()
    # Self-hosted OrcaRouter-Lite: the shared base URL applies to the proxy
    # too — otherwise the detour would suddenly run against the cloud while
    # direct mode talks to the own server.
    if backend == "orcarouter" and (st.get("ORCAROUTER_URL") or "").strip():
        u = st["ORCAROUTER_URL"].strip().rstrip("/")
        if not u.endswith("/chat/completions"):
            u += "/chat/completions" if u.endswith("/v1") else "/v1/chat/completions"
        url = u
    key = (st.get(keyname) or "").strip()
    if not key:
        return _err(503, {"error": f"{keyname} not configured on host"}), None
    try:
        body = json.loads(payload or b"{}")
        want_stream = bool(body.get("stream"))
    except (ValueError, AttributeError):
        body, want_stream = None, False
    # Model router (opt-in per instance, mgr/router.py): the turn may run on
    # another model the running instances use — never blocks, never a new provider.
    if isinstance(body, dict) and ginst is not None and ((ginst.get("config") or {}).get("MODEL_ROUTER")
                                                         or _router.planned(ginst["name"], span["turn"])):
        target = _router.route(ginst, body, span["turn"])
        re_url, re_key = _routed_upstream(target, st) if target else (None, None)
        if re_url:
            body["model"] = target["model"]
            payload, url, key, backend = json.dumps(body).encode(), re_url, re_key, target["backend"]
    if _clamp_max_tokens(body, max_tokens_of(ginst, st)):
        payload = json.dumps(body).encode()
    headers = {"Content-Type": "application/json",
               "HTTP-Referer": f"https://{_settings.PUBLIC_HOST}", "X-Title": "kat56-agent"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return None, {"url": url, "headers": headers, "payload": payload, "backend": backend, "stream": want_stream}


def _open(call, ginst, span, t0, timeout=600):
    """Open the upstream request. -> (response, None) or (None, error) with
    upstream errors passed through 1:1 (the agent has its own retry logic for
    429/5xx and shows 4xx bodies) and booked as a failed span."""
    req = urllib.request.Request(call["url"], data=call["payload"], method="POST", headers=call["headers"])
    try:
        return urllib.request.urlopen(req, timeout=timeout), None
    except urllib.error.HTTPError as e:
        data = e.read()
        if ginst is not None:            # a failed LLM span, with the reason
            _store.usage_add(ginst["name"], call["backend"], 0, 0, 0, ok=False,
                             err=f"HTTP {e.code}: {data[:300].decode('utf-8', 'replace')}",
                             ms=int((time.monotonic() - t0) * 1000), **span)
            _router.outcome(ginst["name"], span["turn"], "error")    # cost router: next turn one level up
        return None, (e.code, e.headers.get("Content-Type", "application/json"), data)
    except Exception as e:
        return None, _err(502, {"error": f"llm upstream unreachable: {e!r}"})


def _call(call, ginst, span, timeout=600):
    """One non-streaming upstream call, books kept. -> (status, ctype, body).
    The sync path and the job thread both run exactly this."""
    t0 = time.monotonic()
    r, err = _open(call, ginst, span, t0, timeout)
    if err:
        return err
    with r:
        out = (r.status, r.headers.get("Content-Type", "application/json"), r.read())
    _proxy_usage(ginst, call["backend"], out[2], ms=int((time.monotonic() - t0) * 1000), **span)
    return out


# ---- job mode: the manager holds the long connection, not the VM -------------
# POST /api/llm/<backend>/jobs -> {"job": id} at once; a thread runs _call;
# GET /api/llm/jobs/<id> -> {"status": "running"} or the upstream answer
# (status + body as the sync path would have sent them). Bounded: a few
# running jobs per caller, a capped store, finished results expire.
JOB_TIMEOUT = 900          # the upstream call of a job (sync: 600)
JOB_TTL = 600              # a finished result waits this long for its poll
JOB_MAX_RUNNING = 4        # per caller
JOB_MAX = 256              # whole store
_jobs_lock = threading.Lock()
_jobs = {}                 # id -> {"owner", "done": ts|None, "res": (status, ctype, body)}


def _jobs_sweep(now):
    for jid in [j for j, v in _jobs.items() if v["done"] and now - v["done"] > JOB_TTL]:
        del _jobs[jid]


def job_submit(owner, call, ginst, span):
    """Start the call in a thread. -> (id, None) or (None, reason)."""
    with _jobs_lock:
        _jobs_sweep(time.time())
        if sum(1 for v in _jobs.values() if v["owner"] == owner and not v["done"]) >= JOB_MAX_RUNNING:
            return None, f"{JOB_MAX_RUNNING} LLM jobs already running"
        if len(_jobs) >= JOB_MAX:
            return None, "LLM job store full"
        jid = secrets.token_urlsafe(16)
        _jobs[jid] = {"owner": owner, "done": None, "res": None}

    def run():
        try:
            res = _call(call, ginst, span, timeout=JOB_TIMEOUT)
        except Exception as e:           # never leave a job "running" forever
            res = _err(502, {"error": f"llm job failed: {e!r}"})
        with _jobs_lock:
            if jid in _jobs:
                _jobs[jid].update(done=time.time(), res=res)
    threading.Thread(target=run, daemon=True, name=f"llmjob-{jid[:6]}").start()
    return jid, None


def job_get(owner, jid):
    """The caller's job or None (a foreign id looks exactly like an unknown one)."""
    with _jobs_lock:
        _jobs_sweep(time.time())
        j = _jobs.get(jid)
        return dict(j) if j and j["owner"] == owner else None


class LLMProxyMixin:
    """The handler side of the key proxy: POST /api/llm/<backend>/chat/completions
    from a VM, upstream with the host-held key, usage booked per instance;
    POST /api/llm/<backend>/jobs + GET /api/llm/jobs/<id> for the job mode.
    Mixed into mgr.httpd.H."""

    def _llm_reply(self, status, ctype, data):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def _llm_caller(self):
        """(instance or None, job owner): a VM by its IP; a host service on the
        docker bridge is booked under one name, not a VM."""
        ginst = _guests.instance_by_ip(self.client_address[0])
        if ginst is None and self.client_address[0].startswith("172.17."):
            ginst = {"name": "hindsight" if _hindsight.enabled() else "services"}   # booked, not a VM
        return ginst, (ginst["name"] if ginst else "ip:" + self.client_address[0])

    def _llm_proxy(self, _pp):
        """POST /api/llm/<backend>/chat/completions — credential injection
        gateway. The body goes to the router (max_tokens capped); the manager
        injects the Authorization header from the settings so the key never
        reaches the VM. Streams (SSE) are passed through line by line, upstream
        errors transparently (status + body). /jobs instead: the same call,
        non-streaming, in a manager thread. Deliberately NO logs of key or
        body — those are exactly what should not leave the host or linger."""
        parts = _pp.strip("/").split("/")      # api/llm/<backend>/chat/completions
        backend = parts[2] if len(parts) > 2 else ""
        job = parts[3:] == ["jobs"]
        if backend not in LLM_PROXY_UPSTREAMS or not (job or parts[3:] == ["chat", "completions"]):
            return self._llm_reply(*_err(404, {"error": "unknown llm proxy path"}))
        ok_g, why = _guard_check(_guests.instance_by_ip(self.client_address[0]))
        if not ok_g:
            return self._llm_reply(*_err(429, {"error": {"message": f"guardrail: {why}", "code": 429}}))
        payload = self._raw(_routes.BODY_MAX_LLM)
        ginst, owner = self._llm_caller()
        span = {"turn": self.headers.get("X-Kaim-Turn", "")[:16],
                "step": self.headers.get("X-Kaim-Step", "") or None}
        if job:                                # a job never streams
            try:
                body = json.loads(payload or b"{}")
                body.pop("stream", None); body.pop("stream_options", None)
            except (ValueError, AttributeError):
                return self._llm_reply(*_err(400, {"error": "job body must be a JSON object"}))
            payload = json.dumps(body).encode()
        err, call = _resolve(backend, payload, ginst, span)
        if err:
            return self._llm_reply(*err)
        if job:
            jid, why = job_submit(owner, call, ginst, span)
            return self._llm_reply(*(_err(429, {"error": why}) if why else _err(202, {"job": jid})))
        if not call["stream"]:
            return self._llm_reply(*_call(call, ginst, span))
        _t0 = time.monotonic()
        r, err = _open(call, ginst, span, _t0)
        if err:
            return self._llm_reply(*err)
        with r:
            self.send_response(r.status)
            self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
            # Write SSE on line by line and flush — full buffering would kill
            # the token streaming in the agent. readline() blocks only until
            # the next event line, never until the end of the stream. Without
            # Content-Length the response ends with the connection close
            # (HTTP/1.0), urllib in the guest reads until EOF.
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                while True:
                    chunk = r.readline()
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    if b'"usage"' in chunk:
                        _proxy_usage(ginst, call["backend"], chunk, ms=int((time.monotonic() - _t0) * 1000), **span)
            except (BrokenPipeError, ConnectionResetError):
                pass               # client gone -> upstream closes via with

    def _llm_job_get(self, _pp):
        """GET /api/llm/jobs/<id> — {"status": "running"}, or the finished
        upstream answer with its status; only the caller's own jobs."""
        j = job_get(self._llm_caller()[1], _pp.rstrip("/").rsplit("/", 1)[-1])
        if j is None:
            return self._llm_reply(*_err(404, {"error": "unknown job"}))
        if not j["done"]:
            return self._llm_reply(*_err(200, {"status": "running"}))
        return self._llm_reply(*j["res"])
