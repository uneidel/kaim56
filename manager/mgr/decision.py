# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Decision backend: who scores the platform's fixed-option questions (the
setup suggestion's persona/skills/tier, optionally the per-turn router).

Setting DECISION_BACKEND:
- "jev" (default): OpenJev NLI (mgr/jevsetup.py, mgr/router.py) as before.
- "decision1-openrouter": Microsoft-Decision-1 through OpenRouter — URL and
  key are the key proxy's (mgr/llmproxy.py), used host-side only.
- "decision1-azure": Decision-1 on a Microsoft Foundry serverless endpoint
  (AZURE_DECISION_ENDPOINT + AZURE_DECISION_KEY, host-side only).
- "local-llm": the llama.cpp endpoint the platform already uses
  (LLAMA_ENDPOINT/LLAMA_API_KEY, model DECISION_LOCAL_MODEL) — nothing leaves
  the LAN; slower, so a longer timeout.

Decision-1's native structured API is not public (2026-10-09), so every
non-Jev backend gets the same OpenAI-compatible chat-completions call with a
strict-JSON prompt. The whole wire format lives in `ask()` — a typed task
({state, questions: {key: {type, instructions, criteria, max}}} -> {key:
{option: score}}) shaped like the decide/evaluate APIs, so a native call can
replace that one function. Unknown options are dropped, scores clamped to
0..1. Callers never see an exception: a failure falls back to Jev and says
so (a note, a log line without secrets).

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from mgr import jevsetup as _jevsetup
from mgr import llmproxy as _llmproxy
from mgr import router as _router
from mgr import settings as _settings

BACKENDS = _settings.DECISION_BACKENDS
DEFAULT_MODEL = "microsoft/decision-1"       # OpenRouter id not published yet: DECISION_MODEL overrides
TIMEOUT, LOCAL_TIMEOUT = 30, 60              # seconds; no retries
REQ_MAX = 120_000                            # bytes of the request body
RESP_MAX = 256 * 1024
DESC_MAX = 300                               # characters per catalog description
SYSTEM = (
    "You are a decision-scoring model. You read a state and typed questions and answer ONLY with one JSON "
    'object, no prose, no code fence: {"answers": {"<question key>": {"<option name>": <score>, ...}, ...}}. '
    "Option names must be copied exactly from the question's criteria. Scores are calibrated probabilities 0..1. "
    'type "choice": exactly one option is right; give every option a probability, together about 1. '
    'type "multi": each option independently; list only options that apply, at most "max" of them.')


def backend():
    b = (_settings.load_settings().get("DECISION_BACKEND") or "jev").strip()
    return b if b in BACKENDS else "jev"


def router_on():
    """The per-turn router asks the decision backend (DECISION_ROUTER=1 and a
    non-Jev backend) — every user message then goes to that backend."""
    st = _settings.load_settings()
    return backend() != "jev" and (st.get("DECISION_ROUTER") or "") == "1"


def ready():
    """Is any classifier configured (Jev, or a non-Jev backend)?"""
    return bool(_router.jev_url()) or backend() != "jev"


def _endpoint(be, st):
    """(url, headers, model, timeout) for a non-Jev backend; raises when it
    is not configured. The keys come from the settings, host-side."""
    if be == "decision1-openrouter":
        url, key = _llmproxy._routed_upstream({"backend": "openrouter"}, st)
        if not url:
            raise RuntimeError("OPENROUTER_API_KEY not configured")
        return (url, {"Authorization": f"Bearer {key}", "HTTP-Referer": f"https://{_settings.PUBLIC_HOST}",
                      "X-Title": "kat56-decision"},
                (st.get("DECISION_MODEL") or "").strip() or DEFAULT_MODEL, TIMEOUT)
    if be == "decision1-azure":
        u, key = (st.get("AZURE_DECISION_ENDPOINT") or "").strip(), (st.get("AZURE_DECISION_KEY") or "").strip()
        if not u or not key or _settings.validate_decision({"AZURE_DECISION_ENDPOINT": u}):
            raise RuntimeError("AZURE_DECISION_ENDPOINT / AZURE_DECISION_KEY not configured")
        p = urllib.parse.urlsplit(u)
        if not p.path.rstrip("/").endswith("/chat/completions"):
            p = p._replace(path=p.path.rstrip("/") + "/chat/completions")
        return (urllib.parse.urlunsplit(p), {"Authorization": f"Bearer {key}", "api-key": key},
                (st.get("DECISION_MODEL") or "").strip() or "Microsoft-Decision-1", TIMEOUT)
    if be == "local-llm":
        url, _ = _llmproxy._routed_upstream({"backend": "llama"}, st)
        if not url:
            raise RuntimeError("LLAMA_ENDPOINT not configured")
        key = (st.get("LLAMA_API_KEY") or "").strip()
        return (url, {"Authorization": f"Bearer {key}"} if key else {},
                (st.get("DECISION_LOCAL_MODEL") or "").strip() or "local-model", LOCAL_TIMEOUT)
    raise RuntimeError(f"no LLM decision backend: {be}")


