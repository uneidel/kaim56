# kAIm56 — self-hosted Firecracker AI-agent platform
# Copyright (C) 2026 the kAIm56 authors
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program is free software under the GNU AGPL v3+; see LICENSE.
"""Model router (security boundary): picks the model for an agent's turn.

Opt-in per instance (config MODEL_ROUTER=<policy>, a runtime key); without it
nothing here runs. The key proxy (mgr/llmproxy.py) asks route() on the first
LLM call of a turn and keeps the answer for the rest of the turn, so the model
never changes inside a tool loop.

- Candidates: the models the RUNNING instances use right now (cloud via the
  proxy's upstreams, or the Jetson's llama.cpp) — and only those the operator
  gave a tier (cheap / strong / code / local). No tier, no routing target: the
  router never reaches a provider the platform does not already use.
- Classifier: Jev (OpenJev NLI cross-encoder, container, JEV_URL in Settings)
  scores one hypothesis per class; the policy maps the classes to a tier
  preference. Below the confidence floor a question counts as undecided.
- Never blocks an answer: Jev unreachable, slow, or no tiered candidate -> the
  instance's own model, as if there were no router.

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import os
import re
import threading
import time
import urllib.request

from mgr import instances as _instances
from mgr import paths as _paths
from mgr import settings as _settings
from mgr import store as _store

ROUTER_FILE = os.path.join(_paths.BASE, "router.json")
TIERS = ("cheap", "strong", "code", "local")
JEV_TIMEOUT = float(os.environ.get("JEV_TIMEOUT", "20"))
PREMISE_MAX = 1500
CACHE_TTL = 3600
DEFAULT_POLICY = {
    "questions": {
        "task": {"chat": "This request is casual conversation or a simple question.",
                 "code": "This request asks for writing or fixing code.",
                 "reasoning": "This request asks for math or step-by-step reasoning.",
                 "text": "This request asks to summarize, translate or rewrite a text.",
                 "extract": "This request asks to extract structured data."},
        "difficulty": {"trivial": "This request is trivial.",
                       "moderate": "This request is of moderate difficulty.",
                       "hard": "This request is hard, expert level."},
    },
    "rules": [
        {"if": {"difficulty": "trivial"}, "prefer": ["cheap", "local"]},
        {"if": {"task": "code"}, "prefer": ["code", "strong"]},
        {"if": {"difficulty": "hard"}, "prefer": ["strong"]},
        {"if": {"task": "reasoning"}, "prefer": ["strong"]},
        {"if": {"task": "chat"}, "prefer": ["cheap", "local"]},
    ],
    "floor": 0.55,
}
_cache = {}                       # (instance, turn) -> decision, for the rest of the turn
_lock = threading.Lock()


# ---- configuration ---------------------------------------------------------
def load():
    """{"tiers": {"backend/model": tier}, "policies": {name: policy}} — the
    built-in 'default' policy is always there (the file may override it)."""
    try:
        with open(ROUTER_FILE) as fh:
            d = json.load(fh)
    except (FileNotFoundError, ValueError, OSError):
        d = {}
    tiers = {k: v for k, v in (d.get("tiers") or {}).items() if v in TIERS}
    policies = {"default": DEFAULT_POLICY, **(d.get("policies") or {})}
    return {"tiers": tiers, "policies": policies}


def save_tiers(tiers):
    """Replace the tier table ({"backend/model": tier}; an empty tier removes)."""
    clean = {str(k)[:160]: v for k, v in (tiers or {}).items() if v in TIERS and "/" in str(k)}
    d = load()
    out = {"tiers": clean, "policies": {k: v for k, v in d["policies"].items() if k != "default"}}
    tmp = ROUTER_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(out, fh, indent=2)
    os.replace(tmp, ROUTER_FILE)
    return clean


def policy_names():
    return sorted(load()["policies"])


def jev_url():
    return (_settings.load_settings().get("JEV_URL") or "").strip().rstrip("/")


# ---- candidates: the models of the running instances -----------------------
def model_of(inst):
    """(backend, model, upstream-or-None) an instance's agent uses; None for a
    template whose calls are not OpenAI-style (claude)."""
    cfg = inst.get("config") or {}
    if cfg.get("LLAMA_ENDPOINT"):
        return ("llama", cfg.get("LLAMA_MODEL") or "local-model", cfg["LLAMA_ENDPOINT"])
    if cfg.get("ORCAROUTER_MODEL"):
        return ("orcarouter", cfg["ORCAROUTER_MODEL"], None)
    if cfg.get("OPENROUTER_MODEL"):
        return ("openrouter", cfg["OPENROUTER_MODEL"], None)
    return None


def candidates():
    """[{key, backend, model, upstream, instances}] — one per distinct model of
    the running instances (key = "backend/model", what the tier table uses)."""
    out = {}
    for inst in _instances.load_instances():
        if not _instances.is_running(inst):
            continue
        m = model_of(inst)
        if not m:
            continue
        key = f"{m[0]}/{m[1]}"
        c = out.setdefault(key, {"key": key, "backend": m[0], "model": m[1], "upstream": m[2], "instances": []})
        c["instances"].append(inst["name"])
    return sorted(out.values(), key=lambda c: c["key"])


# ---- the decision ----------------------------------------------------------
def premise(payload):
    """The last user turn (+ the one before it), as plain text, truncated —
    what the classifier reads. System prompts and tool output stay out."""
    msgs = [m for m in (payload.get("messages") or []) if isinstance(m, dict)]
    users = [i for i, m in enumerate(msgs) if m.get("role") == "user"]
    if not users:
        return ""

    def text(m):
        c = m.get("content")
        if isinstance(c, list):
            c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
        return str(c or "").strip()
    last = text(msgs[users[-1]])
    prev = [text(m) for m in msgs[max(0, users[-1] - 2):users[-1]] if m.get("role") in ("user", "assistant")]
    prev = re.sub(r"⟦think⟧.*?⟦/think⟧", "", " ".join(prev), flags=re.S).strip()
    p = (f"Earlier: {prev[-400:]}\n" if prev else "") + f"Request: {last}"
    return p[-PREMISE_MAX:]


def classify(policy, text):
    """{question: (class, prob)} from Jev, one batched call; raises on failure."""
    hyps, index = [], []
    for q, classes in policy["questions"].items():
        for cls, h in classes.items():
            hyps.append(h)
            index.append((q, cls))
    body = json.dumps({"premise": text, "hypotheses": hyps}).encode()
    req = urllib.request.Request(jev_url() + "/classify", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=JEV_TIMEOUT) as r:
        d = json.load(r)
    best = {}
    for (q, cls), p in zip(index, d.get("entail") or []):
        if q not in best or p > best[q][1]:
            best[q] = (cls, float(p))
    return best, int(d.get("ms") or 0)


def pick(policy, classes, tiered):
    """The first rule whose conditions hold (each class above the floor) and
    whose preferred tiers have a candidate -> (candidate, rule index), else
    (None, None). `tiered`: {tier: [candidate, …]}."""
    floor = float(policy.get("floor", 0.55))
    for i, rule in enumerate(policy.get("rules") or []):
        cond = rule.get("if") or {}
        if not all(classes.get(q, ("", 0))[0] == want and classes[q][1] >= floor for q, want in cond.items()):
            continue
        for tier in rule.get("prefer") or []:
            if tiered.get(tier):
                return tiered[tier][0], i
    return None, None


def route(inst, payload, turn):
    """The decision for this call: {"backend", "model", "upstream", ...} to use
    instead of the instance's own model, or None (= its own model). Cached per
    (instance, turn); every fresh decision is recorded (store.route_add)."""
    name_ = (inst.get("config") or {}).get("MODEL_ROUTER", "").strip()
    if not name_ or not turn or not jev_url():
        return None
    now = time.time()
    with _lock:
        hit = _cache.get((inst["name"], turn))
        for k in [k for k, v in _cache.items() if now - v["ts"] > CACHE_TTL]:
            _cache.pop(k, None)
    if hit is not None:
        return hit["target"]
    conf = load()
    policy = conf["policies"].get(name_)
    own = model_of(inst)
    own_key = f"{own[0]}/{own[1]}" if own else ""
    decision = {"ts": now, "target": None, "classes": {}, "ms": 0, "why": ""}
    tiered = {}
    for c in candidates():
        t = conf["tiers"].get(c["key"])
        if t:
            tiered.setdefault(t, []).append(c)
    if policy is None:
        decision["why"] = f"unknown policy {name_!r}"
    elif not tiered:
        decision["why"] = "no running model has a tier"
    else:
        text = premise(payload)
        if not text:
            decision["why"] = "no user message"
        else:
            try:
                classes, ms = classify(policy, text)
                decision.update(classes={q: [c, round(p, 3)] for q, (c, p) in classes.items()}, ms=ms)
                cand, rule = pick(policy, classes, tiered)
                if cand is None:
                    decision["why"] = "no rule matched"
                elif cand["key"] == own_key:
                    decision["why"] = f"rule {rule}: own model"
                else:
                    decision["target"] = {"backend": cand["backend"], "model": cand["model"],
                                          "upstream": cand["upstream"], "key": cand["key"]}
                    decision["why"] = f"rule {rule}: {conf['tiers'][cand['key']]}"
            except Exception as e:                       # never block an answer
                decision["why"] = f"jev: {e!r}"[:200]
    with _lock:
        _cache[(inst["name"], turn)] = decision
    _store.route_add(inst["name"], turn, own_key, (decision["target"] or {}).get("key") or own_key,
                     decision["classes"], decision["ms"], decision["why"])
    return decision["target"]
