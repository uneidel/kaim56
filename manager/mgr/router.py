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
- Mode "cheapest" (built-in policy 'cheapest', or any policy with
  "mode": "cheapest"): decision.judge() gives the turn a level 1-5; the ladder
  is every tiered running model with a capability level (operator-set, else
  from its tier) and its catalog price; the cheapest model with level >= the
  turn's and the features it needs wins. "catalog": true adds the catalog
  models the operator ladder-tagged in the Models tab. A failed turn is run
  again one level up, once: tasks and squad wakes by the manager (tasks.py),
  a chat turn by moving the instance's NEXT turn up (outcome()).

Part of the mgr package: no import from manager.py. Sibling modules are used
as ``_name.func`` (module attribute), so a test can replace one definition in
one place.
"""
import json
import os
import threading
import time
import urllib.request

from mgr import decision as _decision
from mgr import instances as _instances
from mgr import models as _models
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
CHEAPEST = {"mode": "cheapest", "catalog": False, "check": False, "escalate": True}
TIER_LEVEL = {"local": 2, "cheap": 2, "code": 4, "strong": 5}    # a model's level when the operator set none
LONG_CTX = 100_000                # tokens a model needs for a long-context turn
FAIL_OUTCOMES = ("max_steps", "error")
_cache = {}                       # (instance, turn) -> decision, for the rest of the turn
_plans = {}                       # (instance, turn) -> level the manager set for a turn it runs (tasks.py)
_bump = {}                        # instance -> level: its next chat turn runs there (the last one failed)
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
    levels = {k: int(v) for k, v in (d.get("levels") or {}).items() if v in (1, 2, 3, 4, 5)}
    policies = {"default": DEFAULT_POLICY, "cheapest": CHEAPEST, **(d.get("policies") or {})}
    return {"tiers": tiers, "levels": levels, "policies": policies}


def _store_conf(**part):
    d = load()
    d.update(part)
    out = {"tiers": d["tiers"], "levels": d["levels"],
           "policies": {k: v for k, v in d["policies"].items() if v is not DEFAULT_POLICY and v is not CHEAPEST}}
    tmp = ROUTER_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(out, fh, indent=2)
    os.replace(tmp, ROUTER_FILE)


def save_tiers(tiers):
    """Replace the tier table ({"backend/model": tier}; an empty tier removes)."""
    clean = {str(k)[:160]: v for k, v in (tiers or {}).items() if v in TIERS and "/" in str(k)}
    _store_conf(tiers=clean)
    return clean


def save_levels(levels):
    """Replace the ladder levels ({"backend/model": 1..5}; anything else removes)."""
    clean = {}
    for k, v in (levels or {}).items():
        try:
            v = int(v)
        except (TypeError, ValueError):
            continue
        if 1 <= v <= 5 and "/" in str(k):
            clean[str(k)[:160]] = v
    _store_conf(levels=clean)
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
    """The last user turn as plain text, truncated — what the classifier reads.
    Only that turn: earlier small talk in the premise pulled a plain code task
    to "chat" (live, 2026-09-30); system prompts and tool output stay out too."""
    msgs = [m for m in (payload.get("messages") or []) if isinstance(m, dict)]
    users = [m for m in msgs if m.get("role") == "user"]
    if not users:
        return ""
    c = users[-1].get("content")
    if isinstance(c, list):
        c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return str(c or "").strip()[-PREMISE_MAX:]


def classify(policy, text):
    """({question: (class, prob)}, ms): from the decision backend when
    DECISION_ROUTER is on (mgr/decision.py, falls back to Jev), else Jev."""
    if _decision.router_on():
        return _decision.classify(policy, text)
    return jev_classify(policy, text)


def jev_classify(policy, text):
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


# ---- the ladder: level, price and features per model ------------------------
def facts(backend, model):
    """Price ($ per 1M tokens in/out, `price` = blended 3:1 for a typical turn)
    and features of one model. llama = 0 (local); claude = 0, marked
    subscription; else the OpenRouter catalog (cache only); unknown = None."""
    if backend == "llama":
        return {"pin": 0.0, "pout": 0.0, "price": 0.0, "note": "local", "vision": False, "tools": True, "ctx": 0}
    if backend == "claude":
        return {"pin": 0.0, "pout": 0.0, "price": 0.0, "note": "subscription", "vision": True, "tools": True, "ctx": 200_000}
    row = _models.lookup(model)
    if not row:
        return {"pin": None, "pout": None, "price": None, "note": "price unknown", "vision": False, "tools": True, "ctx": 0}
    return {"pin": row["pin"], "pout": row["pout"], "price": round((3 * row["pin"] + row["pout"]) / 4, 4),
            "note": "", "vision": bool(row.get("vision")), "tools": bool(row.get("tools")), "ctx": int(row.get("ctx") or 0)}


def ladder(conf=None, catalog=False):
    """[{key, backend, model, upstream, tier, level, price, ...}]: the running
    models with a tier or a level (level = operator's, else from the tier);
    with `catalog` also the OpenRouter models the operator gave a level."""
    conf = conf or load()
    out = {}
    for c in candidates():
        tier = conf["tiers"].get(c["key"], "")
        lvl = conf["levels"].get(c["key"]) or TIER_LEVEL.get(tier)
        if lvl:
            out[c["key"]] = {**c, "tier": tier, "level": lvl, "catalog": False, **facts(c["backend"], c["model"])}
    if catalog:
        for key, lvl in conf["levels"].items():
            if key.startswith("openrouter/") and key not in out:
                mdl = key[len("openrouter/"):]
                out[key] = {"key": key, "backend": "openrouter", "model": mdl, "upstream": None, "instances": [],
                            "tier": "", "level": lvl, "catalog": True, **facts("openrouter", mdl)}
    return sorted(out.values(), key=lambda e: e["key"])


def choose(lad, level, needs=None):
    """The cheapest entry with level >= `level` and every needed feature;
    equal price -> the higher level; unknown price last. None if none fits."""
    needs = needs or {}
    ok = [e for e in lad if e["level"] >= level
          and (not needs.get("vision") or e["vision"])
          and (not needs.get("tools") or e["tools"])
          and (not needs.get("long_context") or e["ctx"] >= LONG_CTX)]
    ok.sort(key=lambda e: (e["price"] is None, e["price"] or 0, -e["level"], e["key"]))
    return ok[0] if ok else None


def needs_of(payload):
    """What the request itself demands: tools offered, an image attached, a long context."""
    msgs = [m for m in (payload.get("messages") or []) if isinstance(m, dict)]
    img = any(isinstance(m.get("content"), list) and any(isinstance(p, dict) and p.get("type") == "image_url"
                                                         for p in m["content"]) for m in msgs)
    return {"tools": bool(payload.get("tools")), "vision": img,
            "long_context": len(json.dumps(msgs, ensure_ascii=False)) // 4 > LONG_CTX // 2}


# ---- manager-run turns and escalation ---------------------------------------
def plan(instance, turn, level, escalated=False):
    """The manager runs this turn (task / squad wake) at this level (tasks.py)."""
    with _lock:
        _plans[(instance, turn)] = {"level": max(1, min(5, int(level))), "escalated": bool(escalated), "ts": time.time()}


def planned(instance, turn):
    with _lock:
        return bool(turn) and (instance, turn) in _plans


def cost_routing(inst, squad_on=False):
    """The policy a manager-run turn on `inst` routes with, or None: its
    MODEL_ROUTER policy in mode cheapest, else CHEAPEST when its squad has cost
    routing on. Never for a template without OpenAI-style calls (claude
    members stay on Claude)."""
    if not inst or not model_of(inst):
        return None
    pol = load()["policies"].get((inst.get("config") or {}).get("MODEL_ROUTER", "").strip())
    if pol and pol.get("mode") == "cheapest":
        return pol
    return CHEAPEST if squad_on else None


def failed(ok, res):
    """Did a manager-run turn fail (error, max tool steps, an error reply)?"""
    t = str(res or "")
    return not ok or "(max tool steps reached)" in t or t.lstrip().startswith(("⚠️", "error:"))


def next_level(instance, turn, level):
    """One level above what the turn ran on (its chosen model's level), or None at the top."""
    with _lock:
        d = _cache.get((instance, turn)) or {}
    up = max(int(level), int(d.get("chosen_level") or level)) + 1
    return up if up <= 5 else None


def book(instance, turn, result):
    _store.route_result(instance, turn, result)


def outcome(instance, turn, result):
    """A turn ended (agent trace / proxy error). For a chat turn the router
    chose in mode cheapest: book the result; a failure moves the instance's
    NEXT turn one level up — once (an escalated turn never escalates again).
    Manager-run turns book and re-run themselves (tasks.py)."""
    with _lock:
        d = _cache.get((instance, turn))
    if not d or d.get("mode") != "cheapest" or d.get("planned"):
        return
    book(instance, turn, result)
    if result in FAIL_OUTCOMES and not d.get("escalated") and d.get("escalate", True):
        up = max(d["level"], d.get("chosen_level") or d["level"]) + 1
        if up <= 5:
            with _lock:
                _bump[instance] = up


def savings(now=None):
    """{"24h"|"7d"|"30d": {turns, in, out, actual, strong, saved}}: the routed
    turns' usage cost vs. the same tokens at the strong model's price (the
    'strong' tier's priciest known model, else the top-level one)."""
    lad = [e for e in ladder(catalog=True) if e["price"] is not None]
    strong = (sorted([e for e in lad if e["tier"] == "strong"], key=lambda e: -e["price"])
              or sorted(lad, key=lambda e: (-e["level"], -e["price"])))
    if not strong:
        return {"error": "no ladder model with a known price — give your strong model a tier"}
    s = strong[0]
    now = now or time.time()
    out = {"strong": s["key"]}
    for win, secs in (("24h", 86400), ("7d", 7 * 86400), ("30d", 30 * 86400)):
        turns, rows = _store.route_usage(int(now - secs))
        tin = sum(r[1] for r in rows)
        tout = sum(r[2] for r in rows)
        actual = 0.0
        for mdl, i, o, cost in rows:
            if cost:
                actual += cost
            else:                                    # no cost reported: the catalog's price, local = 0
                row = _models.lookup(mdl)
                actual += (i * row["pin"] + o * row["pout"]) / 1e6 if row else 0.0
        base = (tin * s["pin"] + tout * s["pout"]) / 1e6
        out[win] = {"turns": turns, "in": tin, "out": tout, "actual": round(actual, 4),
                    "strong": round(base, 4), "saved": round(base - actual, 4)}
    return out


# ---- the decision ------------------------------------------------------------
def _rules(policy, payload, conf, own_key, decision):
    tiered = {}
    for c in candidates():
        t = conf["tiers"].get(c["key"])
        if t:
            tiered.setdefault(t, []).append(c)
    if not tiered:
        decision["why"] = "no running model has a tier"
        return
    text = premise(payload)
    if not text:
        decision["why"] = "no user message"
        return
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


def _cheapest(inst, policy, payload, conf, own_key, decision, plan_):
    text = premise(payload)
    needs = needs_of(payload)
    decision.update(mode="cheapest", escalate=policy.get("escalate", True), excerpt=text[:160],
                    planned=bool(plan_), escalated=False)
    note = ""
    if plan_:
        level = plan_["level"]
        decision["escalated"] = plan_["escalated"]
        note = "set by the manager"
    else:
        j = _decision.judge(text)
        level, note = j["level"], j.get("note") or j.get("backend", "")
        needs["vision"] = needs["vision"] or j["needs"].get("vision", False)
        decision.update(classes={"level": [level, j.get("score", 0)]}, ms=j.get("ms", 0))
        with _lock:
            up = _bump.pop(inst["name"], None)
        if up:
            level, decision["escalated"] = max(level, up), True
    decision["level"] = level
    lad = ladder(conf, policy.get("catalog"))
    cand = choose(lad, level, needs)
    own = next((e for e in lad if e["key"] == own_key), None)
    if cand is None:
        decision["chosen_level"] = own["level"] if own else level
        decision["why"] = f"L{level}: no ladder model fits — own model" + (f" ({note})" if note else "")
        return
    decision.update(chosen_level=cand["level"], price=cand["price"])
    p = "price ?" if cand["price"] is None else f"${cand['price']:.2f}/1M" + (f" {cand['note']}" if cand["note"] else "")
    decision["why"] = (f"L{level}{' escalated' if decision['escalated'] else ''}: cheapest L{cand['level']} "
                       f"{cand['key']} ({p})" + (f" · {note}" if note else ""))[:200]
    if cand["key"] != own_key:
        decision["target"] = {"backend": cand["backend"], "model": cand["model"],
                              "upstream": cand["upstream"], "key": cand["key"]}


def route(inst, payload, turn):
    """The decision for this call: {"backend", "model", "upstream", ...} to use
    instead of the instance's own model, or None (= its own model). Cached per
    (instance, turn); every fresh decision is recorded (store.route_add). A
    turn the manager planned (plan()) routes in mode cheapest at its level."""
    name_ = (inst.get("config") or {}).get("MODEL_ROUTER", "").strip()
    now = time.time()
    with _lock:
        plan_ = _plans.get((inst["name"], turn)) if turn else None
        hit = _cache.get((inst["name"], turn))
        for k in [k for k, v in _cache.items() if now - v["ts"] > CACHE_TTL]:
            _cache.pop(k, None)
        for k in [k for k, v in _plans.items() if now - v["ts"] > CACHE_TTL]:
            _plans.pop(k, None)
    if not turn or not (name_ or plan_):
        return None
    if hit is not None:
        return hit["target"]
    conf = load()
    policy = conf["policies"].get(name_)
    cheapest = bool(plan_) or (policy or {}).get("mode") == "cheapest"
    if plan_ and (policy or {}).get("mode") != "cheapest":
        policy = CHEAPEST
    if not cheapest and not (jev_url() or _decision.router_on()):
        return None
    own = model_of(inst)
    own_key = f"{own[0]}/{own[1]}" if own else ""
    decision = {"ts": now, "target": None, "classes": {}, "ms": 0, "why": ""}
    if policy is None:
        decision["why"] = f"unknown policy {name_!r}"
    elif cheapest:
        try:
            _cheapest(inst, policy, payload, conf, own_key, decision, plan_)
        except Exception as e:                   # never block an answer
            decision["why"] = f"cheapest: {e!r}"[:200]
    else:
        _rules(policy, payload, conf, own_key, decision)
    with _lock:
        _cache[(inst["name"], turn)] = decision
    _store.route_add(inst["name"], turn, own_key, (decision["target"] or {}).get("key") or own_key,
                     decision["classes"], decision["ms"], decision["why"], level=decision.get("level"),
                     price=decision.get("price"), escalated=decision.get("escalated", False),
                     excerpt=decision.get("excerpt", ""))
    return decision["target"]