def _secrets(st):
    return [v for k, v in st.items() if v and isinstance(v, str) and len(v) >= 6 and _settings.is_secret_setting(k)]


def safe_err(e, st=None):
    """An error as text without any configured secret in it."""
    msg = f"HTTP {e.code}" if isinstance(e, urllib.error.HTTPError) else f"{type(e).__name__}: {e}"
    for s in _secrets(st if st is not None else _settings.load_settings()):
        msg = msg.replace(s, "***")
    return msg[:200]


def _json_of(text):
    """The JSON object in a model's answer (thinking, fences and prose around it tolerated)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < a:
        raise ValueError("no JSON object in the answer")
    return json.loads(text[a:b + 1])


def ask(task, be=None):
    """One typed decision task -> ({question key: {option: score}}, ms).
    task = {"state": str, "questions": {key: {"type": "choice"|"multi",
    "instructions": str, "criteria": {option: description}, "max": n}}}.
    The ONE place that knows the wire format (chat completions + strict JSON
    today; Decision-1's native structured API later). Raises on failure."""
    st = _settings.load_settings()
    be = be or backend()
    url, headers, model, timeout = _endpoint(be, st)
    body = {"model": model, "temperature": 0, "max_tokens": 2000,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": json.dumps(task, ensure_ascii=False)}]}
    if be == "local-llm":
        body["chat_template_kwargs"] = {"enable_thinking": False}     # llama.cpp: no reasoning tokens
    data = json.dumps(body).encode()
    if len(data) > REQ_MAX:
        raise ValueError(f"request too large ({len(data)} bytes)")
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json", **headers})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(RESP_MAX + 1)
    if len(raw) > RESP_MAX:
        raise ValueError("answer too large")
    content = ((json.loads(raw).get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    answers = _json_of(content).get("answers")
    if not isinstance(answers, dict):
        raise ValueError("answer without 'answers'")
    out = {}
    for key, q in task["questions"].items():
        got = answers.get(key) if isinstance(answers.get(key), dict) else {}
        scores = {}
        for opt, sc in got.items():
            if opt in q["criteria"]:                                 # unknown names dropped
                try:
                    scores[opt] = round(min(1.0, max(0.0, float(sc))), 3)
                except (TypeError, ValueError):
                    pass
        if q.get("type") == "multi":
            scores = dict(sorted(scores.items(), key=lambda t: -t[1])[:int(q.get("max") or len(scores))])
        out[key] = scores
    return out, int((time.time() - t0) * 1000)


def _log_fallback(be, e):
    print(f"[decision] {be} failed ({safe_err(e)}) — Jev used", file=sys.stderr, flush=True)


def choose(question, options, context=""):
    """[{option, score}] best first. options: {name: description} or [name].
    The configured backend; on failure (or with Jev) Jev's entailment."""
    opts = dict(options) if isinstance(options, dict) else {str(o): "" for o in options}
    be = backend()
    if be != "jev":
        try:
            ans, _ = ask({"state": str(context or ""), "questions": {"q": {
                "type": "choice", "instructions": str(question), "criteria": opts}}}, be)
            return sorted(({"option": k, "score": v} for k, v in ans["q"].items()), key=lambda x: -x["score"])
        except Exception as e:
            _log_fallback(be, e)
    if not _router.jev_url():
        return []
    try:
        hyps = [f"{question} {k}: {v}"[:_jevsetup.HYP_MAX] for k, v in opts.items()]
        e = _jevsetup._entail(str(context or question)[:_jevsetup.PURPOSE_MAX], hyps, time.time() + _jevsetup.SUGGEST_TIMEOUT)
        return sorted(({"option": k, "score": round(p, 3)} for k, p in zip(opts, e)), key=lambda x: -x["score"])
    except Exception:
        return []


def classify(policy, text):
    """The router's ({question: (class, prob)}, ms) from the decision backend
    (one request, the policy's hypotheses as option descriptions); on failure
    Jev's (router.jev_classify, which raises — the router keeps the own model)."""
    be = backend()
    try:
        qs = {q: {"type": "choice", "instructions": f"Classify the request: {q}", "criteria": dict(cl)}
              for q, cl in policy["questions"].items()}
        ans, ms = ask({"state": text, "questions": qs}, be)
        best = {q: max(sc.items(), key=lambda t: t[1]) for q, sc in ans.items() if sc}
        if not best:
            raise ValueError("no class scored")
        return best, ms
    except Exception as e:
        _log_fallback(be, e)
    return _router.jev_classify(policy, text)
